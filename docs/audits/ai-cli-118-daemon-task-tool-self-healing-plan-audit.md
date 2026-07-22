---
title: "AI-CLI-118 daemon-restart Task* tool self-healing plan — audit"
category: audit
tags: [audit, ai-cli-118, daemon, task-tools]
status: draft
date: 2026-07-22
source: "aido-stub"
template_version: "audit-1.0.0"
---

# AI-CLI-118 daemon-restart Task* tool self-healing plan — audit

**Status:** draft

**Created:** 2026-07-22

**Auditor:** Codex review (`cx review`, effort: high) — findings incorporated by Claude

**Target commit:** `8bf0857d0ddb5f79ea27411bc03aad4fb8750d95`

<!-- aido:region name="scope" kind="replaceable" -->

## Scope

Target: `docs/plans/ai-cli-118-daemon-task-tool-self-healing-plan.md` (plan doc, `plan-1.0.0`,
task AI-CLI-118), authored in `--mode automated` by an Opus sub-agent. Scope: internal
consistency, AC-writing-practices + plan-template compliance, domain validity of the detection
heuristic (D-1..D-4) against the actual `ai-cli-utils` codebase it proposes to extend
(`quota.py`'s poll loop, `Notifier`, session/task-namespace file layout), and any independent
findings — missing prerequisites, false-positive/negative gaps in the heuristic, undocumented
assumptions about `~/.claude/daemon.log` / `~/.claude/sessions/*.json` / transcript JSONL formats.

## Status Summary

**Latest round:** Round 1

**Outstanding by severity / verdict (post-incorporation, 2026-07-22):**

| Severity | Count | Fixed / Resolved | Deferred (captured) |
|---|---:|---:|---:|
| CRITICAL / P0 | 2 | 2 | 0 |
| MAJOR / P1 | 13 | 10 | 3 |
| MINOR / P2 | 1 | 1 | 0 |
| Cosmetic / P3 | 0 | 0 | 0 |
| **Total** | **16** | **13** | **3** |

*"Deferred (captured)" = DV-3 / F-1 / F-2 — the notification-contract, atomic-dedup, and
UTC-normalization requirements for the **proactive sweep**, which AD-3 defers out of this plan; their
specs are recorded in the plan's "Deferred / follow-up work" section so the follow-up starts correct
(F-2's UTC normalization is *also* fixed inline in T-01 for the on-demand path).*

**Ship-readiness verdict (Round 1):** **NOT ready for implementation** — the original detector did not
establish Task-tool failure (DV-1), the background execution path was disabled by default (DV-2), and
the documented recovery command did not restart an existing session (DV-4, independently re-verified
against `main.py:1591`). **Post-incorporation status (2026-07-22):** AD-1..AD-4 resolved (AI
recommendations) and the plan revised — reason-coded detector (AD-1), truthful recovery + deferred
restart command (AD-2), narrowed scope with a deferred dedicated-watcher sweep (AD-3), no doc-hygiene
change (AD-4); all 16 findings fixed inline or captured in the plan's Deferred section. **The plan now
awaits human ratification of the four AI-recommended Decisions before implementation begins.** Claude
also independently corrected the upstream research doc
(`claude-code-daemon-restart-task-tool-disconnect.md` §5), which had made the same wrong
"restart via `ai c N`" claim DV-4 caught — see that doc's commit `e3823ac`.

<!-- /aido:region name="scope" -->

<!-- aido:region name="round_1_findings" kind="replaceable" -->

## Round 1 — Main Audit

**Round 1 auditor:** Codex (`cx review`, effort: high), read-only sandbox

**Round 1 date:** 2026-07-22

**Round 1 scope:** Internal consistency, EARS/AC quality, decision-record correctness, real-artifact
compatibility, current implementation compatibility, heuristic validity, and independent
reliability/security/portability risks.

Path notation below: `[target plan]` = `docs/plans/ai-cli-118-daemon-task-tool-self-healing-plan.md`,
`[research]` = `~/projects/ai-harness/docs/research/claude-code-daemon-restart-task-tool-disconnect.md`,
`[decision framework]` = `~/projects/ai-harness/docs/procedures/decision-framework.md`.

### R1 Summary

Sixteen findings reproduced: 2 CRITICAL, 13 MAJOR, and 1 MINOR. No inline changes were made
(read-only review environment).

- The three-signal rule (D-4) proves only that a session used Task tools before the restart and
  remained active afterward. It does not prove the registry remained broken.
- 8 of 9 current interactive transcripts satisfy the proposed artifact predicates around the
  observed restart; only 1 has independently investigated ground truth.
- The affected transcript contains a stronger, direct signal ignored by the plan: a post-restart
  `ToolSearch(select:Task…)` result of `"No matching deferred tools found"`.
- `ai c <N>` attaches to an existing tmux session; it does not terminate or restart the running
  client. **(Claude independently re-verified this directly against `main.py:1591` — confirmed
  correct; the research doc's workaround claim has been corrected accordingly.)**
- The proposed host loop (`ai quota watch`) is explicitly off by default and is also disabled in
  the inspected machine configuration.
- `Notifier` does not deliver native OS and remote notification concurrently under normal success,
  and `send()` returns failures instead of raising them.

### R1 Findings

#### Internal Consistency (IC-N)

| ID | Verdict | Evidence |
|---|---|---|
| IC-1 | FAIL — MAJOR | `[target plan]:58-80` says "This was root-caused" and "This is the exact signal the investigation validated"; `[research]:62-67,148-152,193-200` labels the trigger/mechanism inference and restart recovery unverified. |
| IC-2 | FAIL — MAJOR | The roadmap calls for confirming recovery and scoping a lightweight session/task-panel nudge. `[target plan]:527-544` excludes both those integration surfaces and empirical recovery confirmation while specifying a full library, CLI, and daemon sweep. |

#### Spec / AC Compliance (JA-N)

| ID | Verdict | Evidence |
|---|---|---|
| JA-1 | FAIL — MAJOR | `[target plan]:205-210,619-623` populates all human-choice cells and marks all four decisions approved automatically while simultaneously recording that human review is still awaiting. |
| JA-2 | FAIL — MAJOR | `[target plan]:141-149,464-468` specifies long-only `--json` and `--name`; public options require short and long forms. The JSON contract is also unspecified beyond "one object per session." |
| JA-3 | PARTIAL — MAJOR | All 29 ACs are syntactically EARS-shaped, but failure/schema/concurrency/boundary behavior is missing for transcript matching, numeric timestamps, background session records, JSON schema, returned notification failures, timezone equality, PID reuse, and racing marker writers. |
| JA-4 | FAIL — MINOR | Canonical template requires ACs inline with each task; `[target plan]:437-483` centralizes them. The ToC omits the real `Feedback Rounds` heading despite the template requiring every meaningful heading. |

#### Domain Validity (DV-N)

| ID | Verdict | Evidence |
|---|---|---|
| DV-1 | FAIL — **CRITICAL** | D-4 treats continued unrelated tool use as evidence of disconnection. A direct failed Task lookup exists in the incident transcript but is not part of the detector. |
| DV-2 | FAIL — MAJOR | `src/ai_cli/config.py:140-147`, `process_manager.py:135-176`, and its tests establish quota-watch as opt-in/default-off. `quota.py:538-563` has no per-iteration exception boundary around a new sweep. |
| DV-3 | FAIL — MAJOR | `notifications.py:31-80` says OS notification is fallback-only and `send()` "Never raises." `[target plan]:159-163,473-477` assumes native OS plus remote delivery and a raising/failing call. |
| DV-4 | FAIL — **CRITICAL** | `main.py:1579-1600` attaches to an existing tmux session. The fresh client launch occurs only after the running client exits and the wrapper loops through `session_script.py:443-481`. **Claude independently re-verified this directly (`main.py:1591`) — confirmed.** |
| DV-5 | FAIL — MAJOR | Real session records include `kind:"bg"`, duplicate names, numeric-millisecond `startedAt`, and `procStart`. `[target plan]:122-124,449-450` specifies only "live pid" and does not filter kind or defend against PID reuse. |
| DV-6 | FAIL — MAJOR | `[target plan]:513,587-592` describes a bounded byte-offset/last-verdict cursor modeled on `cc_usage.py`; `cc_usage.py:117-130,149-160,168-227` reads complete files and stores timestamp watermarks only (no byte offset, no last-verdict field, non-atomic write). |

#### Independent Findings (F-N)

| ID | Verdict | Evidence |
|---|---|---|
| F-1 | FAIL — MAJOR | Dedup state has no atomic claim, pending/sent state, retry policy, or corrupt-state behavior. The cited marker precedent is a non-atomic shell `touch`, unsuitable for multiple daemon/CLI writers. |
| F-2 | FAIL — MAJOR | Three clocks/formats are compared: daemon ISO-UTC, transcript ISO-UTC, and session epoch milliseconds. No AC defines normalization, equality boundaries, clock skew, or invalid/naive timestamp behavior. |
| F-3 | FAIL — MAJOR | The research explicitly leaves non-macOS applicability unknown. Missing daemon artifacts currently degrade to "all clear," so unsupported hosts would silently report health rather than "unsupported/unobservable." |
| F-4 | FAIL — MAJOR | The target doc contains internal roadmap/task-ID conventions and a `Chosen (Sergei)` personal-name decision column, in a repo that publishes to PyPI (AI-CLI-100). Whether ai-cli-utils' own `docs/` are "outward-facing" in the sense the fleet's naming-hygiene rule means is itself unresolved — flagged as AD-4 rather than unilaterally scrubbed, since every other roadmap entry in this repo already uses the same convention. |

#### DV-1: D-4 is an activity heuristic, not a disconnect detector — `CRITICAL` / `P0`

**Location:** `[target plan]:382-410,455-460,497-500`; real transcript (the incident session)

**Evidence.** The plan claims the multi-signal rule "distinguishes 'disconnected' from 'idle /
didn't need tasks.'" It does not distinguish a disconnected session from a healthy session that
simply did not need another Task call. Concrete false-positive timeline: (1) 10:00 — `TaskList`
succeeds; (2) 10:05 — daemon upgrades; (3) 10:05:10 — the healthy client re-handshakes
successfully; (4) 10:06 — `Read` succeeds, no further Task tool needed; (5) 10:10 — grace period
expires. All D-4 predicates are true, but the registry is healthy. Concrete false negatives: a
session that never used a Task tool before the restart; a disconnected session that only attempts
`ToolSearch` afterward; a cursor/tail scan that misses the old Task baseline.

The real affected transcript contains a **stronger, direct** post-restart signal the plan ignores
entirely: `{"is_error":null,"content":"No matching deferred tools found"}` — i.e. a session that
actually *tried* a Task-family tool call after the restart and got the exact failure string. On
the inspected artifacts, 8 of 9 interactive transcripts satisfy the proposed Task-before/
non-Task-after predicates; only 1 was investigated, so the other 7 cannot honestly be labeled true
or false positives either way.

**Why it matters:** a notification system built on this rule will train users that "disconnect
suspected" means "restart happened while you were working" — the exact trust-eroding
false-positive gap D-4's own rationale claims to avoid.

**Recommendation:** resolve AD-1. Treat an observed failed `ToolSearch(select:Task…)` result as
`confirmed_unavailable`. If only the three-signal correlation holds, report it as `at_risk_after_restart`,
never `disconnected_suspected`.

#### DV-4: The surfaced command does not restart the affected client — `CRITICAL` / `P0`

**Location:** `[target plan]:181-184,481-483,581-586`; `main.py:1579-1600`; `session_script.py:443-481`

**Evidence.** The plan calls `ai c <N>` the "exact recovery command." For an existing tmux
session, the launcher executes `os.execvp("tmux", ["tmux", "attach-session", "-d", "-t",
session_id])`. The fresh client launch occurs only after the running client exits and the wrapper
loops through `session_script.py`. The research doc also already said recovery was "not
exercised." **Claude independently re-verified this directly against `main.py:1591` post-audit —
confirmed correct, and corrected the upstream research doc's workaround section accordingly
(`ai-harness` commit `e3823ac`).**

**Why it matters:** a user who follows the notification is reattached to the same broken process.

**Recommendation:** resolve AD-2. Until a safe restart command exists, surface truthful steps:
exit the affected client, then relaunch (which will then take the genuine "new session" branch).
Do not call `ai c <N>` alone a restart.

*(IC-2, JA-1 through JA-4, DV-2, DV-3, DV-5, DV-6, F-1 through F-4 — full evidence, verification
commands, and recommendations preserved in [Appendix: Round 1 Full Reviewer Output](#appendix-round-1-full-reviewer-output)
below; summarized in the tables above.)*

### R1 Resolution Pass

| Finding | Status | How resolved |
|---|---|---|
| IC-1 | FIXED (plan revised) | Overview certainty language softened to `[INFERENCE]`-qualified (matches research doc); research doc's DV-4-adjacent claim already corrected by Claude (`e3823ac`). |
| IC-2 | RESOLVED via AD-3 | Scope narrowed to library + on-demand CLI; sweep deferred (plan D-1 revised). |
| JA-1 | FIXED (plan revised) | Decision Summary `Chosen (Sergei)`/`Diverged?` reset to `— (pending)`; Status → "Recommended (AI) — awaiting review"; column-semantics note added per the framework's own definition. |
| JA-2 | FIXED (plan revised) | Added `-j/--json`, `-n/--name` short forms + an explicit JSON-array object schema in T-02's CLI contract. |
| JA-3 | FIXED (plan revised) | Expanded ACs: ID-correlated tool_use/tool_result, numeric-`startedAt`, `kind` filtering + `procStart` PID-reuse defense, duplicate names, JSON schema, `NotificationResult` combinations (deferred-sweep spec), UTC normalization; failure-path ACs per public function. |
| JA-4 | FIXED (plan revised) | ACs moved inline per task; standalone "Acceptance Criteria" section removed; ToC now includes `Feedback Rounds` + every real heading. |
| DV-1 | RESOLVED via AD-1 | Reason-coded tiers (`confirmed_unavailable`/`at_risk_after_restart`/`healthy`/`unobservable`/`unsupported`) replace the boolean; `failed_task_lookup_after` added (plan D-4 revised). |
| DV-2 | RESOLVED via AD-3 | Sweep deferred; not folded into off-by-default quota-watch (plan D-2 revised). |
| DV-3 | FIXED (deferred-spec) | Deferred-sweep spec now states the real `Notifier.send()` contract (never raises; returns `list[NotificationResult]`; OS fallback-only), inspect `.success` per channel, handle all combinations. |
| DV-4 | RESOLVED via AD-2 | False `ai c <N>` "restart" claim removed; truthful exit-and-relaunch surfaced (plan D-3 revised). Root claim independently re-verified against `main.py:1591,1600,1626`. |
| DV-5 | FIXED (plan revised) | T-01 specifies `kind=="interactive"` filter, numeric-ms `started_at`, `procStart` process-identity validation, duplicate-name handling — grounded in a real `~/.claude/sessions/*.json` record. |
| DV-6 | FIXED (plan revised) | False byte-offset/last-verdict cursor claim removed; OQ-2 re-scoped to the real `cc_usage.py` watermark pattern (ISO timestamp, whole-file read); no cursor needed for the on-demand CLI. |
| F-1 | FIXED (deferred-spec) | Atomic dedup (`O_CREAT|O_EXCL`/lock, pending→sent state, retry/backoff, stale-claim cleanup) specified in the deferred-sweep section; shell-`touch` TOCTOU precedent noted as unsuitable. |
| F-2 | FIXED (plan revised) | Explicit UTC-normalization ACs covering the three clocks (daemon ISO, transcript ISO, session epoch-ms) + naive/invalid handling in T-01. |
| F-3 | RESOLVED via AD-3 | macOS-only; `unobservable`/`unsupported` are distinct verdict states that never degrade to `healthy` (plan T-01/T-02 ACs). |
| F-4 | RESOLVED via AD-4 | No doc-hygiene change (consistent with repo convention); a repo-wide scrub is a separate fleet decision, not an AI-CLI-118 blocker. |

No finding was marked "FAIL — fixed inline" during Round 1 (read-only sandbox); the "FIXED (plan
revised)" / "RESOLVED via AD-N" statuses above reflect the **post-audit incorporation pass** (Claude,
2026-07-22) into the plan doc, recorded here as the resolution trail.

### R1 Verification Matrix

10/10 of the auditor's own spot-checks reproduced on commit `8bf0857` (full commands + raw output
in [Appendix: Round 1 Full Reviewer Output](#appendix-round-1-full-reviewer-output)) — DV-4's was
independently re-run a second time by Claude post-audit with the same result.

## Decisions Requiring Team Input

<a id="ad-1"></a>

### AD-1: Detector confidence model — `PENDING`

**Context:** DV-1 shows the proposed rule cannot establish disconnection, while the transcript
contains a direct failed-lookup signal after a Task lookup is attempted.

#### (a) Two-tier verdict: direct failure is confirmed; correlation is at-risk

**Pros:** preserves proactive correlation without falsely calling it a confirmed disconnect; uses
the actual failed `ToolSearch` result when available; gives consumers explicit confidence
semantics.
**Cons:** direct confirmation occurs only after the session attempts a Task lookup; requires
separate messaging/exit-codes/dedup policy per tier.

#### (b) Keep the three-signal detector but label every result "suspected"

**Pros:** smallest change to the current plan; remains proactive.
**Cons:** "suspected disconnect" still overstates what the signals prove; likely creates broad
notifications after every qualifying daemon restart.

#### (c) Remove background correlation until another controlled reproduction

**Pros:** lowest false-positive risk; keeps evidence collection focused on direct failure.
**Cons:** silent failures remain silent until a lookup or manual check occurs; delays proactive
remediation value.

**Recommendation (Codex, Round 1):** (a), with `confirmed_unavailable`, `at_risk_after_restart`,
`healthy`, `unobservable`, and `unsupported` as distinct reason-coded states.
**Decision:** ✅ **Resolved (AI recommendation; awaiting human ratification)** — **(a) approved, refined.**
Confidence: **high.** Decision-framework criterion that moved it: **criterion 2 (blast radius)**,
backed by **criterion 1 (fast-decaying reversibility)** — the `Verdict`/reason-code contract is shared
single-source-of-truth infra consumed by the CLI, JSON output, exit codes, and any future watcher, so
it must be robust. Independently verified against source: the direct signal is real (`ToolSearch`
Task-family lookup → `"No matching deferred tools found"`, ID-correlatable in the transcript), and the
five-state set is kept but sharpened — `confirmed_unavailable` requires an **ID-correlated**
lookup→failure pair after the most recent restart; `healthy` is affirmative-only; `unobservable`
(missing/unreadable artifacts) and `unsupported` (non-macOS) must **never** degrade to `healthy` (the
F-3 fix). Revises plan D-4 and the `Verdict` shape (boolean `disconnected_suspected` → reason-coded
`state`); adds `failed_task_lookup_after` to T-01.

<a id="ad-2"></a>

### AD-2: Recovery behavior — `PENDING`

**Context:** DV-4 proves the advertised one-command restart only attaches to a live client, and
actual recovery remains unverified.

#### (a) Surface truthful manual exit-and-relaunch steps

**Pros:** matches current behavior; does not add process-control risk; can include explicit
project/session identity.
**Cons:** not one command; still relies on an unverified recovery hypothesis.

#### (b) Add an explicit, confirmed `session restart` command

**Pros:** can provide the promised single recovery command; gives process termination,
confirmation, identity, and continuation semantics a dedicated contract.
**Cons:** material scope increase; requires idle/in-flight safeguards, confirmation, signal
escalation, timeout, cross-platform tests; recovery still needs empirical validation.

#### (c) Report detection only; omit recovery guidance until reproduced

**Pros:** strictly evidence-based; cannot direct users into ineffective process handling.
**Cons:** leaves users without an immediate next step.

**Recommendation (Codex, Round 1):** (a) for this plan; treat (b) as separate work after recovery
is verified.
**Decision:** ✅ **Resolved (AI recommendation; awaiting human ratification)** — **(a) approved; reject
(c); defer (b).** Confidence: **high.** Criterion: **criterion 4 (cost of wrong-simple vs.
over-engineering) + the sequencing carve-out.** Not a plain "simpler wins": (b) would build a
process-killing restart command on an **unverified premise** — the plan's own OQ-1 admits
restart-restores-`Task*` is unconfirmed — and on a proxy signal that can false-positive (criterion 1:
killing a live foreground client mid-turn is one-way, high-blast). Verified against source
(`main.py:1591,1600,1626`): `ai c <N>` on a live session only `tmux attach-session -d`s — DV-4
confirmed. Surface truthful exit-and-relaunch steps now (exit the CC client so the session-script
wrapper relaunches a fresh `claude --continue`, then `ai c <N>` to reattach); defer the confirmed
`session restart` command until AD-1 is validated in a reproduction AND restart-restores-tools is
empirically confirmed — **filed to the plan's Deferred / follow-up section.** Revises plan D-3; removes
the false "`ai c <N>` = restart" claim everywhere.

<a id="ad-3"></a>

### AD-3: Scope and background execution host — `PENDING`

**Context:** IC-2/DV-2/F-3 show the plan exceeds the filed lightweight scope, selects a disabled
host, and does not define platform support.

#### (a) Narrow to direct transcript/session checks and the originally scoped nudge

**Pros:** matches the recorded roadmap scope; avoids a new always-on service before detector
confidence is resolved; smallest portability surface.
**Cons:** less proactive; requires integration with the session/task-panel workflow.

#### (b) Approve expanded scope and build a dedicated health watcher

**Pros:** separates health checks from quota scraping/notification policy; explicit
enablement/lifecycle/platform contract; avoids accidentally enabling quota alerts.
**Cons:** adds another supervised service; larger implementation/operational surface.

#### (c) Keep the quota-watch integration and explicitly enable it

**Pros:** reuses existing supervision/polling code; small code diff.
**Cons:** couples unrelated concerns; can enable quota scraping/alerts solely to obtain health
monitoring; existing loop latency/exception behavior needs changes regardless.

**Recommendation (Codex, Round 1):** (a) until AD-1 is validated; if proactive monitoring remains
a requirement, choose (b), not the quota watcher.
**Decision:** ✅ **Resolved (AI recommendation; awaiting human ratification)** — **(a) approved; defer
the sweep; reject (c).** Confidence: **high.** Criteria: **criterion 5 (YAGNI — speculative structural
complexity)** + **criterion 2 (concern-coupling / blast radius)** + **criterion 4 sequencing.** The
roadmap entry is a P2 "lightweight nudge" for a **once-observed** quirk (read directly:
`master-roadmap.md:5`); an always-on sweep is speculative infra whose only high-confidence trigger
(`confirmed_unavailable`, post-AD-1) is already in-band. Verified: quota-watch is off by default
(`config.py:147` / `process_manager.py:147`, `auto_start=False`), so folding (c) both mis-hosts and
couples unrelated quota-scraping side effects. Ship the library + on-demand CLI now (low-blast,
testable); defer the proactive sweep to a **dedicated** local watcher post-validation — **filed to the
plan's Deferred / follow-up section** with the DV-3/F-1/F-2 requirements captured. macOS-only for now;
non-macOS → `unsupported` (F-3). Revises plan D-1 (scope) and D-2 (host, now dedicated-watcher /
deferred).

<a id="ad-4"></a>

### AD-4: Public-package doc hygiene (internal IDs / personal-name column) — `PENDING`

**Context:** F-4 (added by Claude during incorporation — Codex's own read-only review flagged this
as sensitive and declined to reproduce exact private values, correctly, but the underlying
question needs an explicit decision, not a silent scrub). `ai-cli-utils` publishes to PyPI
(`AI-CLI-100`), and this plan doc (like the rest of the repo's `docs/roadmap/master-roadmap.md`)
uses internal task IDs (`AI-CLI-118`, `AIH-*`) and the decision-framework's own `Chosen (Sergei)`
personal-name column convention throughout.

#### (a) No change — this is normal internal-repo documentation, not outward-facing content

**Pros:** consistent with every other doc in this repo and the fleet's own established convention
(`AI-CLI-N` ids are used constantly in this repo's own roadmap); the projects-wide naming-hygiene
rule targets "anything others see" in the outward-facing-writing sense (PRs, public posts), not a
package's internal `docs/` planning material.
**Cons:** if `ai-cli-utils`'s `docs/` directory is ever browsable by PyPI consumers/GitHub visitors
in a way that surfaces internal workflow vocabulary, this could read as unpolished to an external
audience.

#### (b) Scrub/generalize this doc's decision-column + ID references before merge

**Pros:** defensively conservative for a published package.
**Cons:** inconsistent with the rest of the repo's docs (would need a repo-wide policy change to be
coherent, not a one-doc fix); loses the AIH-148 divergence-tracking value this column exists for.

**Recommendation:** (a) — this finding conflates package *code* publication (PyPI) with repo *docs*
visibility; no other roadmap/plan entry in this repo has ever been scrubbed this way, and changing
it for one doc would be inconsistent without a deliberate repo-wide decision. Flagging as PENDING
rather than self-resolving since it touches a fleet-wide convention, not just this plan.
**Decision:** ✅ **Resolved (AI recommendation; awaiting human ratification)** — **(a) approved: no
change.** Confidence: **high.** Criterion: **criterion 2 (consistency with the established repo-wide
convention)** — trivial, reversible, contained (one-liner resolution, scorer skipped per the
framework). Every other doc in this repo uses `AI-CLI-N` IDs and the decision-framework's own
`Chosen (Sergei)` column; the projects-wide naming-hygiene rule targets outward-facing *writing*
(PRs, posts, emails), not a package's internal `docs/` planning tree. Changing one doc without a
repo-wide policy would be inconsistent and would lose the AIH-148 divergence-tracking value the column
exists for. No plan change required. (If Sergei wants a repo-wide scrub policy, that is a separate
fleet decision, not an AI-CLI-118 blocker.)

## Outstanding Issues to Fix

| ID | Priority | Issue | Linked findings | Owner | Target |
|---|---|---|---|---|---|
| I-01 | P0 | Replace the disconnect truth table with an evidence-tier model selected in AD-1. | DV-1, JA-3 | Team | Before implementation |
| I-02 | P0 | Remove or replace the ineffective restart command per AD-2. | DV-4, IC-1 | Team | Before implementation |
| I-03 | P1 | Resolve plan scope, execution host, and platform support per AD-3. | IC-2, DV-2, F-3 | Team | Before implementation |
| I-04 | P1 | Align notification and dedup behavior with real return semantics and concurrency. | DV-3, F-1 | Implementer | Plan revision |
| I-05 | P1 | Specify real session/transcript schema, cursor correctness, and time normalization. | JA-3, DV-5, DV-6, F-2 | Implementer | Plan revision |
| I-06 | P1 | Correct decision state (Chosen/Diverged/status → PENDING until real human review) and resolve AD-4. | JA-1, JA-2, JA-4, F-4 | Plan author | Plan revision |

## Already-Correct Items

- ✅ The inspected daemon log contains the exact `shutting down (cause=upgrade, ...)` syntax
  specified by `parse_daemon_restarts`.
- ✅ The real registry records contain `pid`, `sessionId`, `cwd`, `startedAt`, and `name`; the
  plan's core field inventory is grounded in actual artifacts.
- ✅ Real transcripts use top-level timestamped assistant/user events with nested `tool_use` and
  ID-correlated `tool_result` records.
- ✅ The encoded transcript location resolves from registry `cwd` and `sessionId`; the current
  encoding replaces both `/` and `.` with `-`.
- ✅ A new Click command group named `session` has no existing command-group collision in `main.py`.
- ✅ The quota watcher constructs a real `Notifier`, defaults to a 300-second sleep interval, and is
  registered with singleton/respawn supervision when explicitly started.
- ✅ The plan avoids unattended automatic termination based on the heuristic (D-3's notify-only
  call was correct and survives this audit unchanged).
- ✅ All 29 acceptance criteria use a recognizable EARS trigger form (`When`, `If`, or `While`).
- ✅ The parser test strategy correctly prefers synthetic fixtures and non-mocked assertions for
  primary parsing/decision logic.
- ✅ The test plan correctly treats notification delivery as a boundary that may be spied rather
  than actually fired.
- ✅ The `cc_usage.py` cursor reference exists; the defect is its claimed semantics, not a
  fabricated filename.
- ✅ The test plan includes explicit positive and negative rows for the currently proposed
  `assess_session` guards.
- ✅ No `# pragma: no cover` addition is proposed.

## Anti-Patterns to Watch For

- Do not call correlated activity a "true positive" without ground truth. Name it a heuristic
  candidate.
- Do not equate "process exists" with "registry record belongs to that process"; PID reuse requires
  creation-time validation.
- Do not use a notification call-count test as proof of durable exactly-once behavior; include
  concurrent writers and crash points.
- Do not reuse a cursor implementation by name without checking whether it stores offsets,
  timestamps, file identity, or verdict state.
- Do not call an attach command a restart based on comments or research prose; follow the actual
  live-session branch.
- Do not convert unsupported or unobservable platforms into an all-clear verdict.
- Do not record automated recommendations in human-choice fields before a human has actually
  reviewed them.
- Do not claim exact canonical-template compliance when the requested canonical files were
  unavailable (this run hit the AIH-321 broken-worktree-symlink bug — the auditor correctly fell
  back to reading the templates from elsewhere rather than skipping the check).

## Sign-Off Checklist

- [ ] All CRITICAL / P0 findings have linked fixes
- [ ] All MAJOR / P1 findings are fixed or explicitly deferred
- [x] AD-1 through AD-4 are approved or closed with rationale (AI recommendations recorded with
  confidence + decision-framework criterion; awaiting human ratification before implementation)
- [x] Verification Matrix run on 10 findings/checks; 10/10 reproduced (+ DV-4 independently
  re-verified by Claude)
- [ ] At least one append-only verification round completed
- [ ] Final re-grep verification completed
- [x] No unrecorded inline fixes were applied
- [x] Already-Correct Items populated with specific evidence
- [x] Anti-Patterns section records audit methodology lessons
- [ ] User reviewed and approved sign-off

<a id="appendix-round-1-full-reviewer-output"></a>

## Appendix: Round 1 Full Reviewer Output

Full verbatim Codex Round 1 output (all finding detail sections, the complete verification matrix
with commands+expected+actual, Files Read, and Commands Run) preserved at:
`/private/tmp/claude-501/-Users-sergeiwallace-projects-sergei--worktrees-sw-1/79e30993-e987-48ce-bd6b-342cc23032a4/scratchpad/ai-cli-118-round1-output.txt`
(session-scratch path, not repo-tracked — the summarized tables/sections above capture every
finding's core evidence and recommendation; consult the raw file for the full quoted verification
matrix output and the complete Appendix: Files Read / Appendix: Commands Run listings if needed).

<!-- /aido:region name="round_1_findings" -->

<!-- aido:region name="audit_log" kind="append_only" -->

## Audit Log

| Date | Round | Notes |
|------|-------|-------|
| 2026-07-22 | Round 1 | Codex `cx review --effort high`, read-only. 16 findings (2 CRITICAL, 13 MAJOR, 1 MINOR), 4 AD-N decisions (AD-4 added by Claude during incorporation). Verdict: not ready for implementation. Claude independently re-verified DV-4 against `main.py:1591` (confirmed) and corrected the upstream research doc's workaround claim (`ai-harness` `e3823ac`). |
| 2026-07-22 | Round 1 incorporation | AD-1..AD-4 resolved (AI recommendations via the AIH-139 decision framework; awaiting human ratification). AD-1 → reason-coded confidence model; AD-2 → truthful exit-and-relaunch + defer confirmed restart command; AD-3 → narrow to library + CLI, defer sweep to a dedicated watcher; AD-4 → no doc-hygiene change. All 16 findings resolved or captured in the plan's Deferred section (sweep DV-3/F-1/F-2; confirmed restart command). Every DV-N re-verified directly against source (`main.py`, `notifications.py`, `quota.py`, `config.py`, `cc_usage.py`, a real `sessions/*.json`) before trusting the audit's quotes. |

<!-- /aido:region name="audit_log" -->
