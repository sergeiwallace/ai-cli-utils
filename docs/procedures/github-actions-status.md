# GitHub Actions CI Status

> GitHub Actions CI is **active** on this repository and its results are a real verification
> signal. It is not billing-blocked.
>
> Last updated: 2026-11-09

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
is public and `ci.yml` targets only `ubuntu-latest`, `windows-latest` and `macos-latest`.

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

All four CI jobs are required on `main`: **`lint`, `test`, `test-windows`, `test-macos`**. They were
removed during the retirement, left out while the failures above were live, and restored together
once those were fixed (AI-CLI-66mk).

Two things had to change alongside registering the names. `test-macos` lost its
`continue-on-error: true`, because a job permitted to fail cannot gate anything — requiring one is
how a required check gets quietly disabled. And the names themselves are post-consolidation: the
`python-version` matrices are gone, so the contexts are bare job names with no `(3.12)` suffix. A
context that never reports blocks a PR forever, so **read the names off a completed run before
changing this set**, never off a doc:

```bash
gh pr view <n> --json statusCheckRollup --jq '.statusCheckRollup[] | .name'
```

### Checking whether `main` is protected — use the rulesets endpoint

**`gh api repos/user/ai-cli-utils/branches/main/protection` returns `404 Branch not protected` even
when `main` is fully protected.** That endpoint reports only *classic* branch protection. This repo
uses a **ruleset** (`main-protection`), and rulesets do not surface there at all. The 404 is not
evidence of anything.

That is not a hypothetical trap: it was read as "there is no protection on `main`" once already.
Commands that tell the truth:

```bash
# Every rule in force on the branch, whatever the mechanism
gh api repos/user/ai-cli-utils/rules/branches/main --jq '.[].type'

# The ruleset itself, including the required contexts
gh api repos/user/ai-cli-utils/rulesets --jq '.[] | "\(.id) \(.name) \(.enforcement)"'
gh api repos/user/ai-cli-utils/rulesets/<id>

# Cheapest one-line answer
gh api repos/user/ai-cli-utils/branches/main --jq '.protected'
```

Add rules by `PUT`ting the ruleset, which **replaces** it — send the existing rules and
`bypass_actors` back or you will silently drop them. The ruleset also enforces
`required_linear_history`, `deletion` and `non_fast_forward`.

Repository admins keep `bypass_mode: always`. That is what preserves the direct-push path for
commits touching only `.beads/`, and it means a flaky required check holds a merge rather than
hard-locking the repo.

## `test-windows` survives an MSYS2 download outage

`test-windows` runs its tests in an MSYS2 shell set up by `msys2/setup-msys2`. Its default,
`release: true`, downloads the MSYS2 installer from GitHub Releases on every run, and that download
answered `Unexpected HTTP response: 504` on all three of the action's own retries (about 30 seconds
in total) on 2026-11-09. The job failed before any test ran; a re-run passed.

A failed attempt now waits and tries again. Attempt 1 is the action's default: it downloads the
installer and installs it under `RUNNER_TEMP` on the D: drive. After a 2-minute wait, attempt 2 does
the same into its own `location`, because the action refuses to install over the directory a failed
attempt leaves behind. After 4 more minutes, attempt 3 sets `release: false`. That reuses the MSYS2
the Windows runner image ships at `C:\msys64` and needs no download, and `update: true` brings it to
the same current packages through pacman, which moves to the next mirror on its own when one fails.
The first two attempts carry `continue-on-error: true`, and each later step is gated on the previous
attempt's `outcome == 'failure'`, so only a failure of attempt 3 fails the job.

`C:\msys64` is the last resort rather than the first choice. An earlier version of this fix used it
on every run, and two of the three runs that did hit the 40-minute job cap partway through pytest,
reaching 80% of the suite after about 2,200 seconds where comparable runs from D: got there in about
1,500. A direct benchmark in one job found no difference between the two drives in process start,
MSYS2 fork or tmux server start-up, so the cause is not pinned down. Running from D: in the normal
case keeps the job identical to what it was before the retries existed.

A run that needed a retry still reports `test-windows` as passed. The retry shows up as a failed
`msys2-attempt-1` step with an error annotation on the run page, which is where to look when
Windows runs start taking several minutes longer than usual.

## Windows suite runtime: two shards

`test-windows` used to be one job running the whole suite. By 2026-11 its pytest step took 34 to 39
minutes against the job's 40-minute cap: 33m56s, 35m04s and 34m52s on three `main` runs, and 35m51s
and 39m04s on a PR, where the second was cancelled mid-suite at 40m12s with no test failing. MSYS2
setup took 35 to 45 seconds each time, so the suite itself was the cost.

Measured on 2026-11-09, on `main`'s tree:

| Configuration | xdist workers | Suite time |
|---|---|---|
| `-n auto` (probe run 42684654867) | 2 | 1413 s |
| `-n auto` (`main` run 42585808128, same commit) | 2 | 2089 s |
| `-n logical` (PR run 42700237036) | 4 | 1330 s |
| `-n logical` (dispatch run 42700316295) | 4 | 1371 s |

Three things follow from that table:

- `-n auto` counts physical cores, and the 4-vCPU `windows-latest` runner reports 2.
- Doubling the workers saved about 5%, because each test took about twice as long: the slowest copier
  tests went from 17 s to 32-36 s. The suite is CPU-bound on this runner, not waiting on I/O.
- The same commit and worker count took 1413 s on one runner and 2089 s on another. Runner speed is
  what moves the total, and nothing in the suite controls it.

The slowest areas were the copier-update tests (real git and copier subprocesses) and the
session-name family, whose fixtures build a real bare git remote for every test. No test had a
Windows-specific sleep or timeout to scale.

So the job now runs as a two-job matrix, `test-windows (shard 1/2)` and `(shard 2/2)`. The
repo-root plugin `pytest_ci_shard.py` (`-p pytest_ci_shard --ci-shard=INDEX/COUNT`) assigns each
test to a shard by a CRC-32 of its node id, so the shards are disjoint, cover the suite and need no
coordination. It is hand-rolled because pytest-split and pytest-test-groups split by position in
the collected order when there is no stored durations file, and pytest-randomly shuffles that order
with a different seed in each job. pytest-shard hashes too, but has had no release since 2020.
A small `test-windows` job that `needs` both shards is what reports the required check, so the
ruleset is unchanged. It runs under `always()`, so a failed or cancelled shard turns it red
instead of leaving it skipped.

The 40-minute cap stays. Each shard carries half the work, so even the slowest runner measured leaves
it well under the cap. The cost is a second MSYS2 setup per run, which doubles the exposure to a
transient MSYS2 download failure. The step also passes `--durations=15` and omits `-q`, so every log
shows the worker count and the slowest tests.

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
