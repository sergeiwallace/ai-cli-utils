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

The fix changes one existing internal entry point and reuses an existing resolver. It adds
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

## Verification

- RED on base `7b9a456395b58fdc87e1cb69df5565c0afb7e550`:
  `test_given_stale_remote_host_when_launcher_allocates_then_fresh_login_host_names_session`
  observed `('c-r-myrepo-4', 'myrepo-4')` instead of
  `('c-r-myrepo-box-4', 'myrepo-box-4')`.
- Focused regression: 3 passed, covering login-shell priority, tmux fallback, and no-current-id
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

### 2026-10-24 — stale remote identity fixed

Role: `implement-network`, medium effort. The defect crossed the launcher/remote process
boundary, but the confirmed fix remained within the existing host-resolution component.

<!-- /doc:region name="fix_log" -->
