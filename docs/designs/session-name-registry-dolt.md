---
title: "Session-name registry on Dolt"
category: design
tags: [design, name-registry, dolt, sqlite]
status: draft
source: "claude-opus-5-5-2026-11-11"
template_version: "design-1.0.0"
task: AI-CLI-6xud
delegation_provenance:
  version: 2
  contributors: []
---

# Session-name registry on Dolt

## Founding Ask Coverage

Tier 3. `founding_ask_ref_sha256`
`6b5df6dc0d9aef7bd45832af903ad5d8f4439221b051da5e1969a4f4d92dd9fb`, record content sha256
`c68b87eddcba35b45a9a82b768a42dacd14f4c6b8cf2826318db02829b490646`, entry `A001`, generation 1.
The record's `canonical_root_json` names the operator's private configuration repository and the
record carries private session and host names, so neither is reproduced in this public
repository; the digests above bind it. This index is non-authoritative; the raw record governs.

| Authoritative raw entry | Coverage and disposition |
|---|---|
| A001, ask 1: a status for general approval, a separate status for approval in a specific session, and columns for reserved, migrated and in-use | [Data Model](#data-model): `names.status`, the `verdicts` table with `session` scope, and the `holds` and `migrations` tables; [D-3](#d-3) |
| A001, ask 2: never reviewed, rejected in general, approved in general but not yet for a session, and rejections per repository, host or session; "maybe can store a json"; "whatever is robust" | The four states are derived exactly in [Status model](#status-model). Scoped rejections and approvals are `verdicts` rows. The JSON suggestion is answered in [D-3](#d-3): a normalized table, chosen over a JSON column |
| A001, ask 3: "what is the name database", "is it git tracked? it should be" | [Problem Statement](#problem-statement) answers the first. The Dolt database lives on a ref of the private fleet repository ([D-2](#d-2)). The reading of "git tracked" is stated in [Open Questions](#open-questions) item 1 for the owner to confirm |
| A001, ask 4: one replacement name for another; those names are approved to migrate | Seed verdicts in [Phase 3](#phase-3-import-seed-and-queries) (T-3.2) |
| A001, ask 5: "what is the best git tracked database system to use? dolt? if so, let's use dolt" | Answered by the research doc's Recommendation; this design builds on Dolt |
| A001, ask 6: "i don't like the git ref json etc system"; best fit "for schema etc and searching" | The JSON-files-on-a-branch store is replaced outright ([Phase 4](#phase-4-fleet-cutover)); engine-checked schema in [Data Model](#data-model); search in T-3.4 |
| A001, ask 7: "what about sqlite? or maybe some other database system?"; "i'm fine with having a dependency for ai-cli-utils to install" | The research doc compared them. SQLite serves only public installs with no fleet ([D-5](#d-5)). The `dolt` binary is the accepted dependency ([D-6](#d-6)) |
| A001, ask 8: tell the other sessions it is changing; track down the feedback given to another session | The cutover runbook announces the freeze ([Phase 4](#phase-4-fleet-cutover), T-4.3). The earlier feedback becomes seed rows (T-3.2) |
| A001, ask 9: "they should use opus 5.5, not codex ofc" | Process: authored by an Opus orchestrator; the implementation leaf is Opus. Codex is cross-family review only |
| A001, ask 10: "so yeah, then let's do dolt" | This design |
| A001, earlier message: approved and needs-review statuses, a seed of the owner's pre-approved names plus one more, and "if we need to add a status label ... then let's add that" | `unreviewed` is the needs-review status; the seed is T-3.2; the status label is the `status` column |
| A001, amendment: full breadth and depth research, "including any we're not currently considering" | Satisfied by the research doc, converged before this design |
| A001, routing message: run it on a fully migrated session | Process |

**Status:** DRAFT

**Created:** 2026-11-11

**Task:** AI-CLI-6xud

**Research:** [📄 ai-cli-utils/docs/research/session-name-registry-storage.md](../research/session-name-registry-storage.md)

<!-- FEEDBACK RULES (for AI agents):
  1. Never edit, rewrite, or remove user-written feedback. It is permanent record.
  2. When the user writes feedback: commit the doc immediately BEFORE responding or revising.
  3. Each round is a --- bounded section: opening --- before Feedback Round N, closing --- after AI Response Round N.
  4. Append AI response as > **AI Response Round N:** below user feedback, then add closing --- + > **Feedback Round N+1:** prompt + closing ---.
  5. Never overwrite prior rounds.
  6. After each round, add a line item to the Approval Log: date, round N, key decisions/approvals from that round.
-->

<!-- doc:region name="overview" kind="replaceable" -->

## Table of Contents

- [Founding Ask Coverage](#founding-ask-coverage)
- [Executive Summary](#executive-summary)
- [Summary for a wide audience](#summary-for-a-wide-audience)
- [Problem Statement](#problem-statement)
- [Design Overview](#design-overview)
- [Registry on Dolt](#registry-on-dolt)
  - [Where the data lives](#where-the-data-lives)
  - [Status model](#status-model)
  - [Lifecycle of a held name](#lifecycle-of-a-held-name)
  - [Claim protocol](#claim-protocol)
  - [Access path on a host](#access-path-on-a-host)
  - [Behaviour kept from the current registry](#behaviour-kept-from-the-current-registry)
  - [Behaviour kept from the approval-status pull request](#behaviour-kept-from-the-approval-status-pull-request)
  - [Public installs](#public-installs)
- [Data Model](#data-model)
- [Integration](#integration)
- [Implementation Phases](#implementation-phases)
  - [Phase 1: Dolt backend behind the registry interface](#phase-1-dolt-backend-behind-the-registry-interface)
  - [Phase 2: Status model and the picker](#phase-2-status-model-and-the-picker)
  - [Phase 3: Import, seed and queries](#phase-3-import-seed-and-queries)
  - [Phase 4: Fleet cutover](#phase-4-fleet-cutover)
- [Implementation Audit](#implementation-audit)
- [Risks and Mitigations](#risks-and-mitigations)
- [Design Decisions](#design-decisions)
  - [Decision Summary](#decision-summary)
  - [Decision Details](#decision-details)
  - [D-1: Access path from the package to Dolt](#d-1)
  - [D-2: Where the registry database lives](#d-2)
  - [D-3: How general status and scoped verdicts are stored](#d-3)
  - [D-4: How a claim gets exactly one winner](#d-4)
  - [D-5: Public installs with no fleet repository](#d-5)
  - [D-6: Dolt version floor](#d-6)
  - [D-7: Cutover shape](#d-7)
  - [D-8: How other tools read the registry](#d-8)
  - [D-9: Who is recorded as the actor](#d-9)
- [Open Questions](#open-questions)
- [Feedback Rounds](#feedback-rounds)
- [Approval Log](#approval-log)

## Executive Summary

This design moves the fleet's session-name registry from one JSON file per name on a git branch to
a Dolt database stored on its own ref of the same private repository. Each name gets a general
status (unreviewed, rejected or approved), and approvals or rejections can be scoped to a
repository, a host or a session, each recorded with who decided and when. Held names keep their
reserved, in-use and migrated state, and Dolt keeps the history of every row. Uniqueness still
comes from the git server's compare-and-swap on push, so the client keeps a strict claim protocol:
a value unique to each attempt, no merge on a lost push, and a read-back before a win counts. The
picker only offers approved names and fails closed when the status data is missing. A public
install with no fleet keeps working on a local SQLite database with the same schema. The fleet
switches over in one frozen window, and old clients refuse rather than write to the old store.

## Summary for a wide audience

Session names will come from a real database instead of loose files. Only names the owner has
approved will be picked automatically. The owner can reject a name everywhere, or only for one
repository, machine or session. Each machine needs the `dolt` program installed. During the switch,
launches that need a new name pause for a few minutes and then resume on the new store. Nothing
changes for people using the package outside this fleet. Detail is in
[Phase 4](#phase-4-fleet-cutover).

## Problem Statement

The registry today is plain git: about 50 JSON files under `names/`, a 620-name pool, a handful of
hand-added names and a migration ledger, on one branch of a private repository, written by
fast-forward push. It keeps names unique, but it has no schema beyond a Python validator, no query
surface beyond three list commands, no record of who approved what, and no place for the owner's
verdicts. The owner rejected that system for its schema and search story and asked for a
git-tracked database with general and scoped approval statuses and reserved, in-use and migrated
tracking. The research doc compared nine storage families and three hybrids and recommended Dolt
under eight conditions; this design carries each condition into the build.

## Design Overview

**Status:** stub; to be filled during/after implementation.

## Registry on Dolt

### Where the data lives

The fleet's registry is one Dolt database whose remote is the private fleet repository that ships
`config/session-names.toml` today. It uses its own ref, `refs/dolt/session-names`, never the
default `refs/dolt/data`, which the issue tracker's Dolt database already uses in that repository.
Each host keeps a clone under the package's data directory
(`$XDG_DATA_HOME/ai-cli-utils/session-names.dolt`, `LOCALAPPDATA` on Windows). The remote URL is
derived from the fleet repository's own `git remote get-url <registry_remote>`, prefixed `git+`, so
it uses whatever transport and credentials that repository already uses on the host.

Every `dolt` process the package starts gets two environment settings:
`DOLT_REMOTE_INFO_BRANCH` set to empty, so the registry adds no visible branch and never overwrites
the issue tracker's info branch, and a `DOLT_ROOT_PATH` whose global config sets
`metrics.disabled`, so no telemetry spool grows. `git log` of the data ref is not the registry's
history: Dolt rewrites that ref's git commits. `dolt log` and the history tables are the history.

### Status model

Every name in the pool has one general status and any number of scoped verdicts. The owner's four
states are derived, not stored separately:

| Owner's state | Derivation |
|---|---|
| Never reviewed | `names.status = 'unreviewed'` |
| Rejected in general | `names.status = 'rejected'` |
| Approved in general, not yet for a session | `names.status = 'approved'` and no `verdicts` row with `scope_kind = 'session'` and `verdict = 'approved'` |
| Approved for a session | a `verdicts` row with `scope_kind = 'session'` and `verdict = 'approved'` for that session |

A scoped rejection (`verdict = 'rejected'` for a repository, a host or a session) removes the name
from picks in that scope only. A scoped approval for a session lets that session take the name by
hand even if the general status is not `approved`; it never makes the picker offer the name
elsewhere.

The fleet denylist stops being a separate list. Each denylisted name is seeded with
`status = 'rejected'`. A name already held when it was denylisted keeps its hold and gets a
`session`-scoped `approved` verdict for its holder, so the store records both facts.

The picker's eligibility rule, for a launch in repository R on host H for session S:

1. `names.status = 'approved'`;
2. no `verdicts` row rejects the name for scope `repo = R`, `host = H` or `session = S`;
3. every existing filter (length, held, one edit from a held name, alias words, title-prefix
   pair) still applies, in its current order.

A hand-chosen name (`--name`, `migrate-session --to-name`) needs no general approval, but refuses a
name whose general status is `rejected` unless a `session`-scoped `approved` verdict exists for the
launching session, and refuses any scoped rejection that matches.

### Lifecycle of a held name

A row in `holds` is a held token; a token is held exactly while its row exists, as today. Its
`kind` and `state` give the owner's reserved, in-use and migrated vocabulary:

<!-- ac-format: state-machine id="AC-SM-HOLD" initial="absent" coverage="all-transitions-plus-invalid" verifier="uv run pytest tests/test_name_dolt.py tests/test_name_release.py -q" -->

**AC-SM-HOLD — Lifecycle of a held token**

States: `absent`, `spare-reserved` (`kind = 'spare'`, `state = 'reserved'`), `session-active`
(`kind = 'session'`, `state = 'active'`), `session-migrating` (`kind = 'session'`,
`state = 'migration-incomplete'`)
Initial state: `absent`
Event alphabet: `top-up`, `claim`, `migration-intent`, `activate-spare`, `migrate-to-own-spare`,
`migration-complete`, `release`, `spare-release`, `foreign-edit`
Terminal states: none. A released token returns to `absent` and can be held again.

| Transition | Source | Event | Guard | Target | Observable outcome |
|------------|--------|-------|-------|--------|--------------------|
| T1 | absent | top-up | the machine holds fewer than `offline_spares` unactivated spares | spare-reserved | a `holds` row of kind `spare` for that machine |
| T2 | absent | claim | the claim protocol wins | session-active | a `holds` row carrying this attempt's claimant |
| T3 | absent | migration-intent | true | session-migrating | the `holds` row and a `migrations` row in state `intent`, in one commit |
| T4 | spare-reserved | activate-spare | the activating host is the spare's `machine` | session-active | the row's activation fields set; nothing else changes |
| T5 | spare-reserved | migrate-to-own-spare | the migrating host is the spare's `machine` | session-active | as T4; the dry run reports the same plan |
| T6 | session-migrating | migration-complete | true | session-active | the `migrations` row moves to `complete` in the same commit |
| T7 | session-active | release | both absence tests pass and the row is unchanged since the test | absent | the `holds` row deleted; its history kept in `dolt_history_holds` |
| T8 | spare-reserved | spare-release | the spare-release test passes under the spool lock | absent | the `holds` row deleted |
| T9 | any state but absent | foreign-edit | the editing host is not the row's `machine` | unchanged | refused with exit 2; nothing written |
| T10 | spare-reserved | activate-spare or migrate-to-own-spare | the host is not the spare's `machine` | unchanged | refused as held |
| T11 | any | any event not listed for that state (for example `release` on `absent`) | true | unchanged | reject with an invalid-transition refusal (exit 2); nothing written |

Guard exclusivity: T4 and T10, and T5 and T10, share a source and event with complementary guards
(the host is, or is not, the spare's machine). No other source and event pair has more than one
row. Invalid transitions: T9 to T11. Coverage requirement: exercise T1 to T11.

<!-- /ac-format -->

"Migrated" is a `migrations` row in state `complete` linking the old title to the new name.
"In use" is `kind = 'session'` and `state = 'active'`. Released rows are deleted; their history
stays in `dolt_history_holds`.

**Held spares.** A machine's own unactivated spare may be taken by a hand-chosen migrate on that
machine: `migrate-session --to-name <spare>` activates it instead of refusing it as held, and the
dry run reports the same plan the real run would execute. Another machine's spare stays refused as
held.

**Reclaiming a dead session's name** keeps today's rule: release and reconcile delete a row only
when both absence tests pass and the row still matches the version that was tested. This design
adds no time-based expiry; the existing per-session lease files are the evidence the absence test
already reads.

### Claim protocol

Every write follows one loop. It is today's `transact` loop with Dolt in place of the git tree, and
the research doc's five rules made concrete:

1. Take the package's host file lock for the database directory, so one process writes at a time.
2. `dolt fetch` the registry ref, then `dolt reset --hard origin/main`, so the write starts from
   the remote tip.
3. Run the guard and the write as one `dolt sql` script: the guard queries, then plain `INSERT`,
   `UPDATE` or `DELETE` statements, never an upsert, then `call dolt_commit(...)`. A claim row
   carries `claimant = <host>:<session>:<nonce>`, a value unique to this attempt.
4. Confirm the commit exists in `dolt_log`. A write that did not commit (a read-only open, a
   constraint failure, a guard refusal) raises and pushes nothing.
5. `dolt push origin main`. The client never passes `--force` and never pushes any other way.
6. On a rejected push: reset to the fetched remote tip and go back to step 2, never `dolt pull`
   and never a merge. After `publish_attempts` losses, raise `RegistryContendedError`, and the
   launch falls back to a spare as it does today.
7. On a successful push: fetch again and read the row back from `origin/main`. Only a row carrying
   this attempt's `claimant` counts as a win. A row with another claimant means another host won;
   the picker picks again and a hand-chosen name is refused with the winner named.

Any other failure (timeout, authentication, a missing `dolt` binary) raises
`RegistryUnreachableError`, as today, and no pick is made from unverified data.

### Access path on a host

The package runs the `dolt` CLI as a subprocess, `dolt sql -r json -q ...` for reads and a
script on stdin for writes, against the clone directory. It never starts or connects to a
`dolt sql-server`, and it never touches the issue tracker's shared server or its databases. The
host file lock (`portalocker`, already a dependency) serializes the package's own processes, so two
CLI processes never open the database for writing together. Reads take the same lock for the short
time a read runs, because Dolt's own lock falls back silently to read-only for a second opener.

Each call has a timeout taken from the existing `publish_timeout_s` policy value. A `dolt` binary
older than the version floor, or a database whose `meta.min_reader_version` is above the package's,
refuses with a message naming the upgrade.

### Behaviour kept from the current registry

Every behaviour in this list keeps its current guard and its current tests' intent; Phase 1 ports
the tests to the new backend:

- one fleet-wide namespace for session, spare and label tokens;
- every write re-runs its whole guard on the fresh tip, retrying up to `publish_attempts`, then
  `RegistryContendedError`; a picked name that loses is picked again, a hand-chosen one is refused
  with the new tip's reason;
- several changes land in one atomic commit: reserve with spare top-up, reserve with manual add,
  reserve with migration intent, completion with release of the old row;
- the published policy snapshot is what every allocation applies, and an unknown schema or key
  refuses with "update ai-cli-utils";
- `sync-names` regenerates the pool only from a newer policy revision;
- the offline path: `offline_spares` per machine, `take_spare` under `spool.lock` with a durable
  spool, published by the next command; an unreachable registry on a `--name` launch refuses;
- release needs both absence tests; reconcile deletes only an unchanged row; there is no force
  release;
- the migration ledger: intent in the reserve commit, completion with the old row's release,
  idempotent recovery;
- `check-names` reports over-limit, grandfathered, incomplete and stale intents, with its exit
  codes; exit 2 refusal, 3 unreadable, 75 unreachable;
- `known_names` works with no network and includes the shipped seed;
- rows never hold an absolute path; only the owning machine edits or deletes a row;
  `reassign-name` moves ownership without freeing the name.

### Behaviour kept from the approval-status pull request

The unmerged approval-status pull request (#494) built the first status layer on the old store.
This design supersedes it and keeps these behaviours, with its tests ported:

- the picker fails closed: missing or unreadable status data raises, and a launch exits 1;
- only approved names are picked, including spare top-up and offline `take_spare`, and the refusal
  counts the shortfall ("{N} of the {M} names in the database are approved, and every one was
  filtered out") and names the command that approves a name;
- `set-name-status STATUS NAME...` sets many names in one commit, refuses the whole call when any
  name is outside the pool, and records the actor and time;
- `list-names` shows the status column and filters by pool;
- reconcile skips spare top-up when status data is unusable but still releases;
- a seed step, run once, gives the fleet its starting verdicts.

The pull request's `excluded` value becomes `rejected`, and its `needs-review` becomes
`unreviewed`.

### Public installs

With no sibling repository shipping `config/session-names.toml`, the package keeps the registry in
a local SQLite database at the same data directory, with the same tables and constraints, guarded
by the same host file lock. It never needs the `dolt` binary. Its seed is the shipped name list,
all `approved`, so a public user's picks work out of the box, as #494's local seed did.

## Data Model

One schema, written once in a package module that emits the DDL for both dialects (Dolt's MySQL
dialect and SQLite). Types below are the Dolt form.

```sql
create table meta (
  k varchar(64) primary key,
  v varchar(256) not null
);
-- rows: schema_version, min_reader_version, min_dolt_version

create table policy_snapshot (
  revision varchar(64) primary key,     -- the policy commit the snapshot came from
  generated_at varchar(20) not null,
  body json not null                    -- today's generation.json content
);

create table names (
  name varchar(16) primary key,
  origin enum('generated','manual','shipped') not null,
  status enum('unreviewed','rejected','approved') not null default 'unreviewed',
  status_actor varchar(128),
  status_recorded_by varchar(128),
  status_set_at varchar(20),
  status_reason varchar(256),
  added_by varchar(128),
  added_at varchar(20),
  added_reason varchar(256),
  check (name regexp '^[a-z]+$')
);

create table verdicts (
  name varchar(16) not null,
  scope_kind enum('repo','host','session') not null,
  scope_value varchar(128) not null,
  verdict enum('approved','rejected') not null,
  actor varchar(128) not null,
  recorded_by varchar(128) not null,
  set_at varchar(20) not null,
  reason varchar(256),
  primary key (name, scope_kind, scope_value),
  foreign key (name) references names(name)
);

create table holds (
  token varchar(16) primary key,
  kind enum('session','spare','label') not null,
  state enum('reserved','active','migration-incomplete') not null,
  machine varchar(64) not null,
  host_alias varchar(64),
  repo varchar(128),
  repo_alias varchar(64),
  worktree varchar(256),
  title varchar(128),
  client varchar(32),
  label varchar(128),
  tier varchar(32),
  source varchar(32) not null,
  recovery json,
  reserved_at varchar(20) not null,
  activated_at varchar(20),
  claimant varchar(160) not null,
  writer varchar(64) not null,
  check (token regexp '^[a-z]+$')
);

create table migrations (
  old_title varchar(128) primary key,
  new_name varchar(16) not null,
  state enum('intent','complete') not null,
  machine varchar(64) not null,
  recorded_at varchar(20) not null,
  completed_at varchar(20)
);
```

The `holds` fields are today's 19 row fields plus `claimant`; the field rules `row_problem` checks
today (per-kind non-null and null fields, `recovery` set exactly while `migration-incomplete`, no
absolute paths) stay in the package's validator and also become `check` constraints where the
dialect can express them. `holds.token` has no foreign key to `names`, because a label's last
token can be held without being in the pool, as today. The SQLite form replaces `enum` with a
`check (... in (...))`, `json` with `text`, and the `regexp` checks with
`name glob '[a-z]*' and name not glob '*[^a-z]*'`.

History needs no table of its own: `dolt_history_names`, `dolt_history_verdicts` and
`dolt_history_holds` hold every row's value at every commit, with the committer and time. The
SQLite backend has no history, which public installs do not need.

## Integration

- **The fleet repository** keeps `config/session-names.toml`. Phase 4 bumps it to `schema = 2`,
  adds `registry_backend = "dolt"` and `registry_dolt_ref = "refs/dolt/session-names"`, and drops
  `registry_ref` and `denylist` (the denylist moves into `names.status`).
- **Tools in the fleet repository that read the old tree today** (the published-count check in its
  install step, the session-name sweep that reads `migrations/`, the pre-push exemption for the
  ref prefix and the branch-retirement census) change to read `ai list-names --json` and
  `ai list-migrations --json` ([D-8](#d-8)), or drop the old ref's special case.
- **The issue tracker's Dolt database** shares the repository but nothing else: another ref,
  another clone directory, no shared server, no info branch.
- **The approval-status pull request (#494)** is closed as superseded when Phase 4 lands.

## Implementation Phases

<!-- doc:ac-rules:mirror:begin -->
- Every AC is independently testable — a test can fail if only this AC is violated.
- Every AC is falsifiable — "works correctly" is not an AC.
- Use EARS as the default for textual behavioral ACs: `When <trigger>, the system shall <response>` (event-driven); `While <state>` / `Where <feature>` (state-driven / optional); `If <condition>, then the system shall <response>` (unwanted-behavior / failure path). When a decision table, state machine, formula, executable Gherkin, property, or contract expresses the behavior more clearly, wrap it in an `<!-- ac-format: <value> ... --> ... <!-- /ac-format -->` scope (`decision-table` / `state-machine` / `formula` / `gherkin` / `property` / `contract`; unmarked ACs default to `ears`). Full per-format `ac-format` schemas are normative at `task-authoring-standards.md` § Per-Format AC Schemas — **always check that live source directly for the current schemas before relying on this reminder; this mirrored block itself can drift out of date and must never be treated as authoritative on its own.**
- At least one failure-path AC per public function changed — EARS `If <condition>, then the system shall …`, or the marked format's own negative-path convention (a decision table's infeasible-combination row, a state machine's invalid-transition row, a formula's invalid-input row).
- Replacement/refactor tasks: inventory the existing behaviors, then a parity AC for each (preserved, or intentionally dropped + reason).
<!-- doc:ac-rules:mirror:end -->

Every test that exercises the Dolt backend runs a real `dolt` binary against a local bare git
remote, like the current fixture's bare git remote. CI installs `dolt` on Linux, macOS and Windows;
a Dolt test never skips because the binary is missing in CI, and a local run without `dolt` reports
those tests as not run, never as passed.

### Phase 1: Dolt backend behind the registry interface

- **Scope:** a `DoltRegistry` and a `SqliteRegistry` implementing today's `Registry` interface
  (snapshot, transact), the shared schema module, and the claim protocol. Every current registry
  behaviour passes on both backends. No status model yet; no fleet change.
- **Deliverables:**
  - Files created: `src/ai_cli/name_schema.py`, `src/ai_cli/name_dolt.py`, `src/ai_cli/name_sqlite.py`
  - Files modified: `src/ai_cli/name_registry.py` (backend selection, `Snapshot` over rows),
    `src/ai_cli/config.py` (Dolt timeouts), `tests/name_fleet.py` (a Dolt-on-bare-remote fleet)
  - Tests added: `tests/test_name_dolt.py`; the existing registry, release, migrate, prompt, title,
    launch and list suites run against both backends
- **Tasks + acceptance criteria:**
  - **T-1.1 Schema** — one module emits both dialects.
    - [ ] When the schema is applied to an empty Dolt database and to an empty SQLite file, the system shall create the six tables with the constraints in [Data Model](#data-model).
    - [ ] If a `names` or `holds` insert carries a token outside `[a-z]+` (for example `Ava` or `ava2`), then the system shall refuse it at the database on both backends.
  - **T-1.2 Claim protocol** — the seven-step loop.
    - [ ] When two hosts claim the same free name at the same moment over a local bare remote, repeated 20 times, the system shall give exactly one winner in each trial, and the loser's caller shall see the winner's claimant.
    - [ ] When two hosts claim the same name with otherwise identical row values, the system shall still give exactly one winner, because each claimant value is unique.
    - [ ] If a push is rejected, then the system shall reset to the remote tip and re-run the whole guard, and shall never run `dolt pull` or a merge.
    - [ ] If every one of `publish_attempts` attempts is rejected, then the system shall raise `RegistryContendedError` and leave the remote unchanged by this caller.
    - [ ] If the `dolt sql` write does not produce a commit in `dolt_log`, then the system shall push nothing and raise.
    - [ ] The system shall start no `dolt` process without `DOLT_REMOTE_INFO_BRANCH` set to empty and `metrics.disabled` set, and a test shall observe that the bare remote gains no `__dolt_remote_info__` branch.
  - **T-1.3 Access path**
    - [ ] When two package processes on one host write at once, the system shall serialize them through the host file lock, and both writes shall land.
    - [ ] If the `dolt` binary is missing, then the system shall raise `RegistryUnreachableError` with a message naming the install step.
    - [ ] If `dolt version` reports a version below the floor in [D-6](#d-6), or `meta.min_reader_version` is above the package's, then the system shall refuse every read and write with a message naming the upgrade.
  - **T-1.4 Parity** — every behaviour in [Behaviour kept from the current registry](#behaviour-kept-from-the-current-registry).
    - [ ] For each listed behaviour, the existing test that covers it shall pass against `DoltRegistry` and `SqliteRegistry`, or the test shall be ported with the same assertion.
- **Exit gate:** all Phase 1 ACs green; `uv run pytest` passes on Linux and in CI on macOS and Windows with `dolt` installed; a fresh-context diff review against these ACs.

### Phase 2: Status model and the picker

- **Scope:** general status, scoped verdicts, the eligibility rule, and the #494 behaviours.
- **Deliverables:**
  - Files modified: `src/ai_cli/name_registry.py`, `src/ai_cli/session_naming.py`,
    `src/ai_cli/name_prompt.py`, `src/ai_cli/name_commands.py`, `src/ai_cli/main.py`
  - Tests added: `tests/test_session_name_status.py`, `tests/test_session_name_verdicts.py`, and
    launch-level tests in `tests/test_session_name_launch.py`
- **Tasks + acceptance criteria:**
  - **T-2.1 Eligibility**: the decision table AC-DT-ELIG below, plus:
    - [ ] If the status data is missing or unreadable, then the system shall refuse every pick, spare top-up and offline spare take, and a launch shall exit 1.
    - [ ] If no approved name survives the filters, then the system shall refuse with the count of approved names, the count filtered out, and the command that approves a name.
  - **T-2.2 `set-name-status`** — `ai set-name-status -a/--actor ACTOR -r/--reason TEXT STATUS NAME...`
    - [ ] When given several names, the system shall set them all in one commit, each with actor, recorder and time.
    - [ ] If any name is outside the pool, then the system shall refuse the whole call and write nothing.
  - **T-2.3 `set-name-verdict`** — `ai set-name-verdict -s/--scope repo|host|session -v/--value VALUE -a/--actor ACTOR VERDICT NAME...`, and `-d/--delete` to remove a verdict.
    - [ ] When a verdict is set, the system shall write one `verdicts` row per name, keyed by name and scope.
    - [ ] If the scope kind is not one of `repo`, `host`, `session`, then the system shall refuse with exit 2.
  - **T-2.4 Held spares**
    - [ ] When `migrate-session --to-name` names an unactivated spare of the same machine, the system shall activate that spare, and the dry run shall report the same plan.
    - [ ] If the spare belongs to another machine, then the system shall refuse it as held.
- **Exit gate:** all Phase 2 ACs green; the ported #494 tests pass; `uv run pytest` green; a fresh-context diff review against these ACs.

<!-- ac-format: decision-table id="AC-DT-ELIG" coverage="all-feasible-rules" verifier="uv run pytest tests/test_session_name_verdicts.py -q" -->

**AC-DT-ELIG — Picker and hand-choice eligibility**

Condition domains:
- `status ∈ {approved, unreviewed, rejected}`: the name's general status
- `scoped_rejection ∈ {yes, no}`: a `verdicts` row rejects the name for the launch's repository, host or session
- `session_approval ∈ {yes, no}`: a `verdicts` row approves the name for the launching session
- `*` means the condition is irrelevant to that rule.

| Rule | IF status | IF scoped_rejection | IF session_approval | THEN picker offers it | THEN hand-choice allowed |
|------|-----------|---------------------|---------------------|-----------------------|--------------------------|
| R1 | * | yes | * | no | no, refused naming the rejecting scope |
| R2 | approved | no | * | yes | yes |
| R3 | unreviewed | no | no | no | yes |
| R4 | unreviewed | no | yes | no | yes |
| R5 | rejected | no | no | no | no, refused as rejected |
| R6 | rejected | no | yes | no | yes |

Precedence: R1 applies first; every other combination matches exactly one of R2 to R6. No
combination is infeasible. The existing filters (length, held, one edit from a held name, alias
words, title-prefix pair) still apply after this table. Coverage requirement: exercise R1 to R6,
asserting both the pick result and the hand-choice result.

<!-- /ac-format -->

### Phase 3: Import, seed and queries

- **Scope:** a one-shot importer from the old git tree, the seed, and the query commands.
- **Deliverables:**
  - Files created: `src/ai_cli/name_import.py`
  - Files modified: `src/ai_cli/name_commands.py`, `src/ai_cli/main.py`
  - Tests added: `tests/test_name_import.py`, `tests/test_name_queries.py`
- **Tasks + acceptance criteria:**
  - **T-3.1 `import-names`** — `ai import-names -f/--from-ref REF -d/--dry-run`.
    - [ ] When run against a registry tree, the system shall write one Dolt commit holding every pool name, manual name, held row and migration record from that tree, and a second run shall report nothing to import.
    - [ ] When the import completes, the system shall print counts that match the tree: names, manual names, held rows by kind, migrations by state.
    - [ ] If the tree has a row that fails today's validator, then the system shall refuse the whole import and name the row.
  - **T-3.2 Seed** — `ai seed-name-verdicts -f/--file PATH`, reading a TOML file kept in the fleet repository, never in this package.
    - [ ] When seeded, the system shall apply every listed general status and scoped verdict with the file's actor, and the denylisted names shall become `rejected`.
    - [ ] When a denylisted name is held, the system shall keep the hold and add a session-scoped `approved` verdict for its holder.
    - [ ] If the seed file names a name outside the pool, then the system shall refuse the whole seed.
  - **T-3.3 `list-migrations`** — `ai list-migrations -j/--json`.
    - [ ] When run, the system shall list every migration row with its state, as JSON with `--json`.
  - **T-3.4 `find-names`** — `ai find-names -s/--status S -k/--scope KIND -v/--value VALUE -m/--machine M -t/--state STATE -j/--json`.
    - [ ] When given filters, the system shall return only names matching all of them, from one SQL query.
    - [ ] When asked for a name's history with `-H/--history NAME`, the system shall list every past value of its status, verdicts and hold with time and committer (Dolt only).
    - [ ] If `--history` is asked of the SQLite backend, then the system shall refuse with a message that history needs the fleet registry.
- **Exit gate:** all Phase 3 ACs green; an import of a copy of the live tree, run on a scratch remote, prints counts equal to that tree's measured counts; `uv run pytest` green.

### Phase 4: Fleet cutover

- **Scope:** the switch on every fleet host, the fleet repository's readers, and rollback.
- **Deliverables:**
  - Fleet repository (private): `config/session-names.toml` at schema 2, the seed file, the readers
    changed per [Integration](#integration), and the cutover runbook.
  - This repository: a release carrying Phases 1 to 3.
- **Tasks + acceptance criteria:**
  - **T-4.1 Freeze** — publish a policy snapshot with `schema = 2` to the old registry branch.
    - [ ] When the frozen snapshot is published, every client older than this release shall refuse every allocation with "update ai-cli-utils", and the old branch shall receive no further commit.
  - **T-4.2 Import and flip** — on one host: `import-names` from the frozen tip, `seed-name-verdicts`, then merge the schema 2 policy into the fleet repository.
    - [ ] When the flip lands, `check-names` on that host shall exit 0 and `list-names` shall show the imported counts.
  - **T-4.3 Roll out** — each host installs the release, runs `check-names`, and launches one session. Sessions are told before the freeze and after the last host.
    - [ ] When every host has run `check-names` with exit 0 and launched one named session, the cutover shall be recorded complete in the runbook.
  - **T-4.4 Rollback** — revert the schema 2 policy commit and the frozen snapshot; the old branch is unchanged since the freeze. Holds created in Dolt after the flip are listed with `find-names` and re-created on the old store by hand.
    - [ ] When the rollback is rehearsed on a scratch copy before the real cutover, old clients shall allocate again and the listed holds shall be re-created.
  - **T-4.5 Close the old system** — after a week without rollback, delete the old registry branch (its tip anchored first) and close #494 as superseded.
- **Exit gate:** every host passes T-4.3; the fleet repository's readers pass their own tests on schema 2; the rollback rehearsal is recorded.

> **Feedback Round 1:** Does the phasing feel right: too big, too small? Should anything move earlier or later?
> - <enter feedback here>

## Implementation Audit

| # | Phase | Section / Decision | Verified | Notes |
|---|-------|--------------------|---------|-------|
| 1 | N/A | **Design Overview filled**: confirm the `## Design Overview` section above is filled in with concrete implementation knowledge, not left as a stub | - [ ] | |
| 2 | Phase 1 | Claim protocol, D-4 | - [ ] | |
| 3 | Phase 1 | Access path, D-1, D-6 | - [ ] | |
| 4 | Phase 1 | Public installs, D-5 | - [ ] | |
| 5 | Phase 2 | Status model, D-3, D-9 | - [ ] | |
| 6 | Phase 3 | Import, seed and queries | - [ ] | |
| 7 | Phase 4 | Cutover, D-2, D-7, D-8 | - [ ] | |

**Audit completed:**

## Risks and Mitigations

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| 1 | Two hosts both believe they won a name (the identical-value merge) | Two sessions share a name | Unique claimant per attempt; no merge on a lost push; read-back before a win (D-4) |
| 2 | A host's `dolt` is older than the database's feature version | That host cannot read the registry | Version floor checked on open (D-6); every host upgrades before the flip |
| 3 | A silent read-only open on a second opener | A write that seems to happen but did not | Host file lock around every call; the commit is confirmed in `dolt_log` before any push |
| 4 | A hosted push takes about 4.7 s, against about 1.2 s today | Slower named launches | Spares take most launches off the network; refill after the session starts |
| 5 | Telemetry spool growth | Disk filled on a host | `metrics.disabled` set by the package on every call |
| 6 | The registry and the issue tracker interfere in one repository | One overwrites the other's info branch or ref | Separate ref; info branch off; separate clone; no shared server (D-2) |
| 7 | The cutover window stalls launches | Sessions cannot get new names | Old clients fail closed; spares cover offline launches; the window is minutes; rehearsed rollback |
| 8 | A hand edit in SQL bypasses the client | A case twin or invalid row | Constraints in the table itself, not only in the client |

<!-- /doc:region name="overview" -->

<!-- doc:region name="decisions" kind="replaceable" -->

## Design Decisions

### Decision Summary

| # | Decision | Options Considered | Recommended (AI) | Chosen | Diverged? | Rationale | Status |
|---|----------|-------------------|------------------|--------|-----------|-----------|--------|
| D-1 | Access path from the package to Dolt | (a) `dolt` CLI subprocess, (b) per-host `dolt sql-server` with PyMySQL, (c) the issue tracker's shared server | (a) | (a) | No | No server on the launch path, no new Python dependency, no coupling to the tracker | ✅ Resolved |
| D-2 | Where the registry database lives | (a) own ref in the fleet repository, (b) a dedicated private repository, (c) a table in the tracker's database | (a) | (a) | No | Same access and transport as today, isolated by ref | ✅ Resolved |
| D-3 | How status and verdicts are stored | (a) normalized `names` plus `verdicts`, (b) a JSON column per name, (c) status only, verdicts later | (a) | (a) | No | Per-verdict actor, time, history and plain SQL search | ✅ Resolved |
| D-4 | How a claim gets one winner | (a) Dolt alone under the claim protocol, (b) a claim ref per name plus Dolt (hybrid H1) | (a) | (a) | No | One store; the protocol is today's loop; measured 20 of 20 | ✅ Resolved |
| D-5 | Public installs with no fleet | (a) local SQLite with the same schema, (b) local Dolt, (c) keep the JSON file tree | (a) | (a) | No | Works with no extra install on every platform | ✅ Resolved |
| D-6 | Dolt version floor | (a) 2.3.4, the fleet's version, (b) 2.4.1, (c) latest 2.7.x | (a) | (a) | No | No forced upgrade of the tracker's binary; nothing needed is newer | ✅ Resolved |
| D-7 | Cutover shape | (a) one frozen window, (b) dual-write period, (c) per-host gradual | (a) | (a) | No | One store at a time; old clients fail closed by policy schema | ✅ Resolved |
| D-8 | How other tools read the registry | (a) through the `ai` CLI's JSON, (b) direct Dolt reads | (a) | (a) | No | One access path, one schema owner | ✅ Resolved |
| D-9 | Who is recorded as the actor | (a) separate `actor` and `recorded_by`, (b) the agent session only | (a) | (a) | No | The owner's verdicts are relayed by agents; both facts matter | ✅ Resolved |

### Decision Details

<a id="d-1"></a>

#### D-1: Access path from the package to Dolt — ✅ Approved: (a) `dolt` CLI subprocess

**Context.** The package is a short-lived CLI run at every session launch on several hosts. Dolt
offers a CLI and a MySQL-protocol server; the fleet's issue tracker already runs one shared server
per host. An earlier fleet design rejected Dolt partly for "a running `dolt sql-server` on the
launch path of every host".

##### (a) `dolt` CLI subprocess per call

**Pros:**
- No server to start, supervise or reach on the launch path.
- No Python dependency: the package parses `-r json` output.
- Measured fast enough: a read 0.23 s, a write with commit 0.19 s on the fleet's version.

**Cons:**
- One process per call, so a command that needs several queries pays several starts; batch them
  into one script per call.
- Dolt's own lock falls back to read-only for a second opener; mitigated by the host file lock and
  the commit check.

##### (b) Per-host `dolt sql-server` with PyMySQL

**Pros:**
- One connection serves many queries; real transactions.
- PyMySQL is pure Python and MIT licensed.

**Cons:**
- A server per host on the launch path, the concern the earlier design raised.
- A new dependency and a lifecycle (start, port, crash) the package must own on three platforms.

##### (c) The issue tracker's shared server

**Pros:**
- Already running on fleet hosts.

**Cons:**
- Couples the registry to another tool's server, version and incidents; the tracker's own records
  show a five-hour outage from a schema migration on its shared server.
- Absent on public installs and on any host without the tracker.

##### Recommendation

> **Decision:** ✅ Resolved — (a) `dolt` CLI subprocess per call
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-opus-5-5; ai-effort=n/a; ai-profile=interactive -->
<!-- decision-lineage: decision-id=AI-CLI-6xud/D-1; decision-topic=registry-dolt-access-path; governs=artifact:docs/designs/session-name-registry-dolt.md; normalized-proposition=the-package-reaches-dolt-through-the-cli-only; applicability=scope:fleet-registry; outcome-id=cli-subprocess; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

The CLI keeps the launch path free of a server, which answers the earlier design's main objection,
and adds no dependency. Its read-only fallback is covered by the host file lock and the commit
check in the claim protocol.

---

<a id="d-2"></a>

#### D-2: Where the registry database lives — ✅ Approved: (a) own ref in the fleet repository

**Context.** The registry's rows carry host names, repositories and session ids, so it must stay in
a private repository. The fleet repository already holds the registry branch and the issue
tracker's Dolt database on `refs/dolt/data` with its info branch.

##### (a) Its own ref in the fleet repository

**Pros:**
- Same repository, same transport and credentials every host already has.
- A separate ref and the info branch off keep it apart from the tracker.

**Cons:**
- The repository carries two Dolt databases; anyone reading its refs must know which is which.
- Pushes to the repository count against one rate limit; a few a day is far below it.

##### (b) A dedicated private repository

**Pros:**
- Complete isolation from the tracker.

**Cons:**
- A new repository to create, grant on every host and keep credentials for; an outward-facing
  setup step on each host.

##### (c) A table in the tracker's database

**Pros:**
- No second database.

**Cons:**
- Ties the registry's schema and history to the tracker's migrations and its incidents; rejected
  by the research doc's placement condition.

##### Recommendation

> **Decision:** ✅ Resolved — (a) its own ref in the fleet repository
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-opus-5-5; ai-effort=n/a; ai-profile=interactive -->
<!-- decision-lineage: decision-id=AI-CLI-6xud/D-2; decision-topic=registry-dolt-placement; governs=artifact:docs/designs/session-name-registry-dolt.md; normalized-proposition=registry-on-own-dolt-ref-of-fleet-repository; applicability=scope:fleet-registry; outcome-id=own-ref; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

The registry keeps the access it has today and is isolated by ref, clone and the info-branch
setting. The cost is knowing which of two Dolt refs is which, which the policy file names.

---

<a id="d-3"></a>

#### D-3: How general status and scoped verdicts are stored — ✅ Approved: (a) normalized tables

**Context.** The owner asked for a general status, scoped rejections by repository, host or
session, and suggested "maybe can store a json which can track white list / black list etc.
whatever is robust". The research measured that Dolt merges edits to different keys of one JSON
cell cleanly.

##### (a) A `status` column on `names` plus a `verdicts` table

**Pros:**
- Each verdict carries its own actor, recorder, time and reason, and its own row history.
- Plain SQL answers "every name rejected for this host" without JSON functions.
- The database enforces the scope kinds and verdict values.

**Cons:**
- Two tables to join in the picker query.

##### (b) A JSON column of allow and deny lists per name

**Pros:**
- Matches the owner's first suggestion literally; one row per name.

**Cons:**
- Actor and time per entry need a nested structure the engine does not check.
- Searching across names needs JSON path queries in both dialects.

##### (c) General status only, scoped verdicts later

**Pros:**
- Smallest first build.

**Cons:**
- Drops an explicit founding-ask requirement (rejection per repository, host or session); not
  permitted.

##### Recommendation

> **Decision:** ✅ Resolved — (a) a `status` column on `names` plus a `verdicts` table
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-opus-5-5; ai-effort=n/a; ai-profile=interactive -->
<!-- decision-lineage: decision-id=AI-CLI-6xud/D-3; decision-topic=registry-status-storage; governs=artifact:docs/designs/session-name-registry-dolt.md; normalized-proposition=status-column-plus-normalized-verdicts-table; applicability=scope:fleet-registry; outcome-id=normalized; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

The owner asked for "whatever is robust", and offered JSON as one possibility. A normalized table
keeps every verdict's who and when checked by the engine and searchable by plain SQL. The extra
join is one indexed lookup per pick.

---

<a id="d-4"></a>

#### D-4: How a claim gets exactly one winner — ✅ Approved: (a) Dolt alone under the claim protocol

**Context.** Every git-backed option gets uniqueness from the push compare-and-swap. Dolt merges
identical-value claims cleanly, so a client that merges a lost push can believe it won. A claim
ref per name would give one winner by construction.

##### (a) Dolt alone, under the seven-step claim protocol

**Pros:**
- One store and one write per claim.
- The protocol is today's `transact` loop; measured one winner in 20 of 20 concurrent races.

**Cons:**
- Correctness depends on every writer following the protocol; mitigated because the package is
  the only writer and tests pin each rule.

##### (b) A create-if-absent ref per name, plus Dolt for everything else (hybrid H1)

**Pros:**
- One winner by construction, with no protocol to get wrong.

**Cons:**
- Two stores per claim that must agree, and a repair rule for a crash between them.
- A second push per claim on a launch path that already costs about 4.7 s per push.

##### Recommendation

> **Decision:** ✅ Resolved — (a) Dolt alone, under the claim protocol
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-opus-5-5; ai-effort=n/a; ai-profile=interactive -->
<!-- decision-lineage: decision-id=AI-CLI-6xud/D-4; decision-topic=registry-claim-uniqueness; governs=artifact:docs/designs/session-name-registry-dolt.md; normalized-proposition=single-dolt-store-with-claim-protocol; applicability=scope:fleet-registry; outcome-id=protocol; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

The protocol is cheaper than a second store at a few claims a day, and the spare path takes most
launches off the network. T-1.2 pins the identical-value case so a regression fails a test.

---

<a id="d-5"></a>

#### D-5: Public installs with no fleet repository — ✅ Approved: (a) local SQLite with the same schema

**Context.** The package is public and cross-platform; a public user has no fleet repository and
may not have `dolt`. Today such installs keep the JSON tree in a local directory.

##### (a) A local SQLite database with the same schema

**Pros:**
- Python's standard library; works on Windows, macOS and Linux with no install step.
- The same tables and constraints, so status and verdicts work the same way.

**Cons:**
- A second dialect of the schema to keep in step; mitigated by one module emitting both and the
  parity tests running on both backends.
- No row history, which public users do not need.

##### (b) A local Dolt database

**Pros:**
- One backend everywhere, with history.

**Cons:**
- Every public user must install a 100 MB binary that pip and uv cannot provide.

##### (c) Keep the JSON file tree for public installs

**Pros:**
- No change for public installs.

**Cons:**
- Keeps the system the owner rejected alive beside the new one, with no status model.

##### Recommendation

> **Decision:** ✅ Resolved — (a) a local SQLite database with the same schema
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-opus-5-5; ai-effort=n/a; ai-profile=interactive -->
<!-- decision-lineage: decision-id=AI-CLI-6xud/D-5; decision-topic=registry-public-fallback; governs=artifact:docs/designs/session-name-registry-dolt.md; normalized-proposition=public-installs-use-sqlite-with-shared-schema; applicability=scope:public-install; outcome-id=sqlite; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

SQLite gives public users the same model with nothing to install. The second dialect is held in
step by generating both from one module and running every registry test on both backends.

---

<a id="d-6"></a>

#### D-6: Dolt version floor — ✅ Approved: (a) 2.3.4

**Context.** Once a newer Dolt feature version writes a database, older clients cannot read it, so
every fleet host must run a compatible binary. The fleet runs 2.3.4 today for the issue tracker.
Release 2.4.1 made the git-remote history cap a setting; 2.7.0 changed merge storage.

##### (a) 2.3.4, the version the fleet runs

**Pros:**
- No forced upgrade of the binary the issue tracker depends on.
- Every behaviour this design needs was measured on it.

**Cons:**
- The data ref's git history stays capped at 64 commits; harmless, because Dolt's own history
  survives, as measured.

##### (b) 2.4.1 or later

**Pros:**
- The history cap becomes configurable.

**Cons:**
- Forces an upgrade on every host for a setting this design does not need.

##### (c) The latest 2.7.x

**Pros:**
- Newest fixes.

**Cons:**
- Its storage change would lock older binaries, including the tracker's, out of any database it
  writes merge artifacts to.

##### Recommendation

> **Decision:** ✅ Resolved — (a) 2.3.4
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-opus-5-5; ai-effort=n/a; ai-profile=interactive -->
<!-- decision-lineage: decision-id=AI-CLI-6xud/D-6; decision-topic=registry-dolt-version-floor; governs=artifact:docs/designs/session-name-registry-dolt.md; normalized-proposition=dolt-floor-equals-fleet-version; applicability=scope:fleet-registry; outcome-id=2.3.4; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

The floor matches what every fleet host already runs, so the cutover needs no binary upgrade. The
floor is a value in `meta`, so raising it later is one migration.

---

<a id="d-7"></a>

#### D-7: Cutover shape — ✅ Approved: (a) one frozen window

**Context.** Several hosts allocate names. Two stores accepting writes at once would let one name
be held in each. Old clients already refuse a policy snapshot with an unknown schema.

##### (a) One frozen window

**Pros:**
- One store accepts writes at any moment.
- Old clients fail closed by the existing policy check, with no new code in them.

**Cons:**
- Named launches pause for the minutes between the freeze and each host's install; spares cover
  offline launches.

##### (b) A dual-write period

**Pros:**
- No pause.

**Cons:**
- Two sources of truth and a reconciliation step; a backward-compatibility path the project's
  pre-1.0 rule forbids.

##### (c) Gradual, host by host

**Pros:**
- Each host switches when ready.

**Cons:**
- Hosts on different stores can each hold the same name.

##### Recommendation

> **Decision:** ✅ Resolved — (a) one frozen window
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-opus-5-5; ai-effort=n/a; ai-profile=interactive -->
<!-- decision-lineage: decision-id=AI-CLI-6xud/D-7; decision-topic=registry-cutover-shape; governs=artifact:docs/designs/session-name-registry-dolt.md; normalized-proposition=single-frozen-cutover-window; applicability=scope:fleet-registry; outcome-id=frozen-window; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

A short pause is the price of never having two stores accept writes, and the existing policy
check makes the freeze safe without touching old clients. The rollback is rehearsed before the
real window.

---

<a id="d-8"></a>

#### D-8: How other tools read the registry — ✅ Approved: (a) through the `ai` CLI's JSON

**Context.** Four tools in the fleet repository read the old tree's files directly. After the
cutover those files stop changing.

##### (a) Through `ai list-names --json`, `ai list-migrations --json` and `ai find-names --json`

**Pros:**
- One access path and one owner of the schema; a schema change cannot silently break a reader.

**Cons:**
- Each reader needs the package installed, which every fleet host already has.

##### (b) Direct Dolt reads

**Pros:**
- No dependency on the package's CLI.

**Cons:**
- Every reader must know the ref, the clone, the info-branch setting and the schema.

##### Recommendation

> **Decision:** ✅ Resolved — (a) through the `ai` CLI's JSON output
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-opus-5-5; ai-effort=n/a; ai-profile=interactive -->
<!-- decision-lineage: decision-id=AI-CLI-6xud/D-8; decision-topic=registry-external-readers; governs=artifact:docs/designs/session-name-registry-dolt.md; normalized-proposition=external-readers-use-cli-json; applicability=scope:fleet-registry; outcome-id=cli-json; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

Keeping one access path means the Dolt-specific settings live in one place.

---

<a id="d-9"></a>

#### D-9: Who is recorded as the actor — ✅ Approved: (a) separate `actor` and `recorded_by`

**Context.** Approvals today record only the agent session that ran the command. The owner's
verdicts usually arrive through an agent session relaying them.

##### (a) Separate `actor` (who decided) and `recorded_by` (which session wrote it)

**Pros:**
- Answers "who approved this" and "which session wrote it" separately.

**Cons:**
- The actor is self-declared on the command line; there is no identity check, as today.

##### (b) The agent session only

**Pros:**
- No new flag.

**Cons:**
- Cannot tell an owner's verdict from an agent's own choice.

##### Recommendation

> **Decision:** ✅ Resolved — (a) separate `actor` and `recorded_by`
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-opus-5-5; ai-effort=n/a; ai-profile=interactive -->
<!-- decision-lineage: decision-id=AI-CLI-6xud/D-9; decision-topic=registry-verdict-actor; governs=artifact:docs/designs/session-name-registry-dolt.md; normalized-proposition=actor-and-recorder-stored-separately; applicability=scope:fleet-registry; outcome-id=two-fields; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

`--actor` is required for `set-name-status` and `set-name-verdict`; `recorded_by` is filled from
the session automatically.

---

> **Feedback Round 1:** Your approval/feedback on each decision:
> 1. D-1: <approval or feedback>
> 2. D-2: <approval or feedback>
> - <enter feedback here>

## Open Questions

1. Does "git tracked" mean stored in a git repository with history, or readable in a git diff? The
   owner named Dolt as a candidate "git tracked database system", which reads as the first, and
   this design proceeds on it. The owner is asked to confirm. If the second is meant, a readable
   export of the tables committed to a branch on each write can be added without changing the
   store.
2. Which names does the seed approve, reject or scope? The owner's recorded verdicts and the
   earlier pre-approved list are in the fleet's private records; part of the list is still owed by
   another session. The seed file is written from them in T-3.2, and names without a verdict start
   `unreviewed`.

> **Feedback Round 1:** Your thoughts on the open questions:
> 1. <!-- Response to question 1 -->
> 2. <!-- Response to question 2 -->
> - <enter feedback here>

<!-- /doc:region name="decisions" -->

<!-- doc:region name="feedback_rounds" kind="append_only" -->

## Feedback Rounds

(none yet — APPEND_ONLY: prior rounds frozen byte-for-byte)

<!-- /doc:region name="feedback_rounds" -->

<!-- doc:region name="approval_log" kind="append_only" -->

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
| 2026-11-11 | D-1 to D-9 | Resolved by the orchestrating session (Claude Opus 5.5) in automated mode through the decision framework; none escalated |

<!-- /doc:region name="approval_log" -->
