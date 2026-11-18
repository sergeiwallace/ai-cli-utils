# GitHub Actions CI Status

> GitHub Actions CI is **active** on this repository and its results are a real verification
> signal. It is not billing-blocked.
>
> Last updated: 2026-11-18

## Current state

CI is three workflows, one per operating system, and all three trigger on `push` to `main`, on
`pull_request`, and manually via `workflow_dispatch`:

| Workflow | Jobs |
|---|---|
| `ci.yml` | `lint`, `test` |
| `ci-windows.yml` | `test-windows (shard 1/12)` to `(shard 12/12)`, `test-windows` |
| `ci-macos.yml` | `test-macos (shard 1/2)` and `(shard 2/2)`, `test-macos` |

Every job's first attempt runs on a GitHub-hosted standard runner. A later attempt can run on a
self-hosted runner when the maintainers' runner fleet authorizes it, as described in
[Hosted first, with a self-hosted fallback](#hosted-first-with-a-self-hosted-fallback). With that
authorization absent, every job runs where it always ran.

A red CI check means the code or the workflow is broken. Read it. Do not dismiss it as
infrastructure.

## Hosted first, with a self-hosted fallback

Hosted runners for this repository share a concurrent-job cap, and when many runs arrive together
jobs can wait for hours. The workflows let an external watchdog move a starved run onto a
self-hosted runner without changing any check name. The watchdog lives outside this repository;
what this repository carries is the contract below, and
`tests/test_ci_runner_fallback_contract.py` checks it on every job of every workflow.

The repository variable `CI_FALLBACK_RUNS_ON` holds a JSON object. Each key is
`<run_id>-<run_attempt>-<os>` and each value is the label array of a self-hosted runner pool. An
entry authorizes one attempt of one run for one OS key, and nothing else. Unset or `{}` means no
fallback anywhere. This repository never names a self-hosted label; they arrive only through the
variable.

The OS key is fixed per workflow:

| Workflow | OS key |
|---|---|
| `ci.yml` | `linux` |
| `ci-windows.yml` | `windows` |
| `ci-macos.yml` | `macos` |
| `publish.yml`, `wiki.yml` | `release` |

Each job's `runs-on`, for OS key `<os>` and hosted label `<hosted>`, is exactly:

```text
${{ github.run_attempt > 1 && (github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository) && fromJSON(vars.CI_FALLBACK_RUNS_ON || '{}')[format('{0}-{1}-<os>', github.run_id, github.run_attempt)] || '<hosted>' }}
```

So attempt 1 of every job asks for its hosted label whatever the variable holds. A later attempt
asks for the pool labels only when the run is a push, a manual dispatch or a pull request whose
head branch is in this repository, and the variable has the exact entry for this run, attempt and
OS key. A pull request from a fork always runs on hosted runners. A re-run started by hand has no
entry, so it tries hosted runners too.

The summary jobs `test-windows` and `test-macos` use their own workflow's OS key with the hosted
label `ubuntu-latest`. On a re-routed attempt they run on the same self-hosted runner after their
shards, instead of waiting for a hosted Linux runner again.

Steps that cannot run on a self-hosted runner have variants chosen by `runner.environment`
(`github-hosted` or `self-hosted`). On a hosted runner each job runs the steps it always ran. On a
self-hosted runner:

- every job first points `HOME` and `DOLT_ROOT_PATH` at directories under `$RUNNER_TEMP`, because
  the runner's account may have no writable home;
- Linux jobs install uv with uv's installer script instead of `astral-sh/setup-uv`, and every
  `uv sync` still carries `--locked`;
- the `test` job's NATS service image is the empty string, so no service container starts, and the
  host must provide tmux, since no step there may use `sudo` or a package manager;
- the macOS shards run `brew install tmux` only when tmux is missing;
- the publish job publishes with `uv publish --trusted-publishing always`, because
  `pypa/gh-action-pypi-publish` needs Docker; it keeps `id-token: write`;
- the wiki publish job needs `git` and `rsync` on the host.

A self-hosted runner may export `CI_TEST_WRAPPER`, a command prefix that the `test` job and the
macOS shards put before `uv run pytest`. Hosted runners leave it unset, and the step then runs
`uv run pytest` directly.

Windows has no self-hosted pool, so `ci-windows.yml` carries the routing expression but no step
variants.

### Superseded pull request runs are cancelled

Each CI workflow has a `concurrency` group per pull request with `cancel-in-progress` for
`pull_request` events, so a newer push to a pull request cancels that pull request's older run of
the same workflow. A push to `main` or a manual dispatch gets a group of its own, keyed by its run
id, so it never cancels or waits on another run.

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
is public and its CI workflows ask hosted runners only for `ubuntu-latest`, `windows-latest` and
`macos-latest`.

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

### Twelve Windows shards since 2026-11-16

The session-name registry's real-Dolt test cases are slow on Windows, and they pushed both
Windows shards past the 40-minute cap at 46% and 62% (PR #582). Four shards were not enough
either. In run 43758174269, shard 1/4 finished in 36 minutes with 24 failures, and shards 2/4 to
4/4 were cancelled at the cap at about 84%. Of the 73 Dolt cases in shard 1/4, 22 failed because a
`dolt clone` overran its 60-second budget. Those cases took 70 to 137 seconds each, against 12 to
24 seconds for the same cases on macOS. No `dolt fetch`, `push`, `sql` or `reset` overran its own
budget. Most Dolt cases clone the registry at least once, for their observer and for each other
machine they simulate, so Windows pays that clone cost many times in every shard.

The product now gives a clone its own budget, `[session_names] registry_clone_timeout_seconds`,
which defaults to 300 seconds on Windows and 60 elsewhere. A clone is also the only remote call a
host's first read makes, with no fetch straight after it. Cases that used to stop at 60 seconds
now run to the end, so each shard's work goes up. The Windows matrix therefore runs eight shards,
`(shard 1/8)` to `(8/8)`, under the same `test-windows` rollup check. That halves the work of a
four-shard shard, which projects to about 25 minutes from the 4/4 shards' pace. Every Dolt case
still runs on Windows: none is skipped or deselected.

Eight shards were not enough either. In run 43786033751 the eight Windows shards took 20 to 34
minutes, and shard 5/8 was cancelled at the cap at about 95% with no failing test. In the same
run, three Dolt cases were killed by the suite-wide 300-second per-test bound. That bound uses
pytest-timeout's thread method, which ends the worker with `os._exit`, so xdist reported each
kill as a crashed worker. Real-Dolt cases now get a 900-second bound of their own
(`DOLT_TEST_TIMEOUT_S` in `tests/conftest.py`), and the Windows matrix runs twelve shards,
`(shard 1/12)` to `(12/12)`. At shard 5/8's pace, about 42 minutes for an eighth of the
suite, a twelfth takes about 28 minutes.

## macOS suite runtime: two shards

`test-macos` had a 45-minute cap and one job. Healthy runs on `main` took 42 minutes by
2026-11-15 (PR #583: 16:54 to 17:36 UTC), and a pull request adding the session-name
registry's real-Dolt test cases was cancelled at the cap at 83% with no failing test. It now
runs the same way as Windows: `test-macos (shard 1/2)` and `(shard 2/2)` split the suite with
`pytest_ci_shard`, and a small `test-macos` job that `needs` both reports the required check
under `always()`. The 45-minute cap stays and applies to each shard.

## Session checklist

1. Treat CI results as meaningful. A failure is a code or workflow problem.
2. The local gate (`ruff check`, `ruff format --check`, `pytest`) remains the fast pre-push signal;
   CI is the cross-platform one, and it covers Windows, which no local run on Linux or macOS does.
3. Do not restrict the CI workflows to `workflow_dispatch` again without evidence that hosted jobs cannot
   execute — and "the checks are red" is not that evidence.

## A note on dispatching manually

`gh workflow run ci.yml --ref main`, authenticated with a fine-grained personal access token, can
return `HTTP 403: Resource not accessible by personal access token`. That is a **PAT scope** limit (the token lacks `Actions: write`), not a repository or
billing condition. Grant that permission if manual dispatch is needed, or push to a branch and let
the `pull_request` trigger do it.
