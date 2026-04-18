---
title: main.py Refactor — Module Extraction + CLI Dispatch Redesign
category: plans
tags: [refactor, architecture, cli, modules]
status: approved
source: internal
task: AI-CLI-39
---

# main.py Refactor — Module Extraction + CLI Dispatch Redesign

**Status:** APPROVED
**Created:** 2026-04-07
**Task:** `[AI-CLI-39]`

## Table of Contents

- [Overview](#overview)
- [Current State](#current-state)
- [Options](#options)
  - [Option A: Status quo](#option-a-status-quo)
  - [Option B+D: Module extraction (surgical)](#option-bd-module-extraction-surgical)
  - [Option C: Proper subcommand dispatch](#option-c-proper-subcommand-dispatch)
  - [Recommendation](#recommendation)
- [Phase 1 — Module Extraction (B+D)](#phase-1--module-extraction-bd)
  - [Import DAG](#import-dag)
  - [Extraction map](#extraction-map)
  - [Task Breakdown](#task-breakdown)
- [Phase 2 — Click Command Groups (C)](#phase-2--click-command-groups-c)
  - [Is Click best practice?](#is-click-best-practice)
  - [The fast-path problem and its solution](#the-fast-path-problem-and-its-solution)
  - [Migration sketch](#migration-sketch)
- [Batch Plan](#batch-plan)
- [Human Gates](#human-gates)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

---

## Overview

`main.py` is 3385 lines across 69 functions. Several distinct subsystems — handoff queue, iTerm2 fleet, transport switching, tunnel/CDP, config loading — are co-located in one file purely because they were added incrementally. The `cli()` dispatch function alone is 1213 lines of interleaved `if sys.argv[1] == "..."` chains and inline closures. This makes the file hard to navigate and extends the pre-existing circular-import workarounds (5 modules already do deferred `from .main import` to avoid cycles at import time).

Phase 1 (this plan) extracts cohesive subsystems into dedicated modules, eliminates the deferred-import workarounds, and reduces `main.py` to ~1000 lines of session-launch logic + dispatch. Phase 2 (designed here, implemented later) replaces the argparse/sys.argv hybrid with a proper Click command group tree.

> **Feedback Round 1:** Is the scope right? Too broad, too narrow? Anything missing from the goal?
> - <enter feedback here>

---

## Current State

### Size breakdown

| Area | Lines (approx) |
|------|---------------|
| `get_engine_script` (bash template) | 365 |
| iTerm2 (color slots, profile, emit) | 300 |
| `cli()` dispatch | 1213 |
| Session naming + worktree | 200 |
| Handoff queue | 200 |
| Transport (VPN loop, watcher) | 200 |
| Tunnel + CDP commands | 250 |
| Config + XDG + project registry | 200 |
| Circus/signal-watch management | 150 |
| Misc (update, auto-update, Gemini UUID) | 300 |

### Existing deferred imports (circular-import smell)

Five modules already work around circular dependencies by importing from `main.py` lazily inside functions:

- `icon_generator.py` — `get_xdg_state_home`
- `messaging.py` — `load_config`
- `quota_db.py` — `load_config`
- `quota.py` — `load_config`
- `sync.py` — `load_config`, `_get_main_project_dir`, `_get_projects_dir`, `get_xdg_state_home`
- `telemetry.py` — `load_config`
- `vpn_watch.py` — `_is_vpn_active`, `get_xdg_state_home`

Once `load_config` and XDG helpers move to `config.py`, all these become clean top-level imports.

---

## Options

### Option A: Status quo

Keep everything in `main.py`.

**Pros:**
- Zero migration risk
- No test churn

**Cons:**
- Continues growing — every new feature lands in main.py by default
- Circular import workarounds multiply
- `cli()` at 1213 lines is opaque — hard to onboard contributors, hard to reason about control flow
- New feature isolation is impossible — a bug in handoff.py and a bug in transport.py look the same in stack traces

**Verdict:** Not sustainable past v0.3.

---

### Option B+D: Module extraction (surgical)

Extract logical clusters from `main.py` into dedicated modules. `main.py` stays as entry point and dispatch. `cli()` shrinks from 1213 to ~400 lines as inline logic moves to module functions. `get_engine_script` (365 lines of bash template) moves to its own file.

**Pros:**
- Each extraction is independent — can be done one at a time, with full test suite as safety net
- Resolves all existing deferred import workarounds
- No architectural change to CLI dispatch (argparse stays as-is)
- Low regression risk — function signatures don't change, tests don't need rewriting
- Branch + PR model: easy to revert individual extractions

**Cons:**
- `cli()` still uses sys.argv dispatch after Phase 1 (addressed in Phase 2)
- Doesn't fix `--help` coverage (no help for `ai tunnel`, `ai handoff`, etc.)
- Some functions in `cli()` have large inline closures (e.g., `_on_handoff` in signal-watch) that need extracting to move cleanly

---

### Option C: Proper subcommand dispatch

Replace the sys.argv chain + argparse hybrid with a proper Click command group tree. `cli()` becomes a `@click.group()`. Each command (`tunnel`, `handoff`, `sync`, `signal-watch`, `internal`) becomes a `@cli.group()` or `@cli.command()`.

Note: `layout.py`, `gemini.py`, and `research.py` already use Click. This would unify the dispatch model.

**Pros:**
- `--help` at every subcommand level (`ai tunnel --help`, `ai handoff post --help`, etc.)
- Argument validation at the framework level — no manual `if len(sys.argv) < N` guards
- Industry standard for Python CLIs of this complexity (same pattern used by `ruff`, `uv`, `gh`)
- Each command group can live in its own module with `@click.group()` — natural module boundaries
- Eliminates the ~500 lines of `if sys.argv[1] == "..."` chains in `cli()`

**Cons:**
- Larger migration — every command dispatch block must be rewritten as Click command functions
- The inline `_on_handoff` closure in signal-watch and similar inline patterns must be extracted before migration is feasible
- Requires Phase 1 (module extraction) first — migrating 1213-line monolithic `cli()` to Click is error-prone; doing it after extraction (when `cli()` is ~400 lines of thin dispatch) is much safer
- argparse → Click is a library change, not just a structural change — any usage of `parse_known_args()` (which the session parser uses for passthrough args) needs care

**Is this best practice?** Yes, unambiguously. The hybrid sys.argv + argparse approach is a code smell that accumulated through incremental development. Click command groups are the canonical Python CLI pattern at this complexity level. Every major Python CLI tool uses it or an equivalent (argparse subparsers, typer, etc.).

---

### The fast-path problem (Option C deep-dive)

The current pre-argparse dispatch exists because commands like `ai internal *` and `ai vpn-watch` need to short-circuit before the session-launch argparse parser runs (which would reject unrecognized commands). This is a symptom of using a single argparse parser for all commands.

With Click groups, there is no fast-path problem — Click dispatches to the right command group before any argument parsing happens for the selected command. The concern is not real once you have proper subcommand dispatch.

The one legitimate concern: `ai internal *` is called from bash hooks hundreds of times per session. Click adds ~15ms startup overhead vs raw sys.argv. Measured on modern hardware this is imperceptible, but if it becomes an issue, a thin `main()` wrapper can handle `internal` before invoking Click:

```python
def main():
    # Sub-millisecond fast path for machine-to-machine commands.
    # These never need --help, validation, or Click overhead.
    if len(sys.argv) > 1 and sys.argv[1] == "internal":
        _handle_internal(sys.argv[2:])
        return
    cli()  # Click handles everything else
```

This keeps `ai internal` startup at <1ms while Click handles all user-facing commands.

---

### Recommendation

**Phase 1 now (this PR): B+D — module extraction.**
**Phase 2 later: C — Click command groups.**

Rationale: Phase 1 cleans the foundation without changing dispatch behavior. After Phase 1, `cli()` is ~400 lines of thin dispatch over well-defined module functions — small enough to migrate to Click safely in Phase 2. Attempting Phase 2 before Phase 1 means migrating 1213 lines of interleaved logic, which is high regression risk.

---

## Phase 1 — Module Extraction (B+D)

### Import DAG

Critical constraint: no circular imports. The dependency graph must be a strict DAG with `config.py` at the bottom and `main.py` at the top.

```
main.py (cli dispatch)
  ├── session_script.py (get_engine_script)
  │     ├── iterm2.py
  │     ├── session.py
  │     └── config.py
  ├── iterm2.py
  │     └── config.py
  ├── session.py
  │     └── config.py
  ├── handoff.py
  │     ├── config.py
  │     └── messaging.py
  ├── transport.py
  │     ├── config.py
  │     └── messaging.py
  ├── tunnel.py (tunnel + CDP)
  │     └── config.py
  ├── process_manager.py (signal-watch management)
  │     └── config.py
  └── config.py (XDG, load_config, session map, project registry)
        └── (stdlib + external only)
```

After Phase 1, existing modules that currently do `from .main import load_config` etc. will import from `config.py` directly — eliminating all deferred import workarounds.

---

### Extraction map

| New module | Functions to extract from main.py | Est. lines |
|---|---|---|
| `config.py` | `get_xdg_config_home`, `get_xdg_state_home`, `get_xdg_cache_home`, `_migrate_xdg_dir`, `load_config`, `get_session_map_path`, `get_session_map`, `save_session_map`, `_get_projects_dir`, `_find_project_dir`, `get_current_project_name`, `_get_main_project_name`, `_get_main_project_dir`, `_get_project_registry_path`, `_get_handoff_queue_dir`, `load_project_registry`, `validate_registry_completeness`, `_get_project_prefix_by_name`, `get_project_aliases` | ~350 |
| `session.py` | `get_project_prefix`, `find_next_index`, `find_recent_session`, `cleanup_stale_sessions`, `resolve_session`, `build_session_name`, `detect_repo_root`, `create_worktree`, `cleanup_worktree`, `_sweep_stale_iterm2_profiles`, `_find_latest_gemini_uuid`, `get_latest_gemini_session_id` | ~300 |
| `iterm2.py` | `_iterm2_state_dir`, `_is_iterm2`, `_load_iterm2_config`, `_iterm2_palette`, `_assign_iterm2_color_slot`, `_release_iterm2_color_slot`, `_iterm2_session_type`, `_emit_iterm2_profile_setup` + merge `icon_generator.py` | ~350 |
| `handoff.py` | `_log_handoff_event`, `post_handoff`, `_find_best_handoff`, `check_handoff`, `check_handoff_project`, `claim_handoff`, `complete_handoff`, `_claim_handoff_for_signal` | ~200 |
| `transport.py` | `_is_vpn_active`, `_write_transport_state`, `_ensure_vpn_watcher`, `_maybe_stop_vpn_watcher`, `_run_transport_loop` | ~200 |
| `tunnel.py` | `_cmd_tunnel_start`, `_ensure_nats_tunnel`, `_cmd_tunnel_stop`, `_cmd_tunnel_status`, `_find_chrome_binary`, `_cmd_cdp_start`, `_cmd_cdp_stop`, `_cmd_cdp_status` | ~200 |
| `process_manager.py` | `_ensure_circusd`, `_cmd_signal_watch_start`, `_cmd_signal_watch_stop`, `_cmd_signal_watch_status` | ~150 |
| `session_script.py` | `get_engine_script` | ~365 |

**`main.py` after extraction:** ~1000 lines (`cli()` dispatch + remote session launch + update/deploy logic + `_find_aicli_project_path` + `_auto_update_if_stale`).

**`icon_generator.py` fate:** Merge into `iterm2.py`. Its functions (`generate_session_icon`, `cleanup_session_files`, `generate_dynamic_profile`) are all iTerm2 concerns. The merged `iterm2.py` replaces both files.

---

### Task Breakdown

#### T-01: Extract `config.py`

**Size:** M  
**Batch:** 1

Extract XDG path helpers, `load_config`, session map r/w, and project registry functions. This is the most critical extraction because it eliminates all deferred import workarounds in the existing modules.

**Deliverables:**
- `src/ai_cli/config.py` (new)
- `src/ai_cli/main.py` (functions removed, `from .config import *` added)
- `src/ai_cli/sync.py`, `messaging.py`, `quota.py`, `quota_db.py`, `telemetry.py`, `icon_generator.py` — deferred imports replaced with top-level imports from `config.py`
- `tests/test_config.py` (new — extract existing config-related tests from `test_cli.py`)

**Acceptance criteria:**
- [ ] All extracted functions live in `config.py`
- [ ] `from .main import load_config` no longer appears anywhere in the codebase
- [ ] `from .main import get_xdg_state_home` no longer appears anywhere
- [ ] `pytest` passes (1264 tests)
- [ ] `ruff check` clean

**Dependencies:** None

---

#### T-02: Extract `session.py`

**Size:** M  
**Batch:** 2

Extract session naming, index management, worktree operations, and Gemini UUID lookup.

**Deliverables:**
- `src/ai_cli/session.py` (new)
- `src/ai_cli/main.py` (functions removed)
- `tests/test_session.py` (new or expanded — relocate relevant tests)

**Acceptance criteria:**
- [ ] All extracted functions live in `session.py`
- [ ] No imports from `session.py` back to `main.py`
- [ ] `pytest` passes

**Dependencies:** T-01 (session.py imports from config.py)

---

#### T-03: Extract `iterm2.py` + merge `icon_generator.py`

**Size:** M  
**Batch:** 2

Consolidate all iTerm2 logic (color slots, profile emit, dynamic profiles, icon generation) into a single `iterm2.py`. Delete `icon_generator.py`.

**Deliverables:**
- `src/ai_cli/iterm2.py` (new, replaces icon_generator.py)
- `src/ai_cli/icon_generator.py` (deleted)
- `src/ai_cli/main.py` (functions removed)
- Any test imports of `icon_generator` updated

**Acceptance criteria:**
- [ ] `icon_generator.py` no longer exists
- [ ] All icon_generator tests pass against `iterm2.py`
- [ ] All iTerm2 color/profile functions accessible from `iterm2.py`
- [ ] `pytest` passes

**Dependencies:** T-01

---

#### T-04: Extract `handoff.py`

**Size:** S  
**Batch:** 2

Move all handoff queue functions. The inline `_on_handoff` closure in `cli()` signal-watch dispatch must be extracted to `handoff.py` as a proper `build_handoff_callback(config, session_id, handoff_dir)` factory function.

**Deliverables:**
- `src/ai_cli/handoff.py` (new — was partially there, expand)
- `src/ai_cli/main.py` (functions + inline closure removed)

**Acceptance criteria:**
- [ ] All handoff functions accessible from `handoff.py`
- [ ] `_on_handoff` inline closure replaced with `handoff.build_handoff_callback()`
- [ ] Existing handoff tests pass
- [ ] `pytest` passes

**Dependencies:** T-01

---

#### T-05: Extract `transport.py`

**Size:** S  
**Batch:** 2

Move VPN detection and transport loop. `vpn_watch.py` currently imports `_is_vpn_active` and `get_xdg_state_home` from `main.py` — after this extraction, it imports from `transport.py` and `config.py` respectively.

**Deliverables:**
- `src/ai_cli/transport.py` (new)
- `src/ai_cli/main.py` (functions removed)
- `src/ai_cli/vpn_watch.py` (import updated)
- `tests/test_transport.py` (import path updated if needed)

**Acceptance criteria:**
- [ ] `vpn_watch.py` no longer imports from `main.py`
- [ ] All transport tests pass
- [ ] `pytest` passes

**Dependencies:** T-01

---

#### T-06: Extract `tunnel.py`

**Size:** S  
**Batch:** 3

Move tunnel and CDP command implementations. These are self-contained except for `get_xdg_state_home` and `load_config`.

**Deliverables:**
- `src/ai_cli/tunnel.py` (new)
- `src/ai_cli/main.py` (functions removed)

**Acceptance criteria:**
- [ ] All tunnel and CDP functions in `tunnel.py`
- [ ] `pytest` passes

**Dependencies:** T-01

---

#### T-07: Extract `process_manager.py`

**Size:** S  
**Batch:** 3

Move Circus process management (signal-watch start/stop/status + `_ensure_circusd`).

**Deliverables:**
- `src/ai_cli/process_manager.py` (new)
- `src/ai_cli/main.py` (functions removed)

**Acceptance criteria:**
- [ ] `_ensure_circusd` and `_cmd_signal_watch_*` in `process_manager.py`
- [ ] `pytest` passes

**Dependencies:** T-01

---

#### T-08: Extract `session_script.py`

**Size:** S  
**Batch:** 3

Move `get_engine_script` (365-line bash template generator). This is the highest single-function line count in `main.py`.

**Deliverables:**
- `src/ai_cli/session_script.py` (new)
- `src/ai_cli/main.py` (`get_engine_script` removed, import added)

**Acceptance criteria:**
- [ ] `get_engine_script` in `session_script.py`
- [ ] `pytest` passes

**Dependencies:** T-01, T-02, T-03 (session_script imports from config, session, iterm2)

---

#### T-09: Update docs + roadmap

**Size:** S  
**Batch:** 4

Update `docs/tools/ai-cli-usage.md` (module references), architecture memory, and roadmap.

**Dependencies:** T-01 through T-08

---

## Phase 2 — Click Command Groups (C)

Designed here. Implemented after Phase 1 is validated in production.

### Is Click best practice?

Yes. The current hybrid (sys.argv dispatch + argparse for the default command) is a common anti-pattern that accumulates when commands are bolted onto an existing single-command CLI. Industry-standard Python CLIs at this complexity level all use proper subcommand dispatch:

- `ruff` — Click command groups
- `uv` — custom (Rust), but same pattern
- `gh` — cobra (Go equivalent)
- `aws` CLI — botocore command tree

The concrete pain today: `ai tunnel --help` prints the argparse help for session launch (wrong). `ai handoff --help` does the same. There's no per-command help for any command except the session launcher. Click fixes this structurally.

### The fast-path problem and its solution

The current sys.argv pre-dispatch exists because the argparse parser is defined for `ai c`/`ai g` and would reject `ai tunnel start` as an unrecognized command. With Click groups, each command is its own parser — there's no cross-contamination.

The one real startup concern: `ai internal *` is called from bash hooks on every CC session event. With Click, that's an extra ~15ms per call. Solution: a thin `main()` wrapper that handles `internal` before Click loads:

```python
def main():
    # Machine-to-machine fast path — never needs --help or Click overhead
    if len(sys.argv) > 1 and sys.argv[1] == "internal":
        from ai_cli.internal import handle_internal
        handle_internal(sys.argv[2:], load_config())
        return
    cli()  # Click handles everything else
```

This keeps `ai internal` at <1ms while giving all user-facing commands full Click subcommand dispatch.

### Migration sketch

```python
# main.py after Phase 2
import click
from .session import run_session
from .handoff import cmd_handoff
from .tunnel import cmd_tunnel, cmd_cdp
# etc.

@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    if ctx.invoked_subcommand is None:
        # `ai` with no subcommand — legacy: show help
        click.echo(ctx.get_help())

@cli.command('c')
@click.argument('name', required=False, default='')
@click.option('-R', '--remote', is_flag=True)
@click.option('-p', '--project', default='')
@click.option('-r', '--resume', is_flag=True)
@click.option('-W', '--no-worktree', is_flag=True)
@click.option('-b', '--bare', is_flag=True)
@click.option('-s', '--sandbox', is_flag=True)
def cmd_session(name, remote, project, resume, no_worktree, bare, sandbox):
    run_session('c', name, remote=remote, project=project, ...)

@cli.group()
def tunnel():
    pass

@tunnel.command('start')
@click.argument('local_port', type=int)
@click.argument('remote_port', type=int, required=False)
@click.option('--forward', is_flag=True)
def tunnel_start(local_port, remote_port, forward):
    cmd_tunnel_start(local_port, remote_port or local_port, forward)

# etc.
```

The session-launch fast path (`run_session`) is a module function called by the Click handler — same code, cleaner dispatch.

### Phase 2 scope estimate

After Phase 1, `cli()` is ~400 lines. The Click migration touches:
- Rewrite `cli()` as `@click.group()` with registered subcommands
- Extract each sys.argv dispatch block to a named function (most of this happens in Phase 1)
- Add argparse → Click option translations for the session launcher
- Update tests that call `cli()` directly with mock `sys.argv` — they'll need `CliRunner` instead

Estimated effort: ~4 hours of Claude Code time. Low regression risk after Phase 1 cleans the foundation.

---

## Batch Plan

| Batch | Tasks | Focus | Gate |
|-------|-------|-------|------|
| 1 | T-01 | `config.py` — eliminates all deferred import workarounds | Tests pass |
| 2 | T-02, T-03, T-04, T-05 | session, iTerm2, handoff, transport | Tests pass |
| 3 | T-06, T-07, T-08 | tunnel, circus, session_script | Tests pass |
| 4 | T-09 | Docs + roadmap | Human review |
| (later) | Phase 2 | Click command groups | Separate PR |

All batches on `feature/main-py-refactor` branch. Each batch is a separate commit. Full test suite gate between batches.

> **Feedback Round 1:** Does the batching make sense? Should any tasks be reordered, split, or merged?
> - <enter feedback here>

---

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Before coding | Approve scope, module names, import DAG |
| UAT | After Batch 3 | Verify `ai c`, `ai c -R`, `ai tunnel`, `ai handoff` work end-to-end |
| Phase 2 gate | After Batch 3 UAT | Click migration scope pre-approved; UAT after Phase 2 completes |

---

## Open Questions

1. **`process_manager.py` naming** — ✅ Decided: `process_manager.py` (avoids `circus` package name conflict).
2. **`iterm2.py` + `icon_generator.py` merge** — ✅ Decided: keep separate. `icon_generator.py` is significant enough to stand alone; deferred import fixed as side effect of T-01 (`config.py` extraction).
3. **Phase 2 timing** — ✅ Decided: implement Phase 2 (Click) in this task. Firm foundation preferred over deferral.

---

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
| 2026-04-18 | Plan approved | process_manager.py naming; icon_generator.py stays separate; Phase 2 (Click) included in scope |
