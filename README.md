# ai-cli-utils

[![PyPI](https://img.shields.io/pypi/v/ai-cli-utils)](https://pypi.org/project/ai-cli-utils/)
[![Python](https://img.shields.io/pypi/pyversions/ai-cli-utils)](https://pypi.org/project/ai-cli-utils/)
[![License](https://img.shields.io/pypi/l/ai-cli-utils)](https://github.com/sergeiwallace/ai-cli-utils/blob/main/LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/sergeiwallace/ai-cli-utils/ci.yml?label=CI)](https://github.com/sergeiwallace/ai-cli-utils/actions)
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![codecov](https://codecov.io/gh/sergeiwallace/ai-cli-utils/graph/badge.svg)](https://codecov.io/gh/sergeiwallace/ai-cli-utils)

Unified AI session manager and automation toolkit for Claude Code, Gemini CLI, pi, and Codex.

<video src="demo/demo-20260420-053045-1cdf560.mp4" autoplay loop muted playsinline width="100%"></video>

*Four iTerm2 panes: launching Claude Code (`ai c 1`), Gemini CLI (`ai g 1`), pi (`ai p 1`), and Codex (`ai cx 1`), then browsing active sessions with `ai ls`, checking token quota with `ai quota status`, and running a Gemini query with automatic auth fallback.*

Run multiple AI coding sessions in parallel, each isolated in its own git worktree, with auto-resume, remote server support, cross-machine sync, resilient Gemini API access with automatic auth fallback, and persistent SSH tunnels via autossh. Every command and subcommand supports `--help` for inline usage reference.

## What it does

`ai-cli-utils` wraps [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [Gemini CLI](https://github.com/google-gemini/gemini-cli), [pi](https://github.com/badlogic/pi-mono), and [Codex](https://developers.openai.com/codex/cli/) in tmux sessions with production workflow features: numbered sessions, git worktree isolation, mosh/SSH remote access, cross-machine memory sync, and session lifecycle management. It also provides `ai gemini` — a Gemini CLI wrapper with 3-tier auth fallback (OAuth → free API key → paid API key) that automatically retries on capacity errors, so your research prompts keep working even when one auth method is exhausted. If you run multiple AI coding sessions daily, this tool eliminates the boilerplate.

## Features

| Feature | Description |
|---------|-------------|
| **Session management** | `ai c 1`, `ai g 1`, `ai p 1`, `ai cx 1` — named tmux sessions with auto-resume on disconnect |
| **Process hygiene** | `ai ps` — inspect and clean up stale ai-cli processes and PID files |
| **Session picker** | `ai ls` — fzf-powered session picker sorted by activity; `ai attach <name>` to attach directly |
| **Git worktree isolation** | Each session gets its own worktree — parallel work without branch conflicts |
| **Remote sessions** | `ai c -R` — run sessions on a remote server via mosh or SSH; Tailscale auto-started if mosh fails (macOS) |
| **Cross-machine sync** | `ai sync push/pull` — sync Claude Code memory, conversations, and task lists between machines |
| **Handoff queue** | `ai handoff post/check/claim/complete` — delegate tasks between sessions |
| **Fleet messaging** | NATS-based heartbeats, events, and sync notifications |
| **Stale session cleanup** | Automatic detection and cleanup of orphaned sessions |
| **Gemini with fallback** | `ai gemini "prompt"` — 3-tier auth fallback (OAuth → free API → paid API), auto-retry on capacity errors |
| **CC token tracking** | `ai cc-usage push/status` — scan CC session JSONL and push per-call token events to a usage-tracking backend |
| **SSH tunnels** | `ai tunnel start/stop/status` — persistent reverse tunnels via autossh (auto-reconnects on drop) |
| **Signal-watch** | Handoff delivery via Circus-managed background process — isolated from CC session lifecycle |
| **Notifications** | Multi-channel notification delivery (Discord webhook, ntfy push, OS native) with parallel dispatch, OS fallback, and persistent delivery log (`ai notifications log/list`) |
| **iTerm2 layout system** | `ai layout <name>` — YAML-driven window/tab/pane definitions; nested splits, startup commands, per-tab profiles |
| **iTerm2 session naming** | Automatically sets the iTerm2 Session Name and configures `allow-passthrough` + `automatic-rename off` so the session title stays correct |
| **Runtime tinted icons** | Pillow-based PNG icon generation at session launch; auto-contrast tint derived from tab color via HSL color theory |
| **Collision-free tab colors** | Lease-file-based color slot assignment; each session gets a unique color from a configurable palette |

## Installation

### macOS and Linux

Install [uv](https://docs.astral.sh/uv/) first:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then install the package:

```bash
uv tool install ai-cli-utils
```

Or with pipx:

```bash
pipx install ai-cli-utils
```

### Windows

Windows is supported via [MSYS2](https://www.msys2.org/) + Git Bash. Before installing:

1. Install [MSYS2](https://www.msys2.org/) and add it to your PATH.
2. Install tmux inside MSYS2: `pacman -S tmux`
3. Install Python 3.11+ from [python.org](https://www.python.org/downloads/) (the standard Windows installer).
4. Install [uv](https://docs.astral.sh/uv/) from PowerShell:

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

5. Restart Git Bash, then install the package: `uv tool install ai-cli-utils` (or `pipx install ai-cli-utils`)

### Install from a local clone

Install uv first using the instructions for your operating system above, then clone and install the repository.

#### macOS and Linux

```bash
git clone <repository-url>
cd ai-cli-utils
uv tool install .
ai --version
```

#### Windows (MSYS2 / Git Bash)

After installing uv from PowerShell, open Git Bash and run:

```bash
git clone <repository-url>
cd ai-cli-utils
uv tool install .
ai --version
```

For Windows toast notifications, install the optional extra:

```bash
uv tool install "ai-cli-utils[notify-win]"
```

**Unsupported on Windows:**
- `ai c -R` / remote sessions (requires SSH + mosh)
- `ai tunnel` (requires autossh)
- iTerm2 color slot management (macOS-only)

## Quick Start

```bash
# Verify install
ai --version

# Launch Claude Code session 1
ai c 1

# Launch a second session in parallel (isolated worktree)
ai c 2

# Launch Gemini CLI session
ai g research

# Launch pi or Codex session
ai p planning
ai cx review

# Resume a disconnected session
ai c -r 1

# Run a session on your remote dev server
ai c -R 1

# Sync Claude Code memory to another machine
ai sync push
```text

## Usage

### Session management

```bash
ai c <name>            # Start/resume Claude Code session
ai g <name>            # Start/resume Gemini CLI session
ai p <name>            # Start/resume pi session
ai cx <name>           # Start/resume Codex session
ai c -r/--resume       # Resume existing session explicitly
ai c -b/--bare         # Run bare (no tmux wrapper)
ai c -o/--once         # Run once (no auto-resume loop)
ai c -n/--notify       # Fire system notifications on task completion
ai c -s/--sandbox      # Explicitly enable sandboxing
ai c -W/--no-worktree  # Disable git worktree isolation
```text

#### What a new worktree tracks

A new session worktree is based on, and tracks, the branch its repository
integrates through — **not** `main` unconditionally. That branch is resolved
config first, then from the repository's own checkout:

1. the `[worktree_upstream]` entry for the repository, if it has one;
2. otherwise the branch the repository's main checkout is currently on.

So a repository sitting on `main` behaves exactly as before, and one parked on a
long-running workspace branch gets worktrees that track that branch. This matters
for a shared repository on a pull-request workflow, where `main` is the branch
nobody pushes to directly.

```toml
[worktree_upstream]
myproject = "workspace"
```

If the resolved branch does not exist on `origin` — a workspace branch that has
not been pushed yet — the worktree is created with **no upstream** and a warning,
rather than quietly falling back to `origin/main`. A missing upstream makes the
first `git push` stop and ask, which is the safe direction. If the branch exists
nowhere at all, or the repository has no `origin` remote, worktree creation fails
loudly instead of guessing.

When a worktree's `.envrc` is byte-for-byte identical to a repository-root
`.envrc` that `direnv` can successfully execute, `ai` approves that new
worktree path automatically.
Changed or unapproved `.envrc` files still require an explicit `direnv allow`.
Targeted session launches (`ai c <name>`, `ai g <name>`, `ai p <name>`, and `ai cx <name>`) do not pause for
unrelated project-registry discovery prompts.

### Remote sessions

```bash
ai c -R/--remote <name>            # Connect to remote server (uses config)
ai c -R -p/--project myproject <name>  # Specify remote project directory
```text

### Cross-machine sync

```bash
ai sync push [-m] [-n] [-v] [-f]   # Push state to remote; aborts if remote has newer files (use -f to force)
ai sync pull [-m] [-n] [-v] [-f]   # Pull remote state to local
ai sync conflicts                   # Show unresolved sync conflicts
ai sync watch [-v]                  # Watch for sync events via NATS
```text

Flags: `-m`/`--memories-only`, `-n`/`--dry-run`, `-v`/`--verbose`, `-f`/`--force`

Sync includes transcripts, memory, history, and task JSON files under `~/.claude/tasks/<session-name>/`.
On pull, transcript `cwd` and `originalCwd` fields are rewritten to the receiving machine's configured
`[project] projects_dir` (default `~/projects`) so sessions remain resumable across machines.

### Gemini with auth fallback

```bash
ai gemini "prompt" -m deep-think          # Run with 3-tier fallback, stdout + auto file
ai gemini "prompt" -m pro -o output.md    # Specify output file
ai gemini "prompt" -m flash -q            # File only, no stdout (-q/--quiet)
cat prompt.txt | ai gemini -m deep-think  # Pipe from stdin
ai gemini "prompt" -m flash -F            # Stdout only, no file (-F/--no-file)
ai gemini "prompt" -m flash -t 120        # 120s timeout (-t/--timeout)
ai gemini "prompt" -m deep-research       # Gemini Deep Research (Interactions API, polls until done)
ai gemini "prompt" -m deep-think -s 2     # Skip OAuth, go straight to REST API key
```text

**Auth fallback chain (automatic on 429/capacity errors):**
1. Gemini CLI OAuth (free — Google AI subscription)
2. REST API with `GOOGLE_API_KEY_FREE_TIER` — **Flash/Gemma models only.** Pro, image-generation variants, and deep-research have no free quota; tier 2 is skipped automatically for ineligible models.
3. REST API with `GOOGLE_API_KEY_TIER_1` — paid, works for all models.

`deep-research` uses OAuth first and falls back directly to tier 3 — tier 2 is always skipped for it. Use `-s 2` to skip OAuth for Flash calls. For Pro/deep-think where OAuth fails, use `-s 3`.

**Model aliases:** `deep-think`, `pro`, `flash`, `flash-lite`, `deep-research`, or any full Gemini model ID.

**Logs:** `~/.local/state/ai-cli/gemini-logs/` (JSONL). **Auto output:** `~/.local/state/ai-cli/gemini-output/`.

### Session picker

```bash
ai ls              # Interactive fzf session picker (installs fzf via apt if absent)
ai ls -a/--all     # Show all tmux sessions, not just ai-cli sessions
ai attach <name>   # Attach directly to a named tmux session
```text

### SSH tunnels

Keep a reverse tunnel alive across network drops (useful for remote browser automation via CDP):

```bash
ai tunnel start 9222              # Reverse tunnel: localhost:9222 → server:9222 (auto-reconnects)
ai tunnel start 9222 9223         # Different remote port
ai tunnel start 9222 -L/--forward # Forward tunnel instead of reverse
ai tunnel stop 9222               # Stop the tunnel
ai tunnel status                  # List all active tunnels
```text

Requires `autossh` (`brew install autossh` / `apt install autossh`). Host/user from `[remote]` config.

### Handoff queue

```bash
ai handoff post      # Post a task for another session to pick up
ai handoff check     # Check for pending handoffs
ai handoff claim     # Claim a handoff
ai handoff complete  # Mark a handoff as done
```text

### iTerm2 layouts

```bash
ai layout list                   # List available layouts in ~/.config/iterm2/layouts/
ai layout validate <name>        # Validate YAML schema
ai layout profiles <name>        # Regenerate Dynamic Profiles without relaunching window
ai layout <name>                 # Apply layout: open new iTerm2 window with tabs/panes as defined
```text

Layout files live at `~/.config/iterm2/layouts/<name>.yaml`. Each tab can define a base profile, tab color, icon tint, and a root pane with optional nested vertical/horizontal splits, each with a startup directory and command.

### Other commands

```bash
ai ps                    # Show ai-cli processes with health scores; flag suspect/stale ones
ai ps --kill             # Terminate processes above the suspect threshold
ai signal-watch status   # List Circus-managed signal-watch processes
ai memory watch          # Watch for Claude Code memory file changes
ai quota watch           # Monitor API quota usage
ai telemetry writer      # Run telemetry writer daemon
ai update [-f/--force]   # Update to latest from source; --force also reinstalls all deps
ai update -q/--quiet     # Capture git/uv output; report one line naming the new version
ai update -v/--verbose   # Show the full transcript even when --quiet is also passed
ai reconnect             # Print reconnect commands for remote sessions
```text

### Staying current at session launch

`uv tool install` copies the package into its own environment, so a source change
does not take effect until it is reinstalled. `ai c <n>` therefore checks, before
launching, whether the installed build still matches the source — and reinstalls
it if not.

The check is a content fingerprint of the files that ship in the package
(`pyproject.toml` and `src/`), not the repository's current commit. A commit
pointer answers the wrong question in both directions: it advances for commits
that change nothing installed (a docs edit), and it does not move at all for an
uncommitted edit under `src/`, which is the most common way the installed build
goes stale while a change is being tested.

When a reinstall is needed, the launch runs it quietly and prints one line:

```text
ai-cli-utils 0.7.0.post20260814190112 installed (cache-bypassing reinstall)
```

The version carries a `.post<timestamp>` suffix so uv cannot serve a cached build
of an already-seen version; the file on disk is restored to its base version
immediately afterwards. Failures are never quiet — the captured `git`/`uv`
transcript is printed in full on stderr, and the stamp is cleared so the next
launch retries rather than remembering a failed install as done.

Two escape hatches:

- `AI_CLI_UPDATE_VERBOSE=1` — show the whole transcript at launch instead of the
  one-line summary.
- `ai update --force` — reinstall on demand, bypassing uv's cache and
  reinstalling dependencies. It never consults the fingerprint, so it is the way
  to force a refresh when the installed build is suspect.

## Configuration

### Claude Code Session Config

This repo ships two Claude Code session config files:

- **`CLAUDE.md`** — lean config for users with a shared `~/projects/CLAUDE.md` (managed platform setup), where global AI orchestration rules are inherited from there.
- **`CLAUDE-full.md`** — standalone config with all rules included, for everyone else.

After installing, run `ai setup` once to automatically detect your environment and configure the right file:

```bash
ai setup
```text

`ai setup` checks for a shared `~/projects/CLAUDE.md`. If found, it confirms the lean `CLAUDE.md` is correct and takes no action. If not found, it copies `CLAUDE-full.md` → `CLAUDE.md` and marks the file as `assume-unchanged` in git so it won't show as locally modified.

### Tool Config

Configuration lives in `~/.config/ai-cli/config.toml`. A default config is created on first run.

```toml
[project]
main_project = "myproject"     # default project directory under ~/projects/

[remote]
default = "server"             # used by `ai c -R`

[remote.machines.server]
host = "1.2.3.4"               # remote dev server
user = "ubuntu"
transport = "mosh"              # "mosh" (default) or "ssh"
# port = 22
# identity_file = "~/.ssh/id_ed25519"

[worktree]
enabled = true                 # git worktree isolation per session

[worktree_upstream]
# Integration branch for session worktrees, keyed by repository directory name.
# Omit a repository to use the branch its own checkout is on.
# myproject = "workspace"

[session]
stale_session_timeout = 15     # minutes before cleanup considers a session stale

[sync]
remote_host = "user@host"      # for cross-machine sync

[behavior]
notify_on_exit = true          # desktop notifications on task completion

[machine]
# host_id = "mac"              # optional: identify this machine for targeted handoffs (ai handoff --for-machine)

[update]
# extra_venvs = []             # optional: additional venv paths to reinstall into after 'ai update'
```text

To add another machine, give it a short alias and select it for one launch with
`ai c -R -m alias` (or `ai c --remote --remote-machine alias`). `-R` alone uses
`[remote] default`. Existing flat `[remote]` configurations remain supported as
one implicit default machine.

### iTerm2 Config

iTerm2 visual identity settings live in `~/.config/ai-cli-utils/iterm2.toml` (created on first use):

```toml
[iterm2.base_profiles]
# Base Dynamic Profile each session type inherits from
cc         = "ClaudeCode"
gemini     = "GeminiCLI"
shell      = "ShellUtility"

[iterm2.project_colors]
# Pin specific projects/sessions to preferred palette colors
# myproject = "purple"
# research  = "teal"

[iterm2.icon_color_overrides]
# Override auto-contrast icon tint per palette color slot
# purple = "#da7756"
```text

The color palette (16 entries, configurable) is defined in `[iterm2.palette]`. Each session gets a collision-free slot via lease files. When a tab color is set, the session icon is automatically tinted with a contrasting color (180° HSL hue rotation). When no color is set, the Claude brand orange (`#da7756`) is used as fallback.

Set `AI_HOST` in `~/.zshenv` (sourced by all zsh sessions, including non-interactive ones) to identify the machine. This is used when posting targeted handoffs (`ai handoff post --for-machine mac ...`):

```bash
export AI_HOST=mac    # or "hetzner", "work-laptop", etc.
```text

## Requirements

- Python 3.11+
- [tmux](https://github.com/tmux/tmux) (on Windows: install via MSYS2 — `pacman -S tmux`)
- `zsh` **or** `bash` — the tmux session pane runs the generated session script under zsh when it is installed, and falls back to bash otherwise
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [Gemini CLI](https://github.com/google-gemini/gemini-cli), [pi](https://github.com/badlogic/pi-mono), and/or [Codex](https://developers.openai.com/codex/cli/)
- [direnv](https://direnv.net/) (optional — when installed, sessions start under `direnv exec` so the project `.envrc` is loaded; sessions start normally without it)
- [mosh](https://mosh.org/) (optional, for remote sessions — falls back to SSH; Linux/macOS only)
- [autossh](https://www.harding.motd.ca/autossh/) (optional, for `ai tunnel` — `brew install autossh` / `apt install autossh`; Linux/macOS only)
- [NATS](https://nats.io/) (optional — enables real-time handoff delivery, sync watch, and session events; see [NATS Setup Guide](docs/guides/nats-setup.md))

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and contribution guidelines.

## License

[MIT](LICENSE) -- Sergei Wallace
