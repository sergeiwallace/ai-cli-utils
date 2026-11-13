---
title: "Session-name registry on Dolt"
category: design
tags: [design, name-registry, dolt, sqlite]
status: draft
source: "design-review"
template_version: "design-1.0.0"
delegation_provenance:
  version: 2
  contributors: []
---

# Session-name registry on Dolt

## Founding Ask Coverage

The authoritative request contains private names and infrastructure identifiers, so this public
design records only generic requirements. The private cutover runbook binds the exact seed data as
described in T-3.2 and T-4.2. This index is non-authoritative; the raw request governs.

| Authoritative raw entry | Coverage and disposition |
|---|---|
| General approval, separate session approval, and reserved, migrated and in-use tracking | [Data Model](#data-model): `names.status`, session-scoped `verdicts`, `holds` and `migrations`; [D-3](#d-3) |
| Never reviewed, generally rejected, generally approved but not approved for a session, and approvals or rejections per repository, host or session | The four states are derived in [Status model](#status-model). Scoped decisions are normalized `verdicts` rows, as resolved in [D-3](#d-3) |
| The database must be git tracked | The canonical Dolt database and its row history live on a git remote ref ([D-2](#d-2)); this is a requirement, not an open question |
| Replace one previously proposed name, and approve the complete corrected seed for migration | T-3.2 applies the substitution to the migration conversion table the owner was shown (the replacement in, the superseded entry out with no approval) and gives every name in the corrected table a session-scoped approval for the session the table proposed it for, so that session's migration can take it while the picker never offers it on that approval ([D-10](#d-10)); a separately pinned, owner-approved private oracle fixes the exact membership and each entry's session, and T-4.2 checks the candidate seed against it before the flip |
| Use the best git-tracked database, with Dolt selected after research | Answered by the research recommendation; this design builds on Dolt |
| Replace the JSON-ref system with a robust schema and searchable data | The old store is retired in [Phase 4](#phase-4-fleet-cutover), constraints are in [Data Model](#data-model), and T-3.4 supplies SQL-backed search |
| A package dependency is acceptable; compare SQLite and other candidates | The research compared the candidates. Dolt is the fleet dependency ([D-6](#d-6)); SQLite is the no-fleet fallback with the same logical schema ([D-5](#d-5)) |
| Notify affected sessions and incorporate prior status feedback | T-4.3 requires notices before and after cutover. T-3.2 has the run's oracle-recovery task, owned by the run's orchestrating session, recover the earlier feedback into a separately pinned private oracle, owner-approved before the Phase 4 seed write, and requires the candidate seed to match it exactly ([D-10](#d-10)) |
| Authoring and routing constraints | Handled by the orchestration outside this public product design; they do not change runtime behaviour |
| Provide approved and needs-review statuses and seed the owner's pre-approved names plus one more name | `unreviewed` is needs-review, `approved` is approved. T-3.2 binds the pre-approved list and the one additional name as separate parts of the private oracle, alongside the corrected conversion table; the pre-approved list and the addition are general approvals and the table's names are session-scoped approvals for their own sessions' migrations, with no fixed count in this design, and T-4.2 checks every part and its exact membership ([D-10](#d-10)) |
| Research candidates beyond the initially suggested stores | Satisfied by the converged research document |

**Status:** DRAFT

**Created:** 2026-11-11

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
  - [D-10: Which names the seed approves, at what scope, and when the oracle is recovered](#d-10)
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

`Registry` remains the package-facing boundary. Fleet configuration selects `DoltRegistry`, while
standalone installs select `SqliteRegistry`; both consume DDL rendered from one logical schema and
return the same row-backed `Snapshot`. Dolt writes use guarded conditional DML, compare-and-swap
pushes and operation-specific remote read-back. The cutover holds both backends closed until old
offline spares are inventoried and quarantined, then enables Dolt in one gated commit.

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
`kind` and `state`, together with any `migrations` row in state `intent` that names it, give the
owner's reserved, in-use and migrated vocabulary. Two token languages apply, by kind, exactly as
today's validator has them: a `session` or `spare` token matches `[a-z]+`, and a `label` token (the
last token of a dropped label) matches `[a-z0-9]*[a-z][a-z0-9]*`, so it may carry digits but is
never digits only. Every row below that writes a token checks the language for that row's kind.

<!-- ac-format: state-machine id="AC-SM-HOLD" initial="absent" coverage="all-transitions-plus-invalid" verifier="uv run pytest tests/test_name_dolt.py tests/test_name_release.py tests/test_migrate_to_name.py tests/test_migrate_to_name_resume.py -q" -->

**AC-SM-HOLD — Lifecycle of a held token**

States: `absent`; `spare-reserved` (`kind = 'spare'`, `state = 'reserved'`); `session-active`
(`kind = 'session'`, `state = 'active'`, no `intent` migration names the token as its new name);
`session-intent` (`kind = 'session'`, `state = 'active'`, `recovery` null, and a `migrations` row in
state `intent` names the token as its new name); `session-migrating` (`kind = 'session'`,
`state = 'migration-incomplete'`, `recovery` set, and that `intent` row still exists);
`label-active` (`kind = 'label'`, `state = 'active'`); and `cutover-quarantined`
(`kind = 'spare'`, `state = 'cutover-quarantined'`)
Initial state: `absent`
Event alphabet: `top-up`, `claim`, `label-claim`, `migration-intent`, `activate-spare`,
`migrate-to-own-spare`, `migration-complete`, `completion-publish-failure`,
`migration-fail-clean`, `migration-fail-partial`, `recover`, `reassign`, `release`,
`spare-release`, `quarantine-spare`, `reconcile-quarantine`, `foreign-edit`
Terminal states: none. A released token returns to `absent` and can be held again.

Event meanings: `migration-complete` is the completion commit a successful rename publishes;
`completion-publish-failure` is that commit failing to publish after the rename finished on disk;
`migration-fail-clean` is a rename that raised with nothing left under the new title;
`migration-fail-partial` is a rename that raised leaving artifacts under the new title, or that
reported failure; `recover` is a reconcile pass, or a rerun of the same `migrate-session` command,
settling an `intent` row from what is on disk. A `recover` observation is one of `N` (artifacts
under the new title only), `O` (under the old title only), `B` (under both) or `Z` (under
neither). A rerun of `migrate-session` can resume a rename; a reconcile pass cannot.

| Transition | Source | Event | Guard | Target | Observable outcome |
|------------|--------|-------|-------|--------|--------------------|
| T1 | absent | top-up | the machine holds fewer than `offline_spares` unactivated spares | spare-reserved | a `holds` row of kind `spare` for that machine |
| T2 | absent | claim | the token matches `[a-z]+` and the claim protocol wins | session-active | a `holds` row carrying this attempt's claimant |
| T3 | absent | label-claim | the token matches `[a-z0-9]*[a-z][a-z0-9]*` and the claim protocol wins | label-active | a label hold carrying its label metadata and this attempt's claimant; a token such as `abc2` is accepted |
| T4 | absent | claim or label-claim | the token does not match its kind's language: `[a-z]+` for a claim (so `abc2` is refused), `[a-z0-9]*[a-z][a-z0-9]*` for a label claim (so `42`, `Abc` and `abc-2` are refused) | unchanged | refused with exit 2; nothing written |
| T5 | absent | migration-intent | the claim protocol wins | session-intent | the hold (`active`, `recovery` null) and a `migrations` row in state `intent`, in one commit, before any filesystem change |
| T6 | any state but absent | top-up, claim, label-claim or migration-intent | true | unchanged | the guarded insert affects zero rows and nothing is committed; a picked name is picked again, a hand-chosen one is refused as held with the fresh tip's holder |
| T7 | spare-reserved | activate-spare | the activating host is the spare's `machine` | session-active | the row's activation fields set; nothing else changes |
| T8 | spare-reserved | migrate-to-own-spare | the migrating host is the spare's `machine` | session-intent | the spare activated and its `intent` row written in one commit; the dry run reports the same plan |
| T9 | spare-reserved | activate-spare or migrate-to-own-spare | the host is not the spare's `machine` | unchanged | refused as held |
| T10 | spare-reserved | spare-release, or release of a spare | the spare-release test passes under the spool lock | absent | the `holds` row is deleted |
| T11 | spare-reserved | spare-release, or release of a spare | the spare-release test fails, or the token is in the spool | unchanged | kept held, with the reason |
| T12 | session-intent or session-migrating | migration-complete | the completing migration names this token as its new name, its `intent` row matches the old title, new name and machine, and the rename finished on disk | session-active | one commit: the `migrations` row becomes `complete`, the hold becomes `active` with `recovery` null, and T14 or T15 applies to the old hold |
| T13 | session-active | migration-complete | the completing migration names this token as its new name and its `migrations` row is already `complete` | unchanged | no commit; the call reports success (idempotent completion) |
| T14 | session-active | migration-complete | the completing migration names this row's title as its old title, and the row is unchanged, on the same machine, with that exact title | absent | deleted in T12's commit; history remains in `dolt_history_holds` |
| T15 | session-active | migration-complete | the completing migration names this row's token as its old token, but the row changed, is on another machine, or carries another title | unchanged | kept held; T12's commit still lands |
| T16 | session-intent or session-migrating | completion-publish-failure | the rename finished on disk | unchanged | the `intent` row, the new hold and the old hold all stay; nothing is released; the command exits with a partial-migration error naming `reconcile-names` and `migrate-session` as the commands that publish the completion |
| T17 | session-intent | migration-fail-clean | the rollback is proved, no artifact remains under the new title, and the `intent` row matches this machine | absent | one commit deletes the new hold and its `intent` row; the old hold is unchanged; the error names the released name |
| T18 | session-intent or session-migrating | migration-fail-partial | the `intent` row matches this machine | session-migrating | `state = 'migration-incomplete'` with `recovery` set to the exact survivor list observed under the new title; the old hold is unchanged; nothing is released |
| T19 | session-active | migration-fail-partial | the migration's `migrations` row for this token is already `complete` | session-migrating | one commit moves the `migrations` row back to `intent` with `completed_at` null and sets `recovery` |
| T20 | session-migrating | migration-fail-clean | true | unchanged | refused with exit 2; stored survivors prove artifacts exist under the new title, so the name is never released here |
| T21 | session-intent or session-migrating | recover | a resume request's old title, new name, machine or survivor data disagrees with the stored rows | unchanged | refused with exit 2; neither rows nor recovery data change |
| T22 | session-intent or session-migrating | recover | observation `N` | session-active | as T12 |
| T23 | session-intent or session-migrating | recover | observation `O`, and the caller is a `migrate-session` rerun | session-active | the rename is resumed, then T12; if the resumed rename fails, T18 applies instead |
| T24 | session-intent or session-migrating | recover | observation `O`, and the caller is a reconcile pass | unchanged | nothing written; `check-names` lists it as a stale intent once it is older than the stale-intent threshold |
| T25 | session-intent or session-migrating | recover | observation `B` | session-migrating | `recovery` set to the artifacts under the new title |
| T26 | session-intent or session-migrating | recover | observation `Z` | unchanged | nothing written; `check-names` lists it as a stale intent once it is older than the stale-intent threshold |
| T27 | spare-reserved, session-active, session-intent, session-migrating or label-active | reassign | the target host is known, the row still equals the version read, and its `machine` is unchanged | same state | only `machine`, `host_alias` and `writer` change; the token never becomes free |
| T28 | spare-reserved, session-active, session-intent, session-migrating or label-active | reassign | the target host is unknown, or the row changed since it was read | unchanged | refused with exit 2; nothing written |
| T29 | session-active or label-active | release | both absence tests pass, the row is unchanged since the test, and no `intent` row names its title as an old or new title | absent | the `holds` row is deleted; its history remains in `dolt_history_holds` |
| T30 | session-active or label-active | release | an absence test fails, the row changed since the test, or an `intent` row names its title | unchanged | kept held, with the reason ("a recorded rename of this session has not completed" for the last case) |
| T31 | session-intent | release | true | unchanged | kept held: a recorded rename of this session has not completed |
| T32 | session-migrating | release | true | unchanged | refused with exit 2, telling the user to run the same `migrate-session` command again |
| T33 | absent | quarantine-spare | cutover import is running with Dolt writes disabled, and the token is a cache-only spare in a host cache inventory | cutover-quarantined | the spare is held but cannot be activated or released by ordinary lifecycle commands |
| T34 | spare-reserved, session-active, session-intent, session-migrating, label-active or cutover-quarantined | quarantine-spare | true | unchanged | the existing row is kept exactly as it is; nothing written |
| T35 | cutover-quarantined | reconcile-quarantine | every registered host has installed the new client and its old spool and cached spare inventory was reconciled | spare-reserved or session-active | the row becomes the reconciled old spare or activation; the runbook records the source evidence |
| T36 | cutover-quarantined | activate-spare, migrate-to-own-spare, release, spare-release or reassign | true | unchanged | refused as quarantined with exit 2 |
| T37 | any state but absent | foreign-edit | the editing host is not the row's `machine` and the event is not an authorized `reassign` | unchanged | refused with exit 2; nothing written |
| T38 | any | a state and event pair the matrix below marks T38 | true | unchanged | reject with an invalid-transition refusal (exit 2); nothing written |

Every state and event pair, with the rows that govern it (`SR` spare-reserved, `SA`
session-active, `SI` session-intent, `SM` session-migrating, `LA` label-active, `CQ`
cutover-quarantined):

| Event | absent | SR | SA | SI | SM | LA | CQ |
|---|---|---|---|---|---|---|---|
| top-up | T1 | T6 | T6 | T6 | T6 | T6 | T6 |
| claim | T2, T4 | T6 | T6 | T6 | T6 | T6 | T6 |
| label-claim | T3, T4 | T6 | T6 | T6 | T6 | T6 | T6 |
| migration-intent | T5 | T6 | T6 | T6 | T6 | T6 | T6 |
| activate-spare | T38 | T7, T9 | T38 | T38 | T38 | T38 | T36 |
| migrate-to-own-spare | T38 | T8, T9 | T38 | T38 | T38 | T38 | T36 |
| migration-complete | T38 | T38 | T13, T14, T15 | T12 | T12 | T38 | T38 |
| completion-publish-failure | T38 | T38 | T38 | T16 | T16 | T38 | T38 |
| migration-fail-clean | T38 | T38 | T38 | T17 | T20 | T38 | T38 |
| migration-fail-partial | T38 | T38 | T19 | T18 | T18 | T38 | T38 |
| recover | T38 | T38 | T38 | T21 to T26 | T21 to T26 | T38 | T38 |
| reassign | T38 | T27, T28 | T27, T28 | T27, T28 | T27, T28 | T27, T28 | T36 |
| release | T38 | T10, T11 | T29, T30 | T31 | T32 | T29, T30 | T36 |
| spare-release | T38 | T10, T11 | T38 | T38 | T38 | T38 | T36 |
| quarantine-spare | T33, T38 | T34 | T34 | T34 | T34 | T34 | T34 |
| reconcile-quarantine | T38 | T38 | T38 | T38 | T38 | T38 | T35 |
| foreign-edit | T38 | T37 | T37 | T37 | T37 | T37 | T37 |

`quarantine-spare` on `absent` is T33 when the token is in a host cache inventory and T38
otherwise. A `recover`, `migration-complete` or `reassign` against a token with no row or no
recorded rename is T38 ("no rename of ... is recorded", "no session holds ...").

Guard exclusivity: T2 and T4 split `claim`, and T3 and T4 split `label-claim`, on `absent` by the
token language for that kind. T7 and T9, and T8 and T9, split on whether the host is the spare's
machine. T10 and T11 split on the
spare-release test. On `session-active` with `migration-complete`, T13 applies to the migration's
new token, and T14 and T15 to its old token, split on whether the old row is unchanged, local and
carries the old title. On `recover`, T21 takes precedence; otherwise the observations `N`, `O`,
`B` and `Z` are mutually exclusive and exhaustive, and `O` splits into T23 and T24 by caller. T27
and T28, and T29 and T30, have complementary guards. Every other matrix cell names one row.
Invalid and refused transitions: T4, T6, T9, T11, T15, T20, T21, T24, T26, T28, T30 to T32, T34
and T36 to T38. Coverage requirement: exercise every row T1 to T38 on both backends, and at least
one T38 cell in every event row of the matrix that has one, including a digit-bearing label claim, ownership
reassignment without release, each migration failure and recovery observation, and every
forbidden quarantine exit.

<!-- /ac-format -->

"Migrated" is a `migrations` row in state `complete` linking the old title to the new name.
"In use" is `kind = 'session'` and `state = 'active'`. A migration in progress is `session-intent`
(nothing has failed) or `session-migrating` (a failure left artifacts that `recovery` lists), so
`recovery` is set exactly while the state is `migration-incomplete`, as today's validator requires.
Released rows are deleted; their history stays in `dolt_history_holds`.

**Held spares.** A machine's own unactivated spare may be taken by a hand-chosen migrate on that
machine: `migrate-session --to-name <spare>` activates it, with its migration intent in the same
commit (T8), instead of refusing it as held, and the dry run reports the same plan the real run
would execute. Another machine's spare stays refused as
held.

**Reclaiming a dead session's name** keeps today's rule: release and reconcile delete a row only
when both absence tests pass and the row still matches the version that was tested. This design
adds no time-based expiry; the existing per-session lease files are the evidence the absence test
already reads.

### Claim protocol

Every write follows one publish loop. It is today's `transact` loop with Dolt in place of the git
tree, and the research doc's five rules made concrete:

1. Take the package's host file lock for the database directory, so one process writes at a time.
2. `dolt fetch` the registry ref, then `dolt reset --hard origin/main`, so the write starts from
   the remote tip.
3. Express the complete guard in the mutation itself, never in a preceding `SELECT`: claims use
   `INSERT ... SELECT ... WHERE NOT EXISTS (...)`; updates and deletes put ownership, state and
   expected-old-value predicates in their `WHERE` clauses. Run that conditional DML, read
   `ROW_COUNT()`, and call `dolt_commit` only when the exact expected count was changed. A zero or
   unexpected count resets the working set and refuses without a commit. Never use an upsert. A
   claim row carries `claimant = <host>:<session>:<nonce>`, unique to this attempt.
4. Confirm the new commit exists in `dolt_log`. A write that did not commit (a read-only open, a
   constraint failure, a guard refusal or an unexpected affected-row count) resets the working
   set, raises and pushes nothing.
5. `dolt push origin main`. The client never passes `--force` and never pushes any other way.
6. On a rejected push: reset to the fetched remote tip and go back to step 2, never `dolt pull`
   and never a merge. After `publish_attempts` losses, raise `RegistryContendedError`, and the
   launch falls back to a spare as it does today.
7. On a successful push: fetch again and verify the operation-specific postcondition on
   `origin/main`. A claim succeeds only when the row carries this attempt's `claimant`; a row with
   another claimant means another host won, even when every other value is identical. An update
   must equal all intended values. A delete must find the guarded row
   absent. Status, verdict, migration and policy writes verify their complete intended row set;
   they do not apply the claim-only `claimant` rule.

Any other failure (timeout, authentication, a missing `dolt` binary) raises
`RegistryUnreachableError`, as today, and no pick is made from unverified data.

### Access path on a host

The package runs the `dolt` CLI as a subprocess, `dolt sql -r json -q ...` for reads and a
script on stdin for writes, against the clone directory. It never starts or connects to a
`dolt sql-server`, and it never touches the issue tracker's shared server or its databases. The
host file lock (`portalocker`, already a dependency) serializes the package's own processes, so two
CLI processes never open the database for writing together. Reads take the same lock for the short
time a read runs, because Dolt's own lock falls back silently to read-only for a second opener.

Every `dolt fetch` reads and uses `registry_fetch_timeout_seconds`; every `dolt push` reads and uses
`registry_push_timeout_seconds`. These remain independent of the shorter transaction/lock budget
derived from `publish_timeout_s`, so a normal remote operation does not consume offline capacity
merely because it outlasts that local budget. A `dolt` binary older than the version floor, or a
database whose `meta.min_reader_version` is above the package's, refuses with a message naming the
upgrade.

### Behaviour kept from the current registry

Every behaviour in this list keeps its current guard and its current tests' intent; Phase 1 ports
the tests to the new backend:

- one fleet-wide namespace for session, spare and label tokens, with today's per-kind token
  languages (`[a-z]+` for session and spare tokens, `[a-z0-9]*[a-z][a-z0-9]*` for label tokens);
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
- the migration ledger: intent in the reserve commit, completion with the old row's release, the
  clean-failure release of the new row with its intent, the partial-failure hold as
  `migration-incomplete` with its survivors, a completion that failed to publish left for the next
  reconcile or rerun, and idempotent recovery from what is on disk (T5 and T8, T12 to T26 of
  AC-SM-HOLD, and T-1.5);
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
-- rows: schema_version, min_reader_version, min_dolt_version, writes_enabled

create table policy_snapshot (
  revision varchar(64) primary key,     -- the policy commit the snapshot came from
  generated_at varchar(20) not null,
  body json not null                    -- valid JSON; today's generation.json content
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
  state enum('reserved','active','migration-incomplete','cutover-quarantined') not null,
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
  check ((kind in ('session','spare') and token regexp '^[a-z]+$')
      or (kind = 'label' and token regexp '^[a-z0-9]*[a-z][a-z0-9]*$'))
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
absolute paths, the per-kind token language) stay in the package's validator and also become
database `check` constraints.
`holds.token` has no foreign key to `names`, because a label's last token can be held without being
in the pool, as today.

`name_schema.py` defines one logical schema and renders only the syntax differences. The SQLite
connection executes `PRAGMA foreign_keys = ON` and verifies that it reads back as `1` before any
schema or data operation. Its enum types become `check (... in (...))`; its JSON columns become
`text` with `check (json_valid(body))` and
`check (recovery is null or json_valid(recovery))`; its `[a-z]+` checks (`names.name`, and session
and spare tokens) use `x glob '[a-z]*' and x not glob '*[^a-z]*'` on the column `x`, and its
label-token check uses `token glob '*[a-z]*' and token not glob '*[^a-z0-9]*'`, combined with
`kind` exactly as in the Dolt `holds` check. Dolt's native `json`, enum, foreign-key and
regular-expression constraints enforce the same accepted and rejected value sets. A backend may
not omit a logical constraint merely because its DDL spelling differs.

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
    - [ ] If a `names` row, or a `holds` row of kind `session` or `spare`, carries a token outside `[a-z]+` (for example `Abc` or `abc2`), then the system shall refuse it at the database on both backends.
    - [ ] If a `holds` row of kind `label` carries a token outside `[a-z0-9]*[a-z][a-z0-9]*` (for example `42`, `Abc`, `abc-2` or the empty string), then the system shall refuse it at the database on both backends.
    - [ ] When a `holds` row of kind `label` carries a digit-bearing token that is not digits only (for example `abc2` or `2abc`), both backends shall accept it.
    - [ ] If a `verdicts` insert names no existing `names` row, then both backends shall reject it with a foreign-key violation; the SQLite test shall also assert `PRAGMA foreign_keys = 1` on every opened connection.
    - [ ] If `policy_snapshot.body` or a non-null `holds.recovery` is malformed JSON, then both backends shall reject the row at the database.
    - [ ] For every enum, nullability, foreign-key, JSON and per-kind token constraint in the logical schema, a generated valid/invalid fixture matrix shall produce the same result on Dolt and SQLite, and the token rows of that matrix shall give the same verdict as today's row validator for each kind.
  - **T-1.2 Claim protocol** — the seven-step loop.
    - [ ] When two hosts claim the same free name at the same moment over a local bare remote, repeated 20 times, the system shall give exactly one winner in each trial, and the loser's caller shall see the winner's claimant.
    - [ ] When two hosts claim the same name with otherwise identical row values, the system shall still give exactly one winner, because each claimant value is unique.
    - [ ] If a claim's `NOT EXISTS` guard is false, or an update/delete ownership, state or expected-value predicate is false, then its conditional DML shall affect zero rows, create no Dolt commit and push nothing.
    - [ ] A test shall fail if an implementation evaluates a write guard only with `SELECT` and then issues unconditional DML; every mutating statement shall carry the complete guard and assert its affected-row count before commit.
    - [ ] If a push is rejected, then the system shall reset to the remote tip and re-run the whole guard, and shall never run `dolt pull` or a merge.
    - [ ] If every one of `publish_attempts` attempts is rejected, then the system shall raise `RegistryContendedError` and leave the remote unchanged by this caller.
    - [ ] If the `dolt sql` write does not produce a commit in `dolt_log`, then the system shall push nothing and raise.
    - [ ] When a status, verdict, migration, policy update or guarded delete pushes successfully, the system shall verify its operation-specific postcondition after fetching the remote tip and shall not require a claim-only `claimant` value.
    - [ ] The system shall start no `dolt` process without `DOLT_REMOTE_INFO_BRANCH` set to empty and `metrics.disabled` set, and a test shall observe that the bare remote gains no `__dolt_remote_info__` branch.
  - **T-1.3 Access path**
    - [ ] When two package processes on one host write at once, the system shall serialize them through the host file lock, and both writes shall land.
    - [ ] If the `dolt` binary is missing, then the system shall raise `RegistryUnreachableError` with a message naming the install step.
    - [ ] If `dolt version` reports a version below the floor in [D-6](#d-6), or `meta.min_reader_version` is above the package's, then the system shall refuse every read and write with a message naming the upgrade.
    - [ ] When fetch and push timeouts are configured to distinct values above `publish_timeout_s`, every `dolt fetch` shall receive only `registry_fetch_timeout_seconds`, every `dolt push` shall receive only `registry_push_timeout_seconds`, and a fetch or push completing between the old shared budget and its configured budget shall succeed without taking an offline spare.
    - [ ] If either registry timeout setting is invalid, then the system shall refuse while naming that setting; it shall not silently fall back to `publish_timeout_s`.
  - **T-1.4 Allocation and transaction parity**
    - [ ] When any session, spare or label token is held, every allocation path shall treat it as occupied in the one shared namespace on both backends.
    - [ ] When a drop-policy launch reserves a label whose last token carries digits (for example `abc2`), the system shall hold it as a `label` row on both backends, and every other allocation path shall refuse that token as held; today's label-reservation tests shall run unchanged against both backends.
    - [ ] If a drop-policy label's last token is digits only, then the system shall refuse the reservation with exit 2 and write nothing.
    - [ ] When reserve and spare top-up, reserve and manual add, reserve and migration intent, migration completion and old-row release, or a clean migration failure's release of the new row and deletion of its intent are requested together, each pair shall appear in one commit or neither change shall appear.
    - [ ] If a picked claim loses a publish race, then the picker shall re-read and pick again; if a hand-chosen claim loses, then it shall refuse with the fresh tip's holder; after `publish_attempts` losses either path shall raise `RegistryContendedError`.
    - [ ] If a policy snapshot has an unknown schema or key, then every allocation shall refuse with "update ai-cli-utils".
    - [ ] When `sync-names` sees a policy revision newer than the published snapshot, it shall regenerate the pool; for an equal or older revision it shall make no registry change.
    - [ ] If the registry is unreachable during automatic allocation, then the system shall activate one approved same-machine spare under `spool.lock` and durably record the activation in the spool.
    - [ ] If the registry is unreachable during a hand-chosen launch, then the launch shall refuse without taking or spooling any name.
    - [ ] When the next online command publishes a spooled activation, it shall update only the unchanged same-machine spare and remove the spool entry only after the remote postcondition is verified.
    - [ ] If either absence test fails, then release and reconcile shall keep the held row.
    - [ ] If the held row changes after both absence tests, then release and reconcile shall keep the changed row; no force-release path shall exist.
    - [ ] If a proposed row contains an absolute path, then both backends shall refuse it before commit.
    - [ ] If a host other than the row owner attempts an ordinary edit or delete, then the system shall refuse it without a commit.
    - [ ] When `reassign-name` targets a known host and the row is unchanged, it shall change ownership atomically without making the token absent at any point.
    - [ ] When `check-names` sees an over-limit, grandfathered, migration-incomplete or stale-intent row, it shall report the matching category and preserve exit codes 2 for refusal, 3 for unreadable data and 75 for an unreachable registry.
    - [ ] When the registry is unavailable, `known_names` shall return the shipped seed without a network or database read.
  - **T-1.5 Migration success, failure and recovery parity**: every path of today's `migrate_to_name`, `complete_migration`, `release_failed_migration`, `mark_migration_incomplete` and `recover_intents`, mapped to AC-SM-HOLD. Each failure-path AC below is the counterpart of a success-path AC and is held to the same contract: guarded conditional DML, one commit, and the operation-specific remote read-back of the claim protocol, on Dolt and SQLite alike.
    - [ ] When migration starts, the system shall write the new hold (`active`, `recovery` null) and its `migrations.state = 'intent'` row in one commit before any filesystem change (T5, or T8 for an own spare); rerunning the same intent shall be idempotent.
    - [ ] When the rename finishes on disk and the completion publishes, the system shall, in one commit, set the migration row to `complete`, make the new hold `active` with `recovery` null, and delete the old hold only when it is unchanged, on the same machine and carries the old title; otherwise it shall keep the old hold and still publish the completion (T12, T14, T15).
    - [ ] When a completion is published for a migration already `complete`, the system shall make no commit and report success (T13).
    - [ ] If the rename raises and no artifact remains under the new title, then the system shall, in one commit, delete the new hold and its `intent` row, leave the old hold unchanged, and exit with an error naming the released name; after a fetch, the remote shall hold neither the new row nor the intent (T17).
    - [ ] If the rename raises leaving artifacts under the new title, or reports failure, then the system shall hold the new row as `migration-incomplete` with `recovery` equal to the exact survivor list observed under the new title, keep the old hold, release no spare or session name, and exit with an error telling the user to run the same command again (T18).
    - [ ] If a failure is recorded after the migration row was already `complete`, then the system shall move that row back to `intent` with `completed_at` null and set `recovery`, in one commit (T19).
    - [ ] If the rename finishes on disk but the completion commit cannot be published, then the system shall keep the `intent` row, the new hold and the old hold unchanged, release nothing, and exit with an error naming `reconcile-names` and `migrate-session`; the next run of either shall publish the completion as T12 does (T16).
    - [ ] If a clean-failure release is requested for a `migration-incomplete` hold, then the system shall refuse with exit 2 and keep the hold and its recovery data (T20).
    - [ ] When a reconcile pass or a rerun of `migrate-session` settles an `intent` row, the system shall act on what is on disk: artifacts under the new title only shall publish the completion (T22); under the old title only, a rerun shall resume the rename and then complete (T23) while a reconcile pass shall write nothing (T24); under both shall hold the row as `migration-incomplete` with the artifacts under the new title (T25); under neither shall write nothing (T26). A second identical run shall change nothing.
    - [ ] If an `intent` row is left unsettled past the stale-intent threshold, then `check-names` shall list it as a stale intent (T24, T26).
    - [ ] If a release or reconcile targets a hold that an `intent` row names as its old or new title, or a `migration-incomplete` hold, then the system shall keep it held with the reason (T30 to T32).
    - [ ] When the same migration command resumes an incomplete migration, it shall consume the stored survivor data, complete idempotently, set the migration row to `complete`, make the new hold active and release the unchanged old row in one commit; if the resumed rename fails again, the hold shall stay `migration-incomplete` with the newly observed survivors (T18, T23).
    - [ ] If a resume request disagrees with the stored old title, new name, machine or survivor data, then it shall refuse with exit 2 and change neither rows nor recovery data (T21).
    - [ ] A backend-parametrized interrupted-migration test shall stop after each durable boundary (intent committed, each artifact move, rename finished before completion) and prove that the next identical invocation reaches the same completed rows on Dolt and SQLite.
    - [ ] Every test in `tests/test_migrate_to_name.py` and `tests/test_migrate_to_name_resume.py` shall run against both backends, and each AC above shall map to at least one of those tests, or to a new test, that fails when its path is removed.
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
    - [ ] When status is set to the legacy meanings `excluded` or `needs-review` during import, the system shall store `rejected` or `unreviewed`, respectively; the public command shall accept only the three canonical status values.
  - **T-2.3 `set-name-verdict`** — `ai set-name-verdict -s/--scope repo|host|session -v/--value VALUE -a/--actor ACTOR VERDICT NAME...`, and `-d/--delete` to remove a verdict.
    - [ ] When a verdict is set, the system shall write one `verdicts` row per name, keyed by name and scope.
    - [ ] If the scope kind is not one of `repo`, `host`, `session`, then the system shall refuse with exit 2.
  - **T-2.4 Held spares**
    - [ ] When `migrate-session --to-name` names an unactivated spare of the same machine, the system shall activate that spare and write its migration intent in the same commit (AC-SM-HOLD T8), and the dry run shall report the same plan.
    - [ ] If the spare belongs to another machine, then the system shall refuse it as held.
  - **T-2.5 Approval-status parity**
    - [ ] When `list-names` is run, it shall show each name's general status and apply the requested pool filter on Dolt and SQLite.
    - [ ] If status data is unusable during reconcile, then reconcile shall skip only spare top-up, shall still perform guarded releases, and shall report that no unverified spare was created.
    - [ ] When seed metadata says the same seed revision was already applied, a second seed invocation shall make no commit; if the same revision has different content, it shall refuse.
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
  - **T-3.2 Seed** — `ai seed-name-verdicts -f/--file PATH`, reading a TOML file kept in the fleet repository, never in this package. The seed is checked against a separate private oracle at `config/session-name-approvals.oracle.toml` in the fleet repository, which holds the owner's earlier approval feedback recovered from the sessions it was given to. The owner shall approve that oracle's exact normalized rows at a pinned fleet-repository commit, and a separate authoritative cutover metadata record shall store that commit and the oracle's canonical SHA-256. The candidate seed file is not the oracle, and neither its own digest nor an expected digest stored inside it can establish the approved membership. Recovering the oracle is the run's oracle-recovery task: the run's orchestrating session owns it, tracks it as a child issue of the run's issue, and finishes it, with the owner's approval recorded, before the T-4.2 seed write in Phase 4 ([D-10](#d-10)). Phase 3 builds and tests the seed command and its oracle validation against generic test fixtures and does not need the real oracle.

    The seed applies every approval the owner gave, each at the scope the owner gave it and each kept as its own part of the oracle:
    1. the owner's pre-approved list, as general approvals;
    2. the entry the owner approved separately, in addition to that list, as a general approval;
    3. every name the owner approved in the migration conversion table the owner was shown, each as a session-scoped approval for the session the table proposed it for, with the owner's named substitution applied to that table: the superseded entry is removed and gets no approval, and its replacement is approved for the session the superseded entry was proposed for.

    Each part 3 row records the name and its target session: the session that `migrate-session OLD --to-name NAME` renames, identified as OLD. The table approved a name for that one session's migration, not for general use, so a part 3 entry is a `verdicts` row with `scope_kind = 'session'`, `scope_value` set to its target session and `verdict = 'approved'`, and it changes no general status. Under AC-DT-ELIG that session's hand-chosen migration accepts the name whatever its general status (R2, R4 or R6), unless a scoped rejection matches (R1), and the picker never offers the name on that verdict, to that session or any other (R4 and R6 offer nothing). A part 3 name that is also in part 1 or part 2 keeps its general approval as well. The oracle also records the original conversion table with each entry's target session, the superseded entry and the replacement, every name the owner excluded, every recovered scoped verdict, and the owner's explicit answer for any approved name that the exclusions or the fleet denylist also list. This design fixes no count of approved names: the pinned oracle and its separately stored digest fix the exact membership and counts. The candidate manifest records the same parts and carries its actor and reasons. Canonical SHA-256 values are computed over sorted normalized rows.
    - [ ] When seeded, the system shall load the oracle from the separately pinned commit, verify that its recomputed canonical digest equals the digest in the authoritative cutover metadata record, and require each part of the candidate manifest to equal the oracle's part exactly. It shall then, with the file's actor and in one commit, apply one general approval for every name in the pre-approved list and for the separate addition; apply one session-scoped `approved` verdict for every entry of the corrected conversion table, with its target session as the scope value, and no general approval for it unless the name is also in the pre-approved list or is the separate addition; leave the superseded entry without an approval of either kind; set every excluded name and every denylisted name to `rejected`, except a name whose recorded owner answer approves it, which the seed approves at the scope that answer gives; and apply every oracle-scoped verdict.
    - [ ] Given a seeded fixture where a name is only in the corrected conversion table, a test shall prove that the picker never offers it, to its target session or to any other session, that `migrate-session` of its target session to that name is accepted even when the name's general status is `rejected` (R6), and that another session's hand-chosen migration to it is decided as if the verdict were absent (R3 or R5); a companion fixture shall prove that a name in both the table and the pre-approved list is offered by the picker.
    - [ ] When an approved entry is not yet in the pool, the seed shall add it as a `manual` name, with the file's actor and reason, in the same commit as its approval, so no owner approval is dropped.
    - [ ] When a denylisted name is held, the system shall keep the hold and add a session-scoped `approved` verdict for its holder.
    - [ ] If a scoped verdict or a rejection in the seed file names a name outside the pool, then the system shall refuse the whole seed.
    - [ ] If the superseded entry is not a member of the original conversion table, is still a member of the corrected table, or is a member of the pre-approved list or the separate addition, if the replacement is absent from the corrected table, if the replacement's target session differs from the superseded entry's, or if any corrected-table entry has no target session, then the cutover validation shall fail before any seed row is written and report that the substitution cannot be applied as recorded.
    - [ ] If a name in an approved part is also an excluded name or listed by the fleet denylist, and the oracle records no explicit owner answer for it, then the cutover validation shall fail before any seed row is written and name that conflict; it shall neither approve nor reject the name.
    - [ ] If the oracle, its pinned commit or its separately stored expected digest is missing or inaccessible, if the oracle's recomputed digest differs from that expected digest, or if any part of the candidate (the pre-approved list, the separate addition, the original or corrected conversion table, including any entry's target session, the exclusions, the owner answers or the scoped verdicts) differs from the oracle's part, then validation shall fail before any seed row is written.
    - [ ] Given a structurally valid candidate with a self-consistent digest, if any entry of any part differs from the pinned fixture oracle, then a regression test shall prove that validation rejects it without writing a row; a companion test shall prove that the exact oracle-derived candidate is accepted.
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
  - Fleet repository (private): `config/session-names.toml` at schema 2, the seed file, the
    owner-approved oracle and its cutover metadata record (from the run's oracle-recovery task,
    T-3.2), the readers changed per [Integration](#integration), and the cutover runbook.
  - This repository: a release carrying Phases 1 to 3.
- **Tasks + acceptance criteria:**
  - **T-4.1 Drain and freeze** — announce the freeze, quiesce allocation processes and hold both registry and spool locks on every registered host, drain the old spools, then publish a policy snapshot with `schema = 2` to the old registry branch. Keep the Dolt `meta.writes_enabled` gate at `0` throughout.
    - [ ] While all registered hosts remain quiesced and locked, the cutover shall inventory every host's last-fetched spare rows and pending spool, publish every valid pending activation to the old store, record its resulting tip, and only then publish the schema-2 snapshot as the frozen tip.
    - [ ] When the frozen snapshot is published, every connected old client shall refuse every allocation with "update ai-cli-utils", the old branch shall receive no further commit, and every Dolt mutation except import, seed and quarantine reconciliation shall refuse while `writes_enabled = 0`; each host shall install the new release before its cutover locks are released.
    - [ ] If any registered host cannot be locked, inventoried and upgraded, or if the old branch changes after the recorded frozen tip, then the cutover shall stop with Dolt writes disabled.
  - **T-4.2 Import, quarantine and seed** — on one host, import every row from the exact frozen tip unchanged. Then consider the union of all host cache spare inventories by token: when the frozen tip already has that token, its row is authoritative and remains unchanged; only a cache-only spare token is inserted as `cutover-quarantined`. Seed verdicts while Dolt writes remain disabled.
    - [ ] When import completes, every old session and label hold shall exist unchanged, every cache-only token an old offline client could activate shall be occupied by a quarantined Dolt hold, every cached token already present at the frozen tip shall remain occupied by that authoritative frozen row, and no ordinary Dolt allocation shall yet be accepted.
    - [ ] Given a stale host cache that calls a token an unactivated spare while the frozen tip holds that token as an active session, an integration test shall prove that import keeps the active row byte-for-byte, inserts no duplicate or quarantine row for the token, and continues to report the token as occupied.
    - [ ] Given a frozen reserved spare and a different cache-only spare, an integration test shall prove that import keeps the frozen spare row byte-for-byte unchanged while inserting the cache-only token as `cutover-quarantined`.
    - [ ] If the run's oracle-recovery task is not finished with the owner's approval of the oracle recorded, then the cutover shall stop before any seed write with Dolt writes disabled.
    - [ ] Before seed writes, the runbook shall load the owner-approved oracle from its pinned commit, verify its canonical digest against the independently stored expected digest, and compare every part of the candidate with the oracle's part. A mismatch in the pre-approved list, the separate addition, the original or corrected conversion table or any entry's target session, the superseded entry or the replacement, the exclusions, the owner answers, any scoped verdict, or the digest, or an approved name that the exclusions or the fleet denylist also list with no recorded owner answer, shall block the flip.
    - [ ] When validation succeeds, `check-names` in cutover mode shall exit 0 and `list-names` shall show counts equal to the frozen tree plus the attested seed and cache-only quarantines.
  - **T-4.3 Enable and roll out** — after every registered host is upgraded, reconcile each quarantine from its captured cache/spool evidence, verify no old registry process is running, switch policy to the Dolt backend, and set `meta.writes_enabled = 1` in the enabling Dolt commit. Sessions are told before the freeze and after the last host.
    - [ ] At every cutover checkpoint, an integration test shall attempt an old online write, an old cached offline-spare activation and a new Dolt write; zero stores shall accept ordinary allocation before the enabling commit, and only Dolt shall accept it afterward.
    - [ ] When `writes_enabled` becomes `1`, every registered host shall already have the new release and schema-2 policy, the old ref shall still equal its frozen tip, and every old cached spare token shall remain held by its authoritative frozen row or as a reconciled or quarantined cache-only hold in Dolt; otherwise the enabling commit shall refuse.
    - [ ] When every host has run `check-names` with exit 0 and launched one named session through Dolt, the cutover shall be recorded complete in the runbook and the post-cutover notice shall be sent.
  - **T-4.4 Rollback** — quiesce and lock every host, set `meta.writes_enabled = 0`, export every post-flip Dolt hold, recreate those holds on the unchanged frozen old store, and only then switch the policy back to the old backend and restore its supported schema snapshot.
    - [ ] At each rehearsed rollback checkpoint, an integration test shall attempt a Dolt allocation and an old-store allocation: before the policy switch neither shall succeed, and after it only the old store shall succeed with every exported hold present.
    - [ ] If any Dolt hold cannot be represented on the old store, any host cannot be quiesced and locked, or the old ref differs from its frozen tip before hold recreation, then rollback shall stop with both allocation paths disabled.
  - **T-4.5 Close the old system** — after a week without rollback, anchor and retire the old registry branch and close #494 as superseded. A quarantined token is released only after the runbook proves its source host was inventoried and upgraded; an unresolved quarantine remains held.
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
| 6 | Phase 3 | Import, seed and queries, D-10 | - [ ] | |
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
| 7 | The cutover window stalls launches | Sessions cannot get new names | Both stores reject ordinary writes until every host is upgraded; old cached spares are quarantined; the window is announced and rollback is rehearsed |
| 8 | A hand edit in SQL bypasses the client | A case twin or invalid row | Constraints in the table itself, not only in the client |
| 9 | The seed approves a different set from the one the owner approved, or the oracle is not ready when Phase 4 starts | Owner-approved names left unapproved, or a name approved without the owner's word or wider than the owner approved it | Each owner approval is seeded at the scope it was given: the pre-approved list and the separate addition as general approvals, each conversion-table name as a session-scoped approval for its own session's migration, which the picker never offers; each part is checked against the pinned, owner-approved oracle; conflicts with exclusions or the denylist block until the owner answers; the run's oracle-recovery task has a named owner and T-4.2 stops the cutover until it is done (D-10) |

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
| D-10 | Which names the seed approves, at what scope, and when the oracle is recovered | (a) the pre-approved list with the substitution inside it plus one addition, (b) every owner approval at the scope the owner gave it, pinned by the oracle, (c) approve the whole pool, (d) every owner approval as a general approval | (b) | (b) | No | (a) contradicts the record and narrows an explicit requirement; (c) and (d) broaden it | ✅ Resolved by Claude |

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
- One process per call, so a command that needs several queries pays several starts. Batch them
  into one script per call.
- Dolt's own lock falls back to read-only for a second opener. Mitigated by the host file lock and
  the commit check.

##### (b) Per-host `dolt sql-server` with PyMySQL

**Pros:**
- One connection serves many queries.
- Real transactions.
- PyMySQL is pure Python and MIT licensed.

**Cons:**
- A server per host on the launch path, the concern the earlier design raised.
- A new dependency and a lifecycle (start, port, crash) the package must own on three platforms.

##### (c) The issue tracker's shared server

**Pros:**
- Already running on fleet hosts.

**Cons:**
- Couples the registry to another tool's server, version and incidents. The tracker's own records
  show a five-hour outage from a schema migration on its shared server.
- Absent on public installs and on any host without the tracker.

##### Recommendation

> **Decision:** ✅ Resolved — (a) `dolt` CLI subprocess per call
<!-- decision-record: chosen-option=(a); ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->
<!-- decision-lineage: decision-id=session-name-registry-dolt/D-1; decision-topic=registry-dolt-access-path; governs=artifact:docs/designs/session-name-registry-dolt.md; normalized-proposition=the-package-reaches-dolt-through-the-cli-only; applicability=scope:fleet-registry; outcome-id=cli-subprocess; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

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
- The repository carries two Dolt databases. Anyone reading its refs must know which is which.
- Pushes to the repository count against one rate limit. A few a day is far below it.

##### (b) A dedicated private repository

**Pros:**
- Complete isolation from the tracker.

**Cons:**
- A new repository to create, grant on every host and keep credentials for.
- An outward-facing setup step on each host.

##### (c) A table in the tracker's database

**Pros:**
- No second database.

**Cons:**
- Ties the registry's schema and history to the tracker's migrations and its incidents.
- Rejected by the research doc's placement condition.

##### Recommendation

> **Decision:** ✅ Resolved — (a) its own ref in the fleet repository
<!-- decision-record: chosen-option=(a); ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->
<!-- decision-lineage: decision-id=session-name-registry-dolt/D-2; decision-topic=registry-dolt-placement; governs=artifact:docs/designs/session-name-registry-dolt.md; normalized-proposition=registry-on-own-dolt-ref-of-fleet-repository; applicability=scope:fleet-registry; outcome-id=own-ref; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

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
- Matches the owner's first suggestion literally.
- One row per name.

**Cons:**
- Actor and time per entry need a nested structure the engine does not check.
- Searching across names needs JSON path queries in both dialects.

##### (c) General status only, scoped verdicts later

**Pros:**
- Smallest first build.

**Cons:**
- Drops an explicit founding-ask requirement (rejection per repository, host or session), which is
  not permitted.

##### Recommendation

> **Decision:** ✅ Resolved — (a) a `status` column on `names` plus a `verdicts` table
<!-- decision-record: chosen-option=(a); ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->
<!-- decision-lineage: decision-id=session-name-registry-dolt/D-3; decision-topic=registry-status-storage; governs=artifact:docs/designs/session-name-registry-dolt.md; normalized-proposition=status-column-plus-normalized-verdicts-table; applicability=scope:fleet-registry; outcome-id=normalized; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

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
- The protocol is today's `transact` loop.
- Measured one winner in 20 of 20 concurrent races.

**Cons:**
- Correctness depends on every writer following the protocol. Mitigated because the package is
  the only writer and tests pin each rule.

##### (b) A create-if-absent ref per name, plus Dolt for everything else (hybrid H1)

**Pros:**
- One winner by construction, with no protocol to get wrong.

**Cons:**
- Two stores per claim that must agree, and a repair rule for a crash between them.
- A second push per claim on a launch path that already costs about 4.7 s per push.

##### Recommendation

> **Decision:** ✅ Resolved — (a) Dolt alone, under the claim protocol
<!-- decision-record: chosen-option=(a); ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->
<!-- decision-lineage: decision-id=session-name-registry-dolt/D-4; decision-topic=registry-claim-uniqueness; governs=artifact:docs/designs/session-name-registry-dolt.md; normalized-proposition=single-dolt-store-with-claim-protocol; applicability=scope:fleet-registry; outcome-id=protocol; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

The protocol is cheaper than a second store at a few claims a day, and the spare path takes most
launches off the network. T-1.2 pins the identical-value case so a regression fails a test.

---

<a id="d-5"></a>

#### D-5: Public installs with no fleet repository — ✅ Approved: (a) local SQLite with the same schema

**Context.** The package is public and cross-platform; a public user has no fleet repository and
may not have `dolt`. Today such installs keep the JSON tree in a local directory.

##### (a) A local SQLite database with the same schema

**Pros:**
- Python's standard library.
- Works on Windows, macOS and Linux with no install step.
- The same tables and constraints, so status and verdicts work the same way.

**Cons:**
- A second dialect of the schema to keep in step. Mitigated by one module emitting both and the
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
<!-- decision-record: chosen-option=(a); ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->
<!-- decision-lineage: decision-id=session-name-registry-dolt/D-5; decision-topic=registry-public-fallback; governs=artifact:docs/designs/session-name-registry-dolt.md; normalized-proposition=public-installs-use-sqlite-with-shared-schema; applicability=scope:public-install; outcome-id=sqlite; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

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
- The data ref's git history stays capped at 64 commits. Harmless, because Dolt's own history
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
<!-- decision-record: chosen-option=(a); ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->
<!-- decision-lineage: decision-id=session-name-registry-dolt/D-6; decision-topic=registry-dolt-version-floor; governs=artifact:docs/designs/session-name-registry-dolt.md; normalized-proposition=dolt-floor-equals-fleet-version; applicability=scope:fleet-registry; outcome-id=2.3.4; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

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
- Connected old clients fail closed by the existing policy check, and every spare visible in an old
  host cache remains held in Dolt quarantine.

**Cons:**
- Named launches pause while all hosts are quiesced, inventoried and upgraded.
- Offline activation is deliberately disabled during that window.

##### (b) A dual-write period

**Pros:**
- No pause.

**Cons:**
- Two sources of truth and a reconciliation step.
- A backward-compatibility path the project's pre-1.0 rule forbids.

##### (c) Gradual, host by host

**Pros:**
- Each host switches when ready.

**Cons:**
- Hosts on different stores can each hold the same name.

##### Recommendation

> **Decision:** ✅ Resolved — (a) one frozen window
<!-- decision-record: chosen-option=(a); ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->
<!-- decision-lineage: decision-id=session-name-registry-dolt/D-7; decision-topic=registry-cutover-shape; governs=artifact:docs/designs/session-name-registry-dolt.md; normalized-proposition=single-dolt-store-with-gated-frozen-window; applicability=scope:fleet-registry; outcome-id=frozen-window; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

A short pause is the price of never having two stores accept writes. The policy freeze stops
connected old clients, the all-host locks and inventory close the cached-offline path, quarantine
keeps every old spare token occupied, and the Dolt write gate stays closed until all hosts run the
new client. The rollback is rehearsed before the real window.

---

<a id="d-8"></a>

#### D-8: How other tools read the registry — ✅ Approved: (a) through the `ai` CLI's JSON

**Context.** Four tools in the fleet repository read the old tree's files directly. After the
cutover those files stop changing.

##### (a) Through `ai list-names --json`, `ai list-migrations --json` and `ai find-names --json`

**Pros:**
- One access path and one owner of the schema.
- A schema change cannot silently break a reader.

**Cons:**
- Each reader needs the package installed, which every fleet host already has.

##### (b) Direct Dolt reads

**Pros:**
- No dependency on the package's CLI.

**Cons:**
- Every reader must know the ref, the clone, the info-branch setting and the schema.

##### Recommendation

> **Decision:** ✅ Resolved — (a) through the `ai` CLI's JSON output
<!-- decision-record: chosen-option=(a); ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->
<!-- decision-lineage: decision-id=session-name-registry-dolt/D-8; decision-topic=registry-external-readers; governs=artifact:docs/designs/session-name-registry-dolt.md; normalized-proposition=external-readers-use-cli-json; applicability=scope:fleet-registry; outcome-id=cli-json; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

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
- The actor is self-declared on the command line. There is no identity check, as today.

##### (b) The agent session only

**Pros:**
- No new flag.

**Cons:**
- Cannot tell an owner's verdict from an agent's own choice.

##### Recommendation

> **Decision:** ✅ Resolved — (a) separate `actor` and `recorded_by`
<!-- decision-record: chosen-option=(a); ai-family=N/A; ai-model=N/A; ai-effort=N/A; ai-profile=N/A -->
<!-- decision-lineage: decision-id=session-name-registry-dolt/D-9; decision-topic=registry-verdict-actor; governs=artifact:docs/designs/session-name-registry-dolt.md; normalized-proposition=actor-and-recorder-stored-separately; applicability=scope:fleet-registry; outcome-id=two-fields; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

`--actor` is required for `set-name-status` and `set-name-verdict`; `recorded_by` is filled from
the session automatically.

---

<a id="d-10"></a>

#### D-10: Which names the seed approves, at what scope, and when the oracle is recovered — ✅ Resolved by Claude: (b) every owner approval at the scope the owner gave it, pinned by the oracle

**Context.** The owner gave approvals in more than one place: a pre-approved list, one name approved
separately in addition to it, and a migration conversion table the owner was shown, with one name in
that table replaced and every name in it approved to migrate. The first two are approvals of names in
general. The table approved each name for the migration of the one session it was proposed for. An
earlier revision of this design applied the substitution inside the pre-approved list and capped the
seed at that list plus the one addition. The record does not support that reading: the superseded
entry is not in the pre-approved list, the substitution was stated for the table, and the earlier
seed plan approved many more names, excluded a name, and found an owner-approved name on the fleet
denylist. A later revision fixed the membership but made every table name a general approval, which
the picker reads, so a name approved for one session's migration could be offered to an unrelated
new session. The first revision also required the earlier feedback to be recovered "before Phase 3
begins" and named no one to do it.

##### (a) The pre-approved list with the substitution inside it, plus one addition

**Pros:**
- A small, fixed approved set that is easy to state.

**Cons:**
- Contradicts the record: validation would refuse the real seed, because the superseded entry is not
  in the pre-approved list.
- Leaves names the owner approved in the conversion table, some already held by migrated sessions,
  without any approval; that narrows an explicit requirement.

##### (b) Every owner approval at the scope the owner gave it, each part pinned by the oracle

**Pros:**
- The pre-approved list and the separate addition become general approvals, which the picker and
  every hand-chosen name read.
- Each conversion-table name becomes a session-scoped approval for the session the table proposed
  it for, so that session's `migrate-session --to-name` accepts it (AC-DT-ELIG R2, R4 or R6) and the
  picker never offers it on that approval. This keeps the general and session-specific approvals the
  owner asked to be tracked separately.
- Each part, including each table entry's target session, is checked on its own against the
  owner-approved oracle, so a wrong or missing name or session in any part blocks the seed.
- An approved name that the exclusions or the fleet denylist also list waits for the owner's answer,
  so no conflict is settled silently.

**Cons:**
- The public design cannot state the size of the approved set. The private oracle and its digest
  carry it.
- The oracle must record a target session for every table entry. Mitigated by the T-3.2 validation
  that refuses a table entry with no target session, or a replacement whose session differs from the
  superseded entry's.
- Phase 4 depends on the oracle-recovery task finishing. Mitigated by naming its owner, tracking it
  as a child issue of the run, and having T-4.2 stop the cutover until it is done.

##### (c) Approve the whole pool

**Pros:**
- No oracle to recover.

**Cons:**
- Approves names the owner never reviewed, which broadens the owner's approvals and makes the
  `unreviewed` status meaningless at the start.

##### (d) Every owner approval, all as general approvals

**Pros:**
- One kind of approval to seed and check.

**Cons:**
- Turns a name approved for one session's migration into a name the picker may offer to any
  session, which broadens that approval and merges the general and session-specific approvals the
  owner asked to keep apart.

##### Recommendation

> **Decision:** ✅ Resolved by Claude — (b) every owner approval at the scope the owner gave it, pinned by the oracle
<!-- decision-record: chosen-option=(b); ai-family=claude; ai-model=us.anthropic.claude-opus-5-5; ai-effort=high; ai-profile=interactive -->
<!-- decision-lineage: decision-id=session-name-registry-dolt/D-10; decision-topic=registry-seed-approved-set; governs=artifact:docs/designs/session-name-registry-dolt.md; normalized-proposition=seed-applies-each-owner-approval-at-its-given-scope-pinned-by-oracle; applicability=scope:fleet-registry; outcome-id=scoped-union-oracle; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

Option (a) contradicts the record and narrows an explicit requirement; options (c) and (d) broaden
it. Option (b) approves exactly what the owner approved, at the scope the owner approved it, with the
owner's substitution applied where the owner stated it, and leaves the exact membership and each
table entry's session to the pinned oracle and its separately stored digest. The oracle's recovery is
the run's oracle-recovery task, owned by the run's orchestrating session and finished with the
owner's approval before the T-4.2 seed write in Phase 4. Phase 3 needs only generic fixtures, so the
earlier "before Phase 3 begins" precondition moves to T-4.2. Confidence: high; the eligibility table
already lets a session-approved name through that session's hand-chosen migration and keeps it out of
picks.

---

> **Feedback Round 1:** Your approval/feedback on each decision:
> 1. D-1: <approval or feedback>
> 2. D-2: <approval or feedback>
> - <enter feedback here>

## Open Questions

None. "Git tracked" is satisfied by the canonical Dolt database and row history stored on a git
remote ref. The exact private seed is not discretionary: T-3.2 requires the recovered approval
feedback to be fixed in an owner-approved oracle at a pinned commit with its digest stored in a
separate authoritative cutover record. Each of the candidate's approved parts must equal that
oracle's part: the pre-approved list and the separately approved addition, seeded as general
approvals, and the conversion table with the owner's substitution applied to it, seeded as
session-scoped approvals for each entry's own session. T-4.2 blocks cutover on any part or digest
mismatch ([D-10](#d-10)).

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
| 2026-11-11 | D-1 to D-9 | Resolved during automated design review; none required owner input |
| 2026-11-12 | D-10 | Resolved during automated design review after a scope-alignment review found the seed narrowed the owner's approvals |
| 2026-11-13 | D-10 | Revised during automated design review after an audit found the conversion-table names promoted to general approval: each is now a session-scoped approval for its own session's migration, option (d) records the rejected reading, and the label reads Resolved by Claude, since no human approved it |

<!-- /doc:region name="approval_log" -->
