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
and the Recommendation.

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
