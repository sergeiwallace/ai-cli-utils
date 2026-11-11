---
title: "Git-tracked storage for the session-name registry"
category: research
tags: [research, name-registry, dolt, sqlite, git, storage, concurrency]
status: complete
source: "claude-adhoc-2026-11-11"
template_version: "research-1.2.0"
task: AI-CLI-6xud
delegation_provenance:
  version: 2
  contributors:
    - {role: research-leg, family: claude, model: opus, persona: researcher, scope: "Dolt in depth"}
    - {role: research-leg, family: claude, model: opus, persona: researcher, scope: "non-Dolt storage landscape"}
    - {role: inventory-leg, family: claude, model: opus, persona: Explore, scope: "current registry, prior art, touch points"}
---

# Git-tracked storage for the session-name registry

## Founding Ask Coverage

`founding_ask_ref`: Tier 3. `founding_ask_ref_sha256`
`6b5df6dc0d9aef7bd45832af903ad5d8f4439221b051da5e1969a4f4d92dd9fb`, record content sha256
`c68b87eddcba35b45a9a82b768a42dacd14f4c6b8cf2826318db02829b490646`, entry `A001`, generation 1.
The record carries the owner's words verbatim along with private session and host names, so it is
kept in the operator's private configuration repository and not reproduced in this public one.
This index is non-authoritative; the raw record governs.

| Authoritative raw entry | Coverage and disposition |
|---|---|
| A001, ask 1: a general approval status per name, a separate status for approval in a specific session, and columns for reserved, migrated and in-use | Requirements R2 to R4 in [Context](#context); evidence in sections [2](#2-dolt-on-a-git-remote) and [3](#3-schema-validated-text-in-git-with-a-local-query-cache); the schema itself is the design doc's job |
| A001, ask 2: statuses for never reviewed, rejected in general, approved in general but not yet for a session, plus rejections per repository, host or session, "whatever is robust" | R2 and R3; every option is scored on whether it can enforce them ([Comparison](#comparison)) |
| A001, ask 3: "what is the name database", "is it git tracked? it should be" | [Context](#context) answers the first; R1 is git tracked; [Open Questions](#open-questions) item 1 records which sense of the phrase each option meets |
| A001, ask 4: kyle replaces glen; those names are approved to migrate | Seed data for the design, not a storage question; carried into the design doc |
| A001, ask 5: "what is the best git tracked database system to use? dolt? if so, let's use dolt" | The whole document; [Recommendation](#recommendation) |
| A001, ask 6: dislikes the git-ref JSON system; wants the best fit "for schema etc and searching" | R5 and R6; section [3](#3-schema-validated-text-in-git-with-a-local-query-cache) notes that its strongest rival is a refinement of that system, which is why it is not recommended |
| A001, ask 7: "what about sqlite? or maybe some other database system?"; a dependency is acceptable | Sections [5](#5-a-sqlite-file-committed-to-git) and [7](#7-options-considered-and-excluded); dependencies are not scored as a cost |
| A001, ask 8: coordinate with other sessions | Process, outside this document |
| A001, ask 9: the work runs on the most capable model, orchestrated | Process: this run's legs and synthesis were Opus |
| A001, ask 10: "so yeah, then let's do dolt" | [Recommendation](#recommendation) confirms it, with conditions |
| A001, amendment: full breadth and depth research on the best git-tracked storage, "including any we're not currently considering" | Sections 2 to 7 cover nine families; three were not in any earlier brief (one ref per name, Kart-style text, DoltLite) |
| A001, routing message: run it on a fully migrated session on a named host | Process, satisfied by the run that wrote this document |

**Status:** complete

**Created:** 2026-11-11

<!-- doc:region name="context" kind="immutable" -->

## Table of Contents

- [Founding Ask Coverage](#founding-ask-coverage)
- [Context](#context)
- [Temporal Scope](#temporal-scope)
- [Executive Summary](#executive-summary)
- [1. The requirement that decides the comparison](#1-the-requirement-that-decides-the-comparison)
- [2. Dolt on a git remote](#2-dolt-on-a-git-remote)
  - [2.1 What Dolt adds](#21-what-dolt-adds)
  - [2.2 Uniqueness: the same compare-and-swap, measured](#22-uniqueness-the-same-compare-and-swap-measured)
  - [2.3 Running Dolt from a CLI on four hosts](#23-running-dolt-from-a-cli-on-four-hosts)
  - [2.4 Install and Python access](#24-install-and-python-access)
  - [2.5 Schema migrations and version skew](#25-schema-migrations-and-version-skew)
  - [2.6 When to use](#26-when-to-use)
- [3. Schema-validated text in git with a local query cache](#3-schema-validated-text-in-git-with-a-local-query-cache)
- [4. One git ref per name](#4-one-git-ref-per-name)
- [5. A SQLite file committed to git](#5-a-sqlite-file-committed-to-git)
- [6. An append-only event log](#6-an-append-only-event-log)
- [7. Options considered and excluded](#7-options-considered-and-excluded)
- [8. How other tools keep shared registries in git](#8-how-other-tools-keep-shared-registries-in-git)
- [Gaps, blindspots and emergent findings](#gaps-blindspots-and-emergent-findings)
- [Comparison](#comparison)
- [Recommendation](#recommendation)
- [Open Questions](#open-questions)
- [Sources](#sources)
- [Ambiguous Items from Auto-Remediation (Post-Run Review)](#ambiguous-items-from-auto-remediation-post-run-review)
- [Appendix: Research Prompt](#appendix-research-prompt)
- [Appendix: Provenance Ledger](#appendix-provenance-ledger)
- [Run History](#run-history)

## Context

`ai c` and `migrate-session` give every coding-agent session a short human name, and the registry
behind those names must keep each name unique across every machine that runs sessions. Today the
registry is one JSON file per name on a dedicated git branch, written by fast-forward push. The
owner asked for a git-tracked database with a robust schema instead: a general approval status per
name, approvals and rejections scoped to a repository, a host or a session, reserved, in-use and
migrated state, and a history of changes, all searchable. He leans to Dolt and asked for a
full-breadth comparison of git-tracked storage options, including ones nobody had named, before
the design commits to one.

<!-- /doc:region name="context" -->

<!-- doc:region name="body" kind="replaceable" -->

The answer to "what is the name database today" is plain git: 50 rows as JSON files on one branch
of a private repository, 620 generated pool names, 6 hand-added names and 38 migration records,
written in 113 fast-forward commits with no merges (measured from the registry branch at
`91e89d956b6` on 2026-11-11). There is no SQL engine and no schema beyond a Python validator.
A public install with no fleet repository keeps the same tree as files in a per-user data
directory, guarded by a file lock. [INFERENCE]

The requirements every option is scored against, all drawn from the founding ask:

| Id | Requirement |
|---|---|
| R1 | Git tracked |
| R2 | A general status per name: never reviewed, rejected, approved |
| R3 | Approvals and rejections scoped to a repository, a host or a session, with who and when |
| R4 | Reserved, in-use and migrated state with host, session, repository, worktree and times, plus history |
| R5 | A robust schema that evolves through versioned migrations, where an older client fails safe |
| R6 | Searchable and queryable |
| R7 | One winner when two hosts race for one name; a missing or unreadable registry refuses every pick |
| R8 | Works for this public, cross-platform package; a dependency is acceptable |

## Temporal Scope

Sources are weighted 2026 first, then 2025 and 2024. Dolt's git-remote support dates from February
2026, so most Dolt evidence is from 2026. Foundational database and git material from 2014 to 2023
is used where marked. Git-native registry patterns: no significant post-2024 developments found
beyond reftable (2024).

## Executive Summary

1. Picking a specific human name is a specific-value uniqueness invariant, which needs a
   serialization point. Every git-tracked option, Dolt included, gets that point from one primitive:
   the git server's compare-and-swap on a ref during push. Dolt does not make uniqueness stronger
   than the current system. Its value is everything above that primitive.
2. Dolt meets the schema, query, history and scoped-status requirements well, and the fleet already
   runs it. A spike on the fleet's Dolt version confirmed the one-winner behaviour through a git
   remote and found the hazard: two claims with identical values merge cleanly, so both hosts
   believe they won.
3. Dolt's costs are operational, not missing features. It is a roughly 100 MB native binary that pip
   and uv cannot install, its Python package is deprecated, its release cadence brings frequent
   behaviour changes, and it has documented lock, corruption and telemetry-spool hazards under
   concurrent or long-running use.
4. The strongest rival is schema-validated text files in git with a local SQLite query cache, on the
   transport the registry already uses. It costs nothing to install and stays readable in git, but
   it is a refinement of the git-ref JSON system the owner said he does not like.
5. Recommendation: confirm Dolt, under explicit conditions on access path, claim protocol, version
   floor and placement. Those conditions are listed in the [Recommendation](#recommendation).

## 1. The requirement that decides the comparison

Coordination-avoidance theory classifies uniqueness by what is unique. Granting a record "some
unique ID" can be kept without coordination; granting "this specific, unique ID" cannot. [VERIFIABLE][^1]
The owner approves particular names, so a session name is a specific value. A merge-based store
cannot keep this invariant on its own: CRDTs, git union merge and Dolt branch merge all accept
concurrent writes and reconcile them afterwards. One winner needs a serialization point.
[INFERENCE]

Git's push protocol provides one. The server validates that each reference being updated "hasn't
changed while the request was being processed (the obj-id is still the same as the old-id)", and
reports `ok` or `ng` per ref. [VERIFIABLE][^2] Locally, `git update-ref` with an empty old value
makes "sure that the ref you are creating does not exist", and `--stdin` applies several updates
all or nothing. [VERIFIABLE][^3] A push can require that a ref "must not already exist" by giving
`--force-with-lease` an empty expected value. [VERIFIABLE][^4] GitHub recommends at most "6 pushes
per minute per repository", far above a registry written a few times a day. [VERIFIABLE][^5]

The current registry already uses this primitive correctly: it builds a commit on the fetched tip,
pushes without force, and on rejection re-fetches and re-runs the whole guard. The owner's
objections are to the payload and the read path, not to the transport. [INFERENCE]

## 2. Dolt on a git remote

### 2.1 What Dolt adds

Dolt is a SQL database with git-style version control. A Dolt remote can use "a Git repository on
disk or a hosted Git server (over HTTPS or SSH) as the backing store", and stores its data "under a
Git ref (by default `refs/dolt/data`, configurable via `--ref`)". [VERIFIABLE][^6] Git-remote
support shipped in February 2026, needs the `git` binary on the path, and the ref "will not even be
cloned, fetched, or pulled by Git" in normal use. [VERIFIABLE][^7]

On the requirements, Dolt gives engine-enforced tables with primary keys and constraints (R2, R3,
R5), SQL queries (R6), and a per-row `dolt_history_<table>` that records the value at every commit
(R4). [VERIFIABLE][^8] The fleet's issue tracker, Beads, adopted Dolt in early 2026 as its primary
store, and Dolt's git-remote support was built at that project's request. [VERIFIABLE][^9] The
project is active: ten releases between 6 October and 11 November 2026, Apache-2.0, maintained by
DoltHub. [VERIFIABLE][^10]

Dolt's own vendor guidance cuts against small uses: "If you are looking for a simple solution to a
small versioning problem, Dolt is probably not the database for you." It also says Dolt "can be
embedded in Golang applications but not other languages." [VERIFIABLE][^11]

### 2.2 Uniqueness: the same compare-and-swap, measured

A normal push requires that "the current commit on the remote branch must be an ancestor of our
local commit"; otherwise Dolt rejects it. [VERIFIABLE][^12] Data conflicts are cell level: "If two
operations modify the same row, column pair to be different values, a conflict is detected."
[VERIFIABLE][^13] Two consequences follow, and a spike on the fleet's Dolt version measured both
([evidence](evidence/session-name-registry-dolt-race-spike.md)):

- Two hosts insert the same name with different claimant values. The first push wins; the second
  is rejected with `non-fast-forward` (exit 1); its pull reports `CONFLICT (content)` (exit 1).
  Aborting the merge and resetting to the remote leaves the loser holding the winner's row.
  [INFERENCE]
- Two hosts insert the same name with identical values. The second push is rejected, but its pull
  merges cleanly (exit 0) and the next push succeeds. Both hosts then believe they claimed the
  name. [INFERENCE]

Dolt's own concurrency write-up says the engine "is susceptible to the lost update problem".
[VERIFIABLE][^14] Beads met these shapes in production: concurrent guarded deletes all succeeded
because the commit-time merge treated identical deletes as one, [VERIFIABLE][^15] and a retried
`INSERT … ON DUPLICATE KEY UPDATE` overwrote a concurrent writer's row. [VERIFIABLE][^16]

So Dolt over a git remote gives exactly one winner only under a client protocol: every claim writes
a value unique to the attempt (host, session, nonce); a claim is a plain `INSERT`, never an upsert;
a rejected push is never resolved by merging, but by aborting, resetting to the remote and
replaying the whole guarded write; a win is declared only after the row reads back from the remote
tip; and nothing ever force-pushes the registry ref. The current registry's `transact` loop already
has this shape. [INFERENCE]

### 2.3 Running Dolt from a CLI on four hosts

The storage lock is an advisory lock held for an opener's lifetime; a second opener waits 100 ms
and "either errors with `ErrDatabaseLocked` or silently falls back to read-only". [VERIFIABLE][^17]
A silent read-only open conflicts with R7 unless the client checks it can write before a pick.
Concurrent writers have corrupted the journal in 2026 versions. [VERIFIABLE][^18] In the spike, two
writers on one host on version 2.3.4 both succeeded; that is one observation, not a guarantee.
[INFERENCE]

The CLI is fast enough for a launch path. Median timings on version 2.3.4: a read query 0.23 s, a
write with commit 0.19 s, `dolt version` 0.17 s, and a push to a local git remote 0.81 s
([evidence](evidence/session-name-registry-dolt-race-spike.md)). [INFERENCE] Pushes to a hosted git
remote took 44 to 80 s for a tiny database until a fix brought them to "around ~15s per push".
[VERIFIABLE][^19] No 2.7.x hosted-push timing was found. [NO SOURCE]

Dolt collects anonymous usage metrics by default, and when uploads fail "it does not delete the
files". [VERIFIABLE][^20] A Dolt-using host in this fleet had 6.7 GB of spooled telemetry
(1.7 million files) on 2026-11-10, so a client that runs `dolt` must set `metrics.disabled`.
[INFERENCE]

Open defects on the git-remote backend in 2026 include a push failing with "Blob not found" on
corrupt remote heads. [VERIFIABLE][^21]

### 2.4 Install and Python access

Dolt is "a single ~100 megabyte program", installed by script, Homebrew, MSI, winget, Chocolatey or
Scoop. [VERIFIABLE][^22] Homebrew lists bottles for Apple Silicon macOS and Linux but not Intel
macOS. [VERIFIABLE][^23] The PyPI name `dolt` belongs to an unrelated project, and no wheel bundling
the binary was found. [NO SOURCE]

The Python package doltpy is deprecated and points users to `dolt sql-server` plus "any Python MySQL
client". [VERIFIABLE][^24] PyMySQL is pure Python and MIT licensed. [VERIFIABLE][^25] A client can
therefore either shell out to `dolt sql -r json` with no Python dependency, or talk to a sql-server
through PyMySQL. Either way the binary is an install step outside pip and uv. [INFERENCE]

DoltLite, a SQLite fork with Dolt-style versioning installable with `pip install doltlite`, is a
candidate no earlier brief named. It is in beta, ships no Windows wheel, and does not work on
Python builds with a statically linked `_sqlite3` "such as python-build-standalone", [VERIFIABLE][^26]
which is what uv installs by default. [VERIFIABLE][^27] It is not viable for this package today.
[INFERENCE]

### 2.5 Schema migrations and version skew

Once a newer Dolt feature version writes a database, "older clients with a lower feature version
will no longer be able to read or write that database". [VERIFIABLE][^28] Release 2.7.0 says that
"when conflicts arise in merging schema changes, the artifacts persisted to disk will be unreadable
by older versions of Dolt". [VERIFIABLE][^29] In Beads, a migration that dropped and recreated a
table left the store unmergeable with any clone holding unpulled commits. [VERIFIABLE][^30]

A Dolt registry therefore needs two gates the engine does not provide: an application `schema`
row with a minimum reader version checked on every open, and a minimum Dolt feature version
checked before any write. Migrations should be additive and run only when the local and remote
heads match. [INFERENCE]

### 2.6 When to use

Dolt fits when SQL, enforced schema and per-row history matter more than install weight, and when
every writer runs one pinned Dolt version under a disciplined claim protocol. It fits poorly as an
every-user dependency of a public package that must also run with no fleet behind it. [INFERENCE]

## 3. Schema-validated text in git with a local query cache

The canonical data would stay one text file per name, validated against a versioned JSON Schema or
Pydantic model, with history records beside it. Each host rebuilds a SQLite database from the tree
at a known commit and queries that; the cache never authorizes a pick. SQLite's JSON functions are
"built into SQLite by default, as of SQLite version 3.38.0". [VERIFIABLE][^31]

Kart is the closest mature precedent: "Every database table row is stored in its own file", and
schema changes add "legend" entries that "are never modified or deleted", so rows written under
older schemas still read. [VERIFIABLE][^32] Cargo's registry index keeps one file per package and
calls for "Case-insensitive collision detection" and rejecting reserved names "like 'nul'".
[VERIFIABLE][^33]

This option meets R1 in the strongest sense (rows readable in `git log` and diffs), R6 through SQL
over the cache, R7 through the existing transport, and R8 with no new install. Its schema is
enforced only in clients, so an old client cannot be refused by a server. It is also, in substance,
the git-ref JSON system the owner rejected, with a schema and a query layer added. [INFERENCE]

### When to use

When readability in git and zero install outweigh an engine-enforced schema. [INFERENCE]

## 4. One git ref per name

Each name gets its own ref, created with an expected-absent compare-and-swap. Hosts racing for
different names never collide; two hosts racing for one name get one `ok` and one `ng`.
[VERIFIABLE][^2][^3][^4] Gerrit made the same move from SQL to git: NoteDb "is the storage backend
for code review metadata", [VERIFIABLE][^34] and it keeps external IDs unique by keying each note on
a hash of the ID, which "ensures that an external ID is used only once". [VERIFIABLE][^35] Git 2.45
added reftable, whose updates "scale with the size of the reference update, not the number of
overall references". [VERIFIABLE][^36]

Gerrit validates pushes on its server; a four-peer fleet with a plain remote has no server-side
check. Custom refs also need explicit fetch refspecs. At a few writes a day the contention this
removes is small. [INFERENCE]

### When to use

When many writers contend on one registry ref. That is not this registry's load. [INFERENCE]

## 5. A SQLite file committed to git

Replacing an open SQLite file is a documented corruption path: "Unlinking or renaming an open
database file results in behavior that is undefined". [VERIFIABLE][^37] A `git pull` that rewrites
the file while a session holds it open is exactly that case. Git can diff the file through a
`textconv` driver but cannot merge it, so two divergent copies have no automatic resolution.
[VERIFIABLE][^38]

### When to use

Only with one writer that owns the file. Four peer hosts rule it out. [INFERENCE]

## 6. An append-only event log

Status changes as immutable events, folded into a materialized view, give the strongest form of
R4's history. Git's `union` merge driver takes "lines from both versions" and warns "Do not use this
if you do not understand the implications", [VERIFIABLE][^38] and a union-merged log can hold two
reserve events for one name. git-bug's event model merges divergent histories by design and orders
them by Lamport clock. [VERIFIABLE][^39] An event log keeps R7 only if every event lands under the
same compare-and-swap as allocation. Dolt's per-row history tables provide most of this without a
separate log. [INFERENCE]

### When to use

As the history layer of option 3, never as an independently merged file. [INFERENCE]

## 7. Options considered and excluded

| Option | Why excluded |
|---|---|
| TerminusDB | "git-like" version control, not git; a server, recommended through Docker |
| Irmin | An OCaml library with no Python route |
| Noms | Archived; Dolt is its maintained successor |
| cr-sqlite, Automerge, Yjs | CRDT merge accepts concurrent claims, the opposite of one winner (section 1) |
| LiteFS, Litestream | Replication to FUSE or object storage, not git; LiteFS is Linux-only beta |
| DVC, lakeFS, Oxen | Large-data versioning; git holds placeholders or nothing |
| Fossil | Not git; its own docs say forks are possible whenever clones work without full coordination |
| DuckDB over git files | A query layer with no allocation mechanism; nothing over stdlib SQLite at hundreds of rows |
| git notes | Adds nothing over files or refs; its automatic merge strategies lose one winner |
| DoltLite | Viable shape, blocked today on Windows and on uv's default Python builds (section 2.4) |

Each exclusion is backed by the storage-landscape leg's fetched sources, summarized in the
[Provenance Ledger](#appendix-provenance-ledger). [INFERENCE]

## 8. How other tools keep shared registries in git

Homebrew taps keep one file per formula in a git repository, though Homebrew 4.0 moved default
reads to JSON from its website so updates "no longer need to perform the slow `git fetch`".
[VERIFIABLE][^40] Cargo's index, the Nix flake registry and conda-forge's feedstocks all serialize
writes through one writer: a server, a bot or pull-request review. No registry found relies on peer
clients racing pushes for uniqueness; Gerrit comes closest, with a server-side validator.
[INFERENCE] A peer-written registry is less common, which argues for testing the race and
old-client behaviour rather than assuming them. [INFERENCE]

## Gaps, blindspots and emergent findings

- An earlier fleet design compared Dolt for this registry and chose the git-ref layout, citing a
  `dolt sql-server` on every host's launch path, no readable diff on the hosting site, and a second
  publication route to keep healthy; it called the choice a two-way door. The owner's later request
  for a database supersedes that decision; its three concerns become conditions in the
  [Recommendation](#recommendation). [INFERENCE]
- "Git tracked" has two meanings. Dolt meets "stored in a git repository with history". It does not
  meet "readable in a git diff or pull request": its rows are opaque table files under a custom ref.
  [INFERENCE]
- The registry rows carry host names, repositories and session ids. On a public repository any
  custom ref is readable by anyone who can clone it, so the registry must stay in the private
  repository it lives in today. [HEURISTIC]
- The hand-add path bypasses the fleet denylist: a denylisted name is held today because adding a
  name by hand never checks the denylist. The new schema should make denylisting a status, not a
  separate list. [INFERENCE]
- Rows today record no human actor: the writer field is a package version and approval records
  name the agent session. R3's "who set it" needs a separate actor field. [INFERENCE]
- Searches were limited. The research legs could not load the host's search tools and worked from
  direct fetches and blog and issue indexes, so a 2025-2026 git-native store unknown to the authors
  may have been missed. [NO SOURCE]

## Comparison

| # | Option | R1 git tracked | R2-R4 statuses and history | R5 schema | R6 query | R7 one winner | R8 install | Key risk |
|---:|---|---|---|---|---|---|---|---|
| 1 | Dolt on a git remote | Stored in git; rows not readable by git tools | Tables plus per-row history | Engine-enforced; needs app and engine version gates | Full SQL | Ref compare-and-swap; needs the claim protocol | ~100 MB native binary plus git | Version skew, lock fallback, identical-value merge |
| 2 | Schema-validated text plus SQLite cache | Fully readable | Files plus history records | Client-side only | SQL over cache | Ref compare-and-swap, proven here | None new | A refinement of the rejected system |
| 3 | One ref per name | Readable | Per-name commit chain | Client-side only | Derived cache | Per-name compare-and-swap | None new | Custom refspecs, no server check |
| 4 | SQLite file in git | Binary | Tables | In the file | Full SQL | Ref compare-and-swap, no merge on divergence | None new | Corruption on replace-while-open |
| 5 | Event log plus view | Readable | Strongest history | Versioned events | Fold to SQLite | Only if events are compare-and-swap pushed | None new | Union merge breaks one winner |
| 6 | DoltLite | Not on git remotes | Tables plus history | Engine-enforced | Full SQL | No git remote | No Windows or uv-Python support | Beta |

## Recommendation

Confirm Dolt as the registry's store. It is the owner's stated choice, it is the only option that
gives an engine-enforced schema, SQL search and per-row history together, and no evidence found
shows it unable to meet a requirement. Its gap is operational, and each operational risk has a
named mitigation that the design must carry:

1. Placement: a dedicated Dolt database for the registry on its own ref of the private repository
   that holds the registry today, never on the public package repository and never sharing a ref
   with the issue tracker's store, whose writers would contend with it.
2. Claim protocol: the five rules in [2.2](#22-uniqueness-the-same-compare-and-swap-measured), with
   a claimant value unique per attempt, no upserts, no merge on a lost push (abort, reset, replay),
   read-back before declaring a win, and no force push.
3. Access path: one documented path per host, serialized by the package's existing host file lock,
   so two CLI processes never open the database for writing at once, and a write probe before any
   pick so a silent read-only open refuses rather than reads as free.
4. Version floor: an application schema version and a minimum Dolt feature version, both checked
   on open, with additive migrations run only when local and remote heads match.
5. Footprint: `metrics.disabled` set for every Dolt call the package makes, and a clear refusal
   naming the install step when the `dolt` binary is missing.
6. Public installs: the package must still run where no fleet repository exists. Whether that
   fallback is a local Dolt database or a local SQLite database with the same schema is a design
   decision, informed by section 2.4.

The research does not support Dolt as a stronger uniqueness mechanism; it supports Dolt as a better
data model on the same uniqueness mechanism. [INFERENCE]

## Open Questions

1. Which sense of "git tracked" does the owner mean? Classified `design-choice`: Dolt meets the
   storage sense, and readable review can be served by `dolt diff` or a list command. The design
   records the reading it takes.
2. A current hosted-push latency on Dolt 2.7.x. Researchable, deferred to the build: the
   implementation's first live test on the fleet's remote measures it.
3. Whether the two macOS hosts are Intel or Apple Silicon, which decides whether Homebrew bottles
   cover them. Researchable from the hosts at install time; the install path handles both through
   the official release archive.
4. Whether GitHub supports `git push --atomic`. Classified not load-bearing: a single-ref design
   needs no multi-ref atomicity.

## Sources

[^1]: Bailis, P., Fekete, A., Franklin, M. J., Ghodsi, A., Hellerstein, J. M., and Stoica, I. (2014). [Coordination avoidance in database systems](https://arxiv.org/abs/1402.2237). arXiv:1402.2237. Verified accessible 2026-11-11. (Foundational.)
[^2]: Git project. (n.d.). [gitprotocol-pack](https://git-scm.com/docs/gitprotocol-pack). Git documentation. Verified accessible 2026-11-11.
[^3]: Git project. (n.d.). [git-update-ref](https://git-scm.com/docs/git-update-ref). Git documentation. Verified accessible 2026-11-11.
[^4]: Git project. (n.d.). [git-push](https://git-scm.com/docs/git-push). Git documentation. Verified accessible 2026-11-11.
[^5]: GitHub. (n.d.). [Repository limits](https://docs.github.com/en/repositories/creating-and-managing-repositories/repository-limits). GitHub Docs. Verified accessible 2026-11-11.
[^6]: DoltHub. (2026). [Remotes](https://www.dolthub.com/docs/concepts/dolt/git/remotes). Dolt documentation. Verified accessible (HTTP 200 after 301) 2026-11-11; quote re-checked by the author.
[^7]: DoltHub. (2026, February 13). [Announcing Git remote support in Dolt](https://www.dolthub.com/blog/2026-02-13-announcing-git-remote-support-in-dolt/). DoltHub Blog. Verified accessible (HTTP 200) 2026-11-10.
[^8]: DoltHub. (2026). [Dolt system tables](https://www.dolthub.com/docs/sql-reference/version-control/dolt-system-tables). Dolt documentation. Verified accessible (HTTP 200) 2026-11-10.
[^9]: DoltHub. (2026, October 23). [Happy (belated) birthday, Beads!](https://www.dolthub.com/blog/2026-10-23-beads-birthday/). DoltHub Blog. Verified accessible (HTTP 200 after 301) 2026-11-10.
[^10]: DoltHub. (2026). [Releases: dolthub/dolt](https://github.com/dolthub/dolt/releases). GitHub. Verified accessible (HTTP 200) 2026-11-10.
[^11]: DoltHub. (2025, December 30). [When not to use Dolt as your database](https://www.dolthub.com/blog/2025-12-30-why-not-dolt/). DoltHub Blog. Verified accessible (HTTP 200) 2026-11-11; quotes re-checked by the author. (Vendor self-assessment.)
[^12]: DoltHub. (2026, October 8). [How Dolt remotes work](https://www.dolthub.com/blog/2026-10-08-how-dolt-remotes-work/). DoltHub Blog. Verified accessible (HTTP 200) 2026-11-11; quote re-checked by the author.
[^13]: DoltHub. (2026). [Conflicts](https://www.dolthub.com/docs/concepts/dolt/git/conflicts). Dolt documentation. Verified accessible (HTTP 200) 2026-11-10.
[^14]: DoltHub. (2026, February 17). [Dolt's extra layers of concurrency](https://www.dolthub.com/blog/2026-02-17-dolt-concurrency/). DoltHub Blog. Verified accessible (HTTP 200) 2026-11-10.
[^15]: Beads contributors. (2026). [Guarded delete has no single winner under concurrent races (#7243)](https://github.com/steveyegge/beads/issues/7243). GitHub Issues. Verified accessible (HTTP 200) 2026-11-10.
[^16]: Beads contributors. (2026). [A retried create overwrites a concurrent writer's row (#7289)](https://github.com/steveyegge/beads/issues/7289). GitHub Issues. Verified accessible (HTTP 200) 2026-11-10.
[^17]: Dolt contributors. (2026). [Document the noms LOCK contract (#11262)](https://github.com/dolthub/dolt/issues/11262). GitHub Issues. Verified accessible (HTTP 200) 2026-11-10.
[^18]: Dolt contributors. (2026). [Checksum error during concurrent DOLT_COMMIT (#10331)](https://github.com/dolthub/dolt/issues/10331). GitHub Issues. Verified accessible (HTTP 200) 2026-11-10.
[^19]: Dolt contributors. (2026). [Git remote push performance: ~45-80s per push for tiny databases (#10537)](https://github.com/dolthub/dolt/issues/10537). GitHub Issues. Verified accessible (HTTP 200) 2026-11-10.
[^20]: Dolt contributors. (2025). [Dolt can leave behind large telemetry files and never cleans them up (#10021)](https://github.com/dolthub/dolt/issues/10021). GitHub Issues. Verified accessible (HTTP 200) 2026-11-10.
[^21]: Dolt contributors. (2026). [Push to git remote fails with Blob not found (#11323)](https://github.com/dolthub/dolt/issues/11323). GitHub Issues. Verified accessible (HTTP 200) 2026-11-10. (Open.)
[^22]: DoltHub. (2026). [Installation](https://www.dolthub.com/docs/introduction/installation). Dolt documentation. Verified accessible (HTTP 200) 2026-11-10.
[^23]: Homebrew. (2026). [dolt formula](https://formulae.brew.sh/formula/dolt). Homebrew Formulae. Verified accessible (HTTP 200) 2026-11-10.
[^24]: DoltHub. (2023). [dolthub/doltpy](https://github.com/dolthub/doltpy). GitHub. Verified accessible (HTTP 200) 2026-11-10.
[^25]: PyMySQL maintainers. (2026). [PyMySQL](https://pypi.org/project/PyMySQL/). PyPI. Verified accessible (HTTP 200) 2026-11-10.
[^26]: DoltHub. (2026). [doltlite](https://pypi.org/project/doltlite/). PyPI. Verified accessible (HTTP 200) 2026-11-10.
[^27]: Astral. (2026). [Python versions](https://docs.astral.sh/uv/concepts/python-versions/). uv documentation. Verified accessible (HTTP 200) 2026-11-10.
[^28]: DoltHub. (2026). [Versioning](https://www.dolthub.com/docs/other/versioning). Dolt documentation. Verified accessible (HTTP 200) 2026-11-10.
[^29]: DoltHub. (2026, November 4). [Release v2.7.0](https://github.com/dolthub/dolt/releases/tag/v2.7.0). GitHub. Verified accessible (HTTP 200) 2026-11-11; quote re-checked by the author.
[^30]: Beads contributors. (2026). [A migration makes a store unmergeable with any clone that has unpulled commits (#6727)](https://github.com/steveyegge/beads/issues/6727). GitHub Issues. Verified accessible (HTTP 200) 2026-11-10.
[^31]: SQLite. (n.d.). [JSON functions and operators](https://www.sqlite.org/json1.html). Verified accessible 2026-11-11.
[^32]: Koordinates. (n.d.). [Table datasets V3](https://docs.kartproject.org/en/latest/pages/development/table_v3.html). Kart documentation. Verified accessible 2026-11-11.
[^33]: The Cargo Book. (n.d.). [Index format](https://doc.rust-lang.org/cargo/reference/registry-index.html). Verified accessible 2026-11-11.
[^34]: Gerrit Code Review. (n.d.). [NoteDb](https://gerrit-review.googlesource.com/Documentation/note-db.html). Gerrit documentation. Verified accessible 2026-11-11.
[^35]: Gerrit Code Review. (n.d.). [Accounts](https://gerrit-review.googlesource.com/Documentation/config-accounts.html). Gerrit documentation. Verified accessible 2026-11-11.
[^36]: GitHub. (2024). [Highlights from Git 2.45](https://github.blog/open-source/git/highlights-from-git-2-45/). The GitHub Blog. Verified accessible 2026-11-11.
[^37]: SQLite. (n.d.). [How to corrupt an SQLite database file](https://www.sqlite.org/howtocorrupt.html). Verified accessible 2026-11-11.
[^38]: Git project. (n.d.). [gitattributes](https://git-scm.com/docs/gitattributes). Git documentation. Verified accessible 2026-11-11.
[^39]: git-bug. (n.d.). [Data model](https://github.com/git-bug/git-bug/blob/trunk/doc/design/data-model.md). GitHub. Verified accessible 2026-11-11.
[^40]: Homebrew. (2023). [Homebrew 4.0.0](https://brew.sh/2023/02/16/homebrew-4.0.0/). Verified accessible 2026-11-11. (Foundational.)

<!-- /doc:region name="body" -->

<!-- doc:region name="ambiguous_items" kind="replaceable" -->

## Ambiguous Items from Auto-Remediation (Post-Run Review)

(none yet; auto-populated by verify_and_remediate node)

<!-- /doc:region name="ambiguous_items" -->

<!-- doc:region name="appendix_research_prompt" kind="immutable" -->

## Appendix: Research Prompt

**Registry ID:** run `ai-cli-6xud-name-registry-2026-11-11` (no prompt registry on this host)
**Model:** Claude Opus 5.5 for all three legs and for this synthesis
**Date:** 2026-11-11

Method: Claude ad-hoc web (research-doc skill), orchestrated. Three read-only legs ran in parallel;
the orchestrator wrote this document from their returns, a spike it ran itself, and its own
re-fetch of four load-bearing quotes. Each brief opened with the canonical founding-ask carrier
(the record's digest plus its verbatim excerpts). Those excerpts carry private names, so they are
replaced below by a placeholder. Host-specific tool names are generalized. Everything else is the
text the legs received.

```text
## FOUNDING ASK — fidelity tier 3
[canonical root and verbatim excerpts of record docs/asks/AI-CLI-6xud.md, digest
6b5df6dc0d9aef7bd45832af903ad5d8f4439221b051da5e1969a4f4d92dd9fb; withheld from this public repository]

## STAGE FRAMING — non-authoritative
You are a LEAF research worker for an orchestrator session that will write the final research doc
itself. You return evidence-bearing findings in your final message. You do NOT write, create or edit
any file, do NOT commit, do NOT launch agents or sub-agents. Stamp every finding with the date you
fetched it.

The thing being stored: a small session-name registry for a public, cross-platform Python CLI
package (ai-cli-utils, installed with uv/pip on macOS, Linux and Windows). Roughly hundreds of rows,
written rarely, read on every session launch. Four hosts each hold a local copy and must agree.
Requirements: per-name general status (unreviewed / rejected / approved), scoped approvals and
rejections by repo, host and session id with who set them and when, reserved / in-use / migrated
state with host, session, repo, worktree and timestamps, an append-only history of status changes,
searchable and queryable, a schema that evolves through versioned migrations where an older client
fails safely rather than corrupting, and above all a name must be unique across all hosts:
allocation must be atomic and two hosts racing for one name must have exactly one winner. A missing
or unreadable registry must refuse every pick. "Git tracked" is the owner's hard requirement. The
current system stores one JSON file per name on a dedicated git branch, pushed fast-forward only;
the owner rejected it for its weak schema and query story. The fleet already runs Dolt for its
issue tracker (Beads), with a per-host shared Dolt sql-server and git remotes over SSH. The owner
leans to Dolt; this research must confirm or overturn that on evidence.

[Leg 1, Dolt in depth] Questions (a floor): Dolt's current state, release cadence, license and
maintenance; embedded versus sql-server for a CLI, subprocess cost, lock files, two processes on one
host, and the Beads project's embedded experience; Python access; install on Windows, macOS and
Linux for a pip/uv-installed package and what to do when the binary is absent; remotes, push
semantics, merge and cell-level conflicts on a primary-key row; whether Dolt can give a cross-host
atomic claim with exactly one winner; schema migrations and old clients; footprint; known failure
modes and contrarian evidence.

[Leg 2, landscape] The full breadth of git-tracked and versioned storage options other than Dolt,
including ones nobody has named yet: plain text in git validated by a schema; the current git-ref
JSON approach and git-native variants (notes, custom refs, git as a key-value store, git-bug's
model, compare-and-swap on refs); SQLite committed to git and its diff and merge tools; SQLite as a
derived cache; DuckDB; Fossil; TerminusDB; Irmin; Noms; cr-sqlite and CRDT stores; LiteFS and
Litestream; DVC and lakeFS; an append-only event log with a materialized view. For each: truly git
tracked or only versioned, schema and migrations, query, the one-winner mechanism, install
footprint, maturity, failure modes. Also how other developer tools keep small shared registries in
git, and what is published about git push as an optimistic-concurrency lock.

[Leg 3, inventory, repository-internal] The current registry code, its callers and tests; the
unmerged approval-status pull request and its design; the registry branch's layout, field set,
counts and atomicity mechanism; the fleet naming policy and its governing design; the public-install
fallback; the related issues; and the fleet's Dolt operating facts.

<grounding_instructions>
[Leg 1] You are a principal storage engineer who has shipped embedded and versioned databases inside
cross-platform developer CLIs, and who has operated Dolt and git-backed data stores in production
across several machines. [Leg 2] You are a principal engineer who has designed small replicated
metadata stores for developer tooling, including git-backed registries and package indexes, and
who has watched several of them fail under concurrent writers. You have strong opinions backed by
evidence. When you cannot find a source, you say so explicitly.

Temporal scope: Weight sources by recency — 2026 (primary) → 2025 → 2024. Pre-2024 sources are
background context only unless foundational to the topic. If post-2024 literature is genuinely
sparse for a subtopic, state "[subtopic]: no significant post-2024 developments found" rather than
backfilling with older sources. Backfilling is a failure mode, not a hedge. For foundational
database and version-control patterns, 2020–2023 sources are acceptable when marked as
foundational.

[The remainder is the canonical grounding block verbatim: the Chain-of-Verification steps, the
four-tag vocabulary with rationale before tag, the GFM footnote citation format and integrity rules,
the never-invent-a-citation constraint, the source-vetting rule, the diagram rules and the writing
rules.]
</grounding_instructions>

[The canonical "Scope note" and "Independent exploration (gaps, blindspots, emergent threads)"
blocks, verbatim.]

## Retrieval enforcement (mandatory)
For each load-bearing claim, make at least one web search or web fetch call. Never return
[VERIFIABLE] without a URL you actually fetched in this session. Do not stop after one round. If no
source is found for a sub-question, run additional searches across alternate framings before tagging
it [NO SOURCE]; that tag is reserved for genuine evidence absence, not retrieval avoidance.

## Output contract
Findings by question with rationale, tier tag and footnote; a provenance ledger (claim, fetched URL,
verbatim quote, verdict, live status); APA sources; what would change the recommendation; a
completion line.

## ALIGNMENT OBLIGATION
Return evidence, not decisions. Where a finding bears on an explicit requirement in the founding ask,
say which one. Report any evidence that conflicts with the owner's Dolt lean plainly; never soften it.
```

<!-- /doc:region name="appendix_research_prompt" -->

<!-- doc:region name="appendix_provenance" kind="replaceable" -->

## Appendix: Provenance Ledger

Quotes came back through a fetch tool that extracts them with a summarizing model. The four rows
marked "re-checked" were fetched again by the author on 2026-11-11 and matched word for word.

| Claim | Source URL | Verbatim quote | Verdict | Live? |
|---|---|---|---|---|
| Specific-value uniqueness needs coordination | https://arxiv.org/abs/1402.2237 | "grant this record this specific, unique ID" | SUPPORTED | 2xx, 2026-11-11 |
| Push is a per-ref compare-and-swap | https://git-scm.com/docs/gitprotocol-pack | "the obj-id is still the same as the old-id" | SUPPORTED | 2xx, 2026-11-11 |
| Create-if-absent ref update | https://git-scm.com/docs/git-update-ref | "to make sure that the ref you are creating does not exist" | SUPPORTED | 2xx, 2026-11-11 |
| Push lease with empty expect | https://git-scm.com/docs/git-push | "the named ref must not already exist" | SUPPORTED | 2xx, 2026-11-11 |
| Push rate guidance | https://docs.github.com/en/repositories/creating-and-managing-repositories/repository-limits | "The recommended maximum limit is 6 pushes per minute per repository." | SUPPORTED | 2xx, 2026-11-11 |
| Git repository backs a Dolt remote; ref | https://www.dolthub.com/docs/concepts/dolt/git/remotes | "Dolt stores its remote data under a Git ref (by default `refs/dolt/data`, configurable via `--ref` when adding / cloning)." | SUPPORTED, re-checked | 200, 2026-11-11 |
| Git remotes since Feb 2026; ref not fetched by git | https://www.dolthub.com/blog/2026-02-13-announcing-git-remote-support-in-dolt/ | "will not even be cloned, fetched, or pulled by Git" | SUPPORTED | 200, 2026-11-10 |
| Per-row history table | https://www.dolthub.com/docs/sql-reference/version-control/dolt-system-tables | "can be queried to find a row's value at every commit in the current branch's history" | SUPPORTED | 200, 2026-11-10 |
| Beads adopted Dolt | https://www.dolthub.com/blog/2026-10-23-beads-birthday/ | "Beads adopted Dolt in early 2026 as its primary store." | SUPPORTED | 200, 2026-11-10 |
| Release cadence and license | https://github.com/dolthub/dolt/releases | "This release is a minor version bump due to storage changes made to support merge conflict resolution." | SUPPORTED | 200, 2026-11-10 |
| Vendor: small problems are not Dolt's fit | https://www.dolthub.com/blog/2025-12-30-why-not-dolt/ | "If you are looking for a simple solution to a small versioning problem, Dolt is probably not the database for you." | SUPPORTED, re-checked | 200, 2026-11-11 |
| Push needs remote commit as ancestor | https://www.dolthub.com/blog/2026-10-08-how-dolt-remotes-work/ | "For a normal push, the current commit on the remote branch must be an ancestor of our local commit" | SUPPORTED, re-checked | 200, 2026-11-11 |
| Cell-level conflict rule | https://www.dolthub.com/docs/concepts/dolt/git/conflicts | "If two operations modify the same row, column pair to be different values, a conflict is detected." | SUPPORTED | 200, 2026-11-10 |
| Lost update | https://www.dolthub.com/blog/2026-02-17-dolt-concurrency/ | "Dolt is susceptible to the lost update problem" | SUPPORTED | 200, 2026-11-10 |
| Identical concurrent deletes all succeed | https://github.com/steveyegge/beads/issues/7243 | "Dolt's commit-time merge treats two concurrent transactions that each delete the same row as identical" | SUPPORTED | 200, 2026-11-10 |
| Upsert retry overwrites a concurrent row | https://github.com/steveyegge/beads/issues/7289 | "`INSERT … ON DUPLICATE KEY UPDATE`" | SUPPORTED | 200, 2026-11-10 |
| 100 ms lock, silent read-only fallback | https://github.com/dolthub/dolt/issues/11262 | "either errors with `ErrDatabaseLocked` or silently falls back to read-only" | SUPPORTED | 200, 2026-11-10 |
| Concurrent-writer journal corruption | https://github.com/dolthub/dolt/issues/10331 | "writeCommitParentClosure: ReadManyValues: checksum error" | SUPPORTED | 200, 2026-11-10 |
| Hosted git-remote push latency | https://github.com/dolthub/dolt/issues/10537 | "1.82.4 does show substantial speed up, down to around ~15s per push." | SUPPORTED | 200, 2026-11-10 |
| Telemetry spool never cleaned | https://github.com/dolthub/dolt/issues/10021 | "If Dolt is running in an operational context where these uploads fail, it does not delete the files." | SUPPORTED | 200, 2026-11-10 |
| Corrupt remote heads on git remote | https://github.com/dolthub/dolt/issues/11323 | "Blob not found: <hash>.darc" | SUPPORTED | 200, 2026-11-10 |
| ~100 MB single program | https://www.dolthub.com/docs/introduction/installation | "Dolt is a single ~100 megabyte program." | SUPPORTED | 200, 2026-11-10 |
| No Intel macOS bottle | https://formulae.brew.sh/formula/dolt | bottles listed for Apple Silicon macOS and Linux only | SUPPORTED | 200, 2026-11-10 |
| doltpy deprecated | https://github.com/dolthub/doltpy | "To use Dolt with Python, start a Dolt SQL server using `dolt sql-server` and connect with any Python MySQL client." | SUPPORTED | 200, 2026-11-10 |
| PyMySQL MIT | https://pypi.org/project/PyMySQL/ | "PyMySQL is released under the MIT License." | SUPPORTED | 200, 2026-11-10 |
| DoltLite wheels and Python-build limit | https://pypi.org/project/doltlite/ | statically linked `_sqlite3` builds "such as python-build-standalone" do not work | SUPPORTED | 200, 2026-11-10 |
| uv installs python-build-standalone | https://docs.astral.sh/uv/concepts/python-versions/ | "uv instead uses pre-built distributions from the Astral `python-build-standalone` project" | SUPPORTED | 200, 2026-11-10 |
| Feature version locks out older clients | https://www.dolthub.com/docs/other/versioning | "older clients with a lower feature version will no longer be able to read or write that database" | SUPPORTED | 200, 2026-11-10 |
| 2.7.0 artifacts unreadable by older Dolt | https://github.com/dolthub/dolt/releases/tag/v2.7.0 | "When conflicts arise in merging schema changes, the artifacts persisted to disk will be unreadable by older versions of Dolt." | SUPPORTED, re-checked | 200, 2026-11-11 |
| Migration made a store unmergeable | https://github.com/steveyegge/beads/issues/6727 | "cannot merge a table deletion with data modification" | SUPPORTED | 200, 2026-11-10 |
| SQLite JSON built in | https://www.sqlite.org/json1.html | "built into SQLite by default, as of SQLite version 3.38.0 (2022-02-22)" | SUPPORTED | 2xx, 2026-11-11 |
| Kart one file per row, legends | https://docs.kartproject.org/en/latest/pages/development/table_v3.html | "Every database table row is stored in its own file." | SUPPORTED | 2xx, 2026-11-11 |
| Cargo index name rules | https://doc.rust-lang.org/cargo/reference/registry-index.html | "Case-insensitive collision detection" | SUPPORTED | 2xx, 2026-11-11 |
| Gerrit NoteDb | https://gerrit-review.googlesource.com/Documentation/note-db.html | "NoteDb is the storage backend for code review metadata." | SUPPORTED | 2xx, 2026-11-11 |
| Gerrit external-ID uniqueness | https://gerrit-review.googlesource.com/Documentation/config-accounts.html | "This ensures that an external ID is used only once" | SUPPORTED | 2xx, 2026-11-11 |
| Reftable scaling | https://github.blog/open-source/git/highlights-from-git-2-45/ | "Atomic reference updates that scale with the size of the reference update" | SUPPORTED | 2xx, 2026-11-11 |
| SQLite replace-while-open corruption | https://www.sqlite.org/howtocorrupt.html | "unlinking or renaming an open database file results in behavior that is undefined" | SUPPORTED | 2xx, 2026-11-11 |
| Union merge warning | https://git-scm.com/docs/gitattributes | "Do not use this if you do not understand the implications." | SUPPORTED | 2xx, 2026-11-11 |
| git-bug event model | https://github.com/git-bug/git-bug/blob/trunk/doc/design/data-model.md | "doesn't carry much meaning, but it's unbiased and hard to abuse" | SUPPORTED | 2xx, 2026-11-11 |
| Homebrew 4 left git for reads | https://brew.sh/2023/02/16/homebrew-4.0.0/ | "rather than local homebrew/core" | SUPPORTED | 2xx, 2026-11-11 |

<!-- /doc:region name="appendix_provenance" -->

<!-- doc:region name="run_history" kind="append_only" -->

## Run History

- 2026-11-11, round 1: three read-only Opus legs (Dolt in depth, the non-Dolt landscape, a
  repository inventory), a local race-and-latency spike on Dolt 2.3.4, and a re-check of four
  load-bearing quotes. Synthesis by the orchestrating session.

<!-- /doc:region name="run_history" -->
