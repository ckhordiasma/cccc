# cccc — Claude Code Cost Calculator

Estimates your [Claude Code](https://claude.ai/code) API spend by scanning local session files. Uses [Anthropic API pricing](https://docs.anthropic.com/en/docs/about-claude/pricing), not subscription pricing. Since it reads session files from `~/.claude/projects/` on the current machine, it only reflects usage from this machine's Claude Code instance. Supports querying today, a specific date, or a date range. Three implementations with identical output: shell (jq/awk), Python, and Rust.

## How it works

Claude Code writes per-message token usage (input, output, cache write, cache read) to `.jsonl` session files in `~/.claude/projects/`. This tool scans those files, filters by date, and computes cost using the rates in `pricing.json`.

Handles the UTC/local timezone mismatch in session timestamps — messages near midnight are counted correctly regardless of your timezone offset.

## Usage

```sh
./scripts/cccc.sh                        # today
./scripts/cccc.sh 2026-05-13             # specific date
./scripts/cccc.sh 2026-05-01 2026-05-14  # date range (inclusive)

# Python
python3 scripts/cccc.py 2026-05-13

# Rust
cargo build --release
./target/release/cccc 2026-05-13
```

## Implementations

| Version | Path | Dependencies |
|---------|------|-------------|
| Shell | `scripts/cccc.sh` | `jq`, `awk`, `find` (ships with macOS/Linux) |
| Python | `scripts/cccc.py` | Python 3.9+ (no external packages) |
| Rust | `src/` | Rust toolchain (`cargo build --release` to compile) |

## tmux integration

Add to your `.tmux.conf` for a live cost display:

```tmux
set -g status-interval 5
set -g status-right "#(/path/to/cccc/scripts/cccc.sh) | %H:%M %d-%b-%y"
```

## Updating pricing

Edit `pricing.json` when Anthropic changes their rates. Keys are regex patterns matched against model IDs, ordered most-specific-first. Rates are per million tokens in USD.

Or regenerate from the live pricing page:

```sh
scripts/update-pricing.py > pricing.json.new && mv pricing.json.new pricing.json
```

The helper keeps the existing regex keys and only refreshes rates. It errors out if a key now matches multiple models with diverging rates — that means the regex needs to be split into separate keys (a human call).

## Tests

```sh
bash tests/test_cost.sh
```

Runs all three implementations against fixture data with known expected costs. Covers date filtering, empty results, date ranges, pricing pattern order, and cross-implementation parity.
