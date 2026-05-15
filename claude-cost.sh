#!/bin/sh
# Estimate today's Claude Code spend by summing per-message usage from session files.
# Scans all .jsonl session files modified today, extracts messages with today's
# timestamp, and computes cost from the full token breakdown (input, output,
# cache write, cache read) using rates from pricing.json.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PRICING="$SCRIPT_DIR/pricing.json"
TODAY=$(date +%Y-%m-%d)
TODAY_UTC=$(date -u +%Y-%m-%d)

# Pre-extract pricing as pipe-delimited single line (macOS awk can't handle newlines in -v)
PRICING_LINES=$(jq -r '[.models | to_entries[] | "\(.key) \(.value.input) \(.value.output) \(.value.cache_write) \(.value.cache_read)"] | join("|")' "$PRICING")
WS_COST=$(jq -r '.web_search_cost_per_request' "$PRICING")

find ~/.claude/projects/ -name "*.jsonl" -type f -newermt "$TODAY" -exec \
  jq -r --arg today "$TODAY" --arg today_utc "$TODAY_UTC" '
    select(.type == "assistant" and .timestamp and ((.timestamp | startswith($today)) or (.timestamp | startswith($today_utc)))) |
    .message.model as $m | .message.usage |
    "\($m) \(.input_tokens // 0) \(.output_tokens // 0) \(.cache_creation_input_tokens // 0) \(.cache_read_input_tokens // 0) \(.server_tool_use.web_search_requests // 0)"
  ' {} + 2>/dev/null | \
awk -v pricing="$PRICING_LINES" -v ws_cost="$WS_COST" '
  BEGIN {
    n = split(pricing, lines, "|")
    for (i = 1; i <= n; i++) {
      split(lines[i], f, " ")
      patterns[i] = f[1]
      p_in[i] = f[2]
      p_out[i] = f[3]
      p_cw[i] = f[4]
      p_cr[i] = f[5]
    }
    pcount = n
  }

  { inp[$1]+=$2; out[$1]+=$3; cw[$1]+=$4; cr[$1]+=$5; ws+=$6 }

  END {
    total = 0
    for (m in inp) {
      matched = 0
      for (i = 1; i <= pcount; i++) {
        if (m ~ patterns[i]) {
          total += (inp[m]*p_in[i] + out[m]*p_out[i] + cw[m]*p_cw[i] + cr[m]*p_cr[i]) / 1e6
          matched = 1
          break
        }
      }
      if (!matched) {
        total += (inp[m]*p_in[1] + out[m]*p_out[1] + cw[m]*p_cw[1] + cr[m]*p_cr[1]) / 1e6
      }
    }
    total += ws * ws_cost

    printf "$%.2f\n", total
  }'
