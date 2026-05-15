# cccc — Claude Code Cost Calculator

Estimates your daily [Claude Code](https://claude.ai/code) spend by scanning session files directly. Shows a live dollar amount in your tmux status bar, updated every few seconds.

## How it works

Claude Code writes per-message token usage (input, output, cache write, cache read) to `.jsonl` session files in `~/.claude/projects/`. This tool scans all sessions modified today, filters for messages with today's timestamp, and computes cost using the rates in `pricing.json`.

Handles the UTC/local timezone mismatch in session timestamps — messages near midnight are counted correctly regardless of your timezone offset.

## Dependencies

- `jq`
- `awk` (ships with macOS/Linux)
- `find` (ships with macOS/Linux)

The Python version (`claude-cost.py`) requires Python 3.9+ with no external packages.

## Usage

```sh
# Run directly
./claude-cost.sh    # => $42.17

# Or with Python
python3 claude-cost.py
```

## tmux integration

Add to your `.tmux.conf`:

```tmux
set -g status-interval 5
set -g status-right "#(/path/to/cccc/claude-cost.sh) | %H:%M %d-%b-%y"
```

Then reload:

```sh
tmux source-file ~/.tmux.conf
```

The cost display appears on the right side of your status bar and refreshes every 5 seconds (~290ms per run).

## Updating pricing

Edit `pricing.json` when Anthropic changes their rates. Keys are regex patterns matched against model IDs, ordered most-specific-first. Rates are per million tokens in USD.
