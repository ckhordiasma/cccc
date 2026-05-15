#!/bin/sh
# Estimate today's Claude Code spend by summing per-message usage from session files.
# Scans all .jsonl session files modified today, extracts messages with today's
# timestamp, and computes cost from the full token breakdown (input, output,
# cache write, cache read) using published per-model rates.

TODAY=$(date +%Y-%m-%d)
TODAY_UTC=$(date -u +%Y-%m-%d)

find ~/.claude/projects/ -name "*.jsonl" -type f -newermt "$TODAY" -exec \
  jq -r --arg today "$TODAY" --arg today_utc "$TODAY_UTC" '
    select(.type == "assistant" and .timestamp and ((.timestamp | startswith($today)) or (.timestamp | startswith($today_utc)))) |
    .message.model as $m | .message.usage |
    "\($m) \(.input_tokens // 0) \(.output_tokens // 0) \(.cache_creation_input_tokens // 0) \(.cache_read_input_tokens // 0)"
  ' {} + 2>/dev/null | \
awk '
  { inp[$1]+=$2; out[$1]+=$3; cw[$1]+=$4; cr[$1]+=$5 }
  END {
    # Pricing per million tokens: input output cache_write cache_read
    split("5 25 6.25 0.50", opus46); split("5 25 6.25 0.50", opus45)
    split("15 75 18.75 1.50", opus41); split("3 15 3.75 0.30", sonnet)
    split("1 5 1.25 0.10", haiku45); split("0.80 4 1.0 0.08", haiku35)

    total = 0
    for (m in inp) {
      if (m ~ /opus-4-[567]/) { split("5 25 6.25 0.50", p) }
      else if (m ~ /opus-4-1/) { split("15 75 18.75 1.50", p) }
      else if (m ~ /sonnet/) { split("3 15 3.75 0.30", p) }
      else if (m ~ /haiku-4-5/) { split("1 5 1.25 0.10", p) }
      else if (m ~ /haiku/) { split("0.80 4 1.0 0.08", p) }
      else { split("5 25 6.25 0.50", p) }

      total += (inp[m]*p[1] + out[m]*p[2] + cw[m]*p[3] + cr[m]*p[4]) / 1e6
    }

    printf "$%.2f\n", total
  }'
