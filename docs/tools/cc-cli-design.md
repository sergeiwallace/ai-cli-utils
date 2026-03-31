---
title: ai CLI Design — Subcommands Reference
category: tools
tags: [ai-cli, cli, tmux, session-management, reference]
status: current
source: internal
---

# ai CLI Design — Subcommands Reference

## Table of Contents

- [Overview](#overview)
- [Session Management](#session-management)
  - [ai c](#ai-c)
  - [ai ls](#ai-ls)
  - [ai attach](#ai-attach)
  - [ai reconnect](#ai-reconnect)
- [Daemon Commands](#daemon-commands)
  - [ai memory watch](#ai-memory-watch)
  - [ai quota watch](#ai-quota-watch)
  - [ai telemetry writer](#ai-telemetry-writer)
- [Sync Commands](#ai-sync)
- [Utility Commands](#utility-commands)
  - [ai gemini](#ai-gemini)
  - [ai handoff](#ai-handoff)
  - [ai upgrade](#ai-upgrade)
- [Internal Commands](#internal-commands)

---

## Overview

`ai` is the humanware platform CLI. Entry point: `src/ai_cli/main.py:cli()`.

Subcommands are dispatched via `sys.argv` inspection before Click takes over for the default `ai c` session-launch flow.

---

## Session Management

### ai c

```
ai c [N] [-p PROJECT] [-R] [--dry-run] [--verbose]
```

Launch (or resume) a Claude Code session in a tmux worktree. The primary command.

- `N` — session number (default: auto-assigned). Creates worktree `.worktrees/sw-N` on branch `wt-sw-N`.
- `-p PROJECT` — project alias (from `~/.config/ai-cli/config.toml` `[projects]` section)
- `-R` — remote session: SSH tunnel via mosh + tmux on the configured remote host
- `--dry-run` — print what would happen without executing

Session naming convention: `c-<project>-<N>` (local), `c-r-<project>-<N>` (remote).

Auto-runs `git pull --rebase --autostash` at session start to keep worktree current.

### ai ls

```
ai ls [--all]
```

Interactive tmux session picker using fzf. Shows `ai-cli` sessions by default (matching `_AI_SESSION_RE`). `--all` shows all tmux sessions.

- If fzf is available: launches interactive picker → attaches to selected session via `os.execvp`
- If fzf is absent and `apt` is available: installs fzf automatically, then launches picker
- If fzf is unavailable: prints a numbered list with project name and age, followed by `ai attach <name>` hint

Display columns: session name, age (e.g. `5m`, `2h`, `3d`). Sorted by most-recently-active first.

### ai attach

```
ai attach <session-name>
```

Thin wrapper: validates the session exists, then replaces the current process with `tmux attach-session -t <name>` via `os.execvp`.

Exits with error if no session named `<session-name>` exists.

### ai reconnect

```
ai reconnect [session_numbers...]
```

Lists remote tmux sessions (sessions starting with `c-r-`) on the configured remote host via SSH and prints the `ai c N -R` commands to reconnect to each.

- No args: all remote sessions
- `ai reconnect 1 3`: only sessions ending in `-1` or `-3`

Reads `[remote] host` and `[remote] user` from `~/.config/ai-cli/config.toml`.

---

## Daemon Commands

### ai memory watch

```
ai memory watch
```

Starts the memory file watcher daemon. Watches `~/.claude/` for write activity and publishes `dream.started` / `dream.completed` events to NATS JetStream. Used to coordinate sync pausing during active memory writes.

Acquires PID file `memory-watch` — only one instance runs at a time.

### ai quota watch

```
ai quota watch
```

Starts the quota monitoring daemon. Subscribes to NATS subject `quota.*` and tracks Claude token usage. Publishes quota state updates.

Acquires PID file `quota-watch`.

### ai telemetry writer

```
ai telemetry writer
```

Starts the telemetry writer daemon. Subscribes to NATS subject `telemetry.action.*` (durable consumer) and persists events to a local SQLite database at `~/.local/state/ai-cli/telemetry.db`.

Acquires PID file `telemetry-writer`.

---

## ai sync

```
ai sync pull [--verbose] [--dry-run] [--remote-host HOST]
ai sync push [--verbose] [--dry-run] [--remote-host HOST]
ai sync watch [--verbose]
```

Bidirectional sync of Claude config, memory files, conversation history, and handoff queue between local and remote host.

- `pull` — fetch remote state to local staging, apply to `~/.claude/`
- `push` — package local state and push to remote
- `watch` — continuous background sync loop (starts pull/push on file-change events)

Reads remote host from `[remote] host` in config, overridable via `--remote-host`.

---

## Utility Commands

### ai gemini

```
ai gemini "prompt" [-m MODEL] [-o OUTPUT_FILE] [--quiet] [--verbose] [--timeout N] [--no-file]
```

Gemini CLI wrapper with 3-tier auth fallback (OAuth → free API key → paid API key) and auto-retry on 429/capacity errors. See `src/ai_cli/gemini.py`.

Model aliases: `deep-think`, `pro`, `flash`, `flash-lite`, or any full model ID.

### ai handoff

```
ai handoff post <from> <to> <subject> <body>
ai handoff check
ai handoff claim <id>
ai handoff complete <id>
```

Cross-session handoff queue. `post` writes a handoff item; `check` lists pending items for the current session; `claim` marks an item in-progress; `complete` marks it done.

Queue lives at `~/projects/sergei/.handoff-queue/`.

### ai upgrade

```
ai upgrade
```

Reinstalls the `ai` tool from the current source tree using `uv tool install . --force`.

---

## Internal Commands

```
ai internal <action> [args...]
```

Used by hooks and scripts — not for direct human use.

| Action | Args | Purpose |
|--------|------|---------|
| `start` | `engine ai_name uuid` | Record session start event |
| `stop` | `session_id` | Record session stop event |
| `cleanup-worktree` | `path` | Remove stale worktree |
| `send-message` | `session_id msg` | Send message to session via NATS |
| `notify` | `session_id event_type` | Publish session notification |
| `publish` | `subject payload` | Raw NATS publish |
| `session-lifecycle` | `session_id verb` | Publish session started/stopped |
