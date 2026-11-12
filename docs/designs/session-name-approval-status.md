---
title: "Session-Name Approval Status: Design Document"
category: design
tags: [design, session-management, session-names]
status: implemented
template_version: "design-1.0.0"
---

<!-- doc:region name="overview" kind="replaceable" -->

# Session-Name Approval Status: Design Document

**Status:** IMPLEMENTED

**Created:** 2026-11-10

Platform context: [architecture.md](architecture.md). User-facing reference: the
README's "Human session names" section.

## Table of Contents

- [Executive Summary](#executive-summary)
- [Problem Statement](#problem-statement)
- [Where names are stored](#where-names-are-stored)
- [Design](#design)
  - [The status record](#the-status-record)
  - [Status values](#status-values)
  - [Allocation](#allocation)
  - [Reading and changing a status](#reading-and-changing-a-status)
  - [The local registry](#the-local-registry)
- [Design Decisions](#design-decisions)
- [Risks and Mitigations](#risks-and-mitigations)

## Executive Summary

Every name in the session-name database now has an approval status. The picker
hands out only `approved` names; every other name is `needs-review` until a person
approves it, or `excluded` once a person rules it out. The status lives in one file on
the registry ref, `available/status.json`, beside the name list it describes. A
missing or unreadable record makes every allocation refuse with the read error, so a
fault can never widen the pool to unreviewed names.

## Problem Statement

The generated name list (first names of 3 to 4 letters with common English words
removed) contains names the fleet owner would not choose for a session. Picking at
random from it produced names he then asked to change. He approved a list of names
and asked that new sessions choose only from it, with every other name in the pool
marked for human review.

## Where names are stored

Neither Dolt nor SQLite nor JSONL. The name system is plain files in git:

| What | Where | Source |
|---|---|---|
| Naming policy (ref, remote, limits, denylist, confusable rule) | `config/session-names.toml`, tracked TOML in the environment repository that ships it | `src/ai_cli/name_registry.py` `discover()`, `POLICY_MARKER` |
| Available names | `available/names.txt` on the registry ref | `NAMES_TXT` |
| How the list was built, with the policy snapshot every allocation applies | `available/generation.json` on the ref | `GENERATION_JSON`, `Snapshot.policy` |
| Names added by hand | `available/manual/<name>.json` on the ref | `MANUAL_DIR` |
| Approval status | `available/status.json` on the ref | `STATUS_JSON` (this design) |
| Held names | `names/<token>.json` on the ref, one per held name | `NAMES_DIR` |
| Renames | `migrations/<old-title>.json` on the ref | `MIGRATIONS_DIR` |

The ref is a branch of the environment repository (`registry_ref` in the TOML). Every
write is one commit built with git plumbing on a freshly fetched tip and pushed
without force; a lost race re-runs the whole guard on the new tip
(`Registry.transact`). An install with no environment repository keeps the same tree
as files under `$XDG_DATA_HOME/ai-cli-utils/session-names` behind a file lock
(`LocalRegistry`).

## Design

### The status record

```json
{
  "schema": 1,
  "names": {
    "gus": {"status": "approved", "set_by": "chief@box-one", "set_at": "2026-11-10T22:00:00Z"},
    "lee": {"status": "excluded", "set_by": "chief@box-one", "set_at": "2026-11-10T22:00:00Z"}
  }
}
```

A name absent from `names` is `needs-review`. The record is read with the rest of the
tree on every snapshot (`parse_tree`); a record that is not valid JSON, has another
schema, or holds an unknown status value is kept as a problem string, not as data.

### Status values

| Value | Allocated | Listed | Meaning |
|---|---|---|---|
| `approved` | yes | yes | A person approved it for new sessions. |
| `needs-review` | no | yes | The default for every pool name nobody has ruled on. |
| `excluded` | no, not even by `-N` | yes | A person ruled it out but wants it kept as a suggestion (for example a name one edit from a name in use). |

### Allocation

`Snapshot.approved()` returns the approved names, or raises `NameStatusError` when the
record is missing or unreadable. Every path that hands out a name goes through it:

- `pick_request` (a launch's pick, `ai migrate-session --to-name` without a name, and
  `ai list-name-candidates`) passes the approved set to the picker, which removes every
  other name before any other filter is counted. With no approved name free, the
  refusal says how many names are approved and what removed each of them, and names
  `ai set-name-status approved NAME...` as the way to widen the pool.
- Spare top-up (`_spare_writes`) picks only approved names. `ai reconcile-names` skips
  the top-up, but still releases, when the record is unusable.
- An offline launch (`take_spare`) activates only an approved spare.
- A name chosen by hand (`-N`) needs no approval, because a person chose it, but an
  `excluded` name is refused.

The confusable rule is unchanged: a name one edit from any held name is never picked,
which covers every name held on the same host.

### Reading and changing a status

- `ai list-names` adds a status column to every held row (`status` in `-j`).
- `ai list-names -p/--pool` lists every name in the database with its status and
  holder; it exits 3 when the record is missing or unreadable.
- `ai set-name-status STATUS NAME...` sets one status on one or more names in a single
  commit, recording who set it and when. It refuses the whole call when any name is not
  in the database (add it with `ai add-name` first) and refuses to overwrite an
  unreadable record. With no record it creates one.

The fleet owner decides each status. An agent runs `ai set-name-status` only on his
explicit word; the command records `set_by` so every change is attributable.

### The local registry

A local registry is seeded with the status record this package ships,
`src/ai_cli/data/session-names/status.json`: the owner's approved names that are in the
shipped list. An existing local registry without a record gets it on its next read, so
an install with no shared registry keeps working after an upgrade.

## Design Decisions

| Decision | Choice | Why |
|---|---|---|
| Where the status lives | `available/status.json` on the registry ref | The ref is already the name database, and the package writes it with the fetch-guard-push machinery. The TOML is in another repository's working tree, which this package never writes (`name_registry.py` module docstring), so a status command could not edit it safely. |
| One file or one file per name | One file | A missing file is then detectable, so AC-5's refusal is possible; per-name files would make "no data" and "nothing approved" the same tree. `transact` already serialises concurrent writers. |
| Hand-chosen names | Allowed unless `excluded` | A person choosing a name is a review; the exclusion is the owner's explicit "never use". |
| Local registry seed | The shipped approved list | Refusing every launch on a fresh install would break the package for anyone without a shared registry. |

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| A shared registry with no record refuses every new human-named session once this ships. | The operator runs the seed (`ai set-name-status approved ...`) on the registry right after the release; the refusal names the command. |
| An approved name not in the pool cannot be approved. | `ai add-name NAME` first; a common English word also needs `keep_names` in the policy TOML. |
| An old spare row holds an unapproved name. | Offline launches skip it; `ai reconcile-names` replaces spares only with approved names. |
