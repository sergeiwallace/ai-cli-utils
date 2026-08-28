---
title: "Stale-Session Reaper Phase 1 Implementation Audit"
category: audit
tags: [audit, implementation, session-management, safety]
status: findings-pending-fix
date: 2026-08-28
template_version: "audit-1.0.0"
---

<!-- doc:region name="scope" kind="replaceable" -->

# Stale-Session Reaper Phase 1 Implementation Audit

**Status:** findings-pending-fix

**Created:** 2026-08-28

**Auditor:** Codex (GPT-5), independent implementation audit

**Target artifact:** Phase 1 implementation at commit `6668a46e0a7771d10772ee74434ca9a153b57ff2`

## Table of Contents

- [What Was Audited](#what-was-audited)
- [Scope](#scope)
  - [In scope](#in-scope)
  - [Out of scope](#out-of-scope)
- [Methodology](#methodology)
- [Status Summary](#status-summary)
- [Round 1 — Main Audit](#round-1--main-audit)
  - [R1 Summary](#r1-summary)
  - [Implementation Audit Checklist](#implementation-audit-checklist)
  - [Safety Properties](#safety-properties)
  - [R1 Findings](#r1-findings)
  - [R1 Resolution Pass](#r1-resolution-pass)
  - [R1 Verification Matrix](#r1-verification-matrix)
- [Decisions Requiring Team Input](#decisions-requiring-team-input)
- [Outstanding Issues to Fix](#outstanding-issues-to-fix)
- [Already-Correct Items](#already-correct-items)
- [Anti-Patterns to Watch For](#anti-patterns-to-watch-for)
- [Sign-Off Checklist](#sign-off-checklist)
- [Audit Log](#audit-log)
- [Appendix: Files Read](#appendix-files-read)
- [Appendix: Commands Run](#appendix-commands-run)
- [Appendix: Reviewer Prompt](#appendix-reviewer-prompt)

## What Was Audited

The shipped Phase 1 stale-session-reaper implementation at commit
`6668a46e0a7771d10772ee74434ca9a153b57ff2` was checked from scratch against the converged Phase 1
design and its T-1.1 through T-1.5 acceptance criteria. The worktree `HEAD` exactly matched that
commit; the only pre-audit worktree entry was the untracked canonical audit stub at this file path.

The primary question was whether any shipped path can terminate a tmux session that has become live
or lacks the required corroboration. The answer is that no ordinary evaluator branch bypasses its
two explicit gates, but the implementation is **not safe to enable in `reap` mode**: the final file
lease does not fence a concurrent tmux pane mutation, and usable heartbeat evidence can be created
without enforcing lifetime-lease ownership. Both are blocking safety gaps.

## Scope

### In scope

- The 16-row Implementation Audit checklist in `docs/designs/stale-session-reaper.md:225-246`.
- Phase 1 acceptance criteria T-1.1 through T-1.5 at
  `docs/designs/stale-session-reaper.md:145-191`.
- The evaluator, local ledger, lease, process identity, generated supervisor/child topology,
  heartbeat command wiring, configuration defaults, launch-path authority, and named regression
  suites.
- Independent concurrency and shell-process analysis beyond the checklist where required to answer
  the destructive-safety question.
- Public-package portability and hygiene checks on the implementation diff.

### Out of scope

- Phase 2 lifecycle commands and public-documentation corrections. Checklist items 11 and 12 are
  recorded as `N/A — Phase 2 scope, not yet implemented`, as required by the audit invocation.
- Applying fixes. This audit was authorized to write only this audit document.
- Live production state. No Phase 2 worker exists at this commit, so the standalone evaluator is not
  registered or run by a shipped launch/daemon command.

## Methodology

**Approach:** Pin the worktree, read the design and implementation surfaces, expand every reaper,
heartbeat, cleanup, and `kill-session` symbol to its call sites, trace all preserve/kill branches,
inspect the implementation diff and history, inspect test controls rather than trusting test names,
run shell-level signal/input probes, and attempt every requested test/lint command. Claims below are
marked **CONFIRMED** when reproduced from code or command output and **PLAUSIBLE** when they are a
concurrency consequence not exercised against live tmux in this restricted sandbox.

The canonical repo-local `docs/audits/TEMPLATE.md` was absent. The documented fallback path was also
absent, so the current canonical template at `docs/audits/TEMPLATE.md` in the development harness was
read in full before this document was written. This document follows its implementation-audit
structure and finding taxonomy.

## Status Summary

**Latest round:** Round 1 (complete)

**Outstanding by severity / verdict (across all rounds):**

| Severity | Count | Of which fixed | Of which deferred |
|----------|-------|----------------|-------------------|
| P0 | 2 | 0 | 0 |
| P1 | 3 | 0 | 0 |
| P2 | 2 | 0 | 0 |
| P3 | 0 | 0 | 0 |
| **Total** | **7** | **0** | **0** |

**Ship-readiness verdict:** **Not safe to proceed to Phase 2.** Reap-mode activation must remain
unreachable. DV-1 and DV-2 leave plausible paths to killing a session that is live or whose record
does not prove the required lease epoch. DV-3 shows that the signal verification gate enables
evidence for a shell topology that does not satisfy the verified signal/input model. JA-1 confirms
that the mandated real-subprocess exit gate was not implemented, and F-1 is a shipped remote-session
lifecycle regression. The approved default remains `observe`, but observe-first is not a substitute
for closing safety invariants before adding Phase 2 execution.

<!-- /doc:region name="scope" -->

<!-- doc:region name="round_1_findings" kind="replaceable" -->

## Round 1 — Main Audit

**Round 1 auditor:** Codex (GPT-5), independent implementation audit

**Round 1 date:** 2026-08-28

**Round 1 scope:** Full Phase 1 implementation/compliance and destructive-safety pass at the pinned
commit. No source, test, design, or closed design-audit file was edited.

### R1 Summary

The 16 implementation-checklist rows resolve to 8 PASS, 4 PARTIAL, 2 FAIL, and 2 Phase 2 N/A.
Seven new findings were reproduced: 2 P0, 3 P1, and 2 P2. The explicit evaluator predicate is
conjunctive, repeats both gates under the file lease, and targets an opaque tmux ID. Those facts do
not close the final action race because the lease is not honored by tmux itself, nor do they prove
that a ledger record originated during one continuous lease epoch.

The requested pytest commands could not initialize because the sandbox forbids both the user uv
cache and every temporary directory pytest/portalocker attempted. This is recorded as **UNVERIFIED**,
not as a pass or a test failure. Independent no-write Ruff runs completed: `ruff check --no-cache`
reported `All checks passed!`, and `ruff format --no-cache --check` reported
`107 files already formatted`.

### Implementation Audit Checklist

| # | Section / Decision | Verdict | Evidence and verification note |
|---|--------------------|---------|--------------------------------|
| 1 | Design Overview filled with shipped behavior | **FAIL** | `docs/designs/stale-session-reaper.md:60-62` still says `Status: stub — to be filled during/after implementation.` **CONFIRMED** by direct read. See IC-1. |
| 2 | D-1 ledger is atomic, local, schema-validated, and delivery-independent | **PASS** | `write_heartbeat()` validates local fields, writes/flushed/fsyncs a same-directory temporary file, and atomically replaces at `src/ai_cli/stale_session_reaper.py:156-215`; evaluator validation is at `:355-386`; local failure does not suppress publication at `src/ai_cli/main.py:1325-1353`. **CONFIRMED** from code; pytest execution unavailable. |
| 3 | D-2 requires both gates, revalidates, and targets session ID | **PARTIAL** | Initial gates are conjunctive at `src/ai_cli/stale_session_reaper.py:296-298`; both repeat under lease at `:303-315`; kill uses `candidate.session_id`. The final pane mapping is not compared atomically with kill, leaving DV-1. **CONFIRMED code; PLAUSIBLE destructive interleaving.** |
| 4 | D-3 has no launch-path registration or execution | **PASS** | Symbol sweep finds no `StaleSessionReaper`/`evaluate_once` call outside its module/tests. `main.py` imports only the ledger write/remove helpers in the internal heartbeat actions (`src/ai_cli/main.py:1342-1362`). **CONFIRMED.** |
| 5 | D-4/D-5 default and invalid configuration fail closed | **PASS** | D-4 approved exactly “`observe` default; explicit configuration for `reap`” (`docs/designs/stale-session-reaper.md:417-457`). Code defaults to `observe`/600 and returns `None` for invalid mode/threshold (`src/ai_cli/stale_session_reaper.py:29-33`, `:231-244`); `evaluate_once()` returns before tmux on invalid settings (`:275-277`). The distributed config matches at `src/ai_cli/config.py:200-206`. **CONFIRMED.** |
| 6 | D-6 preserves launch-time bookkeeping authority | **PASS** | `cleanup_stale_sessions()` lists names and runs auxiliary-state sweeps only (`src/ai_cli/session.py:520-562`); no reaper import, heartbeat read, or tmux kill exists in that function. **CONFIRMED.** |
| 7 | D-7 binds the sole managed marker and every ledger record to one generation token | **PASS** | Tokenless tmux rows are ignored at `src/ai_cli/stale_session_reaper.py:93-115`; ledger path/record/reader match name and token at `:127-142`, `:187-201`, `:355-374`; the publisher verifies the tmux option at `src/ai_cli/main.py:1330-1345`. **CONFIRMED.** Lease provenance is a separate DV-2 failure. |
| 8 | D-8 holds the generation lease through final identity/gate verification and ID-targeted kill | **PARTIAL** | The context manager remains active through final gates and kill (`src/ai_cli/stale_session_reaper.py:303-318`, `:388-404`). It fences cooperating file-lock holders, but not a tmux client that mutates panes after line 312; see DV-1. **CONFIRMED code; PLAUSIBLE destructive interleaving.** |
| 9 | D-9 uses current-boot monotonic age and preserves on boot/clock uncertainty | **PASS** | Boot generation comes from validated finite `psutil.boot_time()` (`src/ai_cli/stale_session_reaper.py:145-153`); record reads reject missing/invalid clocks, boot mismatch, future stamps, and non-stale ages (`:355-386`). Wall time is never used for eligibility. **CONFIRMED.** |
| 10 | Phase 1 integrated positive, mutation-negative, lease, and pane/process-identity ACs are covered | **PARTIAL** | Genuine positive and two single-gate zero-kill controls exist at `tests/test_stale_session_reaper.py:94-145`; held-lease and UNKNOWN controls exist at `:147-181`. Pane replacement, PID reuse/identity mutation, boot/clock/config/error paths, exact final-race mutation, continuous update-cycle lease, crash release, remote recovery, and real tmux/terminal controls are absent. **CONFIRMED** by complete test-definition inventory. See JA-1. |
| 11 | Phase 2 start/stop/status/run success and failure ACs are covered | **N/A — Phase 2 scope, not yet implemented** | Phase 1 deliberately has no worker lifecycle/CLI integration (`docs/designs/stale-session-reaper.md:193-220`). |
| 12 | Phase 1 publication-parity and Phase 2 public-documentation correction ACs are covered | **N/A — Phase 2 scope, not yet implemented** | Per invocation, this mixed row is reserved for Phase 2 disposition. Phase 1 local-write failure still reaches best-effort publication in code (`src/ai_cli/main.py:1325-1353`). |
| 13 | Supervisor acquires before first usable evidence and replaces all three direct self-`exec` transitions with child cycles | **PASS** | Signal gate, marker write, lease open/acquire, and `_reaper_evidence_enabled=true` precede heartbeat start (`src/ai_cli/session_script.py:141-197`, `:253-256`). Child cycles use status 78 instead of direct script exec (`:257-272`, `:620-625`, `:799-815`). **CONFIRMED static implementation.** |
| 14 | `ProcessProbe.capture_identity()` has typed Procfs/Psutil implementations and UNKNOWN/mismatch preservation coverage | **PARTIAL** | Typed implementations are present at `src/ai_cli/process_probe.py:116-127`, `:167-172`, `:272-282`, and `:376-386`; evaluator rejects UNKNOWN and unequal identities at `src/ai_cli/stale_session_reaper.py:331-353`, `:433-444`. Tests cover backend types and UNKNOWN (`tests/test_stale_session_reaper.py:167-181`, `:214-226`) but not controlled identity mismatch/PID reuse. **CONFIRMED.** |
| 15 | Candidate-local exceptions preserve only the failed candidate and do not roll back earlier authorised kills | **PASS** | Settings/tmux-list failures return with no kill; each materialized candidate is independently wrapped, and prior IDs stay appended (`src/ai_cli/stale_session_reaper.py:269-290`). No rollback path exists. **CONFIRMED code; dedicated A-killed/B-throws regression absent.** |
| 16 | D-10 keeps one pane-leader supervisor and continuous lease through child update cycles, signal forwarding, and crash release | **FAIL** | Child/subshell FD closes are present (`src/ai_cli/session_script.py:125-133`, `:199-204`), but the signal model fails shell probes (DV-3), real continuous-lock/crash/update tests are absent (JA-1), and remote normal exits terminate the supervisor instead of spawning the next child (`:257-272`, `:743-815`; F-1). **CONFIRMED failures.** |

### Safety Properties

| Property | Verdict | Evidence and verification note |
|----------|---------|--------------------------------|
| Both process-liveness and heartbeat-staleness gates are independently required before any evaluator kill; lease is reacquired and both are repeated immediately before kill | **PASS, with blocking fence caveat** | Initial conjunction: `src/ai_cli/stale_session_reaper.py:296-298`. Exclusive lease and repeat: `:303-315`. No branch reaches `kill_session` from only one gate. **CONFIRMED.** DV-1 shows that “immediately” is not atomic with tmux mutation. |
| Every evaluator error/exception/missing-data path preserves the candidate | **PASS** | Invalid config and initial tmux error return empty at `:275-282`; candidate exceptions are caught at `:284-290`; invalid candidate, probe, heartbeat, lease, revalidation, and kill failures return false at `:292-317`, `:331-386`, `:388-404`. **CONFIRMED from production control flow; test matrix incomplete.** |
| Kill is always by immutable session ID, never by name | **PASS** | Production adapter uses `tmux kill-session -t <session_id>` at `:117-119`; evaluator passes the captured ID at `:315`. No reaper name-targeted kill exists. **CONFIRMED.** |
| No launch/attach/resume/cleanup path reaches evaluator or a kill-capable function | **PARTIAL / literal property FAIL** | No launch path reaches the evaluator. However, an explicit sandbox launch kills and recreates its resolved same-name session at `src/ai_cli/main.py:2992-2997`, covered by `tests/test_cli.py:633-688`. This is pre-existing, target-local behavior—not the prohibited global sweep—but it makes the stronger “any kill-capable function” statement literally false. **CONFIRMED.** |
| Exactly one process holds the generation lease for the supervisor lifetime; child and heartbeat subshell close inherited FD first | **PARTIAL** | Child closes the exported FD before child-body work at `src/ai_cli/session_script.py:125-133`; heartbeat subshell closes before its loop at `:199-205`; supervisor closes during final cleanup at `:211-220`. **CONFIRMED static ordering.** No real lock-backend, fork-reference, update-cycle, or crash-release test exists, so full-lifetime cardinality is **UNVERIFIED**; DV-2 additionally shows record writes are not owner-authenticated. |
| T-1.4 signal-model gate actually gates evidence, and real Bash/zsh tests execute | **FAIL** | PGID/job-control success gates the marker/lease/evidence branch (`src/ai_cli/session_script.py:144-197`). But async background children ignore group SIGINT in both installed shells, and Bash redirects their stdin to EOF; the gate checks neither. Tests fake `ps` and `flock` and send signals directly to PIDs (`tests/test_stale_session_reaper.py:257-379`), not via a real terminal/tmux. Both shells are installed, so the fixture would not skip, but pytest could not initialize in this sandbox; execution status is **UNVERIFIED**. See DV-3. |
| Negative controls are genuine zero-kill controls rather than tautologies | **PASS but incomplete** | Live+stale and ended+fresh tests call the real evaluator and assert both `evaluate_once() == []` and `tmux.kills == []` (`tests/test_stale_session_reaper.py:113-145`). Held lease, UNKNOWN identity, generation mismatch, and observe mode do the same (`:147-211`). **CONFIRMED genuine assertions.** The required mutation breadth is absent (JA-1). |
| Any shipped Phase 1 path can kill a session it should preserve | **FAIL — safety cannot be certified** | DV-1 supplies a post-snapshot tmux-mutation race; DV-2 supplies ledger evidence without enforced lease provenance. Both consequences are **PLAUSIBLE** interleavings grounded in **CONFIRMED** code gaps. No ordinary, non-racing evaluator branch was found that skips its explicit gates. |

### R1 Findings

#### DV-1: Final pane state is not fenced atomically with `kill-session` — `P0`

**Location:** `src/ai_cli/stale_session_reaper.py:303-315`

**Evidence:**

```python
refreshed = self._find_exact_current_candidate(candidate)
...
final = self._ended_snapshot(refreshed)
if final is None or not self._heartbeat_is_stale(refreshed) or not _same_snapshot(initial, final):
    ...
    return False
if not self._tmux.kill_session(candidate.session_id):
```

The held object is a filesystem lease (`src/ai_cli/stale_session_reaper.py:388-404`). Tmux does not
consult it. A tmux client can add/respawn a live pane on the same session ID after the final snapshot
and before the separate kill command. The generation, name, ID, ledger, and filesystem lease all
remain unchanged, so line 315 kills the newly live pane.

**Why it matters:** This is the exact forbidden outcome: a session that became live after observation
can be terminated. The code sequence is **CONFIRMED**; the adversarial scheduling is **PLAUSIBLE**
and was not exercised because the sandbox could not run live-tmux tests.

**Verification command:**

```bash
nl -ba src/ai_cli/stale_session_reaper.py | sed -n '303,315p;388,404p'
```

**Recommendation:** Replace `kill_session(session_id)` with a tmux-server-coordinated
compare-and-kill operation that validates the exact expected session ID, generation token, and
complete pane-ID/PID mapping in the same tmux command queue operation that performs the kill. Add a
deterministic test that mutates the pane mapping after the OS snapshot but before action and proves
zero kills. If tmux cannot provide that atomic contract, keep `reap` mode unavailable and revise the
design rather than accepting the race.

#### DV-2: Usable heartbeat records do not prove lifetime-lease ownership — `P0`

**Location:** `src/ai_cli/main.py:1325-1345`; `src/ai_cli/session_script.py:184-207`

**Evidence:**

```python
if len(argv) >= 4:
    generation_token = argv[3]
    marker = subprocess.run(
        ["tmux", "show-options", "-t", argv[1], "-v", "@ai_cli_session_generation"],
        ...
    )
    if marker.returncode == 0 and marker.stdout.strip() == generation_token:
        write_heartbeat(_config.get_xdg_state_home(), argv[1], generation_token)
```

The writer checks only the readable tmux token. It receives no owner identity or authenticated lease
capability and never verifies that the generation supervisor currently owns the lease. Meanwhile,
the supervisor sets the tmux marker before attempting the external `flock` acquisition
(`src/ai_cli/session_script.py:184-195`). Thus the internal command can create a schema-valid record
for a token-marked session even when no generation lease was ever acquired.

**Why it matters:** The design's safety proof says a usable record proves one uninterrupted lease
epoch. That is **CONFIRMED false** for the shipped writer. A token is readable from tmux; if the
internal writer is invoked without the lease and a process probe later produces the false-ended
observation this feature exists to survive, the reaper can acquire the free lease and kill the live
session. That destructive chain is **PLAUSIBLE**, but its missing authorization gate is concrete.

**Verification command:**

```bash
nl -ba src/ai_cli/main.py | sed -n '1325,1345p'
nl -ba src/ai_cli/session_script.py | sed -n '184,207p'
```

**Recommendation:** Redesign usable-record creation/update around a generation-owner capability that
the writer verifies, not merely a public tmux token. Add a negative subprocess regression that calls
the internal heartbeat command with a matching token but no held generation lease and proves no
ledger file is created. Also prove that an in-flight publisher cannot write after supervisor crash
or lease release.

#### DV-3: The signal-model gate approves an invalid asynchronous-shell topology — `P1`

**Location:** `src/ai_cli/session_script.py:144-197`, `:253-264`, `:279-305`;
`tests/test_stale_session_reaper.py:257-379`

**Evidence:** The gate checks only that job control is disabled and supervisor PGID equals terminal
foreground PGID. It then backgrounds the child (`"$_supervisor_script" --ai-cli-child-body &`), and
the child backgrounds the agent. No gate checks signal disposition or stdin behavior. Independent
no-write probes produced:

```text
SHELL /bin/bash ... POLL_AFTER_GROUP_INT None ... SUPERVISOR_INT ... (no CHILD_INT)
SHELL /bin/zsh  ... POLL_AFTER_GROUP_INT None ... SUPERVISOR_INT ... (no CHILD_INT)
/bin/bash RC 0 OUT READ_EOF
/bin/zsh  RC 0 OUT READ=hello
```

Both shells left the asynchronous child alive after process-group SIGINT; Bash also replaced its
stdin with EOF. The shipped tests fake `ps`, `tmux`, and `flock`, launch with `start_new_session=True`,
and send supervisor-directed signals by PID. They never generate terminal Ctrl-C/resize or test
interactive input.

**Why it matters:** T-1.4 requires evidence to be disabled unless the shared-terminal model is
verified. The shipped gate can set `_reaper_evidence_enabled=true` while the agent cannot receive
Ctrl-C, and on Bash cannot read the terminal. This is a user-visible control failure and invalidates
a load-bearing implementation gate.

**Verification command:**

```bash
nl -ba src/ai_cli/session_script.py | sed -n '144,197p;253,305p'
nl -ba tests/test_stale_session_reaper.py | sed -n '257,379p'
```

**Recommendation:** Do not background an interactive child through a non-interactive,
job-control-disabled shell unless real PTY/tmux tests establish stdin and signal semantics. Use a
topology with an actual foreground interactive child and a separate non-terminal monitor, or an
explicit PTY/process-group design. Gate evidence on a real startup self-test where feasible, and add
real Bash/zsh tmux tests for input, Ctrl-C, resize, direct INT/WINCH, and exactly-once TERM.

#### JA-1: The mandatory Phase 1 real-subprocess safety exit gate is largely absent — `P1`

**Location:** `docs/designs/stale-session-reaper.md:182-191`;
`tests/test_stale_session_reaper.py:94-379`

**Evidence:** The file defines 11 test functions. Its supervisor harness replaces `tmux`, `ai`,
`flock`, and `ps` with executable stubs (`tests/test_stale_session_reaper.py:257-307`). There is no
test for pane replacement, controlled PID reuse/identity mismatch, boot/clock/config errors,
candidate A killed then B throws, exact final action race, continuous real lease over multiple
updates, child/subshell FD-reference cardinality, supervisor crash release, local/remote normal and
fast exits, remote recovery heartbeat, real tmux terminal input, or terminal Ctrl-C/resize.

**Why it matters:** These were explicit implementation exit criteria, not optional future coverage.
The most safety-critical lock and terminal claims are mocked, so a green file would not prove them.

**Verification command:**

```bash
rg -n '^def test_' tests/test_stale_session_reaper.py
nl -ba tests/test_stale_session_reaper.py | sed -n '257,379p'
```

**Recommendation:** Implement the complete T-1.5 matrix as real subprocess/tmux tests, with controlled
adapters only where the design expressly allows them. Tests must use the real lock backend and prove
the child/heartbeat-subprocess descriptors are closed, one supervisor PID owns the lock across
updates, crash releases it, and every named mutation issues zero kills.

#### F-1: Remote normal child exit terminates the supervisor instead of restarting — `P1`

**Location:** `src/ai_cli/session_script.py:257-272`, `:743-820`

**Evidence:** Every non-fast, non-update child reaches unconditional `exit 0` at line 815. The
supervisor breaks for a remote template on every child status other than 78 at lines 266-269. Status
0 therefore exits the persistent supervisor. The condition cannot distinguish a completed remote
recovery shell from an ordinary remote agent child.

**Why it matters:** T-1.4 requires ordinary agent exits to spawn the next child while retaining the
same supervisor/lease. Remote sessions instead perform final cleanup and lose the session lifecycle.
The named regression suites contain only string-presence assertions for remote `exec $SHELL`, not a
normal remote exit subprocess test.

**Verification command:**

```bash
nl -ba src/ai_cli/session_script.py | sed -n '257,272p;743,820p'
```

**Recommendation:** Give remote recovery-shell completion a distinct status from ordinary child
completion. Loop on ordinary status 0 for both local and remote sessions; break only on explicit
local-final-exit or remote-recovery-complete status. Add real normal/fast remote subprocess tests and
assert supervisor PID, lease, and heartbeat continuity.

#### IC-1: The required shipped Design Overview remains an unfilled stub — `P2`

**Location:** `docs/designs/stale-session-reaper.md:60-62`

**Evidence:**

```text
## Design Overview

**Status:** stub — to be filled during/after implementation.
```

**Why it matters:** Checklist item 1 explicitly makes the shipped overview an implementation-audit
deliverable. Leaving it blank makes the design's Integration section the only informal shipped
overview, including claims this audit has now disproved.

**Verification command:**

```bash
sed -n '60,62p' docs/designs/stale-session-reaper.md
```

**Recommendation:** After the code fixes and re-audit, replace only the Design Overview stub with a
concise account of the verified shipped behavior and any deliberate fail-closed platform limits.

#### F-2: The supervisor depends on an undeclared external `flock` executable — `P2`

**Location:** `src/ai_cli/session_script.py:184-196`

**Evidence:** The generated wrapper enables evidence only when `command -v flock` succeeds, then
invokes the executable. On this supported macOS audit host, `command -v flock` produced no path for
either Bash or zsh. Portalocker is a Python dependency, but the supervisor does not use it.

**Why it matters:** The failure is conservative—no evidence and therefore no reap—but Phase 1 is
silently inert on a standard supported host. The design required lock-backend portability and real
supported-platform tests.

**Verification command:**

```bash
command -v bash
command -v zsh
command -v flock || true
```

**Recommendation:** Provide a package-controlled, cross-platform lock mechanism whose availability is
tested at install/startup, or explicitly narrow supported evidence platforms. Preserve the current
no-evidence behavior on missing backend and add a visible reason/status surface in Phase 2.

### R1 Resolution Pass

| Finding | Status | How resolved |
|---------|--------|--------------|
| DV-1 | UNRESOLVED — source/design fix required | No edits authorized; add a tmux-coordinated atomic compare-and-kill fence and race test. |
| DV-2 | UNRESOLVED — source/design fix required | No edits authorized; bind ledger writes to verified generation-lease ownership. |
| DV-3 | UNRESOLVED — source fix required | No edits authorized; replace or genuinely verify the interactive shell topology. |
| JA-1 | UNRESOLVED — tests required | No edits authorized; implement the complete real-subprocess T-1.5 exit gate. |
| F-1 | UNRESOLVED — source/test fix required | No edits authorized; distinguish normal remote child exit from recovery-shell completion. |
| IC-1 | UNRESOLVED — documentation fix required | No edit authorized outside this audit target. |
| F-2 | UNRESOLVED — portability fix required | No edits authorized; supply or scope the lock backend. |

### R1 Verification Matrix

| Finding/check | Command | Expected | Actual at `6668a46` | Pass? |
|---------------|---------|----------|------------------------|-------|
| Revision pin | `git rev-parse HEAD` | Exact requested commit | Printed `6668a46e0a7771d10772ee74434ca9a153b57ff2` | ✅ |
| DV-1 action gap | `nl -ba ...stale_session_reaper.py \| sed -n '303,315p;388,404p'` | Separate final snapshot and kill; lease is filesystem-only | Lines 303-315 show snapshot then separate kill; 388-404 show only portalocker | ✅ |
| DV-2 writer authority | `nl -ba src/ai_cli/main.py \| sed -n '1325,1345p'` | Token check but no lease-owner proof | Printed tmux option equality followed directly by `write_heartbeat()` | ✅ |
| DV-3 group SIGINT | Inline Python `start_new_session` probe over Bash and zsh asynchronous children | Child should record/exit on process-group SIGINT | Both supervisors recorded INT; neither child recorded INT; both remained live until TERM | ✅ finding reproduced |
| DV-3 stdin | Inline Python stdin probe over Bash/zsh asynchronous children | Background child retains interactive input | Bash printed `READ_EOF`; zsh read input | ✅ finding reproduced |
| JA-1 test inventory | `rg -n '^def test_' tests/test_stale_session_reaper.py` plus harness read | Required comprehensive matrix and real boundaries | 11 functions; harness stubs tmux/ai/flock/ps; required cases absent | ✅ |
| F-1 remote status flow | `nl -ba src/ai_cli/session_script.py \| sed -n '257,272p;743,820p'` | Ordinary status 0 loops for remote | Child exits 0; remote supervisor breaks for every status except 78 | ✅ |
| Focused pytest | `uv run pytest tests/test_stale_session_reaper.py -v` | Tests execute, both shell parameters not skipped | Exit 2 before collection: uv cache `Operation not permitted` | ⚠️ UNVERIFIED |
| Full pytest | `uv run pytest -q` | Only the two documented unrelated failures, no others | Exit 2 before collection: uv cache `Operation not permitted` | ⚠️ UNVERIFIED |
| Ruff lint fallback | `ruff check --no-cache src/ai_cli tests` | No lint findings | `All checks passed!` | ✅ |
| Ruff format fallback | `ruff format --no-cache --check src/ai_cli tests` | No formatting drift | `107 files already formatted` | ✅ |
| D-4 default | Design D-4 read plus `sed` of module/config defaults | Approved observe-first matches shipped defaults | Design says “`observe` default; explicit configuration for `reap`”; both code surfaces use `observe` | ✅ |

**Verified: 10/10 executable/static checks reproduced their stated result; 0/2 pytest runs reached
collection because of sandbox filesystem restrictions. No test result is claimed from those runs.**

<!-- /doc:region name="round_1_findings" -->

## Decisions Requiring Team Input

None. The findings require safety fixes and missing verification, not an unresolved product choice.
If a tmux-atomic compare-and-kill contract proves infeasible, that would require a new design decision
before `reap` mode can exist; this audit does not pre-select a weaker safety invariant.

## Outstanding Issues to Fix

| ID | Priority | Issue | Linked finding(s) | Owner | Target |
|----|----------|-------|-------------------|-------|--------|
| I-01 | P0 | Fence exact tmux pane mapping atomically with ID-targeted kill | DV-1 | Implementation/design owner | Before Phase 2 |
| I-02 | P0 | Make usable ledger writes prove the generation lease epoch | DV-2 | Implementation/design owner | Before Phase 2 |
| I-03 | P1 | Replace or genuinely verify shell terminal/signal topology | DV-3 | Implementation owner | Before Phase 2 |
| I-04 | P1 | Implement complete real-subprocess Phase 1 safety exit gate | JA-1 | Test/implementation owner | Before Phase 2 |
| I-05 | P1 | Restore remote normal-exit restart/lease parity | F-1 | Implementation owner | Before Phase 2 |
| I-06 | P2 | Fill shipped Design Overview after fixes are verified | IC-1 | Design owner | Re-audit/sign-off |
| I-07 | P2 | Resolve or explicitly scope external lock-backend portability | F-2 | Implementation owner | Before Phase 2 lifecycle rollout |

## Already-Correct Items

- ✅ Worktree and requested revision match exactly (`git rev-parse HEAD`).
- ✅ Default rollout mode matches D-4 exactly: `observe`, with explicit `reap` configuration
  (`docs/designs/stale-session-reaper.md:417-457`; `src/ai_cli/stale_session_reaper.py:29-33`;
  `src/ai_cli/config.py:200-206`).
- ✅ Invalid reaper configuration returns before any tmux operation
  (`src/ai_cli/stale_session_reaper.py:231-244`, `:275-277`).
- ✅ Tokenless sessions are ignored; the session-name regex is not used by the reaper classifier
  (`src/ai_cli/stale_session_reaper.py:93-115`).
- ✅ Local heartbeat replacement is complete-file atomic and generation-conditional
  (`src/ai_cli/stale_session_reaper.py:156-215`).
- ✅ Messaging failure cannot grant local reap authority, and local persistence failure does not
  suppress the existing best-effort messaging attempt (`src/ai_cli/main.py:1325-1353`).
- ✅ Current-boot monotonic time—not wall time—drives eligibility
  (`src/ai_cli/stale_session_reaper.py:145-153`, `:355-386`).
- ✅ UNKNOWN process identity and backend-specific mismatch preserve; identities are immutable typed
  values (`src/ai_cli/process_probe.py:116-127`, `:167-172`, `:272-282`, `:376-386`;
  `src/ai_cli/stale_session_reaper.py:331-353`, `:433-444`).
- ✅ Explicit evaluator kill is ID-targeted (`src/ai_cli/stale_session_reaper.py:117-119`, `:315`).
- ✅ Candidate evaluation exceptions do not authorize later work or roll back prior completed kills
  (`src/ai_cli/stale_session_reaper.py:269-290`).
- ✅ Launch-time `cleanup_stale_sessions()` remains bookkeeping-only
  (`src/ai_cli/session.py:520-562`).
- ✅ No launch/attach/resume path invokes `StaleSessionReaper.evaluate_once()`.
- ✅ Child-body and heartbeat-subshell code both attempt to close the inherited lease descriptor
  before their substantive work (`src/ai_cli/session_script.py:125-133`, `:199-205`).
- ✅ The single-gate negative controls are genuine evaluator calls with explicit zero-kill assertions
  (`tests/test_stale_session_reaper.py:113-145`).
- ✅ No prohibited identifier/proprietary literal was introduced in added lines of the Phase 1 diff;
  the added-line targeted scan returned 0. Pre-existing non-generic placeholders remain in touched
  legacy regression files and should be handled by a separately authorized hygiene pass rather than
  copied into this public audit.
- ✅ Ruff found no unused imports or dead-code lint violations in `src/ai_cli` or `tests`; the
  evaluator module is reasonably factored into adapters, immutable observations, ledger helpers,
  gates, and lease context management.

## Anti-Patterns to Watch For

- Treating a filesystem lease as a mutex for tmux server mutations. Non-cooperating tmux clients do
  not honor it.
- Treating “revalidated immediately before kill” as atomic compare-and-action. There is still an
  observation-to-kill interval.
- Treating a matching public generation token as proof that the caller owns the generation lease.
- Treating PGID equality as proof of stdin and signal disposition for asynchronous shell commands.
- Calling a subprocess test “real” while replacing the lock, tmux, terminal process-group query, and
  heartbeat command with stubs.
- Treating a passing static string assertion as lifecycle parity for normal/fast/crash transitions.
- Reporting a test pass when the runner failed before collection.

## Sign-Off Checklist

- [ ] All P0 findings have linked fixes
- [ ] All P1 findings fixed or explicitly deferred with approved rationale
- [ ] All P2 findings dispositioned
- [x] No AD-N decision is pending
- [x] Verification Matrix contains at least 10 actual checks/results
- [ ] Requested focused and full pytest suites executed successfully
- [ ] At least one append-only verification round completed after fixes
- [ ] Final re-grep/race verification completed
- [x] No inline source/design/test fixes were made
- [x] Already-Correct Items contain specific evidence
- [x] Anti-Patterns section reflects the implementation-audit misses
- [ ] User reviewed and approved sign-off

<!-- doc:region name="audit_log" kind="append_only" -->

## Audit Log

| Date | Action | Notes |
|------|--------|-------|
| 2026-08-28 | Round 1 implementation audit complete | Independent pass at `6668a46`; 2 P0, 3 P1, 2 P2; 16 checklist rows: 8 PASS, 4 PARTIAL, 2 FAIL, 2 N/A; pytest blocked before collection by sandbox; no non-audit edits. |

<!-- /doc:region name="audit_log" -->

## Appendix: Files Read

**Audit format:**

- Canonical audit template in the development harness — full read; taxonomy, required sections,
  verification matrix, anti-patterns, and AD-N skeleton checked before writing.
- `docs/audits/README.md` — audit lifecycle and severity definitions.

**Authoritative requirements and context:**

- `docs/designs/stale-session-reaper.md` — full read, including T-1.1 through T-1.5, all 16
  implementation-checklist rows, D-1 through D-10, Open Questions, and Approval Log.
- `docs/audits/stale-session-reaper-audit.md` — design-audit scope/status, Round 6 closure,
  verification matrix, sign-off, and audit-log context reviewed; no design-audit claim was used as
  implementation evidence.
- `AGENTS.md` — public-package, CLI, and test requirements.

**Primary implementation:**

- `src/ai_cli/stale_session_reaper.py` — full read.
- `src/ai_cli/process_probe.py` — full read.
- `src/ai_cli/session_script.py` — full read.
- `src/ai_cli/main.py` — implementation diff, heartbeat handlers, launch/attach/resume authority,
  tmux-kill sites, and complete symbol/call-site sweep.
- `src/ai_cli/config.py` — implementation diff and reaper/default configuration surfaces.
- `src/ai_cli/session.py:500-590` — launch-time cleanup authority and adjacent bookkeeping.
- `pyproject.toml`, `uv.lock` — Python/runtime lock dependencies and versions.

**Tests:**

- `tests/test_stale_session_reaper.py` — full read; every test/control and harness boundary.
- `tests/test_runaway_loop_guards.py` — full read.
- `tests/test_session_self_update.py` — full read.
- `tests/test_config_watch_hash.py` — full read.
- `tests/test_cli.py` — implementation diff, heartbeat dispatch, sandbox kill, generated-script,
  launch/attach, and stable-script sections; full symbol sweep.
- `tests/test_main.py` — implementation diff and generated-script monitoring/restart sections.
- `tests/test_session.py:283-331`, `:490-544`, `:1695-1715` — launch-cleanup negative controls and
  edge cases surfaced by symbol search.
- `tests/conftest.py:140-180` — tmux test cleanup context surfaced by the test runner.

**History:**

- Commit `8a25e04b9e182b46f2f23ab9b9c29c144aaafb43` — Phase 1 implementation diff and file inventory.
- Commit `6668a46e0a7771d10772ee74434ca9a153b57ff2` — requested main snapshot; only tracking metadata
  changed from the implementation commit.

## Appendix: Commands Run

```bash
git status --short
git rev-parse HEAD
git show -s --format='%H%n%P%n%ad%n%s' --date=iso-strict 6668a46e0a7771d10772ee74434ca9a153b57ff2
git show --stat --oneline --summary 6668a46e0a7771d10772ee74434ca9a153b57ff2
git log --oneline -- src/ai_cli/stale_session_reaper.py src/ai_cli/session_script.py tests/test_stale_session_reaper.py
git diff --stat 15b5459dddb3ba7cabf8f77f556353cbc7ba5225..6668a46e0a7771d10772ee74434ca9a153b57ff2 -- src/ai_cli tests docs/designs/stale-session-reaper.md
git diff --check 15b5459dddb3ba7cabf8f77f556353cbc7ba5225..8a25e04b9e182b46f2f23ab9b9c29c144aaafb43 -- src/ai_cli tests
rg -n 'stale_session_reaper|StaleSessionReaper|publish_heartbeat|revoke-heartbeat|cleanup_stale_sessions|kill-session|@ai_cli_session_generation|AI_CLI_SUPERVISOR_LEASE_FD|_reaper_evidence_enabled' src/ai_cli tests
rg -n '^def test_' tests/test_stale_session_reaper.py
uv run pytest tests/test_stale_session_reaper.py -v
uv run pytest -q
uv run ruff check src/ai_cli tests
uv run ruff format --check src/ai_cli tests
ruff check --no-cache src/ai_cli tests
ruff format --no-cache --check src/ai_cli tests
command -v bash
command -v zsh
command -v flock || true
# Two no-write inline Python subprocess probes:
# 1. start Bash/zsh supervisors in new sessions, background a trapped child, send SIGINT to PGID.
# 2. feed stdin to a job-control-disabled asynchronous child and report READ vs READ_EOF.
```

Requested `uv run` commands all failed at uv cache initialization with `Operation not permitted`.
A direct existing-environment pytest fallback also failed before collection because no permitted
temporary directory existed. Ruff initially hit the same cache policy; its documented `--no-cache`
mode then ran successfully and produced the results recorded above.

<!-- doc:region name="appendix_reviewer_prompt" kind="immutable" -->

## Appendix: Reviewer Prompt

Independent principal-engineer implementation audit of Phase 1 at the pinned commit, against the
converged design's 16 implementation checks and T-1.1 through T-1.5. Re-verify destructive safety,
lease/heartbeat/process identity, launch separation, real shell tests, regression quality,
portability, rollout default, and public-package hygiene from code and actual commands. Write only
this audit file; do not modify source, tests, design, or the closed design audit.

<!-- /doc:region name="appendix_reviewer_prompt" -->
