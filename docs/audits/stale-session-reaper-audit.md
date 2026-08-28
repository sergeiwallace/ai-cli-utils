---
title: Stale-Session Reaper Design Doc — Audit
category: audit
tags: [audit, session-management, reliability]
status: findings-open
date: 2026-08-28
source: "cx-audit"
template_version: "audit-1.0.0"
delegation_provenance:
  version: 2
  contributors: []
task: AI-CLI-tdm6
---

# Stale-Session Reaper Design Doc — Audit

**Status:** findings open — design revision required before implementation

**Created:** 2026-08-28

**Auditor:** Codex `audit` role, effort `high`; the concrete runtime model ID was not exposed to this worker (the invocation scaffold records `gpt-5.6-terra` routing). List per-round auditor in the Audit Log.

**Target artifact:** `docs/designs/stale-session-reaper.md` at commit `f60d4df` (Round 1 baseline),
re-verified through commit `035a90b` (Round 4)

<!-- doc:region name="scope" kind="replaceable" -->

## Table of Contents

- [What Was Audited](#what-was-audited)
- [Scope](#scope)
  - [In scope](#in-scope)
  - [Out of scope](#out-of-scope)
- [Methodology](#methodology)
- [Status Summary](#status-summary)
- [Round 1 — Main Audit](#round-1--main-audit)
  - [R1 Summary](#r1-summary)
  - [R1 Findings](#r1-findings)
  - [Detailed Failed Checks (ordered by severity)](#detailed-failed-checks-ordered-by-severity)
  - [R1 Resolution Pass](#r1-resolution-pass)
  - [R1 Verification Matrix](#r1-verification-matrix)
- [Round 2 — Verification Pass](#round-2--verification-pass-append-only)
  - [R2 Summary](#r2-summary)
  - [R2.1 Round 1 IC/JA/DV verification](#r21-round-1-icjadv-verification)
  - [R2.2 Round 1 F-N verification](#r22-round-1-f-n-verification)
  - [R2.3 AD-N decisions verification](#r23-ad-n-decisions-verification)
  - [R2.4 NEW issues surfaced](#r24-new-issues-surfaced)
  - [R2.5 Verification Matrix](#r25-verification-matrix)
  - [R2 Recommendations](#r2-recommendations)
- [Round 3 — Resolution Verification](#round-3--resolution-verification-append-only)
  - [R3 Summary](#r3-summary)
  - [R3.1 Full backlog verification](#r31-full-backlog-verification)
  - [R3.2 NEW issues surfaced](#r32-new-issues-surfaced)
  - [R3.3 Verification Matrix](#r33-verification-matrix)
  - [R3 Recommendations](#r3-recommendations)
  - [Status after Round 3](#status-after-round-3)
- [Round 4 — Post-D-10 Verification](#round-4--post-d-10-verification-append-only)
  - [R4 Summary](#r4-summary)
  - [R4.1 Full backlog verification](#r41-full-backlog-verification)
  - [R4.2 Required new-surface stress tests](#r42-required-new-surface-stress-tests)
  - [R4.3 NEW issues surfaced](#r43-new-issues-surfaced)
  - [R4.4 Verification Matrix](#r44-verification-matrix)
  - [R4 Recommendations](#r4-recommendations)
  - [Status after Round 4](#status-after-round-4)
- [Decisions Requiring Team Input](#decisions-requiring-team-input)
  - [AD-1: Bind evidence to a session instance](#ad-1)
  - [AD-2: Close the final heartbeat/process TOCTOU](#ad-2)
  - [AD-3: Choose a clock safe for destructive staleness decisions](#ad-3)
- [Outstanding Issues to Fix](#outstanding-issues-to-fix)
- [Already-Correct Items](#already-correct-items)
- [Anti-Patterns to Watch For](#anti-patterns-to-watch-for)
- [Sign-Off Checklist](#sign-off-checklist)
- [Audit Log](#audit-log)
- [Appendix: Files Read](#appendix-files-read)
- [Appendix: Commands Run](#appendix-commands-run)
- [Appendix: Reviewer Prompts](#appendix-reviewer-prompts)
  - [Round 1 Reviewer Prompt](#round-1-reviewer-prompt)
  - [Round 2 Reviewer Prompt (Re-audit)](#round-2-reviewer-prompt-re-audit)

## What Was Audited

`docs/designs/stale-session-reaper.md` at commit `f60d4df` — a new design for an out-of-band,
Circus-managed stale-tmux-session reaper. Triggered by AI-CLI-tdm6, a follow-up to AI-CLI-iy53 (the
4th occurrence of one session launch killing another session's live tmux pane), authored by Codex
(`cx implement --effort medium`, model `gpt-5.6-terra`) via the fleet's `/auto-flow` pipeline.

## Scope

### In scope

- `docs/designs/stale-session-reaper.md` — full document: Executive Summary, Problem Statement,
  Stale-Session Reaper section (Safety invariant, Periodic execution/lifecycle, Candidate
  evaluation/reap protocol, Heartbeat recording), Data Model (Configuration, Heartbeat ledger),
  Integration, Implementation Phases 1-2 with their EARS ACs, Risks and Mitigations, all 6 Design
  Decisions (D-1 through D-6) and their `decision-record` provenance comments, Open Questions.
- Conformance against `~/projects/ai-harness/docs/designs/TEMPLATE.md` (structure, region markers,
  decision-record fixed-key format, feedback-blockquote placeholders).
- Conformance against the 5 hard design requirements recorded in AI-CLI-tdm6's bd description
  (out-of-band, dual-signal corroboration, fail-closed, observe-first rollout, regression coverage).
- Cross-consistency against the existing, already-shipped code it proposes to extend:
  `src/ai_cli/session.py::cleanup_stale_sessions()` (post AI-CLI-iy53's `ed17415` fix),
  `src/ai_cli/session_script.py` (heartbeat publish call, wrapper lifecycle),
  `src/ai_cli/process_probe.py`, `src/ai_cli/process_manager.py`, `src/ai_cli/messaging.py`
  (`publish_heartbeat`), `src/ai_cli/main.py` (`internal publish-heartbeat` dispatch, `internal
  signal-watch` as the existing Circus-watcher pattern to mirror).
- The public-package constraint in this repo's own `CLAUDE.md` (no `ai-core`/`aido`/private names
  or infra referenced anywhere) as it applies to the design's explicit choice to reuse
  ai-cli-utils' own `circus` dependency rather than ai-core's private Circus deployment.

### Out of scope

- Implementation itself (Phase 1/2 code) — not yet written; this is a pre-implementation design
  audit only. Implementation-time verification is `impl-audit`, a separate later stage.
- Re-litigating the AI-CLI-iy53 fix already shipped (`ed17415`) — audit only whether this design
  correctly builds on top of it without regressing its invariant (D-6).
- The unconfirmed root-cause candidate for the original false-positive (wrapper self-reload) —
  the design doc itself already frames this as an open, non-blocking question; audit only whether
  the design's safety invariant correctly does not depend on that root cause being confirmed.

## Methodology

**Approach:** Round 1 Codex (`audit`, effort `high`) full validation pass across Internal
Consistency (IC-N), Spec/AC Compliance against AI-CLI-tdm6's 5 hard requirements (JA-N), Domain
Validity of the tmux/process-probe/Circus design choices (DV-N), and open-scope Independent
Findings (F-N). Findings requiring team input become AD-N. `--resolve-decisions` was NOT granted
for this run (no Run Ledger `authority` record) — every AD-N is surfaced, not resolved.

## Status Summary

**Latest round:** Round 4 (complete)

**Outstanding by severity / verdict (across all rounds):**

| Severity | Count | Of which fixed | Of which deferred |
|----------|-------|----------------|-------------------|
| CRITICAL / P0 | 4 | 4 | 0 |
| MAJOR / P1    | 14 | 11 | 0 |
| MINOR / P2    | 4 | 3 | 0 |
| Cosmetic / P3 | 0 | 0 | 0 |
| **Total**     | **22** | **18** | **0** |

**Ship-readiness verdict:** **Not ready for implementation.** Round 4 verifies all 25 pre-existing
checks as PASS, including JA-2, DV-1, AD-2, and N-1/N-4/N-5/N-6: D-10 removes the separate-holder
topology and makes the pane-leader supervisor the one lifetime lease owner. Three new MAJOR/P1
findings block implementation: N-7 leaves signal-origin-dependent trap behavior undefined, N-8
misstates remote fast-exit behavior, and N-9 omits the shipped watcher's per-attempt reset/rearm
state and its existing regression suites. N-10 is a non-blocking MINOR/P2 stale-terminology
contradiction. Eighteen of 22 historical/new findings are fixed; four Round 4 findings remain open.

## Round 1 — Main Audit

**Round 1 auditor:** Codex `audit` role, effort `high`; concrete runtime model ID not exposed

**Round 1 date:** 2026-08-28

**Round 1 scope:** Full pre-implementation audit of the design at `f60d4df`, the five hard
requirements, the shipped launch-cleanup incident fix, every named source symbol/call site, the
existing Circus patterns, and the relevant tests and public documentation.

### R1 Summary

Twelve open findings: 2 CRITICAL, 7 MAJOR, and 3 MINOR. The design gets the broad architecture
right—out-of-band execution, conjunctive signals, observe-first rollout, exact-ID revalidation,
and launch-path separation—but the destructive predicate does not yet prove that the heartbeat
belongs to the tmux instance being killed, and its final read/kill sequence is not synchronized
with the heartbeat publisher. Those are protocol defects, not implementation polish.

No inline fixes were made. The permission profile allowed writes only to this audit document, and
the three protocol choices are not unambiguous typo/cross-reference fixes.

### R1 Findings

#### Internal Consistency (IC-N)

| ID | Verdict | Severity | Evidence |
|----|---------|----------|----------|
| IC-1 | FAIL | CRITICAL / P0 | The invariant says “for the same tmux session instance” (`stale-session-reaper.md:64`), but the ledger is keyed and validated only by session name (`:109-115`) and is removed only after a successful reaper kill (`:83`). |
| IC-2 | FAIL | MINOR / P2 | D-1/D-2/D-4/D-5/D-6 justify choices using “Criteria 1/2/3” (`:245`, `:291`, `:383`, `:429`, `:475`), but the document defines no Criteria section or mapping. |

#### Spec / AC Compliance (JA-N)

| ID | Verdict | Severity | Evidence |
|----|---------|----------|----------|
| JA-1 | PASS | — | Requirement 1: “Never synchronous with another session's launch — must run out-of-band (periodic Circus watcher), never as a side effect of cleanup_stale_sessions() or any ai c/ai p/ai g/ai cx launch.” Lines 73-75 and ACs at 151-153 prohibit launch registration/evaluation and synchronous fallback. |
| JA-2 | PARTIAL | MAJOR / P1 | Requirement 2: “Corroborated, not single-signal — a session must fail BOTH (a) process-probe-confirmed pane leader ended/zombie AND (b) no heartbeat published in N minutes before being reaped.” Requirement 3: “Fail closed on any ambiguity — an unreadable PID, missing heartbeat record, or uncertain read must preserve the session, never reap it.” The prose/ACs require two signals, but IC-1, DV-1, and DV-2 show that the heartbeat can belong to an old instance or change after the last read, and PID identity is not stable across the final action. |
| JA-3 | PASS | — | Requirement 4: “Configurable, off by default until proven safe — observe-only mode logging what WOULD be reaped, before ever actually killing, OR an explicit opt-in; must not ship with reap as the first deployed behavior with zero track record.” Lines 98-105 and 154-157 default to `observe`, reject invalid config, and require explicit `reap`. |
| JA-4 | FAIL | MAJOR / P1 | Requirement 5: “Robust regression coverage — tests proving the corroboration actually gates correctly (single-signal-only cases must NOT reap; both-signals-confirmed-dead cases must reap), not just a mock of one path.” The exit gate requires non-mocked ledger round trips but explicitly permits “mocked tmux/process boundaries” (`:142`); no AC requires one integrated both-signals-through-revalidation kill test. |
| JA-5 | FAIL | MAJOR / P1 | All written Phase 1/2 ACs use an EARS keyword, but the public `stop` and `status` commands and the long-running `run` entry point named at lines 71-73 have no behavior or failure-path AC. Only `start` failure is specified (`:153`). |
| JA-6 | FAIL | MAJOR / P1 | Integration promises “Existing event publication is unchanged” (`:119`), while T-1.1 only orders a successful ledger write before publication and says a write failure creates no usable record (`:133-134`). It never requires the current NATS attempt and exit-zero behavior (`main.py:1325-1330`) to survive a local write failure. |

#### Domain Validity (DV-N)

| ID | Verdict | Severity | Evidence |
|----|---------|----------|----------|
| DV-1 | FAIL | CRITICAL / P0 | The final protocol performs a second stale-heartbeat read and then kills (`:81`) with no fence, claim, grace interval, or publisher coordination. A fresh heartbeat can replace the file between those operations. |
| DV-2 | PARTIAL | MAJOR / P1 | The tmux snapshot contains session ID, name, and PID but not pane ID (`:79`). `ProcessProbe.has_ended()` is a point-in-time Boolean (`process_probe.py:164-172`); the design does not compare pane identity or process creation identity across passes, leaving PID reuse/respawn between final probe and kill unaddressed. |
| DV-3 | FAIL | MAJOR / P1 | The ledger stores Unix wall time and compares it to the “current clock” (`:112-115`). A forward wall-clock correction makes a fresh record appear old; only future timestamps (backward movement) are rejected. No boot/monotonic identity is specified. |
| DV-4 | PASS | — | Exact tmux session-ID targeting and a second full gate pass (`:79-83`, `:139-141`) are materially safer than the removed name-targeted, single-snapshot loop. A Circus restart loses only the current evaluation; after a kill, an undeleted record has no candidate and is ignored. |
| DV-5 | PASS | — | Existing process probes return `None` for unreadable state and `has_ended()` returns false when the PID remains present (`process_probe.py:135-140`, `:164-172`, `:319-327`); that specific unreadable-state case can fail closed as designed. |

#### Independent Findings (F-N)

| ID | Verdict | Severity | Evidence |
|----|---------|----------|----------|
| F-1 | FAIL | MAJOR / P1 | “managed panes” is not defined (`:79`). The shipped code has two non-equivalent classifiers: metadata presence (`main.py:1087-1114`) and `_AI_SESSION_RE`, which excludes local/remote `cx` and hyphenated/custom names that `build_session_name()` creates (`session.py:430-431`, `:653-717`). Rename behavior is also unspecified. |
| F-2 | FAIL | MINOR / P2 | Phase 2 lists `README.md` but has no documentation AC. README still advertises automatic cleanup and a launch-cleanup staleness timeout (`README.md:34`, `:367-368`), while CHANGELOG says cleanup still reaps verified dead sessions (`CHANGELOG.md:89-92`) even though `ed17415` removed that authority. |
| F-3 | WARN | MINOR / P2 | D-1 calls the ledger “durable” (`:212`), but the algorithm only writes, flushes, and atomically replaces (`:89`); it specifies neither file `fsync` nor parent-directory synchronization. Atomic visibility is not crash durability. |

### Detailed Failed Checks (ordered by severity)

#### IC-1: A name-keyed heartbeat cannot satisfy a same-instance invariant — `CRITICAL` / `P0`

**Location:** `docs/designs/stale-session-reaper.md:64-67`, `:81-83`, `:109-115`; `src/ai_cli/session_script.py:237-244`, `:447`, `:651-653`

**Evidence:**

> “it may terminate only after both of these conditions hold for the same tmux session instance”

> “Records live ... at `session-heartbeats/<encoded-session-name>.json`”

> `{"version": 1, "session_name": "c-session-1", "recorded_at": 1787860800}`

> “After a successful kill, the worker may remove its ledger record ... Records without a current candidate are ignored.”

The shipped EXIT trap removes metadata and watcher files but contains no heartbeat-ledger cleanup;
the STOP path only publishes events. A clean close therefore leaves a valid old name record. A
new tmux instance with the same name can be evaluated before its first heartbeat replaces that
record; the new opaque session ID remains stable through both revalidation passes, so ID targeting
does not reject the old evidence.

**Why it matters:** This is a direct false-positive authorization path against a newly recreated,
live session—the exact bug class the design is intended to eliminate.

**Verification note:** **CONFIRMED** from the complete ledger schema/protocol and the shipped
wrapper cleanup path; no implementation inference is needed.

**Verification command:**

```bash
sed -n '64,68p;79,83p;107,115p' docs/designs/stale-session-reaper.md
sed -n '237,244p;447p;651,653p' src/ai_cli/session_script.py
```

**Recommendation:** Resolve AD-1. Bind every heartbeat and managed-session marker to an
unforgeable per-session generation token, require exact token/name/session-ID agreement at both
passes, and define cleanup as generation-conditional so an old worker can never delete a newer
instance's record. Clean-exit deletion is useful housekeeping but is not a substitute for binding.

#### DV-1: The final heartbeat read and kill are an unfenced TOCTOU — `CRITICAL` / `P0`

**Location:** `docs/designs/stale-session-reaper.md:79-89`, `:139-141`

**Evidence:**

> “the worker immediately re-queries the captured exact session ID and repeats every gate. Only then does it call `tmux kill-session`”

> “then atomically replaces the final file”

No lock, fencing token, reap claim, cancellation handshake, or observation grace appears in the
protocol. Atomic replace guarantees a reader sees a complete old or new record; it does not stop
the writer from publishing a fresh record after the second read and before the kill.

**Why it matters:** At the actual kill time, the hard requirement “no heartbeat published in N
minutes before being reaped” can already be false. A live wrapper recovering from a stall can be
killed despite having just published.

**Verification note:** **PLAUSIBLE**, with a concrete permitted interleaving; implementation does
not exist, so runtime frequency cannot be measured.

**Verification command:**

```bash
sed -n '79,89p;133,142p' docs/designs/stale-session-reaper.md
rg -n 'lock|fenc|quarantine|claim|grace' docs/designs/stale-session-reaper.md || true
```

**Recommendation:** Resolve AD-2. Add a generation-bound lifetime lease held continuously by the
heartbeat/wrapper process, then require the reaper to obtain an exclusive non-blocking lease before
the final full gate pass and hold it through `kill-session`. Any lock ambiguity preserves.

#### JA-2: The written dual-signal/fail-closed requirements are only partially met — `MAJOR` / `P1`

**Location:** authoritative requirement 2/3 in the Round 1 Reviewer Prompt; `docs/designs/stale-session-reaper.md:64-69`, `:136-141`

**Evidence:**

> “Corroborated, not single-signal — a session must fail BOTH (a) process-probe-confirmed pane leader ended/zombie AND (b) no heartbeat published in N minutes before being reaped.”

> “Fail closed on any ambiguity — an unreadable PID, missing heartbeat record, or uncertain read must preserve the session, never reap it.”

The ACs reproduce the two Boolean gates, but IC-1 shows a schema-valid record need not be for the
same instance, DV-1 shows it can cease to be stale before the action, and DV-2 shows process/pane
identity is not carried through the final action.

**Why it matters:** Checking two values is not corroboration if neither is bound to the entity and
instant at which destructive authority is exercised.

**Verification note:** **CONFIRMED** as a compliance gap, with runtime likelihood of the races
unverified pre-implementation.

**Verification command:**

```bash
sed -n '64,69p;77,89p;133,142p' docs/designs/stale-session-reaper.md
```

**Recommendation:** Amend the invariant and every affected AC after AD-1/AD-2: evidence must match
one generation; the final snapshot must remain unchanged while the reaper holds the generation's
exclusive lease; ambiguity in generation, pane identity, process identity, heartbeat revision, or
lease state preserves.

#### JA-4: Regression coverage can pass without an integrated corroborated reap — `MAJOR` / `P1`

**Location:** hard requirement 5; `docs/designs/stale-session-reaper.md:135-142`, `:154-161`

**Evidence:**

> “Robust regression coverage — tests proving the corroboration actually gates correctly (single-signal-only cases must NOT reap; both-signals-confirmed-dead cases must reap), not just a mock of one path.”

> “Focused non-mocked ledger round trips and mocked tmux/process boundaries prove every criterion”

T-1.2 may test eligibility, T-1.3 may separately test a mocked kill, and T-2.3 tests only the two
negative single-signal cases. No criterion forces one test through real ledger persistence,
candidate enumeration, both passes, and the final kill boundary.

**Why it matters:** A wiring bug between individually passing units could bypass or invert a gate
while all required tests remain green.

**Verification note:** **CONFIRMED** by exhaustive reading of all Phase 1/2 ACs.

**Verification command:**

```bash
sed -n '135,142p;154,161p' docs/designs/stale-session-reaper.md
rg -n 'both.signals|end.to.end|non.mocked|mocked tmux|mocked.*process' docs/designs/stale-session-reaper.md || true
```

**Recommendation:** Add an EARS AC requiring an integrated test with a real temporary ledger and
controlled tmux/process adapters: each single-signal case produces zero kill calls; a
generation-matched, both-signals-dead candidate surviving initial, grace, and revalidation produces
exactly one ID-targeted kill. Add a mutation/negative control proving removal of either gate fails.

#### JA-5: Public lifecycle commands lack required behavior/failure ACs — `MAJOR` / `P1`

**Location:** `docs/designs/stale-session-reaper.md:71-75`, `:144-161`; `task-authoring-standards.md` failure-path rule

**Evidence:**

> “The package will provide a session-reaper management command group with `start`, `stop`, `status`, and hidden/internal `run` commands”

> “If Circus is unavailable or registration fails, then the start command shall report failure”

There is no success or failure AC for `stop`/`status`, and no AC for an evaluator exception,
shutdown, or Circus restart in `run`.

**Why it matters:** Implementations may silently swallow stop failures, misreport a dead watcher as
healthy, or crash-loop on one malformed candidate while satisfying every current AC.

**Verification note:** **CONFIRMED**; all Phase ACs were enumerated and all use EARS syntax, but the
required public-surface failure coverage is incomplete.

**Verification command:**

```bash
sed -n '71,75p;144,161p' docs/designs/stale-session-reaper.md
rg -n 'When .*stop|If .*stop|status.*system shall|run.*system shall' docs/designs/stale-session-reaper.md || true
```

**Recommendation:** Inventory `start`, `stop`, `status`, and `run` as public/operational surfaces.
Add success and failure EARS ACs for stop/status, and failure ACs requiring one bad candidate or
iteration exception to preserve all sessions, emit a reason, and let Circus restart without any
half-completed kill authority.

#### JA-6: “Existing event publication is unchanged” has no parity AC — `MAJOR` / `P1`

**Location:** `docs/designs/stale-session-reaper.md:85-89`, `:117-120`, `:132-134`; `src/ai_cli/main.py:1312-1330`

**Evidence:**

> “then retain its current best-effort messaging publication”

> “Existing event publication is unchanged.”

> “If JSON is invalid or the record cannot be written, then the system shall create no usable record and shall not terminate a tmux session.”

The current valid-JSON handler always reaches the best-effort NATS call and exits zero even when
publication raises. The failure AC does not say a ledger write failure must still attempt that call
or remain non-fatal.

**Why it matters:** A natural early return on local write failure would silently break the existing
heartbeat stream and make diagnosis harder precisely when local storage is failing.

**Verification note:** **CONFIRMED** as an untested parity claim against shipped code.

**Verification command:**

```bash
sed -n '85,89p;117,120p;132,135p' docs/designs/stale-session-reaper.md
sed -n '1312,1330p' src/ai_cli/main.py
```

**Recommendation:** Add: “If local ledger persistence fails after valid JSON is accepted, then the
system shall report the local failure, still attempt the existing best-effort message publication,
and exit non-fatally without creating reap authority.” Add a parity test for call order and result.

#### DV-2: PID values are re-read, but pane/process identity is not revalidated — `MAJOR` / `P1`

**Location:** `docs/designs/stale-session-reaper.md:64-83`, `:127-142`; `src/ai_cli/process_probe.py:130-172`, `:229-247`, `:316-338`

**Evidence:**

> “using a delimiter-safe format containing `#{session_id}`, `#{session_name}`, and `#{pane_pid}`”

> “the process probe confirms every PID has ended or is a zombie”

`ProcessProbe` already exposes `start_time_match()`, but the design records no process birth token
and does not query `#{pane_id}`. `has_ended()` is only `not self.is_present(pid) or
self.state(pid) in self.ended_states`.

**Why it matters:** Pane replacement or PID reuse after the final probe can turn an “ended” number
into a live leader while the same tmux session ID remains. Two snapshots reduce the window but do
not close it.

**Verification note:** **PLAUSIBLE** TOCTOU, grounded in the exact shipped interface; no runtime
reproduction was possible because Phase 1 code does not exist.

**Verification command:**

```bash
sed -n '64,69p;77,83p;127,142p' docs/designs/stale-session-reaper.md
sed -n '130,172p;229,247p;316,338p' src/ai_cli/process_probe.py
```

**Recommendation:** Include `#{pane_id}` and compare the complete pane-ID→PID mapping across the
initial/exclusive-lease/final phases. Extend the evaluator/probe contract to return explicit
`LIVE/GONE/ZOMBIE/UNKNOWN` plus process creation identity where observable; any pane replacement,
identity mismatch, or unavailable identity cancels the claim. Cover PID reuse and pane respawn.

#### DV-3: Unix wall time can turn a fresh heartbeat stale — `MAJOR` / `P1`

**Location:** `docs/designs/stale-session-reaper.md:107-115`, `:181-186`

**Evidence:**

> `{"version": 1, "session_name": "c-session-1", "recorded_at": 1787860800}`

> “a finite timestamp not later than its current clock”

There is no monotonic/boot identifier or clock-discontinuity rule. A backward correction produces
a future record and safely preserves; a forward correction produces a large positive age and is
accepted as stale.

**Why it matters:** NTP/manual clock correction or resume behavior can manufacture the second kill
signal without ten minutes of missed heartbeats.

**Verification note:** **PLAUSIBLE** platform/time interleaving; the omission is confirmed, but
host clock behavior was not reproduced in this read-only pre-implementation audit.

**Verification command:**

```bash
sed -n '107,115p;181,186p' docs/designs/stale-session-reaper.md
rg -n 'monotonic|boot.?id|boot.?time|clock.?jump|clock.?discontin' docs/designs/stale-session-reaper.md || true
```

**Recommendation:** Resolve AD-3. Prefer same-boot monotonic age with a boot-generation marker;
cross-boot or unavailable clock identity preserves. Keep wall time only for operator logs.

#### F-1: “Managed panes” has no authoritative classifier — `MAJOR` / `P1`

**Location:** `docs/designs/stale-session-reaper.md:77-80`; `src/ai_cli/main.py:1087-1114`; `src/ai_cli/session.py:430-431`, `:653-717`

**Evidence:**

> “Each interval queries tmux for managed panes”

> “A session is ‘ai-cli-managed’ iff it has a `session-meta-*.json`.” (`main.py:1091-1093`)

> `_AI_SESSION_RE = re.compile(r"^[cgp](-r)?-[a-zA-Z0-9]+-\d+$")`

The regex excludes valid local/remote `cx` and hyphenated/custom names created by
`build_session_name()`. The design does not choose metadata, naming, or a tmux marker, and does not
specify rename handling.

**Why it matters:** The reaper may permanently miss supported session classes or interpret a
manual rename differently from the heartbeat publisher, undermining eventual cleanup and making
observe logs incomplete.

**Verification note:** **CONFIRMED** specification gap against two shipped, non-equivalent notions
of “managed.” It does not assert that the future implementation would necessarily reuse the regex.

**Verification command:**

```bash
sed -n '77,80p' docs/designs/stale-session-reaper.md
sed -n '430,432p;653,717p' src/ai_cli/session.py
sed -n '1087,1114p' src/ai_cli/main.py
```

**Recommendation:** Fold this into AD-1: set an explicit tmux session option containing the
generation token at creation and define that option—not a name regex—as the authoritative managed
marker. Add ACs for `c/g/p/cx`, local/remote, indexed/custom/hyphenated names, rename, clean close,
crash, and foreign tmux sessions.

#### IC-2: Decision rationales cite undefined criteria — `MINOR` / `P2`

**Location:** `docs/designs/stale-session-reaper.md:245`, `:291`, `:337`, `:383`, `:429`, `:475`

**Evidence:**

> “Criteria 1 and 2 favor the robust host-local safety boundary.”

> “Criterion 2 favors an explicit lifecycle separation.”

No Criteria heading or numbered quality-attribute mapping exists. The only numbered nearby items
are the two safety gates and the risk/decision tables, none of which fit all references.

**Why it matters:** Reviewers cannot reconstruct which requirement justified each decision, and
the cross-references can silently drift.

**Verification note:** **CONFIRMED** by a full heading/reference search.

**Verification command:**

```bash
rg -n '^#{2,6} .*Criteria|\b[Cc]riteria? [123]\b' docs/designs/stale-session-reaper.md || true
```

**Recommendation:** Replace every numbered “Criteria” reference with the explicit hard requirement
or quality attribute it means (for example, “fail-closed requirement” and “out-of-band lifecycle
requirement”). No inline fix was made because only the audit doc is writable.

#### F-2: Phase 2 does not require correction of stale public behavior claims — `MINOR` / `P2`

**Location:** `docs/designs/stale-session-reaper.md:146-161`; `README.md:34`, `:367-368`; `CHANGELOG.md:89-92`

**Evidence:**

> “Stale session cleanup | Automatic detection and cleanup of orphaned sessions”

> `stale_session_timeout = 15     # minutes before cleanup considers a session stale`

> “session cleanup no longer kills unrelated live sessions while still reaping verified dead ones”

At `f60d4df`, `cleanup_stale_sessions()` contains no `kill-session`; the design lists README as a
deliverable but adds no falsifiable documentation criterion.

**Why it matters:** Users cannot tell that reaping is currently disabled, later observe-only by
default, independently started, and configured by a new table.

**Verification note:** **CONFIRMED** against source at `session.py:520-562` and public docs.

**Verification command:**

```bash
sed -n '25,38p;365,370p' README.md
sed -n '89,92p' CHANGELOG.md
sed -n '520,562p' src/ai_cli/session.py
```

**Recommendation:** Add a Phase 2 EARS documentation AC requiring README/config examples to name
the independent start command, observe default, explicit reap opt-in, and the limited meaning of
legacy `stale_session_timeout`; add an Unreleased CHANGELOG correction rather than rewriting
release history silently.

#### F-3: Atomic visibility is specified, crash durability is not — `MINOR` / `P2`

**Location:** `docs/designs/stale-session-reaper.md:85-89`, `:107-115`, `:209-217`

**Evidence:**

> “writes and flushes a complete temporary file in that directory, then atomically replaces the final file”

> “Same-host durable evidence independent of messaging availability and retention.”

No `fsync`/`fdatasync` or parent-directory synchronization appears.

**Why it matters:** After process/host crash, a recent replace may be lost while an older valid,
stale record survives. That degrades the intended independent signal and compounds IC-1.

**Verification note:** **PLAUSIBLE** crash outcome; the absence of a durability protocol is
confirmed, while filesystem-specific loss behavior is not reproduced here.

**Verification command:**

```bash
sed -n '85,90p;107,115p;209,217p' docs/designs/stale-session-reaper.md
rg -n 'fsync|fdatasync|sync.*director|director.*sync' docs/designs/stale-session-reaper.md || true
```

**Recommendation:** Either narrow “durable” to “atomically visible” or specify and test flush +
file sync before replace and parent-directory sync where supported. If a platform cannot establish
durability, treat the record as unavailable for reap authority and document the portability rule.

### R1 Resolution Pass

| Finding | Status | How resolved |
|---------|--------|--------------|
| IC-1 | TEAM INPUT NEEDED | No target edit; session-instance binding moved to AD-1. |
| IC-2 | OPEN — target-doc fix required | Replace undefined criterion labels with explicit requirements. No inline fix; only this audit file was writable. |
| JA-2 | TEAM INPUT NEEDED | Depends on AD-1/AD-2 plus the DV-2 identity contract. |
| JA-4 | OPEN — target-doc fix required | Add integrated corroboration/revalidation/kill and mutation-negative-control ACs. |
| JA-5 | OPEN — target-doc fix required | Add lifecycle inventory and stop/status/run failure ACs. |
| JA-6 | OPEN — target-doc fix required | Add best-effort publication parity failure AC. |
| DV-1 | TEAM INPUT NEEDED | Final TOCTOU protocol moved to AD-2. |
| DV-2 | OPEN — target-doc fix required | Specify pane/process identity and reuse/respawn ACs after AD-2. |
| DV-3 | TEAM INPUT NEEDED | Clock choice moved to AD-3. |
| F-1 | TEAM INPUT NEEDED | Authoritative marker/generation design folded into AD-1. |
| F-2 | OPEN — target-doc fix required | Add public-documentation AC and Unreleased correction. |
| F-3 | OPEN — target-doc fix required | Specify durability or narrow the claim. |

### R1 Verification Matrix

| Finding | Command | Expected | Actual (rerun at `f60d4df`) | Pass? |
|---------|---------|----------|-------------------------------------|-------|
| IC-1 | `sed -n '64,68p;79,83p;107,115p' ...; sed -n '237,244p;447p;651,653p' ...` | Same-instance claim, name-only record, kill-only deletion, no EXIT cleanup | Printed all four design facts and the wrapper heartbeat/EXIT/STOP lines; EXIT trap has no `session-heartbeats` removal. | ✅ |
| IC-2 | `rg -n '^#{2,6} .*Criteria|\b[Cc]riteria? [123]\b' ...` | Uses but no defining heading | Printed references at 245, 291, 383, 429, 475; printed no Criteria heading. | ✅ |
| JA-4 | `sed -n '135,142p;154,161p' ...; rg -n 'both.signals|end.to.end|non.mocked|mocked tmux|mocked.*process' ...` | Negative cases plus mocked boundary, no integrated positive AC | Printed T-1.2/T-1.3/T-2.3; only match was line 142, “non-mocked ledger round trips and mocked tmux/process boundaries.” | ✅ |
| JA-5 | `sed -n '71,75p;144,161p' ...; rg -n 'When .*stop|If .*stop|status.*system shall|run.*system shall' ...` | Four commands, only start failure | Printed `start/stop/status/run` and Phase 2 ACs; negative search produced no stop/status/run AC. | ✅ |
| JA-6 | `sed -n '85,89p;117,120p;132,135p' ...; sed -n '1312,1330p' src/ai_cli/main.py` | Unchanged claim without failure parity; current call/exit | Printed the claim/AC and current suppressed NATS call followed by `sys.exit(0)`. | ✅ |
| DV-1 | `sed -n '79,89p;133,142p' ...; rg -n 'lock|fenc|quarantine|claim|grace' ...` | Read→kill sequence and no coordination term | Printed immediate revalidation/read→kill and atomic replace; negative search produced no output. | ✅ |
| DV-2 | `sed -n '64,69p;77,83p;127,142p' ...; sed -n '130,172p;229,247p;316,338p' src/ai_cli/process_probe.py` | No pane ID/birth token; Boolean probe | Printed only session ID/name/PID and `has_ended()` Boolean; probe identity API exists but is absent from the design. | ✅ |
| DV-3 | `sed -n '107,115p' ...; rg -n 'monotonic|boot.?id|boot.?time|clock.?jump|clock.?discontin' ...` | Unix/current clock, no discontinuity mechanism | Printed Unix timestamp/current-clock rule; negative search produced no output. | ✅ |
| F-1 | `sed -n '77,80p' ...; sed -n '430,432p;653,717p' src/ai_cli/session.py; sed -n '1087,1114p' src/ai_cli/main.py` | Undefined “managed”; classifiers differ | Printed the undefined phrase, restrictive regex, valid `cx`/hyphenated naming, and metadata-based managed definition. | ✅ |
| F-2 | `sed -n '25,38p;365,370p' README.md; sed -n '89,92p' CHANGELOG.md` | Stale public claims | Printed “Automatic detection,” legacy timeout text, and “still reaping verified dead ones.” | ✅ |

**Verified: 10/10 sampled findings reproduce on commit `f60d4df9e061643cc44b73f7a1227af5e8348945`.**

## Round 2 — Verification Pass (append-only)

**Round 2 auditor:** Codex (`GPT-5`), independent verification invocation

**Round 2 date:** 2026-08-28

**Round 2 scope:** Verified the complete 12-item Round 1 MUST-fix backlog and AD-1 through AD-3
against `docs/designs/stale-session-reaper.md` at exact commit
`383b0559fb59bdfd0f92413e835dcbccf572bbd8`. The worktree copy matches that commit. Re-read the
full design and inspected the full shipped `session_script.py`, `process_probe.py`, and
`process_manager.py` paths named by the verification prompt. No target-design or source edit was
made; this round writes only this audit history.

### R2 Summary

The complete Round 1 backlog contains the 12 items listed in the invocation; no older open
MUST-fix item was omitted. Verdicts are 8 PASS, 4 PARTIAL, and 0 FAIL. AD-1 and AD-3 PASS; AD-2 is
PARTIAL. Three new issues were reproduced: N-1 (CRITICAL) leaves lease continuity unspecified over
first evidence and the shipped wrapper's self-`exec` paths; N-2 (MAJOR) requires a pane-process
identity that the existing probe cannot capture through its public contract; N-3 (MAJOR) promises
whole-interval rollback while the protocol authorizes per-candidate kills.

### R2.1 Round 1 IC/JA/DV verification

| ID | Verdict | Evidence |
|----|---------|----------|
| IC-1 | **PASS** | Round 1 required session-instance binding (`docs/audits/stale-session-reaper-audit.md:581`). The revised invariant makes `@ai_cli_session_generation` the sole marker and requires token agreement (`docs/designs/stale-session-reaper.md:67-74`); the protocol and cleanup are generation-conditional (`:86-96`); the schema includes the token (`:116-122`); Integration and T-1.1 apply it to creation, naming variants, clean exit, and crash (`:124-126`, `:139-145`). **Verification: CONFIRMED.** |
| IC-2 | **PASS** | Round 1 required replacing undefined numbered criteria (`docs/audits/stale-session-reaper-audit.md:582`). D-1 through D-6 now name the fail-closed, exact-ID, out-of-band, observe-first, eventual-recovery, and launch-separation requirements (`docs/designs/stale-session-reaper.md:280`, `:326`, `:372`, `:418`, `:464`, `:510`); a full numbered-Criteria search returned no matches. **Verification: CONFIRMED.** |
| JA-2 | **PARTIAL** | The dual gates and token binding are explicit (`docs/designs/stale-session-reaper.md:69-74`), and the final pass is lease-held (`:80`, `:86-88`, `:147-154`). The claimed fail-closed result is not complete because continuous lease ownership is not specified across first evidence or either shipped self-`exec` path; see N-1. This leaves the Round 1 dependency row (`docs/audits/stale-session-reaper-audit.md:583`) only partly closed. **Verification: CONFIRMED specification coverage; PLAUSIBLE permitted race.** |
| JA-4 | **PASS** | T-1.4 requires one integrated real-temporary-ledger/token/lease round trip through controlled tmux/process adapters, exactly one ID-targeted positive kill, zero kills for mutation/negative controls, and pane/PID/token/rename negatives; the Phase 1 exit gate repeats that requirement (`docs/designs/stale-session-reaper.md:155-159`). This matches the Round 1 requested integrated positive and gate-removal controls (`docs/audits/stale-session-reaper-audit.md:584`). **Verification: CONFIRMED.** |
| JA-5 | **PARTIAL** | Start, stop, status, and run success/failure ACs are present (`docs/designs/stale-session-reaper.md:167-176`). However, the exception AC requires an entire interval to abort “before any kill” and preserve all sessions (`:174`), while the protocol immediately kills each eligible candidate (`:88`) and defines no whole-pass preflight/commit boundary. See N-3. The Round 1 lifecycle row is at `docs/audits/stale-session-reaper-audit.md:585`. **Verification: CONFIRMED contradiction.** |
| JA-6 | **PASS** | Heartbeat prose says a local write failure remains non-fatal, still attempts `publish_heartbeat`, and creates no reap authority (`docs/designs/stale-session-reaper.md:96`); T-1.1 repeats the parity AC verbatim (`:145`), and Integration says the failure does not suppress publication (`:126`). This closes `docs/audits/stale-session-reaper-audit.md:586`. **Verification: CONFIRMED.** |
| DV-1 | **PARTIAL** | D-8 adds exclusive non-blocking acquisition before the final pass and holds the lease through `tmux kill-session` (`docs/designs/stale-session-reaper.md:80`, `:88`, `:153-154`, `:563-604`). That closes the final read-to-kill edge only while continuous wrapper ownership is real; N-1 shows startup and self-`exec` continuity are unspecified. The original TOCTOU row is `docs/audits/stale-session-reaper-audit.md:587`. **Verification: CONFIRMED text; PLAUSIBLE remaining interleaving.** |
| DV-2 | **PARTIAL** | The revision captures `#{pane_id}`, the complete pane-ID-to-PID mapping, and requires both-pass pane/process identity equality (`docs/designs/stale-session-reaper.md:86`, `:149`, `:153-158`). The existing probe offers `has_ended(pid)` and `start_time_match(pid, recorded)` but no operation that captures a process identity for the first pass (`src/ai_cli/process_probe.py:143-172`, `:236-247`, `:329-339`), and Phase 1 does not list that module as modified (`docs/designs/stale-session-reaper.md:136-138`). See N-2. The Round 1 row is `docs/audits/stale-session-reaper-audit.md:588`. **Verification: CONFIRMED interface mismatch.** |
| DV-3 | **PASS** | The invariant requires current-boot monotonic age (`docs/designs/stale-session-reaper.md:72-74`); the ledger makes wall time log-only and rejects boot/clock uncertainty (`:96`, `:116-122`); T-1.2 covers boot mismatch and unavailable clock/boot identity (`:147-151`); D-9 matches approved option (a) (`:610-650`). This closes `docs/audits/stale-session-reaper-audit.md:589`. **Verification: CONFIRMED.** |

### R2.2 Round 1 F-N verification

| ID | Verdict | Evidence |
|----|---------|----------|
| F-1 | **PASS** | Safety and Integration both say the tmux generation option—not `_AI_SESSION_RE` or another name pattern—is the sole managed marker (`docs/designs/stale-session-reaper.md:67`, `:126`). T-1.1 explicitly covers local/remote, indexed/custom/hyphenated, and `c/g/p/cx` names (`:141`). This closes `docs/audits/stale-session-reaper-audit.md:590`. **Verification: CONFIRMED.** |
| F-2 | **PASS** | T-2.4 requires correcting the README Features table and every legacy timeout example, documenting independent start/observe/opt-in behavior, and adding an Unreleased CHANGELOG correction without rewriting history (`docs/designs/stale-session-reaper.md:184-186`). This matches `docs/audits/stale-session-reaper-audit.md:591`. **Verification: CONFIRMED.** |
| F-3 | **PASS** | The writer guarantee is expressly narrowed to complete-file atomic visibility, “not crash durability”; the next sentence says loss/staleness preserves and waits for a fresh heartbeat rather than granting authority (`docs/designs/stale-session-reaper.md:96`). Risk 2 repeats “Missing or non-durable data preserves” (`:216`), and D-1 now says “atomically visible,” not durable (`:247`, `:280`). This closes `docs/audits/stale-session-reaper-audit.md:592`. **Verification: CONFIRMED.** |

### R2.3 AD-N decisions verification

| ID | Verdict | Evidence |
|----|---------|----------|
| AD-1 | **PASS** | The audit approved option (a), including a random token in tmux metadata/ledger and generation-conditional writes/deletes (`docs/audits/stale-session-reaper-audit.md:825-865`). D-7 reproduces that option and human provenance (`docs/designs/stale-session-reaper.md:516-557`); its substance appears in Safety (`:67-74`), lifecycle/protocol/heartbeat (`:80`, `:86-96`), Data Model (`:116-122`), Integration (`:126`), Phase 1 ACs (`:140-158`), the audit checklist (`:202`), risk mitigation (`:215-216`), and Approval Log (`:684`). **Verification: CONFIRMED.** |
| AD-2 | **PARTIAL** | The audit approved a wrapper-lifetime generation lease held through the final kill and called for non-mocked inheritance/crash-release tests (`docs/audits/stale-session-reaper-audit.md:870-909`). D-8 and the final-pass protocol contain the exclusive fence (`docs/designs/stale-session-reaper.md:80`, `:88`, `:153-159`, `:563-604`), but neither the lifecycle nor an AC defines successful continuity across the direct `exec` transitions at `src/ai_cli/session_script.py:459`, `:638`, and `:641`; see N-1. **Verification: CONFIRMED omission; runtime behavior unverified pre-implementation.** |
| AD-3 | **PASS** | The audit approved same-boot monotonic time plus boot generation (`docs/audits/stale-session-reaper-audit.md:914-952`). D-9 matches (`docs/designs/stale-session-reaper.md:610-650`), with matching Safety (`:72-74`), heartbeat/schema (`:96`, `:116-122`), Phase 1 ACs (`:140`, `:147-150`), audit checklist (`:204`), and Approval Log (`:686`). **Verification: CONFIRMED.** |

### R2.4 NEW issues surfaced

#### N-1: Lifetime-lease continuity is undefined at first evidence and shell self-`exec` — `CRITICAL` / `P0`

**Location:** `docs/designs/stale-session-reaper.md:80`, `:94`, `:144`, `:156-159`, `:575-576`,
`:604`, `:668`; `src/ai_cli/session_script.py:447`, `:449-460`, `:617-642`

**What the Round 1 Resolution Pass claimed:** DV-1 moved the final TOCTOU protocol to approved AD-2
(`docs/audits/stale-session-reaper-audit.md:587`), whose chosen option says a live wrapper
retains the lease continuously and inheritance/crash-release behavior needs non-mocked tests
(`docs/audits/stale-session-reaper-audit.md:875-884`).

**Actual state:** The revision says the wrapper holds the lease “continuously until process exit”
and that this closes the edge (`docs/designs/stale-session-reaper.md:80`, `:604`), but does not
require acquisition to precede the first usable heartbeat (`:94`, `:140-145`). The shipped wrapper
replaces itself directly with a new shell at three `exec` sites and has no lease handoff/adoption
step (`src/ai_cli/session_script.py:449-460`, `:617-642`). T-1.4 exercises a lease through controlled
adapters but never requires real subprocess acquisition, successful self-`exec` inheritance, failed
inheritance, or crash release on supported platforms (`docs/designs/stale-session-reaper.md:155-159`),
despite D-8 naming those non-mocked tests as necessary (`:575-576`, `:604`). “Cannot inherit” merely
forbids new evidence (`:144`); it does not explain how an old stale record plus a now-free lease
prevents the reaper from acquiring destructive authority.

**Why it matters:** If the lease is published after the first record, closes during `exec`, or is
held by an old helper the replacement cannot verify, the “live wrapper continuously owns the
lease” premise is false. In the free-lease case, the same false process observation that motivated
this design can again pass the final fence against a live wrapper.

**Verification note:** **CONFIRMED** specification omission and shipped self-`exec` paths;
**PLAUSIBLE** destructive interleaving because the lease implementation does not exist.

**Verification command:**

```bash
sed -n '76,96p;139,159p;563,606p;666,669p' docs/designs/stale-session-reaper.md
sed -n '447,460p;617,642p' src/ai_cli/session_script.py
```

**Recommended fix (Round 3):** Define one lease owner and descriptor/handle lifecycle. Require the
lease to be acquired and verified before any usable ledger record is written; require atomic,
gap-free transfer/adoption across both self-`exec` paths or replace those paths with a holder whose
lifetime is demonstrably identical to the tmux wrapper generation. Add real subprocess tests for
initial ordering, both exec paths, failed inheritance/adoption, normal exit, crash release, and a
reaper racing each transition on every supported lock backend. Any transition that cannot prove
continuity must revoke/delete that generation's usable record before the lease can become free.

#### N-2: The required process identity has no capture contract — `MAJOR` / `P1`

**Location:** `docs/designs/stale-session-reaper.md:86`, `:136-138`, `:149`, `:153-158`;
`src/ai_cli/process_probe.py:130-172`, `:229-247`, `:316-339`

**What the Round 1 Resolution Pass claimed:** DV-2 required pane/process identity and reuse/respawn
ACs (`docs/audits/stale-session-reaper-audit.md:588`).

**Actual state:** The design now requires the initial pass to capture “each process identity exposed
by the probe” and compare it during revalidation (`docs/designs/stale-session-reaper.md:86`), but the
public probe can only answer liveness and compare a caller-supplied recorded start value; it has no
method that returns a pane process's start identity for capture (`src/ai_cli/process_probe.py:130-172`,
`:236-247`, `:329-339`). Phase 1 neither defines the identity's type/UNKNOWN semantics nor lists
`process_probe.py` among modified files (`docs/designs/stale-session-reaper.md:136-138`). The ACs name
an identity and PID-reuse outcome (`:149`, `:153-158`) without supplying the contract that makes
them independently implementable.

**Why it matters:** An implementer can satisfy the pane-ID-to-PID mapping text while still comparing
only recycled PID numbers, or bypass the existing platform abstraction. Either outcome leaves the
claimed PID-reuse defense unverified or non-portable.

**Verification note:** **CONFIRMED** against the complete `ProcessProbe`, `ProcfsProbe`, and
`PsutilProbe` implementations; runtime behavior is not asserted because Phase 1 code does not exist.

**Verification command:**

```bash
sed -n '84,88p;132,159p' docs/designs/stale-session-reaper.md
sed -n '130,172p;229,247p;316,339p' src/ai_cli/process_probe.py
```

**Recommended fix (Round 3):** Add `process_probe.py` to Phase 1 scope and specify a public typed
snapshot contract that returns state plus platform process-birth identity (or explicit UNKNOWN).
Require initial and lease-held snapshots to match that identity before a zombie counts; define the
safe GONE case separately; add real PID-reuse/identity-unavailable tests for procfs and psutil.

#### N-3: Whole-interval exception rollback conflicts with immediate per-candidate kills — `MAJOR` / `P1`

**Location:** `docs/designs/stale-session-reaper.md:78`, `:88`, `:167-176`

**What the Round 1 Resolution Pass claimed:** JA-5 required run failure behavior that preserves all
sessions and restarts under Circus (`docs/audits/stale-session-reaper-audit.md:585`).

**Actual state:** Lifecycle prose and T-2.1 say one malformed candidate or evaluator exception
aborts the whole interval “before any kill” and preserves all sessions
(`docs/designs/stale-session-reaper.md:78`, `:174`). The candidate protocol instead acquires,
revalidates, and immediately kills each eligible candidate (`:88`). It defines no all-candidate
preflight or commit boundary, so an exception on candidate B cannot undo a completed kill of
candidate A.

**Why it matters:** The implementation cannot satisfy both statements. Tests may encode either
candidate-local fail-closed behavior or whole-interval atomicity and still appear reasonable,
leaving restart behavior and partial-pass authority undefined.

**Verification note:** **CONFIRMED** internal contradiction; no runtime implementation exists.

**Verification command:**

```bash
sed -n '76,90p;167,176p' docs/designs/stale-session-reaper.md
```

**Recommended fix (Round 3):** Choose and specify one boundary. Either stage and validate every
candidate before any destructive action (including how leases are acquired/released), or narrow
the guarantee to candidate-local preservation and state explicitly that already completed,
fully-authorized kills are not rolled back when a later candidate fails. Add a multi-candidate AC
where candidate A is eligible and candidate B throws.

### R2.5 Verification Matrix

| Finding | Command | Expected | Actual at `383b055` | Pass? |
|---------|---------|----------|----------------------|-------|
| IC-1 | `rg -n 'sole authoritative marker|same generation token|generation_token|generation-conditional' docs/designs/stale-session-reaper.md` | Token binding in invariant, writer, schema, Integration | Matches at design lines 67, 69, 96, 119, 122, and 126. | ✅ |
| IC-2 | `rg -n '\\b[Cc]riteria? [123]\\b|\\b[Cc]riterion [123]\\b' docs/designs/stale-session-reaper.md` | No undefined numbered references | No output; exit 1. | ✅ |
| JA-4 | `sed -n '155,159p' docs/designs/stale-session-reaper.md` | Integrated positive plus mutation/negative controls | Printed all three T-1.4 ACs and the matching exit gate. | ✅ |
| JA-5 / N-3 | `sed -n '76,90p;167,176p' docs/designs/stale-session-reaper.md` | Lifecycle ACs present; interval semantics consistent | Commands are covered, but line 174's before-any-kill guarantee conflicts with line 88's immediate candidate kill. | ❌ |
| JA-6 | `sed -n '94,96p;139,145p' docs/designs/stale-session-reaper.md` | Publication attempt survives ledger failure | Prose and T-1.1 both require the attempt and non-fatal result. | ✅ |
| DV-1 / N-1 | `sed -n '76,96p;139,159p;563,606p' ...; sed -n '447,460p;617,642p' src/ai_cli/session_script.py` | Continuous lease specified and tested across lifecycle | Final-pass fence is present; acquisition-before-evidence and three direct `exec` transitions are not covered. | ❌ |
| DV-2 / N-2 | `sed -n '84,88p;132,159p' ...; sed -n '130,172p;229,247p;316,339p' src/ai_cli/process_probe.py` | Two-pass identity contract supported by probe | Design requires identity; probe exposes comparison only, not capture, and is absent from Phase 1 modified files. | ❌ |
| DV-3 | `sed -n '69,74p;94,96p;114,122p;146,151p;610,650p' docs/designs/stale-session-reaper.md` | Same-boot monotonic throughout | Invariant, writer, schema, ACs, and D-9 agree; wall time is logs-only. | ✅ |
| F-1 | `sed -n '65,74p;124,126p;139,142p' docs/designs/stale-session-reaper.md` | One authoritative metadata classifier | Safety, Integration, and AC use only the generation option and enumerate supported names. | ✅ |
| F-3 | `sed -n '94,96p;211,217p;240,280p' docs/designs/stale-session-reaper.md` | Atomic-visibility claim narrowed; safety independent of durability | Prose says “not crash durability” and missing/non-durable data preserves; D-1 says atomically visible. | ✅ |

**Verified: 10/10 sampled backlog checks reproduced against commit
`383b0559fb59bdfd0f92413e835dcbccf572bbd8`; 7 matched the claimed resolution and 3 reproduced
the PARTIAL/new-finding state.**

### R2 Recommendations

**MUST be fixed before implementation:**

- N-1 / JA-2 / DV-1 / AD-2: specify and test gap-free lease ownership from before first usable
  heartbeat through both wrapper self-`exec` paths and final exit/crash.
- N-2 / DV-2: define and scope the cross-platform process-identity capture/revalidation contract.
- N-3 / JA-5: reconcile whole-interval exception semantics with per-candidate kill ordering and add
  the multi-candidate regression AC.

**SHOULD be fixed before design approval:**

- None beyond the MUST items.

**Can be folded into a follow-up:**

- None. All three new issues affect the destructive safety protocol or its required failure
  semantics.

## Round 3 — Resolution Verification (append-only)

**Round 3 auditor:** Codex (`GPT-5`), fresh independent verification invocation

**Round 3 date:** 2026-08-28

**Round 3 scope:** Re-verified the complete Round 2 MUST-fix backlog and every dependent PARTIAL
verdict against `docs/designs/stale-session-reaper.md` at exact commit
`c228a9bad26a2a1e66a432d32a43399290c37c7a`. Commit `c228a9b` does not change the design; the
actual 29-line-addition/11-line-deletion Round 3 design revision is `fe8170b`, and both commits
contain the same design blob `978aef32522637edec5eba6b69395ee62aef327c`. The full design, audit
history, `session_script.py`, `process_probe.py`, and `process_manager.py` were re-read. No target
design or source edit was made; this round writes only this audit history.

### R3 Summary

The complete verification set is 18 items: all 12 Round 1 findings, N-1 through N-3, and AD-1
through AD-3. No older open MUST-fix item was omitted. Fourteen verdicts are PASS and four are
PARTIAL: JA-2, DV-1, N-1, and AD-2. N-2's typed capture contract and N-3's candidate-local semantics
are present and consistent, promoting DV-2 and JA-5 respectively to PASS. N-1's startup ordering,
three self-`exec` branches, fail-closed adoption path, and real-subprocess exit gate are present,
but its separate-holder crash topology is not safe or internally consistent. Three new issues were
reproduced: N-4 (CRITICAL) for asymmetric partial crashes, N-5 (MAJOR) for undefined holder
cardinality across ordinary heartbeat-watcher restarts, and N-6 (MAJOR) because “authenticated
adoption” has no falsifiable authentication contract.

### R3.1 Full backlog verification

| ID | Verdict | Evidence |
|----|---------|----------|
| IC-1 | **PASS** | Round 2's PASS required instance-bound evidence (`docs/audits/stale-session-reaper-audit.md:644`). The design still says the tmux option is the “sole authoritative marker” and every gate agrees on the “same generation token” (`docs/designs/stale-session-reaper.md:67-74`); record paths/schema and acceptance rules remain generation-bound (`:122-128`). **Verification: CONFIRMED.** |
| IC-2 | **PASS** | Round 2 found the stale numbered-criteria references removed (`docs/audits/stale-session-reaper-audit.md:645`). The current D-1 rationale names the “fail-closed requirement” (`docs/designs/stale-session-reaper.md:298`), and the full numbered-Criteria search still returns no match. **Verification: CONFIRMED.** |
| JA-2 | **PARTIAL** | The two gates and ambiguity-preserves rule remain explicit (`docs/designs/stale-session-reaper.md:69-76`), but a live wrapper can lose its separate holder and leave a schema-valid record plus a free lease; the reaper has no observable holder/control field in the ledger schema (`:82-94`, `:122-128`). This does not close Round 2's fail-closed dependency (`docs/audits/stale-session-reaper-audit.md:646`); see N-4 and N-6. **Verification: CONFIRMED specification contradiction/omission; PLAUSIBLE destructive interleaving.** |
| JA-4 | **PASS** | The integrated positive, gate-removal negative, pane/PID/token/rename, and real-subprocess race controls remain required (`docs/designs/stale-session-reaper.md:167-173`), matching Round 2's PASS (`docs/audits/stale-session-reaper-audit.md:647`). **Verification: CONFIRMED.** |
| JA-5 | **PASS** | Candidate-local behavior now agrees in lifecycle prose, the run failure AC, and the explicit A-killed/B-throws AC: “not rolled back,” “preserve that candidate,” and “shall not affect candidate A's already-completed kill” (`docs/designs/stale-session-reaper.md:80`, `:181-190`, `:195-198`). This resolves the contradiction recorded in Round 2 (`docs/audits/stale-session-reaper-audit.md:648`). **Verification: CONFIRMED.** |
| JA-6 | **PASS** | The holder-authorized local write still precedes best-effort messaging, and local persistence failure still logs, attempts publication, and exits non-fatally (`docs/designs/stale-session-reaper.md:100-102`, `:146`, `:154`). This preserves Round 2's PASS (`docs/audits/stale-session-reaper-audit.md:649`). **Verification: CONFIRMED.** |
| DV-1 | **PARTIAL** | The reaper still holds the exclusive generation lease across final revalidation and kill (`docs/designs/stale-session-reaper.md:84`, `:94`, `:165-166`), but that fence protects a live wrapper only while the separate holder remains alive. Holder-only death frees the lock without making the retained record structurally unusable (`:82`, `:122-128`); see N-4. The Round 2 dependency remains open (`docs/audits/stale-session-reaper-audit.md:650`). **Verification: CONFIRMED text; PLAUSIBLE false-probe race.** |
| DV-2 | **PASS** | The public contract now returns backend-tagged immutable identity, uses the procfs start-time field or psutil create time, maps `None` to UNKNOWN, forbids cross-backend comparison, and defines GONE/zombie matching (`docs/designs/stale-session-reaper.md:90-92`). Phase 1 scopes both implementations and the UNKNOWN/PID-reuse tests (`:140-143`, `:155-172`). This closes Round 2's interface mismatch (`docs/audits/stale-session-reaper-audit.md:651`). **Verification: CONFIRMED design contract against the current interface to be extended at `src/ai_cli/process_probe.py:114-172`, `:196-247`, `:300-339`.** |
| DV-3 | **PASS** | Current-boot monotonic age remains authoritative, wall time remains logs-only, and boot/clock uncertainty preserves (`docs/designs/stale-session-reaper.md:71-74`, `:102`, `:125-128`, `:628-668`). **Verification: CONFIRMED; no regression from Round 2's PASS (`docs/audits/stale-session-reaper-audit.md:652`).** |
| F-1 | **PASS** | The generation option remains the sole marker in Safety and Integration, and T-1.1 still enumerates local/remote, indexed/custom/hyphenated, and `c/g/p/cx` sessions (`docs/designs/stale-session-reaper.md:67`, `:132`, `:147`). **Verification: CONFIRMED; no regression from Round 2's PASS (`docs/audits/stale-session-reaper-audit.md:658`).** |
| F-2 | **PASS** | T-2.4 still requires README feature/config corrections plus an Unreleased changelog correction (`docs/designs/stale-session-reaper.md:199-201`). **Verification: CONFIRMED; no regression from Round 2's PASS (`docs/audits/stale-session-reaper-audit.md:659`).** |
| F-3 | **PASS** | Heartbeat recording still promises atomically visible complete-file reads “not crash durability,” and loss/staleness preserves rather than authorizes (`docs/designs/stale-session-reaper.md:102`); the schema continues to treat missing/invalid evidence as preserve (`:128`). **Verification: CONFIRMED; no regression from Round 2's PASS (`docs/audits/stale-session-reaper-audit.md:660`).** |
| AD-1 | **PASS** | Approved option (a) remains reflected by the random generation token in tmux metadata and the generation-keyed ledger/schema (`docs/designs/stale-session-reaper.md:67`, `:100`, `:122-128`, `:534-575`). **Verification: CONFIRMED against the approved decision (`docs/audits/stale-session-reaper-audit.md:1044-1085`).** |
| AD-2 | **PARTIAL** | The design implements a holder-owned exclusive final fence and honestly disclaims lock-handle inheritance across `exec` (`docs/designs/stale-session-reaper.md:76`, `:84`), but D-8 still claims a live wrapper itself retains the lease and that process crash releases it automatically (`:581-622`). A separate holder makes neither statement true for both partial-crash directions; see N-4 through N-6. **Verification: CONFIRMED mismatch with approved option (a) (`docs/audits/stale-session-reaper-audit.md:1089-1129`).** |
| AD-3 | **PASS** | D-9, the schema, and the evaluator still implement approved same-boot monotonic time plus boot generation (`docs/designs/stale-session-reaper.md:125-128`, `:628-668`). **Verification: CONFIRMED against the approved decision (`docs/audits/stale-session-reaper-audit.md:1133-1172`).** |
| N-1 | **PARTIAL** | The revision now orders holder readiness before first usable evidence (`docs/designs/stale-session-reaper.md:76`, `:82`, `:100`, `:149`), names all three shipped self-`exec` sites and suspends writes through adoption (`:84`, `:132`, `:152`), disclaims descriptor/handle inheritance (`:84`), and requires real wrapper/reaper subprocess races on every supported lock backend (`:171-173`). It does not maintain the promised invariant when only the holder or only the wrapper crashes, does not define holder reuse across ordinary watcher restarts, and does not define what authenticates adoption; see N-4 through N-6. This only partially resolves Round 2's CRITICAL finding (`docs/audits/stale-session-reaper-audit.md:672-714`). **Verification: CONFIRMED present fixes and remaining omissions; PLAUSIBLE destructive interleaving.** |
| N-2 | **PASS** | `ProcessProbe.capture_identity(pid) -> ProcessIdentity | None`, backend-specific opaque values, UNKNOWN preservation, state/identity matching, and no cross-backend comparison are explicit in protocol, Integration, ACs, and the exit gate (`docs/designs/stale-session-reaper.md:90-92`, `:132`, `:157-173`). This matches the requested capture contract (`docs/audits/stale-session-reaper-audit.md:716-750`). **Verification: CONFIRMED.** |
| N-3 | **PASS** | Candidate-local exception semantics are identical in lifecycle prose and T-2.1, while T-2.3 covers A killed before B throws (`docs/designs/stale-session-reaper.md:80`, `:188`, `:198`). This matches the requested resolution (`docs/audits/stale-session-reaper-audit.md:752-782`). **Verification: CONFIRMED.** |

### R3.2 NEW issues surfaced

#### N-4: Separate holder and wrapper failures do not preserve the lifetime-lease invariant — `CRITICAL` / `P0`

**Location:** `docs/designs/stale-session-reaper.md:76`, `:82-84`, `:122-128`, `:149-153`,
`:581-622`

**What the Round 2 finding required:** N-1 required “gap-free transfer/adoption” or a holder whose
lifetime is demonstrably identical to the wrapper generation, including holder/wrapper crash and
reaper-race tests (`docs/audits/stale-session-reaper-audit.md:708-714`).

**Actual state:** The new protocol says both that “The holder owns the lock handle” and that “An
abrupt wrapper or holder crash releases the OS lease automatically” (`docs/designs/stale-session-reaper.md:82`).
Those statements cannot both hold for a wrapper-only crash: an OS lock is released when its owning
holder process closes/dies, not when a different supervising wrapper dies. In the opposite direction,
a holder-only crash does release the lease while the wrapper can remain live. The retained ledger
record contains no holder identity, lease epoch, or control-channel state (`:122-128`), so after it
ages the reaper cannot observe the Safety section's claim that the holder/control channel is
unavailable; it sees a schema-valid stale record and an acquirable lease (`:76`, `:84`, `:94`). D-8
retains the old, now-inaccurate claims that “A live or merely stalled wrapper retains” the lease and
that process crash releases it (`:588-589`, `:622`).

**Why it matters:** If the holder dies while a live or stalled wrapper remains, the exact false
process observation that motivated the design can again combine with a stale record and free lease
to authorize a kill. If the wrapper dies while the holder survives or hangs, the holder can instead
retain the generation lease indefinitely and prevent eventual cleanup.

**Verification note:** **CONFIRMED** ownership/crash contradiction and absent observable holder
state; **PLAUSIBLE** destructive race because implementation does not yet exist.

**Verification command:**

```bash
sed -n '76p;82,84p;122,128p;149,153p;581,622p' docs/designs/stale-session-reaper.md
```

**Recommended fix (next design revision):** Specify one process topology and a cross-platform
invariant under which no live wrapper can exist with its generation lease acquirable. Define the
holder's mandatory response to wrapper control-channel EOF/parent death and the wrapper's mandatory
response to holder death; do not call wrapper-crash release “automatic” when another process owns
the lock. If holder death cannot prove wrapper termination before lock availability, add a durable
epoch/claim mechanism that makes the retained record observably ineligible. Add separate real-
subprocess ACs for holder-only crash, wrapper-only crash, simultaneous crash, stalled survivor,
control-channel break, and a reaper racing each state. Reconcile D-8 and the Risk/Open Question text
with the chosen topology.

#### N-5: Holder cardinality is undefined across ordinary heartbeat-watcher restarts — `MAJOR` / `P1`

**Location:** `docs/designs/stale-session-reaper.md:82`, `:140-153`, `:171-173`;
`src/ai_cli/session_script.py:227-240`, `:449-463`

**Actual state:** The design says, “At heartbeat-watcher startup, the wrapper starts one
generation-scoped lease-holder process” (`docs/designs/stale-session-reaper.md:82`). The shipped
wrapper's `start_watcher()` kills/replaces its heartbeat watcher (`src/ai_cli/session_script.py:227-240`)
and is called at the top of every ordinary agent restart loop (`:449-463`), not only once per
wrapper generation. The ACs cover wrapper startup, the three self-`exec` branches, and crashes, but
do not say whether the existing holder is reused, adopted by the replacement heartbeat watcher,
terminated, or duplicated on this routine path (`docs/designs/stale-session-reaper.md:149-153`,
`:171-173`).

**Why it matters:** A literal implementation can start multiple holder processes for one
generation; exclusivity makes later holders fail while the original may become unmonitored, causing
lost heartbeat evidence, leaked processes/leases, or permanent failure to reclaim a dead session.

**Verification note:** **CONFIRMED** lifecycle ambiguity against the shipped loop; duplicate/leak
outcomes are **PLAUSIBLE** until implementation.

**Verification command:**

```bash
sed -n '82p;140,153p;167,173p' docs/designs/stale-session-reaper.md
sed -n '227,240p;449,463p' src/ai_cli/session_script.py
```

**Recommended fix (next design revision):** Make the holder unambiguously generation-scoped and
created once outside the replaceable heartbeat-watcher lifecycle, or define idempotent authenticated
reuse. Specify one-holder cardinality, duplicate-start behavior, control-channel reconnection, and
cleanup of a detected orphan. Add a real-subprocess AC that performs multiple ordinary
`start_watcher()` cycles and proves one lock owner, continued writes through that same owner, and no
orphan holder after wrapper exit/crash.

#### N-6: “Authenticated adoption” has no authentication or replay contract — `MAJOR` / `P1`

**Location:** `docs/designs/stale-session-reaper.md:67`, `:84`, `:107-128`, `:152`, `:171-173`

**Actual state:** The lifecycle calls the transition an “authenticated adoption handshake” and says
only that it is for “the same wrapper generation” (`docs/designs/stale-session-reaper.md:84`). The
generation value itself is stored in the queryable tmux option (`:67`); the Data Model defines only
configuration and heartbeat-record fields (`:107-128`), with no adoption capability, challenge,
holder identity, adoption epoch, single-adopter rule, timeout, or replay/concurrent-adopter outcome.
The AC merely requires that “the holder verifies adoption for the same generation” (`:152`), and
the subprocess gate names generic failed adoption without defining a falsifying invalid peer
(`:171-173`).

**Why it matters:** “Authenticated” is load-bearing but not independently testable as written. An
implementation that checks only the readable generation token, accepts a replay, or permits two
same-generation controllers could satisfy the words while resuming writes for the wrong controller
or revoking the record during a valid transition.

**Verification note:** **CONFIRMED** specification/AC gap; exploitation likelihood is not asserted.

**Verification command:**

```bash
sed -n '67p;82,84p;107,128p;149,153p;167,173p' docs/designs/stale-session-reaper.md
rg -n 'challenge|capabilit|credential|nonce|replay|single.adopter|concurrent.adopter|adoption.*timeout' docs/designs/stale-session-reaper.md || true
```

**Recommended fix (next design revision):** Define the observable adoption contract: credential or
OS-peer property, binding to holder/generation/replacement wrapper, single-use adoption epoch,
write-suspension interval, timeout, and terminal outcomes for wrong generation, wrong peer, replay,
concurrent adopter, and channel loss. The credential need not be a lock descriptor and must not
reintroduce an fd/handle-inheritance claim. Add real-subprocess negative ACs for each invalid peer
class and require the holder to keep or revoke evidence according to one explicit state machine.

### R3.3 Verification Matrix

| Finding/check | Command | Expected | Actual at `c228a9b` | Reproduced? |
|---------------|---------|----------|----------------------|-------------|
| Revision provenance | `git ls-tree c228a9b docs/designs/stale-session-reaper.md; git ls-tree fe8170b docs/designs/stale-session-reaper.md` | Same design blob at both commits | Both printed blob `978aef32522637edec5eba6b69395ee62aef327c`; `git diff --exit-code fe8170b c228a9b -- docs/designs/stale-session-reaper.md` exited 0. | ✅ |
| N-1 startup/exec coverage | `sed -n '76,84p;100p;132p;149,153p;171,173p' docs/designs/stale-session-reaper.md; rg -n 'exec "\\{_session_shell\\}"' src/ai_cli/session_script.py` | Readiness-before-write, three adoption branches, no inheritance claim, real subprocess gate | Printed all claimed protocol/AC text and source execs at lines 459, 638, and 641. | ✅ |
| N-4 partial crashes | `sed -n '76p;82,84p;122,128p;149,153p;581,622p' docs/designs/stale-session-reaper.md` | Separate holder ownership conflicts with automatic wrapper-crash release; no holder state in record | Printed holder-only ownership, both-crash automatic-release claim, five-field record schema, and stale D-8 wrapper-ownership text. | ✅ |
| N-5 ordinary restarts | `sed -n '82p;140,153p;167,173p' docs/designs/stale-session-reaper.md; sed -n '227,240p;449,463p' src/ai_cli/session_script.py` | Holder starts at watcher startup; watcher restarts every outer loop; no ordinary-restart AC | Printed all three facts. | ✅ |
| N-6 adoption authentication | `rg -n 'authenticated adoption|challenge|capabilit|credential|nonce|replay|single.adopter|concurrent.adopter|adoption.*timeout' docs/designs/stale-session-reaper.md` | Authentication adjective but no authenticator/replay contract | Only “authenticated adoption handshake” matched, at design line 84. | ✅ |
| N-2 / DV-2 identity | `sed -n '90,92p;132p;155,173p' docs/designs/stale-session-reaper.md; rg -n 'capture_identity' src/ai_cli/process_probe.py || true` | Complete future contract in design; current probe has no capture method yet | Design printed typed backend/UNKNOWN/mismatch contract and ACs; source search returned no match, consistent with scoped future modification. | ✅ |
| N-3 / JA-5 candidate-local failure | `sed -n '80p;181,198p' docs/designs/stale-session-reaper.md` | Prose and both ACs use candidate-local semantics | Printed preserve/continue behavior and A-killed/B-throws non-rollback AC. | ✅ |
| IC-1 / F-1 regression | `sed -n '67,76p;122,132p;145,150p' docs/designs/stale-session-reaper.md` | Generation marker/schema/classifier unchanged | Printed sole-marker, same-generation, schema, Integration, and naming-variant AC text. | ✅ |
| F-3 regression | `sed -n '102p;122,128p' docs/designs/stale-session-reaper.md` | Atomic visibility is not durability; invalid/unavailable evidence preserves | Printed both limitations and preserve behavior. | ✅ |
| AD-3 regression | `sed -n '71,74p;102p;125,128p;628,668p' docs/designs/stale-session-reaper.md` | Same-boot monotonic throughout | Safety, writer, schema, and D-9 all use monotonic time/boot generation; wall time remains logs-only. | ✅ |

**Verified: 10/10 matrix checks reproduced against commit
`c228a9bad26a2a1e66a432d32a43399290c37c7a`. Six checks confirm resolved or non-regressed behavior;
four reproduce N-1's PARTIAL state and N-4 through N-6.**

### R3 Recommendations

**MUST be fixed before implementation:**

- N-4 / N-1 / JA-2 / DV-1 / AD-2: define a topology that preserves the lifetime-lease invariant
  under holder-only and wrapper-only failure, make the reaper's ambiguity decision observable, add
  partial-crash race ACs, and reconcile D-8's stale wrapper-ownership/automatic-release claims.
- N-5 / N-1: define exactly one generation-scoped holder across every ordinary
  heartbeat-watcher restart, including reuse, duplicate detection, and orphan cleanup, with a real
  repeated-restart subprocess AC.
- N-6 / N-1: define a falsifiable, replay-safe adoption authentication/state contract and its
  invalid-peer subprocess cases.

**SHOULD be fixed before design approval:**

- None beyond the MUST items.

**Can be folded into a follow-up:**

- None. Each new issue affects the approved destructive fence or whether its lifecycle can be
  implemented and tested unambiguously.

### Status after Round 3

The design is **not ready to proceed to implementation**. Across all rounds, 12 of 18 findings are
fixed, but three CRITICAL findings (DV-1, N-1, N-4), three MAJOR findings (JA-2, N-5, N-6), and the
linked AD-2 verification remain open/PARTIAL. A further append-only re-verification is required
after the design resolves N-4 through N-6 and the remaining N-1 dependencies.

## Round 4 — Post-D-10 Verification (append-only)

**Round 4 auditor:** Codex (`GPT-5`), fresh independent verification invocation

**Round 4 date:** 2026-08-28

**Round 4 scope:** Re-verified the complete cross-round backlog against
`docs/designs/stale-session-reaper.md` at exact commit
`035a90b614980428c1c5972a593e514baf3be71b`. The worktree is clean, the named design and source
files match that commit, and the D-10 design blob was introduced by `1fc4166`. The full design,
full Round 1-3 audit history, `session_script.py`, `process_probe.py`, and `process_manager.py` were
read. Relevant launch and generated-wrapper regression call sites in `main.py` and tests were also
traced. No target-design or source edit was made; this round writes only this audit history.

### R4 Summary

All 25 pre-existing checks are now PASS. D-10 closes N-1 and N-4 through N-6 by making the tmux
pane leader the sole process-lifetime lease owner, keeping it stable across replaceable child
generations, and removing every active holder/adoption/control-channel transition. JA-2, DV-1, and
AD-2 therefore also PASS: acquisition precedes usable evidence, a live supervisor blocks the final
exclusive fence, and real subprocess coverage is an explicit Phase 1 exit gate.

Four new issues were reproduced: three MAJOR/P1 (N-7 through N-9) and one MINOR/P2 (N-10). Risk 5's
cross-platform test deferral is honest and safely bounded for facts that only real tmux/shell tests
can establish, but N-7 is a design-time gap within that surface. The shipped three-second threshold
is correct, but N-8 shows the claimed stop outcome omits remote sessions. N-9 shows that moving the
heartbeat tick into a never-restarted supervisor does not yet preserve the existing watcher's
per-agent reset/rearm semantics or test inventory. N-10 records two active stale uses of “wrapper”
that now conflict with supervisor-only ownership. The design remains blocked before implementation.

### R4.1 Full backlog verification

| ID | Verdict | Evidence |
|----|---------|----------|
| IC-1 | **PASS** | The tmux option is the sole managed marker, both gates require the same generation, and the record path/schema are generation-bound (`docs/designs/stale-session-reaper.md:68-77`, `:129-135`). **Verification: CONFIRMED.** |
| IC-2 | **PASS** | D-1 through D-6 continue to name explicit requirements rather than undefined numbered criteria (`docs/designs/stale-session-reaper.md:315`, `:361`, `:407`, `:453`, `:499`, `:545`); the numbered-Criteria search returned no match. **Verification: CONFIRMED.** |
| JA-1 | **PASS** | Launch, attach, resume, and cleanup paths are prohibited from starting or running the evaluator, and Phase 2 repeats the non-interaction AC (`docs/designs/stale-session-reaper.md:93`, `:141`, `:203-204`, `:210`). **Verification: CONFIRMED.** |
| JA-2 | **PASS** | Both independently bound gates are required (`docs/designs/stale-session-reaper.md:70-75`); ambiguity preserves (`:75-77`, `:168-173`); and the reaper holds the exclusive lease through the repeated gates and ID-targeted kill (`:91`, `:99-101`, `:172-173`). D-10 does not weaken this predicate; N-7 through N-10 are lifecycle/parity issues whose specified failure mode creates no usable evidence. **Verification: CONFIRMED.** |
| JA-3 | **PASS** | Absent configuration is `observe`, invalid configuration preserves, and `reap` requires explicit configuration (`docs/designs/stale-session-reaper.md:118-125`, `:205-208`). **Verification: CONFIRMED.** |
| JA-4 | **PASS** | T-1.5 and its exit gate require the integrated positive kill, gate-removal mutation negatives, identity/race cases, and real supervisor/child/reaper subprocesses (`docs/designs/stale-session-reaper.md:180-187`). **Verification: CONFIRMED.** |
| JA-5 | **PASS** | Start/stop/status/run success and failure behavior remains explicit and candidate-local (`docs/designs/stale-session-reaper.md:195-204`); the A-killed/B-throws case remains pinned (`:212`). **Verification: CONFIRMED.** |
| JA-6 | **PASS** | A valid heartbeat attempts best-effort message publication even after local-ledger failure, non-fatally and without reap authority (`docs/designs/stale-session-reaper.md:107-109`, `:139`, `:153`, `:161`). **Verification: CONFIRMED.** |
| DV-1 | **PASS** | The pane-leader supervisor holds the lease continuously (`docs/designs/stale-session-reaper.md:77`, `:83-87`); the reaper can pass the final predicate only after exclusive acquisition and while holding it through kill (`:91`, `:101`). The old holder-only failure edge no longer exists. **Verification: CONFIRMED design closure; lock behavior remains implementation-gated by real tests.** |
| DV-2 | **PASS** | The protocol defines backend-tagged immutable process identities, UNKNOWN semantics, exact GONE/zombie transitions, and two-pass pane-ID-to-PID equality (`docs/designs/stale-session-reaper.md:97-101`), with matching ACs and backend tests (`:164-166`, `:183-187`). **Verification: CONFIRMED against the current extension seams in `src/ai_cli/process_probe.py:114-172`, `:196-247`, `:300-339`.** |
| DV-3 | **PASS** | Only same-boot monotonic age authorizes staleness; wall time is logs-only and boot/clock uncertainty preserves (`docs/designs/stale-session-reaper.md:73`, `:109`, `:135`, `:169`). **Verification: CONFIRMED.** |
| DV-4 | **PASS** | Revalidation targets the captured opaque session ID, never its name, and post-kill deletion requires confirmed ID absence plus a matching generation (`docs/designs/stale-session-reaper.md:101-103`). **Verification: CONFIRMED.** |
| DV-5 | **PASS** | An unreadable or UNKNOWN process observation at either pass preserves; `None` is never treated as an identity match (`docs/designs/stale-session-reaper.md:99`, `:165`, `:168`). **Verification: CONFIRMED.** |
| F-1 | **PASS** | Candidate enumeration and Integration use the generation option rather than `_AI_SESSION_RE`, including local/remote, indexed/custom/hyphenated, and `cx` names (`docs/designs/stale-session-reaper.md:68`, `:97`, `:139`, `:154`). **Verification: CONFIRMED.** |
| F-2 | **PASS** | Phase 2 still requires corrections to the README feature/config claims and an Unreleased changelog entry (`docs/designs/stale-session-reaper.md:213-215`). **Verification: CONFIRMED.** |
| F-3 | **PASS** | The ledger promise remains atomically visible complete-file reads, expressly not crash durability; loss or staleness preserves (`docs/designs/stale-session-reaper.md:109`). **Verification: CONFIRMED.** |
| AD-1 | **PASS** | D-7's approved random token is reflected in tmux metadata, ledger identity, generation-conditional writes/deletes, and legacy-session preservation (`docs/designs/stale-session-reaper.md:551-592`; Safety/Data Model at `:68-77`, `:129-135`). **Verification: CONFIRMED against `docs/audits/stale-session-reaper-audit.md` § AD-1.** |
| AD-2 | **PASS** | The approved WHAT was a wrapper-generation lifetime lease plus an exclusive final fence and non-mocked crash-release coverage (`docs/audits/stale-session-reaper-audit.md` § AD-2). D-8 now makes the persistent pane-leader supervisor that lifetime owner (`docs/designs/stale-session-reaper.md:598-639`); D-10 removes lease-owner `exec` entirely (`:691-723`); T-1.5 requires real crash/update/race tests (`:184-187`). The HOW evolved without weakening the approved safety property. **Verification: CONFIRMED.** |
| AD-3 | **PASS** | D-9 remains same-boot monotonic time plus boot generation and agrees with the schema/evaluator (`docs/designs/stale-session-reaper.md:645-685`, `:135`). **Verification: CONFIRMED against `docs/audits/stale-session-reaper-audit.md` § AD-3.** |
| N-1 | **PASS** | Acquisition and verification precede first usable evidence; all three update sites become child fork/exec cycles under one unchanged supervisor PID/lease; normal/crash release and real transition races are ACs (`docs/designs/stale-session-reaper.md:77`, `:83-87`, `:156-160`, `:184-187`). No lease-holding process self-`exec`s. **Verification: CONFIRMED.** |
| N-2 | **PASS** | `ProcessIdentity` and `capture_identity()` remain fully specified for procfs and psutil with explicit UNKNOWN and backend-mismatch behavior (`docs/designs/stale-session-reaper.md:97-99`, `:139`, `:164-166`, `:186`). **Verification: CONFIRMED.** |
| N-3 | **PASS** | Candidate-local exceptions preserve only that candidate and do not roll back an earlier authorised kill (`docs/designs/stale-session-reaper.md:81`, `:202`, `:212`). **Verification: CONFIRMED.** |
| N-4 | **PASS** | The supervisor is simultaneously pane leader and sole OS-lease owner; its crash and lease release therefore share one process-lifetime boundary, while child exit cannot release the lease (`docs/designs/stale-session-reaper.md:77`, `:83-85`, `:178`). There is no holder/wrapper partial-crash pair. **Verification: CONFIRMED topology; real crash release remains an explicit implementation test at `:185-187`.** |
| N-5 | **PASS** | Exactly one supervisor is created by tmux at session creation, and `start_watcher()`'s heartbeat tick moves into that process rather than starting a lease owner on each agent attempt (`docs/designs/stale-session-reaper.md:83`, `:139`, `:159`, `:184`). In shipped launch code, reattach only rewrites the stable script and attaches; only the absent-session branch invokes `tmux new-session` (`src/ai_cli/main.py:2959-2988`). The only three loop update replacements in shipped `session_script.py` are at `:459`, `:638`, and `:641`; its terminal remote `exec $SHELL` at `:653` is not a supervisor restart but exposes N-8. **Verification: CONFIRMED cardinality; see N-9 for monitoring-state parity.** |
| N-6 | **PASS** | Active Safety states “There is no holder process, adoption protocol, control channel, or lease transfer,” and child updates require none (`docs/designs/stale-session-reaper.md:77`, `:699`). Remaining adoption references occur only in D-10's historical context/rejected option (`:693`, `:706-715`) and decision lineage (`:721`), not in the selected protocol, data model, Integration, or ACs. **Verification: CONFIRMED.** |

### R4.2 Required new-surface stress tests

| Surface | Verdict | Evidence |
|---------|---------|----------|
| Risk 5 portability deferral | **PARTIAL — honest deferral plus N-7** | Real tmux/zsh/Bash behavior is appropriately deferred to concrete interactive-input, single-delivery Ctrl-C, resize, relay, crash, and zero-unauthorised-kill subprocess tests, with no usable evidence on an unverified platform (`docs/designs/stale-session-reaper.md:89`, `:179`, `:185-187`, `:252`). That is a safe implementation gate. N-7 is separate: the design already requires one shell trap to behave differently by signal origin without specifying an observable discriminator. **Verification: CONFIRMED.** |
| Non-hot-reloadable supervisor | **PASS — confirmed non-issue** | The limitation is explicit (`docs/designs/stale-session-reaper.md:87`, `:703-704`). Reap safety depends on the OS lease, generation, process identity, and final exclusive acquisition—not source-build equality (`:77`, `:91`, `:97-101`). An older but protocol-conforming live supervisor continues to block acquisition; once it is gone, retained evidence must still pass both gates. A future generation rollover remains separately gated rather than implicit. **Verification: CONFIRMED at design level; implementation defects in an old supervisor are not evidence that hot reload itself weakens this protocol.** |
| Fast-exit threshold and parity | **PARTIAL — N-8** | The shipped threshold is exactly `< 3` seconds (`src/ai_cli/session_script.py:579-580`, `:601-607`), so D-10's number is correct (`docs/designs/stale-session-reaper.md:85`, `:177`). The claimed whole-session outcome is not: remote wrappers continue by `exec $SHELL` after the loop (`src/ai_cli/session_script.py:651-653`). **Verification: CONFIRMED.** |
| Phase 1 restructuring scope | **PARTIAL — N-9/N-10** | Phase 1 lists only the new reaper test and four source files, and its focused pytest gate names only `tests/test_stale_session_reaper.py` (`docs/designs/stale-session-reaper.md:147-187`). The rewrite directly affects existing tests that parse `start_watcher()` and assert a direct self-`exec` (`tests/test_runaway_loop_guards.py:41-47`; `tests/test_cli.py:1832-1840`) and leaves two active “wrapper” ownership statements after D-10 (`docs/designs/stale-session-reaper.md:103`, `:170`). **Verification: CONFIRMED.** |

### R4.3 NEW issues surfaced

#### N-7: Signal rules require an undefined delivery-origin discriminator — `MAJOR` / `P1`

**Location:** `docs/designs/stale-session-reaper.md:89`, `:175-176`, `:179`, `:185-187`

**What D-10 claimed:** “If that verification cannot establish interactive input, one-delivery
Ctrl-C, resize propagation, and supervisor-addressed signal forwarding, the implementation must
preserve the session and disable reap evidence” (`docs/designs/stale-session-reaper.md:89`).

**Actual state:** The shared foreground group sends terminal-generated `SIGINT` and `SIGWINCH` to
both supervisor and child. For those deliveries, the supervisor trap must not relay. The same prose
then requires the supervisor's `SIGINT`/`SIGWINCH` trap to relay when the signal is addressed only
to the supervisor (`docs/designs/stale-session-reaper.md:89`). Neither the lifecycle prose nor T-1.4
defines a signal-origin discriminator, helper contract, or state transition by which a generated
zsh/Bash trap chooses those opposite actions (`:175-176`). T-1.5 exercises only supervisor-directed
`SIGTERM`, not the programmatic `SIGINT` and supervisor-directed `SIGWINCH` branches (`:185`).

**Why it matters:** An implementation that always relays duplicates terminal Ctrl-C/resize; one
that never relays drops supervisor-directed `SIGINT`/`SIGWINCH`. The fail-closed platform gate makes
an unverified implementation non-destructive, but it does not make the selected terminal topology
implementable or independently testable on any platform.

**Verification note:** **CONFIRMED** design/AC omission. Whether a non-shell helper can provide the
needed origin metadata is an implementation choice, not established here.

**Verification command:**

```bash
sed -n '87,90p;174,187p;244,253p' docs/designs/stale-session-reaper.md
```

**Recommended fix:** Specify one observable signal state machine now. Either constrain the shell
supervisor to group-delivered `SIGINT`/`SIGWINCH` and relay only supervisor-directed `SIGTERM`, or
name a helper/API that supplies origin metadata and define its UNKNOWN outcome as no usable evidence.
Add real cases for terminal Ctrl-C/resize, supervisor-only `SIGINT`/`SIGWINCH`/`SIGTERM`, child exit
during relay, and exact one-delivery counts under both zsh and Bash.

#### N-8: The fast-exit AC drops shipped remote-session shell continuation — `MAJOR` / `P1`

**Location:** `docs/designs/stale-session-reaper.md:85`, `:177`, `:185-187`;
`src/ai_cli/session_script.py:579-608`, `:648-653`

**What D-10 claimed:** “a child whose agent exits in under three seconds makes the supervisor end
the session just as the current outer loop breaks” (`docs/designs/stale-session-reaper.md:85`).

**Actual state:** The design correctly copies the shipped “under three seconds” threshold, but says
that outcome makes the supervisor “end the session” and calls it the current “whole-session stop
behavior” (`docs/designs/stale-session-reaper.md:85`, `:177`). Shipped code does break the restart
loop when elapsed time is `< 3` (`src/ai_cli/session_script.py:579-608`), but the post-loop behavior
is conditional: local sessions `exit 0`, while remote sessions print a diagnostic and
`exec $SHELL`, keeping the pane available until the user exits (`:651-653`).

**Why it matters:** Implementing the AC literally removes a user-visible recovery shell for remote
sessions. Preserving it by self-`exec`ing the supervisor would instead violate D-10's lifetime-lease
topology, so the design must assign a remote-specific child/supervisor terminal state rather than
leave the choice to implementation.

**Verification note:** **CONFIRMED** numeric threshold and local/remote behavior from shipped code;
no future implementation inference is required.

**Verification command:**

```bash
sed -n '578,608p;648,653p' src/ai_cli/session_script.py
sed -n '83,89p;174,187p' docs/designs/stale-session-reaper.md
```

**Recommended fix:** Inventory the two outcomes separately. Require local fast exit to terminate the
supervisor/session. For remote fast exit, either have the stable supervisor spawn/wait on an
interactive-shell child while retaining its lease and heartbeat, or explicitly drop that behavior
with a user-approved reason. Add real local and remote fast/normal exit parity tests.

#### N-9: Watcher fusion omits the shipped per-attempt reset/rearm state machine and tests — `MAJOR` / `P1`

**Location:** `docs/designs/stale-session-reaper.md:83`, `:147-187`;
`src/ai_cli/session_script.py:227-340`, `:449-463`;
`tests/test_runaway_loop_guards.py:41-47`; `tests/test_cli.py:1832-1840`

**What D-10 claimed:** The heartbeat tick “is never restarted per agent attempt” while “its
non-heartbeat monitoring behavior is retained under the same supervisor”
(`docs/designs/stale-session-reaper.md:83`).

**Actual state:** D-10 moves the heartbeat tick and non-heartbeat monitoring into one supervisor
loop that “is never restarted per agent attempt” while saying behavior is retained
(`docs/designs/stale-session-reaper.md:83`). Shipped `start_watcher()` is deliberately killed and
recreated at every outer-loop attempt (`src/ai_cli/session_script.py:227-240`, `:449-463`). Its
counter reset supplies a ten-second grace for *each* agent startup (`:251-256`), and handling an
exit signal breaks the watcher loop (`:286-287`), relying on the next `start_watcher()` call to
rearm monitoring. D-10 defines no equivalent per-child counter reset/rearm transition, and T-1.4/
T-1.5 do not test signal-file/config-monitor parity (`docs/designs/stale-session-reaper.md:174-187`).

Phase 1's deliverables list only `tests/test_stale_session_reaper.py`, and its focused pytest gate
runs only that file (`docs/designs/stale-session-reaper.md:147-187`). Existing regression code
extracts the exact `start_watcher()` subshell by delimiters whose process boundary D-10 moves into
the supervisor (`tests/test_runaway_loop_guards.py:41-47`), while existing CLI coverage asserts the
current direct stable-script `exec` (`tests/test_cli.py:1832-1840`). Those files are absent from the
modified-file inventory and Phase 1 gate.

**Why it matters:** A literal persistent loop either loses the per-attempt startup grace—allowing
stale prompt content to trigger an early `/exit`—or stops signal/config monitoring permanently after
the first handled exit request. The Phase 1 gate can still pass while established generated-script
regressions are broken.

**Verification note:** **CONFIRMED** current state machine, missing parity ACs, and incomplete test/
file inventory. The exact refactor shape remains an implementation choice.

**Verification command:**

```bash
sed -n '83p;147,187p' docs/designs/stale-session-reaper.md
sed -n '227,256p;286,340p;447,463p' src/ai_cli/session_script.py
sed -n '41,47p' tests/test_runaway_loop_guards.py
sed -n '1832,1840p' tests/test_cli.py
```

**Recommended fix:** Specify the supervisor's monitoring state machine: reset the startup grace on
each child spawn, rearm after the signal-file action, preserve config-change detection and pacing,
and define shutdown ordering. Add a parity AC for each moved non-heartbeat behavior. Include the
existing generated-script suites—at minimum `test_runaway_loop_guards.py`, `test_cli.py`, and the
self-update/session-script-focused tests—in Phase 1's modified files and test gate.

#### N-10: Active protocol/AC text still assigns supervisor duties to “wrapper” — `MINOR` / `P2`

**Location:** `docs/designs/stale-session-reaper.md:85`, `:89`, `:103`, `:170`

**What D-10 claimed:** “The supervisor alone owns” exact-generation record revocation and “The
child performs none of that final cleanup” (`docs/designs/stale-session-reaper.md:89`).

**Actual state:** D-10 calls the replaceable process the executable “wrapper body”
(`docs/designs/stale-session-reaper.md:85`) and then says the supervisor alone owns exact-generation
record cleanup and the child performs none (`:89`). Candidate cleanup still says “A wrapper may
similarly clean up” the record (`:103`), and T-1.2 says a live “wrapper holds its generation lease”
(`:170`). In active protocol/AC text, those terms can now mean either the child body or supervisor.

**Why it matters:** A child-body implementation that follows line 103 can revoke evidence on every
ordinary child exit, directly contradicting supervisor-only cleanup. The failure is conservative for
reaping but breaks the documented continuous-evidence lifecycle and makes ownership tests ambiguous.

**Verification note:** **CONFIRMED** terminology contradiction. Historical references in D-8 and
D-10's rejected option are not included in this finding.

**Verification command:**

```bash
rg -n '\bwrapper\b' docs/designs/stale-session-reaper.md
```

**Recommended fix:** Replace the active line 103 and T-1.2 line 170 subjects with “pane-leader
supervisor.” Reserve “wrapper body” for the child and leave clearly historical/rejected-option text
as history.

### R4.4 Verification Matrix

| Finding/check | Command | Expected | Actual at `035a90b` | Reproduced? |
|---------------|---------|----------|----------------------|-------------|
| Revision pin | `git rev-parse HEAD; git diff --exit-code 035a90b -- <design and named source files>` | Exact requested commit and no blob drift | Printed `035a90b614980428c1c5972a593e514baf3be71b`; diff exited 0. | ✅ |
| N-1 / N-4 / AD-2 | `sed -n '77p;83,91p;156,160p;172,185p;598,639p' docs/designs/stale-session-reaper.md` | One pane-leader lease owner, acquire-before-write, child updates, final fence, crash tests | Printed the single-supervisor topology, unchanged PID/lease ACs, exclusive final pass, D-8 alignment, and real subprocess gates. | ✅ |
| N-5 cardinality | `sed -n '449,463p;617,653p' src/ai_cli/session_script.py; sed -n '2959,2988p' src/ai_cli/main.py` | Three update execs replaced; reattach starts no new pane process | Source has update execs at 459/638/641 and terminal remote exec at 653; existing-session launch attaches, absent-session launch creates tmux. | ✅ |
| N-6 removal | `rg -n -i 'holder process|adoption protocol|control channel|lease transfer' docs/designs/stale-session-reaper.md` | No selected-protocol handshake | Matches are the explicit absence statement, D-10/D-8 removal rationale, decision summary/lineage, and rejected option only. | ✅ |
| JA-2 / DV-1 | `sed -n '68,77p;91,109p;163,186p' docs/designs/stale-session-reaper.md` | Bound dual gates, ambiguity preserves, lease-held revalidation/kill | Printed all required gates, UNKNOWN/mismatch preservation, final exclusive lease, and mutation/race ACs. | ✅ |
| DV-2 regression | `sed -n '95,101p;162,187p' ...; sed -n '114,172p;196,247p;300,339p' src/ai_cli/process_probe.py` | Future typed capture contract matches both existing backend seams | Design printed typed identity/UNKNOWN rules and backend AC; source printed the ABC and procfs/psutil start-identity implementations to extend. | ✅ |
| JA-4 regression | `sed -n '180,187p' docs/designs/stale-session-reaper.md` | Integrated positive, mutation-negative, identity, update/crash/race controls | Printed all controls and the Phase 1 real-subprocess exit gate. | ✅ |
| N-7 signal discriminator | `sed -n '87,90p;174,187p' docs/designs/stale-session-reaper.md` | Opposite relay actions have an observable discriminator and matching tests | Same trap is assigned relay/no-relay by delivery origin; no discriminator is defined, and T-1.5 covers only supervisor-directed SIGTERM. | ✅ |
| N-8 fast-exit parity | `sed -n '578,608p;648,653p' src/ai_cli/session_script.py; sed -n '85p;177p' docs/designs/stale-session-reaper.md` | Three-second number and local/remote outcome both match | `< 3` matches; design says whole-session end, while remote shipped code execs an interactive shell after the break. | ✅ |
| N-9 / N-10 lifecycle inventory | `sed -n '83p;147,187p' ...; sed -n '227,256p;286,340p;447,463p' src/ai_cli/session_script.py; rg -n '\bwrapper\b' docs/designs/stale-session-reaper.md` | Per-attempt monitor reset/rearm, complete test gate, unambiguous ownership | Current watcher resets/rearms per attempt; design has no equivalent AC, omits existing suites, and active lines 103/170 still say wrapper. | ✅ |

**Verified: 10/10 matrix checks reproduced against commit
`035a90b614980428c1c5972a593e514baf3be71b`. Seven confirm closure/non-regression of prior items;
three reproduce the four Round 4 findings.**

### R4 Recommendations

**MUST be fixed before implementation:**

- N-7: define an implementable signal-origin/relay state machine and add the missing supervisor-only
  `SIGINT`/`SIGWINCH` real-subprocess cases.
- N-8: specify and test separate local and remote fast-exit outcomes without self-`exec`ing the
  lease-owning supervisor.
- N-9: specify the persistent monitor's per-child reset/rearm transitions, inventory all moved
  non-heartbeat behavior, and include affected existing generated-script suites in Phase 1.

**SHOULD be fixed before design approval:**

- N-10: replace active ambiguous “wrapper” ownership language with “pane-leader supervisor.”

**Can be folded into a follow-up:**

- The supervisor's inability to hot-reload is an accepted operational tradeoff, not a reap-protocol
  safety gap. Document session recreation for supervisor-code upgrades during implementation/rollout;
  no generation-rollover mechanism is required for this phase.
- Risk 5's actual tmux/zsh/Bash portability results belong to the already-required Phase 1 real
  subprocess matrix. The design must keep the current no-usable-evidence outcome for an unverified
  platform.

### Status after Round 4

The pre-Round-4 backlog is fully closed: **25 PASS, 0 PARTIAL, 0 FAIL**. D-10 genuinely resolves the
lease-holder architecture, including N-1/N-4/N-5/N-6 and the substance of approved AD-2. The design
is nevertheless **not ready to proceed to implementation** because N-7, N-8, and N-9 are new
MAJOR/P1 blockers; N-10 is an open MINOR/P2 cleanup. A further append-only re-verification is
required after those design changes. No final implement-stage ship-readiness statement is issued.

<!-- /doc:region name="scope" -->

<!-- doc:region name="round_1_findings" kind="replaceable" -->

<!-- /doc:region name="round_1_findings" -->

## Decisions Requiring Team Input

<a id="ad-1"></a>

### AD-1: Bind evidence to a session instance — `✅ Approved — (a)`

**Context:** IC-1 and F-1 show that a name-keyed heartbeat cannot prove identity across close,
rename, recreation, or tmux-server restart. The same mechanism should also define which sessions
are managed.

#### (a) Random generation token in tmux metadata and ledger

**Pros:**
- A high-entropy token created once per session instance is independent of name and tmux ID reuse.
- A tmux user option can simultaneously provide an authoritative managed marker and the token the reaper reads.
- Generation-conditional writes/deletes prevent an old wrapper or reaper from clobbering a newer instance.

**Cons:**
- Session creation and wrapper-heartbeat arguments must change together.
- Sessions created before the feature have no token and cannot be reaped automatically.

#### (b) Tmux server generation plus session ID

**Pros:**
- Uses tmux-native identity and avoids a random-token lifecycle.
- The reaper already queries the opaque session ID.

**Cons:**
- The design must derive and persist a portable, trustworthy tmux-server generation.
- Binding the heartbeat publisher to that generation adds tmux control-path reads to every writer startup.

#### (c) Keep name-only records and delete on clean exit

**Pros:**
- Smallest change to the proposed schema.
- Normal exits remove most stale records.

**Cons:**
- Crashes, forced exits, and EXIT-trap failures are precisely the cases this feature targets and still leave stale authority.
- Cleanup and recreation can race, allowing an old instance to delete or authorize against a new one.

#### Recommendation

> **Recommended (AI):** Choose (a). It gives the destructive predicate an entity identity rather than a naming convention, and the same tmux option closes F-1. Mitigate the coordinated-change cost with one session-creation helper and end-to-end tests; treat every legacy/tokenless session as observe-only/ineligible, which safely mitigates the migration limitation.
> **Decision:** ✅ Approved — (a) Random generation token in tmux metadata and ledger
<!-- decision-record: chosen-option=(a); ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->

<a id="ad-2"></a>

### AD-2: Close the final heartbeat/process TOCTOU — `✅ Approved — (a)`

**Context:** DV-1 and DV-2 show that immediate double-checking still leaves a last read-to-kill
window. The protocol needs a rule for concurrent heartbeat, pane replacement, and PID reuse.

#### (a) Generation-bound lifetime lease plus exclusive final fence

**Pros:**
- A live or merely stalled wrapper retains an OS-managed lease continuously, so a false process probe cannot reach the kill.
- Process crash releases the lease automatically; the reaper can hold an exclusive lease across the final read and kill.
- Generation binding prevents an old reaper from fencing or deleting a newly recreated session.

**Cons:**
- The current once-per-30-second subprocess must become or acquire a wrapper-lifetime lease holder.
- Cross-platform lock acquisition, inheritance, and crash-release behavior need non-mocked tests.

#### (b) Persistent two-phase reap claim with a grace interval

**Pros:**
- A claim revision plus one heartbeat interval gives an ordinary live publisher time to cancel.
- Circus restart can conservatively abandon or resume persisted claim state.

**Cons:**
- A host/wrapper stall can exceed any finite grace and resume after the final check.
- It adds cleanup state without fully eliminating the last observation-to-kill edge.

#### (c) Rely on repeated probes and a longer threshold

**Pros:**
- Minimal new state and coordination.
- Reduces race likelihood statistically.

**Cons:**
- Does not close the final TOCTOU; it only makes it less frequent.
- Cannot satisfy the exact “before being reaped” heartbeat requirement under an adversarial interleaving.

#### Recommendation

> **Recommended (AI):** Choose (a). Isolate the lease holder and lock adapter in the heartbeat/ledger module so the wrapper change is mechanical; use the already-declared cross-platform locking dependency and real subprocess tests on every supported OS to mitigate portability. Failure to acquire, inherit, or verify the lease always preserves, so both listed Cons fail closed.
> **Decision:** ✅ Approved — (a) Generation-bound lifetime lease plus exclusive final fence
<!-- decision-record: chosen-option=(a); ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->

<a id="ad-3"></a>

### AD-3: Choose a clock safe for destructive staleness decisions — `✅ Approved — (a)`

**Context:** DV-3 shows that a forward wall-clock correction creates artificial age. The ledger
needs a portable rule for process restart and host reboot.

#### (a) Same-boot monotonic time plus boot generation

**Pros:**
- Monotonic elapsed time is immune to NTP/manual wall-clock corrections and suspend-related wall jumps.
- A boot-generation mismatch has a simple fail-closed meaning: preserve until a new heartbeat arrives.

**Cons:**
- Boot identity needs a portable abstraction across Linux, macOS, and Windows.
- Records from before reboot cannot authorize a reap until republished.

#### (b) Wall time plus a persisted clock-discontinuity detector

**Pros:**
- Keeps human-readable Unix timestamps as the primary record.
- Can preserve across reboot when no discontinuity is detected.

**Cons:**
- Correct discontinuity detection across process restart, suspend, and manual changes is itself complex state.
- An undetected forward step recreates the false stale signal.

#### (c) Wall time only with future-date rejection

**Pros:**
- Matches the current proposed schema and is portable.
- No boot-state dependency.

**Cons:**
- Handles backward movement only; forward movement can authorize a premature reap.
- The safety argument depends on ordinary clock behavior rather than a fail-closed invariant.

#### Recommendation

> **Recommended (AI):** Choose (a). Hide platform differences behind a clock/boot-identity adapter; if either value is unavailable, return UNKNOWN and preserve, mitigating portability. Requiring a new heartbeat after reboot is an intentional safe delay, so the second Con does not create destructive risk.
> **Decision:** ✅ Approved — (a) Same-boot monotonic time plus boot generation
<!-- decision-record: chosen-option=(a); ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->

## Outstanding Issues to Fix

| ID | Priority | Issue | Linked finding(s) | Owner | Target |
|----|----------|-------|-------------------|-------|--------|
| I-01 | P0 | Choose and specify generation-bound managed-session/heartbeat identity. | IC-1, JA-2, F-1, AD-1 | Team + design author | Before implementation |
| I-02 | P0 | Choose and specify a final race-free claim/revalidation protocol. | JA-2, DV-1, DV-2, AD-2 | Team + design author | Before implementation |
| I-03 | P1 | Choose a staleness clock and fail-closed reboot/discontinuity behavior. | DV-3, AD-3 | Team + design author | Before implementation |
| I-04 | P1 | Add integrated positive/negative corroboration tests and mutation controls. | JA-4 | Design author | Before implementation |
| I-05 | P1 | Add lifecycle success/failure ACs for start/stop/status/run and Circus restart. | JA-5 | Design author | Before implementation |
| I-06 | P1 | Add heartbeat publication parity AC for local-ledger failure. | JA-6 | Design author | Before implementation |
| I-07 | P2 | Replace undefined criteria references. | IC-2 | Design author | Before design approval |
| I-08 | P2 | Require README/config/CHANGELOG corrections. | F-2 | Design author | Before release |
| I-09 | P2 | Specify crash durability or narrow the durability claim. | F-3 | Design author | Before implementation |

## Already-Correct Items

- ✅ Launch authority boundary: `stale-session-reaper.md:73-75`, `:121`, and ACs `:151-153`, `:159` prohibit reaper calls from launch/cleanup; `session.py:520-562` has no process probe or `kill-session`.
- ✅ The prior incident regression is structurally preserved: `tests/test_session.py:309-331` forces the former false-positive probe result and asserts launch cleanup issues no kill.
- ✅ Two-signal logic is explicitly conjunctive and rejects every named single-signal case (`stale-session-reaper.md:64-69`, `:136-138`, `:160`).
- ✅ Observe-first posture is explicit: absent config equals `mode = "observe"`, invalid config preserves, and no one-off CLI flag enables reap (`:98-105`, `:154-157`).
- ✅ Exact-ID targeting and a complete second gate pass protect against ordinary name deletion/recreation between snapshots (`:79-83`, `:139-141`).
- ✅ The existing heartbeat fact is accurate: the shipped wrapper calls `ai internal publish-heartbeat` on counter zero and every 30 ticks (`session_script.py:230-245`), and messaging currently publishes only to NATS (`messaging.py:223-226`).
- ✅ Reusing Circus adds no production dependency: `circus>=0.18.0` is already declared (`pyproject.toml:29`), and existing registration uses singleton/respawn options (`process_manager.py:148-172`).
- ✅ Circus mid-evaluation restart is fail-safe in the current stateless proposal: no kill occurs until the pass completes, and a post-kill leftover record has no candidate and is ignored (`stale-session-reaper.md:73`, `:81-83`).
- ✅ All written Phase 1/2 AC statements use a recognized EARS prefix (`When`, `If`, or `Where`); JA-5 is about missing surface/failure ACs, not malformed syntax.
- ✅ D-1 through D-6 summary choices, detail headings, fixed-key `decision-record` values, and Approval Log entries agree (`:192-199`, `:203-475`, `:496-505`).
- ✅ The target design contains no prohibited public-package references (`rg -n 'ai-core|aido' docs/designs/stale-session-reaper.md` returned no output).

## Anti-Patterns to Watch For

- Treating a session name as an instance identity. Exact-ID kill targeting does not repair evidence that was keyed to an older instance.
- Treating atomic replace as either synchronization or crash durability. It provides complete-file visibility, not a fence against a later writer and not necessarily stable storage.
- Treating a second snapshot as elimination of TOCTOU. There is always a final observation-to-action edge unless the protocol adds a claim/fence and cancellation rule.
- Mocking eligibility and kill in separate tests and calling the corroboration proven. The destructive wiring needs one integrated positive test plus single-gate negative controls.
- Reusing a historical name regex as the definition of managed sessions. The shipped session builder already supports names the regex rejects.
- Accepting `bd show` failure as evidence that an issue/comment does not exist. This audit used tracked issue/interactions artifacts and recorded the live-tool limitation.

## Sign-Off Checklist

- [x] All CRITICAL / P0 findings have linked fixes (latest design revision `1fc4166`)
- [ ] All MAJOR / P1 findings fixed OR explicitly deferred with rationale in Outstanding Issues
- [ ] All MINOR / P2 / P3 findings logged to the roadmap (even if deferred)
- [x] All AD-N decisions are APPROVED, `✅ Resolved by <agent>`, or explicitly CLOSED with rationale
- [x] Verification Matrix run on at least 5-10 findings; 10/10 reproductions recorded
- [x] At least one verification round (Round 2+) completed because Round 1 has findings
- [ ] Re-grep verification done in the final resolution round
- [x] No inline fixes were made; therefore no missing inline-fix commit hashes exist
- [x] Already-Correct Items populated with specific evidence per row
- [x] Anti-Patterns section records the protocol/audit failure modes found in this round
- [ ] User reviewed and approved sign-off

<!-- doc:region name="audit_log" kind="append_only" -->

## Audit Log

| Date | Action | Notes |
|------|--------|-------|
| 2026-08-28 | Round 1 audit pass complete | Codex audit worker; 2 CRITICAL, 7 MAJOR, 3 MINOR; 10/10 verification-matrix samples reproduced; AD-1..AD-3 pending; no target/source edits. |
| 2026-08-28 | AD-1 APPROVED — (a) Random generation token | Sergei chose the AI-recommended option for all three; no divergence. Implementation pointer: pending design-doc revision (this issue's next step). |
| 2026-08-28 | AD-2 APPROVED — (a) Generation-bound lease + exclusive fence | Sergei chose the AI-recommended option; no divergence. Implementation pointer: pending design-doc revision. |
| 2026-08-28 | AD-3 APPROVED — (a) Same-boot monotonic time + boot generation | Sergei chose the AI-recommended option; no divergence. Implementation pointer: pending design-doc revision. |
| 2026-08-28 | Round 2 verification pass complete | Codex independent verifier; 8 PASS, 4 PARTIAL, 0 FAIL across the 12-item backlog; AD-1/AD-3 PASS, AD-2 PARTIAL; 3 new findings (1 CRITICAL, 2 MAJOR); 10/10 matrix checks reproduced; no target/source edits. |
| 2026-08-28 | Round 3 resolution verification complete | Codex independent verifier; 14 PASS and 4 PARTIAL across 18 backlog/decision checks; N-4 through N-6 added (1 CRITICAL, 2 MAJOR); 10/10 matrix checks reproduced; no target/source edits. |
| 2026-08-28 | Round 4 post-D-10 verification complete | Codex independent verifier; all 25 pre-existing checks PASS; N-7 through N-10 added (3 MAJOR, 1 MINOR); 10/10 matrix checks reproduced; design remains blocked; no target/source edits. |

<!-- /doc:region name="audit_log" -->

## Appendix: Files Read

**Primary subject:**

- `docs/designs/stale-session-reaper.md` — full read, including all decisions, provenance comments, open questions, feedback placeholders, and Approval Log; verified worktree content equals `f60d4df`.
- `docs/audits/stale-session-reaper-audit.md` — full prefilled scaffold and immutable reviewer prompts.
- Round 2: `docs/designs/stale-session-reaper.md` — full read from Git object
  `383b0559fb59bdfd0f92413e835dcbccf572bbd8`;
  `git diff --exit-code 383b055 -- docs/designs/stale-session-reaper.md` confirmed the current
  worktree copy matches. The preceding `f60d4df` bullet records Round 1's historical snapshot, not
  current state.
- Round 2: `docs/audits/stale-session-reaper-audit.md` — full read of all 12 findings, the Resolution
  Pass, verification matrix, AD-1 through AD-3, Outstanding Issues, Audit Log, and reviewer prompt.
- Round 2: `src/ai_cli/session_script.py`, `src/ai_cli/process_probe.py`, and
  `src/ai_cli/process_manager.py` — full reads; wrapper self-`exec`, process identity, and Circus
  lifecycle ground truth. All three worktree copies match `383b055`.
- Round 2: canonical audit `TEMPLATE.md` and `STUB.md` from the available shared documentation
  checkout — full Round 2 structure, finding taxonomy, decision skeleton, and matrix requirements.
  The repo-local template and invocation-listed fallback were absent.
- Round 3: `docs/designs/stale-session-reaper.md` — full read at
  `c228a9bad26a2a1e66a432d32a43399290c37c7a`; design blob equality against the actual revision
  commit `fe8170b` verified, and the complete `fe8170b^..fe8170b` design diff read.
- Round 3: `docs/audits/stale-session-reaper-audit.md` — full read of Rounds 1 and 2, all 12
  original findings, N-1 through N-3, AD-1 through AD-3, recommendations, matrices, and appendices.
- Round 3: `src/ai_cli/session_script.py`, `src/ai_cli/process_probe.py`, and
  `src/ai_cli/process_manager.py` — full reads at `c228a9b`; holder topology checked against all
  three direct self-`exec` sites, ordinary heartbeat-watcher restarts, probe extension seams, and
  Circus lifecycle ground truth.
- Round 4: `docs/designs/stale-session-reaper.md` — full read at
  `035a90b614980428c1c5972a593e514baf3be71b`, including the complete D-10 revision diff from
  `1fc4166`, every rewritten lifecycle/heartbeat/Integration/Phase 1/Risk/decision location, and the
  Approval Log.
- Round 4: `docs/audits/stale-session-reaper-audit.md` — full read of all Round 1-3 findings,
  decisions, matrices, recommendations, appendices, and the complete carried backlog.
- Round 4: `src/ai_cli/session_script.py`, `src/ai_cli/process_probe.py`, and
  `src/ai_cli/process_manager.py` — full reads at `035a90b`; checked all exec/loop/watcher/fast-exit
  paths, process-identity seams, and Circus lifecycle behavior.
- Round 4: `src/ai_cli/main.py:2860-2996` — launch/reattach/new-session process-creation boundary.
- Round 4: `tests/test_session_self_update.py:1-104`, `tests/test_runaway_loop_guards.py:1-90`,
  `tests/test_cli.py:1818-1845`, `tests/test_handoff.py:1390-1445`, and
  `tests/test_iterm2.py:430-493` — existing generated-wrapper parity and structural assertions
  affected by the supervisor/child split.
- Round 4: installed tmux manual source — `pane_pid` definition and pane-exit lifecycle wording;
  consulted only as corroboration and not used for a finding that depends on unverified runtime
  behavior.

**Audit format and authoritative standards:**

- `~/projects/ai-harness/docs/audits/TEMPLATE.md` — full canonical audit structure and AD skeleton. The repo-local and invocation-designated project-template fallback paths did not exist.
- `~/projects/ai-harness/docs/audits/STUB.md` — full finding taxonomy, matrix mandate, and anti-pattern instructions.
- `~/projects/ai-harness/docs/designs/TEMPLATE.md` — full design/decision/approval structure.
- `~/projects/ai-harness/docs/procedures/task-authoring-standards.md` — full EARS, failure-path-per-public-function, parity, and test-strength rules.
- `~/projects/ai-harness/docs/procedures/reasoning-checkpoints.md` — full verification/negative-control standard.
- `~/projects/ai-harness/docs/procedures/design-doc-workflow.md` — full design status, feedback, validation, and approval workflow.
- `AGENTS.md`, `CLAUDE.md` — full repository role, public-package, CLI, portability, and test rules.

**Existing source and configuration:**

- `src/ai_cli/session.py` — full file; cleanup authority, name classifiers, builder, metadata/bg-spare handling.
- `src/ai_cli/session_script.py` — full file; heartbeat subprocess, wrapper/child topology, hot reload, EXIT/STOP paths.
- `src/ai_cli/main.py` — full file; internal heartbeat/signal-watch dispatch, session launch call site, metadata classifier, Click watcher groups.
- `src/ai_cli/messaging.py` — full file; current NATS heartbeat behavior and failure semantics.
- `src/ai_cli/process_probe.py` — full file; ABC, procfs/psutil state/identity semantics, Boolean liveness API.
- `src/ai_cli/process_manager.py` — full file; Circus daemon bootstrap, watcher add/remove/status, respawn/singleton behavior.
- `src/ai_cli/config.py` — full file; defaults, invalid-TOML behavior, XDG state paths.
- `src/ai_cli/process_hygiene.py` — full expanded call-site read; signal-watch scoring and process cleanup interactions.
- `src/ai_cli/transport.py`, `src/ai_cli/vpn_watch.py` — full expanded sibling-watcher/lifecycle reads.
- `src/ai_cli/quota.py:680-770` — quota-watch long-running Circus entry-point pattern.
- `pyproject.toml` — full dependency, entry point, platform, pytest, and lint configuration.

**Tests and public documentation:**

- `tests/test_session.py` — full file; launch authority regression and cleanup edge cases.
- `tests/test_process_probe.py` — full file; real-process state/identity/termination and denied-read behavior.
- `tests/test_process_manager.py` — full file; current watcher registration gating.
- `tests/test_messaging.py` — full file; heartbeat publication and NATS failure behavior.
- `tests/test_cli.py:180-280`, `:1550-1590`, `:2600-2665` — heartbeat and watcher CLI dispatch/missing-argument tests.
- `tests/test_handoff.py:215-940`, `:1209-1390`, `:1660-1775` — internal signal-watch and Circus sibling-pattern tests.
- `tests/test_main.py:780-1020`, `:1160-1420` — watcher dispatch and launch-cleanup call-site tests.
- `README.md`, `CHANGELOG.md` — full reads; current public cleanup/Circus claims and incident history.

**Issue and git-history evidence:**

- `.beads/issues.jsonl:1`, `:23` — complete tracked AI-CLI-iy53/AI-CLI-tdm6 records including descriptions, notes, close reason, and `comment_count=0`.
- `.beads/interactions.jsonl:202-204` — all matching issue interactions; status/close events only, no comments.
- Git commits `a223fe5`, `549abe9`, `1532f49`, `ed17415`, `f60d4df` — commit messages and relevant `session.py`/`test_session.py` diffs.

`bd show` itself could not complete because the embedded database attempted to create a lock outside
the permitted write target. The tracked JSONL records were therefore the verifiable issue snapshot;
no claim is made about untracked/live issue state beyond those artifacts.

## Appendix: Commands Run

```bash
git rev-parse HEAD
git diff f60d4df -- docs/designs/stale-session-reaper.md
git status --short
wc -l <all required and expanded files>
sed -n '<full-file chunks>' <all files listed above>
rg -n '<every named symbol and sibling watcher term>' src/ai_cli tests
jq -r 'select(.id=="AI-CLI-tdm6" or .id=="AI-CLI-iy53") | ...' .beads/issues.jsonl
rg -n 'AI-CLI-(tdm6|iy53)' .beads/interactions.jsonl
bd show AI-CLI-tdm6
bd show AI-CLI-iy53
git show ed17415 -- src/ai_cli/session.py tests/test_session.py
git show 549abe9 -- src/ai_cli/session.py tests/test_session.py
git show a223fe5 -- src/ai_cli/session.py tests/test_session.py
git show 1532f49 -- src/ai_cli/session.py tests/test_session.py
tmux -V
# The ten exact finding commands are reproduced in R1 Verification Matrix.
pytest -q tests/test_session.py::test_given_unrelated_session_with_a_false_ended_pane_result_when_cleanup_runs_then_it_never_kills_the_session tests/test_process_probe.py::test_given_psutil_denies_access_when_state_is_read_then_the_state_is_unknown
ruff check docs/designs/stale-session-reaper.md
aido validate-doc docs/designs/stale-session-reaper.md
aido validate-doc docs/audits/stale-session-reaper-audit.md
# Structural self-checks: 12 detailed findings, 12 resolution rows, 10 matrix rows,
# 3 AD headings, 9 AD option subsections, 3 Recommendation subsections, 3 decision records.
```

The focused pytest command did not execute because neither `pytest` nor
`.venv/bin/pytest` exists in this worktree environment (`zsh: command not found: pytest`). No test
result is claimed. `ruff` reported no Python files for the Markdown target, so that invocation is
also not treated as validation.

**Round 2 verification commands:**

```bash
git rev-parse --verify 383b055^{commit}
git log -1 --format='%H %ad %s' --date=iso-strict 383b055
git diff --exit-code 383b055 -- docs/designs/stale-session-reaper.md
git show 383b055:docs/designs/stale-session-reaper.md | nl -ba
nl -ba docs/audits/stale-session-reaper-audit.md
nl -ba src/ai_cli/session_script.py
nl -ba src/ai_cli/process_probe.py
nl -ba src/ai_cli/process_manager.py
git diff --exit-code 383b055 -- src/ai_cli/session_script.py src/ai_cli/process_probe.py src/ai_cli/process_manager.py
rg -n 'sole authoritative marker|same generation token|generation_token|generation-conditional' docs/designs/stale-session-reaper.md
rg -n '\b[Cc]riteria? [123]\b|\b[Cc]riterion [123]\b' docs/designs/stale-session-reaper.md
sed -n '76,96p;139,159p;563,606p;666,669p' docs/designs/stale-session-reaper.md
sed -n '447,460p;617,642p' src/ai_cli/session_script.py
sed -n '84,88p;132,159p' docs/designs/stale-session-reaper.md
sed -n '130,172p;229,247p;316,339p' src/ai_cli/process_probe.py
sed -n '167,187p' docs/designs/stale-session-reaper.md
git diff --check
```

The document-structure and ToC validators both passed after the Round 2 append.

**Round 3 verification commands:**

```bash
git status --short
git rev-parse HEAD
git show -s --format=fuller c228a9b
git show -s --format=fuller fe8170b
git log --oneline -- docs/designs/stale-session-reaper.md docs/audits/stale-session-reaper-audit.md
git diff --unified=40 fe8170b^ fe8170b -- docs/designs/stale-session-reaper.md
git diff --exit-code c228a9b:docs/designs/stale-session-reaper.md docs/designs/stale-session-reaper.md
git ls-tree c228a9b docs/designs/stale-session-reaper.md
git ls-tree fe8170b docs/designs/stale-session-reaper.md
nl -ba docs/designs/stale-session-reaper.md
nl -ba docs/audits/stale-session-reaper-audit.md
nl -ba src/ai_cli/session_script.py
nl -ba src/ai_cli/process_probe.py
nl -ba src/ai_cli/process_manager.py
rg -n 'lease|holder|control channel|adopt|lock backend|lock adapter' docs/designs/stale-session-reaper.md
rg -n 'exec "\{_session_shell\}"|start_watcher|watcher_pid' src/ai_cli/session_script.py
rg -n 'authenticated adoption|challenge|capabilit|credential|nonce|replay|single.adopter|concurrent.adopter|adoption.*timeout' docs/designs/stale-session-reaper.md
# The ten exact Round 3 matrix commands are reproduced in R3.3.
git diff --check
```

Round 3 validation passed: template/region validation reported the expected audit template and
four regions, the ToC checker resolved all 41 links, the filled-audit checker exited 0, and
`git diff --check` reported no whitespace errors. The exact validator invocations are omitted here
to avoid adding environment-specific tool names and account paths to this public-package document.

**Round 4 verification commands:**

```bash
git status --short
git rev-parse HEAD
git show -s --format='%H %s' 035a90b
git diff --exit-code 035a90b -- docs/designs/stale-session-reaper.md src/ai_cli/session_script.py src/ai_cli/process_probe.py src/ai_cli/process_manager.py
git show 035a90b:docs/designs/stale-session-reaper.md | nl -ba
nl -ba docs/audits/stale-session-reaper-audit.md
nl -ba src/ai_cli/session_script.py
nl -ba src/ai_cli/process_probe.py
nl -ba src/ai_cli/process_manager.py
git log -p -1 -- docs/designs/stale-session-reaper.md
rg -n -i 'holder|adopt|control channel|self-exec|supervisor|child body|foreground process group|SIGINT|SIGWINCH|SIGTERM|under three|start_watcher|watcher_pid|while true' docs/designs/stale-session-reaper.md src/ai_cli/session_script.py
rg -n 'new-session|respawn-pane|respawn-window|_write_stable|get_engine_script\(' src/ai_cli/main.py src/ai_cli/session.py src/ai_cli/session_script.py
rg -n 'get_engine_script|start_watcher|_exit_elapsed|refresh-template|AI_SESSION_STARTED' src tests
sed -n '2959,2988p' src/ai_cli/main.py
sed -n '227,256p;286,340p;447,463p;578,608p;617,653p' src/ai_cli/session_script.py
sed -n '41,47p' tests/test_runaway_loop_guards.py
sed -n '1832,1840p' tests/test_cli.py
rg -n '\bwrapper\b' docs/designs/stale-session-reaper.md
# The ten exact Round 4 matrix commands and actual outcomes are recorded in R4.4.
git diff --check
```

<!-- doc:region name="appendix_reviewer_prompt" kind="immutable" -->

## Appendix: Reviewer Prompts

### Round 1 Reviewer Prompt

**Model:** Codex `audit` role (`gpt-5.6-terra` per `codex/audit.config.toml`), effort `high`

**Date:** 2026-08-28

```text
You are a principal staff engineer specializing in reliable distributed process management and
CLI tooling for developer-facing agent orchestration systems. You have shipped production systems
that manage long-lived background processes and know the gap between what looks rigorous on paper
and what actually holds up under crash/restart/race conditions. You call out that gap directly.
When you cannot verify a claim, you say so explicitly rather than waving past it. Your judgment is
the product, not a summary.

You are READ-ONLY on source code, docs, and configuration EXCEPT for the audit doc itself (which
you write to) and INLINE FIXES IN THE TARGET DOC for the narrow class of stale-label / typo /
cross-reference errors where the correct value is unambiguous.

Inline fix discipline: if you fix something inline, record it in the Round 1 Resolution Pass table
as `FAIL — fixed inline` with the commit hash of your fix (note: you cannot commit yourself in this
worktree — if you make an inline fix, say so explicitly in the Resolution Pass row and leave the
commit-hash cell blank; the orchestrating session will commit it).

## Your Task

Audit docs/designs/stale-session-reaper.md at commit f60d4df against the scope below on the
following validation dimensions:

  1. Internal Consistency (IC-N): does the target contradict itself? Cross-reference every section
     against every other — e.g. does the Safety invariant in "Stale-Session Reaper" match what
     Phase 1/2's ACs actually test? Does the Data Model match what "Heartbeat recording" describes
     writing? Do the Design Decisions (D-1..D-6) match what the prose sections above them describe?
  2. Spec / AC Compliance (JA-N): does the design satisfy every one of AI-CLI-tdm6's 5 hard design
     requirements (quoted below), and does every Phase 1/2 AC follow the EARS format and the
     failure-path-AC-per-public-function rule from task-authoring-standards.md?
  3. Domain Validity (DV-N): are the tmux/session-ID revalidation protocol, the process-probe
     usage, the Circus watcher lifecycle choice, and the heartbeat-ledger atomic-write design
     defensible against the actual shipped code they extend? Could the described protocol still
     produce a false-positive reap given how tmux session IDs and pane PIDs actually behave, or how
     the existing process_probe.py / process_manager.py / session_script.py code actually works?
  4. Independent Findings (F-N, open scope): surface anything else that matters — missing edge
     cases (PID reuse between the initial eligibility check and revalidation; a session renamed but
     not recreated between checks; Circus itself restarting mid-evaluation and losing in-flight
     state; a heartbeat write racing a reap decision; what happens to an existing heartbeat record
     for a session that was cleanly and intentionally closed, not crashed — does its ledger entry
     ever get cleaned up on a NORMAL exit, or only after a reap fires, per the "removed only after a
     successful kill" rule in "Candidate evaluation and reap protocol"?), undocumented assumptions,
     contradictions with existing code, or anything a 5th occurrence of this bug class could exploit.
     Use your own senior judgment and follow any lead the audit turns up.

AI-CLI-tdm6's 5 hard design requirements (JA-N source of truth — quote exactly):
  1. Never synchronous with another session's launch — must run out-of-band (periodic Circus
     watcher), never as a side effect of cleanup_stale_sessions() or any ai c/ai p/ai g/ai cx launch.
  2. Corroborated, not single-signal — a session must fail BOTH (a) process-probe-confirmed pane
     leader ended/zombie AND (b) no heartbeat published in N minutes before being reaped.
  3. Fail closed on any ambiguity — an unreadable PID, missing heartbeat record, or uncertain read
     must preserve the session, never reap it.
  4. Configurable, off by default until proven safe — observe-only mode logging what WOULD be
     reaped, before ever actually killing, OR an explicit opt-in; must not ship with reap as the
     first deployed behavior with zero track record.
  5. Robust regression coverage — tests proving the corroboration actually gates correctly
     (single-signal-only cases must NOT reap; both-signals-confirmed-dead cases must reap), not
     just a mock of one path.

For findings that require team input (you cannot decide alone), do NOT apply a fix. Move them to
the "Decisions Requiring Team Input" section as AD-N with two or three options, pros / cons /
recommendation (each option its own subsection; bullets one per line). Every AD-N Decision line
must be followed by TEMPLATE.md's fixed-key `decision-record` HTML comment: chosen-option and,
when AI-resolved, family, concrete model ID, effort, and profile/persona. Use this exact skeleton
(from docs/audits/TEMPLATE.md — do not improvise a different shape):

<a id="ad-1"></a>

### AD-1: [Decision name] — `[PENDING | ✅ Resolved by <agent> — (x) | ✅ Approved — (x) | CLOSED]`

**Context:** <1-2 sentences>

#### (a) [Option A]

**Pros:**
- <pro>

**Cons:**
- <con>

#### (b) [Option B]

**Pros:**
- <pro>

**Cons:**
- <con>

#### Recommendation

> **Recommended (AI):** <one option + why + how every listed Con is mitigated>
> **Decision:** `PENDING`
<!-- decision-record: chosen-option=PENDING; ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->

For each finding, supply:
  - File:line reference (or doc-section).
  - Exact quoted evidence (verbatim — paraphrasing is a failure mode).
  - Why it matters (1-2 sentences on user-visible impact or architectural risk).
  - A bash verification command that demonstrates the finding (e.g. a grep proving a claimed
    cross-reference is or isn't accurate, since Phase 1/2 code does not exist yet to execute).
  - A specific recommended fix.

You MUST run a Verification Matrix on at least 5-10 of your own findings: re-run the verification
command and record the actual output. A finding without a reproduced verification command is a
hypothesis, not a fact.

## Code-review scope (lean toward over-reading)

Read all source code, schemas, configuration, and tests that the target design doc references,
proposes to modify, or makes claims about the current behavior of. This is a completeness
requirement, not a sampling exercise. Bias toward reading too much code rather than too little.

For every symbol / function / module / config key / CLI command the target references
(`cleanup_stale_sessions`, `publish_heartbeat`, `internal publish-heartbeat`, `internal
signal-watch`, `process_probe.has_ended`, `ProcfsProbe`, `PsutilProbe`, `process_manager.py`'s
existing Circus watcher registration pattern), run `grep -rn <symbol> src/ai_cli/` to surface every
call site. Add anything that surfaces to your read list before producing findings.

Record every file you read in `## Appendix: Files Read`, grouped by category.

## Files to read (read in full, do not skim — and expand this list during the run)

### Audit format (read FIRST — this is how to WRITE the audit)

0. `docs/audits/TEMPLATE.md` in this repo if present; else
   `~/projects/project-template/template/docs/audits/TEMPLATE.md`. Conform your output to it.

### Primary subject

1. `docs/designs/stale-session-reaper.md` — the design doc under audit (read in full, first).

### Authoritative requirements

2. This audit doc's own "Scope" section above — the 5 hard requirements are quoted verbatim there
   and in "Your Task" above; do not re-derive them from a different source.
3. `~/projects/ai-harness/docs/designs/TEMPLATE.md` — structural/decision-record conformance.

### Prior incident context (why this design exists)

4. `src/ai_cli/session.py` — read `cleanup_stale_sessions()` and its docstring in full: the
   AI-CLI-iy53 fix that removed all `tmux kill-session` authority from launch-time cleanup. Verify
   the new design's Integration section claim that it "remains unchanged in authority."
5. `tests/test_session.py` — the `test_given_unrelated_session_with_a_false_ended_pane_result...`
   regression test that pins the AI-CLI-iy53 invariant. Verify the new design cannot regress it.

### Existing code the design proposes to extend (lean toward over-listing)

6. `src/ai_cli/session_script.py` — `start_watcher()`'s heartbeat subshell (the ~30s `ai internal
   publish-heartbeat` call the design's "Heartbeat recording" section builds on) and the outer
   `while true` wrapper loop (including its hot-reload self-`exec`, the correlated-but-unconfirmed
   false-positive candidate the design's Open Question references).
7. `src/ai_cli/main.py` — the `internal publish-heartbeat` dispatch (~line 1312-1330) and the
   `internal signal-watch` dispatch (~line 1367+) as the existing Circus-watcher CLI pattern the
   design proposes a `session-reaper` command group should mirror.
8. `src/ai_cli/messaging.py` — `publish_heartbeat()` (~line 223) — what it actually sends today,
   to verify the design's claim that "existing event publication is unchanged."
9. `src/ai_cli/process_probe.py` — the full `ProcessProbe` ABC, `ProcfsProbe`, `PsutilProbe` — to
   verify the design's "process probe confirms every PID has ended or is a zombie" claim is
   achievable with the existing interface, and whether it already exposes what's needed or the
   design under-specifies a needed extension.
10. `src/ai_cli/process_manager.py` — the existing Circus watcher registration/lifecycle pattern
    (used today by `signal-watch`) — verify the design's Phase 2 claim that it "gains Circus
    registration/removal helpers beside existing watchers" is consistent with what's actually there.
11. `README.md` and `CHANGELOG.md` — the existing `circus`/`signal-watch` feature description, and
    this repo's public-package framing, to check the design's public-package-safe framing holds.
12. This repo's root `CLAUDE.md` — the "no proprietary names" / "no ai-core" public-package rule —
    verify the design doc itself contains no ai-core/aido references.

### Jira issues (read issue AND all comments)

13. AI-CLI-tdm6 (`bd show AI-CLI-tdm6` in this repo) — the full original request, 5 hard design
    requirements, and informal Run Ledger notes are the authoritative source for JA-N compliance.
14. AI-CLI-iy53 (`bd show AI-CLI-iy53`) — the prior incident this design closes the gap on.

## Output

Write findings into this audit doc following the Round 1 section structure:
  R1 Summary → R1 Findings (IC / JA / DV / F tables + detailed F-N subsections) → R1 Resolution
  Pass → R1 Verification Matrix → AD-N entries in "Decisions Requiring Team Input" if any →
  Already-Correct Items.

Append a row to the Audit Log when done. Update the Status Summary's cross-round counts and
ship-readiness verdict.

Never fabricate evidence to satisfy a section. Empty findings sections are honest if nothing was
found; faked findings are not. Cite file:line for every codebase claim.

## Anti-patterns (avoid)

- Code-only check that ignores the design doc's own Decision Details / Approval Log.
- Frontmatter-only status check that ignores doc-body completion signals.
- Partial read of the long design doc — the Design Decisions and Approval Log are at the bottom.
- Inline fixes without commit hashes recorded in Resolution Pass (note: you cannot commit in this
  worktree — say so explicitly rather than fabricating a hash).
- Empty Already-Correct Items list (the audit's credibility depends on it).
- Verification commands that aren't actually run.
- Under-reading the codebase — read sibling/neighbor code (e.g. `signal-watch`'s existing Circus
  pattern) for pattern-consistency, not just the named symbols.
- Treating "the design doesn't specify X" as automatically a finding — check first whether X is a
  genuinely required behavior (per the 5 hard requirements or TEMPLATE.md) or reasonably left to
  the implementation phase.
```

### Round 2 Reviewer Prompt (Re-audit)

**Model:** Codex `audit` role, ideally a fresh invocation, effort `high`

**Date:** 2026-08-28 (post-Round-1)

```text
You are a principal staff engineer specializing in reliable distributed process management and
CLI tooling (same domain as Round 1, ideally a fresh agent/session for independent verification).
You are reading the audit history of docs/designs/stale-session-reaper.md. This is a later-round
verification pass.

## Scope guard — full open MUST-fix backlog

OPEN-MUST-FIX-BACKLOG must be the full current list of every unresolved item marked MUST be fixed
before merge anywhere in the prior audit history (this doc's Round 1 section and any subsequent
round), with its ID and latest verification status. Your task is to verify that EVERY item in that
backlog, including every applicable finding (IC-N / JA-N / DV-N / F-N) and AD-N decision, has been
correctly applied to the target design doc. If the filled list omits an older open MUST-fix item
you find in the audit history, add it to your output; do not accept a narrowed scope.

You will also surface NEW issues (N-N) that prior fixes themselves introduced. Any FAIL or PARTIAL
backlog item remains in the open MUST-fix backlog and must be carried into every subsequent
re-verification and fix scope until it passes.

This is NOT an exhaustive re-audit. It is a verification pass. The Round 1 auditor already did the
broad coverage; you are confirming the Resolution Pass table's claims are actually true in the
target.

## Constraints

- APPEND-ONLY: do not edit the target doc/code in this round. If a fix is missing or incorrect,
  surface it as an N-N finding for Round 3 to apply.
- READ-ONLY on Round 1 findings: do not rewrite IC-1's wording or change F-3's severity. Verify,
  report PASS / FAIL / PARTIAL with quoted evidence.

## Verification methodology

For each open MUST-fix backlog item:
  1. Read the Resolution Pass row's "How resolved" claim.
  2. Open the target at the location the resolution claims the fix landed.
  3. Compare the actual text against the claimed fix.
  4. Report PASS (present and correct), FAIL (missing or wrong — quote what's actually there), or
     PARTIAL (name what's present and what's missing).

For AD-N decisions: locate the chosen option's reflection in the target design doc and verify it
matches the chosen option (not a different option, not a half-applied version).

For NEW issues: re-read the target sections prior fixes modified. Look for stale cross-references
introduced by those fixes, Resolution Pass claims that didn't actually land, contradictions those
fixes introduced, and draft-author scaffolding left over from the edit pass.

## Output

Write into the Round 2 section of this audit doc:
  R2 Summary → full open MUST-fix backlog verification table (PASS/FAIL/PARTIAL + evidence) →
  R2.3 AD-N verification table → R2.4 NEW issues (N-N) detailed subsections →
  R2 Recommendations (MUST / SHOULD / can-defer).

Append a row to the Audit Log. Update the Status Summary cross-round counts. Never fabricate; cite
file:line for every claim.

## Files to read

0. `docs/audits/TEMPLATE.md` in this repo, or (if not present)
   `~/projects/project-template/template/docs/audits/TEMPLATE.md`.
1. `docs/designs/stale-session-reaper.md` — read every section prior fixes touched.
2. This audit doc — its full history is your verification checklist; derive and check the complete
   OPEN-MUST-FIX-BACKLOG, not only the preceding round.
3. `src/ai_cli/session.py`, `src/ai_cli/session_script.py`, `src/ai_cli/process_probe.py`,
   `src/ai_cli/process_manager.py` — consult if a backlog claim about them might be contradicted.
```

<!-- /doc:region name="appendix_reviewer_prompt" -->
