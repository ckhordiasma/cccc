#!/usr/bin/env python3
"""Estimate Claude Code spend by summing per-message usage from session files.

Usage:
    cccc.py                          # today
    cccc.py 2026-05-13               # specific date
    cccc.py 2026-05-01 2026-05-14    # date range (inclusive)
"""

import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECTS_DIR = Path(os.environ.get("CLAUDE_PROJECTS_DIR", str(Path.home() / ".claude" / "projects")))
TOKEN_FIELDS = ["input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"]
RATE_FIELDS = ["input", "output", "cache_write", "cache_read"]


def load_pricing():
    pricing_file = Path(os.environ.get("CLAUDE_PRICING_FILE", str(SCRIPT_DIR.parent / "pricing.json")))
    data = json.loads(pricing_file.read_text())
    patterns = [(re.compile(k), v) for k, v in data["models"].items()]
    return patterns, data.get("web_search_cost_per_request", 0.01)


def get_rates(model_id: str, patterns: list) -> tuple:
    for pattern, rates in patterns:
        if pattern.search(model_id):
            return tuple(rates[f] for f in RATE_FIELDS)
    return tuple(patterns[0][1][f] for f in RATE_FIELDS)


def date_range(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def main():
    if len(sys.argv) >= 3:
        start_date = date.fromisoformat(sys.argv[1])
        end_date = date.fromisoformat(sys.argv[2])
    elif len(sys.argv) == 2:
        start_date = date.fromisoformat(sys.argv[1])
        end_date = start_date
    else:
        start_date = date.today()
        end_date = start_date

    pricing_patterns, ws_cost = load_pricing()

    # Include end_date+1 to catch evening messages that cross the UTC boundary
    prefixes = {d.isoformat() for d in date_range(start_date, end_date + timedelta(days=1))}

    cutoff = datetime.combine(start_date, datetime.min.time()).timestamp()

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
