---
title: ai CLI guide
category: tools
tags: [ai-cli, cli, tmux, session-management, reference]
status: current
source: internal
---

# ai CLI guide

This guide explains how the main `ai` commands behave and how to configure them. Every command's
usage line, description and options, each in both its short and long form, are in the generated
[CLI reference](../reference/cli.md), which `ai <command> -h` matches exactly. Options are listed
there, not here.

## Table of Contents

- [Start, resume, attach](#start-resume-attach)
- [Command index](#command-index)
- [Overview](#overview)
- [Session Management](#session-management)
  - [ai c](#ai-c)
  - [ai cos](#ai-cos)
  - [ai ls](#ai-ls)
  - [ai attach](#ai-attach)
  - [ai sessions](#ai-sessions)
  - [ai reconnect](#ai-reconnect)
  - [ai ssh](#ai-ssh)
- [Daemon Commands](#daemon-commands)
  - [ai memory watch](#ai-memory-watch)
  - [ai quota watch](#ai-quota-watch)
  - [ai telemetry writer](#ai-telemetry-writer)
  - [ai notifications](#ai-notifications)
- [Sync Commands](#ai-sync)
- [Utility Commands](#utility-commands)
  - [Removed ai gemini command](#removed-ai-gemini-command)
  - [ai spend gemini](#ai-spend-gemini)
  - [ai cc-usage](#ai-cc-usage)
  - [ai layout](#ai-layout)
  - [ai color](#ai-color)
  - [ai iterm2 sessions](#ai-iterm2-sessions)
  - [ai iterm2 restore](#ai-iterm2-restore)
  - [ai cdp](#ai-cdp)
  - [ai tunnel](#ai-tunnel)
  - [ai update](#ai-update)
  - [ai setup](#ai-setup)
  - [ai ps](#ai-ps)
  - [ai upgrade](#ai-upgrade)
- [Internal Commands](#internal-commands)

---

## Start, resume, attach

`ai -h` prints the same rows. When a session's tmux session is already running, `ai c NAME`
attaches to it (detaching any other client), so `ai attach` is not needed for a session `ai`
started.

<!-- start-resume-attach:begin -->
| Command | What it does |
|---|---|
| `ai c NAME` | Start a Claude Code session named NAME. When NAME's tmux session is already running, attach to it instead; `ai attach` is not needed for a session `ai` started |
| `ai g NAME` | The same for Gemini CLI; `ai p NAME` for Pi and `ai cx NAME` for Codex |
| `ai c NAME -r` | Resume NAME's existing session, running or stopped (NAME is its title, first name or index); refuses, creating nothing, when no session holds NAME |
| `ai c NAME -R -r` | The same on the default remote machine; -m ALIAS picks another configured machine |
| `ai c -t NAME` | Start a throwaway probe session: it holds no first name, its worktree lives outside the repo, it is never a session home and it is never relaunched at boot |
| `ai cos -r` | Resume this machine's chief-of-staff session |
| `ai sessions` | List the running sessions of this host and its paired host; `ai sessions list -a -S fleet` lists every session of every host that publishes them |
<!-- start-resume-attach:end -->

## Command index

Every command and subcommand with its one-line use, as `ai -h` lists them. Each links to its
section of the [CLI reference](../reference/cli.md).

<!-- cli-index:begin -->
| Command | Summary |
|---|---|
| [`ai add-name`](../reference/cli.md#ai-add-name) | Session names: add a name to the name database without a prompt |
| [`ai adopt-session`](../reference/cli.md#ai-adopt-session) | Register a Claude Code session started outside `ai` so `ai c <n>` resumes it. |
| [`ai attach`](../reference/cli.md#ai-attach) | Attach to a tmux session by name |
| [`ai audit-sessions`](../reference/cli.md#ai-audit-sessions) | Survey titled Claude Code sessions fleet-wide and report which `ai c <n>` cannot resume |
| [`ai backfill-trust`](../reference/cli.md#ai-backfill-trust) | Register Claude Code workspace trust for every repo under a root (fixes 'workspace has not been trusted' permission drops, GH #72896) |
| [`ai c`](../reference/cli.md#ai-c) | Launch a Claude Code session. |
| [`ai cc-usage`](../reference/cli.md#ai-cc-usage) | Claude Code CLI per-call token usage |
| [`ai cc-usage push`](../reference/cli.md#ai-cc-usage-push) | Scan JSONL session files and push new events |
| [`ai cc-usage status`](../reference/cli.md#ai-cc-usage-status) | Print cc-usage cursor summary |
| [`ai cdp`](../reference/cli.md#ai-cdp) | Chrome DevTools Protocol browser management |
| [`ai cdp start`](../reference/cli.md#ai-cdp-start) | Launch Chrome with a CDP port |
| [`ai cdp status`](../reference/cli.md#ai-cdp-status) | Show CDP Chrome status |
| [`ai cdp stop`](../reference/cli.md#ai-cdp-stop) | Stop a CDP-launched Chrome instance |
| [`ai check-names`](../reference/cli.md#ai-check-names) | Session names: report every held name outside the length limits; exit 1 when one is too long |
| [`ai clear-restart-request`](../reference/cli.md#ai-clear-restart-request) | Clear a fenced restart request after its recorded target has exited |
| [`ai color`](../reference/cli.md#ai-color) | Reassign iTerm2 color for the current ai session |
| [`ai copier-update`](../reference/cli.md#ai-copier-update) | Propagate project-template changes to downstream projects |
| [`ai cos`](../reference/cli.md#ai-cos) | Attach to, resume or launch this machine's chief-of-staff Claude Code session, here or with -R on a remote machine (`ai c --chief`). |
| [`ai cx`](../reference/cli.md#ai-cx) | Launch a Codex session |
| [`ai deploy`](../reference/cli.md#ai-deploy) | Alias for update (historical) |
| [`ai doctor`](../reference/cli.md#ai-doctor) | Check the native binaries ai-cli-utils needs, installing what it can |
| [`ai exit-session`](../reference/cli.md#ai-exit-session) | Persist a final exit and stop the exact recorded session processes. |
| [`ai find-names`](../reference/cli.md#ai-find-names) | Session names: find pool names by status, verdict scope, holder and hold state, or show a name's history |
| [`ai g`](../reference/cli.md#ai-g) | Launch a Gemini CLI session |
| [`ai import-names`](../reference/cli.md#ai-import-names) | Session names: copy the old git-tree registry (pool, manual names, held rows, renames) into the fleet's Dolt database in one commit; a second run finds nothing to import |
| [`ai iterm2`](../reference/cli.md#ai-iterm2) | iTerm2 session tracking |
| [`ai iterm2 restore`](../reference/cli.md#ai-iterm2-restore) | Re-attach the recorded sessions in iTerm2 (macOS) |
| [`ai iterm2 sessions`](../reference/cli.md#ai-iterm2-sessions) | List the sessions recorded for iTerm2 restore; prune or refresh them |
| [`ai layout`](../reference/cli.md#ai-layout) | Apply a tmux pane layout. |
| [`ai layout list`](../reference/cli.md#ai-layout-list) | List the available layout files |
| [`ai layout profiles`](../reference/cli.md#ai-layout-profiles) | Regenerate a layout's iTerm2 Dynamic Profiles without relaunching it |
| [`ai layout validate`](../reference/cli.md#ai-layout-validate) | Check a layout file against the layout schema without applying it |
| [`ai list-migrations`](../reference/cli.md#ai-list-migrations) | Session names: list every recorded rename with its state |
| [`ai list-name-candidates`](../reference/cli.md#ai-list-name-candidates) | Session names: count the free names per length and the letter tiers the picker would use |
| [`ai list-names`](../reference/cli.md#ai-list-names) | Session names: list every held name with its title, host, client, kind and general status |
| [`ai locate-session`](../reference/cli.md#ai-locate-session) | Session names: which host holds a session, whether it is active, and which messaging rung reaches it |
| [`ai ls`](../reference/cli.md#ai-ls) | List tmux sessions (fzf picker when available) |
| [`ai memory`](../reference/cli.md#ai-memory) | Memory-file watchers and helpers |
| [`ai memory watch`](../reference/cli.md#ai-memory-watch) | Run the memory-file watcher daemon |
| [`ai migrate-session`](../reference/cli.md#ai-migrate-session) | Rename a session already registered in an `ai` slot. |
| [`ai migrate-transcript`](../reference/cli.md#ai-migrate-transcript) | Move a Claude Code session transcript from one project root to another, such as from a repo root to its worktree |
| [`ai notifications`](../reference/cli.md#ai-notifications) | Notification delivery and history |
| [`ai notifications list`](../reference/cli.md#ai-notifications-list) | List configured notification channels |
| [`ai notifications log`](../reference/cli.md#ai-notifications-log) | Show notification history |
| [`ai p`](../reference/cli.md#ai-p) | Launch a Pi session |
| [`ai probe-transcripts`](../reference/cli.md#ai-probe-transcripts) | Session names: prove each installed client's transcript probe answers present, absent and unknown |
| [`ai ps`](../reference/cli.md#ai-ps) | Process hygiene — show/kill stale ai-cli processes. |
| [`ai ps clean`](../reference/cli.md#ai-ps-clean) | Kill orphaned processes after a confirmation prompt; suspect ones only with -f/--force |
| [`ai ps cron`](../reference/cli.md#ai-ps-cron) | Non-destructive maintenance: remove stale transport files and refresh the remote cache. |
| [`ai quota`](../reference/cli.md#ai-quota) | Claude weekly-quota tracking and statusline helpers |
| [`ai quota history`](../reference/cli.md#ai-quota-history) | Print quota history |
| [`ai quota record`](../reference/cli.md#ai-quota-record) | Record a single AI-usage event into the quota DB |
| [`ai quota scrape`](../reference/cli.md#ai-quota-scrape) | Scrape /usage from a fresh CC session |
| [`ai quota status`](../reference/cli.md#ai-quota-status) | Print current quota snapshot |
| [`ai quota statusline-part`](../reference/cli.md#ai-quota-statusline-part) | Emit the quota section of the CC statusline |
| [`ai quota sync`](../reference/cli.md#ai-quota-sync) | Sync quota snapshot from the remote server |
| [`ai quota watch`](../reference/cli.md#ai-quota-watch) | Quota-watch Circus daemon management |
| [`ai quota watch run`](../reference/cli.md#ai-quota-watch-run) | Run quota-watch daemon (Circus entry point) |
| [`ai quota watch start`](../reference/cli.md#ai-quota-watch-start) | Register quota-watch with Circus and start it |
| [`ai quota watch status`](../reference/cli.md#ai-quota-watch-status) | Show quota-watch Circus status |
| [`ai quota watch stop`](../reference/cli.md#ai-quota-watch-stop) | Stop quota-watch |
| [`ai reassign-name`](../reference/cli.md#ai-reassign-name) | Session names: move a name's reconciliation to another host id; the name stays held |
| [`ai reconcile-names`](../reference/cli.md#ai-reconcile-names) | Session names: publish offline activations, release names whose worktree and transcript are gone |
| [`ai reconnect`](../reference/cli.md#ai-reconnect) | Print reconnect commands for remote tmux sessions |
| [`ai register`](../reference/cli.md#ai-register) | Register a repository root and its task prefix |
| [`ai release-name`](../reference/cli.md#ai-release-name) | Session names: release one name once its worktree and transcript are both gone |
| [`ai remove-name`](../reference/cli.md#ai-remove-name) | Session names: remove a name that was added by hand |
| [`ai retire-session`](../reference/cli.md#ai-retire-session) | Retire a dead session's home slot `.worktrees/NAME`: anchor its branch tips, then remove the checkout and its registry entry. |
| [`ai seed-name-verdicts`](../reference/cli.md#ai-seed-name-verdicts) | Session names: apply the owner's approvals from a seed file, after checking it part by part against the owner-approved oracle the cutover record pins |
| [`ai session-reaper`](../reference/cli.md#ai-session-reaper) | Independently managed stale-session reaper |
| [`ai session-reaper start`](../reference/cli.md#ai-session-reaper-start) | Register the stale-session reaper with Circus |
| [`ai session-reaper status`](../reference/cli.md#ai-session-reaper-status) | Show stale-session reaper status |
| [`ai session-reaper stop`](../reference/cli.md#ai-session-reaper-stop) | Stop the stale-session reaper |
| [`ai sessions`](../reference/cli.md#ai-sessions) | List sessions and manage their run state, relaunch and publication. |
| [`ai sessions boot-unit`](../reference/cli.md#ai-sessions-boot-unit) | Manage the one-shot boot relaunch service (macOS and Linux) |
| [`ai sessions boot-unit install`](../reference/cli.md#ai-sessions-boot-unit-install) | Write the boot relaunch service and enable it, or disable and remove it, as session_relaunch.on_boot (or -b/--on-boot) asks |
| [`ai sessions boot-unit status`](../reference/cli.md#ai-sessions-boot-unit-status) | Report whether the boot relaunch service is installed, current and enabled |
| [`ai sessions boot-unit uninstall`](../reference/cli.md#ai-sessions-boot-unit-uninstall) | Disable and remove the boot relaunch service |
| [`ai sessions enrol`](../reference/cli.md#ai-sessions-enrol) | Give an existing live session a validated restart recipe. |
| [`ai sessions list`](../reference/cli.md#ai-sessions-list) | List sessions as a table grouped by host, active sessions first: this host's running sessions and, with the default pair scope, its paired host's published ones. |
| [`ai sessions mark`](../reference/cli.md#ai-sessions-mark) | Record a session's run state in the registry: running, or exited with a cause (-c) and optional JSON evidence (-e) |
| [`ai sessions policy`](../reference/cli.md#ai-sessions-policy) | Set a local session's boot relaunch policy and queue publication. |
| [`ai sessions publish`](../reference/cli.md#ai-sessions-publish) | Publish a snapshot of the session registry to the configured publication repo, when it has unpublished changes and the debounce window has passed |
| [`ai sessions publish-timer`](../reference/cli.md#ai-sessions-publish-timer) | Manage the background publication timer |
| [`ai sessions publish-timer install`](../reference/cli.md#ai-sessions-publish-timer-install) | Install and enable the background timer that publishes session state; removes it instead when no publication repo is configured |
| [`ai sessions publish-timer status`](../reference/cli.md#ai-sessions-publish-timer-status) | Report whether the publication timer is installed, current and active |
| [`ai sessions publish-timer uninstall`](../reference/cli.md#ai-sessions-publish-timer-uninstall) | Disable and remove the publication timer |
| [`ai sessions relaunch`](../reference/cli.md#ai-sessions-relaunch) | Relaunch recorded sessions detached, without a terminal application. |
| [`ai sessions stop`](../reference/cli.md#ai-sessions-stop) | Stop a managed session so its wrapper does not relaunch the agent |
| [`ai sessions wind-down`](../reference/cli.md#ai-sessions-wind-down) | Stop a running session for a maintenance window, recording maintenance_wind_down so the boot relaunch or a named relaunch brings it back |
| [`ai set-name-status`](../reference/cli.md#ai-set-name-status) | Session names: set the general status of one or more names in one commit; only approved names are picked, unreviewed is every name nobody has ruled on, rejected is never picked or taken by hand |
| [`ai set-name-verdict`](../reference/cli.md#ai-set-name-verdict) | Session names: approve or reject names for one repository, host or session only; a scoped rejection keeps the name out of picks there, a session approval lets that session take it by hand |
| [`ai setup`](../reference/cli.md#ai-setup) | Run interactive setup wizard |
| [`ai show-name-config`](../reference/cli.md#ai-show-name-config) | Session names: print each naming setting, its value and the layer that set it |
| [`ai spend`](../reference/cli.md#ai-spend) | Usage-cost reports |
| [`ai spend gemini`](../reference/cli.md#ai-spend-gemini) | Report historical Gemini CLI spend from local logs |
| [`ai ssh`](../reference/cli.md#ai-ssh) | Open an interactive SSH shell on ALIAS, or the configured default when omitted |
| [`ai sync`](../reference/cli.md#ai-sync) | Sync CC session data (JSONL + memory files) across machines |
| [`ai sync cleanup`](../reference/cli.md#ai-sync-cleanup) | Remove stale transcript copies and orphan lock dirs from worktree CC dirs |
| [`ai sync conflicts`](../reference/cli.md#ai-sync-conflicts) | List unresolved conflict files and recent conflict log entries |
| [`ai sync pull`](../reference/cli.md#ai-sync-pull) | Pull from the staging repo and apply it to the local CC projects dir. |
| [`ai sync push`](../reference/cli.md#ai-sync-push) | Push local CC data to the staging repo and server. |
| [`ai sync repair-worktree`](../reference/cli.md#ai-sync-repair-worktree) | Copy every conversation from a project's main CC dir into a worktree's CC dir, so the worktree session can resume them. |
| [`ai sync resolve`](../reference/cli.md#ai-sync-resolve) | Resolve accumulated conflict files: delete transcript conflict backups and merge memory conflicts. |
| [`ai sync watch`](../reference/cli.md#ai-sync-watch) | Listen for pull requests from other machines and run `ai sync pull --force` on each. |
| [`ai sync-names`](../reference/cli.md#ai-sync-names) | Session names: regenerate and publish the available-name list when this checkout's naming policy is newer |
| [`ai telemetry`](../reference/cli.md#ai-telemetry) | Telemetry writer daemon |
| [`ai telemetry writer`](../reference/cli.md#ai-telemetry-writer) | Run the telemetry writer daemon |
| [`ai tunnel`](../reference/cli.md#ai-tunnel) | autossh SSH tunnels to the remote server |
| [`ai tunnel start`](../reference/cli.md#ai-tunnel-start) | Start an SSH tunnel |
| [`ai tunnel status`](../reference/cli.md#ai-tunnel-status) | Show running SSH tunnels |
| [`ai tunnel stop`](../reference/cli.md#ai-tunnel-stop) | Stop an SSH tunnel |
| [`ai update`](../reference/cli.md#ai-update) | Update ai-cli-utils from the source tree (git pull + uv tool install) |
| [`ai upgrade`](../reference/cli.md#ai-upgrade) | Upgrade ai-cli-utils via uv tool upgrade |
| [`ai watch-vpn`](../reference/cli.md#ai-watch-vpn) | Run the VPN watch daemon |
| [`ai ws`](../reference/cli.md#ai-ws) | Workspace-wide operations across all repos |
| [`ai ws pull`](../reference/cli.md#ai-ws-pull) | Pull/rebase all repos and worktrees in a VS Code workspace file |
<!-- cli-index:end -->

---

## Overview

`ai` is the unified CLI for Claude Code and Gemini CLI sessions. Entry point: `src/ai_cli/main.py:cli()`.

```bash
ai --version   # print installed version
ai -V          # same, short form
```

### Platform support

- **Linux / macOS**: full feature set.
- **Windows (MSYS2 / Git Bash)**: experimental. The Windows-specific session-launch paths exist, but real tmux-server integration is unverified and its integration suite is skipped after a Windows CI hang. Requires tmux installed via MSYS2 (`pacman -S tmux`). Remote sessions (`ai c -R`), SSH tunnels (`ai tunnel`), and iTerm2 color slot management are unavailable. Desktop notifications work when the `[notify-win]` optional extra is installed (`pip install "ai-cli-utils[notify-win]"`).

Subcommands are dispatched through a Click command-group tree, so `--help` works at every level (e.g. `ai tunnel --help`, `ai quota watch --help`). The only pre-Click fast path is `ai internal <action>`, which is reserved for machine-to-machine bash-hook callers.

### Module layout

Behaviour is split across focused modules so `main.py` stays thin:

| Module | Purpose |
|--------|---------|
| `ai_cli.config` | XDG paths, `load_config`, session map, project registry |
| `ai_cli.session` | session naming, worktree ops, Gemini UUID lookup |
| `ai_cli.iterm2` | iTerm2 color slots, profile emit, tmux passthrough |
| `ai_cli.icon_generator` | tinted PNG generation and Dynamic Profile JSON |
| `ai_cli.transport` | VPN-aware mosh/SSH transport loop, Tailscale recovery |
| `ai_cli.tunnel` | autossh SSH tunnels, CDP (Chrome DevTools) management |
| `ai_cli.process_manager` | Circus daemon bootstrap and quota-watch lifecycle |
| `ai_cli.session_script` | bash template that wraps each session's engine loop and runs its in-shell watcher |
| `ai_cli.main` | CLI dispatch + session launch + update/deploy helpers |

---

## Session Management

### ai c

```bash
ai c [N|LABEL|NAME|TITLE] [-r] [-N NAME] [-w|-u] [-S|-I] [-p PROJECT] [-R] [-d|--dry-run] [--verbose]
ai c -t [NAME] [-b] [-W] [-p PROJECT] [-d|--dry-run]
```

Launch (or resume) a Claude Code session in a tmux worktree. The primary command.

- `N` — session number (default: auto-assigned). Creates worktree `.worktrees/<title>` on branch `wt-<title>`.
- `LABEL` — an optional label the title carries: `ai c review` is `myrepo-box-review-ada` with human
  names on, `myrepo-box-review-1` without.
- `NAME` — a human name this repo already holds on this host reattaches to that session
  (`ai c ada`); a positional name never creates one.
- `-r`/`--resume`: resume the existing session NAME or TITLE names, or refuse. TITLE is
  the full title with either suffix style: a first name (`myrepo-box-ada`) or a legacy
  index from before human names (`myrepo-box-2`, `myrepo-box-chief-1`). A running tmux
  session is attached. A stopped one, with its worktree or Claude Code transcript still on
  disk, is relaunched under the same title, which resumes its worktree, branch and
  conversation. When nothing holds the name, the launch exits 1 before it reserves a name,
  creates a worktree, branch or transcript, or asks a remote host to allocate one, and
  lists the closest existing titles and the command that starts a new session. An
  unreadable name registry refuses with its read error. With `-R`, the remote host answers
  the same question for its own sessions (`ai c myrepo-box-ada -R -r`); the chief of staff
  resumes with `ai cos -r` or `ai cos -R -r`. With `-d`, the plan is the attach, the
  relaunch or the refusal; a remote dry run contacts no host, so the remote host decides
  at launch. Design: [session-resume-by-name.md](../designs/session-resume-by-name.md).
- `-t`/`--probe`: launch a throwaway probe or test session that nothing treats as a
  session home. It is titled `myrepo-box-probe-1` (`myrepo-box-probe-NAME-1` with a NAME)
  by index and reserves no first name. Its worktree is made under the per-user state
  directory (`probe-worktrees/<repo>-<digest>/<title>`), outside the repository, and is not
  recorded as a canonical session worktree. Its run record is never relaunched at boot,
  the iTerm2 restore skips it, and `ai iterm2 sessions --prune` drops it once the session
  is gone. Refused with `-r`, `-N`, `-w`, `-u`, `-S`, `-F` and `-R`. Remove the worktree
  with `git worktree remove` once the probe has exited. Design:
  [probe-sessions.md](../designs/probe-sessions.md).
- `-N`/`--name NAME` — name the new session with this first name. It is checked like a picked
  one; a name not yet in the name database asks to be added. Never passed to the engine.
- `-w`/`--with-name`, `-u`/`--no-name` — with a label, keep or drop the first name for this launch.
- `-S`/`--session-names`, `-I`/`--no-session-names` — use human names or index names for this
  launch, whatever the config says.
- `-p PROJECT` — the project to launch in, given either as a registered task prefix or as the
  name of a repository directory under the projects directory. The launcher enters that
  directory before it creates the worktree, and refuses the launch when the directory is not
  there — naming the resolved path and the host it was sought on — rather than continuing in
  whatever directory it started in. A value that is neither a registered prefix nor an
  existing repository is reported as unknown input.
- `-R` — remote session: mosh + tmux on the configured remote host (auto-switches to SSH when VPN is active)
- `-d`/`--dry-run` — print the resolved launch plan and exit. Reports the values you cannot
  read off the command line: the session index actually free, the tmux-vs-bare decision
  after config and the tmux preflight, the worktree path and branch. It returns above
  every write, so it creates no worktree or branch, registers no workspace trust, reaps
  no stale sessions, and does not auto-update the CLI.
- `-X`/`--restart-session [TARGET]` — restart an existing idle managed Claude Code
  session by title, conversation UUID or tmux session name; also available at the top level.
- `-Y`/`--restart-check [TARGET]` — check the restart gates without acting.
- `-j`/`--json` — print the restart report as JSON.
- `-E`/`--event-completion` — with `-X`, always print a JSON launch receipt to stdout
  and return after successful replacement exec, leaving readiness to a later bind event.
  There is no session-record polling, registration wait, detached idle wait or compaction
  pre-step in this mode. It returns 0 with `reason: "started"`; launch failure retains
  exit 1 and the existing `verification-failed` report. The receipt carries the preserved
  UUID as `session_id` and `restart_session_id`, the replacement's `replacement_pid` and
  `replacement_start_time`, the restart actor's `engine_pid` and `engine_start_time`,
  and the existing remote `launch_id` or `null`. Birth times are Unix epoch seconds.
  Local and peer verification remain `UNVERIFIED`. Without this flag, the usual restart
  verification, output and exit codes apply. Remote `ai c -R -X -E` forwards the mode.

Session naming convention: the title is `{repo-alias}-{host-alias}-[{label}-]{N}`, or with
human names `{repo-alias}-{host-alias}-[{label}-]{name}`; its tmux session is `c-<title>` (local) or
`c-r-<title>` (remote). See README § Session names for how a name is picked and reserved.
The name records which machine launched the session. `ai c <name> -R` attaches to a live
session of that name on the remote host whichever way it was launched, so a session the
remote host started itself (`c-<project>-<name>`) is attached, not duplicated as a
`c-r-` session; only a slot with no live session is allocated as `c-r-`.
Without `-r`, a positional word that is not an existing title labels a NEW session, and the
launch prints `new session <title>` before it creates a worktree or a tmux session, so a
mistyped resume (`ai c cos` meant as `ai c <title> -r`) is visible.

Every alias lookup refreshes the local host identity before naming, including engine
launches, `ai cos`, remote allocation, migration, audit labels and name commands.
The single source order is:
a fresh login shell run without inherited `AI_HOST`, then the global tmux environment, then
the inherited `AI_HOST` only when neither fresh source answers. Each fresh probe is bounded
by `[relaunch] host_refresh_timeout_seconds`. If the remaining identity has no host alias,
`[naming] legacy_convention` controls whether allocation uses the old name form or refuses;
a refusal names the host id it tried and, when replaced, the inherited id.

The user config file (`$XDG_CONFIG_HOME/ai-cli-utils/config.toml`, defaulting to
`~/.config/ai-cli-utils/config.toml`; `%APPDATA%/ai-cli-utils/config.toml` on Windows)
is read on every launch; no restart is needed.
Choose the conventions per machine in its `[naming]` table:

| Mode | `alias_convention` | `legacy_convention` |
| --- | --- | --- |
| Both (default) | `true` | `"full"` |
| Alias only | `true` | `"off"` |
| Legacy only | `false` | `"full"` |
| Neither (refuse new launches) | `false` | `"off"` |

With aliases enabled, `"reach"` reaches existing legacy slots but requires an alias for a
new name. `alias_convention = false` with `legacy_convention = "reach"` is invalid;
change the latter to `"full"` or `"off"`. Invalid values and disabled naming report the
config keys to change. `ai cos` and `--chief` use the same policy as ordinary engine
launches, including legacy fallback when no alias resolves. `auto_migrate` controls only
fresh interactive legacy-slot migration; automatic restarts and recipe replay retain the
recorded identity without a migration or alias prompt.

**Plugin refresh before Claude Code starts.** Configure the plugins to refresh in
the same machine config file:

```toml
[launch]
refresh_plugins = ["example-plugin@example-marketplace"]
```

The default is `refresh_plugins = []`, which runs no plugin commands. The launcher
reads this list before each Claude Code start, including every wrapper auto-restart,
so changing it takes effect at the next start. For each `name@marketplace`, it runs
`claude plugin list --json` to record the installed version, then
`claude plugin marketplace update marketplace`, `claude plugin update name@marketplace`,
and another list to record the resulting version. The launch log contains
`Plugin refresh name@marketplace: before -> after`; an unavailable version reads `unknown`.
Each command has a 10-second timeout. A failed command or timeout logs one warning
and launch continues with the available installation. Missing `claude` skips the
refresh silently. This also applies to bare and one-shot launches.

Before a remote launch, when this machine's `ai` is an editable install, the launcher
fetches `origin/main` into that checkout (bounded by a 20 s timeout) and fast-forwards a
checkout that is behind, then restarts itself on the new code. The remote host's copy
is updated separately, so without this a client-side fix never reached the machine
running the client. It only fast-forwards a checkout that is clean and on `main`; a
dirty, diverged or other-branch checkout is left exactly as it was, the `Install:` line
says how far behind it is, and a warning names the checkout and the one command that
fixes it. A fetch that fails or times out reads `could not check origin/main` and the
launch continues.

Auto-runs `git pull --rebase --autostash` at session start to keep worktree current.

**How the launcher decides between tmux and bare.** In order: `[session] use_tmux`
wins outright if the machine sets it; with no setting tmux is the default rather than
an opt-in; a tmux that cannot be used gets one recovery attempt, described below; and
if it still cannot be used the launch continues in bare mode with a notice naming what
was lost. A missing tmux is not by itself fatal — tmux is an enhancement, and a missing
enhancement must not block a launch — but the launch still has to come away with *some*
way to submit the auto-compact prompt; see "The submission transport is required" below.
Windows skips the recovery because no native tmux exists there. The
decision is made **after argument validation and before any worktree is created**, so a
launch that has to fall back does not leave a worktree behind first.

The question asked is always whether tmux *runs*, never whether it is on `PATH`. `ai
doctor` runs `tmux -V` too, so a build that resolves and then dies on a missing shared
library reads as unusable instead of `OK`.

**Recovering an unusable tmux.** Two different faults, two different answers:

- **tmux is absent** — one unattended install attempt (rootless managers first:
  micromamba/conda/brew, then the system ones when running as root), verified by
  executing the result rather than by looking for the file.
- **tmux is present but cannot load a shared library** — the launcher reads the loader's
  own error, looks for the named library in directories around tmux's own install prefix
  (`<prefix>/lib`, `<prefix>/lib64`, and one nesting level below for extracted
  self-contained bundles), prepends what it finds to `LD_LIBRARY_PATH`
  (`DYLD_LIBRARY_PATH` on macOS), and re-runs tmux to prove the repair worked. Nothing is
  installed or written to disk, and a repair that cannot be demonstrated is undone.
  Because it is re-derived on every invocation, a host whose system libraries are removed
  by a restart recovers on the next launch with no action from you. No package manager is
  invoked for this fault: a freshly installed tmux elsewhere would not displace the broken
  one on `PATH`, so the install could not be verified to have fixed anything.

  Set **`AI_CLI_LIBRARY_PATH`** to name a library directory this search does not know
  about; it is tried first. The `export` line for making a successful repair permanent in
  ordinary shells is printed by `ai doctor`.

A tmux that runs but answers `tmux -V` in an unexpected shape is reported as
*unconfirmed*, not unusable, and the launch stays under tmux — losing detach/reattach over
an unparsed version string would be worse than the fault being guarded against.

**The submission transport is required: tmux OR a harness-owned pty.** A session's
auto-compact prompt is submitted through the terminal that owns the engine process, and
a running process's controlling terminal cannot be relocated into one it does not already
own — so the transport is settled at launch or never. Either satisfies the requirement:
tmux where tmux hosts the session, and a harness-owned pty on the bare paths. There are three outcomes:

1. **A transport resolves** and the launch proceeds.
2. **A transport was achievable here and is not available** — `[session] use_pty = false`
   with no tmux, or a pty that would not allocate. The launch is **refused**, naming what
   each side failed on, before any worktree, branch or session record exists. A session
   started without a transport does not fail now, it fails hours later when it fills its
   context and the harness has no way to compact it.
3. **No transport is implementable on this platform at all.** `os.openpty` is POSIX-only,
   so a Windows host without tmux can satisfy neither side however it is configured. This
   **proceeds**, with one notice naming the upgrade path.

Case 3 does not refuse, and not out of politeness: a condition no in-process action can
satisfy is not a guard, and refusing would strand `ai c` on every Windows host. What is
lost there is harness-*driven* compaction (the harness choosing when, so it can
checkpoint first), not compaction itself — the engine's own auto-compact needs no
terminal and still applies. tmux runs on Windows under MSYS2/Cygwin/WSL, so the upgrade
is reachable; a native pseudo-console (ConPTY) would also serve but needs a dependency
this package does not carry.

Set `[session] use_pty = false` to make tmux the only accepted transport; every bare
launch is then refused rather than started without a managed compaction path. That is an
explicit opt-out, so it refuses on every platform, including ones with no pty API.

The pty carries the submission and nothing else. On the pty path the launcher stays
running instead of exec'ing the engine: it starts the engine on a pty it owns, puts your
terminal in raw mode and copies bytes both ways unchanged. Text written to the session's
FIFO, `$XDG_STATE_HOME/ai-cli-utils/pty/<session>.in` (mode `0600`, also passed to the
engine as `AI_CLI_SUBMIT_FIFO`), is read by the engine as if typed, for example
`printf '/compact\r' > "$AI_CLI_SUBMIT_FIFO"`. A submitter gets no screen to read back, so
it confirms a compaction from the transcript, not the pane. Beside the FIFO,
`<session>.input-at` (mode `0600`) holds the epoch time, in seconds, of your last keystroke,
or of the launch if you have not typed yet. A submitter reads it to avoid writing over a
draft you have started. Text from the FIFO does not change it.

- **Resize:** SIGWINCH on the launcher copies your window size onto the pty, and the engine
  gets its own SIGWINCH.
- **Ctrl-C:** your terminal is raw, so the key arrives at the engine's pty as the byte
  `0x03`; the engine handles it as it would in a plain terminal. The launcher is never
  interrupted.
- **SIGTERM / SIGHUP:** forwarded to the engine. The launcher waits for it to exit,
  restores your terminal, removes the FIFO and exits `128 + signum`. Otherwise it exits
  with the engine's own status.

The pty path deliberately provides **no detach/reattach and no scrollback**. Those are
what tmux is for, and reimplementing them here would be reimplementing a multiplexer
badly. `TIOCSTI` is never used: it is privileged and denied on macOS.

**Bare mode (`-b`, or `[session] use_tmux = false`):** worktree isolation, `--name`,
and conversation resume all still apply — tmux is not a prerequisite for any of them.
A bare launch creates `.worktrees/<project>-N` on branch `wt-<project>-N`, `cd`s into
it, pins `CLAUDE_CODE_TASK_LIST_ID` to the session name, and passes `--continue` when
a transcript titled for this session already exists. The one thing bare mode cannot
offer is the auto-resume *loop*: nothing supervises the process, so when the engine
exits the session is over. Auto-index assignment also switches source — with tmux it
probes live sessions, in bare mode it scans `.worktrees/` and treats a directory with
no live engine process as a reusable slot.

**iTerm2 Session Name and Session Title:** when running inside iTerm2, each session automatically configures two tmux options on the new (or resumed) pane:

- `allow-passthrough all` — enables DCS passthrough so iTerm2-specific escape sequences (OSC 1, `SetProfile`, etc.) sent from inside tmux reach the outer terminal. Without this, name-setting sequences are silently dropped.
- `automatic-rename off` — prevents tmux from sending OSC 0/2 sequences for the running process name (e.g. `zsh`, `claude`), which would override the session name and flip the Session Title dropdown from "Name" to "Shell".

The result: the Session Name field in iTerm2's Edit Session → General tab is always set to the tmux session name (e.g. `c-ai-cli-2`, `c-r-session-1`), and the Session Title dropdown stays on "Name".

### ai g

```bash
ai g [N] [-p PROJECT] [-R] [-d|--dry-run] [--verbose]
```

Launch (or resume) a Gemini CLI session in a tmux loop. Same flags as `ai c`.

**Session resume:** on each launch or restart, `ai g` checks `~/.gemini/tmp/{name}/chats/` for the most recent session file. If a `checkpoint-{name}.json` exists and is newer than the latest chat file (or no chat files exist), it is automatically converted to a chat session file and resumed via `gemini -r {uuid}`. No manual `/resume load` required.

**Checkpoint conversion:** the conversion is idempotent — the same checkpoint always produces the same UUID. Once converted, Gemini auto-saves all future messages to the chat file natively. The original checkpoint is never deleted and remains as a fallback.

### ai cos

```bash
ai cos [SUFFIX|N] [-k|--machine-key KEY] [-H|--fm-home DIR] [-R] [-m MACHINE] [-r] [-d|--dry-run]
ai c -F|--chief [SUFFIX|N] ...     # the same launch on any engine: ai c, ai g, ai p, ai cx
ai mychief [SUFFIX|N] ...          # a configured command_aliases entry: the same as ai cos  # docs-example: skip
```

Attach to, resume or launch this machine's one chief-of-staff session (with `-R`, the remote
machine's). It needs the `[chief_of_staff]` settings in `config.toml`; the first four have no
built-in default, and a launch that needs one that is unset exits 1 and names it:

| Setting | Used for |
|---|---|
| `project` | the repository every chief launches into, whatever the cwd; `-p` may name it but no other project |
| `session_name` | the session-name segment that marks a chief: `ai c <session_name>[-N]` is the chief launch itself |
| `instance_key_prefix` | the registry's `chief_instance_key`, written as `<instance_key_prefix>/<machine-key>` |
| `homes_dir` | the directory of per-machine chief homes, relative to `$XDG_STATE_HOME` unless absolute or `~`; not needed with `-H` |
| `command_aliases` | optional list of extra subcommand names that behave exactly like `ai cos`; one naming a built-in command is refused |
| `flag_aliases` | optional list of extra long-flag names, without the `--`, that behave exactly like `--chief` on every engine command; one naming an existing option is refused |

- `-k`/`--machine-key KEY` — machine key naming the chief home (default `$AI_MACHINE_ID`, else
  the one seeded home under `homes_dir`).
- `-H`/`--fm-home DIR` — an explicit chief home.
- `-R` — run the chief on the remote machine; the dispatching machine reads only `project` and
  `session_name`, and the remote `ai` resolves and registers everything else.

Both `-k` and `-H` are refused without `-F`/`--chief` or `ai cos`. The full behaviour (attach,
resume, create, one chief per machine) is in the README's "Chief-of-staff session" section.

### ai ls

```bash
ai ls [--all]
```

Interactive tmux session picker using fzf. Shows `ai-cli` sessions by default (matching `_AI_SESSION_RE`). `--all` shows all tmux sessions.

- If fzf is available: launches interactive picker → attaches to selected session via `os.execvp`
- If fzf is absent and `apt` is available: installs fzf automatically, then launches picker
- If fzf is unavailable: prints a numbered list with project name and age, followed by `ai attach <name>` hint

Display columns: session name, age (e.g. `5m`, `2h`, `3d`). Sorted by most-recently-active first.

### ai attach

```bash
ai attach <session-name>
```

Thin wrapper: validates the session exists, then replaces the current process with `tmux attach-session -t <name>` via `os.execvp`.

Exits with error if no session named `<session-name>` exists.

### ai sessions

```bash
ai sessions [list] [-a] [-S host|pair|fleet] [-g host|status|relaunch|none] [-s KEYS] [-j]
```

Bare `ai sessions` runs `ai sessions list` with the `[sessions_list]` defaults. It prints one table
with the columns `HOST`, `SESSION`, `STATUS`, `CAUSE`, `CHANGED` and `RELAUNCH`, grouped by host
with this host first, and sorted by status (`running`, `relaunched`, `unknown`, `exited`) and then
by name. Only running sessions are listed unless `-a/--all` is given.

- `-S/--scope`: `host` is this host; `pair` (the default) adds the paired host; `fleet` adds every
  host that publishes. This host's rows come from its live registry; other hosts' rows come from
  the snapshots each host pushes with `ai sessions publish` to `[session_relaunch.publish] repo`,
  fetched with a 15-second bound. A host that does not publish is not shown, and each snapshot's
  time is printed under the table.
- `-g/--group-by` and `-s/--sort-by` choose the grouping column and a comma-separated list of sort
  keys (`status`, `name`, `changed` newest first, `host` this host first, `relaunch`).
- `-j/--json` prints this host's registry records exactly as before; it ignores the other options
  and refuses `-S pair` and `-S fleet`.

The defaults live in the `[sessions_list]` table of `config.toml`, read on every call, with a flag
overriding each:

```toml
[sessions_list]
group_by = "host"
sort_by = ["status", "name"]
show_all = false
scope = "pair"
pair_host = ""  # empty: the default [remote.machines] alias
```

[docs/reference/sessions-list-defaults.toml](../reference/sessions-list-defaults.toml) documents
each key.

### ai reconnect

```bash
ai reconnect [session_numbers...]
```

Lists remote tmux sessions (sessions starting with `c-r-`) on the configured remote host via SSH and prints the `ai c N -R` commands to reconnect to each.

- No args: all remote sessions
- `ai reconnect 1 3`: only sessions ending in `-1` or `-3`

Reads `[remote] host` and `[remote] user` from `~/.config/ai-cli/config.toml`.

### ai ssh

```bash
ai ssh [alias]
```

Opens an interactive SSH shell on a configured `[remote.machines.<alias>]` host. Without an alias, uses `[remote] default` (or the sole configured alias).

---

## Daemon Commands

### ai memory watch

```bash
ai memory watch
```

Starts the memory file watcher daemon. Watches `~/.claude/` for write activity and publishes `dream.started` / `dream.completed` events to NATS JetStream. Used to coordinate sync pausing during active memory writes.

Acquires PID file `memory-watch` — only one instance runs at a time.

### ai quota watch

```bash
ai quota watch start    # register with Circus and start the daemon
ai quota watch stop     # remove from Circus
ai quota watch status   # show Circus status
ai quota watch run      # run the daemon directly (Circus entry point)
```

Manages the quota-watch daemon via Circus. `start` is idempotent and runs automatically at session launch. The daemon polls Claude usage, fires threshold alerts (50/75/90%) through the unified `Notifier` (Discord, ntfy, OS fallback), and publishes `quota.threshold.{50,75,90}` events to NATS when available.

### ai telemetry writer

```bash
ai telemetry writer
```

Starts the telemetry writer daemon. Subscribes to NATS subject `telemetry.action.*` (durable consumer) and persists events to a local SQLite database at `~/.local/state/ai-cli/telemetry.db`.

Acquires PID file `telemetry-writer`.

### ai notifications

```bash
ai notifications list
ai notifications log [-n N] [-s SINCE] [-S/--source SRC] [-F/--failed]
```

`list` prints configured channels (Discord, ntfy, OS fallback) and whether their credentials are present. `log` shows delivery history from the `notification_log` table in `~/.local/state/ai-cli/quota.db`. Filters: `--last N`, `--since 2h|30m|1d|yesterday|ISO`, `--from`/`--to` date range, `--source` (e.g. `quota-watch`), `--failed` for rows with at least one failed channel.

---

## ai sync

```bash
ai sync push [-m|--memories-only] [-n|--dry-run] [-v|--verbose] [-f|--force]
ai sync pull [-m|--memories-only] [-n|--dry-run] [-v|--verbose] [-f|--force] [-p|--prefer-remote]
ai sync conflicts
ai sync resolve [-n|--dry-run] [-v|--verbose]
ai sync watch [-a|--auto] [-v|--verbose]
ai sync cleanup [-n|--dry-run] [-v|--verbose]
ai sync repair-worktree <project> <worktree> [-n|--dry-run] [-v|--verbose]
```

Bidirectional sync of Claude Code session data between local and remote host.

**Scope — what `ai sync` handles:**
- `~/.claude/projects/` — per-project JSONL conversation files and memory files
- `~/.claude/history.jsonl` — prompt history (translated for cross-machine path differences)

**Out of scope — what `ai sync` does NOT handle:**
- Git-tracked files (config, hooks, scripts) — use `git pull/push` in the relevant repo

This boundary exists because `ai sync` is for CC session migration (conversations, memories, history) — not for config management. Files tracked in git are authoritative in git; syncing them outside git creates conflicts and dirty working trees.

- `pull` — fetch remote state to local staging, apply to `~/.claude/`
- `push` — package local state and push to remote. Before committing, fetches origin and checks whether remote has newer versions of any files being modified. Aborts with an error listing the affected files if so — run `ai sync pull` first. Use `--force` / `-f` to bypass.
- `watch` — long-running listener: runs `ai sync pull --force` each time another machine asks for a pull over NATS. A session launch starts it with `--auto`, which exits at once when `[sync] auto_watch` is off.

Each action's options: [CLI reference](../reference/cli.md#ai-sync).

---

## ai ws

```bash
ai ws pull [--workspace PATH] [--remote] [--dry-run] [--verbose]
ai ws pull -w PATH
ai ws pull -r
ai ws pull -d
ai ws pull -v
```

Pull/rebase all repos and their worktrees listed in a VS Code `.code-workspace` file in one command.

**Primary use case:** after arriving at the Mac from Hetzner work, sync all repos without clicking each Source Control sync button.

**Behaviour:**

- Clean main tree → `git pull --rebase`
- Dirty main tree → `git stash push`, pull, `git stash pop` (logged with `⚠`)
- Clean worktrees → pulled
- Dirty worktrees → skipped (`↷`), never stashed — active sessions may have uncommitted work
- Non-existent or non-git folders → silently skipped or warned

Options: [`ai ws pull` in the CLI reference](../reference/cli.md#ai-ws-pull).

**Config** (`~/.config/ai-cli-utils/config.toml`):

```toml
[workspace]
local_path = "~/projects/myproject/core-cli-local.code-workspace"
remote_path = "~/projects/myproject/core-cli-remote.code-workspace"
```

Default falls back to `core-cli-local.code-workspace` if not configured.

**Example output:**

```text
Workspace: ~/projects/myproject/core-cli-local.code-workspace (13 repos)

  ✓  myproject          main
  ✓  companion            main   +  .worktrees/session-1   .worktrees/session-2
  ⚠  core-cli         main  (stashed+pulled)
  ✓  ai-cli-utils    main
  ↷  ai-cli-utils/ai-cli-1  (dirty, skipped)

Done: 11 pulled, 1 stashed+pulled, 1 skipped (dirty)
```

---

## Utility Commands

### Removed ai gemini command

`ai gemini`, `src/ai_cli/gemini.py`, and `src/ai_cli/research.py` were removed in v0.7.0. The command no longer has a replacement in this package. Use `ai g` to launch an installed Gemini CLI in a managed tmux session.

The historical implementation used API-key fallback, research-depth orchestration, and JSONL logging. Those behaviors and their configuration options are no longer provided by `ai-cli-utils`. This note preserves the removal context; the release history records the complete retired interface.

### ai spend gemini

```bash
ai spend gemini
```

Print a cost summary from historical local Gemini run logs, optionally supplemented with a GCP BigQuery billing export. It does not invoke Gemini or create new logs.

**BigQuery setup** (one-time, optional historical billing data):

1. Enable detailed billing export in Cloud Console → Billing → Billing export → Detailed usage cost
2. Add to `~/.config/ai-cli/config.toml`:

```toml
[gemini_billing]
gcp_project_id = "your-gcp-project"
billing_export_table = "your-project.billing_export.gcp_billing_export_v1_XXXXXX"
```

3. Install `google-cloud-bigquery` (`pip install google-cloud-bigquery`)

When BigQuery is not configured, `ai spend gemini` reports the local historical logs that are available.

**State files:**
- `~/.local/state/ai-cli/gemini-logs/YYYY-MM-DD.jsonl` — historical per-run log, if retained from a version before v0.7.0

### ai cc-usage

```bash
ai cc-usage push [-d/--dry-run]
ai cc-usage status
```

Scan Claude Code session JSONL files and push per-call token usage events to a configured REST API backend.

**`push`** scans `~/.claude/projects/` for assistant message entries not yet pushed (tracked by a cursor file), and POSTs them in batches of 500 to `/api/v1/usage/cc/ingest`. On success, the cursor is advanced so subsequent runs only send new events. `--dry-run` / `-d` scans and counts without pushing or advancing the cursor.

**`status`** prints the number of sessions tracked by the cursor and the timestamp of the last successful push.

**Config** (`~/.config/ai-cli-utils/config.toml`):

```toml
[core-cli]
api_url = "https://your-backend-host"
api_key  = "hw-api-..."
```

Both `api_url` and `api_key` must be set for `push` to run.

**State file:** `~/.local/state/ai-cli-utils/cc-usage-cursor.json` — maps session UUID → last pushed `occurred_at` ISO timestamp.

---

### ai layout

```bash
ai layout list
ai layout validate <name>
ai layout profiles <name>
ai layout <name>
```

`list`, `validate` and `profiles` are reserved; any other first argument is a layout name.

YAML-driven iTerm2 window/tab/pane layout system. Layout files live at `~/.config/iterm2/layouts/<name>.yaml`.

- `list` — list available layout files, showing tab names and description for each
- `validate <name>` — validate YAML schema without applying (exits 0 on success, 1 on error)
- `profiles <name>` — regenerate Dynamic Profile JSON files for all tabs in the layout without rebuilding the window (useful after color or icon changes)
- `<name>` — apply a layout: generates Dynamic Profiles, then launches a new iTerm2 window via the Python API with tabs and pane splits as defined

Layout YAML schema supports nested pane splits (vertical/horizontal), per-tab base profiles, tab colors, icon color overrides, and arbitrary startup commands per pane. See `docs/designs/iterm2-layout-system.md`.

### ai color

```bash
ai color <palette-name|#hex>
```

Ad hoc reassignment of the current session's iTerm2 tab color. Takes a palette color name (e.g., `purple`, `teal`) or a hex value (e.g., `#5e35b1`). Updates the tab color immediately via `SetColors` escape sequence and rewrites the session's Dynamic Profile JSON.

### ai iterm2 sessions

```bash
ai iterm2 sessions [-p|--prune [-P|--probe-remote]] [-r|--refresh] [-j|--json]
```

Lists the sessions `ai c` has recorded, local and remote, with the iTerm2 window, tab and pane each was last seen in. `--prune` removes records whose session is proved gone (`--probe-remote` also asks each remote host over ssh), `--refresh` re-reads every position in one AppleScript pass (macOS), and `--json` prints the registry as stored. The registry, its lifecycle and the `[iterm2.persistence]` switches are described in [iTerm2 session persistence](../iterm2-persistence.md).

### ai iterm2 restore

```bash
ai iterm2 restore [-s|--startup] [-d|--dry-run] [-o|--only local|remote] [-a|--arrangement NAME]
```

Re-attaches the recorded sessions after an iTerm2 restart (macOS). It opens the saved arrangement on demand, then types each session's `ai` command into its recorded pane when that pane is an idle shell (it never opens a tab or window, and at startup relaunches nothing when any session survived the restart), and prints one `restored`, `skipped` or `dead` line per session. `--dry-run` prints the plan and changes nothing; `--startup` is the form iTerm2's startup hook runs. Off unless `[iterm2.persistence.restore] enabled = true`; see [Restore](../iterm2-persistence.md#restore-ai-iterm2-restore).

### ai watch-vpn

```bash
ai watch-vpn
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

**Connection durability (AI-CLI-w679).** On the `transport = "ssh"` path a dropped link no
longer ends the session. The remote side runs under `tmux`, so a drop leaves it detached and
intact — `ai c -R` reattaches, bounded by `[remote] reconnect_attempts` (default 10) with
`reconnect_backoff` seconds before the first retry, doubling to a 30s cap. A clean exit (ssh
0) or an interrupt (130) is treated as *you* ending the session and is never reconnected;
anything else, 255 above all, is a transport failure. mosh needs none of this: it survives a
dropped link and a roaming address by design.

The budget bounds **one outage**, not the session's lifetime (AI-CLI-qre8). A connection
that stayed up for `[remote] reconnect_stable_seconds` (default 300) was working, so the drop
that ends it starts a fresh budget and a fresh backoff. Before this, a session that had
reattached cleanly nine times over several days gave up on its tenth brief blip, while its
remote tmux session and agent kept running. However the runner ends (`clean_exit`,
`interrupted`, `gave_up`), it writes the verdict, last ssh exit code, failed-attempt count,
total drops, credential-fault count, the resolved `cause` (`transport` or `auth`) and
`last_error` (the last lines ssh or its `ProxyCommand` printed to stderr) to
`~/.local/state/ai-cli-utils/transport-outcomes/<session>.json`, and that record is kept
after the launcher exits. One file therefore holds both the launcher's exit 255 and the
`ProxyCommand`'s reason for it.

The record also names the launcher that wrote it: `pid`, `launched_at`, `code_commit` (the
`ai update` commit it started on) and `code_version`. It is rewritten at every drop with the
verdict `reconnecting` (or `awaiting_auth` during a credential outage), so listing
`transport-outcomes/` shows how close each running launcher is to giving up, not only how
the finished ones ended. Each drop notice says how long the connection had held (`Connection
lost (ssh exit 255) after 4h02m connected`), which separates a fresh drop after a working
connection from one more failure in a flapping outage.

**A running launcher keeps the code it started with.** `ai update` replaces the installed
code, but a launcher that has been reattaching for days still runs what it imported at
launch, including an older reconnect ladder. When the installed commit has moved past the
loaded one, the next drop warns once per update, naming both commits. Reattaching with the
same command loads the new code and costs nothing, because the remote session keeps running.

**A credential outage is not a dropped link (AI-CLI-yewj).** An expired SSO token behind a
`ProxyCommand` fails as fast as a dead host and with the same exit 255, so ten quick
attempts used to run out in minutes while the token stayed expired. ssh's stderr is now
relayed live and also read: an attempt whose stderr matches `[remote]
reconnect_auth_patterns` (by default expired tokens, a failed SSO refresh and
`Permission denied (`) does not spend `reconnect_attempts`. Instead it asks you to
re-authenticate and retries on a 30s backoff doubling to 15 minutes, for up to `[remote]
reconnect_auth_wait_seconds` (default 14400, four hours) of continuous credential failure,
then reattaches by itself once a login succeeds.

**A remote launch that refuses is not a dropped link (AI-CLI-a8h5).** When the remote `ai`
refuses to launch (a chief home it cannot find, a session that must be migrated first), it
exits 78 rather than 1. ssh relays that status, and the ladder stops on it with one message
under the remote's own reason, verdict `refused`, cause `remote_launch`, instead of
retrying the same refusal `reconnect_attempts` times.

Among transport faults it stays deliberately indifferent to *why* the link dropped, because
from the client side an intermediary's idle timeout, its absolute session cap and a real
network failure are not distinguishable — reattaching covers all three.

The interactive session also sets `ServerAliveInterval` (default 30s) and
`ServerAliveCountMax` (default 3), configurable as `[remote] server_alive_interval` and
`server_alive_count_max`. That is **hardening, not the fix** for the above: it makes
behaviour independent of whatever `~/.ssh/config` a machine carries, and stops an idle
session being collected by a NAT or firewall that reaps silent flows. On the setup that
produced the original report, `ssh -G` showed those options were *already* in effect, so a
missing keepalive was not the cause there. The effective give-up time is
`interval × count_max`.

**Terminal restore on exit.** Both transports restore the local terminal when a session
ends, however it ended. A remote `tmux` or agent enables mouse reporting, bracketed paste,
focus reporting and an extended key mode, and clears them only on its own clean exit — so a
dropped connection used to leave them set locally, at which point mouse movement arrives in
your shell as literal text like `35;66;6M` (an SGR mouse report) and the only fix was
killing the terminal. The mosh path restores in the transport loop's `finally`; the pure-SSH
path appends the restore to its own `zsh -c` command line, because that path `exec`s and no
Python cleanup survives it.

### ai cdp

```bash
ai cdp start [-p|--port N] [-I|--no-incognito] [-t|--tunnel] [-L|--forward]
ai cdp stop  [-p|--port N] [-t|--tunnel]
ai cdp status
```

Launches and manages a Chrome, Chromium or Microsoft Edge instance with the Chrome DevTools
Protocol (CDP) remote debugging endpoint exposed. Useful for attaching Playwright,
agent-browser, or any CDP-capable tool to a browser session without managing browser flags
manually. Edge is supported because some managed endpoints ship Edge and no Chrome; being
Chromium-derived it takes the same flags and is found by the same port scan, so nothing
downstream distinguishes them.

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

Options and defaults: [`ai cdp start`](../reference/cli.md#ai-cdp-start) and
[`ai cdp stop`](../reference/cli.md#ai-cdp-stop) in the CLI reference.

**Config (`[cdp]` section in `config.toml`):**

```toml
[cdp]
# binary_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# port = 9222
```

Browser binary auto-detected in this order: `binary_path` config key → well-known
macOS/Linux/Windows paths → `shutil.which` across common executable names. Within the
well-known paths the order is **Chrome, then Chromium, then Microsoft Edge**, so a machine
carrying Chrome keeps resolving to it; Edge is only chosen when neither Chrome nor Chromium
is present.

Every launch also passes **`--remote-allow-origins=*`**. Chromium and Edge 111+ reject a
DevTools WebSocket handshake whose `Origin` header is not explicitly allowed, and the
rejection presents as a **hang rather than an error**, so a client that sends an `Origin`
gets no diagnostic at all. The wildcard is bounded by the port: `--remote-debugging-port`
binds loopback only, so reaching the endpoint already requires local access.

### ai tunnel

```bash
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

```bash
ai update [--force]
```

Reinstalls `ai-cli-utils` from source. Bumps the version to `{base}.post{timestamp}` so uv sees it as a new package, runs `uv tool install --force`, then restores `pyproject.toml`.

- `--force` — additionally passes `--reinstall` to reinstall all dependencies (not just ai-cli-utils). Use for corrupt-environment recovery.
- `deploy` is kept as an alias for backward compatibility.
- On Mac (`AI_HOST=mac`): runs `git pull --rebase` first.

### ai setup

```bash
ai setup
```

Detects the runtime environment and configures the Claude Code session config (`CLAUDE.md`) accordingly.

- **Managed platform detected** (`~/projects/CLAUDE.md` exists): confirms `CLAUDE.md` (lean version) is correct — the shared AI orchestration rules are inherited from `~/projects/CLAUDE.md`. No file changes made.
- **Standalone install**: copies `CLAUDE-full.md` → `CLAUDE.md` (standalone config with all rules), then runs `git update-index --assume-unchanged CLAUDE.md` so the swap doesn't show as a local modification.

Run once after cloning or installing. Safe to re-run at any time.

### ai ps

```bash
ai ps [-r|--refresh]
ai ps clean [-f|--force]
ai ps cron
```

Process hygiene inspector. `ai ps` lists every ai-cli-related process on this machine and the configured remote host with an orphan score and a verdict, and only reads.

**Scoring:** each process is assigned a score based on tmux session presence, PID file state, and age. Higher score = more suspect. A score of 80 or more is orphaned; 40 to 79 is suspect.

- `ai ps -r` re-checks the remote host over SSH instead of using the cached result.
- `ai ps clean` kills orphaned processes after a confirmation prompt; `-f` also kills suspect ones and skips the prompt.
- `ai ps cron` is the non-destructive maintenance a session launch runs: it removes stale transport files and refreshes the remote cache, and never kills a process.

### ai upgrade

```bash
ai upgrade
```

Reinstalls the `ai` tool from the current source tree using `uv tool install . --force`.

---

## Internal Commands

```bash
ai internal <action> [args...]  # docs-example: skip
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
| `cleanup-session-files` | `ai_name` | Remove session-specific icon PNG and Dynamic Profile JSON; called by EXIT trap on session end |
