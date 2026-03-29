# ai-cli

Unified AI session manager for Claude Code and Gemini CLI.

Manages tmux-based AI coding sessions with features like:

- **Session management** — `ai c 1`, `ai c 2` to launch numbered Claude Code sessions in tmux with auto-resume
- **Fleet operations** — `ai c ls`, `ai c -R` to list and restart sessions
- **Cross-machine sync** — `ai sync push/pull` to sync CC memory and conversation history between machines
- **Handoff queue** — `ai handoff post/check/claim/complete` for cross-session task delegation
- **Memory management** — `ai memory search/audit` for CC memory operations
- **Notifications** — desktop and push notifications for long-running operations

## Install

```bash
uv tool install ai-cli
```

## Requirements

- Python 3.11+
- tmux
- Claude Code CLI (`claude`)

## License

MIT
