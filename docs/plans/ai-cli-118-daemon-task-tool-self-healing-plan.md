---
title: Detect and remediate CC daemon-restart Task* tool disconnects
category: plan
tags: [plan]
status: draft
source: "None"
template_version: "plan-1.0.0"
task: AI-CLI-118
stub: false
---

<!-- DOC-LINK CONVENTION: link other docs as PLAIN RELATIVE Markdown links [📄 <repo>/<relpath>](<relative-path>)
     — plain link text, NO backticks; NEVER the vscode://file scheme. -->

# Detect and remediate CC daemon-restart Task* tool disconnects

**Status:** DRAFT

**Created:** 2026-07-22

**Task:** AI-CLI-118

**Research:** [📄 ai-harness/docs/research/claude-code-daemon-restart-task-tool-disconnect.md](../../../ai-harness/docs/research/claude-code-daemon-restart-task-tool-disconnect.md)

> **⚠️ Superseding finding (2026-07-22, same day) — read before resuming this plan.** The
> daemon-restart-causes-a-stale-lease root-cause model this whole plan is built on was directly
> tested and falsified (neither session restart nor daemon restart fixed the symptom). The
> actual likely root cause is unrelated to the daemon entirely — an upstream Claude Code
> GrowthBook model-gate bug (`tengu_vellum_ash`, matching
> [anthropics/claude-code#75577](https://github.com/anthropics/claude-code/issues/75577)).
> Full writeup: [📄 ai-harness/docs/bugs/aih-335-task-tool-permanently-disabled.md](../../../ai-harness/docs/bugs/aih-335-task-tool-permanently-disabled.md).
> **This plan's D-1/D-3/D-4 detection-and-remediation design may need re-examination against
> the real mechanism before implementation proceeds** — the daemon-log-correlation detection
> heuristic (D-1/D-4) and the "exit and relaunch" remediation guidance (D-3) were both designed
> around the now-superseded theory. Not yet resolved which parts of the plan still hold up;
> flag for the maintainer's review alongside the pending D-3 ratification.

**Audit:** [📄 ai-cli-utils/docs/audits/ai-cli-118-daemon-task-tool-self-healing-plan-audit.md](../audits/ai-cli-118-daemon-task-tool-self-healing-plan-audit.md) — Round 1 (Codex, 16 findings) incorporated; AD-1..AD-4 resolved (AI recommendations, awaiting human ratification).

**Mode:** `--mode automated` — Decisions were first auto-resolved via the AIH-139 decision scorer
([📄 ai-harness/docs/procedures/decision-framework.md](../../../ai-harness/docs/procedures/decision-framework.md)),
then **revised after the Round 1 audit** (see each Decision's *Revised after Round 1 audit* note and
the Approval Log). No mid-run human gate has been taken yet; all Decisions are AI recommendations
still awaiting the maintainer's review.

## Table of Contents

- [Overview](#overview)
- [Task Breakdown](#task-breakdown)
  - [T-01: Detection library (`task_tool_health.py`)](#t-01-detection-library-task_tool_healthpy)
  - [T-02: On-demand CLI command (`ai session check-tasks`)](#t-02-on-demand-cli-command-ai-session-check-tasks)
  - [T-03: Remediation guidance, config, docs](#t-03-remediation-guidance-config-docs)
- [Deferred / follow-up work (not built in this plan)](#deferred--follow-up-work-not-built-in-this-plan)
- [Batch Plan](#batch-plan)
- [Test Plan](#test-plan)
- [Risks and Mitigations](#risks-and-mitigations)
- [What must NOT regress (constraints / non-goals)](#what-must-not-regress-constraints--non-goals)
- [Scope boundary](#scope-boundary)
- [Implementation Audit](#implementation-audit)
- [Human Gates](#human-gates)
- [Decisions](#decisions)
  - [Decision Summary](#decision-summary)
  - [D-1: Detection architecture / where detection runs](#d-1)
  - [D-2: Where the periodic sweep runs](#d-2)
  - [D-3: Remediation level](#d-3)
  - [D-4: Detection confidence model (false-positive control)](#d-4)
  - [Post-hoc automated-mode digest](#post-hoc-automated-mode-digest)
- [Open Questions](#open-questions)
- [Feedback Rounds](#feedback-rounds)
- [Approval Log](#approval-log)

<!-- doc:region name="overview" kind="replaceable" -->

## Overview

Claude Code's shared background daemon (the `bg-pty-host`/`bg-spare` process pool that serves fast
session spawning, background subagents, and — per the research — the `Task*` tool family) can
restart mid-session, most often to apply an auto-update. When it does, an already-attached,
long-lived foreground CC session can **silently lose `Task*` tool availability** (`TaskList`/
`TaskCreate`/`TaskUpdate`/`TaskGet` vanish from the `ToolSearch` deferred-tool catalog) with **no
error surfaced to the agent or the user**. On 2026-07-22 (session `sw-1`) the timing was observed to
correlate: `~/.claude/daemon.log` showed an upgrade-restart
(`shutting down (cause=upgrade, …, live_workers=2)`, v2.1.216→v2.1.217) roughly 46 seconds after the
session's last successful `Task*` call, and no `Task*` call succeeded afterward; the same session's
transcript later contains a direct `ToolSearch(select:Task…)` result of `"No matching deferred tools
found"`. The exact internal mechanism (whether `Task*` serving actually routes through the daemon
lease) is an **[INFERENCE]** — verifiable on the timing, unproven on causation — per the research
doc's own framing; on-disk task data was untouched, so this is a live tool-discovery problem, not
data loss. **The root defect lives inside CC's closed-source client and cannot be fixed from this
repo.** What this plan builds, in `ai-cli-utils`, is the buildable, testable half: **detect** the
condition with an evidence-tiered proxy heuristic, and **surface** it (an explicit on-demand CLI
check that names truthful recovery steps) — never by killing a live session automatically.

The hard constraint driving the whole design: no external tool or hook can query "does `TaskList`
currently resolve for this live session" — that is internal CC agent-loop state, exposed via no
file, env var, or IPC surface. Detection is therefore necessarily a **correlation heuristic** over
three local artifacts that *are* readable: `~/.claude/daemon.log` (restart events),
`~/.claude/sessions/<pid>.json` (live-session inventory: `pid`, `sessionId`, `cwd`, numeric-ms
`startedAt`, `procStart`, `kind`), and each session's transcript JSONL under
`~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl` (last successful `Task*` call, whether the
session kept doing non-`Task*` tool work since the restart, and — the strongest signal — whether it
*tried* a `Task*` lookup afterward and got the failure string). The audit (DV-1) showed the original
three-signal correlation over-claims certainty; this revision demotes it to an `at_risk_after_restart`
tier and adds a direct `confirmed_unavailable` tier for the observed failed lookup (see [D-4](#d-4)).

> **Feedback Round 1:** Is the scope right? Too broad, too narrow? Anything missing from the goal?
> - <enter feedback here>

## Task Breakdown

> **AC quality rules** (`docs/procedures/task-authoring-standards.md` is AUTHORITATIVE — open it for the full/latest standard; this inline reminder is sync-checked against its canonical block by `aido validate-doc` and must not be edited independently):
<!-- doc:ac-rules:mirror:begin -->
- Every AC is independently testable — a test can fail if only this AC is violated.
- Every AC is falsifiable — "works correctly" is not an AC.
- Use EARS as the default for textual behavioral ACs: `When <trigger>, the system shall <response>` (event-driven); `While <state>` / `Where <feature>` (state-driven / optional); `If <condition>, then the system shall <response>` (unwanted-behavior / failure path). When a decision table, state machine, formula, executable Gherkin, property, or contract expresses the behavior more clearly, wrap it in an `<!-- ac-format: <value> ... --> ... <!-- /ac-format -->` scope (`decision-table` / `state-machine` / `formula` / `gherkin` / `property` / `contract`; unmarked ACs default to `ears`). Full per-format `ac-format` schemas are normative at `task-authoring-standards.md` § Per-Format AC Schemas — **always check that live source directly for the current schemas before relying on this reminder; this mirrored block itself can drift out of date and must never be treated as authoritative on its own.**
- At least one failure-path AC per public function changed — EARS `If <condition>, then the system shall …`, or the marked format's own negative-path convention (a decision table's infeasible-combination row, a state machine's invalid-transition row, a formula's invalid-input row).
- Replacement/refactor tasks: inventory the existing behaviors, then a parity AC for each (preserved, or intentionally dropped + reason).
<!-- doc:ac-rules:mirror:end -->

This is **net-new** work (no module being replaced), so no feature-parity inventory is required
(see [What must NOT regress](#what-must-not-regress-constraints--non-goals) for the ordinary
non-regression constraints instead). ACs live inline under each task (canonical template structure).
Each task names its file-level scope so it maps to a single Codex `cx implement` delegation.

### T-01: Detection library (`task_tool_health.py`)

**Size:** M
**Batch:** 1

Pure, dependency-light detection engine — the single source of truth every other surface calls. No
CLI, no notifications, no I/O side effects beyond reading the three artifacts. All parsing functions
take explicit inputs (text / path) so they are testable against synthetic fixtures without a live
daemon.

**Deliverables:**

- Files created: `src/ai_cli/task_tool_health.py`, `tests/test_task_tool_health.py`
- Files modified: none
- Tests added: `tests/test_task_tool_health.py::*`

**Public surface (spec the *what*, not the *how* — internal structures are the implementer's choice):**

- `parse_daemon_restarts(log_text: str) -> list[datetime]` — the tz-aware (UTC) timestamps of every
  `shutting down (cause=upgrade, …)` line in `~/.claude/daemon.log`, ascending.
- `enumerate_live_sessions(sessions_dir: Path) -> list[SessionInfo]` — one record per
  `~/.claude/sessions/<pid>.json` with `kind == "interactive"` whose `pid` is a live process **whose
  OS process-creation time matches the record's `procStart`** (PID-reuse defense), carrying at least
  `name`, `pid`, `session_id`, `cwd`, `started_at` (tz-aware UTC, parsed from numeric epoch-ms), and
  `proc_start`. Duplicate `name`s are permitted — records are keyed by `pid`, not `name`.
- `last_task_tool_call(transcript_path: Path) -> datetime | None` — tz-aware UTC timestamp of the
  last successful `Task*` (`TaskList`/`TaskCreate`/`TaskUpdate`/`TaskGet`) ID-correlated
  `tool_use`/`tool_result` in the transcript, or `None` if there is none.
- `has_tool_activity_since(transcript_path: Path, since: datetime) -> bool` — whether any successful
  **non-`Task*`** tool call appears after `since` (proof the session is still actively working).
- `last_failed_task_lookup_after(transcript_path: Path, since: datetime) -> datetime | None` — the
  **direct** signal: the tz-aware UTC timestamp of the most recent ID-correlated `ToolSearch`
  `tool_use` (whose input names the `Task*` family) whose `tool_result` is `"No matching deferred
  tools found"` after `since`, or `None` if none exists. **N-01 fix:** returns a timestamp, not a
  bare bool, specifically so `assess_session` can compare it against `last_task_tool_call` and apply
  "latest observation wins" — a failed lookup is not a permanent verdict; a later successful `Task*`
  call must clear it.
- `assess_session(session: SessionInfo, restarts: list[datetime], now: datetime, *, min_idle: timedelta) -> Verdict`
  — the heuristic; returns a `Verdict` dataclass with a reason-coded `state`
  (`confirmed_unavailable` | `at_risk_after_restart` | `healthy` | `no_issue_observed` |
  `unobservable` | `unsupported`), a human `reason: str`, `restart_at`, and `last_task_call`.
  **Latest observation wins** between `last_task_tool_call` (success) and
  `last_failed_task_lookup_after` (failure) — whichever is more recent determines
  `confirmed_unavailable` vs. `healthy`; correlation-only evidence (no direct signal either way) is
  `at_risk_after_restart`; genuinely insufficient evidence (merely idle, min-idle not elapsed) is
  `no_issue_observed` — **N-02 fix:** this is a distinct state from `healthy`, which is reserved for
  affirmative evidence only (a successful `Task*` call was actually observed, or the session's lease
  is known-fresh). Missing/unreadable artifacts return `unobservable` (never `healthy` or
  `no_issue_observed`); non-macOS returns `unsupported`.

**Acceptance criteria (T-01):**

*Daemon-log parsing:*
- [ ] When `parse_daemon_restarts` is given log text containing one or more `shutting down (cause=upgrade, …)` lines, the system shall return their tz-aware UTC timestamps in ascending order.
- [ ] When `parse_daemon_restarts` is given log text with `cause=idle_exit` shutdowns but no `cause=upgrade` line, the system shall return an empty list (only upgrade restarts count).
- [ ] If `parse_daemon_restarts` is given empty or malformed log text, then the system shall return an empty list and shall not raise.

*Session enumeration (DV-5):*
- [ ] When `enumerate_live_sessions` reads a `sessions/` dir, the system shall return one record per JSON file with `kind == "interactive"` whose `pid` is a live process, each carrying `name`, `pid`, `session_id`, `cwd`, a tz-aware UTC `started_at` parsed from the numeric-millisecond `startedAt`, and `proc_start`.
- [ ] If a `sessions/<pid>.json` has `kind == "bg"` (or any non-`interactive` kind), then the system shall exclude it from the returned records.
- [ ] When two live session files share the same `name`, the system shall return each as a distinct record keyed by `pid` (names are not assumed unique).
- [ ] If a live `pid`'s OS process-creation time does not match the record's `procStart`, then the system shall exclude that record (PID-reuse defense) and shall not raise.
- [ ] If a `sessions/<pid>.json` file is missing, unparseable, or names a dead pid, then the system shall skip that file and shall not raise.

*Transcript scanning:*
- [ ] When `last_task_tool_call` reads a transcript containing successful ID-correlated `Task*` tool calls, the system shall return the tz-aware UTC timestamp of the most recent one.
- [ ] If `last_task_tool_call` reads a transcript with no `Task*` call (or a missing/unreadable file), then the system shall return `None` and shall not raise.
- [ ] When `has_tool_activity_since` reads a transcript with a successful non-`Task*` tool call after `since`, the system shall return `True`.
- [ ] If `has_tool_activity_since` finds only `Task*` calls, or no tool calls, after `since` (or the file is unreadable), then the system shall return `False` and shall not raise.
- [ ] When `last_failed_task_lookup_after` reads a transcript containing an ID-correlated `ToolSearch`(Task-family) `tool_use` whose `tool_result` is `"No matching deferred tools found"` after `since`, the system shall return the tz-aware UTC timestamp of the most recent such failure.
- [ ] If `last_failed_task_lookup_after` finds no such correlated failed lookup after `since` (or the file is unreadable), then the system shall return `None` and shall not raise.

*Timestamp normalization (F-2):*
- [ ] When `assess_session` compares the daemon-log ISO-UTC, transcript ISO-UTC, and session epoch-millisecond timestamps, the system shall normalize all three to tz-aware UTC before comparison.
- [ ] If any timestamp input is naive (no tz) or otherwise unparseable, then the system shall treat that artifact as unobservable for that comparison and shall not raise or silently coerce it to a wrong-tz value.
- [ ] When two compared timestamps (e.g. a failure vs. a success, or `now − R` vs. `min_idle`) are exactly equal, the system shall treat the boundary as **not yet elapsed** (`now − R == min_idle` does not satisfy `≥ min_idle`; a failure and success at the identical instant do not clear each other — the failure wins, since it cannot be proven superseded).
- [ ] If `now` is earlier than a session's recorded `started_at` or the daemon's recorded restart time (clock skew / an artifact from a different clock source), then the system shall treat the elapsed-time comparison as unobservable rather than computing a negative duration.

*`assess_session` reason-coded verdicts (DV-1 / AD-1, N-01/N-02 fixes applied):*

"Latest observation wins" (N-01): compute `last_success = last_task_tool_call(...)` and
`last_failure = last_failed_task_lookup_after(..., since=restart_at)`. If both are `None`, there is
no direct observation either way — fall through to the correlation/insufficient-evidence rules
below. If only one exists, it wins. If both exist, whichever timestamp is **later** wins — a
`last_failure` after `last_success` means `confirmed_unavailable`; a `last_success` after
`last_failure` means `healthy`, clearing the earlier failure.

- [ ] **(confirmed_unavailable — direct, no later recovery)** When `last_failed_task_lookup_after` returns a timestamp for a session after its most recent upgrade-restart, and either no successful `Task*` call exists after that restart or the latest successful call predates that failure timestamp, `assess_session` shall return `state = "confirmed_unavailable"`, and this shall outrank every correlation-based verdict.
- [ ] **(healthy — recovered after a prior failure)** If a session has both a failed lookup and a later successful `Task*` call after the most recent restart (the successful call's timestamp is after the failure's), then `assess_session` shall return `state = "healthy"`, not `confirmed_unavailable` — a later successful observation clears an earlier failure.
- [ ] **(at_risk_after_restart)** When a session started before an upgrade-restart R, its last successful `Task*` call predates R with no `Task*` call after R, it has continued non-`Task*` tool activity after R, `now − R ≥ min_idle`, and no direct failed lookup is observed after R, `assess_session` shall return `state = "at_risk_after_restart"`.
- [ ] **(healthy — already reconnected, no failure observed)** If a successful `Task*` call exists after the most recent restart and no failed lookup is observed after that restart, then `assess_session` shall return `state = "healthy"`.
- [ ] **(healthy — no restart since)** If the most recent upgrade-restart predates the session's last successful `Task*` call (and no failed lookup is observed after that restart), then `assess_session` shall return `state = "healthy"`.
- [ ] **(healthy — started after restart)** If the session started after the most recent upgrade-restart, then `assess_session` shall return `state = "healthy"` (its lease is known-fresh — this is affirmative evidence, not an absence of evidence).
- [ ] **(no_issue_observed — merely idle)** If the session has had no non-`Task*` activity since the restart and no direct failed or successful `Task*` observation, then `assess_session` shall return `state = "no_issue_observed"` (NOT `"healthy"` — this is insufficient evidence, not affirmative health; cannot distinguish disconnect from idle, so it is not flagged, but it must be distinguishable downstream from a genuine affirmative pass).
- [ ] **(no_issue_observed — min-idle race)** If `now − R < min_idle` and no direct failed or successful observation exists, then `assess_session` shall return `state = "no_issue_observed"` (reason: min-idle not elapsed; NOT `"healthy"`).
- [ ] **(unobservable — F-3)** If the transcript or session artifacts required to assess a session are missing or unreadable, then `assess_session` shall return `state = "unobservable"` and shall NOT degrade to `healthy` or `no_issue_observed`.
- [ ] **(unsupported — F-3)** If the host platform is not macOS, then `assess_session` shall return `state = "unsupported"` (the `~/.claude/daemon.log` restart semantics are macOS-only for now).

**Dependencies:** None

### T-02: On-demand CLI command (`ai session check-tasks`)

**Size:** S
**Batch:** 2

A thin caller over T-01: enumerate live sessions (or one named session), assess each, print a
human-readable verdict (and `--json`/`-j` for machines / other tooling), exit non-zero when a
disconnect state (`confirmed_unavailable` or `at_risk_after_restart`) is found so the agent/user gets
a scriptable signal.

**Deliverables:**

- Files created: none
- Files modified: `src/ai_cli/main.py` (register `ai session` group + `check-tasks` subcommand;
  the `session` group is new), `tests/test_cli_dispatch.py` (dispatch wiring)
- Tests added: `tests/test_task_tool_health_cli.py::*`

**CLI contract (JA-2):**

- Options carry short and long forms: `-j/--json`, `-n/--name <N>`.
- `--json` emits a JSON array, one object per assessed session, each:
  `{"name": str, "pid": int, "session_id": str, "cwd": str, "state":
  "confirmed_unavailable"|"at_risk_after_restart"|"healthy"|"no_issue_observed"|"unobservable"|"unsupported",
  "reason": str, "restart_at": str|null (ISO-8601 UTC), "last_task_call": str|null (ISO-8601 UTC),
  "recovery": str|null}`. `recovery` is populated only for actionable (`confirmed_unavailable`/
  `at_risk_after_restart`) states; `null` otherwise. **N-02:** `no_issue_observed` is reported as its
  own state (never collapsed into `"healthy"`) so a machine consumer can distinguish "confirmed
  working" from "no evidence either way" even though both are non-actionable for exit-code purposes.
- Exit code: non-zero when any assessed session is `confirmed_unavailable` or `at_risk_after_restart`
  (or a named session is not-live); zero for `healthy`, `no_issue_observed`, `unobservable`, or
  `unsupported` (none of these are actionable, but they remain distinguishable in the reported state).

**Acceptance criteria (T-02):**
- [ ] When `ai session check-tasks` runs with one or more live sessions in a `confirmed_unavailable` or `at_risk_after_restart` state, the system shall print a per-session verdict naming the session, its state, its reason, and the truthful recovery steps, and shall exit non-zero.
- [ ] When `ai session check-tasks` runs and every live session is `healthy`, the system shall report all-clear and exit zero.
- [ ] When `ai session check-tasks --json` (or `-j`) runs, the system shall emit a JSON array — one object per assessed session, conforming to the CLI-contract schema above — to stdout.
- [ ] When `ai session check-tasks --name <N>` (or `-n <N>`) runs and exactly one live session matches `<N>`, the system shall assess only that session.
- [ ] If `ai session check-tasks --name <N>` matches more than one live session (duplicate names are permitted, DV-5), then the system shall report all matches with their distinguishing `pid`s and exit non-zero as an ambiguity error, rather than silently picking one.
- [ ] If `ai session check-tasks --name <N>` names a session with no live process, then the system shall report it as not-live and exit non-zero (distinct from an all-clear).
- [ ] If `~/.claude/daemon.log` is absent, then `ai session check-tasks` shall report `unobservable` (NOT all-clear/`healthy`) — **F-3 fix:** the daemon log is a required artifact for restart correlation; its absence means restart history cannot be determined, not that no restarts occurred, and must not silently degrade to a healthy-looking zero-exit result.
- [ ] If `ai session check-tasks` runs on a non-macOS platform, then the system shall report `unsupported` and exit zero (neither an error nor a false all-clear).

**Dependencies:** T-01

### T-03: Remediation guidance, config, docs

**Size:** S
**Batch:** 2

Truthful guided remediation (D-3, revised per AD-2): the `check-tasks` output names the **truthful**
recovery steps — exit and relaunch the affected CC *client* (which takes the genuine fresh-client
path and establishes a new daemon lease), then reattach with `ai c <N>` if detached — **not** a bare
`ai c <N>` "restart" (DV-4 proved `ai c <N>` on a live session only `tmux attach`es to the same
process). The min-idle threshold is config, not a constant (`[task_tool_health]` in config.toml).
Roadmap + a short procedure note updated in the same commit.

**Deliverables:**

- Files created: none
- Files modified: `src/ai_cli/config.py` (or the config schema) for `[task_tool_health]` defaults
  (`min_idle_seconds` only — **N-03 fix:** the `enabled` flag is NOT part of this task's scope; it
  belongs to the deferred watcher's own spec, see [Deferred / follow-up work](#deferred--follow-up-work-not-built-in-this-plan),
  and must not be added as dead configuration before that watcher exists),
  `docs/roadmap/master-roadmap.md` (mark AI-CLI-118 progress), a short note in the relevant
  procedure/README
- Tests added: `tests/test_task_tool_health.py::TestConfigDefaults`, remediation-string assertions
  folded into T-02 tests

**Acceptance criteria (T-03):**
- [ ] When `[task_tool_health].min_idle_seconds` is set, `assess_session` shall use it as `min_idle` (config over code; a documented default applies when unset).
- [ ] If `[task_tool_health].min_idle_seconds` is absent or malformed, then the system shall fall back to the documented default and shall not raise.
- [ ] When a disconnect state is reported by the CLI, the surfaced recovery text shall describe the truthful exit-and-relaunch-the-client steps for the affected session, and shall NOT present a bare `ai c <N>` invocation as a "restart".

**Dependencies:** T-01, T-02

<!-- /doc:region name="overview" -->

<!-- doc:region name="decisions" kind="replaceable" -->

## Decisions

### Decision Summary

> **Column semantics (per [decision framework](../../../ai-harness/docs/procedures/decision-framework.md)):**
> `Recommended (AI)` = the AI's final recommendation (corrections kept in Rationale). `Chosen (Maintainer)`
> = the **human's** final pick — left `— (pending)` until the maintainer actually reviews (these are
> AI-auto-resolved recommendations, revised post-audit, **not** human-approved). `Diverged?` cannot be
> computed until a human choice exists → `— (pending)`.

| # | Decision | Options Considered | Recommended (AI) | Chosen (Maintainer) | Diverged? | Rationale | Status |
|---|----------|-------------------|------------------|-----------------|-----------|-----------|--------|
| D-1 | Detection architecture / where detection runs | (a) SessionStart hook nudge only, (b) on-demand CLI only, (c) library + on-demand CLI + periodic background sweep, (d) library + on-demand CLI now, sweep deferred | (d) — N-04: was mislabeled "(a-narrowed)" pre-fix, now its own option | (d) | No | **Original rec was (c)** (crit 2 blast-radius + silent-mid-session value). Revised after Round-1 audit (AD-3): roadmap AI-CLI-118 is a P2 lightweight nudge for a once-observed quirk; an always-on sweep is speculative structural complexity (crit 5) whose only high-confidence trigger (`confirmed_unavailable`) is already in-band — ship the low-blast library + CLI, defer the sweep (crit 4 sequencing). | ✅ Approved (maintainer, 2026-07-22) |
| D-2 | Where the periodic sweep runs (when built) | (a) new dedicated Circus watch service, (b) fold into the existing `ai quota watch` loop, (c) ai-core scheduled JobDef on Hetzner | (a) dedicated watcher — **but the sweep itself is deferred** (see D-1/AD-3) | (a) | No | **Original rec was (b)** fold-into-quota-watch. Revised after Round-1 audit (AD-3/DV-2): quota-watch is off by default and folding couples unrelated quota-scraping side effects (crit 2); if/when proactive monitoring is validated, build a dedicated watcher (a), not the fold. (c) wrong host (crit 2). | ✅ Approved (maintainer, 2026-07-22) |
| D-3 | Remediation level | (a) notify-only, (b) guided restart command, (c) fully automatic unattended restart | (a) surface truthful **exit-and-relaunch** recovery steps; reject (c); defer a confirmed `session restart` command | — (spike done, awaiting the maintainer) | — (pending) | Crit 1 (reversibility) + blast radius reject (c). Revised after Round-1 audit (AD-2/DV-4): `ai c <N>` on a live session only `tmux attach`es, so the "one-command restart" was false — surface truthful steps; defer a real restart command until the restart-restores-tools premise is empirically confirmed (crit 4 sequencing). **The maintainer pushed back (2026-07-22):** exit-and-relaunch shouldn't be the steady-state answer if an in-place daemon reconnect is achievable — spun out as [AIH-335](../../../../../ai-harness/docs/roadmap/master-roadmap.md). **Spike result:** no known safe in-place reconnect found (external research + 2 corroborating GitHub issues, independently re-verified); recommendation reverts to (a), now evidenced rather than assumed. | ⏸ Spike done — awaiting the maintainer's ratification |
| D-4 | Detection confidence model (false-positive control) | (a) restart-correlation alone, (b) three-signal boolean `disconnected_suspected`, (c) live `ToolSearch` probe, (d) reason-coded evidence-tiered model | (d): `confirmed_unavailable` / `at_risk_after_restart` / `healthy` / `no_issue_observed` / `unobservable` / `unsupported`, "latest observation wins" (N-01) — was mislabeled "(a-tiered)" pre-fix, now its own option | (d) | No | **Original rec was (b)** three-signal boolean. Revised after Round-1 audit (AD-1/DV-1): the correlation proves only "used Task* before, worked after", not a broken registry; the incident transcript has a direct failed-lookup signal the boolean ignored. Further revised after Round-2 (N-01/N-02): split `healthy` (affirmative-only) from a new `no_issue_observed` (insufficient evidence) state, and made the direct-failure signal a timestamp so a later success clears it. Reason-coded tiers are shared contract infra (crit 2) — demote correlation, add the direct tier, never degrade unobservable/unsupported to healthy/no_issue_observed. (c) infeasible. | ✅ Approved (maintainer, 2026-07-22) |

<a id="d-1"></a>

#### D-1: Detection architecture / where detection runs — Recommended (AI): (a-narrowed) library + on-demand CLI; sweep deferred

**Context.** The failure is silent and happens *mid-session* to an already-running session. That
single fact constrains the whole architecture: whatever detects it must be able to look at a live
session *after* a daemon restart, without the session itself having to notice.

##### (a) SessionStart hook nudge only

**Pros:**

- Cheapest to build; mirrors the existing `task-panel-startup.sh` / `context-high-notice.sh` hook pattern.

**Cons:**

- The session cannot detect its *own* future mid-session drop: at SessionStart its own lease is fresh, so the condition is by definition absent for itself.

##### (b) On-demand CLI command only

**Pros:**

- Simple, scriptable, no background process; the agent or user can check explicitly, and any hook (SessionStart, `/task-panel`) can call it to sweep *other* live sessions.

**Cons:**

- Requires someone (or a hook) to run it — but the failure mode is silent, so nobody necessarily knows to look. Delivers diagnosis-on-request plus the direct `confirmed_unavailable` catch, not a fully unattended proactive sweep.

##### (c) Detection library + on-demand CLI + periodic background sweep

**Pros:**

- The periodic sweep is the only surface that catches a silent mid-session drop with *no* human or hook prompt at all.

**Cons:**

- Most surface area: a library + a command + an always-on poll-loop edit and its dedup/notification/lifecycle machinery — see the audit's DV-2/F-1/F-3 for the operational-complexity cost.

##### (d) Detection library + on-demand CLI now; periodic sweep deferred to a dedicated watcher — **N-04 fix: this option was previously mislabeled "(a-narrowed)"; it is its own distinct option, not a variant of (a)**

**Pros:**

- Ships the library + CLI's `confirmed_unavailable` direct catch and explicit on-demand check now,
  low blast-radius, fully testable against synthetic fixtures.
- Keeps the door open: the on-demand CLI is exactly what a future sweep or hook would call.

**Cons:**

- No proactive, fully-unattended catch of a silent mid-session drop until the deferred watcher is
  built (accepted — see Rationale).

##### Recommendation

> **Original decision (pre-audit):** ✅ Approved — auto (2026-07-22); confidence: high — (c) library + CLI + periodic sweep. Rationale: criterion 2 (blast radius) + the silent-mid-session nature.

> **Revised after Round 1 audit (2026-07-22) — AD-3:** Recommend **(d): detection library +
> on-demand CLI now; defer the periodic sweep** to a dedicated watcher, built only after the
> detection confidence model (D-4) is validated in a real reproduction. Confidence: high. Criteria
> moved: **criterion 5** (the always-on sweep is speculative structural complexity for a
> *once-observed* quirk — the roadmap asked for a "lightweight nudge", P2) + **criterion 4 sequencing**
> (ship the low-blast, testable library + CLI that delivers the `confirmed_unavailable` catch and the
> explicit check; add the proactive sweep as separate infra once the premise is proven — filed as a
> follow-up task). The SessionStart hook from (a) remains scoped out for *self*-detection, but the
> on-demand CLI (b) is exactly what a hook would call to sweep *other* sessions — (d) is (b) shipped
> now, with (c)'s sweep explicitly deferred rather than built.

> **The maintainer's decision (2026-07-22):** ✅ Approved as recommended — (d).

---

<a id="d-2"></a>

#### D-2: Where the periodic sweep runs — Recommended (AI): dedicated watcher, but the sweep is deferred

**Context.** *If/when* a sweep is built (deferred per D-1), it must run *on the Mac*, because
`~/.claude/daemon.log` and `~/.claude/sessions/*.json` are local to the host running the sessions.

##### (a) New dedicated Circus watch service

**Pros:**

- Clean separation of concerns; explicit enablement / lifecycle / platform contract; does not entangle task-health with quota alerting.

**Cons:**

- A second Circus service, a second PID guard, a second start/stop lifecycle.

##### (b) Fold into the existing `ai quota watch` loop

**Pros:**

- Reuses an already-supervised local loop already wired to `Notifier`.

**Cons:**

- `ai quota watch` is **off by default** (`[quota_watch].auto_start = false`, DV-2), so the sweep would not run on this machine without separately opting into quota alerts; folding couples task-health to quota-scraping side effects; the loop has no per-iteration exception boundary for a new sweep (DV-2).

##### (c) ai-core scheduled JobDef on Hetzner

**Pros:**

- Matches the fleet's "recurring server tasks use ai-core scheduling" rule.

**Cons:**

- Wrong host: the daemon.log + session files live on the Mac, invisible to a Hetzner JobDef.

##### Recommendation

> **Original decision (pre-audit):** ✅ Approved — auto (2026-07-22); confidence: medium — (b) fold into `ai quota watch`.

> **Revised after Round 1 audit (2026-07-22) — AD-3 / DV-2:** The sweep is **deferred** (D-1). When
> built, recommend **(a) a dedicated watcher**, not the quota-watch fold. Confidence: high. Criterion:
> **criterion 2** — (c) is the wrong host, and (b)'s concern-coupling has a concrete cost (enabling
> quota scraping/alerts solely to obtain health monitoring, plus the missing exception boundary). The
> original fold-into-(b) recommendation was reversed by the DV-2 evidence that quota-watch is
> off-by-default and unrelated to task-health.

> **The maintainer's decision (2026-07-22):** ✅ Approved as recommended — (a), with the sweep itself still deferred per D-1.

---

<a id="d-3"></a>

#### D-3: Remediation level — Recommended (AI): truthful exit-and-relaunch guidance; reject (c); defer a confirmed restart command

**Context.** Once detected, how forcefully should the system respond? The recovery action is
"restart the affected session," which means terminating a live foreground CC client — and detection
is a *proxy* heuristic that can be wrong. DV-4 additionally proved the originally-advertised
`ai c <N>` "restart" only *attaches* to the live process.

##### (a) Notify-only / surface truthful recovery steps

**Pros:**

- Zero risk to live work; the human/agent decides whether and when to act; can name explicit project/session identity.

**Cons:**

- Requires a human/agent to act; does not literally auto-heal; still relies on the (unverified) hypothesis that a client relaunch restores `Task*`.

##### (b) Add an explicit, confirmed `session restart` command

**Pros:**

- Could provide a genuine single recovery command with process termination, confirmation, and continuation semantics.

**Cons:**

- Material scope increase (idle/in-flight safeguards, confirmation, signal escalation, timeout, cross-platform tests); and it would be built on an **unverified premise** — the plan's own OQ-1 says restart-restores-`Task*` is not yet confirmed.

##### (c) Fully automatic unattended restart

**Pros:**

- Truly hands-off "self-healing."

**Cons:**

- One-way, high-blast-radius: killing a foreground CC process mid-turn can lose an in-progress response; on a **false positive** it kills a healthy session. Unacceptable.

##### Recommendation

> **Original decision (pre-audit):** ✅ Approved — auto (2026-07-22); confidence: high — (a) notify-only + (b) surfaced restart command `ai c <N>`; reject (c).

> **Revised after Round 1 audit (2026-07-22) — AD-2 / DV-4:** Recommend **(a): surface truthful
> exit-and-relaunch recovery steps** (exit the CC client — e.g. `/exit` — so the session-script
> wrapper relaunches a fresh `claude --continue` client with a new daemon lease, then `ai c <N>` to
> reattach if detached); **reject (c)**; **defer (b)** a confirmed `session restart` command as
> separate work, to be built only after (i) the D-4 confidence model is validated and (ii) the
> restart-restores-`Task*` premise is empirically confirmed — filed as a follow-up task. Confidence:
> high. Criteria: **criterion 1** (reversibility — no irreversible process kill on a proxy signal) +
> **criterion 4 sequencing** (do not build a high-blast restart command on an unverified premise).
> The false "`ai c <N>` = restart" claim is removed everywhere (DV-4).

> **The maintainer's decision (2026-07-22):** ⏸ **Blocked, not approved as-is.** Pushback: exit-and-relaunch
> shouldn't be the steady-state remediation if an in-place daemon-lease reconnect is achievable —
> "auto-restarting is not a good solution... I feel like it should be possible" — and this was never
> actually investigated, only assumed infeasible by scope-narrowing after DV-4. Spun out as a spike:
> [AIH-335](../../../../../ai-harness/docs/roadmap/master-roadmap.md) (ai-harness roadmap) — local
> lever check done (no CLI flag; `/doctor` in-session command untested, flagged as a candidate) +
> Codex `cx research --effort high` running for external evidence (GitHub issues, docs, community
> internals write-ups). **D-3 stays open until AIH-335 reports back.** If a safe in-place reconnect is
> confirmed, D-3 is revised to prefer it (and D-3(b)'s deferred confirmed-restart-command may also
> unblock, since AIH-335's finding would resolve the "unverified premise" objection that deferred it).
> If AIH-335 confirms no lever exists, (a) ships as originally recommended with that negative result
> as documented evidence.

> **AIH-335 spike result (2026-07-22):** external research (Codex `cx research --effort high`,
> two most load-bearing citations independently re-verified by Claude against the live GitHub
> API) found **no known safe in-place reconnect** — no CLI flag, no documented recovery command,
> no confirmed signal/env-var mechanism; every documented reconnect/`respawn` facility is scoped
> to configured MCP servers or dispatched background agent sessions, never an ordinary
> foreground session's native tool registration. Two real GitHub issues
> ([#80034](https://github.com/anthropics/claude-code/issues/80034),
> [#75894](https://github.com/anthropics/claude-code/issues/75894)) substantially corroborate
> this — same symptom, same daemon-log vocabulary, open/unresolved upstream. `/doctor` remains
> an untested candidate (documented only as install/settings diagnostics, no reconnect claim
> either way) — flagged for a future disposable-session test, not attempted here (testing it
> against a live disconnected session risks losing that session). Full findings appended to
> [📄 ai-harness/docs/research/claude-code-daemon-restart-task-tool-disconnect.md](../../../../../ai-harness/docs/research/claude-code-daemon-restart-task-tool-disconnect.md)
> §7. **D-3 recommendation unchanged from pre-spike (a), now evidenced rather than assumed —
> awaiting the maintainer's actual ratification, not auto-applied.**

---

<a id="d-4"></a>

#### D-4: Detection confidence model (false-positive control) — Recommended (AI): reason-coded evidence tiers

**Context.** The heuristic is a proxy, not certain. The audit (DV-1) showed the original three-signal
*boolean* over-claims: continued unrelated tool use after a restart does not prove the `Task*`
registry stayed broken (concrete false-positive: a healthy session that re-handshaked and simply did
not need another `Task*` call). The incident transcript, meanwhile, contains a *direct* signal the
boolean ignored — a `ToolSearch(select:Task…)` result of `"No matching deferred tools found"`.

##### (a) Flag on daemon-restart correlation alone

**Cons:** fires on any session that merely *hasn't needed* a `Task*` call since a restart — a large false-positive class.

##### (b) Multi-signal boolean `disconnected_suspected` (the original pick)

**Pros:** requires positive evidence the session is still working before flagging.
**Cons (DV-1):** still cannot *establish* disconnection — it proves only "used `Task*` before the restart, kept working after." Labels a correlation as a confirmed disconnect and ignores the stronger direct signal.

##### (c) Live `ToolSearch` probe of the session

**Cons:** infeasible — no external surface can query another live session's tool registry (the hard constraint).

##### (d) Reason-coded evidence-tiered model — **N-04 fix: this option was previously mislabeled "(a-tiered)"; it is its own distinct option, not a variant of (a)**

**Pros:**

- `confirmed_unavailable` — an ID-correlated failed `Task*` `ToolSearch` after the most recent restart, when not superseded by a later successful call (N-01: "latest observation wins") — is a *direct* observation, not a correlation; it outranks correlation-only evidence.
- `at_risk_after_restart` — the demoted three-signal correlation — is surfaced with honest, lower confidence, never as a confirmed disconnect.
- `healthy` is reserved for **affirmative** evidence only (a real successful `Task*` observation, or a known-fresh lease); `no_issue_observed` is a distinct state for genuinely insufficient evidence (merely idle, min-idle race) so it is never conflated with a confirmed pass (N-02 fix); `unobservable` (missing/unreadable required artifacts) and `unsupported` (non-macOS) never degrade to `healthy` or `no_issue_observed` (fixes F-3).

**Cons:**

- Direct confirmation only appears after the session attempts a `Task*` lookup; requires six distinct
  states' worth of messaging/exit-code semantics instead of a boolean.

##### Recommendation

> **Original decision (pre-audit):** ✅ Approved — auto (2026-07-22); confidence: high — (b) multi-signal boolean.

> **Revised after Round 1 audit (2026-07-22) — AD-1 / DV-1, further revised after Round 2 (N-01/N-02):**
> Recommend the **(d) reason-coded evidence-tiered model** with states `confirmed_unavailable` /
> `at_risk_after_restart` / `healthy` / `no_issue_observed` / `unobservable` / `unsupported`.
> Confidence: high. Criteria: **criterion 2** (the `Verdict`/reason contract is shared
> single-source-of-truth infra consumed by the CLI, JSON output, exit codes, and the future watcher —
> a fast-decaying two-way door, **criterion 1**). This replaces the boolean `disconnected_suspected`
> with `Verdict.state`; the direct `last_failed_task_lookup_after` signal is added to T-01, returning
> a timestamp (not a bare bool) specifically so a later successful call can supersede an earlier
> failure (Round 2 found the original bool-based design couldn't express this — N-01). `healthy` and
> `no_issue_observed` were split apart after Round 2 found the original design conflated "confirmed
> working" with "no evidence either way" under one `healthy` label (N-02). The accepted downside — a
> genuinely-disconnected but idle session reports `no_issue_observed` (not flagged) until it does more
> tool work or attempts a lookup — is the safe direction to err, and is now at least honestly labeled
> as low-evidence rather than misreported as a confirmed pass.

> **The maintainer's decision (2026-07-22):** ✅ Approved as recommended — (d).

### Post-hoc automated-mode digest

All four Decisions were **first** auto-resolved with no mid-run gate (per
[📄 ai-harness/skills/doc/automated-mode.md](../../../ai-harness/skills/doc/automated-mode.md)),
**then revised after the Round 1 audit** (Codex `cx review`, incorporated + re-resolved via the
decision framework). Post-revision confidences: D-1/D-3/D-4 **high**, D-2 **high** (was medium). All
four are AI recommendations that **remain awaiting the maintainer's review** — nothing is human-approved, and
the Decision Summary's `Chosen (Maintainer)` / `Diverged?` columns are `— (pending)` accordingly (JA-1).
Two decisions defer robustness that must be filed as tasks: the proactive sweep (D-1/D-3/AD-3) and a
confirmed `session restart` command (D-3/AD-2). No metered research was fired.

<!-- /doc:region name="decisions" -->

<!-- doc:region name="task_breakdown" kind="replaceable" -->

## Deferred / follow-up work (not built in this plan)

These are intentionally deferred per the audit-revised Decisions, with their requirements captured so
the follow-up work starts from a correct spec. **Both should be filed as roadmap follow-up tasks.**

1. **Proactive periodic sweep + notify + dedup** (deferred per D-1 / D-2 / AD-3). Build only after the
   D-4 confidence model is validated in a real reproduction. Requirements when built:
   - Run in a **dedicated** local (macOS) Circus watcher with its own enable flag
     (`[task_tool_health].enabled`, default off) and lifecycle — **not** folded into `ai quota watch`
     (AD-3 / DV-2).
   - Fire notifications **only on `confirmed_unavailable`** (the high-confidence tier) to avoid
     cry-wolf on the `at_risk` proxy, until the model earns broader trust.
   - Notification delivery must use the **real** `Notifier.send()` contract (DV-3): it returns
     `list[NotificationResult]` and **never raises**; OS-native delivery is a **fallback that fires
     only when all primary channels fail or none are configured** (not concurrent with remote). The
     sweep must inspect `NotificationResult.success` per channel — never wrap `send()` in a
     raises-on-failure assumption — and handle every combination (all-success / partial / all-fail /
     OS-fallback-only).
   - **Atomic** dedup (F-1): claim a `(session, restart-event)` marker with `O_CREAT|O_EXCL` (or a
     file lock), with explicit pending→sent state, retry/backoff on transient send failure, and
     stale-claim cleanup — the shell-`touch` precedent in `statusline-command.sh` is a non-atomic
     TOCTOU check unsuitable for multiple daemon/CLI writers.
   - UTC normalization across all three clocks (F-2), as in T-01.
2. **Confirmed `session restart` command** (deferred per D-3 / AD-2). Build only after the
   restart-restores-`Task*` premise is empirically confirmed (OQ-1). Requirements: idle/in-flight
   safeguards, explicit confirmation, signal escalation + timeout, session identity, and cross-platform
   tests — a genuine process-control contract, not the `ai c <N>` attach that DV-4 debunked.

## Batch Plan

| Batch | Tasks | Focus | Exit gate |
|-------|-------|-------|-----------|
| 1 | T-01 | Detection library | All T-01 ACs checked + `ruff check` + `ruff format --check` + `pytest tests/test_task_tool_health.py` green |
| 2 | T-02, T-03 | On-demand CLI + remediation/config/docs | All T-02/T-03 ACs checked + full `pytest` green + roadmap updated |

> **Feedback Round 1:** Does the batching make sense? Should any tasks be reordered, split, or merged?
> - <enter feedback here>

## Test Plan

- **Fixtures over live daemon.** Every heuristic AC is exercised by constructing a synthetic
  `daemon.log` string, a temp `sessions/` dir with crafted `<pid>.json` files (use the current
  process pid + its real creation time for the "live, matching-procStart" case; an obviously-dead pid
  for the "dead" case; a live pid with a mismatched `procStart` for the PID-reuse case; a
  `kind:"bg"` record for the exclusion case; two same-`name` records for the duplicate-name case), and
  a temp transcript JSONL with hand-written ID-correlated `tool_use`/`tool_result` entries at
  controlled timestamps — including the `ToolSearch`(Task-family) → `"No matching deferred tools
  found"` pair for the `confirmed_unavailable` case. No test triggers or waits on a real CC daemon
  restart.
- **Non-mocked behavioral assertions.** Assert on the real return values of `parse_daemon_restarts` /
  `enumerate_live_sessions` / `last_task_tool_call` / `has_tool_activity_since` /
  `failed_task_lookup_after` / `assess_session` against the fixtures — not "a mock was called."
- **Reason-code truth-table coverage for `assess_session`.** One dedicated row per state/branch:
  `confirmed_unavailable`, `at_risk_after_restart`, the four `healthy` branches (reconnected /
  no-restart-since / started-after / merely-idle), the min-idle race, `unobservable`, and
  `unsupported` — so a regression in any single guard fails a dedicated test. Assert that
  `confirmed_unavailable` outranks a simultaneously-satisfied `at_risk` correlation.
- **Timestamp normalization** is asserted directly: mixed ISO-UTC (daemon/transcript) + epoch-ms
  (session) inputs resolve to the same tz-aware UTC instants; a naive/invalid timestamp yields
  `unobservable` for that comparison, never a silent wrong-tz coercion.
- **CLI contract** tests assert the short/long option forms, the JSON-array schema shape, and the
  exit-code mapping (non-zero on `confirmed_unavailable`/`at_risk`/not-live; zero otherwise).
- **Exit gate (runnable predicate, not prose):** `ruff check .` + `ruff format --check .` +
  `pytest tests/test_task_tool_health.py tests/test_task_tool_health_cli.py tests/test_cli_dispatch.py`
  all green, and the full `pytest` suite green before Batch 2 closes.

*(Sweep/notify/dedup tests belong to the deferred follow-up, not this plan — see
[Deferred / follow-up work](#deferred--follow-up-work-not-built-in-this-plan).)*

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| `at_risk_after_restart` false positive on a healthy session | D-4 reason tiers surface it as *lower-confidence*, never a confirmed disconnect; the on-demand CLI is opt-in (a dismissible report, never a killed session, D-3). The high-confidence `confirmed_unavailable` tier requires a direct failed lookup. |
| False negative (real disconnect on an idle session) | Accepted + documented; the direct `confirmed_unavailable` signal catches it the moment the session attempts a `Task*` lookup; safer direction to err than false-positive. |
| Real session schema drift (`kind`, numeric `startedAt`, `procStart`, duplicate names) | Parsers are grounded in the actual on-disk schema (DV-5) and defensive (skip-and-continue on unparseable/mismatched input); a format change degrades to "session skipped" / `unobservable`, never a false `healthy`. |
| Exact internal mechanism (does `Task*` route through the daemon lease?) is unconfirmed | The heuristic is correlation-based and the direct tier is observation-based; neither asserts the mechanism. Documented as accepted `[INFERENCE]` uncertainty (Overview + Scope boundary + OQ-3). |
| `ai c <N>` does not restart a live client (DV-4) | Remediation text surfaces truthful exit-and-relaunch steps; the false "one-command restart" claim is removed; a confirmed restart command is deferred (D-3/AD-2). |
| Non-macOS / missing artifacts silently reporting all-clear (F-3) | `unobservable` / `unsupported` are distinct verdict states that never degrade to `healthy`. |

## What must NOT regress (constraints / non-goals)

- Normal `Task*` tool behavior when nothing is wrong — the plan adds no wrapper or shim around the
  `Task*` tools themselves; it only reads artifacts.
- Normal session launch (`ai c`/`ai g`) behavior — no change to the launch path.
- The existing `ai quota watch` behavior — this plan no longer touches the quota-watch loop (the
  sweep is deferred, AD-3); quota polling/notification is untouched.
- Notification delivery for existing sources — this plan fires no notifications (deferred); `Notifier`
  is read-referenced only for the deferred follow-up's spec.

## Scope boundary

Explicitly **out of scope / not attempted** (intellectual-honesty per the research doc's own Open
Questions):

- **Cannot fix the root cause.** CC's daemon lease-reestablishment lives in closed-source client
  code; this plan detects and surfaces, it does not patch the defect.
- **Cannot guarantee zero false negatives/positives.** Detection is a proxy heuristic over local
  artifacts; the reason tiers (D-4) make confidence explicit, but certainty is impossible without an
  internal CC signal.
- **No proactive background sweep in this plan.** Deferred to a dedicated watcher post-validation
  (D-1/D-2/AD-3) — see [Deferred / follow-up work](#deferred--follow-up-work-not-built-in-this-plan).
- **No automatic session restart, and no confirmed `session restart` command in this plan.**
  Deliberately excluded (D-3/AD-2): too high blast radius on a proxy signal, and built on an
  unverified premise.
- **`ai c <N>` is not a restart** (DV-4): it attaches to a live tmux session. Recovery guidance
  surfaces truthful exit-and-relaunch steps instead.
- **macOS-only** for now (F-3): non-macOS hosts return `unsupported`.
- **No SessionStart hook** for self-detection (a session cannot see its own future drop) and **no
  `/task-panel` skill edit** (that skill is canonical in `ai-harness`, a different repo); wiring the
  on-demand check into a hook/skill is a reasonable follow-up, out of this worktree's scope.
- **Empirical confirmation of the restart workaround** (does exit-and-relaunch actually restore
  `Task*`?) is a manual verification step for the next natural reproduction, not a code AC (OQ-1).

## Implementation Audit

> **Step 14 gate** — complete before updating docs or presenting UAT. Verify every T-XX task's
> acceptance criteria against the actual codebase. Any unmet AC restarts from implementation.

### T-01: Detection library

- [ ] `parse_daemon_restarts` upgrade-only + malformed-safe ACs verified
- [ ] `enumerate_live_sessions` interactive-only + numeric-`startedAt` + `procStart` PID-reuse + duplicate-name + skip-on-unparseable ACs verified
- [ ] `last_task_tool_call` / `has_tool_activity_since` / `failed_task_lookup_after` present + failure-path ACs verified
- [ ] Timestamp-normalization (UTC + naive/invalid) ACs verified
- [ ] `assess_session` all five reason states verified (`confirmed_unavailable` outranks `at_risk`; `unobservable`/`unsupported` never degrade to `healthy`)

### T-02: On-demand CLI

- [ ] `check-tasks` disconnect/all-clear/`-j`/`-n`/not-live/missing-daemon-log/non-macOS ACs verified; JSON-array schema + exit-code mapping verified

### T-03: Remediation, config, docs

- [ ] `min_idle_seconds` config (+ malformed-fallback) ACs + truthful-recovery-text AC verified; roadmap updated

**Audit completed:** <!-- YYYY-MM-DD — update when all items above are checked -->

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Before coding | Approve scope + ratify the four AI-recommended Decisions (D-1..D-4, post-audit) + confirm AD-1..AD-4 resolutions; override any in Feedback Round 1 |
| UAT | After implementation | Approve for merge; confirm a live `ai session check-tasks` run behaves on the fleet |

## Open Questions

1. **[OPEN — manual verification]** Does exit-and-relaunching the CC client actually restore `Task*`
   availability (research OQ)?
   - **Status:** Unverifiable until the next natural reproduction — it cannot be triggered reliably or
     safely. The plan ships detection + truthful recovery steps regardless; empirical confirmation is
     a manual UAT/verification step, not a code AC, and it **gates** building the deferred confirmed
     `session restart` command (D-3/AD-2).
2. **[RESOLVED]** How to scan the transcript for the last `Task*` call without per-poll cost blowing
   up on large JSONL files?
   - **Resolution (honest scope, corrected per DV-6):** For the on-demand CLI (this plan), a single
     bounded read per invocation is fine — there is no poll loop here. The earlier claim of a
     "byte-offset + last-verdict cursor modeled on `cc_usage.py`" was **wrong**: `cc_usage.py` stores
     per-session **ISO-timestamp watermarks** (`session_id → latest occurred_at`), reads whole files,
     and writes non-atomically — no byte offset, no verdict field. Any cursor for the deferred sweep
     should follow that real watermark pattern (with atomic writes added), not the fabricated
     byte-offset design.
3. **[RESOLVED]** Does `Task*` serving actually route through the daemon lease, or is the correlation
   coincidental (research OQ, mechanism uncertainty)?
   - **Resolution (reasoning):** Deliberately does not matter to this plan. The correlation tier is
     timing-based and the direct tier is observation-based (an actual failed lookup); neither asserts
     the internal mechanism. Documented as accepted `[INFERENCE]` uncertainty in the
     [Scope boundary](#scope-boundary).

> **Feedback Round 1:** Your thoughts on the open questions:
> 1. <!-- Response to question 1 -->
> 2. <!-- Response to question 2 -->
> 3. <!-- Response to question 3 -->
> - <enter feedback here>

<!-- /doc:region name="task_breakdown" -->

<!-- doc:region name="feedback_rounds" kind="append_only" -->

## Feedback Rounds

(none yet — APPEND_ONLY: prior rounds frozen byte-for-byte)

<!-- /doc:region name="feedback_rounds" -->

<!-- doc:region name="approval_log" kind="append_only" -->

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
| 2026-07-22 | D-1..D-4 auto-resolved | `--mode automated` run; D-1/D-3/D-4 high confidence, D-2 medium; none left PENDING. Awaiting maintainer review (may override any in Feedback Round 1). |
| 2026-07-22 | Round 1 audit incorporated; D-1..D-4 revised (AD-1..AD-4 resolved) | Codex `cx review` (16 findings) incorporated. AD-1 → reason-coded confidence model (revises D-4); AD-2 → truthful exit-and-relaunch, defer confirmed restart command (revises D-3); AD-3 → narrow to library + CLI, defer sweep to a dedicated watcher (revises D-1/D-2); AD-4 → no doc-hygiene change. All four are **AI recommendations awaiting human ratification** — `Chosen (Maintainer)`/`Diverged?` remain pending (JA-1). Two deferred-robustness items filed to the Deferred section (sweep; confirmed restart command). |

<!-- /doc:region name="approval_log" -->
