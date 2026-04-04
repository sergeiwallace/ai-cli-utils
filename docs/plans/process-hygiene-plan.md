# Process Hygiene (`ai ps`) — Implementation Plan

**Status:** DRAFT
**Created:** 2026-04-04
**Task:** `[AI-CLI-28]`

## Table of Contents

- [Overview](#overview)
- [Process Catalog](#process-catalog)
- [Orphan Detection Rules](#orphan-detection-rules)
- [Options](#options)
- [CLI Design](#cli-design)
- [Task Breakdown](#task-breakdown)
- [Batch Plan](#batch-plan)
- [Human Gates](#human-gates)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Overview

ai-cli manages several long-running background processes (mosh-server, signal-watch, autossh tunnels, circus workers) across two machines. When sessions end abnormally — network drops, mosh client disconnects, CC crashes — server-side processes are left running indefinitely with no owner. Today this requires manual inspection and killing. Five orphaned `mosh-server` processes from March 2026 were found on Mac and killed manually, triggering this feature.

Goal: `ai ps` shows a live inventory of all ai-managed processes with age and status. `ai ps clean` kills orphans with a confirmation prompt. Optionally runs automatically at session start or on a schedule.

> **Feedback Round 1:** Is the scope right? Too broad, too narrow? Anything missing from the goal?
> - <enter feedback here>

## Process Catalog

Processes tracked by `ai ps`:

| Process | Where | Orphan risk | Notes |
|---------|-------|-------------|-------|
| `mosh-server` | Mac + Hetzner | **High** — client disconnect leaves server running | Main motivator |
| `signal-watch` | Mac + Hetzner | Medium — tied to tmux session that may be gone | Has PID file at `~/.local/state/ai-cli/signal-watch-<project>.pid` |
| `autossh` tunnels | Mac | Low — managed by ai tunnel, but can outlive intent | 2 persistent tunnels currently |
| `circusd` | Mac | Low — intentionally persistent daemon | Should rarely need cleaning |
| `nats-server` | Hetzner | Low — intentionally persistent | Don't kill; report status only |
| `ai memory watch` / `ai sync watch` | Mac | Medium — launched per tmux session | Orphaned if tmux session is gone |

**Not tracked:** pytest/uv test processes (transient, not ai-managed), claude CLI itself (the main process).

## Orphan Detection Rules

### mosh-server
Two detection strategies (see Options):

**Age-based:** Any `mosh-server` process older than a configurable threshold (default 6h) is flagged as orphaned. Simple, no false negatives, but may flag intentionally long-lived servers.

**Connection-based:** Check if a corresponding `mosh-client` process exists with a matching UDP port. If no client → orphaned. More precise but requires port matching via `lsof`/`netstat`.

Recommendation: connection-based with age-based as fallback (if `lsof` unavailable).

### signal-watch
Orphaned if: PID in pidfile is no longer running, OR the tmux session it was watching no longer exists.

### autossh
Orphaned if: the target tunnel port is no longer in use / the configuration that created it no longer exists. Low priority — autossh processes are intentional and rarely stale.

### memory watch / sync watch
Orphaned if: the tmux session that launched them no longer exists (check via `tmux ls`).

## Options

### Option A: `ai ps` as a standalone subcommand

`ai ps [clean] [--remote] [--force] [--all]` — new top-level subcommand in `main.py`.

**Pros:**
- Discoverable (`ai --help` lists it)
- Consistent with existing `ai tunnel`, `ai signal-watch` pattern
- Easy to call from cron or hooks

**Cons:**
- Adds to an already large `main.py` (mitigated by the test-split work)

### Option B: Integrate into `ai status` or existing command

Add process listing to `ai signal-watch status` and extend `ai tunnel status`.

**Pros:**
- No new top-level command
- Avoids sprawl

**Cons:**
- Doesn't cover cross-domain processes (mosh + circus + watchers)
- `ai ps clean` has no natural home
- Harder to call from automation

### Recommendation

**Option A.** The process hygiene problem is cross-domain — it spans mosh, signal-watch, autossh, circus. A dedicated `ai ps` command gives it a clear home, matches the Unix `ps` mental model, and is easy to automate. The code volume is modest (~150 lines).

## CLI Design

```
ai ps                       # list all managed processes (local only)
ai ps --remote              # list local + Hetzner processes
ai ps clean                 # show orphans and prompt to kill (local)
ai ps clean --remote        # show orphans on both machines and prompt
ai ps clean --force         # kill orphans without confirmation
```

**Output format (`ai ps`):**
```
LOCAL (mac)
  mosh-server   pid=9414   age=27d   ← orphaned (no client)
  mosh-server   pid=24205  age=0h    active (client: artelier)
  signal-watch  pid=33021  age=2h    active (project: sw)
  autossh       pid=29914  age=20h   active (tunnel: R:9222)
  autossh       pid=62702  age=6d    active (tunnel: L:4222)
  circusd       pid=75058  age=5d    active

HETZNER (sergei@178.104.70.139)
  mosh-server   pid=518783 age=0h    active (art session)
  nats-server   pid=29579  age=4d    active
```

**`ai ps clean` output:**
```
Orphans found:
  LOCAL  mosh-server  pid=9414  age=27d  (no active client)
  LOCAL  mosh-server  pid=25170 age=10d  (no active client)

Kill 2 orphan(s)? [y/N]
```

## Task Breakdown

### T-01: Process detection utilities

**Size:** M
**Batch:** 1

Implement the core detection functions:
- `_list_mosh_servers(remote: bool) → list[ProcessInfo]` — parse `ps` output, cross-reference with `lsof -i UDP` for client detection
- `_list_signal_watch(remote: bool) → list[ProcessInfo]` — read pidfiles + tmux session check
- `_list_autossh(remote: bool) → list[ProcessInfo]` — parse `ps` output
- `_list_watchers(remote: bool) → list[ProcessInfo]` — memory/sync watch, cross-ref tmux sessions
- `ProcessInfo` dataclass: `pid, name, age_seconds, status ("active"|"orphaned"), detail`

**Deliverables:**
- Detection functions in `main.py` (or new `process_hygiene.py` module)
- `ProcessInfo` datatype

**Acceptance criteria:**
- [ ] Correctly identifies 0 orphans when all sessions are active
- [ ] Correctly identifies orphaned mosh-server (no mosh-client with matching port)
- [ ] Works when remote=True (SSH to Hetzner)
- [ ] Handles `lsof` unavailable gracefully (falls back to age-based)

**Dependencies:** None

### T-02: `ai ps` and `ai ps clean` CLI commands

**Size:** S
**Batch:** 1

Wire T-01 detectors into the CLI.

**Deliverables:**
- `ai ps` subcommand in `main.py`
- `--remote`, `--force` flags
- Formatted table output
- Confirmation prompt for `ai ps clean`

**Acceptance criteria:**
- [ ] `ai ps` lists all managed processes with age + status
- [ ] `ai ps clean` shows orphans and prompts before killing
- [ ] `ai ps clean --force` kills without prompting
- [ ] `ai ps --remote` includes Hetzner processes (SSH)
- [ ] No processes killed without explicit user action or `--force`

**Dependencies:** T-01

### T-03: Tests

**Size:** S
**Batch:** 1

**Deliverables:**
- `tests/test_process_hygiene.py` (or in `test_cli.py`)
- Unit tests for orphan detection logic (mocked `ps`/`lsof` output)
- CLI dispatch tests for `ai ps` and `ai ps clean`

**Acceptance criteria:**
- [ ] Orphan detection tested with mocked process output
- [ ] `ai ps clean --force` tests verify correct PIDs killed
- [ ] Remote=True path mocked (no actual SSH)

**Dependencies:** T-01, T-02

### T-04: Auto-hygiene hook (optional, deferred)

**Size:** S
**Batch:** 2 (post-UAT)

Run `ai ps clean --force --local-only` automatically on `ai c` session launch (or as a daily cron). Configurable via `[process_hygiene]` section in config.toml.

**Dependencies:** T-01, T-02

## Batch Plan

| Batch | Tasks | Focus | Gate |
|-------|-------|-------|------|
| 1 | T-01, T-02, T-03 | Core feature | Human UAT |
| 2 | T-04 | Auto-hygiene (optional) | Human approval |

> **Feedback Round 1:** Does the batching make sense? Should any tasks be reordered, split, or merged?
> - <enter feedback here>

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Before coding | Scope, orphan detection approach, CLI design |
| UAT | After Batch 1 | Run `ai ps` live, verify orphan detection accuracy |

## Open Questions

1. **Orphan detection strategy for mosh-server:** connection-based (lsof UDP port match) vs age-based (>6h threshold)? Connection-based is more precise but requires `lsof`. Age-based is simpler but has false positives for long-lived legitimate sessions. Recommendation: connection-based with age fallback.

2. **Auto-hygiene trigger:** should `ai ps clean` run automatically (session start, daily cron) or always require explicit invocation? Running automatically risks killing processes the user intended to keep. Recommendation: explicit-only for now, with opt-in cron via config.

3. **Scope of `ai ps` output:** only ai-managed processes (mosh, signal-watch, autossh, circus, watchers) or all background processes the user might care about? Broader scope is more useful but harder to define "managed". Recommendation: ai-managed only.

4. **Remote default behavior:** should `ai ps` always include Hetzner, or only when `--remote` is passed? SSHing adds ~1-2s latency. Recommendation: local-only by default, `--remote` opt-in.

5. **Module placement:** add to `main.py` (~150 lines) or new `process_hygiene.py` module? Given we're already splitting test_main.py, a new module keeps concerns separate. Recommendation: new module.

> **Feedback Round 1:** Your thoughts on the open questions:
> - <enter feedback here>

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
