---
title: "ai migrate-session never calls a partial migration finished, and never launches one"
category: plan
tags: [plan, migrate-session, launch, hooks]
status: active
source: "None"
template_version: "plan-1.0.0"
task: AI-CLI-ofp7
stub: false
---

<!-- DOC-LINK CONVENTION: link other docs as PLAIN RELATIVE Markdown links [📄 <repo>/<relpath>](<relative-path>)
     with plain link text, NO backticks. Cross-repo links carry a local relative link AND a GitHub URL.
     Never use the vscode://file scheme. -->

<!-- doc:region name="overview" kind="replaceable" -->

# ai migrate-session never calls a partial migration finished, and never launches one — Implementation Plan

## Founding Ask Coverage

`canonical_root_json`: N/A (no ask-fidelity root was minted for this task). `founding_ask_ref_sha256`: N/A.
The owning Beads issue `AI-CLI-ofp7` quotes the maintainer's asks and the numbered gaps in full. The
operator's governing ask for this run, verbatim:

```text
and we need to prioritize getting the session-migrate command / cli script finished and working end-to-end and any gaps or bugs addressed and /merge in and tested to work for whichever cc sessions we've partiailly migrated on any and all machine hosts and then once its working, we'll start winding down all the cc sessions on this machine host that need to be migrated and they'll /save-state and auto-compact and and exit. i will give those other cc sessiosn names. give me suggestions for what to name them from the registry
```

Coverage index (non-authoritative):

| Ask entry | Where covered |
|-----------|---------------|
| "finished and working end-to-end and any gaps or bugs addressed" | T-01 to T-07; each closes numbered gaps of `AI-CLI-ofp7` |
| "/merge in" | Batch Plan exit gates: each task lands as its own merged PR |
| "tested to work for whichever cc sessions we've partially migrated on any and all machine hosts" | T-03 `--status` finds every partial migration on a host, `-R` reaches another host; T-05 finishes the stopped ones. Running it against the real sessions is the operator's step after merge, not this plan's (this plan's work runs on fixtures only) |
| "once its working, we'll start winding down all the cc sessions ... /save-state and auto-compact and exit" | Out of this repo: operator-run, after T-03 to T-05 merge. T-07's `--relaunch` is the tool for each session's stop, migrate and relaunch |
| "give me suggestions for what to name them from the registry" | Out of this plan: answered by the operator session from the name registry |

**Status:** ACTIVE

**Created:** 2026-11-11

**Task:** AI-CLI-ofp7 (this repo), with a companion change to the hooks in the private tooling that installs them.

<!-- FEEDBACK RULES (for AI agents):
  1. Never edit, rewrite, or remove user-written feedback. It is permanent record.
  2. When the user writes feedback: commit the doc immediately BEFORE responding or revising.
  3. Each round is a --- bounded section.
  4. Append AI response as > **AI Response Round N:** below user feedback.
  5. Never overwrite prior rounds.
  6. After each round, add a line item to the Approval Log.
-->

## Table of Contents

- [Founding Ask Coverage](#founding-ask-coverage)
- [Summary for a wide audience](#summary-for-a-wide-audience)
- [Overview](#overview)
- [Task Breakdown](#task-breakdown)
  - [T-01: Every hook reports done or pending without acting (gap 16)](#t-01-every-hook-reports-done-or-pending-without-acting-gap-16)
  - [T-02: --finish by one name from any cwd, and installed-build skew (gaps 8, 13)](#t-02---finish-by-one-name-from-any-cwd-and-installed-build-skew-gaps-8-13)
  - [T-03: Finished only when a re-plan is empty, read-only live plans, and --status (gaps 1, 2, 4, 14)](#t-03-finished-only-when-a-re-plan-is-empty-read-only-live-plans-and---status-gaps-1-2-4-14)
  - [T-04: No launch of a partial migration (gaps 3, 7, 13 launch half)](#t-04-no-launch-of-a-partial-migration-gaps-3-7-13-launch-half)
  - [T-05: A sweep finishes every stopped partial migration (gap 5)](#t-05-a-sweep-finishes-every-stopped-partial-migration-gap-5)
  - [T-06: Beads re-key publishes what it changed (gap 9)](#t-06-beads-re-key-publishes-what-it-changed-gap-9)
  - [T-07: --relaunch stops, migrates, relaunches and verifies (gaps 10, 12)](#t-07---relaunch-stops-migrates-relaunches-and-verifies-gaps-10-12)
- [Batch Plan](#batch-plan)
- [Implementation Audit](#implementation-audit)
- [Human Gates](#human-gates)
- [Decisions](#decisions)
  - [Decision Summary](#decision-summary)
  - [Decision Details](#decision-details)
  - [D-1: What counts as work left — ✅ Resolved](#d-1)
  - [D-2: What --status lists by default — ✅ Resolved](#d-2)
  - [D-3: Whether the launch guard has an override — ✅ Resolved](#d-3)
  - [D-4: How the Beads publish step is reached — ✅ Resolved](#d-4)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Summary for a wide audience

Renaming a session with `ai migrate-session` could report success while parts of the old name were
still on disk. Nothing then stopped `ai c` from starting that half-renamed session. After this plan,
a migration is marked finished only when a fresh read-only plan finds nothing left. One command lists
every migration on a host as complete or partial, and `ai c` refuses to start a partial one, printing
the command that finishes it. Nothing changes for a session that was never migrated.

## Overview

`ai migrate-session` records `finished` from the run's own warnings, not from what is left on disk.
External hooks print no completion status, so a re-plan reads every hook as "would do" even right
after it finished. There is no completeness listing and no launch guard. This plan makes one
predicate, "a read-only re-plan finds nothing left", the single source of truth for finished,
for `--status`, for the launch guard and for the sweep.

> **Feedback Round 1:** Is the scope right? Too broad, too narrow? Anything missing from the goal?
> - <enter feedback here>

## Task Breakdown

> **AC quality rules** (`docs/procedures/task-authoring-standards.md` in the private tooling repo is authoritative):
<!-- doc:ac-rules:mirror:begin -->
- Every AC is independently testable — a test can fail if only this AC is violated.
- Every AC is falsifiable — "works correctly" is not an AC.
- Use EARS as the default for textual behavioral ACs: `When <trigger>, the system shall <response>` (event-driven); `While <state>` / `Where <feature>` (state-driven / optional); `If <condition>, then the system shall <response>` (unwanted-behavior / failure path). When a decision table, state machine, formula, executable Gherkin, property, or contract expresses the behavior more clearly, wrap it in an `<!-- ac-format: <value> ... --> ... <!-- /ac-format -->` scope (`decision-table` / `state-machine` / `formula` / `gherkin` / `property` / `contract`; unmarked ACs default to `ears`). Full per-format `ac-format` schemas are normative at `task-authoring-standards.md` § Per-Format AC Schemas — **always check that live source directly for the current schemas before relying on this reminder; this mirrored block itself can drift out of date and must never be treated as authoritative on its own.**
- At least one failure-path AC per public function changed — EARS `If <condition>, then the system shall …`, or the marked format's own negative-path convention (a decision table's infeasible-combination row, a state machine's invalid-transition row, a formula's invalid-input row).
- Replacement/refactor tasks: inventory the existing behaviors, then a parity AC for each (preserved, or intentionally dropped + reason).
<!-- doc:ac-rules:mirror:end -->

Every test drives the CLI entry point (`ai migrate-session`, `ai c`, `ai sessions relaunch`) on
fixtures: a temporary `HOME`, `XDG_STATE_HOME` and `XDG_CONFIG_HOME`, stub hooks, stub `bd`. No test
or manual check touches a real session.

### T-01: Every hook reports done or pending without acting (gap 16)

**Size:** M
**Batch:** 1

The hooks live in the private tooling that installs them. Each one's `--dry-run` prints exactly one
`migrate-status: done|pending` line, derived from its own durable state, per
[📄 ai-cli-utils/docs/reference/migrate-session-hooks.md](../reference/migrate-session-hooks.md).
A leftover pair the hook skipped because the target exists is `pending`, never `done`.

**Deliverables:**

- Files modified: the hook classes and the held-output migrate verb, in the private tooling repo
- Tests added: per-hook CLI tests in that repo

**Acceptance criteria:**

- [ ] When a hook's `--dry-run` runs on state its live run already converged, it shall print `migrate-status: done` and exit 0.
- [ ] When a hook's `--dry-run` runs on state with work left, including a skipped pair whose target exists, it shall print `migrate-status: pending`.
- [ ] If a hook's `--dry-run` runs, then it shall write nothing (fixture tree identical before and after).
- [ ] When `ai migrate-session OLD NEW --finish -n` runs one minute after a successful `--finish -y` on the same fixture, every hook row shall read `done earlier`.

**Dependencies:** None

### T-02: --finish by one name from any cwd, and installed-build skew (gaps 8, 13)

**Size:** M
**Batch:** 1

**Deliverables:**

- Files modified: `src/ai_cli/main.py` (`cmd_migrate_session`), `src/ai_cli/session_migrate.py` (ledger readers), docs
- Tests added: CLI tests for name resolution and skew

**Acceptance criteria:**

- [ ] When `ai migrate-session NAME --finish` runs from any directory and NAME equals one recorded pair's NEW, its OLD, or the first-name label of its NEW, the system shall finish that pair in the repo the record names.
- [ ] If NAME matches no recorded pair, then the command shall exit 2 and name what it searched.
- [ ] If NAME matches more than one recorded pair, then the command shall exit 2 and list each pair with its full command.
- [ ] When a writing run finds the installed build older than the build the migration record format needs, the system shall run the existing update path first.
- [ ] If that update fails, then the command shall refuse before its first write and print the update command.
- [ ] Each new ledger row shall record the migration record format number.

**Dependencies:** None

### T-03: Finished only when a re-plan is empty, read-only live plans, and --status (gaps 1, 2, 4, 14)

**Size:** L
**Batch:** 2

`finish_session(dry_run=True)` stops refusing a live session: it plans, and adds a report line that
a writing run will refuse until the session is stopped. Writing runs keep the refusal. After a writing
`--finish` or full migration, the command re-plans with `dry_run=True` and computes the parts left
(D-1). It appends `finished` (with `verified: true`) only when that list is empty; otherwise it names
each part left and exits 1, and `--to-name` marks the registry incomplete. `--status` (D-2) prints, per
recorded pair, `complete` or `partial` with the parts left and the command that finishes it.

**Deliverables:**

- Files modified: `src/ai_cli/session_migrate.py` (`finish_session` live guard, re-plan before the finished row), `src/ai_cli/main.py` (`--status`), a small module for the completion predicate, docs
- Tests added: CLI tests for each AC

**Acceptance criteria:**

- [ ] When `ai migrate-session OLD NEW --finish -n` runs while NEW is live, it shall print the plan, name the live session, write nothing and exit 0 or 1 by the plan, never 2.
- [ ] While NEW is live, a writing `--finish` shall still refuse with exit 2.
- [ ] When a writing run's re-plan finds any part left, the system shall append no `finished` row, name each part left, and exit 1.
- [ ] When a writing run's re-plan finds nothing left, the system shall append one `finished` row carrying `verified: true` and exit 0.
- [ ] If a `--to-name` run's re-plan finds a part left, then the registry entry shall be marked incomplete, not complete, and the command shall exit 1.
- [ ] When `ai migrate-session -s/--status` runs, it shall list each recorded pair without a verified `finished` row as `complete` or `partial`, with the parts left and the finishing command, and exit 1 if any is partial, else 0.
- [ ] When `-a/--all` is added, it shall also re-plan pairs whose `finished` row is verified.
- [ ] When `-R/--remote` is added, it shall run the same status on the default remote host through `ai`'s existing remote route and print its output.
- [ ] `--status` shall write nothing, including on the ledger.

**Dependencies:** T-01 (otherwise every hook reads pending); the open PRs changing `finish_session` and `_plan_hooks` merged first.

### T-04: No launch of a partial migration (gaps 3, 7, 13 launch half)

**Size:** M
**Batch:** 3

The launch-time finish already runs for the launching name. It now uses T-03's predicate: if the
finish refuses, fails, or leaves anything, the launch is refused and prints the `--finish` command.
`ai sessions relaunch` gets the same check before it starts anything. A ledger row with a format
newer than the running build refuses the launch with the update command.

**Deliverables:**

- Files modified: `src/ai_cli/main.py` (`_finish_recorded_migrations` and its callers), the relaunch command, docs
- Tests added: CLI tests through `ai c` and `ai sessions relaunch`

**Acceptance criteria:**

- [ ] If `ai c NAME` meets a recorded migration to NAME whose finish leaves any part, then it shall start nothing, print the parts left and the `--finish` command, and exit non-zero.
- [ ] If the launch-time finish raises, then the launch shall be refused the same way, never continued.
- [ ] When `ai sessions relaunch` targets a session with a partial migration, it shall refuse it the same way and still relaunch the others.
- [ ] When the override flag (D-3) is given, the launch shall go ahead and print one warning naming the parts left.
- [ ] If a ledger row's format is newer than the running build understands, then the launch shall refuse and print the update command.

**Dependencies:** T-03; the open PR changing `_finish_recorded_migrations` merged first.

### T-05: A sweep finishes every stopped partial migration (gap 5)

**Size:** S
**Batch:** 3

`ai migrate-session --finish -a/--all` re-plans every recorded pair on the host, finishes each one
that is partial and whose sessions are stopped, skips and names each live one, and prints one
summary row per pair. Pairs whose `finished` row predates `verified` are re-planned like unfinished
ones, so migrations recorded before this fix are covered.

**Acceptance criteria:**

- [ ] When the sweep runs, each stopped partial pair shall be finished and re-planned, and each shall read `complete` afterwards or name what is left.
- [ ] If a pair's session is live, then the sweep shall skip it, name it, and exit 1.
- [ ] When a pair's only `finished` row lacks `verified`, the sweep shall re-plan it.

**Dependencies:** T-03.

### T-06: Beads re-key publishes what it changed (gap 9)

**Size:** M
**Batch:** 2

Per D-4: after a store's issues are re-keyed, the command runs the configured publish command for that
store, then re-plans that store. A failed publish or a store still holding issues on OLD is a part
left. The private tooling installs the configuration, so it ships enabled there.

**Acceptance criteria:**

- [ ] When a run re-keys issues in a store and a publish command is configured, the system shall run it once for that store, after the re-key.
- [ ] If the publish command exits non-zero, then the store shall read `failed` and the run shall exit 1 with no `finished` row.
- [ ] Where no publish command is configured, the store shall read `publish needed` as today, and `--status` shall show it.

**Dependencies:** None for the re-key half; T-03 for the finished rule.

### T-07: --relaunch stops, migrates, relaunches and verifies (gaps 10, 12)

**Size:** L
**Batch:** 4

The opt-in stop, migrate and relaunch run the private audit resolved for. It calls `ai sessions stop`,
runs the migration or finish, relaunches through a non-replay `ai c NEW`, and then verifies that the
session registered under NEW. It works when run from inside a tmux session.

**Acceptance criteria:**

- [ ] When `--relaunch` is given and the session is live, the system shall stop it through `ai sessions stop`, migrate, relaunch NEW, and report the session registered under NEW.
- [ ] If the migration leaves any part, then nothing shall be relaunched and the command shall exit 1.
- [ ] When run from inside a tmux session, the relaunch shall start NEW in its own tmux session without the caller unsetting any variable.
- [ ] If NEW does not register within the timeout, then the command shall exit 1 and say what it read.

**Dependencies:** `ai sessions stop` merged; T-03 and T-04.

## Batch Plan

| Batch | Tasks | Focus | Exit gate |
|-------|-------|-------|-----------|
| 1 | T-01, T-02 | Hooks report honestly; finish by name; build skew | Both PRs merged; their ACs checked; `uv run pytest` green on each head |
| 2 | T-03, T-06 | One completion predicate; `--status`; publish | PRs merged after the PRs changing `finish_session` merge; ACs checked; suite green |
| 3 | T-04, T-05 | Launch guard; sweep | PRs merged; ACs checked; suite green |
| 4 | T-07 | Driven relaunch | PR merged; AC checked; suite green |

> **Feedback Round 1:** Does the batching make sense? Should any tasks be reordered, split, or merged?
> - <enter feedback here>

## Implementation Audit

> **Step 14 gate**: complete before updating docs or presenting UAT.

### T-01: Every hook reports done or pending without acting (gap 16)

- [ ] AC 1 to 4

### T-02: --finish by one name from any cwd, and installed-build skew (gaps 8, 13)

- [ ] AC 1 to 6

### T-03: Finished only when a re-plan is empty, read-only live plans, and --status (gaps 1, 2, 4, 14)

- [ ] AC 1 to 9

### T-04: No launch of a partial migration (gaps 3, 7, 13 launch half)

- [ ] AC 1 to 5

### T-05: A sweep finishes every stopped partial migration (gap 5)

- [ ] AC 1 to 3

### T-06: Beads re-key publishes what it changed (gap 9)

- [ ] AC 1 to 3

### T-07: --relaunch stops, migrates, relaunches and verifies (gaps 10, 12)

- [ ] AC 1 to 4

**Audit completed:** <!-- YYYY-MM-DD -->

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Before coding | None: every Decision below is resolved, so the plan is approved |
| UAT | After implementation | The maintainer runs `--status` on each host and finishes the partial migrations it lists |

## Decisions

### Decision Summary

| # | Decision | Options Considered | Recommended (AI) | Chosen | Diverged? | Rationale | Status |
|---|----------|-------------------|------------------|--------|-----------|-----------|--------|
| D-1 | What counts as work left | (a) Re-plan with the same planner, (b) Per-part completion journal | (a) | | | One source of truth read from disk; a stamp can hide leftovers | ✅ Resolved |
| D-2 | What `--status` lists by default | (a) Unverified pairs, `-a` adds verified ones, (b) Every pair always | (a) | | | Bounded default cost; `-a` still lists every recorded migration | ✅ Resolved |
| D-3 | Whether the launch guard has an override | (a) No override, (b) Explicit override flag | (b) | | | The acceptance allows one; a broken hook must not strand a session | ✅ Resolved |
| D-4 | How the Beads publish step is reached | (a) Configured publish command, (b) A hook that publishes | (a) | | | Only the core run knows which stores it changed | ✅ Resolved |

### Decision Details

<a id="d-1"></a>

#### D-1: What counts as work left — ✅ Resolved

**Context.** Gaps 1, 2, 3 and 5 all need one answer to "is this migration finished". Today the answer
comes from the run's own warnings, and a pair was recorded finished while a plan an hour later still
listed about 30 rewrites.

##### (a) Re-plan with the same planner

Run `finish_session(dry_run=True)` for the pair and read its parts table. A part is left unless its
status is `done earlier`, an informational skip (`no matching artifact`, `informational`), or a hook
reporting `migrate-status: done`.

**Pros:**

- The planner that does the work also judges it, so the check cannot drift from the code.
- It reads the disk, so it catches leftovers a later process recreated.

**Cons:**

- A re-plan costs one dry run of every hook, a few seconds per pair.
- It is only as honest as each hook's status line, which is why T-01 comes first.

##### (b) Per-part completion journal

Each part writes a done marker to the ledger; finished means every marker present.

**Pros:**

- Cheap to read; no hook subprocesses.

**Cons:**

- A marker cannot see an artifact recreated after it was written, which is the measured failure.
- The hook contract forbids a completion stamp that hides remaining source artifacts.

##### Recommendation

> **Decision:** ✅ Resolved by ofp7-migrate-complete — (a) Re-plan with the same planner
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=claude-opus-5-5; ai-effort=default; ai-profile=architect -->
<!-- decision-lineage: decision-id=AI-CLI-ofp7/D-1; decision-topic=migrate-session-work-left-predicate; governs=artifact:ai-cli-utils/session_migrate.finish_session; normalized-proposition=finished-means-empty-dry-run-replan; applicability=repo:ai-cli-utils; outcome-id=AI-CLI-ofp7; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

(a) is the issue's own acceptance wording ("finished is recorded only when a re-plan finds nothing
left"), so Gate 0 keeps it. The cost Con is mitigated by running the re-plan once per writing run and
once per pair in `--status`, never in a loop. The honesty Con is mitigated by T-01 landing first and
by T-01's test that a re-plan right after a finish reads every hook done.

---

<a id="d-2"></a>

#### D-2: What --status lists by default — ✅ Resolved

**Context.** The acceptance asks for "one read-only verb (for example `ai migrate-session --status [-a]`)"
that lists every recorded migration as complete or partial. Each listed pair costs a re-plan.

##### (a) Unverified pairs, `-a` adds verified ones

`--status` re-plans every pair without a `verified` finished row, which includes every pair recorded
before this fix. `-a/--all` re-plans every recorded pair. A NAME argument narrows to that pair.

**Pros:**

- The default shows exactly the migrations that may be partial, at the cost of those only.
- `-a` meets "every recorded migration on the host".

**Cons:**

- A verified pair that a later process dirtied shows only under `-a`.

##### (b) Every pair always

**Pros:**

- One behaviour, nothing hidden.

**Cons:**

- Re-plans the whole history on every call, which grows without bound.

##### Recommendation

> **Decision:** ✅ Resolved by ofp7-migrate-complete — (a) Unverified pairs, `-a` adds verified ones
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=claude-opus-5-5; ai-effort=default; ai-profile=architect -->
<!-- decision-lineage: decision-id=AI-CLI-ofp7/D-2; decision-topic=migrate-session-status-default-scope; governs=artifact:ai-cli-utils/main.cmd_migrate_session; normalized-proposition=status-default-replans-unverified-pairs; applicability=repo:ai-cli-utils; outcome-id=AI-CLI-ofp7; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

The acceptance names `-a` as optional, so (a) keeps every listed requirement: `-a` lists every recorded
migration. The Con is mitigated by the launch guard (T-04), which re-plans a pair at its own launch
whatever its row says, so a dirtied verified pair is caught before it can start.

---

<a id="d-3"></a>

#### D-3: Whether the launch guard has an override — ✅ Resolved

**Context.** The standing rule is never to launch a session whose migration is partial. The
acceptance says "an explicit override flag may exist". A hook that cannot converge (one refused for
hours on a measured host) would otherwise keep a session from starting at all.

##### (a) No override

**Pros:**

- The rule cannot be bypassed.

**Cons:**

- One broken hook strands the session until someone fixes that hook.

##### (b) Explicit override flag

A flag with short and long forms on `ai c` and `ai sessions relaunch`, never set by any default or
config, that starts the session anyway and prints the parts left.

**Pros:**

- A person can still start a session whose leftover is known and harmless.

**Cons:**

- An agent may reach for it to get past the guard.

##### Recommendation

> **Decision:** ✅ Resolved by ofp7-migrate-complete — (b) Explicit override flag
<!-- decision-record: chosen-option=(b); ai-family=claude; ai-model=claude-opus-5-5; ai-effort=default; ai-profile=architect -->
<!-- decision-lineage: decision-id=AI-CLI-ofp7/D-3; decision-topic=partial-migration-launch-override; governs=artifact:ai-cli-utils/main._finish_recorded_migrations; normalized-proposition=launch-guard-has-explicit-override-flag; applicability=repo:ai-cli-utils; outcome-id=AI-CLI-ofp7; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

The acceptance allows the flag, so this does not narrow the rule. The Con is mitigated three ways that
do not depend on intent: the flag is per invocation and never read from config or the environment, the
refusal text prints the `--finish` command and not the flag, and every overridden launch writes the parts
left into its launch log so the next `--status` and sweep still see the pair as partial.

---

<a id="d-4"></a>

#### D-4: How the Beads publish step is reached — ✅ Resolved

**Context.** Gap 9: the re-key ends with "each changed store is named as needing its publish step",
and someone publishes by hand. The publish step is private tooling, so this public package cannot
name it, and a raw push is not allowed.

##### (a) Configured publish command

A config entry read at call time names an argv template with a `{store}` placeholder. The run calls it
once per store it changed, then re-plans that store.

**Pros:**

- The run knows exactly which stores it changed.
- The command is config, so the private tooling installs it beside the hooks.

**Cons:**

- A host without the config keeps today's "publish needed".

##### (b) A hook that publishes

A hook in `migrate-session.d/` publishes stores.

**Pros:**

- Reuses the hook contract and its status line.

**Cons:**

- Hooks run before the Beads re-key in `--finish`, and receive only OLD and NEW, so the hook cannot know which stores changed.

##### Recommendation

> **Decision:** ✅ Resolved by ofp7-migrate-complete — (a) Configured publish command
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=claude-opus-5-5; ai-effort=default; ai-profile=architect -->
<!-- decision-lineage: decision-id=AI-CLI-ofp7/D-4; decision-topic=migrate-session-beads-publish-route; governs=artifact:ai-cli-utils/session_migrate._apply_beads; normalized-proposition=beads-publish-is-a-configured-command; applicability=repo:ai-cli-utils; outcome-id=AI-CLI-ofp7; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

(a) delivers "publishes each store it changed and verifies convergence" where the command is
configured. Its Con is mitigated by the private tooling's installer writing the entry on every host it
manages, so the feature ships enabled there, and by `--status` naming any store still unpublished.

---

## Open Questions

None open.

## Approval Log

| Date | Decision | Actor | Notes |
|------|----------|-------|-------|
| 2026-11-11 | D-1 to D-4 resolved | ofp7-migrate-complete (Claude Opus 5.5, architect) | Resolved under the decision framework; each stays inside the issue's acceptance |

<!-- /doc:region name="overview" -->

<!-- doc:region name="decisions" kind="replaceable" -->

<!-- /doc:region name="decisions" -->

<!-- doc:region name="task_breakdown" kind="replaceable" -->

<!-- /doc:region name="task_breakdown" -->

<!-- doc:region name="feedback_rounds" kind="append_only" -->

<!-- /doc:region name="feedback_rounds" -->

<!-- doc:region name="approval_log" kind="append_only" -->

<!-- /doc:region name="approval_log" -->
