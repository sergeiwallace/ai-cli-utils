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

Without a terminal on stdin, tmux launches leave the session detached and return 0
after confirming a live pane. Launch failures still return nonzero; attach later
with `ai attach <session-name>`. With a terminal, launches attach as usual.
Bare launches (`-b/--bare`) keep returning the engine's exit status.

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
Unknown evidence refuses. Every transcript record is scanned in 64 KiB chunks;
a temporary disk-backed ledger matches background launches and completions across
the entire history. Only the last 8 MiB of complete main-chain records are retained
for turn, compaction and context checks. Missing tail evidence still refuses.
An individual record over 8 MiB, an incomplete or malformed record, a read failure,
a changing file, or an unmatched launch refuses with `background-work`. There is
no history size limit. Temporary ledger files are removed after the scan.

For an allowlisted npx root, helpers must resolve to the root's recorded Node
interpreter (including npm/npx scripts) or a system shell (`/bin/sh`,
`/usr/bin/bash`, `/usr/bin/sh`). Every file named under `~/.npm/_npx` in helper
arguments must have a verified identity stamp in the same allowlist entry.
Uncertified installations, unrelated executables and unreadable processes refuse
with `background-work`; non-npx entries retain their existing behavior.

Context must be at or below 20 percent, or within one assistant turn of an automatic
compaction. Missing usage and unknown model windows do not count as zero.
`context_window_tokens` wins over transcript window evidence. Otherwise, the latest
main-chain assistant usage row's matching `requestedModel` takes precedence: `[1m]`
selects 1,000,000 for known non-haiku models; an untagged request selects the base
table window (200,000 for known models), even when `message.model` has `[1m]`.
Provider prefixes through the last `anthropic.` and trailing Bedrock `-v<digits>:<digits>`
versions are stripped when comparing bases and looking up windows. A malformed or
mismatched request is ignored: fall back to a `[1m]` suffix on `message.model`
(known non-haiku models only), then the model table. Unknown windows require an
explicit override. The status line assumes 1,000,000 for the Claude 5 family, so
its percentage can differ from this gate's. A manual compaction passes only
through the percentage gate.

When context fails and `pre_step = true`, the configured injection adapter submits
`/save-state` once to the target pane. The command waits up to
`compaction_wait_seconds` for a new main-chain `compact_boundary` record; an older
boundary does not qualify. It then requires `settle_seconds` of idle after the
compaction, and rechecks every gate before preparing the pinned restart. The wait
budget allows that additional idle interval. A busy registry, changed process or
supervisor generation, or lost lock aborts the wait. Background work after
compaction refuses with `background-work`; adapter failures and timeouts refuse
with `context`. With `pre_step = false` or no adapter, failing context refuses
without injection. Check mode never runs the pre-step.

Before the `/save-state` pre-step and again in the final assessment, the restart
checks read-only that the local registry record has a valid boot recipe and that
the registry file, directory and existing lock permit acquiring a generation.
Missing records, missing or invalid recipes, and unavailable generation storage
refuse with `no-boot-recipe`, leaving the agent alive without injection or signals.
The check neither adopts legacy sessions nor writes a generation.

For a refreshed child under a pre-feature supervisor, failed generation acquisition
is counted in `<state dir>/session-begin-failures-<tmux session>`. The child waits
1, 2, 4, 8, 16, then 30 seconds before exit 76; later delays stay at 30 seconds.
A successful acquisition resets the counter. On failure 20 it prints one session
warning and parks in one-second waits without retrying or starting an agent.
SIGTERM exits the parked child with status 143; Ctrl+C (SIGINT) exits it with
status 130, within one wait interval. If the old supervisor relaunches that child,
the retained counter makes another failed acquisition park again. Supervisors
that already pass `AI_CLI_RUN_GENERATION` bypass this compatibility path.

The restart pins the exact conversation before signalling, atomically prepares a
compatible replacement child, and sends SIGTERM only to the verified agent PID.
If necessary, SIGKILL follows after the grace period and another identity and gate
check. The pane and supervisor survive. Unsupported running programs refuse with
`supervisor-upgrade-required`. On macOS, compatibility requires proof that the
running shell loaded the inspected script; an already-refreshed script without
readable running-program evidence can require a separately reviewed migration.
Windows process signalling is unsupported: use the configured injection route,
or the command refuses with `no-mechanism`.

If a restart request is fenced after its target exits, the supervisor prints
`ai clear-restart-request REQUEST_ID --reason 'explain recovery'` (`-r` for short).
This command requires a nonempty reason and refuses while the recorded target PID
still has the same birth identity, or its identity cannot be proved. It records
the reason, actor and proof of target absence or PID reuse in the supervisor's
launch log, then removes only that request file so the waiting loop can continue.
If the log cannot be written, the request remains fenced.

On the next check or restart attempt, a failed request is reconciled to `complete`
under the session lock if a live replacement has the pinned conversation UUID
in the same tmux pane, a different PID from the old agent, a matching
recorded process start time, and `VERIFIED` local evidence. The pane must still
belong to the recorded supervisor generation and birth identity, and that
supervisor must be an ancestor of the replacement. The request records the
reconciliation time, replacement PID and evidence kinds; the result records
`failed: false` with reason `reconciled`. Missing, unreadable, unknown or conflicting
evidence keeps the fence and leaves both files unchanged. Reconciliation preserves
`last_restart`, so the existing cooldown is unchanged and can still refuse a retry.
The cooldown always counts from `last_restart`, which is written before the action,
and applies to every outcome: a verified restart, a failed one and a reconciled one.
No command clears it early.

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
auto = false                       # Opt in to automatic restart evaluation
auto_interval_seconds = 600
```

With `auto = true`, each supervisor heartbeat starts a detached
`ai internal restart-auto <tmux session>` evaluator when that session's
`auto_interval_seconds` have elapsed (600 seconds by default). The heartbeat does
not wait for evaluation or restart. Interval reservations and evaluation times
are stored in `<state dir>/restart-session/<tmux session>.auto.json`; scheduling
failures retry on a later interval and do not interrupt heartbeat publication.

The evaluator restarts only for a verified newer native installation or the
rendered `Update installed · Restart to apply` notice, or a positively observed
reachability failure: refused socket connection, missing bridge registration,
wrong user title, or a complete local/remote listing proving absence or wrong
identity. Unknown and unobserved results never become restart triggers with age.
Observer timeouts, failed commands and malformed, stale or truncated listings
remain unknown. A mobile-only failure cannot be detected without independent
observation. Each evaluation appends one JSON line containing the tmux session,
trigger verdicts and outcome to `<state dir>/restart-session/auto.log`.

Automatic restarts use the same pre-step, idle/background/context gates, shared
lock, conversation pin and cooldown as `-X`. Failed replacement verification
suppresses automatic retries until passive verification succeeds or the operator
retries manually. Enable automation only after every external restart actor uses
the shared lock and cooldown protocol below.

External commands are argv arrays in `restart-session.d/*.toml` beside the config
file. Duplicate adapters refuse. Placeholders are expanded as individual arguments,
without a shell: `{tmux_pane}`, `{tmux_session}`, `{session_id}`, `{title}`, `{command}`.

```toml
[inject]
argv = ["python3", "-m", "example_injector", "--pane", "{tmux_pane}", "--command", "{command}"]

[observer]
argv = ["ssh", "host-b", "list-sessions-as-seen-from-here"]
```

An installer can additionally write `restart-session.d/mcp-servers.toml` to
recognize configured servers whose wrappers exec another program or rewrite the
process title. The file is a trust artifact: generate it from verified installed
server configuration and verified runtime forms, never from arbitrary live
children. The consumer does not generate or learn entries. Missing files preserve
the configured-command/shebang checks. Invalid files or stale/unreadable stamps
refuse with `background-work` and a diagnostic naming the entry and requesting
that the allowlist be regenerated. All entries are validated on every check,
even if a matching process is absent; there is no fallback around stale stamps.

Version 1 has integer `version = 1` and a `servers` array (empty is valid).
Each server requires a unique nonempty string `name`, an absolute canonical
`executable` path, a nonempty string array `argv`, and a nonempty `identities`
array. `argv` must equal the entire process command line returned by
`psutil.Process.cmdline()`, including argv[0] and any blank arguments left by a
rewritten title. argv[0] must be nonempty. There are no wildcards, prefixes,
placeholder expansion, shell parsing or optional arguments. The executable
reported by `psutil.Process.exe()` must resolve to `executable`; trusting argv[0]
alone is insufficient.

Each identity requires an absolute canonical `path` (resolve symlinks before
generating), integer nonnegative byte `size`, and lowercase 64-digit `sha256` of
the file's complete bytes. Paths must be unique within an entry, and the
executable must have a stamp. The installer must also stamp the server's script
and any tool implementation whose upgrade should invalidate the entry, including
scripts hidden by a rewritten title. Their paths in argv, when visible, must be
recorded exactly. Stamp failures require regeneration after verifying the new
installation; never refresh stamps merely to make a live child pass.

For example, an exec-wrapped notifier has argv
`["/opt/example/python", "-P", "/opt/example/notifier.py", "mcp-adapter"]`, executable
`/opt/example/python`, and stamps for both files. A title-rewritten tool can have
argv `["npm exec example-tool", "", ""]`, executable `/opt/example/node`, and stamps
for both node and the installed tool script. Record the actual verified number of
blank arguments; it is not inferred. Use native absolute paths on Windows. A
complete TOML contract example is in
[tests/fixtures/restart_mcp_servers.toml](tests/fixtures/restart_mcp_servers.toml);
its empty-file stamps are placeholders to replace with installation measurements.

Only a matching direct child born from 0 through 60 seconds after the agent is
allowed as a server root; its own subtree is then allowed. Unknown, late or
unreadable roots still refuse, as do unfinished transcript tasks. Installing this
file does not relax registry, background task, turn, context or supervisor gates.

The injection adapter submits `/exit` for `mechanism = "exit"` or unavailable
signal access. It must preserve the session when background work is reported.
A non-zero exit refuses; a zero exit still requires replacement verification.
For `/save-state`, the adapter must return JSON with `state` set to `SENT`,
`STARTED` or `COMPLETED`, `submitted` set to `true`, and exit zero. `QUEUED` with
exit 2, `submitted` set to `true`, and `landing` set to `queued` waits within the
same compaction deadline; without new compaction evidence it refuses with reason
`context`, reporting that save-state was queued and never observed to compact.
`HELD`, invalid queued results and other outcomes refuse immediately without
retrying injection or signalling. The report
includes the adapter's protocol state and exit code; arbitrary diagnostic output
is omitted. A successful submission still requires new compaction evidence and
all restart gates to pass.
During the wait only, an unreadable or changing transcript or an unfinished background launch
skips that pass within the existing compaction deadline and settle extension, resets the
settle interval to require readable evidence with every launch finished, and is named in the timeout
detail if it affected the last pass; pre-injection reads, the final assessment and every other
gate retain their immediate refusals.
The observer returns JSON with `status` (`seen`, `absent`, `unknown`), `complete`,
`observed_at` (epoch seconds or an ISO timestamp with a timezone) and `rows` carrying
`session_id`, `title` and, where available, `bridge_session_id`. Evidence must be
observed during the current invocation and identify the replacement conversation
and expected title. Duplicate or mismatched identities fail. Failed commands,
timeouts, malformed, stale or partial listings remain unknown.

The report separates `local`, `peer`, `message_delivery` and `mobile` verdicts.
Local verification requires the replacement's exact UUID, user title, bridge
registration, passive messaging socket connection and complete local agent listing.
Only agent rows matching the target UUID or title participate in identity checks;
unrelated rows with a missing PID or incomplete identity do not block verification.
A matching row must have a non-empty UUID and title and an integer PID greater
than 1. Malformed matching rows remain unknown, and a replacement-verification
timeout names their invalid fields in the report detail. Duplicate matches and
PID, UUID or title mismatches still fail; incomplete or truncated listings remain
unknown.
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

##### Human session names

With human names on, a new session ends in a short first name instead of `N`:
`{repo-alias}-{host-alias}-[{label}-]{name}`, for example `myrepo-box-ada` or
`myrepo-box-review-ada`. The name is unique on every machine that shares a name
registry, so "the ada session" names one session wherever it runs.

```bash
ai c                # myrepo-box-ada: the shortest free name, then one sharing the alias's letters
ai c review         # myrepo-box-review-ada
ai c -N cleo        # myrepo-box-cleo: choose the name yourself (the label stays positional)
ai c -N cleo review # myrepo-box-review-cleo
ai c ada            # reattaches to myrepo-box-ada if this repo holds it here; never creates it
ai c 3              # an index slot, exactly as before
```

| Flag | Effect for this launch |
|---|---|
| `-N/--name NAME` | Use this first name. It is never passed to the engine. |
| `-w/--with-name` | With a label, keep a first name after it. |
| `-u/--no-name` | With a label, drop the first name: `myrepo-box-review`. |
| `-S/--session-names` | Use a first name, whatever the config says. |
| `-I/--no-session-names` | Use an index, whatever the config says. |

A name is picked from the name database: a generated list of first names of 3 to 4
letters with common English words removed, plus any name added by hand. The picker
never hands out a held name, a name one typo away from a held name, or a name whose
title would be a prefix of another session's. A name you choose with `-N` passes the
same checks. If it is not in the database yet, `ai` asks
`cleo is not in the name database. Add it? [y/n]`. Answering `n`, or choosing a name
that is refused, leads to `Name to use instead, or "auto" to pick one:`, which takes
another name (added without a second question) or `auto`. A value outside the limits
is refused with the limits printed, and the question repeats until a valid answer,
end of input or Ctrl-C (exit 130, nothing created). Without a terminal there is no
one to ask, so a refused or unknown name exits 2; `ai add-name NAME` adds one first.

Settings live in `[session_names]` in `config.toml`; a new config file has
`enabled = true`:

```toml
[session_names]
enabled = true            # AI_CLI_SESSION_NAMES=1 or 0 overrides it
name_with_label = "keep"  # or "drop"; AI_CLI_SESSION_NAME_WITH_LABEL overrides it
min_name_length = 3       # used only where no shared name registry exists
max_name_length = 4       # used only where no shared name registry exists
```

The registry is the first repository under the projects directory that ships
`config/session-names.toml`. That file names a git ref, and every machine reserves
names on it with a fast-forward push, so two machines can never take the same name.
It also sets the length limits for every machine that uses it, plus per-host
`enabled` values in `[hosts.<host id>]` and defaults in `[defaults]`. With no such
repository, the registry is a local directory seeded with the shipped list.
`ai show-name-config` prints each setting with the layer that set it.

Offline, a launch takes one of the spare names its machine reserved earlier and
prints `offline: using reserved spare <name>`; with none left it refuses before
creating anything. The next `ai` command that reaches the registry publishes the
activation.

| Command | Effect |
|---|---|
| `ai show-name-config` | Each naming setting, its value and the layer that set it. |
| `ai add-name NAME [-r/--reason TEXT]` | Add a name to the database without a prompt. |
| `ai remove-name NAME` | Remove a name that was added by hand, unless it is held. |
| `ai sync-names [-d/--dry-run]` | Regenerate the list when this checkout's naming policy is newer than the published one. |
| `ai reconcile-names [-t/--title T] [-d/--dry-run] [-j/--json]` | Publish offline activations, release names whose worktree and transcript are both gone, and top up spares. |
| `ai release-name NAME` | Release one name by hand, after the same two absence tests; nothing bypasses them. |
| `ai reassign-name NAME -t/--to HOST -r/--reason TEXT` | Move a name's reconciliation to another host id; the name stays held. |
| `ai check-names [-j/--json]` | Report every held name outside the length limits, grouped by host with the command to run there; exit 1 when one is too long. |

A name is released by the machine that holds it, once the session's worktree and its
client transcript are both proved gone. A store that cannot be read, or a session that
cannot be matched to its record, holds the name. Existing sessions move to human names
with `ai session-migrate OLD --to-name [NAME]` or, for a whole host,
`ai session-migrate --all --to-names` ([details](docs/tools/cc-session-adoption.md));
`ai session-adopt NAME -m/--name FIRST` adopts an outside session into a human-named slot.

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
the `[machine] marker_file` when one is configured, then the hostname) by the
first of these that names it:

1. `<projects>/<environment repo>/config/machine-host-aliases.toml`, when
   `[project] environment_repo` names a checkout that exists (`$PROJECTS_DIR`,
   then `[project] projects_dir`, default `~/projects`). When it names the host,
   no other file is read.
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

### Session boot relaunch

On macOS and Linux, a boot or login job can run `ai sessions relaunch -b/--boot`
to recreate eligible sessions in detached tmux sessions without opening a terminal application.
Relaunch is off by default; configure
`config.toml` under your ai-cli-utils configuration directory:

```toml
[session_relaunch]
on_boot = true
causes = ["host_reboot", "terminal_quit_or_crash"]
network_wait_seconds = 120
network_probe = "api.anthropic.com:443" # Empty disables the network wait
```

```bash
ai sessions boot-unit install -d       # --dry-run: inspect exact unit, path and enable/disable plan
ai sessions boot-unit install          # Apply session_relaunch.on_boot from config.toml
ai sessions boot-unit status           # Check installed bytes and service-manager state
ai sessions boot-unit uninstall        # Disable and remove the unit; leave config.toml intact
ai sessions relaunch -b -d             # --boot --dry-run: print each decision and winning veto
ai sessions relaunch --boot            # Recreate eligible owning-host sessions detached
ai sessions list -j                    # --json: inspect recorded run state
ai attach c-myproject-host-1           # Attach later from a terminal
```

`boot-unit install` writes only changed bytes with atomic replacement and reports the number
of mutations. Setting `on_boot = false` and running it again disables and removes the unit;
an unchanged installation reports `0 mutations`. Profile installers can pass
`-b/--on-boot true|false` as the desired state; this does not edit the configuration, which
`relaunch --boot` still reads at run time. Both install and uninstall accept `-d/--dry-run`
without changing files or invoking a service manager.

Linux uses a one-shot systemd user service in the XDG configuration directory. It runs when
the user manager starts; boot before login requires user lingering to be provisioned separately.
macOS uses a `RunAtLoad` LaunchAgent in `~/Library/LaunchAgents` at login, with no `KeepAlive`.
Neither unit opens a terminal application. Both capture the absolute installed `ai` entry point,
`PATH`, `HOME`, and absolute XDG directory overrides at installation; reinstall after moving
the executable or changing that environment. Windows refuses boot-unit management.
The captured `PATH` keeps existing directories in order, removes duplicates and entries with
an exact `.worktrees` or `.direnv` path segment, and always retains the installed `ai` directory.

The command waits for each eligible session's recorded directory and configured TCP endpoint,
up to the timeout. It replays the exact stored session name and local launch recipe, including
destination-owned remote sessions, once-only sessions and bare commands hosted inside tmux.
Incomplete legacy recipes are refused with a diagnostic. A session finally exited back to the
shell by hand stays down; `manual_exit` and `unknown` veto every policy, including `always`.
The command also refuses foreign/remote records, deny-boot safety files, existing tmux sessions,
disabled causes and policy `never`. Each boot attempt is durably reserved before launch and is
never repeated in the same boot, even after launch failure. Decisions are logged and stored in
the registry; configured publication uses the existing publisher. Dry runs write and launch nothing.

Use `ai sessions relaunch <slug>` to explicitly restart one stopped session owned by this host,
detached through its recorded recipe, even if boot relaunch is disabled or its policy is `never`.
Live, unknown, foreign and incomplete records are refused; `-d/--dry-run` previews without changes,
and a slug cannot be combined with `-b/--boot`. Use `ai sessions policy <slug> <auto|never|always>`
to change a local session's boot policy and queue publication through the existing background timer.
Every boot policy still leaves manually exited sessions down.

### Enrol an existing session

Use `ai sessions enrol <name>` to give an existing live tmux session a validated restart
recipe. It prints the recipe and sets boot relaunch policy to `never`. Preview with
`-d/--dry-run`; an already valid recipe is left unchanged. Enrolment only updates registry
bookkeeping: it never signals, restarts or types into the session.

The command first uses the local registry record's launch argv and working directory,
with the launcher's configured host identity. Without a record, a `c-r-*` session must have
a pane leader running its exact generated `<state dir>/sessions/<name>.sh` script; engine,
name and flags come from that script, the directory from the live pane and the host from
`AI_HOST`. Missing or unparseable script data, invalid recipes and a directory mismatch
are refused without changing the registry. Restart still requires every existing gate,
including `remoteControlAtStartup = true`; enrolment does not enable Remote Control.

After the fixed package is available, run these commands in order on **each**
macOS or Linux host, as the user who owns its tmux server:

```sh
uv tool upgrade --reinstall ai-cli-utils
ai sessions enrol --all --dry-run --json
ai sessions enrol --all --json
ai sessions list --json
```

For a source installation, replace the first command with
`uv tool install --force .` from the updated package checkout. `ai update` is
not required for this recovery. A package reinstall alone refreshes validated
sessions on their next existing heartbeat or acquisition call; the explicit
enrolment commands also cover sessions without either call.

On Windows with tmux in WSL's default distribution, run in PowerShell:

```powershell
wsl.exe -- uv tool upgrade --reinstall ai-cli-utils
wsl.exe -- ai sessions enrol --all --dry-run --json
wsl.exe -- ai sessions enrol --all --json
wsl.exe -- ai sessions list --json
```

For tmux in Git Bash, run the macOS/Linux commands inside Git Bash instead.
Native Windows without tmux reports
discovery unavailable; boot relaunch remains supported only on macOS and Linux.

The bulk command (`-a/--all`, `-d/--dry-run`, `-j/--json`) reports each session as
`registered`, `already-registered`, or `skipped` with a reason. In a dry run,
`registered` means **would register**; nothing is written. It validates the live
pane's exact generated script, embedded launch metadata, and any saved metadata.
Legacy supervisors and remote-style names are supported. Missing saved metadata
is recoverable when the generated script supplies it; absent or conflicting
evidence, renamed tmux sessions, and foreign ownership are skipped without guessing.
Discovery failures return a nonzero exit status.

Bulk enrolment preserves existing records, ownership, generations and policy;
newly recovered sessions default to boot policy `never`. It atomically regenerates
validated stable scripts, including already registered sessions; running shells
retain their current program until the next child launch. It never signals,
restarts or types into a pane. Repeated runs leave current scripts and records
unchanged. To opt a recovered session into boot replay deliberately, use
`ai sessions policy c-session-1 auto`; the configured boot and safety gates still apply.

Template refresh after an update, supervisor generation acquisition, heartbeats, and
`ai sessions relaunch --boot` also attempt bulk recovery automatically, reading
configuration at call time. Automatic recovery is best effort and logs its
decisions; use the explicit command to inspect every skip. A stopped supervisor
or unverifiable session still requires operator attention.

Older acquisition scripts can inherit extended keyboard mode from an agent,
encoding Ctrl+C as text instead of delivering SIGINT. The upgraded acquisition
helper resets terminal input before waiting and records a clean stop recognized
by old supervisors. A child already parked permanently after its twentieth
failure runs only shell `sleep`: it cannot execute upgraded helpers or read a
regenerated script. Enrolment can register it and refresh its script, but waking
that particular child requires a separate operator action; these commands do
not restart it. Regeneration prevents that permanent park on future child starts.

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

### Chief-of-staff session

One coordinating session per machine that dispatches to your other sessions and surfaces their blockers, with its `FM_HOME` in a per-machine state directory rather than a repository. `-F/--chief` is a mode on every engine command, so the chief can be any agent you launch.

The names that tie a chief to your environment are settings in `config.toml`, read on every launch. None has a built-in default: a chief launch that needs one that is unset refuses and names it. The two alias lists are optional: an alias naming a built-in command or an existing option is refused.

```toml
[chief_of_staff]
project = "myenv"                      # the repository every chief launches into
session_name = "chief"                 # the session-name segment that marks a chief
instance_key_prefix = "example-chief"  # registry key: <instance_key_prefix>/<machine-key>
homes_dir = "myenv/chief-of-staff"     # relative to $XDG_STATE_HOME unless absolute or ~
command_aliases = ["mychief"]          # optional: extra subcommands that are `ai cos`
flag_aliases = ["mychief"]             # optional: extra long flags that are `--chief`
```

A chief always launches into `project`, whatever repository you run the command from. These examples use the settings above, a project whose task prefix is `env`, and host alias `box`:

```bash
ai c chief                  # Claude chief: session env-box-chief-1, tmux c-env-box-chief-1
ai c chief-3                # Chief in slot 3: env-box-chief-3 (any integer N); `ai c chief 3` is the same
ai c 3 -F/--chief           # The same slot through the flag
ai c chief -p env           # -p may name the chief's project (by alias, prefix or directory), never another
ai c --chief review         # Optional suffix: env-box-chief-review-1
ai p --chief                # Same mode on Pi: tmux p-env-box-chief-1 (likewise `ai g`, `ai cx`)
ai cos                      # Shorthand for `ai c --chief` (takes the same optional suffix or N)
ai mychief 1                # A configured command alias: the same as `ai cos 1`
ai g --mychief              # A configured flag alias: the same as `ai g --chief`
ai c -F -k/--machine-key K  # Machine key naming the chief home (default: $AI_MACHINE_ID)
ai c -F -H/--fm-home DIR    # Use an explicit chief home instead of <homes_dir>/<key>
ai c -F --dry-run           # Print the resolved plan; registers and starts nothing
ai c chief-2 -R -m box      # Run the chief on remote machine `box`: named, homed and registered there
ai cos -R -m box            # Same, through the shorthand
ai cos 1 -r -R -p env       # From another machine: attach to or resume chief 1 on the remote machine
```

Every one of these attaches, resumes or creates, decided on the machine the chief runs on (the remote one with `-R`):

| Chief N on that machine | What the command does |
|---|---|
| running (its tmux session has a live process) | attach to the tmux session its registration names (`c-…` or `c-r-…`, however it was launched); nothing is created or re-registered |
| tmux session alive but its process exited, or registered but not running, or (with `-r`) a saved transcript | relaunch under the same name, which resumes its worktree and conversation |
| nothing | create and register it; with `-r`, refuse with "no resumable chief session …" |
| a different chief is running | refuse, naming the running chief and the command that attaches to it |

An unnumbered request (`ai cos`, `ai c --chief`, `ai c chief`) means whichever chief is running or registered.

The name is `{repo-alias}-{host-alias}-{session_name}-[suffix-]{index}`, with the index allocated from the same lowest-free-slot scan as any other session unless you name it, and the worktree lands at `<project>/.worktrees/` under that name. A positional `chief` or `chief-N` (your `session_name`) is the chief launch itself, with everything `--chief` does; any other name carrying that segment (`chief-x`) is refused without the flag, so an ordinary session can never pass for the chief. With no `session_name` configured, no positional name is a chief launch.

Worktree isolation is **on**, like every other session: a session working in a main tree is the exception, not the chief. Pass `-W/--no-worktree` if you want the chief in the repo root instead.

`-k/--machine-key` and `-H/--fm-home` are refused without `-F`, so neither silently applies to an ordinary session. The home must already exist with its `config/message-transports.json` (your environment's installer seeds it); with neither `-k` nor `$AI_MACHINE_ID` set (a non-interactive shell, such as the remote side of `-R`, can miss an rc-file export) the one seeded home under `homes_dir` is used, and several are refused; a chief launch refuses a missing or unseeded home and refuses a second chief while the first is running (it names the command that attaches to it). A chief running in tmux under a registration that names another (a later launch overwrote it and died) is still found by its tmux session name and re-registered when you attach. On launch it records the chief's agent name and tmux target in `<home>/state/chief-session.json` and the schema-2 `<home>/registry.json` (with `chief_instance_key = "<instance_key_prefix>/<machine-key>"`), then hands off to the ordinary launch with `FM_HOME` / `AI_SESSION_ROLE=chief-of-staff` exported into the pane.

A chief does not have to run on the machine it coordinates: cross-machine messaging (Agent Mail, Buzz, git-inbox, native SendMessage) reaches it wherever it runs. With `-R/--remote` (and optionally `-m MACHINE`) the local `ai` only plans the ssh hand-off and forwards `--chief`, `-k` and `-H`; the remote `ai` resolves the remote machine's chief home, attaches to, resumes or creates the chief there (refusing a different live one), writes the registration there and names the session under the remote host's alias (`c-r-{repo-alias}-{remote-alias}-{session_name}-{index}`). An unnumbered `-R` request is not allocated a slot on the dispatching side, so the remote host picks its own chief. The dispatching machine needs `project` and `session_name` to plan the hand-off; it resolves and writes nothing else, so it needs neither a chief home nor the remote's host alias. Both machines must run an `ai` that accepts `--chief`.

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
# environment_repo = "myenv"   # sibling repo with your shared tooling (host aliases, dolt_server.py, wake sidecar)

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
# marker_prefix = "myenv"      # git-config marker namespace `ai session-migrate` rewrites

[machine]
# marker_file = "/etc/myenv/machine"  # host-id file, read when AI_HOST is unset

[chief_of_staff]               # see "Chief-of-staff session"; required by `ai cos`
# project = "myenv"
# session_name = "chief"
# instance_key_prefix = "example-chief"
# homes_dir = "myenv/chief-of-staff"
# command_aliases = ["mychief"]
# flag_aliases = ["mychief"]

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

[sidecar]
enabled = false                # opt in to the optional wake sidecar
mode = "machine"              # "machine", "session", or "both"
boot = false                   # true when the operating system starts it

[sync]
remote_host = "user@host"      # for cross-machine sync

[behavior]
notify_on_exit = true          # desktop notifications on task completion

[update]
# extra_venvs = []             # optional: additional venv paths to reinstall into after 'ai update'
```

On a local session launch, `ai` asks the `[project] environment_repo` checkout to ensure its
machine wake sidecar is running. That checkout's machine profile is authoritative when
present, so the local `[sidecar]` values are ignored. The check is bounded, never runs
for remote launches, and never blocks a session after a sidecar failure. Without a
sidecar implementation, setting `enabled = true` prints one notice and continues.

With `mode = "session"`, each new local tmux session gets a detached `wake-watch`
window running the watcher for that session's name. The window keeps the main window
selected and restarts the watcher two seconds after any exit. `mode = "both"` also
starts the session watcher alongside the machine sidecar. Reattaching leaves existing
windows alone. Bare launches, remote launches, and Windows do not create watcher windows.
The optional sidecar CLI must be installed for either watcher mode to run.

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
