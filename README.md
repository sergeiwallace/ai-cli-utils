# ai-cli-utils

[![PyPI](https://img.shields.io/pypi/v/ai-cli-utils)](https://pypi.org/project/ai-cli-utils/)
[![Python](https://img.shields.io/pypi/pyversions/ai-cli-utils)](https://pypi.org/project/ai-cli-utils/)
[![License](https://img.shields.io/pypi/l/ai-cli-utils)](https://github.com/sergeiwallace/ai-cli-utils/blob/main/LICENSE) <!-- public-hygiene: allow -->
[![CI](https://img.shields.io/github/actions/workflow/status/sergeiwallace/ai-cli-utils/ci.yml?label=CI)](https://github.com/sergeiwallace/ai-cli-utils/actions) <!-- public-hygiene: allow -->
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![codecov](https://codecov.io/gh/sergeiwallace/ai-cli-utils/graph/badge.svg)](https://codecov.io/gh/sergeiwallace/ai-cli-utils) <!-- public-hygiene: allow -->

Unified AI session manager and automation toolkit for Claude Code, Gemini CLI, pi, and Codex.

<video src="demo/demo-20260420-053045-1cdf560.mp4" autoplay loop muted playsinline width="100%"></video>

*Four iTerm2 panes: launching Claude Code (`ai c 1`), Gemini CLI (`ai g 1`), pi (`ai p 1`), and Codex (`ai cx 1`), then browsing active sessions with `ai ls` and checking token quota with `ai quota status`.*

Run multiple AI coding sessions in parallel, each isolated in its own git worktree, with auto-resume, remote server support, cross-machine sync, and persistent SSH tunnels via autossh. Every command and subcommand supports `--help` for inline usage reference.

## What it does

`ai-cli-utils` wraps [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [Gemini CLI](https://github.com/google-gemini/gemini-cli), [pi](https://github.com/badlogic/pi-mono), and [Codex](https://developers.openai.com/codex/cli/) in tmux sessions with production workflow features: numbered sessions, git worktree isolation, mosh/SSH remote access, cross-machine sync, and session lifecycle management. If you run multiple AI coding sessions daily, this tool eliminates the boilerplate.

## Features

| Feature | Description |
|---------|-------------|
| **Session management** | `ai c 1`, `ai g 1`, `ai p 1`, `ai cx 1` — named tmux sessions with auto-resume on disconnect |
| **Process hygiene** | `ai ps` — inspect and clean up stale ai-cli processes and PID files |
| **Session picker** | `ai ls` — fzf-powered session picker sorted by activity; `ai attach <name>` to attach directly |
| **Git worktree isolation** | Each session gets its own worktree — parallel work without branch conflicts |
| **Remote sessions** | `ai c -R -m <alias>` — run sessions on a configured remote server via mosh or SSH; `ai ssh [alias]` opens a matching shell |
| **Cross-machine sync** | `ai sync push/pull` — sync Claude Code memory, conversations, and task lists between machines |
| **Fleet messaging** | NATS-based heartbeats, events, and sync notifications |
| **Stale-session reaper** | `ai c` keeps an independent, heartbeat-corroborated watcher running in observe mode; set `mode = "reap"` explicitly to enable reaping |
| **Session recovery** | `ai session-audit`, `ai session-adopt`, and `ai cc-migrate` find, adopt, and move resumable Claude Code sessions safely |
| **CC token tracking** | `ai cc-usage push/status` — scan CC session JSONL and push per-call token events to a usage-tracking backend |
| **SSH tunnels** | `ai tunnel start/stop/status` — persistent reverse tunnels via autossh (auto-reconnects on drop) |
| **Notifications** | Multi-channel notification delivery (Discord webhook, ntfy push, OS native) with parallel dispatch, OS fallback, and persistent delivery log (`ai notifications log/list`) |
| **iTerm2 layout system** | `ai layout <name>` — YAML-driven window/tab/pane definitions; nested splits, startup commands, per-tab profiles |
| **iTerm2 session naming** | Automatically sets the iTerm2 Session Name and configures `allow-passthrough` + `automatic-rename off` so the session title stays correct |
| **Runtime tinted icons** | Pillow-based PNG icon generation at session launch; auto-contrast tint derived from tab color via HSL color theory |
| **Collision-free tab colors** | Lease-file-based color slot assignment; each session gets a unique color from a configurable palette |

## Installation

### macOS and Linux

Install [uv](https://docs.astral.sh/uv/) with your OS package manager first:

```bash
# macOS
brew install uv
# Debian/Ubuntu
sudo apt install uv
# Fedora
sudo dnf install uv
```

Then install the package:

```bash
uv tool install ai-cli-utils
```

Or with pipx:

```bash
pipx install ai-cli-utils
```

### Windows (experimental)

Windows use via [MSYS2](https://www.msys2.org/) + Git Bash is experimental. The
Windows-specific session-launch paths exist, but real tmux-server integration and
the interactive launch lifecycle (keyboard interrupts, bare-mode display, and
stale-worktree recovery) are not verified on Windows. That integration suite is
skipped after it hung in Windows CI. Use the following setup with that limitation
in mind. Before installing:

1. Install [MSYS2](https://www.msys2.org/) and add it to your PATH.
2. Install tmux inside MSYS2: `pacman -S tmux`
3. Install Python 3.14 from [python.org](https://www.python.org/downloads/) (the standard Windows installer).
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

**Unavailable or unverified on Windows:**

- Real tmux-server session integration, including keyboard interrupts, bare-mode
  display, and stale-worktree recovery (unverified; automated coverage is skipped)
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
```

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
ai c -q/--quiet        # Suppress launch progress (warnings and errors still print)
ai c -v/--verbose      # Add skipped steps, resolved paths and timings to the launch progress
```

#### Restarting an idle Claude Code session

```bash
ai -Y session-1                    # Check every restart gate and passive trigger
ai --restart-check session-1 -j     # The same report as JSON
ai -X session-1                    # Restart by custom title
ai --restart-session <session-uuid> # Or by conversation UUID or tmux session name
ai c 1 -p myproject -X             # Resolve the existing launch slot; create nothing
ai c 1 -R -p myproject -X          # Resolve and restart on the configured remote host
ai -X                              # Inside the target pane: detach, then wait for idle
```

`-X/--restart-session [TARGET]` and `-Y/--restart-check [TARGET]` work at the top
level and on `ai c`. `-j/--json` selects structured output. An absent or ambiguous
target refuses. A self-restart returns immediately after starting a detached helper;
the helper owns the lock and applies the same gates after the current turn ends.
Check mode never signals, injects, refreshes a script or publishes a restart request.

The command requires a live Claude Code process inside a compatible `ai c`
supervisor, an idle registry and completed transcript turn, no pending background
agents or shells, and no unexplained descendant processes. Configured MCP server
trees are allowed. It requires `remoteControlAtStartup = true` in the effective
user, project and local Claude Code settings, without changing those files.
Unknown evidence refuses. Transcript evidence is read backwards in bounded chunks;
histories larger than 8 MiB currently refuse with `background-work` because older
background launches cannot be excluded within that bound.

Context must be at or below 20 percent, or within one assistant turn of an automatic
compaction. Missing usage and unknown model windows do not count as zero. Configure
`context_window_tokens` for an unrecognised or extended model window. A manual
compaction passes only through the percentage gate. The save-state pre-step and
automatic scheduling are separate integrations; a required pre-step currently
refuses with `context` and `pre-step unavailable`.

The restart pins the exact conversation before signalling, atomically prepares a
compatible replacement child, and sends SIGTERM only to the verified agent PID.
If necessary, SIGKILL follows after the grace period and another identity and gate
check. The pane and supervisor survive. Unsupported running programs refuse with
`supervisor-upgrade-required`. On macOS, compatibility requires proof that the
running shell loaded the inspected script; an already-refreshed script without
readable running-program evidence can require a separately reviewed migration.
Windows process signalling is unsupported: use the configured injection route,
or the command refuses with `no-mechanism`.

Restart settings are read on every call from `config.toml`:

```toml
[restart_session]
context_threshold_pct = 20
compaction_fresh_turns = 1
context_window_tokens = 0
settle_seconds = 60
min_uptime_seconds = 1800
cooldown_seconds = 3600
term_grace_seconds = 20
relaunch_timeout_seconds = 180
mechanism = "kill"                 # "exit" uses the injection adapter
pre_step = true
compaction_wait_seconds = 1800
auto = true                        # Default for the separate automatic evaluator
auto_interval_seconds = 600
```

External commands are argv arrays in `restart-session.d/*.toml` beside the config
file. Duplicate adapters refuse. Placeholders are expanded as individual arguments,
without a shell: `{tmux_pane}`, `{tmux_session}`, `{session_id}`, `{title}`, `{command}`.

```toml
[inject]
argv = ["python3", "-m", "example_injector", "--pane", "{tmux_pane}", "--command", "{command}"]

[observer]
argv = ["ssh", "host-b", "list-sessions-as-seen-from-here"]
```

The injection adapter submits `/exit` for `mechanism = "exit"` or unavailable
signal access. It must preserve the session when background work is reported.
A non-zero exit refuses; a zero exit still requires replacement verification.
The observer returns JSON with `status` (`seen`, `absent`, `unknown`), `complete`,
`observed_at` (epoch seconds or an ISO timestamp with a timezone) and `rows` carrying
`session_id`, `title` and, where available, `bridge_session_id`. Evidence must be
observed during the current invocation and identify the replacement conversation
and expected title. Duplicate or mismatched identities fail. Failed commands,
timeouts, malformed, stale or partial listings remain unknown.

The report separates `local`, `peer`, `message_delivery` and `mobile` verdicts.
Local verification requires the replacement's exact UUID, user title, bridge
registration, passive messaging socket connection and complete local agent listing.
Without verified peer evidence the result is non-zero `verification-incomplete`,
even after a local restart. Message delivery and mobile visibility remain
`UNVERIFIED` until independently observed.

All restart actors must hold the same exclusive OS lock at
`<state dir>/restart-session/<tmux session>.lock` through revalidation, any pre-step,
action and verification. Never unlink or replace that lock file. A separate atomic
`<tmux session>.json` records `last_restart`, conversation and outcome, including
failed attempts. Cooldown expiry does not authorize automatic retry after failed
verification. The durable `.request.json` and per-request `.launched` receipt fence
an interrupted or failed resume; no title lookup or fresh-conversation fallback is
allowed. A manual retry validates the same live conversation before replacing its
failed request. An incompatible or no-longer-live target needs operator recovery;
do not remove a request merely to bypass its refusal.

#### Session names

Every session is named `{repo-alias}-{host-alias}-{optional-slug}-{N}`, and that
one name is its worktree directory (`.worktrees/<name>`), its branch (`wt-<name>`),
its transcript title and its pinned task list. `N` auto-increments from 1, and an
omitted slug leaves no double hyphen:

```bash
ai c            # myrepo-box-1
ai c review     # myrepo-box-review-1
ai c 3          # myrepo-box-3 (or the existing session in that slot)
```

`{repo-alias}` is the repository's registered task prefix (`ai register -x`).
Where it comes from a persistent `config/fleet-projects.toml` registry, from
`ai register -x` or from the registration prompt, it must be lowercase letters
and digits starting with a letter, with single hyphens only between them
(`ai-cli`), and at most that registry's top-level `max_length` (6 when absent;
prefer 3 or fewer). Prefixes already in the per-machine `config.toml` tiers keep
resolving as before.

`{host-alias}` is this machine's short alias (lowercase letters and digits
starting with a letter, at most the file's `max_length`, 5 by default; prefer 3
or fewer), resolved at launch from this machine's host id (`AI_HOST`, then
`/etc/ai-harness/machine`, then the hostname) by the first of these that names it:

1. `<projects>/ai-harness/config/machine-host-aliases.toml`, when an ai-harness
   checkout exists (`$PROJECTS_DIR`, then `[project] projects_dir`, default
   `~/projects`). When it names the host, no other file is read.
2. `config/machine-host-aliases.toml` in any other repository under the projects
   directory, in directory-name order. A repository joins by shipping the file.
   Two of them giving the host different aliases is refused, naming both.
3. `~/.config/ai-cli-utils/machine-host-aliases.toml`

```toml
max_length = 5

[aliases]
my-host = "box"
```

On a machine none of them names, `ai c` in a terminal asks for the alias, prints
the limit and the full path of the file that sets it, and appends the answer
under `[aliases]` in the first of those files that exists (creating the local
file when none does). The edit is parsed and re-resolved before it replaces the
file, so a bad answer or a malformed result leaves the file untouched. When the
file is in a git checkout, commit and push it so every machine sees it.
`ai c --dry-run` asks and plans but writes nothing. Without a terminal (a hook,
an agent shell, `ai update`) the launch is refused with every path checked, rather than given an
invented alias. Sessions named before the host alias joined the name
(`myrepo-3`) still resolve by slot until they are moved with
`ai session-migrate myrepo-3 myrepo-box-3`; nothing new is created in that form.

Choosing between the two verbs: `ai session-migrate OLD NEW` renames a session
already registered in an `ai` slot (worktree, branch, transcript, tasks, memory,
resume state, and its agent worktrees and `wt/OLD/*` branches in every repo
follow the new name); `ai session-adopt NAME` registers a
session that was started outside `ai`, such as a plain `claude` in the repo root.

```bash
ai session-migrate myrepo-3 myrepo-box-3   # rename a registered slot
ai session-adopt myrepo-box-4              # register a session started outside ai
```

A migration carries everything `ai` keys by the session's name or slot, listed
first by `ai session-migrate -n` ([the full table](docs/tools/cc-session-adoption.md#migrating-a-session-that-already-has-a-slot-ai-session-migrate)).
State owned by other tools joins the same run through a hook file in
`session-migrate.d/` under the config directory: each hook is planned with
`--dry-run` before anything is written, can refuse the migration, and runs after
the core moves ([hook contract](docs/reference/session-migrate-hooks.md)). An
`ai session-adopt` that retitles the session runs the same hooks the same way.

A session that is finished for good has its home slot retired by its owner with
`ai session-retire NAME`; no automatic reap removes a session home. The command
refuses while the session runs (a Claude Code process, its tmux session, or a
heartbeat fresher than the reaper's `stale_after_seconds`), while the checkout
has uncommitted or untracked work, or while its checked-out commit is in neither
its upstream nor `origin/main`. Otherwise it anchors the tips of the checked-out
branch and `wt-NAME` under `refs/preserved/session-retire/`, runs
`git worktree remove`, drops the canonical-registry entry and appends a row to
`session-retirements.jsonl` in the state directory. The branches and the Claude
Code project directory stay ([details](docs/tools/cc-session-adoption.md#retiring-a-dead-sessions-slot-ai-session-retire)).

```bash
ai session-retire myrepo-3 -n   # what would be removed and preserved, nothing written
ai session-retire myrepo-3      # prompts, then retires
```

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

`direnv` approvals are per-path, so a brand new worktree's `.envrc` is unapproved
even when the repository root's identical copy is approved. When `direnv` is
installed, `ai` therefore approves the worktree itself — after creating it and
before starting the agent — and prints the path it approved.

The scope is exactly the worktrees `ai` creates under `.worktrees/`, only their
own `.envrc`, and only when that file is **byte-identical to the repository
root's**. The repository root, an `.envrc` inherited from a parent directory, and
worktrees registered anywhere else are never approved automatically: `ai` vouches
for a directory because it created it, which is not something it can claim about a
directory it merely launched in. It does not vouch for the file's *contents* at
all — git writes whatever the checked-out branch carries — so an `.envrc` that
differs from the root's keeps `direnv`'s normal prompt, including one a branch
legitimately edits and one the session itself rewrote. Automatic approval
therefore only ever spares you re-approving content you already have at the
repository root.

Note that this requires the root's `.envrc` to *exist and match*, not to be
approved. Requiring an approved root would make the feature do nothing on a host
where nothing is approved yet, which is exactly the host that needs it. A
worktree `direnv` has already approved is left alone, and a host with no `direnv`
skips the step silently. Set `AI_CLI_SKIP_DIRENV=1` to turn automatic approval
off.
Targeted session launches (`ai c <name>`, `ai g <name>`, `ai p <name>`, and `ai cx <name>`) do not pause for
unrelated project-registry discovery prompts.

### Remote sessions

```bash
ai c -R/--remote <name>            # Connect to remote server (uses config)
ai c -R -p/--project myproject <name>  # Specify remote project directory
ai c -R -m/--remote-machine <alias> <name>  # Select a configured remote machine
ai ssh [alias]                     # Open an interactive shell on that machine
```

### Cross-machine sync

```bash
ai sync push [-m] [-n] [-v] [-f]   # Push state to remote; aborts if remote has newer files (use -f to force)
ai sync pull [-m] [-n] [-v] [-f]   # Pull remote state to local
ai sync conflicts                   # Show unresolved sync conflicts
ai sync watch [-v]                  # Watch for sync events via NATS
```

Flags: `-m`/`--memories-only`, `-n`/`--dry-run`, `-v`/`--verbose`, `-f`/`--force`

Sync includes transcripts, memory, history, and task JSON files under `~/.claude/tasks/<session-name>/`.
On pull, transcript `cwd` and `originalCwd` fields are rewritten to the receiving machine's configured
`[project] projects_dir` (default `~/projects`) so sessions remain resumable across machines.

### Session recovery

```bash
ai session-audit                    # Find titled sessions that cannot resume
ai session-audit -a/--adopt         # Adopt every safe session
ai session-adopt <name>             # Adopt one session into this repository
ai session-retire <name>            # Retire a dead session's home slot
ai cc-migrate <destination>         # Move a transcript between project roots
```

### Session picker

```bash
ai ls              # Interactive fzf session picker (installs fzf via apt if absent)
ai ls -a/--all     # Show all tmux sessions, not just ai-cli sessions
ai attach <name>   # Attach directly to a named tmux session
```

### Chief-of-staff (firstmate) session

One coordinating session per machine that dispatches to your other sessions and surfaces their blockers ([Firstmate](https://github.com/kunchenguid/firstmate)-style, with its `FM_HOME` in a per-machine state directory rather than a repository). `-F/--firstmate` is a mode on every engine command, so the chief can be any agent you launch. A chief always launches into the `ai-harness` repository (prefix `aih`), whatever repository you run the command from. These examples use host alias `fw`:

```bash
ai c firstmate              # Claude chief: session aih-fw-firstmate-1, tmux c-aih-fw-firstmate-1
ai c firstmate-3            # Chief in slot 3: aih-fw-firstmate-3 (any integer N); `ai c firstmate 3` is the same
ai c 3 -F/--firstmate       # The same slot through the flag
ai c firstmate -p aih       # -p may name ai-harness (by alias, prefix or directory), never another project
ai c --firstmate review     # Optional suffix: aih-fw-firstmate-review-1
ai p --firstmate            # Same mode on Pi: tmux p-aih-fw-firstmate-1 (likewise `ai g`, `ai cx`)
ai cos                      # Shorthand for `ai c --firstmate` (takes the same optional suffix or N)
ai fm 1 / ai firstmate 1    # Aliases of `ai cos 1`
ai c -F -k/--machine-key K  # Machine key naming the chief home (default: $AI_MACHINE_ID)
ai c -F -H/--fm-home DIR    # Use an explicit chief home instead of $XDG_STATE_HOME/firstmate/chief-of-staff/<key>
ai c -F --dry-run           # Print the resolved plan; registers and starts nothing
ai c firstmate-2 -R -m fw   # Run the chief on remote machine `fw`: named, homed and registered there
ai cos -R -m fw             # Same, through the shorthand
ai fm 1 -r -R -p aih        # From the Mac: attach to or resume chief 1 on the remote machine
```

Every one of these attaches, resumes or creates, decided on the machine the chief runs on (the remote one with `-R`):

| Chief N on that machine | What the command does |
|---|---|
| running (its tmux session has a live process) | attach to the tmux session its registration names (`c-…` or `c-r-…`, however it was launched); nothing is created or re-registered |
| tmux session alive but its process exited, or registered but not running, or (with `-r`) a saved transcript | relaunch under the same name, which resumes its worktree and conversation |
| nothing | create and register it; with `-r`, refuse with "no resumable chief session …" |
| a different chief is running | refuse, naming the running chief and the command that attaches to it |

An unnumbered request (`ai cos`, `ai c --firstmate`, `ai c firstmate`) means whichever chief is running or registered.

The name is `aih-{host-alias}-firstmate-[suffix-]{index}`, with the index allocated from the same lowest-free-slot scan as any other session unless you name it, and the worktree lands at `ai-harness/.worktrees/aih-{host-alias}-firstmate-[suffix-]{index}`. A positional `firstmate` or `firstmate-N` is the chief launch itself, with everything `--firstmate` does; any other name carrying a `firstmate` segment (`firstmate-x`) is refused without the flag, so an ordinary session can never pass for the chief.

Worktree isolation is **on**, like every other session: a session working in a main tree is the exception, not the chief. Pass `-W/--no-worktree` if you want the chief in the repo root instead.

`-k/--machine-key` and `-H/--fm-home` are refused without `-F`, so neither silently applies to an ordinary session. The home must already exist with its `config/message-transports.json` (your environment's installer seeds it); with neither `-k` nor `$AI_MACHINE_ID` set (a non-interactive shell, such as the remote side of `-R`, can miss an rc-file export) the one seeded home under `$XDG_STATE_HOME/firstmate/chief-of-staff/` is used, and several are refused; a chief launch refuses a missing or unseeded home and refuses a second chief while the first is running (it names the command that attaches to it). A chief running in tmux under a registration that names another (a later launch overwrote it and died) is still found by its tmux session name and re-registered when you attach. On launch it records the chief's agent name and tmux target in `<home>/state/chief-session.json` and the schema-2 `<home>/registry.json`, then hands off to the ordinary launch with `FM_HOME` / `AI_SESSION_ROLE=chief-of-staff` exported into the pane.

A chief does not have to run on the machine it coordinates: cross-machine messaging (Agent Mail, Buzz, git-inbox, native SendMessage) reaches it wherever it runs. With `-R/--remote` (and optionally `-m MACHINE`) the local `ai` only plans the ssh hand-off and forwards `--firstmate`, `-k` and `-H`; the remote `ai` resolves the remote machine's chief home, attaches to, resumes or creates the chief there (refusing a different live one), writes the registration there and names the session under the remote host's alias (`c-r-aih-{remote-alias}-firstmate-{index}`). An unnumbered `-R` request is not allocated a slot on the dispatching side, so the remote host picks its own chief. Nothing is resolved or written on the dispatching machine, so it needs neither a chief home nor the remote's host alias.

### SSH tunnels

Keep a reverse tunnel alive across network drops (useful for remote browser automation via CDP):

```bash
ai tunnel start 9222              # Reverse tunnel: localhost:9222 → server:9222 (auto-reconnects)
ai tunnel start 9222 9223         # Different remote port
ai tunnel start 9222 -L/--forward # Forward tunnel instead of reverse
ai tunnel stop 9222               # Stop the tunnel
ai tunnel status                  # List all active tunnels
```

Requires `autossh` (`brew install autossh` / `apt install autossh`). Host/user from `[remote]` config.

### iTerm2 layouts

```bash
ai layout list                   # List available layouts in ~/.config/iterm2/layouts/
ai layout validate <name>        # Validate YAML schema
ai layout profiles <name>        # Regenerate Dynamic Profiles without relaunching window
ai layout <name>                 # Apply layout: open new iTerm2 window with tabs/panes as defined
```

Layout files live at `~/.config/iterm2/layouts/<name>.yaml`. Each tab can define a base profile, tab color, icon tint, and a root pane with optional nested vertical/horizontal splits, each with a startup directory and command.

### Other commands

```bash
ai ps                    # Show ai-cli processes with health scores; flag suspect/stale ones
ai ps --kill             # Terminate processes above the suspect threshold
ai memory watch          # Watch for Claude Code memory file changes
ai quota watch           # Monitor API quota usage
ai telemetry writer      # Run telemetry writer daemon
ai doctor [-n/--dry-run] # Check required native tools and direnv
ai register -p <path> -x <prefix>  # Register a repository and task prefix
ai ws pull [-d/--dry-run]          # Pull/rebase repositories in a workspace
ai upgrade            # Upgrade an installed uv tool from PyPI
ai update [-f/--force]   # Update to latest from source; --force also reinstalls all deps
ai update -q/--quiet     # Capture git/uv output; report one line naming the new version
ai update -v/--verbose   # Show the full transcript even when --quiet is also passed
ai reconnect             # Print reconnect commands for remote sessions
```

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

```
ai-cli-utils 0.8.0.post20260814190112 installed (cache-bypassing reinstall)
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

### Launch output

Every line the launcher prints on its own behalf follows one grammar on stderr, so
a launch can be read at a glance and its log replayed later:

```text
2026-10-13 14:03:07 EDT [launch] start: Starting Claude Code session (local, tmux)
2026-10-13 14:03:07 EDT [launch] install: editable checkout 0.8.0; current
2026-10-13 14:03:07 EDT [launch] tmux: 3.7c at /opt/homebrew/bin/tmux
2026-10-13 14:03:07 EDT [launch] mode: launching inside tmux (tmux is the default session mode)
2026-10-13 14:03:08 EDT [launch] session: resolved myproject-3
2026-10-13 14:03:08 EDT [launch] worktree: creating isolated worktree
2026-10-13 14:03:18 EDT [launch] worktree: still creating isolated worktree (10s elapsed)
2026-10-13 14:03:20 EDT [launch] worktree: created /home/user/src/myproject/.worktrees/myproject-3 (12.4s)
2026-10-13 14:03:21 EDT [launch] handoff: ready, handing off to Claude Code (myproject-3)
```

Each phase is the line's operation (see
[Every output line carries a tag](#every-output-line-carries-a-tag)).

- A phase that may block prints a line before it starts and one with its outcome;
  while it runs, a heartbeat with the elapsed time repeats every 10 seconds so a
  slow worktree sync or remote probe never looks hung. Outcomes that took two
  seconds or more carry their duration.
- `Warning:` and `Error:` lines are never suppressed; `handoff:` is always the last
  line before the launcher hands the terminal to the engine. A phase that fails or is
  interrupted reports it at error or warning level under its own operation.
- `-q/--quiet` keeps only warnings and errors; `-v/--verbose` adds skipped steps,
  resolved paths and every duration.
- Every line starts with the time it was produced, 24-hour, in US Eastern time with
  the zone's abbreviation (`EDT` or `EST`). The time is dim on a terminal and comes
  before the tag, as in Python's `logging` and structlog. Its zone, format, delimiters
  and colour are configurable, and it can be turned off (see
  [Output configuration](#output-configuration)).
- Every element of a line is styled: the tag bold blue, the operation bold cyan,
  `handoff:` bold green, `Warning:` bold yellow and `Error:` bold red. Text a command
  passes in carries no styling of its own; escape sequences in it are removed. The
  elements, their order, delimiters and colours are configurable (see
  [Output configuration](#output-configuration)).
- Colour is used only when stderr is a terminal. Redirected output, CI logs and the
  per-launch log under the state directory receive the plain text. Set `NO_COLOR`
  to any non-empty value (or `TERM=dumb`) to turn colour off on a terminal too.
- Each launch writes a log under the state directory (`launch-logs/`) with every line
  the launch printed, plus the `-v` detail lines and the `-q`-suppressed ones, each
  starting with its time. Anything else written to stderr during the launch, such as a
  traceback, is kept there too. Warnings and errors are also stored in the local
  telemetry database before the launch hands off. The terminal line, the log line and
  the telemetry event come from one record, so they carry the same time, tag,
  operation, level and text (see [Output configuration](#output-configuration)). A log
  directory that cannot be written is skipped; the launch carries on. Once the session
  is running, the lines its session script prints join the same launch's log, and
  its warnings and errors, such as a failed direnv load, also reach telemetry. A reload
  row is stored only once it has escalated.
- `--dry-run` prints its plan of resolved values on stdout instead of launching.
- Every line starts at column 0 of its own row and the process exits on a fresh row,
  even after `ai c -R` reattaches: on a terminal each line ends in CR LF, text from a
  child process has its control characters removed, and when ssh hands the terminal
  back the launcher restores its output settings and moves to a fresh row without
  erasing what the remote side printed.

### Every output line carries a tag

Not only the launcher: every line any `ai` command prints starts with a log type
tag and the operation it belongs to, `[<tag>] <operation>: `. Output from a child
process the launcher runs is relayed under its own tag and the caller's operation
rather than passed through, so the Dolt supervisor's
`dolt_server: healthy (socket accepted)` prints as:

```text
[dolt_server] ensure: healthy (socket accepted)
```

All of it goes through one module, `ai_cli.output`, whose `emit(tag, op, message)`
takes a member of the `Tag` enum and an operation from the configured vocabulary
(see [Output configuration](#output-configuration)), refuses anything else, and owns
the colour rules above. A stdlib logging record names no operation, so it takes the
configured `default` (`[log] general: ...`). [structlog](https://www.structlog.org/) builds one record per
line and [Rich](https://rich.readthedocs.io/) renders its elements, so a line from
the session script, a relayed child process or a nested `ai` command (which hands its
records to the parent as JSON) is styled the same way as any other. Untagged output is a short, named list (`Untagged`), kept so
programs can parse it: `--json` documents, `ai internal` replies, the Claude Code
statusline segment, `-V/--version`, Click's `--help` text and terminal escape
sequences. A test scans the package source and fails on any other way of reaching
the terminal (`print`, `click.echo`, a `sys.stdout`/`sys.stderr` write, a prompting
`input()`, or a child process left to inherit the terminal), and on any styling done
outside the renderer (`click.style`, a Rich import, an ANSI escape literal). The
same test fails any output call that passes no operation, or one outside the shipped
vocabulary.

## Configuration

### Claude Code Session Config

This repo ships two Claude Code session config files:

- **`CLAUDE.md`** — lean config for users with a shared `~/projects/CLAUDE.md` (managed platform setup), where global AI orchestration rules are inherited from there.
- **`CLAUDE-full.md`** — standalone config with all rules included, for everyone else.

After installing, run `ai setup` once to automatically detect your environment and configure the right file:

```bash
ai setup
```

`ai setup` checks for a shared `~/projects/CLAUDE.md`. If found, it confirms the lean `CLAUDE.md` is correct and takes no action. If not found, it copies `CLAUDE-full.md` → `CLAUDE.md` and marks the file as `assume-unchanged` in git so it won't show as locally modified.

### Tool Config

Configuration lives in `~/.config/ai-cli-utils/config.toml` (on Windows, `config.toml` in the ai-cli-utils folder under `%APPDATA%`). A default config is created on first run. An older `~/.config/ai-cli/` directory is moved to the new name on first use.

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
# Limits launch-time auxiliary-state housekeeping only; it never ends tmux sessions.
stale_session_timeout = 15

[stale_session_reaper]
# `ai c` starts this independent watcher when needed; observe is the safe default.
mode = "observe"
# Set `mode = "reap"` only after reviewing observe-mode logs.
stale_after_seconds = 600

[sync]
remote_host = "user@host"      # for cross-machine sync

[behavior]
notify_on_exit = true          # desktop notifications on task completion

[update]
# extra_venvs = []             # optional: additional venv paths to reinstall into after 'ai update'
```

To add another machine, give it a short alias and select it for one launch with
`ai c -R -m alias` (or `ai c --remote --remote-machine alias`). `-R` alone uses
`[remote] default`. Existing flat `[remote]` configurations remain supported as
one implicit default machine.

### Output configuration

An `[output]` table in `config.toml` controls every element of every line an `ai`
command prints: which elements appear, their order, the delimiters around them,
their colours and the patterns their values must match. Without the table, lines
render as shown under [Launch output](#launch-output). Any key you set overrides the
shipped default of the same name; the rest keep their defaults. The shipped defaults,
which list every key:

```toml
[output]
order = ["datetime", "tag", "operation", "level", "description"]
separator = " "

[output.elements.datetime]
enabled = true
format = "%Y-%m-%d %H:%M:%S %Z"
timezone = "America/New_York"
prefix = ""
suffix = ""
style = "dim"
pattern = '^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [A-Z]{2,5}$'

[output.elements.tag]
enabled = true
prefix = "["
suffix = "]"
style = "bold blue"
pattern = '^[a-z0-9_-]+$'

[output.elements.tag.styles]
# transport = "bold magenta"

[output.elements.operation]
enabled = true
required = true
default = "general"
prefix = ""
suffix = ":"
style = "bold cyan"
pattern = '^[a-z][a-z0-9-]*$'
vocabulary = [
    "start", "request", "install", "update", "reexec", "remote", "probe", "transport",
    "connect", "reconnect", "auth-wait", "session", "worktree", "terminal", "tmux", "direnv",
    "mode", "handoff", "ensure", "reload", "config", "load", "plan", "cleanup", "exit", "general",
    "adopt", "allocate", "apply", "attach", "audit", "check", "compact", "configure", "heartbeat",
    "history", "lease", "list", "migrate", "notify", "open", "profiles", "prune", "pull", "push",
    "refresh", "register", "repair", "report", "resolve", "restore", "resume", "retire", "scrape",
    "set", "stage", "status", "stop", "sync", "translate", "usage", "validate", "watch",
]

[output.elements.operation.styles]
handoff = "bold green"

[output.elements.level]
enabled = true
show = ["warning", "error"]
prefix = ""
suffix = ":"
style = ""

[output.elements.level.styles]
warning = "bold yellow"
error = "bold red"

[output.elements.description]
enabled = true
prefix = ""
suffix = ""
style = ""
pattern = '^[^\x00-\x1f\x7f]*$'

[output.progress.reload]
warn_after = 3
error_after = 6

[output.sinks.terminal]
enabled = true

[output.sinks.file]
enabled = true
min_level = "detail"

[output.sinks.telemetry]
enabled = true
min_level = "warning"
```

- `order` lists the elements left to right, and `separator` goes between two that
  are present. An element left out of `order` does not appear. `order` must include
  `description` and may name each element once.
- `elements` holds one table per element. Each element has `enabled`, `prefix` and
  `suffix` (the delimiters written around its value) and `style`.
- `style` is a [Rich style](https://rich.readthedocs.io/en/stable/style.html): attributes
  such as `bold`, `dim`, `italic` and `underline`, a named colour, `color(n)` for the
  256-colour palette, or `#rrggbb`. Rich downgrades a colour the terminal cannot show.
  Colour reaches a terminal only; a pipe, a log file or `NO_COLOR` gets plain text.
- `styles` in the `tag`, `operation` and `level` tables overrides `style` for one tag,
  operation or level by name, for example `transport = "bold magenta"`.
- `pattern` is a regular expression a value must match, checked when the table is
  read. The `tag` pattern must match every tag the package uses. The `operation`
  pattern must match every name in its `vocabulary`. The `datetime` pattern must
  match what `format` renders. The `description` pattern must compile.
- `datetime` is the time a line was produced. `format` is a strftime format using
  the directives every platform supports (`%a %A %b %B %c %d %f %H %I %j %m %M %p
  %S %U %w %W %x %X %y %Y %z %Z %%`). `timezone` is `UTC`, `local` or an IANA zone
  name such as `Europe/Berlin`. The default is `America/New_York`; set
  `timezone = "local"` for the host's own zone. `%Z` shows the zone's abbreviation, so
  a line always says which zone its time is in. For a shorter line, set
  `format = "%H:%M:%S"` together with `pattern = '^\d{2}:\d{2}:\d{2}$'`, or turn the
  element off with `enabled = false`. A `format` that renders empty or fails the
  `pattern`, or a `timezone` that names no known zone, is refused when the table is read.
- `tag` is the log type tag, `[launch]` by default. With `enabled = false` lines
  carry no tag.
- `operation` is the step a line belongs to, such as `reconnect:`, `ensure:` or a
  launch phase such as `worktree:`. Every line the package prints names one.
  `vocabulary` lists the allowed operation names: you can add names (for your own
  `ai internal emit` calls) but not remove a shipped one, and a name outside the list is
  refused. `required` decides what a line without an operation shows. With `true` a line that carries no operation (a stdlib logging
  record) renders `default`, which must be in the vocabulary; with `false` such a line
  renders without the element.
- `level` is the `Warning:`/`Error:` label. `show` lists the levels that get a label,
  from `detail`, `info`, `warning` and `error`.
- `description` is the text itself. It cannot be disabled.
- `progress` holds the `reload` table, which sets when the reload row escalates. When a session
  script keeps reloading, its pane shows one row that updates in place with the reason,
  the attempt and the time since the first reload. From `warn_after` consecutive
  reloads the row is a warning; at `error_after` an error line naming the count and
  how to stop the session is also printed and stays in scrollback. Both are whole
  numbers of at least 1, and `error_after` may not be less than `warn_after`.
- `sinks` decides where each line goes. `terminal` is the styled line on the screen.
  `file` is the per-launch log, which gets the same elements as plain text and always
  starts with the datetime, even when the element is off for the terminal; it keeps
  each line at or above its `min_level` (default `detail`, every line). `telemetry`
  stores each line at or above `min_level` (`detail`, `info`, `warning` or `error`) in
  the local telemetry database, in one batch just before the launch hands off or the
  command exits. `[telemetry] enabled = false` also turns it off. Each sink has
  `enabled`.

The table is read again whenever `config.toml` changes, so an edit applies to the next
line, even inside a running `ai c` session. If any part of `[output]` is invalid, none
of it applies: lines render with the shipped defaults and each problem is printed once
as `[config] load: Error: <file>: <key>: <reason>`, for example
`output.elements.tag.style: 'bold blu' is not a valid style`. The command itself
carries on.

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
```

The color palette (16 entries, configurable) is defined in `[iterm2.palette]`. Each session gets a collision-free slot via lease files. When a tab color is set, the session icon is automatically tinted with a contrasting color (180° HSL hue rotation). When no color is set, the Claude brand orange (`#da7756`) is used as fallback.

## Requirements

- Python 3.11 or newer. `requires-python` is `>=3.11`, with no upper bound, so a new Python release
  does not make an already-published version uninstallable. Development and CI run on 3.14 alone —
  that is a separate decision about this repository, not a restriction on installing the package —
  and the shipped source is byte-compiled on 3.11 in CI so the wider claim stays falsifiable.
- [tmux](https://github.com/tmux/tmux) (optional but the default — auto-installed on first launch where a package manager can do it unattended, and a tmux that is installed but cannot load a shared library is repaired by pointing the dynamic loader at the library where it actually is, re-derived on every launch so a wiped system directory heals itself. A launch that still cannot use tmux continues in bare mode, naming the library it could not find. Set `AI_CLI_LIBRARY_PATH` to add a library directory the search does not know about. On Windows there is no native tmux, so bare mode is the right answer: `[session] use_tmux = false`)
- `zsh` **or** `bash` — the tmux session pane runs the generated session script under zsh when it is installed, and falls back to bash otherwise
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [Gemini CLI](https://github.com/google-gemini/gemini-cli), [pi](https://github.com/badlogic/pi-mono), and/or [Codex](https://developers.openai.com/codex/cli/)
- [direnv](https://direnv.net/) (optional — when installed, sessions start under `direnv exec` so the project `.envrc` is loaded; sessions start normally without it)
- [mosh](https://mosh.org/) (optional, for remote sessions — falls back to SSH; Linux/macOS only)
- [autossh](https://www.harding.motd.ca/autossh/) (optional, for `ai tunnel` — `brew install autossh` / `apt install autossh`; Linux/macOS only)
- [NATS](https://nats.io/) (optional — enables fleet messaging, sync watch, and session events; see [NATS Setup Guide](docs/guides/nats-setup.md))

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and contribution guidelines.

## License

[MIT](LICENSE) -- AI CLI Utils Contributors
