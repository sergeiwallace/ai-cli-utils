---
title: "Remote allocation trusts an inherited stale host identity"
category: bug
tags: [session, remote, naming, host-identity]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Remote allocation trusts an inherited stale host identity

**Status:** resolved

**Severity:** P1 — a host rename could prevent a remote session from launching under
the current naming convention.

**Created:** 2026-10-24

## Symptoms

A remote `ai c 4 -R` launch reached `ai internal allocate-session-name` with
`AI_HOST=old-host`, while the host-alias registry named only `new-host`. Allocation
either failed with an error naming `old-host`, or used the configured legacy session-name
form even though the current host had a valid alias.

The failure happened before the session or worktree was created. Restart behavior inside
an existing session was unaffected.

## Environment

- The launching machine dispatches over SSH.
- The remote command runs through a non-interactive login shell.
- A long-lived parent environment still exports `AI_HOST=old-host` after the machine was
  renamed to `new-host`.
- The alias registry contains `new-host = "box"` and no `old-host` entry.

## Reproduction Steps

1. Set the remote process environment to `AI_HOST=old-host`.
2. Configure the remote login profile to export `AI_HOST=new-host`.
3. Configure the host-alias registry with only `new-host = "box"`.
4. Ask the launcher to allocate slot 4 through its SSH command construction.
5. Before the fix, observe `myrepo-4`; expected `myrepo-box-4`.

The frozen regression drives `_request_remote_session_allocation`, parses the SSH stub's
real remote command, and sends it through the real `ai internal allocate-session-name`
entry. Only SSH, the login-shell probe, tmux, and the session-list query are stubbed.

## Root Cause Analysis

The remote allocator runs before the generated agent wrapper. The wrapper already refreshed
`AI_HOST` on every agent launch through a bounded source order, but allocation resolved the
host alias immediately from its inherited environment. A login shell can inherit a value
that predates a machine rename, especially when its profile sets `AI_HOST` only when absent.
The later wrapper refresh therefore could not help: name allocation had already failed.

Causal chain:

```text
long-lived remote parent exports old-host
  -> non-interactive login shell inherits old-host
  -> internal allocator resolves the alias before host refresh
  -> the registry has only new-host
  -> allocation fails or selects the configured legacy form
```

Rejected hypotheses:

- The launcher does not export its own `AI_HOST` into the remote command.
- `session_naming.host_id()` calls `detect_machine_profile()`; a persisted
  `[machine] host_id` does not override the environment on this read path.
- A stale tmux value is not necessarily causal. A fresh login-shell result wins; global
  tmux is consulted only when the login-shell result is absent or unusable.

## Scope Decision

The original fix changed one internal entry point; the follow-up moves refresh into the
shared alias resolver and applies the existing naming policy to chief launches. It adds
no public API or cross-repository boundary and is reversible. The narrow causal fix is
therefore appropriate; no redesign threshold is met.

## Fix

Before resolving the alias, `ai internal allocate-session-name` now uses the same bounded
identity source order as an agent launch:

1. a fresh login shell run without inherited `AI_HOST`;
2. the global tmux environment when the login result is unusable;
3. the inherited value only when neither fresh source answers.

A fresh answer updates `AI_HOST` for allocation. When neither source answers, the existing
`[naming] legacy_convention` policy remains authoritative: `full` uses the old name form;
stricter settings refuse and name the inherited host id that was tried. Both probes use the
configured timeout and do not loop.

## Entry-point follow-up

The allocator-only fix left shell launches exposed: `ai cos` directly built an alias prefix,
ignoring `[naming]`, and every other direct alias lookup still trusted inherited identity.
The same defect affects ordinary engine launches, migration targets, audit labels and name
commands. The production fix belongs at alias resolution, with one `refresh_host_env`
helper updating `AI_HOST` only after a fresh answer. Name-command ownership uses that same
helper before reading the machine id, and migration ownership is read after resolving the
refreshed target stem. Chief launches use `resolve_launch_naming`, including its legacy
prefix and host-alias fields; a legacy title therefore never invents a host segment.

The underlying `resolve_host_env` source order and timeouts are unchanged. Existing-name
inspection remains independent of launch policy: audit reads titles, explicit migrations
need the alias target, and auto-migration can describe a manual target even when disabled.
Replay, automatic restart and attach retain their recorded identities. The entry-point
inventory also exposed a reconnect parser that stripped remote titles to the last segment
and lost the project when host aliases or labels were present. Reconnect now preserves the
full remote name and selects the longest registered project prefix, without resolving an
alias for the local machine. Three additional frozen regressions fail on the exported base
and pass after this independent correction.

The focused regression module drives real CLI parsing over temporary alias registries,
fake login-shell/tmux executables, temporary repositories and client stores. On base
`1ecbbc17`, its 17 cases fail because engine plans use `myrepo-1`, chief/name/migration
commands reject `old-host`, and audit omits `label=review`. Two initial fixture errors were
corrected after restoring unfixed production source, then all 17 cases were re-confirmed
RED and frozen again before restoring the fix. Regressions assert the four config modes,
legacy fallback, actionable single refusal, refreshed ownership, and no chief registration
on a dry run. No naming resolver is mocked in these cases.

## Verification

Shared-entry-point follow-up on base `1ecbbc17`:

- Frozen naming and reconnect regressions: `20 passed in 0.35s`.
- Focused naming, remote, chief, migration, audit and relaunch gate:
  `544 passed in 6.76s`.
- `uv run ruff check src/ tests/`: `All checks passed!`.
- `uv run ruff format --check src/ tests/`: `298 files already formatted`.
- `uv run pyright src/`: `0 errors, 0 warnings, 0 informations`.
- `uv run pytest -q --tb=short`:
  `6194 passed, 21 skipped, 11 warnings in 218.17s (0:03:38)`.
- Installed-style `uv run --no-sync ai cos 1 --dry-run` against the real source tree,
  using temporary config, a seeded temporary chief home and fake shell/tmux executables,
  plans `myrepo-box-chief-1` from a retired inherited host id without creating anything.
- Documentation validation passes for this bug record, the usage guide and changelog.

Original allocator-only verification:

- RED on base `7b9a456395b58fdc87e1cb69df5565c0afb7e550`:
  `test_given_stale_remote_host_when_launcher_allocates_then_fresh_login_host_names_session`
  observed `('c-r-myrepo-4', 'myrepo-4')` instead of
  `('c-r-myrepo-box-4', 'myrepo-box-4')`.
- Original focused regression: 3 passed, covering login-shell priority, tmux fallback, and no-current-id
  naming policy.
- Related suite: 133 passed across remote launch, host refresh, naming, client ownership,
  and internal-output coverage.
- Ruff check: passed. Ruff format check: 304 files already formatted.
- Pyright: 0 errors, 0 warnings, 0 informations.
- Full pytest: 6155 passed, 21 skipped, 12 warnings in 199.58 seconds.

The warnings are dependency deprecations and existing threaded process-fork warnings outside
the changed allocation path.

## Lessons Learned

- A login shell is not automatically a fresh identity source when it inherits the variable
  being detected.
- Identity must be refreshed before the first irreversible or naming-dependent decision, not
  only before the eventual child process starts.
- Cross-machine tests should execute both the launcher's constructed command and the remote
  entry point; testing either side alone misses environment propagation defects.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

### 2026-10-24 — shared naming entry points fixed

Role: `implement-network`, high effort, for the cross-entry-point identity and policy audit.
Frozen regressions: `uv run pytest tests/test_naming_entry_points.py -n 0 -q --tb=short`:
`17 passed in 0.38s`. Related naming, remote, migration, audit, chief and relaunch gate:
`541 passed in 3.76s`. Tests explicitly cover replay and non-interactive launches keeping
legacy slots unchanged. The original entry-point-only hypothesis was rejected by the
additional stale-host regressions; refresh belongs in shared alias resolution.

### 2026-10-24 — reconnect and public-comment follow-ups

The full gate on the shared naming fix reported `2 failed, 6189 passed, 21 skipped,
13 warnings in 200.78s (0:03:20)`. Both failures were existing public-package hygiene guards
flagging a private machine name and tracking reference in a browser-test comment. An
exported `1ecbbc17` snapshot reproduced both failures, alongside three new RED reconnect
cases: `5 failed, 39 deselected in 0.40s`. The comment was scrubbed independently;
`uv run pytest tests/test_public_repo_hygiene.py -n 0 -q --tb=short` then reported
`41 passed in 1.58s`. Frozen naming and reconnect regressions together reported
`20 passed in 0.35s` after the reconnect correction.

### 2026-10-24 — stale remote identity fixed

Role: `implement-network`, medium effort. The defect crossed the launcher/remote process
boundary, but the confirmed fix remained within the existing host-resolution component.

<!-- /doc:region name="fix_log" -->
