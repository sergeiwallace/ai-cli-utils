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

**Mode:** `--mode automated` — every Decision + Open Question below was resolved by this run via the
AIH-139 decision scorer ([📄 ai-harness/docs/procedures/decision-framework.md](../../../ai-harness/docs/procedures/decision-framework.md)); see the post-hoc digest at the end of the Decisions section. No mid-run human gate was taken.

## Table of Contents

- [Overview](#overview)
- [Task Breakdown](#task-breakdown)
  - [T-01: Detection library (`task_tool_health.py`)](#t-01-detection-library-task_tool_healthpy)
  - [T-02: On-demand CLI command (`ai session check-tasks`)](#t-02-on-demand-cli-command-ai-session-check-tasks)
  - [T-03: Periodic sweep + notify + dedup](#t-03-periodic-sweep--notify--dedup)
  - [T-04: Remediation guidance, config, docs](#t-04-remediation-guidance-config-docs)
- [Batch Plan](#batch-plan)
- [Acceptance Criteria](#acceptance-criteria)
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
  - [D-4: Detection conservativeness (false-positive control)](#d-4)
  - [Post-hoc automated-mode digest](#post-hoc-automated-mode-digest)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

<!-- aido:region name="overview" kind="replaceable" -->

## Overview

Claude Code's shared background daemon (the `bg-pty-host`/`bg-spare` process pool that serves fast
session spawning, background subagents, and — per the research — the `Task*` tool family) can
restart mid-session, most often to apply an auto-update. When it does, an already-attached,
long-lived foreground CC session can **silently lose `Task*` tool availability** (`TaskList`/
`TaskCreate`/`TaskUpdate`/`TaskGet` vanish from the `ToolSearch` deferred-tool catalog) with **no
error surfaced to the agent or the user**. This was root-caused on 2026-07-22 (session `sw-1`):
`~/.claude/daemon.log` showed an upgrade-restart (`shutting down (cause=upgrade, …, live_workers=2)`,
v2.1.216→v2.1.217) roughly 46 seconds after the session's last successful `Task*` call, and no
`Task*` call succeeded afterward. On-disk task data was untouched — this is a live tool-discovery
problem, not data loss. **The root defect lives inside CC's closed-source client and cannot be fixed
from this repo.** What this plan builds, in `ai-cli-utils`, is the buildable half: **detect** the
condition with a conservative proxy heuristic, and **remediate** by surfacing it (native OS + ntfy
notification, plus an explicit on-demand CLI check) with the exact restart command — never by
killing a live session automatically.

The hard constraint driving the whole design: no external tool or hook can query "does `TaskList`
currently resolve for this live session" — that is internal CC agent-loop state, exposed via no
file, env var, or IPC surface. Detection is therefore necessarily a **correlation heuristic** over
three local artifacts that *are* readable: `~/.claude/daemon.log` (restart events),
`~/.claude/sessions/<pid>.json` (live-session inventory: `pid`, `sessionId`, `cwd`, `startedAt`),
and each session's transcript JSONL under `~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl` (last
successful `Task*` call, and whether the session has kept doing non-`Task*` tool work since the
restart). This is the exact signal the investigation validated; the plan makes it conservative
enough not to cry wolf.

> **Feedback Round 1:** Is the scope right? Too broad, too narrow? Anything missing from the goal?
> - <enter feedback here>

## Task Breakdown

> **AC quality rules** (`docs/procedures/ac-writing-practices.md` is AUTHORITATIVE — open it for the full/latest standard; this inline reminder is sync-checked against its canonical block by `aido validate-doc` and must not be edited independently):
<!-- aido:ac-rules:mirror:begin -->
- Every AC is independently testable — a test can fail if only this AC is violated.
- Every AC is falsifiable — "works correctly" is not an AC.
- Phrase every AC with EARS keywords: `When <trigger>, the system shall <response>` (event-driven); `While <state>` / `Where <feature>` (state-driven / optional); `If <condition>, then the system shall <response>` (unwanted-behavior / failure path).
- At least one failure-path AC — EARS `If <condition>, then the system shall …` — per public function changed.
- Replacement/refactor tasks: inventory the existing behaviors, then a parity AC for each (preserved, or intentionally dropped + reason).
<!-- aido:ac-rules:mirror:end -->

This is **net-new** work (no module being replaced), so no feature-parity inventory is required
(see [What must NOT regress](#what-must-not-regress-constraints--non-goals) for the ordinary
non-regression constraints instead). Full ACs live in [Acceptance Criteria](#acceptance-criteria);
each task below names its file-level scope so it maps to a single Codex `cx implement` delegation.

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

- `parse_daemon_restarts(log_text: str) -> list[datetime]` — the timezone-aware timestamps of every
  `shutting down (cause=upgrade, …)` line in `~/.claude/daemon.log`, ascending.
- `enumerate_live_sessions(sessions_dir: Path) -> list[SessionInfo]` — one record per
  `~/.claude/sessions/<pid>.json` whose `pid` is a live process, carrying at least `name`, `pid`,
  `session_id`, `cwd`, `started_at` (tz-aware).
- `last_task_tool_call(transcript_path: Path) -> datetime | None` — timestamp of the last successful
  `Task*` (`TaskList`/`TaskCreate`/`TaskUpdate`/`TaskGet`) `tool_use`/`tool_result` in the
  transcript, or `None` if there is none.
- `has_tool_activity_since(transcript_path: Path, since: datetime) -> bool` — whether any successful
  **non-`Task*`** tool call appears after `since` (proof the session is still actively working).
- `assess_session(session: SessionInfo, restarts: list[datetime], now: datetime, *, min_idle: timedelta) -> Verdict`
  — the heuristic; returns a `Verdict` dataclass (`disconnected_suspected: bool`, `reason: str`,
  `restart_at`, `last_task_call`) combining all guard conditions (see Overview + D-4).

**Dependencies:** None

### T-02: On-demand CLI command (`ai session check-tasks`)

**Size:** S
**Batch:** 2

A thin caller over T-01: enumerate live sessions (or one named session), assess each, print a
human-readable verdict (and `--json` for machines / other tooling), exit non-zero when a disconnect
is suspected so the agent/user gets a scriptable signal.

**Deliverables:**

- Files created: none
- Files modified: `src/ai_cli/main.py` (register `ai session` group + `check-tasks` subcommand;
  the `session` group is new), `tests/test_cli_dispatch.py` (dispatch wiring)
- Tests added: `tests/test_task_tool_health_cli.py::*`

**Dependencies:** T-01

### T-03: Periodic sweep + notify + dedup

**Size:** M
**Batch:** 3

The proactive catch — the only surface that notices a *silent, mid-session* drop without anyone
thinking to look. Fold a per-poll task-health sweep into the **existing** `ai quota watch` loop
(D-2): each poll, assess every live session; on a **newly** suspected disconnect fire the existing
`Notifier` (native OS + ntfy) once per `(session, restart-event)` pair, deduped via a state file
(mirroring the `context-high-noticed-<session>` marker pattern) so a session is nagged at most once
per restart event.

**Deliverables:**

- Files created: none (dedup markers live under `~/.claude/state/` or the XDG state dir at runtime)
- Files modified: `src/ai_cli/quota.py` (add the sweep call into the existing poll loop),
  `src/ai_cli/task_tool_health.py` (dedup-marker read/write helper + a `sweep(...)` orchestration
  function that returns the sessions to notify — keeps the poll-loop edit thin)
- Tests added: `tests/test_task_tool_health.py::TestSweepDedup`, `tests/test_task_tool_health.py::TestSweepNotify`

**Dependencies:** T-01

### T-04: Remediation guidance, config, docs

**Size:** S
**Batch:** 3

Notify-only + guided remediation (D-3): the notification body and the `check-tasks` output both name
the exact recovery command (restart the affected session — `ai c <N>`, which reconnects via
`--continue` and establishes a fresh daemon lease). Thresholds are config, not constants
(`[task_tool_health]` in config.toml). Roadmap + a short procedure note updated in the same commit.

**Deliverables:**

- Files created: none
- Files modified: `src/ai_cli/config.py` (or the config schema) for `[task_tool_health]` defaults
  (`min_idle_seconds`, `enabled`), `docs/roadmap/master-roadmap.md` (mark AI-CLI-118 progress),
  a short note in the relevant procedure/README
- Tests added: `tests/test_task_tool_health.py::TestConfigDefaults`, remediation-string assertions
  folded into T-02/T-03 tests

**Dependencies:** T-01, T-02, T-03

<!-- /aido:region name="overview" -->

<!-- aido:region name="decisions" kind="replaceable" -->

## Decisions

### Decision Summary

| # | Decision | Options Considered | Recommended (AI) | Chosen (Sergei) | Diverged? | Rationale | Status |
|---|----------|-------------------|------------------|-----------------|-----------|-----------|--------|
| D-1 | Detection architecture / where detection runs | (a) SessionStart hook nudge only, (b) on-demand CLI only, (c) detection library + on-demand CLI + periodic background sweep | (c) library + CLI + periodic sweep | (c) | No | Crit 2 (blast radius: the library is shared single-source-of-truth infra) + the failure is *silent + mid-session*, so start-only or on-demand-only structurally cannot deliver the value — nobody knows to look | ✅ Approved — auto (2026-07-22); confidence: high |
| D-2 | Where the periodic sweep runs | (a) new dedicated Circus watch service, (b) fold into the existing `ai quota watch` loop, (c) ai-core scheduled JobDef on Hetzner | (b) fold into `ai quota watch` | (b) | No | Crit 2 + consistency-with-existing-pattern: the daemon.log + session files live on the Mac, so (c) can't see them; (b) reuses an already-Circus-supervised local poll loop already wired to `Notifier`. Two-way door (extractable later) | ✅ Approved — auto (2026-07-22); confidence: medium |
| D-3 | Remediation level | (a) notify-only, (b) guided one-command restart, (c) fully automatic unattended restart | (a) notify-only + (b) surfaced restart command | (a)+(b), reject (c) | No | Crit 1 (reversibility) + blast radius: auto-killing a live foreground CC process mid-turn is one-way + high-blast (lost in-progress response), and acting automatically on a *proxy* heuristic that can false-positive would kill healthy sessions | ✅ Approved — auto (2026-07-22); confidence: high |
| D-4 | Detection conservativeness (false-positive control) | (a) flag on daemon-restart correlation alone, (b) multi-signal: restart-since-last-Task*AND continued non-Task* activity AND min-idle elapsed, (c) live `ToolSearch` probe of the session | (b) multi-signal | (b) | No | Crit 4 (cost of wrong-simple): a false positive nags a healthy session and erodes trust in the signal; extra guard conditions cost ~nothing. (c) is infeasible per the hard external-observability constraint | ✅ Approved — auto (2026-07-22); confidence: high |

<a id="d-1"></a>

#### D-1: Detection architecture / where detection runs — ✅ Approved: (c) library + CLI + periodic sweep

**Context.** The failure is silent and happens *mid-session* to an already-running session. That
single fact constrains the whole architecture: whatever detects it must be able to look at a live
session *after* a daemon restart, without the session itself having to notice.

##### (a) SessionStart hook nudge only

**Pros:**

- Cheapest to build; mirrors the existing `task-panel-startup.sh` / `context-high-notice.sh` hook pattern.

**Cons:**

- Structurally cannot detect the failure: at SessionStart the session has a *fresh* daemon lease, so the condition is by definition absent. It cannot see its own *future* mid-session drop.
- Only useful for cross-session sweeping if abused as a generic trigger — a hacky use of a start event.

##### (b) On-demand CLI command only

**Pros:**

- Simple, scriptable, no background process; the agent or user can check explicitly.

**Cons:**

- Requires someone to *suspect* the problem and run it — but the whole failure mode is that it is silent, so nobody knows to look. Delivers diagnosis, not proactive catch.

##### (c) Detection library + on-demand CLI + periodic background sweep

**Pros:**

- The pure library is a single testable source of truth every surface reuses.
- The periodic sweep is the only surface that catches a silent mid-session drop with no human prompt — it is what makes this "self-healing" rather than "self-diagnosing on request".
- On-demand CLI still available for explicit checks and for the sweep/other tooling to call.

**Cons:**

- More surface area than (a)/(b): a library + a command + a poll-loop edit.

##### Recommendation

> **Decision:** ✅ Approved — auto (2026-07-22); confidence: high — (c) library + CLI + periodic sweep

Criterion 2 (blast radius) plus the silent-mid-session nature of the failure decide it: (a) and (b)
cannot deliver the core value. The added surface in (c) is modest and the library keeps it DRY. The
SessionStart hook from (a) is deliberately **scoped out** (see [Scope boundary](#scope-boundary)) —
it cannot detect this failure.

---

<a id="d-2"></a>

#### D-2: Where the periodic sweep runs — ✅ Approved: (b) fold into `ai quota watch`

**Context.** The sweep must run *on the Mac*, because `~/.claude/daemon.log` and
`~/.claude/sessions/*.json` are local to the host running the sessions. The fleet already runs a
local, Circus-supervised poll loop (`ai quota watch`, 300 s default) wired to `Notifier`.

##### (a) New dedicated Circus watch service

**Pros:**

- Clean separation of concerns; task-health is not conceptually part of quota-watch.

**Cons:**

- A second Circus service, a second PID guard, a second start/stop lifecycle — real operational complexity for a check that runs on the same cadence as an existing loop.

##### (b) Fold into the existing `ai quota watch` loop

**Pros:**

- Reuses an already-supervised local loop already wired to `Notifier`; near-zero new operational surface.
- The poll loop is effectively a generic "periodic local CC health check" substrate; a second cheap check fits.

**Cons:**

- Mild concern-mixing (quota vs. task-health on one daemon). Extraction later is a rename-level, two-way-door change.

##### (c) ai-core scheduled JobDef on Hetzner

**Pros:**

- Matches the fleet's "recurring tasks use ai-core scheduling, not cron" rule for *server* tasks.

**Cons:**

- Wrong host: the daemon.log + session files live on the Mac, invisible to a Hetzner JobDef. Fails the basic requirement.

##### Recommendation

> **Decision:** ✅ Approved — auto (2026-07-22); confidence: medium — (b) fold into `ai quota watch`

Criterion 2 rules out (c) outright (wrong host). Between (a) and (b), the weighted pass (criterion 4)
favors (b): the concern-mixing cost is small and reversible (two-way door), while (a)'s duplicate
daemon lifecycle is real recurring cost. Medium confidence because the concern-mixing is a genuine
(if minor) smell; flagged as extractable if the quota loop grows.

---

<a id="d-3"></a>

#### D-3: Remediation level — ✅ Approved: (a) notify-only + (b) surfaced restart command

**Context.** Once detected, how forcefully should the system respond? The recovery action is
"restart the affected session," which means killing a live foreground CC process — and detection is
a *proxy* heuristic that can be wrong.

##### (a) Notify-only

**Pros:**

- Zero risk to live work; the human/agent decides whether and when to act.

**Cons:**

- Requires a human/agent to act on the notice; does not literally auto-heal.

##### (b) Guided one-command restart

**Pros:**

- Low-friction: the notice/CLI output names the exact command (`ai c <N>`).

**Cons:**

- Still a manual step (by design).

##### (c) Fully automatic unattended restart

**Pros:**

- Truly hands-off "self-healing."

**Cons:**

- One-way, high-blast-radius: killing a foreground CC process mid-turn can lose an in-progress response or corrupt visible state. On a **false positive** (the heuristic is a proxy), it kills a perfectly healthy working session. Unacceptable risk-to-reward.

##### Recommendation

> **Decision:** ✅ Approved — auto (2026-07-22); confidence: high — (a) notify-only + (b) surfaced command; reject (c)

Criterion 1 (reversibility) + blast radius decide it decisively. The response is a notification that
names the exact restart command; recovery stays a deliberate human/agent action. (c) is rejected
outright — a wrong automatic action on someone's live session is exactly the cost the framework's
reversibility criterion exists to prevent. A thin `ai session heal` auto-restart wrapper is
deferred, not built (restart already exists via `ai c`/`ai reconnect`); revisit only if the manual
step proves to be friction in practice.

---

<a id="d-4"></a>

#### D-4: Detection conservativeness (false-positive control) — ✅ Approved: (b) multi-signal

**Context.** The heuristic is a proxy, not certain. Too aggressive and it nags healthy sessions
(the "crying wolf" cost); too loose and it misses real disconnects. What guard conditions gate a flag?

##### (a) Flag on daemon-restart correlation alone

**Pros:**

- Simplest; catches every true positive.

**Cons:**

- Fires on any session that merely *hasn't needed* a `Task*` call since a restart — a large false-positive class. Erodes trust in the signal fast.

##### (b) Multi-signal: restart-since-last-`Task*` AND continued non-`Task*` activity AND min-idle elapsed

**Pros:**

- Requires positive evidence the session is *still working* (non-`Task*` tool calls after the restart) before flagging — distinguishes "disconnected" from "idle / didn't need tasks."
- `min_idle` avoids racing a session that may still re-handshake right after a restart.

**Cons:**

- A real disconnect on a session that goes quiet right after the restart won't flag until it does more tool work — an accepted false-negative, safer than a false-positive.

##### (c) Live `ToolSearch` probe of the session

**Pros:**

- Would be a direct signal if it existed.

**Cons:**

- Infeasible: no external surface can query another live session's tool registry (the hard constraint). Not buildable.

##### Recommendation

> **Decision:** ✅ Approved — auto (2026-07-22); confidence: high — (b) multi-signal

Criterion 4 (cost of wrong-simple): a false positive is the expensive error here (it trains the user
to ignore the notice), and the extra guard conditions cost almost nothing. (c) is infeasible per the
external-observability constraint. The accepted downside — a genuinely-disconnected but idle session
won't flag until it resumes tool work — is the safe direction to err.

### Post-hoc automated-mode digest

All four Decisions were auto-resolved with no mid-run gate (per
[📄 ai-harness/skills/doc/automated-mode.md](../../../ai-harness/skills/doc/automated-mode.md)).
D-1/D-3/D-4 are **high** confidence; **D-2** is **medium** (fold-into-quota-watch has a minor
concern-mixing smell, but it is a two-way door and the alternative adds a duplicate daemon
lifecycle) — it did not meet the "low-confidence AND consequential" bar for a `PENDING` gate because
it is cheaply reversible. Nothing was left `PENDING`. No metered research was fired (none needed;
all evidence is local). Sergei can override any call in Feedback Round 1.

<!-- /aido:region name="decisions" -->

<!-- aido:region name="task_breakdown" kind="replaceable" -->

## Batch Plan

| Batch | Tasks | Focus | Exit gate |
|-------|-------|-------|-----------|
| 1 | T-01 | Detection library | All T-01 ACs checked + `ruff check` + `ruff format --check` + `pytest tests/test_task_tool_health.py` green |
| 2 | T-02 | On-demand CLI | All T-02 ACs checked + `pytest tests/test_task_tool_health_cli.py tests/test_cli_dispatch.py` green |
| 3 | T-03, T-04 | Sweep + notify + remediation/config/docs | All T-03/T-04 ACs checked + full `pytest` green + roadmap updated |

> **Feedback Round 1:** Does the batching make sense? Should any tasks be reordered, split, or merged?
> - <enter feedback here>

## Acceptance Criteria

EARS-phrased, independently testable. Every AC maps to a synthetic-fixture test — **no test requires
a live Claude Code daemon restart** (neither reliable nor safe to trigger). Fixtures inject a
synthetic `daemon.log`, synthetic `sessions/<pid>.json`, and synthetic transcript JSONL. Non-mocked
behavioral assertions on the real parsing/heuristic logic (do not mock the primary inputs).

### T-01 — Detection library

- [ ] When `parse_daemon_restarts` is given log text containing one or more `shutting down (cause=upgrade, …)` lines, the system shall return their timezone-aware timestamps in ascending order.
- [ ] When `parse_daemon_restarts` is given log text with `cause=idle_exit` shutdowns but no `cause=upgrade` line, the system shall return an empty list (only upgrade restarts count).
- [ ] If `parse_daemon_restarts` is given empty or malformed log text, then the system shall return an empty list and shall not raise.
- [ ] When `enumerate_live_sessions` reads a `sessions/` dir, the system shall return one record per JSON file whose `pid` is a live process, each carrying `name`, `pid`, `session_id`, `cwd`, and a tz-aware `started_at`.
- [ ] If a `sessions/<pid>.json` file is missing, unparseable, or names a dead pid, then the system shall skip that file and shall not raise.
- [ ] When `last_task_tool_call` reads a transcript containing successful `Task*` tool calls, the system shall return the timestamp of the most recent one.
- [ ] If `last_task_tool_call` reads a transcript with no `Task*` call (or a missing/unreadable file), then the system shall return `None` and shall not raise.
- [ ] When `has_tool_activity_since` reads a transcript with a successful non-`Task*` tool call after `since`, the system shall return `True`.
- [ ] If `has_tool_activity_since` finds only `Task*` calls, or no tool calls, after `since`, then the system shall return `False`.
- [ ] **(true positive)** When `assess_session` is given a session that started before an upgrade-restart R, whose last successful `Task*` call predates R with no `Task*` call after R, that has continued non-`Task*` tool activity after R, and where `now − R ≥ min_idle`, the system shall return a `Verdict` with `disconnected_suspected = True`.
- [ ] **(false-positive avoidance — no restart)** If the most recent upgrade-restart predates the session's last successful `Task*` call (i.e. no restart since the tools last worked), then `assess_session` shall return `disconnected_suspected = False`.
- [ ] **(false-positive avoidance — started after restart)** If the session started *after* the most recent upgrade-restart, then `assess_session` shall return `disconnected_suspected = False` (its lease is fresh).
- [ ] **(false-positive avoidance — merely idle)** If the session has had no non-`Task*` tool activity since the restart, then `assess_session` shall return `disconnected_suspected = False` (cannot distinguish disconnect from idle).
- [ ] **(false-positive avoidance — already reconnected)** If a successful `Task*` call exists after the most recent restart, then `assess_session` shall return `disconnected_suspected = False`.
- [ ] **(false-positive avoidance — min-idle race)** If `now − R < min_idle`, then `assess_session` shall return `disconnected_suspected = False`.

### T-02 — On-demand CLI (`ai session check-tasks`)

- [ ] When `ai session check-tasks` runs with one or more live sessions suspected disconnected, the system shall print a per-session verdict naming the session and the exact restart command, and shall exit non-zero.
- [ ] When `ai session check-tasks` runs and no live session is suspected disconnected, the system shall report all-clear and exit zero.
- [ ] When `ai session check-tasks --json` runs, the system shall emit machine-readable verdicts (one object per assessed session) to stdout.
- [ ] When `ai session check-tasks --name <N>` runs, the system shall assess only session `<N>`.
- [ ] If `ai session check-tasks --name <N>` names a session with no live process, then the system shall report it as not-live and exit non-zero (distinct from an all-clear).
- [ ] If `~/.claude/daemon.log` is absent, then `ai session check-tasks` shall treat "no known restarts" as all-clear and exit zero without raising.

### T-03 — Periodic sweep + notify + dedup

- [ ] When the quota-watch poll loop runs a sweep and a session is newly suspected disconnected, the system shall fire `Notifier` once for that `(session, restart-event)` pair with a body naming the session and the restart command.
- [ ] While a `(session, restart-event)` dedup marker exists, the system shall NOT fire a repeat notification for that same pair on subsequent polls.
- [ ] When a *new* upgrade-restart produces a fresh disconnect verdict for a previously-notified session, the system shall fire a new notification (the dedup key includes the restart timestamp).
- [ ] If `Notifier.send` raises or fails, then the sweep shall log and continue the poll loop and shall not crash the quota-watch daemon.
- [ ] If the sweep encounters an unreadable session/transcript/log artifact, then it shall skip that session and continue, and shall not crash the poll loop.

### T-04 — Remediation guidance, config, docs

- [ ] When `[task_tool_health].enabled = false` in config, the system shall skip the sweep entirely (no assessment, no notification).
- [ ] When `[task_tool_health].min_idle_seconds` is set, `assess_session` shall use it as `min_idle` (config over code; a documented default applies when unset).
- [ ] When a disconnect is reported (CLI or notification), the surfaced text shall contain the exact restart command for the affected session.

## Test Plan

- **Fixtures over live daemon.** Every heuristic AC is exercised by constructing a synthetic
  `daemon.log` string, a temp `sessions/` dir with crafted `<pid>.json` files (use the current
  process pid for the "live" case, an obviously-dead pid for the "dead" case), and a temp transcript
  JSONL with hand-written `tool_use`/`tool_result` entries at controlled timestamps. No test triggers
  or waits on a real CC daemon restart.
- **Non-mocked behavioral assertions.** Assert on the real return values of
  `parse_daemon_restarts` / `last_task_tool_call` / `has_tool_activity_since` / `assess_session`
  against the fixtures — not "a mock was called." `Notifier` is the one system-boundary seam that
  may be substituted with a spy in T-03 to assert *what* would be sent and *how many times* (dedup),
  since firing real OS/ntfy notifications in a unit test is a side effect at the boundary.
- **Truth-table coverage for `assess_session`.** Each false-positive-avoidance AC is a distinct row
  (no-restart / started-after / merely-idle / already-reconnected / min-idle-race) plus the one
  true-positive row — a small explicit truth table, so a regression in any single guard fails a
  dedicated test.
- **Dedup is asserted behaviorally.** Run the sweep twice over the same fixture state and assert the
  spy `Notifier` fired exactly once; then inject a *new* restart timestamp and assert it fires again.
- **Exit gate (runnable predicate, not prose):** `ruff check .` + `ruff format --check .` +
  `pytest tests/test_task_tool_health.py tests/test_task_tool_health_cli.py tests/test_cli_dispatch.py`
  all green, and the full `pytest` suite green before T-03/T-04 close.

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Heuristic false positive nags a healthy session | D-4 multi-signal guards (requires continued non-`Task*` activity + min-idle); notify-only (D-3) means a false positive costs a dismissible notice, never a killed session |
| Heuristic false negative (real disconnect on an idle session) | Accepted + documented; the on-demand `check-tasks` command lets a suspicious agent/user check explicitly; safer direction to err than false-positive |
| Transcript JSONL can be large → per-poll scan cost | Bounded reverse/tail read + a per-session cursor cache mirroring `cc_usage.py`'s cursor pattern (see Open Questions) |
| Exact internal mechanism (does `Task*` really route through the daemon lease?) is unconfirmed | The heuristic is correlation-based and does not depend on the mechanism being proven; documented as accepted uncertainty (Open Questions + Scope boundary) |
| Concern-mixing on the quota-watch daemon (D-2) | Two-way door; `sweep()` is a standalone function callable from a dedicated service later with no rewrite |
| CC changes daemon.log format / session-json schema in a future version | Parsers are defensive (skip-and-continue on unparseable input); a format change degrades to "no restarts found / session skipped" (fail-safe: no false positives), surfaced by the tests breaking on the fixture if we update fixtures to a new format |

## What must NOT regress (constraints / non-goals)

- Normal `Task*` tool behavior when nothing is wrong — the plan adds no wrapper or shim around the
  `Task*` tools themselves; it only reads artifacts.
- Normal session launch (`ai c`/`ai g`) behavior — no change to the launch path.
- The existing `ai quota watch` behavior — the sweep is additive; a task-health failure must never
  break quota polling or notification (T-03 failure-path ACs enforce this).
- Notification delivery for existing sources — reuses `Notifier` without altering its contract.

## Scope boundary

Explicitly **out of scope / not attempted** (intellectual-honesty per the research doc's own Open
Questions):

- **Cannot fix the root cause.** CC's daemon lease-reestablishment lives in closed-source client
  code; this plan detects and surfaces, it does not patch the defect.
- **Cannot guarantee zero false negatives/positives.** Detection is a proxy heuristic over local
  artifacts; D-4 tunes it conservative, but certainty is impossible without an internal CC signal.
- **No SessionStart hook.** Deliberately excluded (D-1): at session start the lease is fresh, so the
  hook cannot detect this failure.
- **No automatic session restart.** Deliberately excluded (D-3): too high blast radius on a proxy
  signal.
- **No `/task-panel` skill edit in this plan.** That skill is canonical in `ai-harness` (a different
  repo); wiring the on-demand check into it is a reasonable follow-up but is out of this
  `ai-cli-utils` worktree's scope.
- **Empirical confirmation of the restart workaround** (does restarting actually restore `Task*`?)
  is a manual verification step for the next natural reproduction, not a code AC (see Open Questions).

## Implementation Audit

> **Step 14 gate** — complete before updating docs or presenting UAT. Verify every T-XX task's
> acceptance criteria against the actual codebase. Any unmet AC restarts from implementation.

### T-01: Detection library

- [ ] `parse_daemon_restarts` upgrade-only + malformed-safe ACs verified
- [ ] `enumerate_live_sessions` live/dead-pid + skip-on-unparseable ACs verified
- [ ] `last_task_tool_call` / `has_tool_activity_since` present + failure-path ACs verified
- [ ] `assess_session` true-positive + all five false-positive-avoidance ACs verified

### T-02: On-demand CLI

- [ ] `check-tasks` suspected/all-clear/`--json`/`--name`/missing-daemon-log ACs verified

### T-03: Periodic sweep + notify + dedup

- [ ] Notify-once + dedup + new-restart re-notify + notifier-failure-isolation + skip-unreadable ACs verified

### T-04: Remediation, config, docs

- [ ] `enabled`/`min_idle_seconds` config ACs + remediation-command-present AC verified; roadmap updated

**Audit completed:** <!-- YYYY-MM-DD — update when all items above are checked -->

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Before coding | Approve scope + the four auto-resolved Decisions (override any in Feedback Round 1) |
| UAT | After implementation | Approve for merge; confirm a live `ai session check-tasks` run behaves on the fleet |

## Open Questions

1. **[RESOLVED]** Does restarting the session actually restore `Task*` availability (research OQ)?
   - **Resolution (reasoning):** Unverifiable until the next natural reproduction — it cannot be
     triggered reliably or safely. The plan ships detection + notify + surfaced restart command
     regardless; the restart command is the documented workaround from the research. Empirical
     confirmation is captured as a manual UAT/verification step, not a code AC, so it does not block
     implementation.
2. **[RESOLVED]** How to scan the transcript for the last `Task*` call without per-poll cost blowing
   up on large JSONL files?
   - **Resolution (reasoning + codebase spike):** Bounded read — scan from the end / cap bytes — and
     cache a per-session cursor (last-scanned offset + last verdict), mirroring `cc_usage.py`'s
     existing `_load_cursor`/`_cursor` pattern. Poll cadence is 300 s, so even an unbounded scan is
     tolerable; the cursor is a straightforward optimization, not a correctness dependency.
3. **[RESOLVED]** Does `Task*` serving actually route through the daemon lease, or is the correlation
   coincidental (research OQ, mechanism uncertainty)?
   - **Resolution (reasoning):** Deliberately does not matter to this plan. The heuristic is
     correlation-based (restart-vs-last-successful-call timing); it does not assert or depend on the
     internal mechanism. Documented as accepted uncertainty in the [Scope boundary](#scope-boundary)
     — confirming the mechanism would require CC internals and is not a prerequisite for a useful,
     conservative detector.

> **Feedback Round 1:** Your thoughts on the open questions:
> 1. <!-- Response to question 1 -->
> 2. <!-- Response to question 2 -->
> 3. <!-- Response to question 3 -->
> - <enter feedback here>

<!-- /aido:region name="task_breakdown" -->

<!-- aido:region name="feedback_rounds" kind="append_only" -->

## Feedback Rounds

(none yet — APPEND_ONLY: prior rounds frozen byte-for-byte)

<!-- /aido:region name="feedback_rounds" -->

<!-- aido:region name="approval_log" kind="append_only" -->

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
| 2026-07-22 | D-1..D-4 auto-resolved | `--mode automated` run; D-1/D-3/D-4 high confidence, D-2 medium; none left PENDING. Awaiting Sergei review (may override any in Feedback Round 1). |

<!-- /aido:region name="approval_log" -->
