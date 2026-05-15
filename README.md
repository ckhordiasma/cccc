# cccc — Claude Code Cost Calculator

Estimates your [Claude Code](https://claude.ai/code) API spend by scanning local session files. Uses [Anthropic API pricing](https://docs.anthropic.com/en/docs/about-claude/pricing), not subscription pricing. Since it reads session files from `~/.claude/projects/` on the current machine, it only reflects usage from this machine's Claude Code instance. Supports querying today, a specific date, or a date range. Three implementations with identical output: shell (jq/awk), Python, and Rust.

## How it works

Claude Code writes per-message token usage (input, output, cache write, cache read) to `.jsonl` session files in `~/.claude/projects/`. This tool scans those files, filters by date, and computes cost using the rates in `pricing.json`.

Handles the UTC/local timezone mismatch in session timestamps — messages near midnight are counted correctly regardless of your timezone offset.

## Usage

```sh
./jq/cccc.sh                        # today
./jq/cccc.sh 2026-05-13             # specific date
./jq/cccc.sh 2026-05-01 2026-05-14  # date range (inclusive)

# Python
python3 python/cccc.py 2026-05-13

# Rust
cd rust && cargo build --release
./rust/target/release/cccc 2026-05-13
```

## Implementations

| Version | Path | Dependencies |
|---------|------|-------------|
| Shell | `jq/cccc.sh` | `jq`, `awk`, `find` (ships with macOS/Linux) |
| Python | `python/cccc.py` | Python 3.9+ (no external packages) |
| Rust | `rust/` | Rust toolchain (`cargo build --release` to compile) |

## tmux integration

Add to your `.tmux.conf` for a live cost display:

```tmux
set -g status-interval 5
set -g status-right "#(/path/to/cccc/jq/cccc.sh) | %H:%M %d-%b-%y"
```

## Updating pricing

Edit `pricing.json` when Anthropic changes their rates. Keys are regex patterns matched against model IDs, ordered most-specific-first. Rates are per million tokens in USD.

## Tests

```sh
bash tests/test_cost.sh
```

Runs all three implementations against fixture data with known expected costs. Covers date filtering, empty results, date ranges, pricing pattern order, and cross-implementation parity.
