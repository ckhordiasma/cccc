#!/usr/bin/env python3
"""Estimate Claude Code spend by summing per-message usage from session files."""

import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

USAGE = """\
Estimate Claude Code API spend by scanning local session files.

Usage:
    cccc                            today
    cccc YYYY-MM-DD                 specific date
    cccc YYYY-MM-DD YYYY-MM-DD      date range (inclusive)
    cccc -h | --help                show this help

Environment:
    CLAUDE_PROJECTS_DIR             override session-file directory (default: ~/.claude/projects)
    CLAUDE_PRICING_FILE             use this pricing.json instead of the default
"""

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECTS_DIR = Path(os.environ.get("CLAUDE_PROJECTS_DIR", str(Path.home() / ".claude" / "projects")))
TOKEN_FIELDS = ["input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"]
RATE_FIELDS = ["input", "output", "cache_write", "cache_read"]


def die(msg: str) -> None:
    print(f"cccc: {msg}", file=sys.stderr)
    sys.exit(1)


def die_with_usage(msg: str) -> None:
    print(f"cccc: {msg}\n", file=sys.stderr)
    print(USAGE, end="", file=sys.stderr)
    sys.exit(1)


def parse_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        die_with_usage(f"invalid date {s!r} (expected YYYY-MM-DD)")


def load_pricing():
    pricing_file = Path(os.environ.get("CLAUDE_PRICING_FILE", str(SCRIPT_DIR.parent / "pricing.json")))
    try:
        text = pricing_file.read_text()
    except OSError as e:
        die(f"failed to read pricing file {pricing_file}: {e.strerror}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        die(f"failed to parse pricing file {pricing_file}: {e}")
    if not isinstance(data, dict) or "models" not in data or not isinstance(data["models"], dict):
        die(f"invalid pricing file {pricing_file}: missing or non-object 'models'")
    patterns = []
    for k, v in data["models"].items():
        try:
            compiled = re.compile(k)
        except re.error as e:
            die(f"invalid regex {k!r} in pricing file: {e}")
        if not isinstance(v, dict):
            die(f"invalid pricing file {pricing_file}: model {k!r} is not an object")
        for field in RATE_FIELDS:
            if not isinstance(v.get(field), (int, float)):
                die(f"invalid pricing file {pricing_file}: model {k!r} missing or non-numeric {field!r}")
        patterns.append((compiled, v))
    if not patterns:
        die(f"invalid pricing file {pricing_file}: no models defined")
    return patterns, data.get("web_search_cost_per_request", 0.01)


def get_rates(model_id: str, patterns: list) -> tuple:
    for pattern, rates in patterns:
        if pattern.search(model_id):
            return tuple(rates[f] for f in RATE_FIELDS)
    return tuple(patterns[0][1][f] for f in RATE_FIELDS)


def main():
    args = sys.argv[1:]

    if any(a in ("-h", "--help") for a in args):
        print(USAGE, end="")
        return
    if args and args[0].startswith("-"):
        die_with_usage(f"unknown option {args[0]!r}")

    if len(args) > 2:
        die_with_usage("too many arguments")
    elif len(args) == 2:
        start_date = parse_date(args[0])
        end_date = parse_date(args[1])
    elif len(args) == 1:
        start_date = parse_date(args[0])
        end_date = start_date
    else:
        start_date = date.today()
        end_date = start_date

    pricing_patterns, ws_cost = load_pricing()

    # Filter on the half-open *local-time* interval [start_unix, end_unix).
    # Prefix-matching on UTC date strings miscounted messages near UTC midnight
    # (e.g. 02:00 UTC was treated as today even though it's yesterday-evening-local).
    start_unix = datetime.combine(start_date, datetime.min.time()).timestamp()
    end_unix = datetime.combine(end_date + timedelta(days=1), datetime.min.time()).timestamp()

    totals: dict[str, list[int]] = {}

    for jsonl_file in PROJECTS_DIR.rglob("*.jsonl"):
        if jsonl_file.stat().st_mtime < start_unix:
            continue
        try:
            with open(jsonl_file) as f:
                for line in f:
                    entry = json.loads(line)
                    if entry.get("type") != "assistant":
                        continue
                    ts = entry.get("timestamp", "")
                    try:
                        ts_unix = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                    except (ValueError, AttributeError):
                        continue
                    if not (start_unix <= ts_unix < end_unix):
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
