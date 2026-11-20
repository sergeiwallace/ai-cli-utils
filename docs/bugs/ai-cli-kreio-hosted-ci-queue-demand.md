---
title: "Pull requests wait on hosted macOS and Windows jobs when the account's concurrency cap fills"
category: bug
tags: [bug, ci, github-actions, renovate, runners]
status: fixed
template_version: "bug-1.0.0"
---

# Pull requests wait on hosted macOS and Windows jobs when the account's concurrency cap fills

**Status:** fixed (AI-CLI-kreio, under AI-CLI-un01).

## Table of Contents

- [Summary](#summary)
- [Reproduction](#reproduction)
- [Root Cause](#root-cause)
- [Fix](#fix)
- [Verification](#verification)
- [Design Decisions](#design-decisions)
  - [D-1: How Renovate keeps its branches current](#d-1)
  - [D-2: The two update kinds that could never pass](#d-2)
  - [D-3: Docs-only and already-tested main pushes](#d-3)
  - [D-4: Windows shard count](#d-4)
  - [D-5: A local route that reports the required checks](#d-5)

## Summary

Hosted runners share an account-wide cap: 5 concurrent macOS jobs and 40 jobs in total, measured on
2026-11-18. Pull requests in this repository waited up to 40 minutes for a macOS slot. Over half of
the hosted job time went to three Renovate pull requests that failed on every run and were rebased,
and so re-run in full, after every merge to `main`.

## Reproduction

Read-only Actions API over the 400 most recent runs, 2026-11-18 15:30Z to 2026-11-20 02:20Z, with
the job-level endpoint (`runs/<id>/jobs`) on every macOS, Windows, Linux CI and wiki run:

| Source | Runs | Hosted job-minutes | Share |
|---|---|---|---|
| Renovate pull requests (3 branches) | 132 | 19,198 | 53.6% |
| Other pull requests | 132 | 9,910 | 27.7% |
| Pushes to `main` | 59 | 6,723 | 18.8% |
| CodeQL default setup | 123 | about 240 | under 1% |

By operating system: Windows 26,170 job-minutes (73%), Linux 6,357 (18%), macOS 3,311 (9%).

macOS slots during the waits: 54 of 136 macOS shard jobs queued for more than 10 minutes. At the
midpoint of each of those waits this repository's own jobs held 4.7 of the 5 macOS slots on
average: Renovate 48%, `main` pushes 34%, other pull requests 18%. In the 3 waits over 30 minutes,
Renovate held 80%.

Other repositories on the account started 20 hosted runs in the same window, all short Linux jobs.
The one busy repository ran its 700 jobs on self-hosted runners, which do not count against the cap.

## Root Cause

1. **Renovate rebased every update branch after every merge.** `rebaseWhen` defaulted to `auto`,
   which means "rebase when behind the base branch" whenever automerge is on. Each of the three open
   update pull requests was rebased 22 times in 35 hours, and each rebase started the full Linux,
   macOS and Windows set again. The ruleset does not require branches to be up to date
   (`strict_required_status_checks_policy: false`), so those rebases bought nothing.
2. **Two of the three could never pass.** The ruff update moved the pin in `pyproject.toml` and left
   the `ruff-pre-commit` hook's rev behind, so `test_check_ruff_version_sync.py` failed. The Python
   update moved `.python-version` to 3.15 alone, while the workflows, `pyproject.toml` and the
   version-sync check keep one interpreter version in several files. The third, lock-file
   maintenance, failed on a `winreg` test stub that pull request #625 fixes.
3. **Flaky macOS failures on `main` sent runs back for a re-run.** 4 of 12 macOS runs on `main`
   failed. Two of them were the same set-up error in `tests/test_name_dolt.py`: `FileExistsError` on
   `schema-root/.dolt`. With `tmp_path_retention_policy = "failed"`, a passing test's temporary
   directory is deleted with errors ignored and its numbered name is reused by the next
   parametrization, so a partial delete left the old `.dolt` directory for the next case to collide
   with.

## Fix

- `renovate.json5`: `rebaseWhen: "conflicted"`; the pre-commit manager is on for
  `astral-sh/ruff-pre-commit` only, grouped with the `ruff` package into one pull request; the
  `pyenv` manager (`.python-version`) is off. `tests/test_renovate_config_contract.py` pins each rule.
- `tests/test_name_dolt.py`: the `Databases` fixture tolerates a schema root left by an earlier,
  partly deleted directory of the same name.
- `scripts/run_local_ci.py`: runs one required check's commands, read from the workflow file, on
  the current machine, and with `-r/--report` posts the result as a commit status of the same name.
  `docs/procedures/github-actions-status.md` describes the fallback route.

## Verification

- The Renovate contract tests failed against the old configuration (3 of 7) and pass after it.
- The local-CI tests drive the script through its command line against the real workflow files.
- Expected effect, from the table above: Renovate runs drop from about 4 full sets per update branch
  per merge window to one set per real update, which removes most of the 53.6% share and most of
  the macOS slot holders during waits. Read the next week of runs to confirm.

## Design Decisions

<a id="d-1"></a>

#### D-1: How Renovate keeps its branches current — ✅ Resolved by Claude (us.anthropic.claude-opus-5-5)

**Context.** Renovate rebased its branches after every merge, and every rebase re-ran the full set.

##### (a) Rebase only on a conflict

**Pros:**
- Removes the per-merge re-runs outright; an update is tested once per new version.
- Automerge still works, because the ruleset does not require up-to-date branches.

**Cons:**
- An update branch can be tested against an older `main` than the one it merges into.

##### (b) Keep rebasing, on a schedule

**Pros:**
- Branches stay closer to `main`.

**Cons:**
- Each scheduled window still re-runs every open update in full.

##### (c) Run only Linux jobs for Renovate branches until they pass

**Pros:**
- Keeps rebasing while cutting macOS and Windows demand.

**Cons:**
- The workflows would need a branch-dependent skip that still reports `test-windows` and
  `test-macos`, which every pull request then depends on.

##### Recommendation

(a): it removes the measured cause with one setting. Pull requests from people are not rebased
either, so (a) holds update branches to the same standard.

> **Decision:** — (a) Rebase only on a conflict
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-opus-5-5; ai-effort=high; ai-profile=agent -->
<!-- decision-lineage: decision-id=AI-CLI-kreio/D-1; decision-topic=renovate-rebase-policy; governs=config:renovate.json5; normalized-proposition=renovate-rebases-update-branches-only-on-conflict; applicability=host:all; outcome-id=rebase-when-conflicted; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

<a id="d-2"></a>

#### D-2: The two update kinds that could never pass — ✅ Resolved by Claude (us.anthropic.claude-opus-5-5)

**Context.** The ruff and Python updates each changed one of several pins that this repository
keeps equal on purpose, so they were red by construction.

##### (a) Update ruff's two pins together, and stop Renovate moving the interpreter

**Pros:**
- The ruff update becomes mergeable; the interpreter stays a deliberate, multi-file change.

**Cons:**
- A new Python release no longer opens a reminder pull request.

##### (b) Turn Renovate off for both

**Pros:**
- No more failing update pull requests of either kind.

**Cons:**
- ruff updates stop too, and they were otherwise mergeable.

##### Recommendation

(a): it keeps the useful update and removes the one that cannot pass. Other pre-commit hooks stay
off, so enabling that manager adds no new pull requests.

> **Decision:** — (a) Update ruff's two pins together, and stop Renovate moving the interpreter
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-opus-5-5; ai-effort=high; ai-profile=agent -->
<!-- decision-lineage: decision-id=AI-CLI-kreio/D-2; decision-topic=renovate-pin-groups; governs=config:renovate.json5; normalized-proposition=ruff-pins-update-together-and-the-interpreter-pin-is-not-auto-updated; applicability=host:all; outcome-id=group-ruff-disable-pyenv; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->
> **Post-build review:** behaviour-change: Renovate no longer opens pull requests for `.python-version`.

<a id="d-3"></a>

#### D-3: Docs-only and already-tested main pushes — ✅ Resolved by Claude (us.anthropic.claude-opus-5-5)

**Context.** Two candidates were measured: a docs-only pull request that reports the required checks
without running the OS sets, and a `main` push that skips the OS sets when its tree equals a pull
request head that already passed.

##### (a) Do not build either now

**Pros:**
- No gate job in front of the required checks, which would decide whether `test-windows` and
  `test-macos` mean anything.

**Cons:**
- Leaves the measured demand of both: 2 of 22 non-Renovate pull requests were docs-only, and 6 of
  22 `main` pushes had a tree equal to their pull request's tested head, about 7% of job-minutes
  together.

##### (b) Add a scope job that both OS workflows wait on

**Pros:**
- Removes that 7%.

**Cons:**
- A mistake in the scope job turns a required check green without running it.

##### Recommendation

(a): with the Renovate cause gone, 7% does not justify a job that can turn the required checks green
without running them. Revisit if the next week of runs still shows waits.

> **Decision:** — (a) Do not build either now
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-opus-5-5; ai-effort=high; ai-profile=agent -->
<!-- decision-lineage: decision-id=AI-CLI-kreio/D-3; decision-topic=ci-scope-skips; governs=config:.github/workflows; normalized-proposition=no-docs-only-or-tested-tree-skip-for-the-os-workflows; applicability=host:all; outcome-id=no-scope-gate; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

<a id="d-4"></a>

#### D-4: Windows shard count — ✅ Resolved by Claude (us.anthropic.claude-opus-5-5)

**Context.** Windows is 73% of hosted job-minutes and fans out to 12 shards.

##### (a) Keep 12 shards

**Pros:**
- The slowest shard of a green `main` run took 24 minutes against a 40-minute cap.

**Cons:**
- 12 Windows slots per run while it runs.

##### (b) Go back to 8 shards

**Pros:**
- 4 fewer concurrent Windows jobs per run.

**Cons:**
- Saves about 4 job-minutes per run: set-up was 13 of 216 job-minutes in that run, so the total
  barely moves, and 8 shards already ran into the cap once.

##### Recommendation

(a): the shard count changes how many slots a run holds at once, not how much work it does, and
fewer shards brings back the timeouts that led to 12.

> **Decision:** — (a) Keep 12 shards
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-opus-5-5; ai-effort=high; ai-profile=agent -->
<!-- decision-lineage: decision-id=AI-CLI-kreio/D-4; decision-topic=windows-shard-count; governs=config:.github/workflows/ci-windows.yml; normalized-proposition=the-windows-suite-keeps-twelve-shards; applicability=host:all; outcome-id=twelve-shards; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->

<a id="d-5"></a>

#### D-5: A local route that reports the required checks — ✅ Resolved by Claude (us.anthropic.claude-opus-5-5)

**Context.** When the hosted queue is starved and no self-hosted runner is attached, a pull request
still needs `lint`, `test`, `test-windows` and `test-macos`. The ruleset names those contexts with
no required source, so a commit status of the same name satisfies it.

##### (a) A script that runs one check's commands from the workflow and can report the result

**Pros:**
- One command per check; the commands come from the workflow file, so they cannot drift from CI.
- The status says it came from a local run, and a failure is reported as a failure.

**Cons:**
- A local machine is not a clean runner, so a local pass is weaker evidence than a hosted one.

##### (b) Document the commands only

**Pros:**
- Nothing new to maintain.

**Cons:**
- The commands drift from the workflows, and nothing reports the check names.

##### Recommendation

(a): it gives the fallback one command and keeps the commands tied to the workflows. The script
refuses to report from a dirty tree or from the wrong operating system.

> **Decision:** — (a) A script that runs one check's commands from the workflow and can report the result
<!-- decision-record: chosen-option=(a); ai-family=claude; ai-model=us.anthropic.claude-opus-5-5; ai-effort=high; ai-profile=agent -->
<!-- decision-lineage: decision-id=AI-CLI-kreio/D-5; decision-topic=local-ci-fallback; governs=src:scripts/run_local_ci.py; normalized-proposition=a-local-run-of-a-required-check-can-report-its-result-as-a-commit-status; applicability=host:all; outcome-id=local-ci-script; relation=different-question; related-decision-id=; supersedes=; approval-log-decision-id=; approval-actor=; approval-date=; approval-commit= -->
> **Post-build review:** behaviour-change: a maintainer can now satisfy a required check from a local run; the status description says so.
