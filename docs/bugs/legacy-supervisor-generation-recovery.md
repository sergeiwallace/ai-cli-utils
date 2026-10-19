---
title: "Legacy supervisors relaunch after interrupted generation acquisition"
category: bug
tags: [session, tmux, supervisor, signals, registry]
status: fixed
severity: P1
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Legacy supervisors relaunch after interrupted generation acquisition

## Founding Ask Coverage

The canonical request is the original worker prompt. Its verbatim text contains
private session and machine names, so it remains outside this public repository.
This coverage index is non-authoritative: investigate registration, retries,
signals and logs; reproduce before fixing; cover every managed Claude Code
session; preserve platform behavior; run focused and full verification.

**Status:** fixed locally — focused verification passes; full suite remains red;
not deployed.

**Severity:** P1

**Created:** 2026-10-18

## Table of Contents

- [Founding Ask Coverage](#founding-ask-coverage)
- [Symptoms](#symptoms)
- [Environment](#environment)
- [Reproduction Steps](#reproduction-steps)
- [Root Cause Analysis](#root-cause-analysis)
- [Prior Fix Attempts](#prior-fix-attempts)
- [Fix](#fix)
- [Acceptance Criteria](#acceptance-criteria)
- [Verification](#verification)
- [Observability](#observability)
- [Lessons Learned](#lessons-learned)

## Symptoms

A refreshed session child reports that its session is not registered and cannot
acquire a durable supervisor generation. After twenty failures it prints
"not retrying" but stays parked. Ctrl+C starts another failing child, printing
the same diagnostic again. An interrupt can also reach Python while it imports
modules used to render that error.

## Environment

Reported on Linux with Python 3.14.7. Reproduced locally on macOS with Python
3.14.3, bash and zsh, including terminal-delivered Ctrl+C in isolated tmux panes.
The recovery command and filesystem transaction use portable Python APIs;
Windows does not execute the POSIX supervisor fixtures.

## Reproduction Steps

1. Start a supervisor that predates durable generation registration. Publish a
   current stable child script without going through a new launcher invocation.
2. Leave the owning-host registry absent or without that session's boot recipe.
3. Let generation acquisition fail twenty times, or interrupt acquisition or
   its retry delay.
4. Observe that the historical supervisor restarts statuses 130 and 143; its
   recognized clean-stop statuses are 77 and 79.

`tests/test_session_begin_recovery.py` exercises the generated shell fragment,
the real internal CLI and filesystem, and the historical shell status contract.
Its tmux tests also verify that a shell can accept input after the wrapper stops.
External acquisition stand-ins provide deterministic failure and blocking;
the registration recovery tests use the actual registry implementation.

## Root Cause Analysis

`main._sync_stable_session_script` regenerates live scripts from saved launch
metadata without calling the owning-host registration performed by ordinary
`_do_session_launch` calls. A pre-feature session can therefore receive a child
that requires `session_run_state.begin` despite never receiving a local recipe.
Remote-style `c-r-session-1` names still require a **local** registry record on
the host running the supervisor; a connection record on the initiating host
cannot satisfy that requirement.

`session_run_state._find` correctly refuses an unregistered transition.
The legacy-child failure path then parks after twenty attempts and traps INT
with `exit 130`. The already-running historical supervisor is unaffected by
changes to its stable file: it restarts every child status except 77 and 79.
Interrupting the parked child therefore re-enters acquisition rather than
ending the launcher. Before parking, the child also lacks an acquisition-time
INT trap, so an interrupt during Python startup has the same relaunch outcome.

Recipe-bearing records are retained by registry pruning and removal. There is
no evidence here of pruning deleting a registered recipe or a name-conversion
race. Read-only inspection on the reported Linux host confirms that the session
has no registry entry and that its live supervisor's open script predates the
generation-begin block while retaining the 77/79 clean-stop contract. Its stable
replacement script contains the parking path and no registration recovery.
The failure counter is 38. Together these observations confirm the upgrade and
signal-status causal chain on the affected session itself.

Scope assessment: no new subsystem, public behavior contract or architectural
boundary is needed. Reuse the existing metadata, registry lock and clean-stop
statuses. A contained fix addresses the confirmed causal chain.

## Prior Fix Attempts

| Date | Attempt | Outcome |
|---|---|---|
| 2026-10-18 | Persist retry counts, back off to thirty seconds, park after twenty failures | Limits background retries but leaves the launcher alive; signal exit statuses still request a replacement from old supervisors. |

## Fix

Generated scripts pass a minimal structured launch description to the hidden
internal `_begin` command. It validates the exact session identity, engine,
absolute working directory, project prefix and options before recovery.
`session_run_state.begin` recovers a missing local record or recipe in the same
locked transaction that assigns the generation. Existing recipes, lifecycle
fields and relaunch policy survive; conflicting local ownership is refused.
Same-named remote connection records remain separate and unchanged. Existing
recipes retain normal generation acquisition after a configured host-label
change; metadata never replaces their recorded owner. Newly
recovered legacy sessions use relaunch policy `never` to avoid silently enabling
boot replay. Calls without metadata continue to refuse unregistered sessions.

The legacy child installs INT/TERM traps before invoking Python. These return
the clean-stop statuses understood by old supervisors. Exhausting retries or
failing to persist the retry counter returns 77 immediately instead of parking.
Successful acquisition resets the counter and removes these temporary traps
before the normal child signal handlers take over.

## Acceptance Criteria

- WHEN a managed script starts without a local record or recipe, the launcher
  SHALL recover registration before allowing an agent to start.
- WHEN reconstructing missing tracking would conflict with the requested session
  identity or recorded owning host, the launcher SHALL refuse without replacing
  registry records.
- WHEN acquisition or backoff receives Ctrl+C, the historical supervisor SHALL
  stop without starting a replacement agent.
- WHEN acquisition fails twenty consecutive times, the child SHALL return a
  recognized clean-stop status without parking or starting an agent.
- WHEN acquisition succeeds, the launcher SHALL preserve existing lifecycle
  policy and recipe, reset retry state, and retain normal signal handling.
- WHEN a supervisor supplies its generation, the child SHALL continue to bypass
  generation acquisition and retry-state creation.

## Verification

Frozen regression comparison uses a temporary copy of the package with the
three production modules taken from base revision `dedd4ffc`. The worktree and
frozen tests stay intact: **27 failed, 6 passed** on the base implementation;
**33 passed** with the fix. Two further cases proved that same-named remote
connections must not shadow local records: **2 failed** before that correction.
A further registered-owner compatibility case failed before preserving the
normal begin contract for existing recipes. The four real CLI integration
cases still fail on the base implementation after allowing a longer import
ceiling; their behavioral assertions are unchanged. Interrupt-response checks
retain their two-second deadline. Both shell variants and every engine are
covered.

The focused gate covering recovery, retries, run state, session commands,
enrolment, shell compatibility and the shell-quoting fixture reports
**147 passed**. Ruff checking all source and tests passes; formatting checks for
touched files pass.

Commands used for the final gates:

```sh
uv run --no-sync ruff check src tests
uv run --no-sync ruff format --check src/ai_cli/session_commands.py src/ai_cli/session_run_state.py src/ai_cli/session_script.py tests/test_session_begin_recovery.py tests/test_session_begin_retry.py tests/test_cli.py
uv run --no-sync pytest -q -n 4 --tb=short tests/test_session_begin_recovery.py tests/test_session_begin_retry.py tests/test_session_run_state.py tests/test_session_commands.py tests/test_session_enrol.py tests/test_session_script_bash32.py 'tests/test_cli.py::TestGetEngineScript::test_given_shell_metacharacters_when_rendering_then_worktree_is_shell_quoted'
uv run --no-sync pytest -q -n 16 --tb=short
```

The full gate includes the wider launcher/restart integration tests. Existing
cleanup watchers cannot enumerate processes in this sandbox, and restart
inspection cannot establish supervisor compatibility.
A base-revision comparison of representative existing tests reproduces
**1 failed, 1 passed, 2 errors** with the same inspection/cleanup restrictions.
A pre-correction full run reported **77 failed, 5373 passed, 23 skipped,
113 errors**. It exposed the quoting fixture's obsolete metadata occurrence
count, now corrected. The stderr-relay failure in `test_output_datetime.py` and
the existing lifecycle fixture timeout also reproduce on the base revision:
**2 failed, 1 passed** for those two cases plus the quoting fixture on the base.
The final full run reports **74 failed, 5377 passed, 23 skipped, 4 warnings,
110 errors in 856.55s (0:14:16)**. Neither generation-recovery test module has a
failure or error in that run. The revised shell-quoting test also passes.
The complete log is retained locally as `begin-full-complete.log` under the
temporary directory; it is not committed because tracebacks contain local paths.

The following inventory names every remaining failing/erroring module. Counts
are pytest result events: one test can fail and also error during cleanup.
Only the representative baseline comparisons above establish pre-existing
failures directly. The memory-guard assertion and other remaining cases have
not been individually classified; no unrelated production code was changed.

| Module under `tests/` | Failed | Errors |
|---|---:|---:|
| `test_bare_pty_host.py` | 2 | 0 |
| `test_ctrl_z_suspend.py` | 5 | 2 |
| `test_output_datetime.py` | 1 | 0 |
| `test_process_probe.py` | 2 | 0 |
| `test_pytest_memory_guard.py` | 1 | 0 |
| `test_reload_progress.py` | 11 | 11 |
| `test_restart_spawn_isolation.py` | 1 | 0 |
| `test_restart_supervisor.py` | 4 | 9 |
| `test_session_detached_exit.py` | 0 | 9 |
| `test_session_launch_integration.py` | 0 | 22 |
| `test_session_launch_shell_resolution.py` | 2 | 8 |
| `test_session_lifecycle_script.py` | 1 | 0 |
| `test_session_recovery_shell.py` | 0 | 6 |
| `test_session_reload_loop.py` | 1 | 3 |
| `test_session_retire.py` | 3 | 0 |
| `test_session_script_sinks.py` | 5 | 5 |
| `test_session_title_follows_resolved_name.py` | 0 | 1 |
| `test_stale_session_reaper.py` | 7 | 17 |
| `test_supervisor_terminal_handoff.py` | 5 | 0 |
| `test_tmux_default_server_isolation.py` | 2 | 4 |
| `test_tmux_ownership_classification.py` | 1 | 0 |
| `test_tmux_process_cleanup.py` | 13 | 13 |
| `test_transport_termination_signals.py` | 3 | 0 |
| `test_worktree_branch_path_mismatch.py` | 4 | 0 |

### Follow-up: real-supervisor fixture identity

The next CI run exposed seven failures in `test_stale_session_reaper.py` on
Linux and macOS. Its shared real-supervisor helper registered `test-session`
but generated launch metadata for engine `c` and agent name `session-1`.
`session_commands.begin` correctly rejected that identity before launching a
child. The helper also supplied only `argv` and `cwd` as its recipe: the base
revision's `valid_boot_recipe` and restart preflight already reject that
incomplete registration. Ordinary launches use `session_launch_state.register_launch`
to register the resolved engine-prefixed identity and a complete recipe.

The correction is confined to the fixture. Local and remote cases now use
`c-session-1` and `c-r-session-1`, respectively, and register complete recipes.
The helper calls `can_begin` before starting tmux so invalid setup fails at its
source. All launch-count, lease, heartbeat, process-exit and ownership assertions
remain intact; recovery validation and production signal handling are unchanged.

The frozen fast-exit regression reported **1 failed, 1 error** before the
correction and **1 passed, 1 error** after it. The base scratch checkout also
reported **1 passed, 1 error**. All three teardown errors come from the sandbox
denying process enumeration in the cleanup watcher. The base is `aeb2a421`,
extracted into a fresh temporary directory without changing Git worktrees.

The follow-up focused lifecycle gate reports **146 passed**, including both
shell variants of terminal-delivered Ctrl+C during legacy acquisition and
backoff. The reaper file reports **5 failed, 78 passed, 17 errors**: all five
failures occur after child startup while waiting for heartbeat evidence, which
the sandbox's denied process-group probes disable. All 22 failure/error events
also occur at the base revision. Its comparison of the previously failing files
reports **74 failed, 302 passed, 1 skipped, 1 warning, 110 errors**. Explicit
`PYTHONPATH` entries select the archived base package for subprocesses as well
as the outer tests, avoiding the shared environment's editable package path.
Ruff checking source and tests, formatting all 260 source and test files,
document validation, and `pyright src/` pass; Pyright reports zero errors,
warnings and information messages across 72 files.

The required full command, `uv run --no-sync pytest -q -n 8 --timeout 300`,
reports **80 failed, 5415 passed, 23 skipped, 2 warnings, 110 errors in
2315.15s (0:38:35)**. Comparing exact node IDs initially matches 74 failures
and every error to the base. Further comparisons of
`test_pytest_memory_guard.py` and `test_session_migrate.py` report
**5 failed, 45 passed** on base and **6 failed, 44 passed** on current, proving
three more of the full-run failures pre-existing. In total, **77 failures and
110 errors** reproduce on base.

Three full-run failures remain intermittent: the memory guard's host-locale
allocation probe times out after thirty seconds, and the two unreadable-store
migration cases fail while using a one-second subprocess budget. Serial
retests of those exact cases report **3 passed** on base and **3 passed** on
current. Both modules and their tests are unchanged from base. These results
establish no additional regression, but leave the failures under the full
parallel workload unresolved. No timeout or behavioral assertion was relaxed.

The focused lifecycle command was:

```sh
uv run --no-sync pytest -q -n 0 --timeout 120 tests/test_session_begin_recovery.py tests/test_session_begin_retry.py tests/test_session_run_state.py tests/test_session_commands.py tests/test_session_enrol.py tests/test_session_script_bash32.py
```

The base CI run also records an intermittent failure in
`test_tmux_process_cleanup.py` for the recovery-shell fixture's killed-run case:
the process tree survives cleanup. This differs from the local sandbox's
deterministic enumeration denial, which prevents investigating that mechanism
here. No cleanup behavior was changed without a matching causal reproduction.

## Observability

### Follow-up: enrol existing live sessions

Acquisition-time recovery only runs when a refreshed supervisor or child calls
`sessions _begin` with launch metadata. It cannot register a session that stays
alive without acquiring again. The original single-session `sessions enrol`
uses a recorded launch when available, but its no-record parser requires a
remote-style Claude name and `AI_HOST`. It does not support host-wide discovery.
A tmux name differing from the generated identity must be skipped, not inferred.

Bulk enrolment now discovers live tmux names, validates the exact stable-script
invocation and its embedded metadata, and checks saved metadata when present.
Generated defaults may add fields absent from older saved metadata; every
persisted field must still agree. Missing saved metadata is recoverable from the
script; missing evidence everywhere is not. The running supervisor need not
implement the current generation or child protocol. No process or script changes
are needed to write its local registration.

The registry transaction preserves existing ownership, policy, generations and
connection records. Concurrent enrolment returns `already-registered` for the
loser without another write. Pane PID and script bytes are rechecked before
the transaction; changed evidence is refused. New records default to policy
`never`, preserving the earlier recovery safety contract. Existing incomplete
records retain their recorded launch arguments when constructing their recipe.
The original named enrolment command and its output contract remain unchanged.

Automatic recovery is called from update-time stable-script refresh, the
version-change template-refresh command, supervisor generation acquisition, and
boot relaunch. These calls read current configuration and never block an
unrelated launch on discovery failure. The explicit bulk command reports those
failures and per-session skip reasons. Native Windows discovery uses the same
portable filesystem and registry code; hosts without tmux are diagnosed rather
than treated as empty. The existing Windows boot-relaunch restriction remains.

Additional acceptance criteria:

- WHEN bulk enrolment discovers a validated legacy session, the tool SHALL add
  its local registration without restarting or signalling any session.
- WHEN a valid local registration exists, the tool SHALL leave its record and
  registry bytes unchanged, including ownership, generation and relaunch policy.
- WHEN metadata, identity or ownership cannot be validated, the tool SHALL report
  a skip with a reason and leave that session unchanged.
- WHEN concurrent enrolment registers the same session, the tool SHALL persist
  exactly one record and preserve the winning registration.
- WHEN dry-run is requested, the tool SHALL report proposed results without
  creating a registry, lock, or script, including through boot relaunch.
- WHEN an update refresh, supervisor start or boot relaunch runs, the tool SHALL
  attempt live-session enrolment using configuration read at that call.
- WHEN the existing named enrolment command runs, the tool SHALL preserve its
  prior recipe output, validation and explicit policy behavior.

The new regression module initially reported **25 failed** on the starting
revision `c0738310`. After correcting the internal-refresh test's dispatcher and
the concurrent-test instrumentation, all **25 failed** again against the
archived base package, then **25 passed** against the implementation. Tests use
generated scripts and real registry files/locks; tmux and process inspection are
fixture-controlled so no live session is touched. README documents host-local
operator commands and each result status. Further regressions cover socket
permission failures, pane replacement, platform dispatch, boot dry-run and
uninitialised configuration. Bulk inspection uses a read-only config load so it
cannot create or rewrite a machine profile. The config cases reported **2 failed**
before that correction; the socket-permission case reported **1 failed, 1 passed**
before distinguishing unavailable discovery from an empty server.

The focused lifecycle/config gate reports **318 passed**. The refresh-dispatch,
burst-limit and complete bulk module gate reports **88 passed**. Refresh reuses
its existing tmux discovery for enrolment; burst tests retain the discovery
limit, bound the new per-pane inspections, and verify that rejected refreshes
perform no subprocesses or counter writes. Ruff and full Pyright pass, with
**0 errors, 0 warnings, 0 informations**.

A follow-up identity regression reported **1 failed** when read-only loading
returned no profile for an absent config: enrolment recorded the OS hostname,
but a later ordinary launch would register the configured environment identity.
Read-only loading now resolves missing profile fields in memory using the same
identity detection as ordinary loading, without persisting them. Its bulk/config
gate reports **90 passed**. Full Pyright on both base and changed code reports
**0 errors, 0 warnings, 0 informations**.

The full command, `uv run --no-sync pytest -q -n 8 --timeout 300`, reports
**76 failed, 5453 passed, 23 skipped, 3 warnings, 110 errors in 1387.23s
(0:23:07)**. An archived checkout of starting revision `c0738310`, using the same
interpreter and explicit base-package import paths, reports **77 failed,
5417 passed, 24 skipped, 3 warnings, 110 errors in 1693.80s (0:28:13)**.
Exact test-ID comparison matches **72 failures and all 110 errors** to base.
The base-only failures include three repository-index checks affected by the
archive having no Git metadata and two memory-allocation probes; these are not
claimed as fixes from enrolment.

Four failures were unmatched. Two were the refresh burst assertions already
corrected by reusing discovery and accounting for bounded pane inspections.
The source-enforcement check identified an undeclared output operation; bulk
reports now use the existing `session` operation. The fourth is the unchanged
memory-guard stderr-forwarding test's subprocess timeout under the full parallel
workload. Serial probes of that case plus source enforcement report **2 passed**
on base and **2 passed** on changed code. The timing failure remains intermittent;
its timeout and assertions were not changed.

After all corrections, the final gate reports **199 passed**:

```sh
uv run --no-sync pytest -q -n 4 --timeout 120 --tb=short tests/test_output_enforcement.py tests/test_cli_dispatch.py tests/test_runaway_loop_guards.py tests/test_session_enrol_all.py tests/test_config.py
uv run --no-sync ruff check src tests
uv run --no-sync pyright
```

The full-suite run began before the refresh and output-operation corrections;
their passing final focused gate verifies those fixes. A new ownership case was
also added after collection and is included in the final focused gate. The full
suite remains red from the reproduced base failures and process-inspection
restrictions. Native Windows and live operator enrolment were not exercised.

Read only the local state registry, saved metadata, launch logs and reaper log.
The registry has 31 records (3 local and 28 remote connection records), with no
boot recipes. There are 32 saved metadata files; 30 lack a launch-log path.
Ninety launch logs contain no durable-generation error or matching traceback.
The reaper log has generation-related entries but no reported acquisition
error. Raw logs and identifiers are not copied into this public document.

The descriptive SSH hostname fails name resolution, but the configured SSH
endpoint is reachable. Read-only inspection confirms one live pane, an absent
session registry entry, failure counter 38, saved metadata, and the old parking
path in its stable script. Reading the live supervisor's open script descriptor
confirms that it lacks generation acquisition and recognizes 77/79 as final.

The last 1,000 pane-history lines contain 34 unregistered-transition errors,
13 exhaustion messages and 9 `KeyboardInterrupt` traces. The affected session's
linked launch log contains none of those markers, nor do the 100 most recent
remote launch logs. This limits historical reconstruction from persistent logs;
the pane and live process provide the decisive evidence.

The remote registry has 9 local records, 5 with recipes. Of 29 saved Claude Code
session metadata files, 21 have no matching local registry record. Recovery must
therefore cover the shared upgrade path rather than a single session name.
No live sessions or remote state were changed, and the fix was not deployed.

## Lessons Learned

A refreshed child must be compatible with the supervisor already executing,
including its exit-status protocol. Standalone signal-response tests cannot
prove that a parent will stop relaunching the child. Registration recovery must
also be atomic with generation acquisition and preserve existing ownership and
relaunch policy.

<!-- /doc:region name="summary" -->

<!-- doc:region name="fix_log" kind="append_only" -->

2026-10-18: Implemented as an implementation worker; regression-first diagnosis
uses real shell, terminal and registry boundaries. No agents were delegated.

2026-10-18: Corrected the real-supervisor fixture identity and recipe without
changing production validation or existing behavioral assertions. Recorded full
verification, isolated base comparisons and the remaining intermittent cases.

2026-10-19: Added bulk enrolment and automatic recovery for validated live
sessions. Preserved the named enrolment contract, existing registrations and
boot policy; added regression coverage and host-local operator commands.

2026-10-19: Preserved launcher identity during read-only config inspection,
aligned bulk output with the shipped vocabulary, and recorded full-suite
comparison and final focused verification.

<!-- /doc:region name="fix_log" -->
