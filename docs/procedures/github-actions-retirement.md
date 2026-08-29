# GitHub Actions CI Retirement

> GitHub-hosted Actions checks are retired for this repository and are not a code-verification
> signal.
>
> Last updated: 2026-08-29

## Decision and scope

GitHub Actions CI is retired on this repository. The maintainer's GitHub Actions billing has been
exhausted, blocking every hosted job before it can execute; this is an account-level billing
condition, not a code failure or a repository-level defect. The project is migrating to
self-hosted CI runners.

The branch ruleset on `main` no longer requires the `lint` / `test` GitHub Actions checks to pass
before merging, since those checks cannot run. `ci.yml` is retained as reference material and is
manual-only (`workflow_dispatch`) until self-hosted CI is live — it must not auto-trigger on
`push` or `pull_request` while Actions billing remains exhausted.

`publish.yml` (the PyPI release workflow) is unaffected by this change and still triggers on
version tags; it will also fail if attempted while billing is exhausted, until GitHub Actions
billing is restored or replaced with a self-hosted/manual release path.

## Authoritative verification

Until self-hosted CI is confirmed live, the local test gate is the authoritative verification
signal: `ruff check`, `ruff format --check`, `pyright`, and `pytest`, run locally before merging
a change. Do not treat a red or missing GitHub Actions check as meaningful during this interval —
a hosted check fails on billing before it ever executes code, and a `workflow_dispatch`-only check
has not run automatically at all.

## Session checklist

1. Use the local test gate as the verification result until self-hosted CI is confirmed live.
2. Treat a hosted Actions failure as billing-blocked infrastructure, not a code failure.
3. Keep `ci.yml` manual-only unless this retirement decision is explicitly revised.
