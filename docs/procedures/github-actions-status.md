# GitHub Actions CI Status

> GitHub Actions CI is **active** on this repository and its results are a real verification
> signal. It is not billing-blocked.
>
> Last updated: 2026-09-10

## Current state

`ci.yml` triggers on `push` to `main`, on `pull_request`, and manually via `workflow_dispatch`. It
runs on GitHub-hosted standard runners.

A red CI check means the code or the workflow is broken. Read it. Do not dismiss it as
infrastructure.

## The retirement this replaces was based on a false premise

This file supersedes `github-actions-retirement.md`, which stated that "the maintainer's GitHub
Actions billing has been exhausted, blocking every hosted job before it can execute" and that the
project was "migrating to self-hosted CI runners". That was measured false on 2026-09-10.

**Evidence, from the very day the retirement was written.** Run `33238718021`, on `main`,
2026-08-29 — the date in the retired doc's own `Last updated` line:

| job | runner | conclusion | duration |
|---|---|---|---|
| `lint` | `GitHub Actions 1000027777` | **success** | 17s |
| `test (3.12)` | `GitHub Actions 1000027779` | failure | ~3 min |
| `test-windows (3.11/3.12/3.13)` | `GitHub Actions 1000027778/81/82` | failure | ~6 min |

Two facts kill the billing hypothesis. The jobs ran for **minutes**, whereas a billing-blocked job
is rejected before execution and fails in seconds. And `lint` **passed** — a job cannot pass without
having run.

Confirmed again on current runs: CodeQL executed successfully on `main` and on PR #122 on
2026-09-09, reporting `runner_group_name: "GitHub Actions"` with an `ubuntu-latest` label, which is
a GitHub-hosted runner rather than a self-hosted one.

**Why that is the expected state.** GitHub Actions is free and unmetered for **public**
repositories on standard GitHub-hosted runners, on every plan including Free. Minute allowances and
spending limits apply to private repositories and to larger or specialised runners. This repository
is public and `ci.yml` targets only `ubuntu-latest` and `windows-latest`.

**What most likely happened.** CI was red because tests were failing. That was read as a billing
block, and an account-level billing condition affecting private repositories was generalised to a
public repository where it never applied. The consequence was worse than a stale doc: the retirement
told every session to disregard CI results, and removed `lint` / `test` from the branch ruleset, so
the real failures stopped being visible at all.

## Known-failing tests are a separate problem

Re-enabling the triggers does not make CI green. The failures above are real and, on the evidence,
mostly environmental or Windows-portability issues rather than a billing wall:

- `AttributeError: module 'signal' has no attribute 'SIGKILL'` / `'SIGCONT'`
- `assert 78 == 75` and `assert 78 == 0` — `EX_CONFIG` where `EX_TEMPFAIL` or success was expected,
  which suggests the runner lacks configuration the test assumes
- `assert '--continue' in [...'--resume'...]` — a CLI-flag expectation that has since changed

Tracked separately. Fix them on their merits; do not silence CI again to hide them.

## Required status checks

`lint` and `test` are **not** currently required by the `main` ruleset. They were removed during the
retirement and have deliberately not been re-added yet: making a failing check required would block
every merge, which is how a well-intentioned gate becomes an outage. Re-add them once the failures
above are fixed, `lint` first since it already passes.

## Session checklist

1. Treat CI results as meaningful. A failure is a code or workflow problem.
2. The local gate (`ruff check`, `ruff format --check`, `pytest`) remains the fast pre-push signal;
   CI is the cross-platform one, and it covers Windows, which no local run on Linux or macOS does.
3. Do not restrict `ci.yml` to `workflow_dispatch` again without evidence that hosted jobs cannot
   execute — and "the checks are red" is not that evidence.

## A note on dispatching manually

`ghp workflow run ci.yml --ref main` returns `HTTP 403: Resource not accessible by personal access
token`. That is a **PAT scope** limit (the token lacks `Actions: write`), not a repository or
billing condition. Grant that permission if manual dispatch is needed, or push to a branch and let
the `pull_request` trigger do it.
