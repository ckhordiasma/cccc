#!/usr/bin/env python3
"""Estimate today's Claude Code spend by diffing modelUsage snapshots.

Each run saves current modelUsage to baselines/<today>.json.
Today's cost = current modelUsage - baselines/<yesterday>.json.
Since tmux runs this every 30s, yesterday's file reflects end-of-day state.
"""

import json
from datetime import date, timedelta
from pathlib import Path

STATS_FILE = Path.home() / ".claude" / "stats-cache.json"
BASELINE_DIR = Path.home() / ".claude" / "cost-baselines"

# Pricing per million tokens: (input, output, cache_write, cache_read)
# Source: https://platform.claude.com/docs/en/about-claude/pricing
PRICING = {
    "claude-opus-4-7":          (5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-6":          (5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-5-20251101": (5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-1":          (15.0, 75.0, 18.75, 1.50),
    "claude-sonnet-4-6":            (3.0, 15.0, 3.75, 0.30),
    "claude-sonnet-4-5-20250929":   (3.0, 15.0, 3.75, 0.30),
    "claude-sonnet-4-20250514":     (3.0, 15.0, 3.75, 0.30),
    "claude-haiku-4-5-20251001":    (1.0, 5.0, 1.25, 0.10),
    "claude-3-5-haiku-20241022":    (0.80, 4.0, 1.0, 0.08),
}

DEFAULT_PRICING = (5.0, 25.0, 6.25, 0.50)
TOKEN_FIELDS = ["inputTokens", "outputTokens", "cacheCreationInputTokens", "cacheReadInputTokens"]


def main():
    try:
        data = json.loads(STATS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        print("$?.??")
        return

    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today()
    yesterday = today - timedelta(days=1)

    current_usage = data.get("modelUsage", {})

    # Always save current state (becomes tomorrow's baseline)
    (BASELINE_DIR / f"{today.isoformat()}.json").write_text(json.dumps(current_usage))

    # Load yesterday's baseline
    yesterday_file = BASELINE_DIR / f"{yesterday.isoformat()}.json"
    if not yesterday_file.exists():
        print("$0.00*")
        return

    baseline = json.loads(yesterday_file.read_text())
    total_cost = 0.0

    for model_id, current in current_usage.items():
        base = baseline.get(model_id, {})
        p_in, p_out, p_cw, p_cr = PRICING.get(model_id, DEFAULT_PRICING)
        rates = [p_in, p_out, p_cw, p_cr]

        for field, rate in zip(TOKEN_FIELDS, rates):
            delta = max(0, current.get(field, 0) - base.get(field, 0))
            total_cost += delta * rate / 1_000_000

    if total_cost >= 100:
        print(f"${total_cost:.0f}")
    elif total_cost >= 10:
        print(f"${total_cost:.1f}")
    else:
        print(f"${total_cost:.2f}")


if __name__ == "__main__":
    main()
