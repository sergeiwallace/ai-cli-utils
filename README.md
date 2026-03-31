# ai-cli-utils

[![PyPI](https://img.shields.io/pypi/v/ai-cli-utils)](https://pypi.org/project/ai-cli-utils/)
[![Python](https://img.shields.io/pypi/pyversions/ai-cli-utils)](https://pypi.org/project/ai-cli-utils/)
[![License](https://img.shields.io/pypi/l/ai-cli-utils)](https://github.com/sergeiwallace/ai-cli-utils/blob/main/LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/sergeiwallace/ai-cli-utils/ci.yml?label=CI)](https://github.com/sergeiwallace/ai-cli-utils/actions)
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![codecov](https://codecov.io/gh/sergeiwallace/ai-cli-utils/graph/badge.svg)](https://codecov.io/gh/sergeiwallace/ai-cli-utils)

Unified AI session manager and automation toolkit for Claude Code and Gemini CLI.

Run multiple AI coding sessions in parallel, each isolated in its own git worktree, with auto-resume, remote server support, cross-machine sync, and resilient Gemini API access with automatic auth fallback.

## What it does

`ai-cli-utils` wraps [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and [Gemini CLI](https://github.com/google-gemini/gemini-cli) in tmux sessions with production workflow features: numbered sessions, git worktree isolation, mosh/SSH remote access, cross-machine memory sync, and session lifecycle management. It also provides `ai gemini` — a Gemini CLI wrapper with 3-tier auth fallback (OAuth → free API key → paid API key) that automatically retries on capacity errors, so your research prompts keep working even when one auth method is exhausted. If you run multiple AI coding sessions daily, this tool eliminates the boilerplate.

## Features

| Feature | Description |
|---------|-------------|
| **Session management** | `ai c 1`, `ai c 2` — numbered tmux sessions with auto-resume on disconnect |
| **Session picker** | `ai ls` — fzf-powered session picker sorted by activity; `ai attach <name>` to attach directly |
| **Git worktree isolation** | Each session gets its own worktree — parallel work without branch conflicts |
| **Remote sessions** | `ai c -R` — run sessions on a remote server via mosh or SSH |
| **Cross-machine sync** | `ai sync push/pull` — sync Claude Code memory and conversations between machines |
| **Handoff queue** | `ai handoff post/check/claim/complete` — delegate tasks between sessions |
| **Fleet messaging** | NATS-based heartbeats, events, and sync notifications |
| **Stale session cleanup** | Automatic detection and cleanup of orphaned sessions |
| **Gemini with fallback** | `ai gemini "prompt"` — 3-tier auth fallback (OAuth → free API → paid API), auto-retry on capacity errors |
| **Notifications** | Desktop and push notifications on task completion |

## Installation

```bash
uv tool install ai-cli-utils
```

Or with pipx:

```bash
pipx install ai-cli-utils
```

## Quick Start

```bash
# Launch Claude Code session 1
ai c 1

# Launch a second session in parallel (isolated worktree)
ai c 2

# Launch Gemini CLI session
ai g research

# Resume a disconnected session
ai c -r 1

# Run a session on your remote dev server
ai c -R 1

# Sync Claude Code memory to another machine
ai sync push
```

## Usage

### Session management

```bash
ai c <name>          # Start/resume Claude Code session
ai g <name>          # Start/resume Gemini CLI session
ai c -r <name>       # Resume existing session explicitly
ai c -b <name>       # Run bare (no tmux wrapper)
ai c -o <name>       # Run once (no auto-resume loop)
```

### Remote sessions

```bash
ai c -R <name>              # Connect to remote server (uses config)
ai c -R -p myproject <name> # Specify remote project directory
```

### Cross-machine sync

```bash
ai sync push         # Push Claude Code state to remote
ai sync pull         # Pull remote state to local
ai sync conflicts    # Show unresolved sync conflicts
ai sync watch        # Watch for sync events via NATS
```

### Handoff queue

```bash
ai handoff post      # Post a task for another session to pick up
ai handoff check     # Check for pending handoffs
ai handoff claim     # Claim a handoff
ai handoff complete  # Mark a handoff as done
```

### Gemini with auth fallback

```bash
ai gemini "prompt" -m deep-think          # Run with 3-tier fallback, stdout + auto file
ai gemini "prompt" -m pro -o output.md    # Specify output file
ai gemini "prompt" -m flash --quiet       # File only, no stdout
cat prompt.txt | ai gemini -m deep-think  # Pipe from stdin
ai gemini "prompt" -m flash --no-file     # Stdout only, no file
```

**Auth fallback chain (automatic on 429/capacity errors):**
1. Gemini CLI OAuth (free — Google AI subscription)
2. REST API with `GOOGLE_API_KEY_FREE_TIER`
3. REST API with `GOOGLE_API_KEY_TIER_1`

**Model aliases:** `deep-think`, `pro`, `flash`, `flash-lite`, or any full Gemini model ID.

**Logs:** `~/.local/state/ai-cli/gemini-logs/` (JSONL). **Auto output:** `~/.local/state/ai-cli/gemini-output/`.

Install with Gemini REST support: `uv tool install "ai-cli-utils[gemini]"`

### Session picker

```bash
ai ls                # Interactive fzf session picker (installs fzf via apt if absent)
ai ls --all          # Show all tmux sessions, not just ai-cli sessions
ai attach <name>     # Attach directly to a named tmux session
```

### Other commands

```bash
ai memory watch      # Watch for Claude Code memory file changes
ai quota watch       # Monitor API quota usage
ai telemetry writer  # Run telemetry writer daemon
ai upgrade           # Self-upgrade the tool
ai reconnect         # Print reconnect commands for remote sessions
```

## Configuration

Configuration lives in `~/.config/ai-cli/config.toml`. A default config is created on first run.

```toml
[project]
main_project = "myproject"     # default project directory under ~/projects/

[remote]
host = "1.2.3.4"              # remote dev server
user = "ubuntu"
transport = "mosh"             # "mosh" (default) or "ssh"
# port = 22
# identity_file = "~/.ssh/id_ed25519"

[worktree]
enabled = true                 # git worktree isolation per session

[session]
stale_session_timeout = 15     # minutes before cleanup considers a session stale

[sync]
remote_host = "user@host"      # for cross-machine sync

[behavior]
notify_on_exit = true          # desktop notifications on task completion
```

## Requirements

- Python 3.11+
- [tmux](https://github.com/tmux/tmux)
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and/or [Gemini CLI](https://github.com/google-gemini/gemini-cli)
- [mosh](https://mosh.org/) (optional, for remote sessions — falls back to SSH)
- [NATS](https://nats.io/) (optional, for fleet messaging and sync watch)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and contribution guidelines.

## License

[MIT](LICENSE) -- Sergei Wallace
