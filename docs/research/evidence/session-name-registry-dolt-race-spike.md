---
title: "Evidence: Dolt cross-host claim race on a git remote (spike)"
category: research
tags: [research, evidence, dolt, name-registry, concurrency]
status: complete
source: "claude-adhoc-2026-11-11"
---

# Evidence: Dolt cross-host claim race on a git remote

Run 2026-11-11 on a Linux host with `dolt version 2.3.4`, the version the fleet runs. It supports
[Git-tracked storage for the session-name registry](../session-name-registry-storage.md) sections 2
and the Recommendation. Cases 1 and 2 ran in round 1; everything from Case 3 on ran in round 2,
after the critic pass, with the same setup in a fresh `mktemp -d` directory that was removed
afterwards.

## Setup

Everything ran in one `mktemp -d` directory, removed afterwards. `DOLT_ROOT_PATH` pointed Dolt's
global config into that directory, so the host's own Dolt config and telemetry spool were not
touched, and `metrics.disabled` was set there.

1. A bare git repository stood in for the shared remote. Dolt refuses to push to a git remote with
   no branches ("git remote has no branches ... initialize the repository with an initial
   branch/commit first"), so one empty git commit was pushed to `main` first.
2. Host A: `dolt init`, then
   `create table names(name varchar(16) primary key, claimant varchar(64) not null)`, commit, and
   `dolt remote add origin git+file://<dir>/remote.git`, then `dolt push origin main`.
3. Host B: `dolt clone git+file://<dir>/remote.git B`.

## Case 1: both hosts claim one name with different claimant values

| Step | Command | Exit | Output |
|---|---|---:|---|
| A claims | `insert into names values ('ava','A:host-a:nonce-1')`, commit | 0 | |
| B claims | `insert into names values ('ava','B:host-b:nonce-2')`, commit | 0 | |
| A pushes | `dolt push origin main` | 0 | |
| B pushes | `dolt push origin main` | 1 | `! [rejected] main -> main (non-fast-forward)` |
| B pulls | `dolt pull origin main` | 1 | `CONFLICT (content): Merge conflict in names`; `dolt_conflicts` shows 1 row |
| B recovers | `dolt merge --abort`, `dolt reset --hard origin/main` | 0, 0 | `names` now holds only A's row `('ava','A:host-a:nonce-1')` |

The remote picked one winner. The loser learned it only by failing to push and then failing to
merge. Recovery by aborting the merge and resetting to the remote leaves the loser with the
winner's row.

## Case 2: both hosts claim one name with identical values

| Step | Command | Exit |
|---|---|---:|
| A claims and pushes | `insert into names values ('kai','reserved')`, commit, push | 0 |
| B claims and pushes | same insert, commit, push | 1 (non-fast-forward) |
| B pulls | `dolt pull origin main` | 0, a clean merge commit |
| B pushes again | `dolt push origin main` | 0 |

Both hosts now hold a commit that inserted `kai`, and both believe they claimed it. Dolt's merge
treats identical cell values as no conflict. A claim row must therefore carry a value unique to
the attempt, and a client must never resolve a lost push by merging.

Cases 1 and 2 are sequential: A finished its push before B started. They show the ancestor
rejection, not a race. Case 3 is the race.

## Case 3: both hosts push at the same moment (20 trials)

Each trial: both hosts pull, each inserts the same new name with its own claimant value and
commits, then both `dolt push origin main` commands start together as two background processes.
After each trial both hosts reset to the remote.

| Result | Count |
|---|---:|
| Exactly one push exited 0 | 20 |
| Both pushes exited 0 | 0 |
| Neither push exited 0 | 0 |
| A won | 13 |
| B won | 7 |

In every trial the remote row was the winner's claimant value, and the loser's push failed with
`! [rejected] main -> main (non-fast-forward)`. The message does not say which layer refused it:
Dolt's client-side ancestor check or the `--force-with-lease` push it issues (next section). The
outcome, one winner per race, is the same either way. Both hosts won some races, so the pushes
genuinely overlapped.

## What Dolt writes to the git remote

Read from Dolt's source at the `v2.3.4` tag, and checked against the bare remote in the spike:

- The data push is `git push --porcelain --force-with-lease=<dstRef>:<expectedOID>`
  (`go/store/blobstore/internal/git/impl.go`, line 412 at `v2.3.4`).
- `maxParentedCommits = 64`: after 64 parented commits Dolt writes a parentless commit on the data
  ref "to sever the history chain" so `git gc` can prune old objects (`git_blobstore.go`, line 750
  at `v2.3.4`). Release `v2.4.1` (2026-10-02) is the first to make this configurable, with the
  `git-remote.max-history-commits` setting, where `0` means unlimited (dolthub/dolt pull request
  #11993).
- After each data write Dolt force-pushes a visible branch, `__dolt_remote_info__`, holding one
  `DOLT_REMOTE.md` file. Setting the `DOLT_REMOTE_INFO_BRANCH` environment variable to an empty
  value disables it (`git_refs.go`, `ResolveInfoBranch`).

Measured on the bare remote:

| Check | Result |
|---|---|
| Refs after the first push | `refs/dolt/data`, `refs/heads/__dolt_remote_info__`, `refs/heads/main` |
| Same push with `DOLT_REMOTE_INFO_BRANCH=` | `refs/dolt/data`, `refs/heads/main` only |
| Dolt commits written (one host's log) | 92 |
| `git rev-list --count refs/dolt/data` | 10 |
| Parentless commits on `refs/dolt/data` | 1 |
| A fresh `dolt clone` of that remote: `dolt log` entries | 93 (the 92 plus the initial commit) |
| The same clone: `dolt_history_names` rows for one name | 71, one per commit since it was inserted |

So the git history of the data ref is not the registry's history, and Dolt rewrites it, but
Dolt's own commit graph and per-row history survive intact in the data the ref points at.

## Key case and constraints

| Table definition | Insert `ava`, then `Ava` |
|---|---|
| `name varchar(16) primary key` (server default `utf8mb4_0900_bin`) | both inserted |
| `name varchar(16) collate utf8mb4_0900_ai_ci primary key` | `Ava` refused: `duplicate primary key given: [Ava]` |
| `name varchar(16) primary key, check (name regexp '^[a-z]+$')` | `Ava` refused: `Check constraint ... violated`; `ava2` refused the same way |

## Case 4: two hosts edit different keys of one JSON cell

A row held `{"a":1,"b":1}`. A set `$.a` to 2, committed and pushed. B set `$.b` to 2 and
committed, then pulled: the merge was clean (exit 0, no rows in `dolt_conflicts`), and the cell
read `{"a":2,"b":2}`. Dolt 2.3.4 merges edits to different keys of one JSON document. Edits to the
same key were not tested here; the cell-level rule predicts a conflict.

## Hosted push latency

Against the private hosted repository that holds the registry today, over SSH, with
`DOLT_REMOTE_INFO_BRANCH=` and a scratch data ref (`dolt remote add --ref refs/dolt-spike/...`).
A plain git push of a scratch ref to the same remote is the comparison, since that is what the
current registry's write does. Both scratch refs were deleted afterwards and the deletion was
checked with `git ls-remote`.

| Operation | Runs | Times |
|---|---:|---|
| First `dolt push` (schema only) | 1 | 5.43 s |
| `dolt push` of one inserted row | 5 | 4.71, 4.61, 4.82, 4.74, 5.06 s (median 4.74 s) |
| `dolt clone` of that ref | 1 | 1.43 s |
| `dolt pull` with nothing new | 1 | 1.08 s |
| `git push` of one empty commit to a scratch ref | 5 | 1.19, 1.13, 1.36, 1.29, 1.21 s (median 1.21 s) |

That remote already carries another Dolt database on the default `refs/dolt/data` ref, with its
own `__dolt_remote_info__` branch. The info branch has one fixed name per repository, so two Dolt
databases in one repository with the branch enabled would overwrite each other's copy.

## Registry counts

Measured from the registry branch tip `46d1154ce22` (2026-11-11): 52 files under `names/`, 6
hand-added names under `available/manual/`, a 620-line `available/names.txt`, 39 files under
`migrations/`, 116 commits, 0 merges (`git ls-tree`, `git rev-list --count`,
`git rev-list --merges --count`). Round 1 measured the earlier tip `91e89d956b6`: 50 names, 6
hand-added, 38 migrations, 113 commits, 0 merges.

## Latency (5 runs each, median, against a local file remote)

| Operation | Median |
|---|---:|
| `dolt sql -q "select * from names" -r json` | 0.23 s |
| `dolt version` | 0.17 s |
| one insert plus `call dolt_commit(...)` in one `dolt sql` call | 0.19 s |
| `dolt push origin main` to the local git remote | 0.81 s |

A push to a hosted remote adds network and server time on top. The upstream issue measured about
15 s per push to a hosted git remote after its fix; this spike did not measure a hosted push.

## Two writers on one host

Two `dolt sql -q "insert ..."` processes started together against one database both exited 0 and
both rows landed. That is one observation on one version. It does not contradict the upstream
reports of a 100 ms lock wait with a silent read-only fallback, or of journal corruption under
concurrent writers, which were on other versions and loads.
