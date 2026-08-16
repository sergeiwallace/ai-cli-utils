---
title: Windows CI matrix test failures -- root cause and fix scoping
category: research
tags: [research, windows, ci, testing]
status: complete
source: "gpt-5-codex-2026-08-15"
template_version: "research-1.2.0"
delegation_provenance:
  delegated_to: codex/research
  tier: research
  model: gpt-5-codex
  effort: high
  persona: research
  worktree: /Users/user/projects/ai-cli-utils/.worktrees/sw6-win-ci-research
  session: sw-6
task: AI-CLI-6ibt
---

# Windows CI matrix test failures -- root cause and fix scoping

**Status:** complete

**Created:** 2026-08-15

<!-- doc:region name="context" kind="immutable" -->

## Table of Contents

- [Context](#context)
- [Temporal Scope](#temporal-scope)
- [Executive Summary](#executive-summary)
- [1. Evidence and Method](#1-evidence-and-method)
  - [CI evidence and limitations](#ci-evidence-and-limitations)
  - [Classification rules](#classification-rules)
- [2. CI History](#2-ci-history)
- [3. Shared Root Causes](#3-shared-root-causes)
  - [Explicit UTF-8 at file and byte boundaries](#explicit-utf-8-at-file-and-byte-boundaries)
  - [Undeclared tmux availability](#undeclared-tmux-availability)
  - [Native path semantics versus display paths](#native-path-semantics-versus-display-paths)
  - [POSIX-only attributes patched by name](#posix-only-attributes-patched-by-name)
  - [The CI lane does not model the documented Windows environment](#the-ci-lane-does-not-model-the-documented-windows-environment)
- [4. Failure Classification by Test Area](#4-failure-classification-by-test-area)
- [Comparison](#comparison)
- [Recommendation](#recommendation)
  - [Immediate fix pass](#immediate-fix-pass)
  - [Add Windows skips only for documented exclusions](#add-windows-skips-only-for-documented-exclusions)
  - [Deeper follow-up](#deeper-follow-up)
- [Gaps, Blindspots and Emergent Findings](#gaps-blindspots-and-emergent-findings)
- [Open Questions](#open-questions)
- [Sources](#sources)
- [Ambiguous Items from Auto-Remediation (Post-Run Review)](#ambiguous-items-from-auto-remediation-post-run-review)
- [Appendix: Research Prompt](#appendix-research-prompt)
- [Appendix: Provenance Ledger](#appendix-provenance-ledger)
- [Run History](#run-history)

## Context

This pass scopes the approximately 100 test failures reported from the
`test-windows` GitHub Actions job. It distinguishes production portability bugs,
tests for documented Windows exclusions, portable-test defects, and unresolved
cases. It does not implement fixes.

**Primary period:** 2026-04-25 through 2026-08-15
**Source weighting:** current repository source at commit
`d50320956415e953b6de22045f47aa23df2eb9c4` and public GitHub Actions pages are
primary; the task's raw-log excerpts are leads where authenticated logs could not
be re-fetched.

<!-- /doc:region name="context" -->

<!-- doc:region name="body" kind="replaceable" -->

## Temporal Scope

The working tree was inspected at commit
`d50320956415e953b6de22045f47aa23df2eb9c4`, committed 2026-08-15. Public Actions
pages were checked on 2026-08-15; GitHub displays the latest job's completion as
2026-08-16 UTC. Historical samples cover the Windows job's introduction on
2026-04-25 through the current run.

## Executive Summary

The failures are not approximately 100 independent defects. The best provisional
split is **about 20 production bugs, 12 tests of documented Windows exclusions,
54 portable-test defects, and 14 unresolved cases**. [INFERENCE] Four shared
causes should clear much of the matrix: explicit UTF-8 at generated-script and
subprocess boundaries, explicit tmux availability in unit tests, path-aware
fixtures/assertions, and a Windows test environment that actually models the
documented MSYS2/Git Bash support contract. [INFERENCE]

The premise that tmux is unsupported on Windows is stale. The current README tells
Windows users to install tmux under MSYS2; its exclusion list names remote
sessions, tunnels, and iTerm2 color management instead. [VERIFIABLE][^1] The
Windows-support plan likewise selected Git Bash as the primary shell, required
tmux, and specified no blanket Windows test skipping. [VERIFIABLE][^2]

The job is not a new 24-hour regression. Its inaugural matrix run timed out after
six hours, while the current failure reaches pytest and exits in minutes.
[VERIFIABLE][^3][^4] No green baseline was established in this pass: the first run,
sampled runs in July and August, and the current run all failed, but an exhaustive
all-history job-level query was unavailable. [INFERENCE]

## 1. Evidence and Method

### CI evidence and limitations

The current workflow runs `uv run pytest --tb=short -q` on `windows-latest` for
Python 3.11, 3.12, and 3.13. It does not install MSYS2 or tmux, select Git Bash as
the step shell, or set a Python UTF-8 environment variable. [VERIFIABLE][^5]

The public current job page confirms that Python 3.12 failed in pytest after 3m32s,
but requires sign-in to view the raw log. [VERIFIABLE][^4] The local `gh` credential
was rejected and shell network resolution was unavailable. Consequently, this
pass could not independently enumerate every failing test or prove the task
brief's "15+ consecutive" count. [NO SOURCE] The category counts below are bounded
estimates based on the supplied failure signatures plus broad inspection of the
named tests and their production call paths; they are not a reconstructed pytest
summary. [INFERENCE]

Two read-only local checks were attempted. `scripts/lint_doc_alignment.py` passed,
and `scripts/check_ruff_version_sync.py` exited successfully. Focused pytest could
not start because the sandbox denied all temporary-directory candidates. [NO SOURCE]

### Classification rules

- **(a) Production bug:** real Windows input reaches code that mishandles encoding,
  separators, or another supported behavior.
- **(b) Windows skip:** the test crosses into a feature the current README explicitly
  excludes on Windows. A pure argument-builder test remains portable even when its
  eventual integration is excluded.
- **(c) Portable-test defect:** production behavior is valid, but a mock, fixture,
  expected string, executable layout, or CI prerequisite assumes POSIX.
- **(d) Unclear:** the supplied signature cannot be mapped confidently without the
  exact traceback or a native Windows reproduction.

## 2. CI History

The Windows matrix was added on 2026-04-25 in the repository's Windows-support
commit. [VERIFIABLE][^6] The inaugural Python 3.11, 3.12, and 3.13 jobs all exceeded
the six-hour limit; the linked 3.11 page is representative. [VERIFIABLE][^3] This
rules out a previously green launch baseline. [INFERENCE]

The failure mode has changed over time:

| Date | Sample | Observed failure stage | Implication |
|---|---|---|---|
| 2026-04-25 | Inaugural 3.11 job | Six-hour timeout [VERIFIABLE][^3] | Broken from introduction |
| 2026-07-20 | Python 3.12 job | `uv sync --dev` [VERIFIABLE][^7] | Provisioning also unstable |
| 2026-07-29 | Python 3.11 job | Checkout [VERIFIABLE][^8] | Not every red run is pytest |
| 2026-08-08 | Python 3.11 job | Matrix failure/cancellation [VERIFIABLE][^9] | Failure persisted before current burst |
| 2026-08-16 UTC | Python 3.12 job | Pytest exit 1 in 3m32s [VERIFIABLE][^4] | Current mass failure is now exposed |

The main-branch Actions view currently reports 953 workflow results and shows the
latest sequence of main runs, but it does not expose a job-level success filter in
the unauthenticated rendered page. [VERIFIABLE][^10] Therefore, "the Windows job
has never been green" remains unproven; the defensible conclusion is that it was
red at introduction, is red now, and has no green sample in the dates checked.
[INFERENCE]

## 3. Shared Root Causes

### Explicit UTF-8 at file and byte boundaries

`_write_launch_script_if_changed()` calls `Path.read_text()` and
`Path.write_text()` without an encoding. [VERIFIABLE][^11] The generated script
contains `✦`, `▶`, `✓`, `✗`, `↻`, and `⏸`. [VERIFIABLE][^12] Python documents that
these methods otherwise use the platform-dependent locale encoding.
[VERIFIABLE][^13] On a cp1252 Windows locale, writing `✦` reproduces the supplied
failure mechanism; adding `encoding="utf-8"` at this central helper should clear
multiple launch-script, refresh, and runaway-loop tests. [INFERENCE]

The tmux error path also calls `.decode()` without an encoding on captured byte
stderr. [VERIFIABLE][^14] A supplied byte `0x97` is not valid standalone UTF-8, so
this is a second real production boundary even though the exact failing traceback
could not be retrieved. [INFERENCE] Use an explicit encoding policy with a
non-crashing error strategy rather than relying on the runner locale. [HEURISTIC]

Estimated impact: **8-15 failures**, mostly category (a). [INFERENCE]

### Undeclared tmux availability

Production intentionally switches a Windows launch to bare mode when
`shutil.which("tmux")` returns false. [VERIFIABLE][^15] Many CLI tests mock tmux
subprocesses and `os.execvp` but do not mock `shutil.which`; on the stock runner,
the code correctly takes bare mode before the expected `new-session`, `attach`, or
`execvp` call. [VERIFIABLE][^16] Those tests need to declare tmux as an input, not
skip Windows. [INFERENCE]

Estimated impact: **10-20 failures**, category (c). [INFERENCE]

### Native path semantics versus display paths

Three distinct path problems are grouped in the log leads:

1. `TestDetectRepoRoot` feeds `/home/user/...` to `WindowsPath`; that is not a
   drive-qualified Windows absolute path. The fixture models Unix Git output while
   executing Windows path semantics. [VERIFIABLE][^17]
2. Copier, public-hygiene, sync conflict-display, and similar tests compare
   `str(Path(...))` with hard-coded `/` strings. Native Windows output uses `\`.
   These are normally category (c), unless forward slashes are an explicit public
   serialization contract. [INFERENCE]
3. Session migration has a production defect: nested `cwd` and `originalCwd` values
   are detected only by `source_root + "/"`. [VERIFIABLE][^18] A resolved Windows
   root followed by `\subdir` is not rewritten, so this is category (a), separate
   from any strict expected-string assertion. [INFERENCE]

The sync module contains further production recognizers for `"/projects/"` and
`".worktrees/"`, plus `/` splitting, against values described as filesystem paths.
[VERIFIABLE][^19] These are credible category (a) bugs, but cross-machine transcript
serialization may intentionally use POSIX-form paths; confirm with a Windows
fixture before changing all of them. [INFERENCE]

Estimated impact: **15-25 failures** across categories (a) and (c). [INFERENCE]

### POSIX-only attributes patched by name

Python documents `os.getuid()` and `os.ttyname()` as Unix-only.
[VERIFIABLE][^20] Two CLI tests patch `os.getuid` even though production already
routes privilege detection through a Windows-aware helper, while two iTerm2 tests
patch `os.ttyname` directly even though that attribute does not exist on Windows.
[VERIFIABLE][^21][^22] Patch the project helper, inject the attribute with
`create=True`, or patch a platform-neutral seam. These are category (c), not reasons
to alter otherwise guarded production behavior. [INFERENCE]

Estimated impact: **four failures**. [INFERENCE]

### The CI lane does not model the documented Windows environment

The repository supports Windows specifically through MSYS2 plus Git Bash and tells
users to install tmux there. [VERIFIABLE][^1] The current Windows job instead uses
the default runner shell and installs neither dependency. [VERIFIABLE][^5] Tests in
`test_config_watch_hash.py` invoke `bash`, `sha256sum`, and a hard-coded POSIX
`PATH`; GrowthBook tests similarly invoke real Bash with POSIX path entries.
[VERIFIABLE][^23][^24]

These are not proof that the product feature is unsupported on Windows. They show
that the behavioral test harness and CI support contract disagree. [INFERENCE]
Either create a native-unit-test seam and retain one MSYS2/Git Bash integration
lane, or provision the documented environment before running those tests.
[HEURISTIC]

Estimated impact: **10-18 failures**, principally category (c). [INFERENCE]

## 4. Failure Classification by Test Area

Counts are rough allocations, not exact log counts. Mixed rows identify the
dominant class first.

| Test area | Provisional class | Approx. failures | Evidence and action |
|---|---:|---:|---|
| `test_cli.py` | (c), some (a) | 20-30 | Declare tmux availability; patch `_is_root`; make path display portable; fix UTF-8 helper |
| `test_session.py` | (c) | 2-5 | Use drive-aware Git-output fixtures or platform-mocked path classes |
| `test_remote.py` | (b)/(c) | 6-10 | Skip Windows CLI integration for documented remote exclusion; keep pure builders portable |
| `test_iterm2.py` | (c), some (b) | 2-6 | Patch the tty seam portably; skip only live iTerm2/color integration |
| `test_sync.py` | (a)/(c)/(d) | 8-15 | Normalize filesystem logic; separate display paths; reproduce transcript path format |
| `test_quota.py` | (c)/(d) | 3-7 | Separate Bash-script integration from Python logic; retain Git Bash coverage |
| `test_workspace.py` | (c) | 2-4 | Build JSON with `json.dumps`; do not interpolate unescaped Windows paths |
| `test_growthbook_launch_toggle.py` | (c) | 4-5 | Run in a declared Git Bash lane or inject the shell/tool paths |
| `test_copier_update.py` | (c) | 1-3 | Compare `Path` objects or normalize intentional display strings |
| `test_bare_worktree.py` | (c)/(d) | 1-4 | Existing `/proc` skips are appropriate; inspect remaining traceback before expanding |
| `test_config_watch_hash.py` | (c) | 5 | Replace hard-coded POSIX `PATH`; execute in the documented shell lane |
| `test_cc_migrate.py` | (a)/(c) | 2-4 | Fix separator-aware nested-root rewrite, then make expected path native |
| `test_public_repo_hygiene.py` | (c) | 2-5 | Normalize report paths or compare `Path` components |
| `test_runaway_loop_guards.py` | (a) | 2-5 | Central UTF-8 generated-script write is the likely shared fix |
| `test_sync_watch.py` | (d) | 0-3 | PID liveness already uses cross-platform `psutil`; exact traceback required [VERIFIABLE][^25] |
| `test_ruff_pin_integrity.py` | (c) | 2 | Fake venv creates `bin/ruff`; production checks `Scripts` plus Windows executable suffix [VERIFIABLE][^26] |
| `test_lint_doc_alignment.py` | (d) | 0-2 | Production reads both docs as UTF-8 and local check passed [VERIFIABLE][^27] |

The non-overlapping whole-suite estimate is:

| Category | Midpoint | Plausible range | Main contributors |
|---|---:|---:|---|
| (a) Production bug | ~20 | 15-25 | UTF-8 writes/decodes, migration and sync path logic |
| (b) Documented exclusion | ~12 | 8-15 | Remote, tunnel, and live iTerm2 integration only |
| (c) Portable-test defect | ~54 | 45-60 | tmux preflight, separators, POSIX attributes, Bash/ruff fixtures |
| (d) Unclear | ~14 | 10-20 | Remaining sync, quota, bare-worktree, lint-alignment traces |
| **Total** | **~100** | **estimate** | Based on supplied failure scale, not raw log enumeration |

## Comparison

| # | Fix cluster | Likely failures cleared | Effort | Risk | Priority |
|---|---|---:|---:|---:|---:|
| 1 | Explicit UTF-8 in launch-script and stderr boundaries | 8-15 | Low | Low | 1 |
| 2 | Patch tmux availability in command-path unit tests | 10-20 | Low | Low | 1 |
| 3 | Path-aware fixtures and display assertions | 15-25 | Medium | Low | 1 |
| 4 | Separator-aware migration and sync logic | 4-10 | Medium | Medium | 2 |
| 5 | MSYS2/Git Bash integration lane or declared shell fixture | 10-18 | Medium | Medium | 2 |
| 6 | Skips for documented exclusions | 8-15 | Low | Low | 2 |
| 7 | Traceback-driven residual pass | 10-20 | Unknown | Low | 3 |

These ranges overlap because one failure can cross more than one root cause; they
must not be summed. [INFERENCE]

## Recommendation

### Immediate fix pass

1. Add explicit UTF-8 to `_write_launch_script_if_changed()` reads and writes, and
   make captured tmux stderr decoding non-crashing. Add focused tests with the
   actual non-ASCII script symbols and cp1252-like bytes.
2. In every mocked tmux command-path test, patch `shutil.which("tmux")` to a stable
   executable. Keep separate tests for the intended no-tmux-to-bare Windows branch.
3. Replace literal POSIX path expectations with `Path` comparisons where semantics
   are filesystem-native. Where output is a serialized/reporting contract, choose
   and document one separator format, then normalize in production and tests.
4. Fix `cc_migrate._rewrite_line()` using separator-aware containment rather than
   `source_root + "/"`. Add drive-qualified Windows cases for exact-root and nested
   paths.

This sequence is the highest-value, lowest-effort pass and should substantially
reduce the matrix before individual failures are touched. [INFERENCE]

### Add Windows skips only for documented exclusions

Add Windows guards to tests that actually enter remote SSH/mosh, autossh tunnel,
or live iTerm2 color-management behavior. The README explicitly excludes those
three areas. [VERIFIABLE][^1]

Do **not** add a blanket Windows skip to tmux, generated Bash session scripts,
config-watch hashing, GrowthBook launch toggles, or other core launch behavior.
Those belong to the documented MSYS2/Git Bash support path, and the prior support
plan explicitly called for cross-platform tests without blanket skips.
[VERIFIABLE][^2] If the stock native-Windows lane cannot host them, mark them as
requiring the Git Bash integration lane rather than as unsupported product
features. [HEURISTIC]

### Deeper follow-up

After the shared fixes land, obtain one raw `pytest -q` failure list per Python
version and reclassify only the residuals. Prioritize:

- whether sync history records use native Windows paths or canonical POSIX paths;
- the precise source of byte `0x97` and the correct decode policy;
- the remaining `test_lint_doc_alignment.py`, `test_sync_watch.py`, quota, and bare
  worktree failures;
- whether Git checkout/sync failures are independent runner provisioning problems;
- a job-history query that conclusively finds any green `test-windows` job.

## Gaps, Blindspots and Emergent Findings

- **Stale premise:** tmux is supported, not excluded, in the current README. This
  materially narrows category (b). [VERIFIABLE][^1]
- **Environment-contract blindspot:** the job called `test-windows` tests native
  Windows Python under the runner's default setup, while user-facing support is
  scoped to MSYS2/Git Bash. Passing one does not establish the other. [INFERENCE]
- **History blindspot:** the first job and several later samples are red, but
  unauthenticated GitHub pages cannot answer whether any one of hundreds of
  historical matrix jobs was green. [NO SOURCE]
- **Failure-list blindspot:** without the raw pytest log, the estimated counts may
  over-allocate tests that share a single parametrized traceback or under-allocate
  collection/setup errors repeated across files. [INFERENCE]
- **Disconfirming evidence for two leads:** the ruff production locator already
  checks both `bin` and `Scripts`, and the doc-alignment script already reads UTF-8.
  Their failures should not be assigned to generic production encoding without
  exact traces. [VERIFIABLE][^26][^27]

## Open Questions

1. Does authenticated Actions history contain any green `test-windows` matrix job?
2. What is the exact per-file/per-version residual list after the first four shared
   fixes?
3. Should the workflow model documented support by running pytest from an MSYS2/Git
   Bash step, or keep both native-Windows and Git Bash lanes with different scopes?
4. Are transcript/history paths canonicalized by their producer, or must sync accept
   both Windows and POSIX path forms?
5. Are forward-slash conflict/report paths a public machine-readable contract or
   merely human-facing output?

## Sources

[^1]: ai-cli-utils contributors. (2026). [README: Windows installation and unsupported features](https://github.com/sergeiwallace/ai-cli-utils/blob/d50320956415e953b6de22045f47aa23df2eb9c4/README.md#L67-L115). GitHub. Verified accessible (HTTP 200) 2026-08-15. (Defines MSYS2, Git Bash, tmux installation, and the three exclusions.)

[^2]: ai-cli-utils contributors. (2026). [Windows support plan: scope, shell decision, and test strategy](https://github.com/sergeiwallace/ai-cli-utils/blob/d50320956415e953b6de22045f47aa23df2eb9c4/docs/plans/windows-support-plan.md#L26-L59). GitHub. Verified accessible (HTTP 200) 2026-08-15. (States Git Bash/tmux scope and no-skipping intent; later sections contain the CI acceptance criteria.)

[^3]: GitHub. (2026). [Inaugural Windows Python 3.11 job](https://github.com/sergeiwallace/ai-cli-utils/actions/runs/24938795498/job/73028901439). GitHub Actions. Verified accessible (HTTP 200; logs expired) 2026-08-15. (Shows the six-hour timeout.)

[^4]: GitHub. (2026). [Current Windows Python 3.12 job](https://github.com/sergeiwallace/ai-cli-utils/actions/runs/31923968515/job/95108445636). GitHub Actions. Verified accessible (HTTP 200; logs sign-in gated) 2026-08-15. (Shows pytest exit 1 and 3m32s duration.)

[^5]: ai-cli-utils contributors. (2026). [CI workflow Windows matrix](https://github.com/sergeiwallace/ai-cli-utils/blob/d50320956415e953b6de22045f47aa23df2eb9c4/.github/workflows/ci.yml#L49-L60). GitHub. Verified accessible (HTTP 200) 2026-08-15. (Defines runner, versions, setup, and pytest command.)

[^6]: ai-cli-utils contributors. (2026). [Commit adding Windows support and CI](https://github.com/sergeiwallace/ai-cli-utils/commit/085cc323f7b265a95f8924be01220bff86ed8827). GitHub. Verified accessible (HTTP 200) 2026-08-15. (Introduced the Windows matrix on 2026-04-25.)

[^7]: GitHub. (2026). [2026-07-20 Windows Python 3.12 job](https://github.com/sergeiwallace/ai-cli-utils/actions/runs/29732074860/job/88318624788). GitHub Actions. Verified accessible (HTTP 200) 2026-08-15. (Sampled dependency-sync failure.)

[^8]: GitHub. (2026). [2026-07-29 Windows Python 3.11 job](https://github.com/sergeiwallace/ai-cli-utils/actions/runs/30490667399/job/90707467361). GitHub Actions. Verified accessible (HTTP 200) 2026-08-15. (Sampled checkout failure.)

[^9]: GitHub. (2026). [2026-08-08 Windows Python 3.11 job](https://github.com/sergeiwallace/ai-cli-utils/actions/runs/31239420003/job/93057740069). GitHub Actions. Verified accessible (HTTP 200) 2026-08-15. (Sampled matrix failure before the current run.)

[^10]: GitHub. (2026). [Main-branch CI run history](https://github.com/sergeiwallace/ai-cli-utils/actions/workflows/ci.yml?query=branch%3Amain). GitHub Actions. Verified accessible (HTTP 200; partial dynamic rendering) 2026-08-15. (Shows 953 workflow results and recent main runs.)

[^11]: ai-cli-utils contributors. (2026). [Generated launch-script writer](https://github.com/sergeiwallace/ai-cli-utils/blob/d50320956415e953b6de22045f47aa23df2eb9c4/src/ai_cli/main.py#L706-L721). GitHub. Verified accessible (HTTP 200) 2026-08-15. (Read/write calls omit encoding.)

[^12]: ai-cli-utils contributors. (2026). [Generated session script status symbols](https://github.com/sergeiwallace/ai-cli-utils/blob/d50320956415e953b6de22045f47aa23df2eb9c4/src/ai_cli/session_script.py#L408-L427). GitHub. Verified accessible (HTTP 200) 2026-08-15. (Contains non-ASCII symbols written by the helper.)

[^13]: Python Software Foundation. (2026). [Pathlib `read_text` and `write_text`](https://docs.python.org/3/library/pathlib.html#pathlib.Path.read_text). Python documentation. Verified accessible (HTTP 200) 2026-08-15. (Documents locale-dependent default encoding.)

[^14]: ai-cli-utils contributors. (2026). [Tmux stderr decode path](https://github.com/sergeiwallace/ai-cli-utils/blob/d50320956415e953b6de22045f47aa23df2eb9c4/src/ai_cli/main.py#L2415-L2437). GitHub. Verified accessible (HTTP 200) 2026-08-15. (Calls byte `.decode()` without an explicit encoding.)

[^15]: ai-cli-utils contributors. (2026). [Windows no-tmux fallback](https://github.com/sergeiwallace/ai-cli-utils/blob/d50320956415e953b6de22045f47aa23df2eb9c4/src/ai_cli/main.py#L1850-L1889). GitHub. Verified accessible (HTTP 200) 2026-08-15. (Switches Windows to bare mode when tmux is absent.)

[^16]: ai-cli-utils contributors. (2026). [CLI tmux command-path tests](https://github.com/sergeiwallace/ai-cli-utils/blob/d50320956415e953b6de22045f47aa23df2eb9c4/tests/test_cli.py#L637-L674). GitHub. Verified accessible (HTTP 200) 2026-08-15. (Mocks subprocess and exec boundaries but not tmux discovery.)

[^17]: ai-cli-utils contributors. (2026). [Repository-root detection fixtures](https://github.com/sergeiwallace/ai-cli-utils/blob/d50320956415e953b6de22045f47aa23df2eb9c4/tests/test_session.py#L832-L850). GitHub. Verified accessible (HTTP 200) 2026-08-15. (Uses Unix absolute and relative paths.)

[^18]: ai-cli-utils contributors. (2026). [Session migration cwd rewrite](https://github.com/sergeiwallace/ai-cli-utils/blob/d50320956415e953b6de22045f47aa23df2eb9c4/src/ai_cli/cc_migrate.py#L109-L135). GitHub. Verified accessible (HTTP 200) 2026-08-15. (Nested-root recognition hard-codes `/`.)

[^19]: ai-cli-utils contributors. (2026). [Sync history path recognition](https://github.com/sergeiwallace/ai-cli-utils/blob/d50320956415e953b6de22045f47aa23df2eb9c4/src/ai_cli/sync.py#L560-L689). GitHub. Verified accessible (HTTP 200) 2026-08-15. (Splits and recognizes filesystem paths with `/` literals.)

[^20]: Python Software Foundation. (2026). [`os.getuid` and `os.ttyname`](https://docs.python.org/3/library/os.html#os.getuid). Python documentation. Verified accessible (HTTP 200) 2026-08-15. (Both APIs are documented as Unix-only.)

[^21]: ai-cli-utils contributors. (2026). [CLI privilege-mocking tests](https://github.com/sergeiwallace/ai-cli-utils/blob/d50320956415e953b6de22045f47aa23df2eb9c4/tests/test_cli.py#L865-L910). GitHub. Verified accessible (HTTP 200) 2026-08-15. (Patches `os.getuid` directly.)

[^22]: ai-cli-utils contributors. (2026). [iTerm2 tty helper and tests](https://github.com/sergeiwallace/ai-cli-utils/blob/d50320956415e953b6de22045f47aa23df2eb9c4/tests/test_iterm2.py#L568-L581). GitHub. Verified accessible (HTTP 200) 2026-08-15. (Patches `os.ttyname` directly.)

[^23]: ai-cli-utils contributors. (2026). [Config-watch Bash harness](https://github.com/sergeiwallace/ai-cli-utils/blob/d50320956415e953b6de22045f47aa23df2eb9c4/tests/test_config_watch_hash.py#L37-L60). GitHub. Verified accessible (HTTP 200) 2026-08-15. (Invokes Bash and Unix tools with a POSIX PATH.)

[^24]: ai-cli-utils contributors. (2026). [GrowthBook launch-toggle Bash harness](https://github.com/sergeiwallace/ai-cli-utils/blob/d50320956415e953b6de22045f47aa23df2eb9c4/tests/test_growthbook_launch_toggle.py#L37-L72). GitHub. Verified accessible (HTTP 200) 2026-08-15. (Builds a POSIX-style PATH and invokes Bash.)

[^25]: ai-cli-utils contributors. (2026). [Cross-platform PID liveness helper](https://github.com/sergeiwallace/ai-cli-utils/blob/d50320956415e953b6de22045f47aa23df2eb9c4/src/ai_cli/config.py#L25-L31). GitHub. Verified accessible (HTTP 200) 2026-08-15. (Uses `psutil.pid_exists`.)

[^26]: ai-cli-utils contributors. (2026). [Ruff executable locator and Windows-sensitive fixture](https://github.com/sergeiwallace/ai-cli-utils/blob/d50320956415e953b6de22045f47aa23df2eb9c4/scripts/check_ruff_version_sync.py#L116-L150). GitHub. Verified accessible (HTTP 200) 2026-08-15. (Production checks `bin`, `Scripts`, and executable suffix; the linked test fixture creates only `bin/ruff`.)

[^27]: ai-cli-utils contributors. (2026). [Documentation-alignment reader](https://github.com/sergeiwallace/ai-cli-utils/blob/d50320956415e953b6de22045f47aa23df2eb9c4/scripts/lint_doc_alignment.py#L44-L82). GitHub. Verified accessible (HTTP 200) 2026-08-15. (Reads both documents explicitly as UTF-8.)

<!-- /doc:region name="body" -->

<!-- doc:region name="ambiguous_items" kind="replaceable" -->

## Ambiguous Items from Auto-Remediation (Post-Run Review)

(none -- this was a manual in-session research pass, not an auto-remediation run)

<!-- /doc:region name="ambiguous_items" -->

<!-- doc:region name="appendix_research_prompt" kind="immutable" -->

## Appendix: Research Prompt

**Registry ID:** none (manual delegated invocation)
**Model:** `gpt-5-codex`
**Date:** 2026-08-15

```text
Act as a senior Python portability and CI engineer researching the failing
ai-cli-utils Windows test matrix. Modify only
docs/research/windows-ci-matrix-test-failures.md; do not change tests or
production code.

The windows-latest job runs Python 3.11, 3.12, and 3.13 and reportedly has
approximately 100 failures across CLI, session, remote, iTerm2, sync, quota,
workspace, GrowthBook, Copier, worktree, migration, public-hygiene, watcher,
ruff-integrity, and documentation-alignment tests. Investigate broadly across
the actual tests and the production paths they exercise.

For each representative failure, classify it as:
(a) a real production portability bug;
(b) a test of a feature explicitly unsupported on Windows and eligible for a
    Windows skip;
(c) a POSIX-assuming test fixture, mock, assertion, or environment mismatch;
(d) unresolved without deeper evidence.

Estimate the category counts, identify shared root causes, determine whether the
job was ever green using public CI history where possible, and recommend the
highest-value next fix pass. Re-verify the README's actual Windows contract; do
not assume the brief's tmux characterization is current. Clearly report
retrieval limitations and distinguish source-backed facts from inference.

## Scope note -- questions, examples, and named references are a starting point, not a checklist
The questions, topics, and named examples below are illustrative anchors and a FLOOR for this
research -- not an exhaustive list to answer only or evaluate only. Reason independently: survey the
landscape broadly, follow the evidence where it leads, expand scope where warranted, and surface
relevant work, factors, and failure modes not named here. Actively resist answering only the listed
questions or evaluating only the named approaches -- an output that merely fills in the listed items
has NOT met the research goal.

## Independent exploration (gaps, blindspots, emergent threads) -- required
Treat the question list as a FLOOR, not a ceiling. As you research, actively surface what this
framing may be missing and pursue each promising thread to a logical conclusion:
- Adjacent or upstream factors the questions don't capture.
- Contrarian / disconfirming evidence -- report it even when it challenges the premise.
- Emerging 2025-2026 practices, tools, or research not anchored by the named examples.
- Known failure modes and second-order effects.
Whenever a load-bearing thread surfaces mid-research, follow it to its conclusion and report it in a
dedicated "Gaps, blindspots & emergent findings" subsection. Explicitly NAME any blindspot you
suspect but cannot resolve (and why) rather than omitting it. Anchor bias -- over-fitting to the
listed questions and example approaches -- is a known failure mode; counter it deliberately and say
where you did.

<grounding_instructions>
You are a senior Python portability and CI engineer with production experience
maintaining cross-platform command-line tools. You separate failures in product
behavior from failures in test modeling, and you state evidence gaps explicitly.

Temporal scope: Weight sources by recency -- 2026 (primary) -> 2025 -> 2024.
Pre-2024 sources are background context only unless foundational to the topic.
If post-2024 literature is genuinely sparse for a subtopic, state
"[subtopic]: no significant post-2024 developments found" rather than
backfilling with older sources. Backfilling is a failure mode, not a hedge.

Before generating your final output, execute a Chain-of-Verification (CoVe)
to ensure factual fidelity over compliance.

Inside your thought process:
1. Isolate the core facts required.
2. Draft a tentative response.
3. Hostile Cross-Examination: flag any claim where you are citing a source because
   the prompt implied you should, rather than because you verified it.
4. Strip away any claim that cannot be empirically verified.

When generating your final output, classify every major claim. Write your rationale
before appending the tag -- writing the tag first causes post-hoc rationalization.
Rationale -> evidence check -> tag.

- [VERIFIABLE]: backed by documentation, peer-reviewed research, or official
  tech blogs (2024-2026). Carry an inline footnote ref to the source: [VERIFIABLE][^N].
- [HEURISTIC]: widely accepted best practice without a specific citation.
- [INFERENCE]: a logical conclusion drawn from context. Provide your reasoning
  in-text. Do not fabricate a source. Tier tag only -- NO footnote ref.
- [NO SOURCE]: explicitly state when you cannot find verifiable data. Tier tag
  only -- NO footnote ref.

Citation format (mandatory for every externally-sourced claim):
- Inline: append the GFM footnote ref directly after the tier tag -- [VERIFIABLE][^N].
  A claim citing multiple sources carries ascending separate refs -- [VERIFIABLE][^3][^7]
  (never grouped [^3,^7]; never out of order).
- Footnote definitions live once, under ## Sources, in APA form with a clickable
  URL or DOI and an access-verification stamp.
- URL-or-DOI ALWAYS: every source entry carries a clickable URL or a doi.org link --
  paywalled/gated is fine. Only a truly irreducible source gets an explicit
  [no online source located] marker with a one-line justification.
- Integrity: footnote refs are contiguous from [^1], every [^N] ref has a matching
  definition, and every definition is referenced -- no gaps, no orphans.
- [INFERENCE] / [NO SOURCE] claims carry the tier tag with NO footnote ref.

Hard constraint (overrides all formatting preferences): never invent a citation
to satisfy a formatting instruction. Accuracy > completeness.

Format diagrams using Mermaid.js or ASCII. Format math using LaTeX.
NEVER generate binary images.
</grounding_instructions>
```

<!-- /doc:region name="appendix_research_prompt" -->

<!-- doc:region name="appendix_provenance" kind="replaceable" -->

## Appendix: Provenance Ledger

(none -- manual in-session research; no automated provenance sidecar was generated)

<!-- /doc:region name="appendix_provenance" -->

<!-- doc:region name="run_history" kind="append_only" -->

## Run History

- 2026-08-15: Manual scoped research pass. Inspected current repository source,
  historical commits, and public GitHub Actions pages; authenticated raw logs were
  unavailable. Wrote and validated the designated research artifact only.

<!-- /doc:region name="run_history" -->
