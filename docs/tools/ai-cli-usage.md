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
  - [ai layout](#ai-layout)
  - [ai color](#ai-color)
  - [ai cdp](#ai-cdp)
  - [ai signal-watch](#ai-signal-watch)
  - [ai tunnel](#ai-tunnel)
  - [ai update](#ai-update)
  - [ai setup](#ai-setup)
  - [ai ps](#ai-ps)
  - [ai upgrade](#ai-upgrade)
- [Internal Commands](#internal-commands)

---

## Overview

`ai` is the unified CLI for Claude Code and Gemini CLI sessions. Entry point: `src/ai_cli/main.py:cli()`.

```
ai --version   # print installed version
ai -V          # same, short form
```

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
- `-R` — remote session: mosh + tmux on the configured remote host (auto-switches to SSH when VPN is active)
- `--dry-run` — print what would happen without executing

Session naming convention: `c-<project>-<N>` (local), `c-r-<project>-<N>` (remote).

Auto-runs `git pull --rebase --autostash` at session start to keep worktree current.

**iTerm2 Session Name and Session Title:** when running inside iTerm2, each session automatically configures two tmux options on the new (or resumed) pane:

- `allow-passthrough all` — enables DCS passthrough so iTerm2-specific escape sequences (OSC 1, `SetProfile`, etc.) sent from inside tmux reach the outer terminal. Without this, name-setting sequences are silently dropped.
- `automatic-rename off` — prevents tmux from sending OSC 0/2 sequences for the running process name (e.g. `zsh`, `claude`), which would override the session name and flip the Session Title dropdown from "Name" to "Shell".

The result: the Session Name field in iTerm2's Edit Session → General tab is always set to the tmux session name (e.g. `c-ai-cli-2`, `c-r-sw-1`), and the Session Title dropdown stays on "Name".

### ai g

```
ai g [N] [-p PROJECT] [-R] [--dry-run] [--verbose]
```

Launch (or resume) a Gemini CLI session in a tmux loop. Same flags as `ai c`.

**Session resume:** on each launch or restart, `ai g` checks `~/.gemini/tmp/{name}/chats/` for the most recent session file. If a `checkpoint-{name}.json` exists and is newer than the latest chat file (or no chat files exist), it is automatically converted to a chat session file and resumed via `gemini -r {uuid}`. No manual `/resume load` required.

**Checkpoint conversion:** the conversion is idempotent — the same checkpoint always produces the same UUID. Once converted, Gemini auto-saves all future messages to the chat file natively. The original checkpoint is never deleted and remains as a fallback.

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
ai sync push [--memories-only] [--dry-run] [--verbose] [--force]
ai sync pull [--memories-only] [--dry-run] [--verbose] [--force]
ai sync conflicts
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
ai gemini "prompt" [-m MODEL] [-d DEPTH] [-o OUTPUT_FILE] [--quiet] [--verbose]
             [--timeout N] [--no-file] [--resume RUN_ID] [--planning-model MODEL]
```

Gemini CLI wrapper with 3-tier auth fallback (OAuth → free API key → paid API key) and research depth tiers. See `src/ai_cli/gemini.py` and `src/ai_cli/research.py`.

**Depth tiers** (`-d`/`--depth`):
- `quick` (default) -- single-shot call, current behavior
- `standard` -- Planner-Executor: query generation -> concurrent grounded search -> synthesis (~2x tokens, 2+ model calls)

**Model aliases** (`-m`/`--model`):
- `deep-think` (default) — Gemini 3.1 Pro with HIGH thinking via 3-tier fallback
- `pro`, `flash`, `flash-lite` — standard Gemini models via 3-tier fallback
- `deep-research` — Gemini Deep Research via Interactions API. Async, polls until complete, cancels on Ctrl-C. Tries OAuth (tier 1) first; falls back to paid API key (tier 3). Free-tier key (tier 2) is skipped — deep-research has no free quota.
- Any full Gemini model ID

**Auth tier notes** (see [pricing](https://ai.google.dev/gemini-api/docs/pricing)):
- **Tier 1 (OAuth):** free via gemini CLI credentials. Works for all models.
- **Tier 2 (free API key):** free quota for Flash text/multimodal models (2.0, 2.5, 3.x — including `gemini-3.1-flash-live-preview`), Gemma 4, and Gemini Embedding only. Returns a billing error — not a 429 — for Pro models, image-generation variants (`gemini-3.1-flash-image-preview`, `gemini-3-pro-image-preview`), and deep-research. The fallback chain skips tier 2 automatically for ineligible models.
- **Tier 3 (paid API key):** covers all models. Use `-s 3` when OAuth is unavailable and the model is not free-tier eligible.

**Flags:**
- `-m`/`--model` -- Model alias or full model ID (see above)
- `-s`/`--start-tier` -- Start at auth tier 1 (OAuth, default), 2 (free API key), or 3 (paid API key). For Flash models: `-s 2` skips OAuth. For Pro/deep-research: `-s 3` (free-tier key has no quota for these).
- `-d`/`--depth` -- Research depth: `quick` or `standard`
- `--planning-model MODEL` -- Override planning model for standard tier (default: `deep-think`)
- `--resume RUN_ID` -- Resume a standard run from last completed step
- `-o`/`--output` -- Output file path (auto-generated if omitted)
- `--quiet`/`-q` -- Suppress stderr progress output
- `--verbose`/`-v` -- Show detailed tier/model info
- `-t`/`--timeout` -- Timeout in seconds (default: 600)
- `-F`/`--no-file` -- Stdout only, no file written

**Depth config:** `~/.config/ai-cli/research.yaml` -- optional YAML file to override preset defaults (models, query counts, concurrency). Built-in defaults are used if absent.

**Checkpoints:** `~/.local/state/ai-cli/research-runs/<run-id>/` -- JSON snapshots after each step. Use `--resume <run-id>` to restart from last completed step.

### ai handoff

```
ai handoff post [--remote] <title> <priority> <project> <message>
ai handoff check
ai handoff claim <file>
ai handoff complete <file>
```

Cross-session handoff queue. `post` writes a handoff item (add `--remote` to post to Hetzner via SSH); `check` prints the highest-priority pending file; `claim` atomically moves a file to `claimed/`; `complete` moves it to `completed/`.

Queue lives at `~/projects/<main-project>/.handoff-queue/` (configured via `main_project` in config.toml). Publishing also delivers via NATS `handoff.{project}` for real-time pickup by signal-watch.

### ai layout

```
ai layout list
ai layout validate <name>
ai layout profiles <name>
ai layout <name>
```

YAML-driven iTerm2 window/tab/pane layout system. Layout files live at `~/.config/iterm2/layouts/<name>.yaml`.

- `list` — list available layout files, showing tab names and description for each
- `validate <name>` — validate YAML schema without applying (exits 0 on success, 1 on error)
- `profiles <name>` — regenerate Dynamic Profile JSON files for all tabs in the layout without rebuilding the window (useful after color or icon changes)
- `<name>` — apply a layout: generates Dynamic Profiles, then launches a new iTerm2 window via the Python API with tabs and pane splits as defined

Layout YAML schema supports nested pane splits (vertical/horizontal), per-tab base profiles, tab colors, icon color overrides, and arbitrary startup commands per pane. See `docs/designs/iterm2-layout-system.md`.

### ai color

```
ai color <palette-name|#hex>
```

Ad hoc reassignment of the current session's iTerm2 tab color. Takes a palette color name (e.g., `purple`, `teal`) or a hex value (e.g., `#5e35b1`). Updates the tab color immediately via `SetColors` escape sequence and rewrites the session's Dynamic Profile JSON.

### ai signal-watch

```
ai signal-watch start <project> <session>
ai signal-watch stop <session>
ai signal-watch status
```

Manages signal-watch processes via Circus process manager (`circusd`). Signal-watch subscribes to NATS `handoff.{project}` for a CC session and writes pending marker files on arrival.

- `start` — registers a Circus watcher named `sw-{session}` and starts it. Idempotent (removes existing watcher first). Auto-starts `circusd` via `_ensure_circusd()` if not running.
- `stop` — removes the Circus watcher. Silent if circusd is not running (EXIT trap calls this unconditionally).
- `status` — lists all `sw-*` watchers and their status.

Circus uses IPC (not TCP) at `~/.local/state/ai-cli/circus.endpoint`. Config written to `~/.local/state/ai-cli/circus.ini`. Launched automatically by the bash session template at session start; stopped at EXIT.

### ai vpn-watch

```
ai vpn-watch
```

Internal entry point for the Circus-managed VPN state watcher. **Not intended for direct human invocation** — started and stopped automatically by `ai c -R` sessions.

Polls `_is_vpn_active()` every `remote.vpn_poll_interval` seconds (default: 3). On state change, waits 2 seconds (debounce) and re-checks. If the change is confirmed, publishes `{"vpn": bool, "ts": ...}` to NATS subject `vpn.state.changed`. All active `ai c -R` transport loops subscribe to this subject and switch between mosh and SSH accordingly.

**Transport selection logic (mosh-first):**
1. VPN active → SSH (direct IP via `[remote] vpn_host`)
2. VPN inactive → mosh (Tailscale/LAN IP via `[remote] host`)
3. Mosh fails in under 60s with non-zero exit → check if Tailscale is reachable:
   - **Tailscale unreachable (macOS):** launches `Tailscale.app`, polls for 20s until reachable, then retries mosh
   - **Tailscale still unreachable or non-macOS:** falls back to SSH
4. VPN detected after mosh starts (NATS event or direct poll) → terminate mosh, switch to SSH

Logs VPN transitions to `~/.local/state/ai-cli-utils/vpn-transitions.log` (JSONL).

### ai cdp

```
ai cdp start [-p|--port N] [-I|--no-incognito] [-t|--tunnel] [-L|--forward]
ai cdp stop  [-p|--port N] [-t|--tunnel]
ai cdp status
```

Launches and manages a Chrome/Chromium instance with the Chrome DevTools Protocol (CDP)
remote debugging endpoint exposed. Useful for attaching Playwright, agent-browser, or
any CDP-capable tool to a browser session without managing Chrome flags manually.

- `start` — launches Chrome in the background with `--remote-debugging-port=<port>` and
  `--user-data-dir=/tmp/chrome-debug-<port>` (required to force a fresh process). Adds
  `--incognito` by default (disable with `--no-incognito` / `-I`). Waits up to 5 s for
  `localhost:<port>/json/version` to respond, then prints `CDP ready at localhost:<port>`.
  Idempotent — prints "already running" if a live process is registered on that port.
  Writes PID to `~/.local/state/ai-cli-utils/cdp-<port>.pid`.
  - `-t`/`--tunnel` — also starts an SSH tunnel for the CDP port after Chrome launches.
    Defaults to a **reverse tunnel** (`-R`) so remote machines can reach the local Chrome
    via `localhost:<port>`. Pass `-L`/`--forward` to use a forward tunnel instead.
- `stop` — sends SIGTERM to the registered process and removes the PID file.
  Silent if no process is registered on that port. Pass `-t`/`--tunnel` to also stop
  the SSH tunnel registered on the same port.
- `status` — lists all registered CDP processes with port, PID, and alive/dead state.
  Cleans up stale PID files for dead processes.

**Flags:**

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--port` | `-p` | `9222` | CDP port |
| `--no-incognito` | `-I` | off | Disable `--incognito` flag |

**Config (`[cdp]` section in `config.toml`):**

```toml
[cdp]
# binary_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# port = 9222
```

Chrome binary auto-detected in this order: `binary_path` config key → well-known macOS/Linux/Windows
paths → `shutil.which` across common executable names.

### ai tunnel

```
ai tunnel start <local-port> [remote-port] [--forward]
ai tunnel stop <port>
ai tunnel status
```

Persistent SSH reverse tunnel backed by autossh. Automatically reconnects on network drop or broken pipe.

- `start` — launches `autossh -M 0 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -N -R {remote}:localhost:{local} {user}@{host}`. Writes PID to `~/.local/state/ai-cli/tunnel-{port}.pid`.
- `--forward` — use `-L` (forward) instead of `-R` (reverse, default).
- `remote-port` — defaults to `local-port` if omitted.
- `stop` — sends SIGTERM, removes PID file. Silent if not running.
- `status` — lists alive/dead tunnels, cleans stale PID files.

Requires `autossh` (`brew install autossh` / `apt install autossh`). Reads host/user from `[remote]` config section.

### ai update

```
ai update [--force]
```

Reinstalls `ai-cli-utils` from source. Bumps the version to `{base}.post{timestamp}` so uv sees it as a new package, runs `uv tool install --force`, then restores `pyproject.toml`.

- `--force` — additionally passes `--reinstall` to reinstall all dependencies (not just ai-cli-utils). Use for corrupt-environment recovery.
- `deploy` is kept as an alias for backward compatibility.
- On Mac (`AI_CLI_HOST=mac`): runs `git pull --rebase` first.

### ai setup

```
ai setup
```

Detects the runtime environment and configures the Claude Code session config (`CLAUDE.md`) accordingly.

- **Managed platform detected** (`~/projects/CLAUDE.md` exists): confirms `CLAUDE.md` (lean version) is correct — the shared AI orchestration rules are inherited from `~/projects/CLAUDE.md`. No file changes made.
- **Standalone install**: copies `CLAUDE-full.md` → `CLAUDE.md` (standalone config with all rules), then runs `git update-index --assume-unchanged CLAUDE.md` so the swap doesn't show as a local modification.

Run once after cloning or installing. Safe to re-run at any time.

### ai ps

```
ai ps [--kill] [--threshold N] [--json]
```

Process hygiene inspector. Lists all ai-cli-related processes with health scores and flags stale or suspect ones.

**Scoring:** each process is assigned a score based on tmux session presence, PID file state, and age. Higher score = more suspect.

**Flags:**
- `--kill` / `-k` — terminate processes at or above the threshold
- `--threshold N` / `-t N` — kill threshold (default from config, typically 50)
- `--json` / `-j` — output as JSON (useful for scripting)

Safe to run at any time; without `--kill` it only reads and reports.

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
| `publish-event` | `session_id event_type` | Publish fleet event |
| `publish-heartbeat` | `session_id json` | Publish worker heartbeat |
| `publish-session-event` | `session_id verb` | Publish session started/stopped |
| `signal-watch` | `project session_id` | Subscribe durable to `handoff.{project}`, claim tasks, write pending-file for auto-pickup |
| `cleanup-session-files` | `ai_name` | Remove session-specific icon PNG and Dynamic Profile JSON; called by EXIT trap on session end |
