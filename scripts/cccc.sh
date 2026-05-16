#!/bin/sh
# Estimate Claude Code spend by summing per-message usage from session files.

USAGE='Estimate Claude Code API spend by scanning local session files.

Usage:
    cccc                            today
    cccc YYYY-MM-DD                 specific date
    cccc YYYY-MM-DD YYYY-MM-DD      date range (inclusive)
    cccc -h | --help                show this help

Environment:
    CLAUDE_PROJECTS_DIR             override session-file directory (default: ~/.claude/projects)
    CLAUDE_PRICING_FILE             use this pricing.json instead of the default
'

usage_die() {
  printf "cccc: %s\n\n%s" "$1" "$USAGE" >&2
  exit 1
}

validate_date() {
  case "$1" in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
    *) usage_die "invalid date '$1' (expected YYYY-MM-DD)" ;;
  esac
  date -jf %Y-%m-%d "$1" +%Y-%m-%d >/dev/null 2>&1 || \
    date -d "$1" +%Y-%m-%d >/dev/null 2>&1 || \
    usage_die "invalid date '$1' (expected YYYY-MM-DD)"
}

for arg in "$@"; do
  case "$arg" in
    -h|--help) printf "%s" "$USAGE"; exit 0 ;;
    -*) usage_die "unknown option '$arg'" ;;
  esac
done

[ $# -gt 2 ] && usage_die "too many arguments"
[ $# -ge 1 ] && validate_date "$1"
[ $# -ge 2 ] && validate_date "$2"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PRICING="${CLAUDE_PRICING_FILE:-$SCRIPT_DIR/../pricing.json}"

if [ $# -eq 0 ]; then
  START_DATE=$(date +%Y-%m-%d)
  END_DATE="$START_DATE"
elif [ $# -eq 1 ]; then
  START_DATE="$1"
  END_DATE="$1"
else
  START_DATE="$1"
  END_DATE="$2"
fi

# Build pipe-delimited list of date prefixes to match.
# Timestamps in session files are UTC, so for each local date include the next
# day to catch evening messages that cross the UTC boundary.
DATE_PREFIXES=""
cur="$START_DATE"
END_PLUS1=$(date -v+1d -jf %Y-%m-%d "$END_DATE" +%Y-%m-%d 2>/dev/null || date -d "$END_DATE + 1 day" +%Y-%m-%d 2>/dev/null)
while true; do
  if [ -z "$DATE_PREFIXES" ]; then
    DATE_PREFIXES="$cur"
  else
    case "$DATE_PREFIXES" in
      *"$cur"*) ;;
      *) DATE_PREFIXES="$DATE_PREFIXES|$cur" ;;
    esac
  fi
  [ "$cur" = "$END_PLUS1" ] && break
  cur=$(date -v+1d -jf %Y-%m-%d "$cur" +%Y-%m-%d 2>/dev/null || date -d "$cur + 1 day" +%Y-%m-%d 2>/dev/null)
done

if [ ! -r "$PRICING" ]; then
  printf "cccc: failed to read pricing file '%s'\n" "$PRICING" >&2
  exit 1
fi

# Pre-extract pricing as pipe-delimited single line
PRICING_LINES=$(jq -r '[.models | to_entries[] | "\(.key) \(.value.input) \(.value.output) \(.value.cache_write) \(.value.cache_read)"] | join("|")' "$PRICING" 2>&1) || {
  printf "cccc: failed to parse pricing file '%s': %s\n" "$PRICING" "$PRICING_LINES" >&2
  exit 1
}
WS_COST=$(jq -r '.web_search_cost_per_request' "$PRICING" 2>&1) || {
  printf "cccc: failed to parse pricing file '%s': %s\n" "$PRICING" "$WS_COST" >&2
  exit 1
}

# jq emits "null" for missing keys; coerces to 0 in awk and silently masks bad pricing
if [ -z "$PRICING_LINES" ]; then
  printf "cccc: invalid pricing file '%s': no models defined\n" "$PRICING" >&2
  exit 1
fi
BAD_MODEL=$(printf "%s" "$PRICING_LINES" | tr '|' '\n' | awk '{ for (i=2; i<=5; i++) if ($i == "null" || $i == "") { print $1; exit } }')
if [ -n "$BAD_MODEL" ]; then
  printf "cccc: invalid pricing file '%s': model '%s' has missing or non-numeric rate fields\n" "$PRICING" "$BAD_MODEL" >&2
  exit 1
fi

PROJECTS_DIR="${CLAUDE_PROJECTS_DIR:-$HOME/.claude/projects}"

find "$PROJECTS_DIR" -name "*.jsonl" -type f -newermt "$START_DATE" -exec \
  jq -r --arg prefixes "$DATE_PREFIXES" '
    ($prefixes | split("|")) as $ps |
    .timestamp as $ts |
    select(.type == "assistant" and $ts and any($ps[]; . as $p | $ts | startswith($p))) |
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
