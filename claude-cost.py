#!/usr/bin/env python3
"""Estimate today's Claude Code spend by summing per-message usage from session files.

Scans all .jsonl session files modified today, extracts assistant messages with
today's timestamp (local or UTC), and computes cost from the full token breakdown
(input, output, cache write, cache read) using rates from pricing.json.
"""

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECTS_DIR = Path.home() / ".claude" / "projects"
TOKEN_FIELDS = ["input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"]
RATE_FIELDS = ["input", "output", "cache_write", "cache_read"]


def load_pricing():
    pricing_file = SCRIPT_DIR / "pricing.json"
    data = json.loads(pricing_file.read_text())
    patterns = [(re.compile(k), v) for k, v in data["models"].items()]
    return patterns, data.get("web_search_cost_per_request", 0.01)


def get_rates(model_id: str, patterns: list) -> tuple:
    for pattern, rates in patterns:
        if pattern.search(model_id):
            return tuple(rates[f] for f in RATE_FIELDS)
    return tuple(patterns[0][1][f] for f in RATE_FIELDS)


def main():
    pricing_patterns, ws_cost = load_pricing()

    today_local = date.today().isoformat()
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prefixes = {today_local, today_utc}
    cutoff = datetime.combine(date.today(), datetime.min.time()).timestamp()

    totals: dict[str, list[int]] = {}

    for jsonl_file in PROJECTS_DIR.rglob("*.jsonl"):
        if jsonl_file.stat().st_mtime < cutoff:
            continue
        try:
            with open(jsonl_file) as f:
                for line in f:
                    entry = json.loads(line)
                    if entry.get("type") != "assistant":
                        continue
                    ts = entry.get("timestamp", "")
                    if not any(ts.startswith(p) for p in prefixes):
                        continue
                    msg = entry.get("message", {})
                    model = msg.get("model", "")
                    if not model:
                        continue
                    usage = msg.get("usage", {})
                    if model not in totals:
                        totals[model] = [0, 0, 0, 0, 0]
                    for i, field in enumerate(TOKEN_FIELDS):
                        totals[model][i] += usage.get(field, 0)
                    totals[model][4] += usage.get("server_tool_use", {}).get("web_search_requests", 0)
        except (json.JSONDecodeError, OSError):
            continue

    total_cost = 0.0
    for model, counts in totals.items():
        rates = get_rates(model, pricing_patterns)
        for tokens, rate in zip(counts[:4], rates):
            total_cost += tokens * rate / 1_000_000
        total_cost += counts[4] * ws_cost

    print(f"${total_cost:.2f}")


if __name__ == "__main__":
    main()
