#!/bin/sh
# End-to-end tests for scripts/cccc.sh, scripts/cccc.py, and target/release/cccc
#
# Fixtures contain messages on Jan 05, Jan 15, and Jan 25 with known token
# counts. The key regression test: querying Jan 15 must NOT include Jan 05
# or Jan 25 messages (the jq startswith scoping bug caused all dates to match).
#
# Tests 4-6 intentionally run failing invocations, so set -e is omitted.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export CLAUDE_PROJECTS_DIR="$SCRIPT_DIR/fixtures"
export CLAUDE_PRICING_FILE="$SCRIPT_DIR/fixtures/pricing.json"
# Fixture timestamps are picked so each is in the middle of its UTC day; pin
# TZ so local-midnight bounds line up with the fixture dates on any host.
export TZ=UTC

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
# Matches: 3x opus(1k,2k,3k,4k) + 2x haiku(500+0,1k+100k,0,0) + 1x opus(5k,10k,0,0,ws=2)
# Does NOT match: Jan 05 or Jan 25 messages
# Haiku uses haiku-4-5 rates ($1/$5) not haiku ($0.80/$4) — catches pricing pattern order bugs
# Expected: $1.03
echo "Test 1: Single date (Jan 15)"
sh_out=$("$ROOT/scripts/cccc.sh" 2026-01-15)
py_out=$(python3 "$ROOT/scripts/cccc.py" 2026-01-15)
rs_out=$("$ROOT/target/release/cccc" 2026-01-15)
check "sh output" '$1.03' "$sh_out"
check "py output" '$1.03' "$py_out"
check "rs output" '$1.03' "$rs_out"
check "sh/py parity" "$py_out" "$sh_out"
check "sh/rs parity" "$rs_out" "$sh_out"
echo

# --- Test 2: Date with no matches (Jan 30) ---
echo "Test 2: No matches (Jan 30)"
sh_out=$("$ROOT/scripts/cccc.sh" 2026-01-30)
py_out=$(python3 "$ROOT/scripts/cccc.py" 2026-01-30)
rs_out=$("$ROOT/target/release/cccc" 2026-01-30)
check "sh output" '$0.00' "$sh_out"
check "py output" '$0.00' "$py_out"
check "rs output" '$0.00' "$rs_out"
echo

# --- Test 3: Date range (Jan 05 to Jan 25) ---
# Matches all messages across all dates
# Expected: $6.33
echo "Test 3: Date range (Jan 05 to Jan 25)"
sh_out=$("$ROOT/scripts/cccc.sh" 2026-01-05 2026-01-25)
py_out=$(python3 "$ROOT/scripts/cccc.py" 2026-01-05 2026-01-25)
rs_out=$("$ROOT/target/release/cccc" 2026-01-05 2026-01-25)
check "sh output" '$6.33' "$sh_out"
check "py output" '$6.33' "$py_out"
check "rs output" '$6.33' "$rs_out"
check "sh/py parity" "$py_out" "$sh_out"
check "sh/rs parity" "$rs_out" "$sh_out"
echo

# Helper: run "$@" with sh/py/rs, capture stdout+stderr and exit code
run_impl() {
  impl="$1"; shift
  case "$impl" in
    sh) "$ROOT/scripts/cccc.sh" "$@" ;;
    py) python3 "$ROOT/scripts/cccc.py" "$@" ;;
    rs) "$ROOT/target/release/cccc" "$@" ;;
  esac
}

# --- Test 4: --help produces usage and exits 0 ---
echo "Test 4: --help produces usage"
for impl in sh py rs; do
  out=$(run_impl "$impl" --help 2>&1)
  rc=$?
  if [ "$rc" -eq 0 ] && printf "%s" "$out" | grep -q "^Usage:"; then
    printf "  PASS  %s --help\n" "$impl"
    pass=$((pass + 1))
  else
    printf "  FAIL  %s --help (rc=%s, out=%s)\n" "$impl" "$rc" "$out"
    fail=$((fail + 1))
  fi
done
echo

# --- Test 5: invalid date exits 1 without panicking ---
echo "Test 5: invalid date exits cleanly"
for impl in sh py rs; do
  out=$(run_impl "$impl" not-a-date 2>&1)
  rc=$?
  if [ "$rc" -eq 1 ] && ! printf "%s" "$out" | grep -qE "(panicked at|Traceback)"; then
    printf "  PASS  %s invalid date\n" "$impl"
    pass=$((pass + 1))
  else
    printf "  FAIL  %s invalid date (rc=%s, out=%s)\n" "$impl" "$rc" "$out"
    fail=$((fail + 1))
  fi
done
echo

# --- Test 6: unknown flag exits 1 without panicking ---
echo "Test 6: unknown flag exits cleanly"
for impl in sh py rs; do
  out=$(run_impl "$impl" --bogus 2>&1)
  rc=$?
  if [ "$rc" -eq 1 ] && ! printf "%s" "$out" | grep -qE "(panicked at|Traceback)"; then
    printf "  PASS  %s unknown flag\n" "$impl"
    pass=$((pass + 1))
  else
    printf "  FAIL  %s unknown flag (rc=%s, out=%s)\n" "$impl" "$rc" "$out"
    fail=$((fail + 1))
  fi
done
echo

# --- Test 7: invalid pricing file exits cleanly ---
echo "Test 7: invalid pricing file exits cleanly"
for impl in sh py rs; do
  out=$(CLAUDE_PRICING_FILE=/nonexistent/pricing.json run_impl "$impl" 2026-01-15 2>&1)
  rc=$?
  if [ "$rc" -eq 1 ] && ! printf "%s" "$out" | grep -qE "(panicked at|Traceback)"; then
    printf "  PASS  %s invalid pricing file\n" "$impl"
    pass=$((pass + 1))
  else
    printf "  FAIL  %s invalid pricing file (rc=%s, out=%s)\n" "$impl" "$rc" "$out"
    fail=$((fail + 1))
  fi
done
echo

# --- Test 8: pricing file missing required fields ---
echo "Test 8: incomplete pricing file exits cleanly"
tmpbad=$(mktemp)
echo '{"models": {"opus": {"input": 1}}}' > "$tmpbad"
for impl in sh py rs; do
  out=$(CLAUDE_PRICING_FILE="$tmpbad" run_impl "$impl" 2026-01-15 2>&1)
  rc=$?
  if [ "$rc" -eq 1 ] && ! printf "%s" "$out" | grep -qE "(panicked at|Traceback)"; then
    printf "  PASS  %s incomplete pricing\n" "$impl"
    pass=$((pass + 1))
  else
    printf "  FAIL  %s incomplete pricing (rc=%s, out=%s)\n" "$impl" "$rc" "$out"
    fail=$((fail + 1))
  fi
done
rm -f "$tmpbad"
echo

# --- Test 9: UTC-date crossing — message at 02:00 UTC is yesterday-evening-local ---
# In America/New_York (UTC-4 in May), 2026-05-16T02:00:00Z = 2026-05-15 22:00 EDT.
# Querying 2026-05-16 must NOT count it. See BUG.md.
echo "Test 9: UTC-date crossing (regression for BUG.md)"
tmpdir=$(mktemp -d)
mkdir -p "$tmpdir/projects/proj"
cat > "$tmpdir/projects/proj/session.jsonl" <<'EOF'
{"type":"assistant","timestamp":"2026-05-16T02:00:00.000Z","message":{"model":"claude-opus-4-6","usage":{"input_tokens":1000000,"output_tokens":1000000}}}
EOF
touch "$tmpdir/projects/proj/session.jsonl"
for impl in sh py rs; do
  out=$(TZ=America/New_York CLAUDE_PROJECTS_DIR="$tmpdir/projects" \
        run_impl "$impl" 2026-05-16 2>&1)
  check "$impl excludes yesterday-evening-local message" '$0.00' "$out"
done
rm -rf "$tmpdir"
echo

# --- Summary ---
total=$((pass + fail))
if [ "$fail" -eq 0 ]; then
  echo "All $total checks passed."
else
  echo "$fail/$total checks FAILED."
  exit 1
fi
