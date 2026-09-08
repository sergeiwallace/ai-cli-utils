---
title: "User Behavior Telemetry — Design Document"
category: design
tags: [telemetry, analytics, nudges, guidance, privacy, sqlite]
status: draft
source: myproject
template_version: "design-1.0.0"
---

> **Migrated from `myproject` (SW-837, 2026-07-26).** This design doc followed its implementation
> into this repo: the code it describes lives here, not in `myproject`, since the SW-907 repo-ownership
> migration. `myproject/docs/designs/user-behavior-telemetry.md` is now a `status: moved` stub pointing here.
> Content is unchanged by the move — any drift between this doc and the current code predates it
> and is not a migration artifact.
<!-- doc:region name="overview" kind="replaceable" -->

# User Behavior Telemetry — Design Document

**Status:** DRAFT

**Tracked as:** `AI-CLI-142`

**Created:** 2026-03-18

**Research:** [docs/research/telemetry-event-design-early-stage-apps.md](../research/telemetry-event-design-early-stage-apps.md)

<!-- FEEDBACK RULES (for AI agents):
  1. Never edit, rewrite, or remove user-written feedback. It is permanent record.
  2. When the user writes feedback: commit the doc immediately BEFORE responding or revising.
  3. Each round is a --- bounded section: opening --- before Feedback Round N, closing --- after AI Response Round N.
  4. Append AI response as > **AI Response Round N:** below user feedback, then add closing --- + > **Feedback Round N+1:** prompt + closing ---.
  5. Never overwrite prior rounds.
-->

## Table of Contents

<!-- COMP-128: the ToC sits ABOVE the Executive Summary (it is self-referential otherwise).
  D5 (c): list EVERY `## ` and EVERY `### ` heading in the real doc, with GitHub-style
  anchors (lowercase, spaces→hyphens, punctuation stripped) so they navigate in-window
  (incl. VS Code Remote-SSH). `companion toc check` validates this once COMP-127 lands. If
  all-`###` proves too noisy, fall back to D5 (a) "meaningful `###`" — a deterministic
  OR-rule: include a `###` when it (1) has child `####`, (2) its section body ≥ ~8-10
  lines, (3) its parent `##` is allowlisted (Design Decisions / Open Questions /
  appendices), or (4) matches a pattern (`### D-N`); `<!-- toc:skip -->` /
  `<!-- toc:include -->` on a heading override the heuristic. -->

- [Problem Statement](#problem-statement)
- [Design Decisions](#design-decisions)
- [Data Model](#data-model)
  - [Event Table](#event-table-analyticsdb)
  - [Nudge Lifecycle Events](#nudge-lifecycle-events)
  - [Identity Mapping](#identity-mapping-user_mapping-table-production-db)
  - [SQLite PRAGMA Configuration](#sqlite-pragma-configuration)
- [Integration](#integration)
  - [ADHD Guidance System](#adhd-guidance-system)
  - [Backlog Curation](#backlog-curation)
  - [Daily Digest](#daily-digest)
  - [Web Dashboard](#web-dashboard)
  - [CLI](#cli)
  - [Fatigue Score](#fatigue-score)
- [Implementation Phases](#implementation-phases)
- [Risks and Mitigations](#risks-and-mitigations)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Problem Statement

The platform delivers nudges and guidance across three surfaces (CLI, web, agent API) to help users ship. Today there is no way to measure whether this guidance actually works. Without telemetry, we cannot answer: "Did this nudge cause the user to complete a task, or would they have done it anyway?" We also cannot detect fatigue — users silently disengaging because nudges are annoying rather than helpful.

This design establishes the instrumentation layer that measures nudge effectiveness, detects fatigue, and provides the data foundation for causal analysis — all without third-party dependencies, with minimal GDPR burden, and at zero infrastructure cost.

## Design Decisions

| # | Decision | Options Considered | Chosen | Rationale | Status |
|---|----------|-------------------|--------|-----------|--------|
| 1 | Analytics infrastructure | Custom SQLite, PostHog Cloud, PostHog self-hosted, Plausible | Custom SQLite (`analytics.db`, separate from prod DB) | Zero ops, joinable with prod data, no third-party processor, trivial GDPR | Pending |
| 2 | Write path | Direct inserts, aiosqlite, in-process queue + background thread, external message broker | In-process queue + background writer thread | Non-blocking (~0.01ms enqueue), batched transactions for 50K+ inserts/sec, no external deps | Pending |
| 3 | Event schema | Click-through tracking, pageview-oriented, 5-state nudge lifecycle | 5-state lifecycle: created, shown, interacted, converted, expired | Decouples conversion from interaction (attribution window, not click-through). Matches Firebase/Segment patterns | Pending |
| 4 | Primary analysis (<500 users) | Dashboards, automated reports, qualitative + ad-hoc SQL | Qualitative interviews + ad-hoc SQL | Statistical power too low for quantitative methods at <500 users. Invest in instrumentation now, analysis later | Pending |
| 5 | Causal method (recurring nudges) | A/B testing, observational analysis, Micro-Randomized Trials | MRT with probabilistic delivery (70/30 send/withhold) | Works at 30-50 users; each nudge decision is an independent randomization point, generating dozens of observations per user per week | Pending |
| 6 | Causal method (system changes) | Pre/post comparison, A/B test, Bayesian Structural Time Series | BSTS via CausalImpact | No control group needed; uses user's own history as baseline. Requires 14+ days pre-intervention data | Pending |
| 7 | Privacy approach | Third-party analytics, first-party with PII, first-party pseudonymized content-free | First-party, pseudonymized, content-free events | Events record *that* a nudge happened and *what* the user did, never *what* it said. No personal data in analytics DB | Pending |
| 8 | GDPR erasure | Delete events, anonymize in-place, cryptographic erasure | Cryptographic erasure (delete mapping, not events) | Preserves aggregates, orphans pseudonymized events. Daily-rotated salts make re-identification impossible without mapping table | Pending |
| 9 | Scaling path | Build for scale now, fixed infrastructure, tiered progression | SQLite -> DuckDB sidecar -> ClickHouse (only if needed) | DuckDB reads SQLite directly (zero-ETL). Trigger: query latency >1s on cohort queries, not user count | Pending |
| 10 | Fatigue detection | Single metric, manual review, composite score | Composite score: dismiss rate + speed dismiss rate + negative action rate | Three independent signals with validated thresholds. Enables automated throttling (reduce frequency, pause, digest mode) | Pending |

> **Feedback:**
> 1. Analytics infrastructure (SQLite):
>
>    - <approval or feedback>
>
>    - <approval or feedback>
> 2. Write path (background thread):
>
>    - <approval or feedback>
>
>    - <approval or feedback>
> 3. Event schema (5-state lifecycle):
>
>    - <approval or feedback>
>
>    - <approval or feedback>
> 4. Primary analysis (qualitative + SQL):
>
>    - <approval or feedback>
>
>    - <approval or feedback>
> 5. Causal method for recurring nudges (MRT):
>
>    - <approval or feedback>
>
>    - <approval or feedback>
> 6. Causal method for system changes (BSTS):
>
>    - <approval or feedback>
>
>    - <approval or feedback>
> 7. Privacy approach (content-free):
>
>    - <approval or feedback>
>
>    - <approval or feedback>
> 8. GDPR erasure (cryptographic):
>
>    - <approval or feedback>
>
>    - <approval or feedback>
> 9. Scaling path (SQLite -> DuckDB -> ClickHouse):
>
>    - <approval or feedback>
>
>    - <approval or feedback>
> 10. Fatigue detection (composite score):
>
> - <approval or feedback>
>
> - <approval or feedback>
> - <approval or feedback>

## Data Model

### Event Table (`analytics.db`)

Separate SQLite file from the production database. WAL mode enabled for read-write concurrency.

```sql
CREATE TABLE events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    user_id     TEXT NOT NULL,    -- pseudonymized: sha256(user_id + daily_salt)
    event_type  TEXT NOT NULL,    -- e.g. nudge.created, nudge.shown, nudge.converted
    nudge_id    TEXT,             -- links events in the same nudge lifecycle
    task_id     TEXT,             -- opaque UUID, never task name
    properties  TEXT,             -- JSON blob for event-specific data
    session_id  TEXT
);

CREATE INDEX idx_events_user_ts  ON events(user_id, ts);
CREATE INDEX idx_events_nudge    ON events(nudge_id) WHERE nudge_id IS NOT NULL;
CREATE INDEX idx_events_type_ts  ON events(event_type, ts);
```

### Nudge Lifecycle Events

| # | State | Event Name | Trigger | Key Properties |
|---|-------|-----------|---------|----------------|
| 1 | created | `nudge.created` | Nudge engine decides to send | `nudge_type`, `intent`, `mrt_arm` (treatment/control/deterministic) |
| 2 | shown | `nudge.shown` | Rendered on user's surface | `surface` (cli/web/agent), `shown_confidence` (rendered/delivered) |
| 3 | interacted | `nudge.interacted` | User clicks/taps/acknowledges | `interaction_type`, `time_to_interact_ms` |
| 4 | dismissed | `nudge.dismissed` | User explicitly dismisses | `time_to_dismiss_ms`, `dismiss_type` (dismiss/snooze) |
| 5 | converted | `nudge.converted` | Attributed outcome within correlation window | `outcome_event_id`, `attribution_method` (direct/window) |
| 6 | expired | `nudge.expired` | Attribution window closes without conversion | `ttl_seconds` |

Conversion is decoupled from interaction. Default attribution windows: 4 hours for task-completion nudges, 24 hours for session-start nudges.

The `mrt_arm` field on `nudge.created` enables causal analysis: compare outcomes where `mrt_arm = "treatment"` vs `mrt_arm = "control"` for the same nudge type and time period. Withheld nudges (control) only produce `nudge.created` and `nudge.expired` events.

### Identity Mapping (`user_mapping` table, production DB)

```sql
CREATE TABLE user_mapping (
    mapping_id     TEXT PRIMARY KEY,
    person_id      TEXT NOT NULL,
    external_id    TEXT NOT NULL,
    id_type        TEXT NOT NULL,   -- 'user_id' | 'anonymous_id' | 'api_key'
    linked_at      TEXT NOT NULL,
    source_surface TEXT NOT NULL,   -- 'web' | 'cli' | 'agent'
    UNIQUE(external_id, id_type)
);
```

GDPR erasure: delete all `user_mapping` rows for a `person_id`. Events become orphaned pseudonymous data that cannot be re-linked.

### SQLite PRAGMA Configuration

| # | PRAGMA | Value | Why |
|---|--------|-------|-----|
| 1 | `journal_mode` | `WAL` | Read-write concurrency |
| 2 | `synchronous` | `NORMAL` | Durability at transaction level, minimal perf cost |
| 3 | `busy_timeout` | `5000` | Prevents "database is locked" under concurrent access |
| 4 | `journal_size_limit` | `67108864` | Caps WAL at 64MB |

> **Feedback:** Does the data model cover the events you care about? Anything missing?
> - <approval or feedback>

## Integration

### ADHD Guidance System

The nudge engine is the primary event producer. Every nudge decision point emits `nudge.created` (with `mrt_arm` for causal analysis). The guidance system's frequency caps and throttling consume the fatigue score computed from telemetry data.

### Backlog Curation

Task prioritization nudges (e.g., "Focus on SW-42 — it's blocking two other tasks") emit lifecycle events tied to `task_id`. Conversion is measured as task state change within the attribution window, not nudge click-through.

### Daily Digest

The daily digest is a batch nudge surface. Each digest delivery emits a single `nudge.created` with `nudge_type = "daily_digest"`. Individual items within the digest are tracked as `nudge.shown` events with the digest's `nudge_id` as a parent reference in `properties`.

### Web Dashboard

Client-side capture via Beacon API (`navigator.sendBeacon()`) with 30-second flush interval. Events batch in memory and flush on `visibilitychange` (tab close/switch) or at 20-event threshold. The `/api/telemetry/batch` endpoint routes to the same background writer thread.

### CLI

Telemetry consent follows VS Code tiered model (`off`/`errors`/`usage`/`all`) with Homebrew-style env var kill switch (`AI_CORE_NO_TELEMETRY=1`). Config stored in `~/.config/core-cli/telemetry.toml`. Content-free events only — never log task names, file paths, or nudge text.

### Fatigue Score

Composite score computed from telemetry data, consumed by the nudge engine for automated throttling:

```
Fatigue = 0.4 * dismiss_rate + 0.3 * speed_dismiss_rate + 0.3 * negative_action_rate
```

| # | Score Range | Interpretation | Automated Action |
|---|-------------|---------------|-----------------|
| 1 | 0.00 - 0.15 | Healthy | None |
| 2 | 0.15 - 0.30 | Elevated | Reduce frequency 25% |
| 3 | 0.30 - 0.50 | High | Pause non-critical nudges, switch to digest mode |
| 4 | 0.50+ | Critical | Pause all nudges for 7 days |

> **Feedback:** Does the integration coverage look right? Any surfaces or systems missing?
> - <approval or feedback>

## Implementation Phases

<!-- Per-phase task ACs follow the canonical AC quality rules. `docs/procedures/task-authoring-standards.md`
  is AUTHORITATIVE (open it for the full/latest standard; this inline reminder is sync-checked
  against its canonical block by `companion validate-doc` and must not be edited independently): -->

<!-- doc:ac-rules:mirror:begin -->
- Every AC is independently testable — a test can fail if only this AC is violated.
- Every AC is falsifiable — "works correctly" is not an AC.
- Use EARS as the default for textual behavioral ACs: `When <trigger>, the system shall <response>` (event-driven); `While <state>` / `Where <feature>` (state-driven / optional); `If <condition>, then the system shall <response>` (unwanted-behavior / failure path). When a decision table, state machine, formula, executable Gherkin, property, or contract expresses the behavior more clearly, wrap it in an `<!-- ac-format: <value> ... --> ... <!-- /ac-format -->` scope (`decision-table` / `state-machine` / `formula` / `gherkin` / `property` / `contract`; unmarked ACs default to `ears`). Full per-format `ac-format` schemas are normative at `task-authoring-standards.md` § Per-Format AC Schemas — **always check that live source directly for the current schemas before relying on this reminder; this mirrored block itself can drift out of date and must never be treated as authoritative on its own.**
- At least one failure-path AC per public function changed — EARS `If <condition>, then the system shall …`, or the marked format's own negative-path convention (a decision table's infeasible-combination row, a state machine's invalid-transition row, a formula's invalid-input row).
- Replacement/refactor tasks: inventory the existing behaviors, then a parity AC for each (preserved, or intentionally dropped + reason).
<!-- doc:ac-rules:mirror:end -->

<!-- SPEC RIGOR (implementation-readiness) — so a sub-agent executes this from the doc alone
  (task-spec best-practices research R-1780610095; full standard: docs/procedures/task-authoring-standards.md):
  • Ship each AC as an executable test where feasible; commit failing tests first.
  • Mandate >=1 NON-MOCKED behavioral assertion per behavior — do not mock the primary inputs;
   gate on mutation score, treat line coverage as a floor not a target.
  • Spec the WHAT (I/O, edge cases, failure paths, parity), NOT the HOW (internal data
   structures, algorithm, naming) — over-constraining internals degrades quality.
  • Exit gates are harness-enforced, runnable predicates (run the suite; fresh-context diff
   review against the ACs), never self-declared "done". -->

### Tier 1: SQLite Event Table + Background Writer + Basic Lifecycle

**Goal:** Instrument all nudge delivery points. Get events flowing into `analytics.db`.

- [ ] Create `analytics.db` with event schema and indexes
- [ ] Implement `EventWriter` (in-process queue + background writer thread)
- [ ] Instrument nudge engine to emit 5-state lifecycle events
- [ ] Add `mrt_arm` field and probabilistic delivery (70/30) to nudge engine
- [ ] Implement CLI telemetry consent (`telemetry.toml` + env var kill switch)
- [ ] Implement web Beacon API capture + `/api/telemetry/batch` endpoint
- [ ] Implement `user_mapping` table and identity stitching on CLI auth
- [ ] Add fatigue score computation (dismiss rate + speed + NAR)
- [ ] Wire fatigue score into nudge frequency caps
- [ ] Monthly time-based sharding (attached DB pattern for O(1) purge)

**Analysis at this tier:** Qualitative interviews + ad-hoc SQL queries against `analytics.db`. No dashboards, no automated reports.

### Tier 2: DuckDB Sidecar + Cohort Analysis

**Trigger:** Query latency >1s on cohort queries, or ~500+ users.

- [ ] Add DuckDB dependency, attach to `analytics.db` for analytical queries
- [ ] Build cohort retention queries (DuckDB window functions)
- [ ] Implement MRT analysis pipeline (Bambi/PyMC hierarchical models)
- [ ] Implement BSTS analysis for system-wide changes (CausalImpact)
- [ ] Add Bayesian SPRT for early stopping of failing nudge types
- [ ] Build basic effectiveness dashboard (web surface)

### Tier 3: ClickHouse (only if needed)

**Trigger:** DuckDB query performance degrades. Probably never at core-cli's scale.

- [ ] Migrate event ingestion to ClickHouse
- [ ] Adapt analytical queries

> **Feedback:** Does the phasing feel right — too big, too small? Should anything move earlier or later?
> - <approval or feedback>

## Risks and Mitigations

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| 1 | Over-engineering telemetry before product-market fit | Wasted effort on instrumentation nobody queries | Tier 1 is deliberately minimal — single table, background thread, ad-hoc SQL. No dashboards or pipelines until Tier 2 |
| 2 | MRT probabilistic delivery degrades user experience | 30% of nudge opportunities are withheld (control arm) | `mrt_arm = "deterministic"` for critical alerts (always delivered). Only recurring, non-critical nudges participate in MRT |
| 3 | Fatigue score thresholds are miscalibrated | Over-throttling (user misses useful nudges) or under-throttling (user gets annoyed) | Thresholds are starting points from industry benchmarks. Calibrate via MRT data as it accumulates |
| 4 | SQLite write contention under load | Analytics writes block production DB | Separate `analytics.db` file eliminates contention. Background writer serializes all writes through single thread |
| 5 | GDPR erasure request with incomplete mapping cleanup | Pseudonymized events theoretically re-linkable | Daily-rotated salts + mapping deletion makes re-identification computationally infeasible. Upgrade to crypto-shredding if audited |
| 6 | Clock drift across CLI (offline) and web (online) | Broken causal ordering (completion appears before nudge) | Standard timestamps sufficient while CLI is online. HLC implementation deferred — schema accommodates it without migration |

## Open Questions

1. Should the fatigue score weights (0.4/0.3/0.3) be configurable per user or per nudge type, or is a global default sufficient for Tier 1?
> - <approval or feedback>
2. What is the right MRT randomization probability? 70/30 (send/withhold) is the research default, but higher withhold rates give faster statistical power at the cost of more missed nudges.
> - <approval or feedback>
3. Should we implement the Atlassian-style "daily state summary" as a CLI minimal-telemetry fallback (`level = "usage"`), or is the full event stream sufficient for all consent levels?
> - <approval or feedback>
4. At what point do we build a telemetry dashboard in the web surface? Tier 2 trigger (>500 users), or earlier if we find ourselves running the same ad-hoc queries repeatedly?
> - <approval or feedback>
5. Should the attribution window (4h task / 24h session) be static or adaptive based on observed time-to-event distributions?
> - <approval or feedback>

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|

<!-- /doc:region name="overview" -->

<!-- doc:region name="decisions" kind="replaceable" -->

(empty — populated as work progresses)

<!-- /doc:region name="decisions" -->

<!-- doc:region name="feedback_rounds" kind="append_only" -->

(empty — populated as work progresses)

<!-- /doc:region name="feedback_rounds" -->

<!-- doc:region name="approval_log" kind="append_only" -->

(empty — populated as work progresses)

<!-- /doc:region name="approval_log" -->
