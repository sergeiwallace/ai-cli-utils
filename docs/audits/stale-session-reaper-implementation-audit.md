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
- [Round 2 — Current-HEAD P0 Re-verification](#round-2--current-head-p0-re-verification)
  - [R2 P0 Resolution](#r2-p0-resolution)
  - [R2 Verification Matrix](#r2-verification-matrix)
- [Round 3 — F-01 P1/P2 Backlog Disposition](#round-3--f-01-p1p2-backlog-disposition)
  - [R3 Summary](#r3-summary)
  - [R3 NEW issues surfaced](#r3-new-issues-surfaced)
  - [R3 Verification Matrix](#r3-verification-matrix)
  - [R3 Recommendations](#r3-recommendations)
  - [Status after Round 3](#status-after-round-3)
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

**Latest round:** Round 3 (F-01 P1/P2 backlog disposition complete)

**Outstanding by severity / verdict (across all rounds):**

| Severity | Count | Of which fixed | Of which deferred |
|----------|-------|----------------|-------------------|
| P0 | 2 | 2 | 0 |
| P1 | 5 | 0 | 0 |
| P2 | 3 | 1 | 0 |
| P3 | 0 | 0 | 0 |
| **Total** | **10** | **3** | **0** |

**Ship-readiness verdict:** **Not ready for the v0.8.0 release.** Round 3 closes only F-2. DV-3,
JA-1, and F-1 remain PARTIAL; IC-1 remains FAIL; and the pass surfaced two new P1 contradictions
plus one new P2 documentation-completion gap. DV-1 and DV-2 have no source/blob regression from
Round 2, but this worker could not repeat their live-tmux checks: the current permission profile
rejects both uv's cache and every temporary/socket location despite the invocation's contrary
execution-environment note. The exact failures are recorded in the R3 Verification Matrix.

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
    marker = subprocess.run(["tmux", "show-options", "-t", argv[1], "-v", "@ai_cli_session_generation"], ...)
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

## Round 2 — Current-HEAD P0 Re-verification

**Round 2 date:** 2026-09-02

**Target artifact:** commit `3b084f67178810736a3c6e12ab6ed00df73cd251`.

**Method:** The requested audit worker was invoked with the existing document as its write target,
but macOS denied its launcher-auth process enumeration before the worker started. The equivalent
direct audit below therefore reads the current implementation and executes the relevant tests in the
repository virtual environment. This is not a claim that the unavailable worker ran.

### R2 P0 Resolution

| Finding | Current result | Evidence |
|---------|----------------|----------|
| DV-1 final tmux action race | **RESOLVED** | `SubprocessTmuxAdapter.fence_and_kill()` uses one synchronous `tmux if-shell -F` command to compare the captured complete fingerprint and then kill only the opaque session ID (`src/ai_cli/stale_session_reaper.py:159-183`). The deterministic command-shape and evaluator tests pass. The two isolated-socket live-tmux fence tests are skipped because this environment rejects socket creation. |
| DV-2 unauthorised heartbeat persistence | **RESOLVED** | The supervisor starts the ticker only after it has acquired the generation lease (`src/ai_cli/session_script.py:207-217,313-320`); the handler requires the generation marker and an undecayed pane whose PID equals the supplied supervisor PID before calling `write_heartbeat()` (`src/ai_cli/main.py:1481-1534`). |

### R2 Verification Matrix

| Check | Command | Actual result |
|-------|---------|---------------|
| Target revision | `git rev-parse HEAD` | `3b084f67178810736a3c6e12ab6ed00df73cd251` |
| Atomic-fence and lease tests | `python -m pytest -n 0 --timeout=30` with the two real fence tests plus deterministic evaluator and lease tests | **2 passed, 2 skipped, 0 failed, 0 errors**; the skips are the real-tmux isolated-socket cases. |
| Supervisor/signal/remote lifecycle regressions | `python -m pytest -n 0 --timeout=30` with the candidate-isolation, signal, terminal, and remote-normal-exit tests | **9 passed, 0 skipped, 0 failed, 0 errors**. |
| Current source review | Read the adapter, supervisor, and heartbeat handler named above | Both previous P0 mechanisms are absent from the current control flow. |

**R2 conclusion:** The two P0 findings that made this document stale are fixed and tested at the
current target. The document intentionally remains `findings-pending-fix` because this bounded
re-verification does not replace a full independent disposition of the earlier P1/P2 findings.

## Round 3 — F-01 P1/P2 Backlog Disposition

**Round 3 auditor:** Codex audit (`cx audit`, effort: `xhigh`), independent verification pass

**Round 3 date:** 2026-09-02

**Round 3 target:** commit `0f3ee3d9f363d4f843d4c476dfe0a41c2622464d`

**Round 3 scope:** Re-verify the complete carried P1/P2 MUST-fix backlog—DV-3, JA-1, F-1,
IC-1, and F-2—against current source, tests, design, and history; confirm no DV-1/DV-2 blob
regression; and surface issues introduced or exposed by intervening changes. Verification only:
no source, test, or design edit was authorized.

### R3 Summary

The five carried items resolve to **1 PASS, 3 PARTIAL, and 1 FAIL**. F-2 is fixed: the generated
supervisor uses the declared Python `portalocker` dependency through an internal helper and no
longer invokes `flock(1)`. DV-3's foreground-child topology and F-1's status routing were changed
in the intended direction, but neither has the mandatory real-boundary matrix: the terminal test
uses a pipe and a shared process group, both real-PTY promotion tests explicitly skip Bash, and the
remote-normal-exit test disables the lease and never exercises fast recovery or heartbeat
continuity. JA-1 is therefore still materially incomplete. IC-1 is unchanged: the Design Overview
is still a one-line stub.

Three new findings were reproduced: **N-1 and N-2 are P1**, and **N-3 is P2**. N-1 is a direct
code/design contradiction introduced after the original topology fix: a second direct `SIGINT`
to the supervisor now calls its termination path even though T-1.4 says direct supervisor
`SIGINT` must be record-only and must not disrupt the child. N-2 records that an intervening change
deleted both the per-child signal/configuration monitoring and `tests/test_config_watch_hash.py`,
while T-1.4/T-1.5 still require that state and explicitly say the named suite must continue passing
instead of being deleted. N-3 captures the broader unremoved draft/feedback/checklist scaffolding
beyond IC-1's overview stub.

The invocation's execution-environment premise did not match this worker. Both exact requested
pytest commands exited 2 before collection with `Failed to initialize cache ... Operation not
permitted`; the existing-venv fallback failed before collection with `No usable temporary directory
found`; and the isolated tmux probe exited 1 with `no suitable socket path`. Consequently there are
no honest current-run pass/fail/skip counts to report. These are per-command **UNVERIFIED** results,
not test failures and not repetitions of the earlier round's claimed environment.

After the evidence pass, the shared worktree advanced from the requested target to descendant
`7e4c1d06cc50d034ec1e702e7604e2532f1a9ef0`. Its sole tracked change is
`tests/test_cli_dispatch.py`; a target-to-final-HEAD diff over every Round 3 source, test, design,
and dependency input exited 0. The dispositions below therefore remain pinned to the requested
`0f3ee3d9...` target, but this document does not mislabel the final worktree state as that commit.

| Backlog item | R3 verdict | Current quoted evidence | Verification note |
|--------------|------------|-------------------------|-------------------|
| DV-3 — signal-model topology | **PARTIAL — remains open (P1)** | The child wrapper now executes `os.setpgrp()` and the supervisor calls `os.tcsetpgrp(0, pgid)` (`src/ai_cli/session_script.py:242-267,303-343`). However, the nominal terminal regression supplies `stdin=subprocess.PIPE`, asserts `os.getpgid(child_pid) == process.pid`, and injects `os.killpg(...)` (`tests/test_stale_session_reaper.py:716-724,1030-1059`), so it does not exercise the separately grouped real terminal/tmux path. The only real-PTY promotion tests say `pytest.skip("bash closes the saved terminal descriptor for background children on macOS")` (`:1103-1158`), and no test generates terminal resize delivery to the child. The direct-supervisor signal contract has also drifted; see N-1. | **CONFIRMED** from source, complete test read, and history. Current execution is **UNVERIFIED** because pytest never collected and tmux could not create a socket. |
| JA-1 — mandatory T-1.5 real-subprocess gate | **PARTIAL — remains open (P1)** | The file now has 35 top-level test functions and adds real adapter fencing, candidate isolation, shell subprocess, and remote-status coverage (`tests/test_stale_session_reaper.py:155-1160`). It still has no controlled PID-reuse/identity-change evaluator test, multi-update continuous-lease test, supervisor-crash release/no-further-heartbeat test, remote fast-recovery lease/record/heartbeat test, real-tmux terminal Ctrl-C/resize matrix, or child-exits-during-TERM-relay case. The real tmux fixture is used only by cleanup/fence tests (`:109-250`); the remote test passes `lease_acquired=False` (`:1081-1100`); and the design-named `tests/test_config_watch_hash.py` is absent from `HEAD`. | **CONFIRMED** by full test inventory, repository-wide symbol search, and `git ls-tree`; see N-2. Runtime is **UNVERIFIED** with exact errors in the matrix. |
| F-1 — remote normal exit restarts | **PARTIAL — remains open (P1)** | The supervisor now breaks only for status 77/79, so status 0 loops (`src/ai_cli/session_script.py:340-353`), and the remote recovery shell returns 79 (`:815-860`). A real generated-supervisor subprocess test makes its first remote child return 0 and observes a second child (`tests/test_stale_session_reaper.py:1081-1100`). But that test explicitly uses `lease_acquired=False` and asserts neither unchanged supervisor PID nor lease/heartbeat continuity; there is no remote fast-recovery subprocess test. | **CONFIRMED** source correction and real status-routing boundary; the required full regression is incomplete and its current execution is **UNVERIFIED**. |
| IC-1 — Design Overview stub | **FAIL — remains open (P2)** | `docs/designs/stale-session-reaper.md:60-62` still reads `**Status:** stub — to be filled during/after implementation.` | **CONFIRMED** by direct read at target commit. |
| F-2 — undeclared external `flock` | **PASS — closed (P2)** | The supervisor opens the generation lease itself and invokes `ai internal acquire-generation-lease` (`src/ai_cli/session_script.py:206-217`). That handler duplicates the inherited descriptor and applies `portalocker.LOCK_EX \| portalocker.LOCK_NB` (`src/ai_cli/main.py:1462-1480`); `portalocker>=2.0` is declared (`pyproject.toml:34`); and the focused source/test sweep finds no executable `flock` call. Both installed shells inherited the dynamically allocated descriptor in a no-write probe (`inherited 10`, `inherited 11`). | **CONFIRMED** source/dependency/descriptor path. The pytest regression at `tests/test_stale_session_reaper.py:516-546` is present but its current execution is **UNVERIFIED** with the matrix's exact runner error. |

DV-1 and DV-2 remain closed for this round's bounded regression check. `git diff --exit-code
3b084f67178810736a3c6e12ab6ed00df73cd251..HEAD --` over the five implementation/test/design
inputs exited 0. Current source still uses the one-command tmux fingerprint compare-and-kill
(`src/ai_cli/stale_session_reaper.py:141-183,356-383`) and still requires a matching live
supervisor pane before persistence (`src/ai_cli/main.py:1499-1534`). Live-tmux non-regression is
**UNVERIFIED** in this worker because the isolated socket command failed exactly as recorded below.

### R3 NEW issues surfaced

#### N-1: Repeated direct supervisor SIGINT violates the record-only signal contract — `P1`

**Location:** `src/ai_cli/session_script.py:269-299`;
`docs/designs/stale-session-reaper.md:89,174-177`;
`tests/test_stale_session_reaper.py:791-844`

**Evidence:**

The design gives direct supervisor delivery one uniform behavior:

> "the supervisor's `SIGINT` and `SIGWINCH` traps have one uniform, observable behavior for direct
> delivery: record-only and never relay" (`docs/designs/stale-session-reaper.md:89`).

Current code makes the second direct `SIGINT` invoke the same child-terminating path as `SIGTERM`:

```bash
_supervisor_record_int() {
  if (( _supervisor_int_count == 1 && SECONDS <= _supervisor_int_deadline )); then
    _supervisor_term
    return
  fi
  ...
}
trap '_supervisor_record_int' INT
```

`_supervisor_term` sends `TERM` to the live child and exits (`src/ai_cli/session_script.py:269-278`).
The added regression confirms this is intentional current behavior: it sends `SIGINT` twice to
`process.pid` and asserts the child logged `TERM` and the supervisor exited
(`tests/test_stale_session_reaper.py:808-844`). History attributes the change to commit `d348f6e`.

**Why it matters:** T-1.4 expressly requires direct supervisor `SIGINT` to leave the child
undisturbed. The current gate can enable reap evidence for a supervisor that violates that approved
signal model, and any process delivering two direct interrupts within three seconds can terminate
a live child/session. This is a reachable code/design contradiction, so it is **CONFIRMED P1**.

**Verification command:**

```bash
nl -ba src/ai_cli/session_script.py | sed -n '269,300p'
nl -ba docs/designs/stale-session-reaper.md | sed -n '89p;174,177p'
nl -ba tests/test_stale_session_reaper.py | sed -n '791,844p'
git show --format= --unified=25 d348f6e -- src/ai_cli/session_script.py tests/test_stale_session_reaper.py
```

**Recommendation:** Restore record-only/no-relay behavior for every direct supervisor `SIGINT`.
Keep any double-Ctrl-C escape gesture entirely in the foreground child, where terminal-generated
input actually arrives, and add real tmux/PTY Bash and zsh tests proving terminal double-Ctrl-C,
direct supervisor `SIGINT`, and direct supervisor `SIGWINCH` remain distinguishable by topology.

#### N-2: T-1.5 requires monitoring behavior and a test suite that were deleted — `P1`

**Location:** `docs/designs/stale-session-reaper.md:83,178-191`;
`src/ai_cli/session_script.py:524-559`; deleted `tests/test_config_watch_hash.py`

**Evidence:**

T-1.5 says generated-script regressions must cover per-child signal/configuration monitoring and
that `tests/test_config_watch_hash.py` "shall be updated for the supervisor/child structure and
shall continue passing rather than being deleted" (`docs/designs/stale-session-reaper.md:189`).
The Phase 1 exit gate repeats monitoring reset/rearm as mandatory (`:191`). Current
`start_watcher()` contains only the one-second loop plus Gemini reload/restart handling
(`src/ai_cli/session_script.py:524-559`); the signal-file startup grace, idle-prompt detection,
configuration hashing, and configuration-change state are absent. `git ls-tree -r --name-only HEAD
tests` returns no `tests/test_config_watch_hash.py`, and commit `10bae49` records that file as
`deleted file mode 100644` alongside removal of the production behavior.

**Why it matters:** The Phase 1 exit gate cannot pass as written, and a future reviewer can either
restore deliberately retired behavior or accept its deletion while incorrectly claiming design
compliance. That live specification/implementation contradiction has two materially different
readings and is therefore **CONFIRMED P1**.

**Verification command:**

```bash
nl -ba docs/designs/stale-session-reaper.md | sed -n '83p;178,191p'
nl -ba src/ai_cli/session_script.py | sed -n '524,559p'
git ls-tree -r --name-only HEAD tests | rg 'test_config_watch_hash.py' || true
git show --stat --oneline 10bae49 -- src/ai_cli/session_script.py tests/test_config_watch_hash.py
```

**Recommendation:** Treat the committed behavior removal as authoritative: delete the obsolete
monitoring-state claims and replace the named-suite preservation AC with the current explicit
no-auto-injection behavior and its actual tests. Record the dropped parity items and rationale
explicitly before JA-1 can pass.

#### N-3: The shipped design still contains broad draft-author scaffolding — `P2`

**Location:** `docs/designs/stale-session-reaper.md:5,13,60-62,111-112,222-246,734-750`

**Evidence:** The design remains `status: draft` / `**Status:** DRAFT`, retains multiple
`<enter feedback here>` placeholders, leaves 13 of 16 Implementation Audit rows unchecked, and
retains `**Audit completed:** <!-- YYYY-MM-DD ... -->`. This is broader than IC-1's one-line Design
Overview stub and persists after Phase 1 and Phase 2 implementation text was added.

**Why it matters:** The document does not distinguish approved historical decisions from unfinished
authoring prompts or show which implementation claims have actually passed audit. That is a
documentation/sign-off defect, not a demonstrated destructive runtime path, so it is
**CONFIRMED P2**.

**Verification command:**

```bash
nl -ba docs/designs/stale-session-reaper.md | sed -n '1,15p;60,62p;111,112p;222,246p;734,750p'
rg -n '<enter feedback here>|Audit completed|\- \[[ x]\]' docs/designs/stale-session-reaper.md
```

**Recommendation:** After the P1 source/spec/test contradictions are resolved, fill the Design
Overview, remove unfilled feedback prompts, set an accurate lifecycle status, and update each
Implementation Audit row with evidence from the final verification round rather than bulk-checking
the table.

### R3 Verification Matrix

| Finding/check | Command | Expected | Actual at `0f3ee3d9` | Reproduced? |
|---------------|---------|----------|----------------------|-------------|
| Target revision at evidence-pass start | `git rev-parse HEAD` | Exact requested commit | Printed `0f3ee3d9f363d4f843d4c476dfe0a41c2622464d` | ✅ |
| Final shared-worktree drift | `git show -s HEAD`; target-to-HEAD scoped diff | Report any post-pass movement and in-scope impact | Final HEAD `7e4c1d06...` is a direct child; only `tests/test_cli_dispatch.py` changed; scoped diff exited 0 | ✅ no audited-input drift |
| Round 2 P0 blob regression | `git diff --exit-code 3b084f6..HEAD -- <five inputs>` | No source/test/design drift since R2 | Exit 0, no diff | ✅ |
| DV-1 static fence | `nl -ba ...stale_session_reaper.py \| sed -n '141,183p;356,383p'` | One validated tmux compare-and-kill command under final evaluation | Fingerprint capture and one `if-shell -F` queue command remain | ✅ |
| DV-2 static publisher guard | `nl -ba src/ai_cli/main.py \| sed -n '1499,1534p'` | Marker plus matching live supervisor pane before write | Both checks still precede `write_heartbeat()` | ✅ |
| DV-3 topology/test boundary | Source/test extracts at `session_script.py:242-343` and test lines 1030-1158 | Separate real terminal group verified for Bash and zsh | Source has promotion; pipe test shares PGID; real-PTY tests skip Bash; no child-resize test | ✅ PARTIAL reproduced |
| JA-1 inventory | AST test inventory plus repository-wide AC-term search | Complete T-1.5 matrix | 35 top-level functions; named crash/PID-reuse/remote-recovery/real-tmux-terminal cases absent; one required suite deleted | ✅ PARTIAL reproduced |
| F-1 flow | `nl -ba ...session_script.py \| sed -n '340,353p;815,860p'` plus test `:1081-1100` | Status 0 restarts; 79 ends recovery; real continuity assertions | Routing fixed and second child observed; lease is explicitly disabled and no fast/continuity case exists | ✅ PARTIAL reproduced |
| IC-1 | `sed -n '60,62p' docs/designs/stale-session-reaper.md` | Filled overview | Printed `Status: stub — to be filled during/after implementation.` | ✅ FAIL reproduced |
| F-2 | Portalocker source/dependency sweep plus Bash/zsh inherited-FD probe | No executable `flock(1)` dependency; inherited descriptor reaches helper | No executable call; `portalocker>=2.0`; shells printed `inherited 10` / `inherited 11` | ✅ PASS |
| N-1 | Direct-signal source/design/test/history extracts | Direct supervisor INT remains record-only | Second INT calls `_supervisor_term`; test expects child `TERM` | ✅ finding reproduced |
| N-2 | `git ls-tree` plus design/source/history extracts | Required suite exists and monitoring contract matches code | No path in `HEAD`; history shows deletion; source behavior absent while AC remains | ✅ finding reproduced |
| N-3 | Draft/scaffolding grep | Shipped design has no unfinished author prompts | DRAFT, feedback placeholders, unchecked audit rows, and completion placeholder remain | ✅ finding reproduced |
| Focused pytest | `uv run pytest tests/test_stale_session_reaper.py -v` | Real pass/fail/skip counts | Exit 2 before collection: `Failed to initialize cache ... Operation not permitted (os error 1)` | ❌ UNVERIFIED |
| Existing-venv fallback | `.venv/bin/python -m pytest ... -n 0 --timeout=45` | Bypass uv and execute focused suite | Exit 1 before collection: `FileNotFoundError: ... No usable temporary directory found` | ❌ UNVERIFIED |
| Full pytest | `uv run pytest -q` | Informational real pass/fail/skip counts | Exit 2 before collection with the same uv-cache `Operation not permitted` error | ❌ UNVERIFIED |
| Live tmux capability | `tmux -L codex-r3-0f3ee3 new-session -d -s probe 'sleep 30'` | Exit 0 | Exit 1: `no suitable socket path` | ❌ UNVERIFIED |

**Verified:** 13/13 static/history/no-write shell checks reproduced their stated outcomes at the
target commit. **0/3 requested runtime gates executed** (focused pytest, full pytest, live tmux);
the direct-venv fallback also failed before collection. No pass/fail/skip count is fabricated.

### R3 Runtime Verification Addendum (supplied by Claude, not Codex)

The `cx audit --write-target` worker above ran inside its own sandbox (macOS Seatbelt via `cx`'s
`sandbox-adapter`), which denied uv-cache access, offered no usable temporary directory, and could
not create a tmux socket — hence its 4 UNVERIFIED runtime rows. The operating Claude Code session's
own direct shell on this same Mac does not have those restrictions (confirmed independently before
Round 3 was launched: `tmux -L <isolated-socket> new-session -d ...` exits 0). This addendum
supplies the missing runtime evidence at the current shared-worktree HEAD (`7e4c1d06`, a direct,
audited-input-identical descendant of the Round 3 target `0f3ee3d9` per the drift check above).

| Check | Command | Actual result |
|-------|---------|----------------|
| Live tmux capability | `tmux -L <isolated-socket> new-session -d -s probe 'sleep 3'` | Exit 0 |
| Focused pytest | `uv run pytest tests/test_stale_session_reaper.py -v --timeout=45` | **46 passed, 2 skipped, 3 failed** in 51.32s |

The 3 failures:

- `test_given_clean_child_exit_when_supervisor_finishes_then_tmux_session_is_removed` — times out
  (>45s). This is the already-tracked, already-pre-existing `AI-CLI-wyit` hang (root cause not yet
  investigated; unrelated to this round's findings).
- `test_given_agent_exits_on_first_ctrl_c_when_child_restarts_then_second_ctrl_c_exits_session[bash]`
  and `[zsh]` — **both fail** with `Failed: supervisor child did not become ready`, confirmed at
  clean `origin/main` HEAD with no uncommitted source/test changes. Filed as **`AI-CLI-uyev`** (P1)
  with a hypothesis that this is the concrete, currently-failing manifestation of this round's **N-1**
  finding (a second direct supervisor `SIGINT` now invokes the child-terminating path instead of
  staying record-only) — not yet root-caused to that specific line, but the symptom (the double-Ctrl-C
  escape test itself failing) is consistent with N-1's description of the exact same code path.

This addendum does not change any R3 backlog-item verdict above (DV-3/JA-1/F-1 remain PARTIAL, IC-1
remains FAIL, F-2 remains PASS) — it supplies runtime evidence the verdicts were already reached
without, and it adds one new piece of evidence (`AI-CLI-uyev`) supporting N-1's real-world impact.
The **live-tmux non-regression check for DV-1/DV-2** (Round 2's fixes) is now also runtime-confirmed:
none of the 3 failures above are DV-1/DV-2-shaped (both remain closed).

### R3 Recommendations

**MUST be fixed before the v0.8.0 release:**

- DV-3 / N-1: restore the approved direct-supervisor signal semantics and add a real tmux/PTY
  Bash-and-zsh input/Ctrl-C/resize/direct-signal matrix.
- JA-1 / N-2: reconcile the deleted monitoring behavior and suite with T-1.4/T-1.5, then implement
  the still-missing PID-reuse, update-cycle lease, crash-release, remote-recovery, and zero-kill
  cases at their real boundaries.
- F-1: retain the corrected status routing, but add real local/remote normal and fast paths that
  assert one supervisor PID, continuous lease ownership, record retention/revocation, and heartbeat
  continuity through remote recovery.
- IC-1 / N-3: finish and status the shipped design using verified behavior, not placeholder text.
- Re-run `uv run pytest tests/test_stale_session_reaper.py -v` and `uv run pytest -q` in a worker
  that actually permits the uv cache, writable temporary directories, subprocess enumeration, and
  isolated tmux sockets; record real pass/fail/skip counts.

**SHOULD be fixed before final audit sign-off:**

- Add a dedicated release gate that runs the real-tmux subset serially, so ordinary xdist behavior
  cannot hide or cross-contaminate socket/process lifecycle failures.
- Update the Design Implementation Audit rows only after each row has a cited passing check.

**Can be folded into a follow-up:**

- None of the open Round 3 items. All are carried MUST-fix backlog, direct contradictions in its
  acceptance contract, or the evidence required to close those items.

### Status after Round 3

The audit remains **findings-pending-fix** and the v0.8.0 release is **not ready** on this evidence.
Across all rounds, 3 of 10 findings are fixed: both P0 findings and F-2. Outstanding are **5 P1**
(DV-3, JA-1, F-1, N-1, N-2) and **2 P2** (IC-1, N-3), with none deferred. The next verification
must carry all seven open items and must supply current real pytest/tmux results; source/blob
identity and prior-round results are not substitutes for that runtime gate.

## Decisions Requiring Team Input

None. The findings require safety fixes and missing verification, not an unresolved product choice.
If a tmux-atomic compare-and-kill contract proves infeasible, that would require a new design decision
before `reap` mode can exist; this audit does not pre-select a weaker safety invariant.

## Outstanding Issues to Fix

| ID | Priority | Issue | Linked finding(s) | Owner | Target |
|----|----------|-------|-------------------|-------|--------|
| I-01 | RESOLVED | Fence exact tmux pane mapping atomically with ID-targeted kill | DV-1 | — | Resolved in R2 |
| I-02 | RESOLVED | Make usable ledger writes prove the generation lease epoch | DV-2 | — | Resolved in R2 |
| I-03 | P1 | Correct and genuinely verify shell terminal/signal topology | DV-3, N-1 | Implementation owner | Before v0.8.0 release |
| I-04 | P1 | Complete the real-subprocess Phase 1 safety gate and reconcile the deleted monitoring contract | JA-1, N-2 | Test/implementation owner | Before v0.8.0 release |
| I-05 | P1 | Verify remote normal/fast-exit restart, lease, and heartbeat parity | F-1 | Implementation owner | Before v0.8.0 release |
| I-06 | P2 | Fill and status the shipped design after fixes are verified | IC-1, N-3 | Design owner | Re-audit/sign-off |
| I-07 | RESOLVED | Replace external `flock(1)` with the declared Python lock backend | F-2 | — | Resolved in R3 |

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
- Changing signal semantics after design approval without updating the governing acceptance criteria.
- Treating deletion of a design-mandated behavior and its named test as satisfaction of that gate.
- Reporting a test pass when the runner failed before collection.

## Sign-Off Checklist

- [x] All P0 findings have linked fixes
- [ ] All P1 findings fixed or explicitly deferred with approved rationale
- [ ] All P2 findings dispositioned
- [x] No AD-N decision is pending
- [x] Verification Matrix contains at least 10 actual checks/results
- [ ] Requested focused and full pytest suites executed successfully
- [x] At least one append-only verification round completed after fixes
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
| 2026-09-02 | Round 2 current-HEAD P0 re-verification | Commit `3b084f6`; DV-1/DV-2 resolved by direct source and test review after the requested audit worker could not start under macOS process-enumeration restrictions. |
| 2026-09-02 | Round 3 F-01 P1/P2 backlog disposition | Commit `0f3ee3d9f363d4f843d4c476dfe0a41c2622464d`; 1 PASS, 3 PARTIAL, 1 FAIL across the carried backlog; 2 new P1 and 1 new P2; final shared-worktree HEAD advanced to an audited-input-identical descendant; Auditor: Codex audit (`cx audit`, effort: `xhigh`). |

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

**Round 3 verification inputs:**

- Canonical audit template in the development harness — full 982-line read before task action;
  finding taxonomy, append-only round structure, verification matrix, anti-patterns, and exact AD-N
  skeleton checked.
- `docs/audits/stale-session-reaper-implementation-audit.md` — full 646-line pre-R3 history read;
  complete MUST-fix backlog independently reconstructed.
- `src/ai_cli/stale_session_reaper.py` — full current 571-line read; evaluator and P0 fence
  non-regression checked.
- `src/ai_cli/session_script.py` — full current 861-line read; supervisor, child, ticker, signal,
  remote-exit, and lock-helper call paths checked.
- `src/ai_cli/main.py:1440-1550` — internal lease, publish-heartbeat, and revoke-heartbeat handlers;
  symbol/call-site sweep expanded to the worker entry point.
- `tests/test_stale_session_reaper.py` — full current 1,188-line read; all 35 top-level test
  functions and their real-versus-controlled boundaries inventoried.
- `docs/designs/stale-session-reaper.md` — full current 774-line read; overview, T-1.1 through
  T-1.5, D-1 through D-10, Implementation Audit, feedback scaffolding, and Approval Log checked.
- `pyproject.toml:30-111`, `uv.lock:895-900` — declared lock/test dependencies and configured
  pytest execution mode checked.
- `tests/test_runaway_loop_guards.py`, `tests/test_cli.py`, `tests/test_main.py`,
  `tests/test_session_self_update.py` — targeted test-name/symbol searches for the T-1.5 matrix;
  not treated as full-file reads in this bounded round.
- History for commits `268970b`, `d348f6e`, `10bae49`, `15eed6a`, `a30650d`, and `8a5f40f` —
  relevant signal/topology/test/design changes and the Round 2 audit update checked without relying
  on commit messages as implementation evidence.

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

**Round 3 commands:**

```bash
git status --short --branch
git rev-parse HEAD
git show -s --format='%H%n%cd%n%s' --date=iso-strict HEAD
git diff --exit-code 3b084f67178810736a3c6e12ab6ed00df73cd251..HEAD -- \
  src/ai_cli/stale_session_reaper.py src/ai_cli/session_script.py src/ai_cli/main.py \
  tests/test_stale_session_reaper.py docs/designs/stale-session-reaper.md
git log --oneline -- <Round 3 implementation/test/design inputs>
git show --format= --unified=25 d348f6e -- \
  src/ai_cli/session_script.py tests/test_stale_session_reaper.py
git show --stat --oneline 10bae49 -- \
  src/ai_cli/session_script.py tests/test_config_watch_hash.py
git ls-tree -r --name-only HEAD tests | rg 'test_config_watch_hash.py' || true
rg -n '^def test_|^    def test_' tests/test_stale_session_reaper.py
rg -n -i 'pane.*replac|pid reuse|identity.*mismatch|supervisor.*crash|continuous.*lease|remote.*recovery|SIGWINCH' tests src/ai_cli docs/designs/stale-session-reaper.md
rg -n '\bflock\b|command -v flock' \
  src/ai_cli/session_script.py src/ai_cli/main.py tests/test_stale_session_reaper.py
python3 -c '<AST-parse the four current Python inputs and inventory test functions>'
command -v uv; uv --version; command -v tmux; tmux -V
command -v bash; bash --version; command -v zsh; zsh --version; command -v flock || true
# No-write Bash and zsh probes: dynamically allocated descriptor inherited by a Python child.
# No-write `script -q /dev/null` Bash and zsh probes: saved fd 9 remains a terminal descriptor.
uv run pytest tests/test_stale_session_reaper.py -v
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider \
  tests/test_stale_session_reaper.py -v -n 0 --timeout=45
uv run pytest -q
tmux -L codex-r3-0f3ee3 new-session -d -s probe 'sleep 30'
markdownlint docs/audits/stale-session-reaper-implementation-audit.md
aido validate-doc docs/audits/stale-session-reaper-implementation-audit.md
git diff --check
git show -s --format='%H%n%P%n%cd%n%s' --date=iso-strict HEAD
git diff --name-status 0f3ee3d9f363d4f843d4c476dfe0a41c2622464d..HEAD
git diff --exit-code 0f3ee3d9f363d4f843d4c476dfe0a41c2622464d..HEAD -- \
  src/ai_cli/stale_session_reaper.py src/ai_cli/session_script.py src/ai_cli/main.py \
  tests/test_stale_session_reaper.py docs/designs/stale-session-reaper.md pyproject.toml uv.lock
```

The two exact `uv run` commands exited 2 before collection because the configured cache path could
not be opened (`Operation not permitted`). The existing-venv fallback exited 1 before collection
because Python found no usable temporary directory among its standard candidates. The isolated
tmux command exited 1 with `no suitable socket path`. These current Round 3 errors supersede the
invocation's execution-environment premise for this worker only; they are not product test results.
Markdown lint and canonical document validation both exited 0; `git diff --check` reported no
whitespace errors. The final revision check found that the shared worktree had advanced to direct
child `7e4c1d06...`, whose only tracked delta was `tests/test_cli_dispatch.py`; the scoped
target-to-HEAD audit-input diff exited 0.

<!-- doc:region name="appendix_reviewer_prompt" kind="immutable" -->

## Appendix: Reviewer Prompt

Independent principal-engineer implementation audit of Phase 1 at the pinned commit, against the
converged design's 16 implementation checks and T-1.1 through T-1.5. Re-verify destructive safety,
lease/heartbeat/process identity, launch separation, real shell tests, regression quality,
portability, rollout default, and public-package hygiene from code and actual commands. Write only
this audit file; do not modify source, tests, design, or the closed design audit.

<!-- /doc:region name="appendix_reviewer_prompt" -->
