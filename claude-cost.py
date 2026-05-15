#!/usr/bin/env python3
"""Estimate today's Claude Code spend by summing per-message usage from session files.

Scans all .jsonl session files modified today, extracts assistant messages with
today's timestamp (local or UTC), and computes cost from the full token breakdown
(input, output, cache write, cache read) using published Anthropic rates.
"""

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Pricing per million tokens: (input, output, cache_write, cache_read)
# Source: https://platform.claude.com/docs/en/about-claude/pricing
PRICING_PATTERNS = [
    (re.compile(r"opus-4-[567]"),   (5.0, 25.0, 6.25, 0.50)),
    (re.compile(r"opus-4-1"),       (15.0, 75.0, 18.75, 1.50)),
    (re.compile(r"sonnet"),         (3.0, 15.0, 3.75, 0.30)),
    (re.compile(r"haiku-4-5"),      (1.0, 5.0, 1.25, 0.10)),
    (re.compile(r"haiku"),          (0.80, 4.0, 1.0, 0.08)),
]

DEFAULT_PRICING = (5.0, 25.0, 6.25, 0.50)
TOKEN_FIELDS = ["input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"]


def get_pricing(model_id: str) -> tuple:
    for pattern, rates in PRICING_PATTERNS:
        if pattern.search(model_id):
            return rates
    return DEFAULT_PRICING


def main():
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
                        totals[model] = [0, 0, 0, 0]
                    for i, field in enumerate(TOKEN_FIELDS):
                        totals[model][i] += usage.get(field, 0)
        except (json.JSONDecodeError, OSError):
            continue

    total_cost = 0.0
    for model, counts in totals.items():
        rates = get_pricing(model)
        for tokens, rate in zip(counts, rates):
            total_cost += tokens * rate / 1_000_000

    print(f"${total_cost:.2f}")


if __name__ == "__main__":
    main()
