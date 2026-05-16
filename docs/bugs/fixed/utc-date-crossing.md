# Bug: UTC-date-crossing inflates first-day costs

## Symptom

Running `cccc` (today) shortly after local midnight returns a non-zero cost
even when the user hasn't used Claude Code today. The reported figure
includes the previous evening's usage.

Observed at 00:11 EDT on 2026-05-16:

```
$ cccc
$40.97
```

Expected: close to `$0.00` (only ~11 minutes of today-local elapsed).

## Root cause

All three implementations filter messages by **UTC-date prefix match** on
the `timestamp` field:

```rust
// src/main.rs
if !prefixes.iter().any(|p| ts.starts_with(p.as_str())) {
    continue;
}
```

`prefixes` for "today" is `{today_local, today_local + 1 day}` (as date
strings, e.g. `2026-05-16`, `2026-05-17`). A message with timestamp
`2026-05-16T02:00:00Z` matches the `2026-05-16` prefix even though
`02:00 UTC` is `22:00 EDT` on **2026-05-15** — yesterday local.

For users west of UTC, every yesterday-evening-local message has a
today-UTC timestamp and gets counted. The bug shows on the **first day**
of any query range (the start boundary); the end boundary is correct
because `end_date + 1` is already included for evening overlap.

## Reproduction

```sh
#!/bin/sh
set -e
export TZ=America/New_York   # UTC-4 in May

QUERY_DATE=2026-05-16
TMP=$(mktemp -d)
mkdir -p "$TMP/projects/proj"

# A message at 02:00 UTC = 22:00 EDT the previous day (yesterday local)
cat > "$TMP/projects/proj/session.jsonl" <<EOF
{"type":"assistant","timestamp":"${QUERY_DATE}T02:00:00.000Z","message":{"model":"claude-opus-4-7-20251001","usage":{"input_tokens":1000000,"output_tokens":1000000}}}
EOF

# Make sure mtime is "recent enough" not to be skipped by the file pre-filter
touch "$TMP/projects/proj/session.jsonl"

export CLAUDE_PROJECTS_DIR="$TMP/projects"
export CLAUDE_PRICING_FILE="$PWD/pricing.json"

echo "Querying $QUERY_DATE (today local in EDT) with one yesterday-evening-local message:"
cccc "$QUERY_DATE"
echo "Expected: \$0.00"

rm -rf "$TMP"
```

Expected output once fixed: `$0.00`. Current output: non-zero (the cost
of 1M input + 1M output tokens of Opus 4.7 = `$30.00`).

## Fix sketch

Replace the prefix-match filter with a precise unix-timestamp range check
in all three implementations:

- Compute `start_unix` = local midnight of `start_date`
- Compute `end_unix` = local midnight of `end_date + 1 day`
- For each message, parse `timestamp` to unix seconds and require
  `start_unix <= ts_unix < end_unix`

The file-level `mtime < cutoff` pre-filter (skip files definitely too
old) stays as-is — it's still a correct fast skip.
