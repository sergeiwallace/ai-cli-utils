# ai-cli-utils

[![PyPI](https://img.shields.io/pypi/v/ai-cli-utils)](https://pypi.org/project/ai-cli-utils/)
[![Python](https://img.shields.io/pypi/pyversions/ai-cli-utils)](https://pypi.org/project/ai-cli-utils/)
[![License](https://img.shields.io/pypi/l/ai-cli-utils)](https://github.com/sergeiwallace/ai-cli-utils/blob/main/LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/sergeiwallace/ai-cli-utils/ci.yml?label=CI)](https://github.com/sergeiwallace/ai-cli-utils/actions)

Unified AI session manager for Claude Code and Gemini CLI.

Manages tmux-based AI coding sessions with features like:

- **Session management** -- `ai c 1`, `ai c 2` to launch numbered Claude Code sessions in tmux with auto-resume
- **Git worktree isolation** -- each session gets its own worktree for parallel development
- **Remote sessions** -- `ai c -R` to run sessions on a remote server via mosh/SSH
- **Cross-machine sync** -- `ai sync push/pull` to sync CC memory and conversation history between machines
- **Handoff queue** -- `ai handoff post/check/claim/complete` for cross-session task delegation
- **Fleet messaging** -- NATS-based heartbeats, events, and sync notifications
- **Notifications** -- desktop and push notifications for long-running operations

## Install

```bash
uv tool install ai-cli-utils
```

Or with pipx:

```bash
pipx install ai-cli-utils
```

## Requirements

- Python 3.11+
- tmux
- Claude Code CLI (`claude`) and/or Gemini CLI (`gemini`)

## Configuration

Configuration lives in `~/.config/ai-cli/config.toml`. A default config is created on first run.

Key settings:

```toml
[project]
main_project = "myproject"  # your main project directory under ~/projects/

[remote]
host = "1.2.3.4"
user = "ubuntu"
transport = "mosh"  # or "ssh"

[sync]
remote_host = "user@host"  # for cross-machine sync
```

## License

MIT
