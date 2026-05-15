#!/bin/sh
# End-to-end tests for claude-cost.sh and claude-cost.py
#
# Fixtures contain messages on Jan 05, Jan 15, and Jan 25 with known token
# counts. The key regression test: querying Jan 15 must NOT include Jan 05
# or Jan 25 messages (the jq startswith scoping bug caused all dates to match).

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export CLAUDE_PROJECTS_DIR="$SCRIPT_DIR/fixtures"
export CLAUDE_PRICING_FILE="$SCRIPT_DIR/fixtures/pricing.json"

pass=0
fail=0

check() {
  label="$1"
  expected="$2"
  actual="$3"

  if [ "$actual" = "$expected" ]; then
    printf "  PASS  %s\n" "$label"
    pass=$((pass + 1))
  else
    printf "  FAIL  %s: expected %s, got %s\n" "$label" "$expected" "$actual"
    fail=$((fail + 1))
  fi
}

echo "Running cccc tests..."
echo

# --- Test 1: Single date (Jan 15) ---
# Matches: 3x opus(1k,2k,3k,4k) + 1x haiku(500,1k,0,0) + 1x opus(5k,10k,0,0,ws=2)
# Does NOT match: Jan 05 or Jan 25 messages
# Expected: $0.53
echo "Test 1: Single date (Jan 15)"
sh_out=$("$ROOT/claude-cost.sh" 2026-01-15)
py_out=$(python3 "$ROOT/claude-cost.py" 2026-01-15)
check "sh output" '$0.53' "$sh_out"
check "py output" '$0.53' "$py_out"
check "sh/py parity" "$py_out" "$sh_out"
echo

# --- Test 2: Date with no matches (Jan 30) ---
echo "Test 2: No matches (Jan 30)"
sh_out=$("$ROOT/claude-cost.sh" 2026-01-30)
py_out=$(python3 "$ROOT/claude-cost.py" 2026-01-30)
check "sh output" '$0.00' "$sh_out"
check "py output" '$0.00' "$py_out"
echo

# --- Test 3: Date range (Jan 05 to Jan 25) ---
# Matches all messages across all dates
# Expected: $5.83
echo "Test 3: Date range (Jan 05 to Jan 25)"
sh_out=$("$ROOT/claude-cost.sh" 2026-01-05 2026-01-25)
py_out=$(python3 "$ROOT/claude-cost.py" 2026-01-05 2026-01-25)
check "sh output" '$5.83' "$sh_out"
check "py output" '$5.83' "$py_out"
check "sh/py parity" "$py_out" "$sh_out"
echo

# --- Summary ---
total=$((pass + fail))
if [ "$fail" -eq 0 ]; then
  echo "All $total checks passed."
else
  echo "$fail/$total checks FAILED."
  exit 1
fi
