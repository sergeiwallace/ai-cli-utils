---
title: "Boot relaunch replays a session that was not running before the reboot"
category: bug
tags: [bug, sessions, boot-relaunch, reboot, run-state]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Boot relaunch replays a session that was not running before the reboot

## Founding Ask Coverage

The ask: a session that was not running before a reboot must not be relaunched by the boot
unit; a row whose supervisor died with the host must be able to leave `running`, by hand or at
boot; stale remote rows on the launcher host must never be replayed; and the fix must not set
any session's relaunch policy to `never`. All four are covered. Showing an earlier boot's
record distinctly in `ai sessions list` before the boot pass runs is a separate issue
(AI-CLI-o214).

**Status:** resolved

**Severity:** P1

**Created:** 2026-11-14

## Symptoms

After a reboot, the boot unit (`ai sessions relaunch --boot`) started a detached session that
the user did not believe had been running before the reboot. Another session's row still read
`running` after the reboot with no tmux session or process, and correcting it by hand failed:

```text
$ ai sessions mark c-myproject-1 exited -c host_reboot
session state persistence refused: stale supervisor transition sequence
```

## Reproduction Steps

1. Run a session, so its row reads `running` with this boot's id and a transition sequence
   taken from the monotonic clock.
2. Reboot. Keep it down at boot (for example policy `never`), so the boot pass replays nothing.
3. Run `ai sessions mark <name> exited -c host_reboot`: refused, stale sequence.
4. Change its policy to `auto` and reboot again: the boot pass replays it, although it was not
   running before the second reboot.

`tests/test_session_boot_relaunch.py` (the tests after `BOOTED_AT`) and
`tests/test_session_run_state.py` rebuild each step with a changed boot id and boot time in a
temporary state directory, on simulated Linux and macOS.

## Root Cause Analysis

1. The boot pass decided "was it running at the reboot" from the row alone: any row not
   `exited` whose boot id differs from this boot's counts as `host_reboot`. Reconcile leaves a
   row from an earlier boot unchanged on purpose, for the boot pass to judge, but the boot pass
   never recorded its own verdict on a row it kept down. Such a row stayed `running` for every
   later boot, and once its policy allowed replay (a policy repair, a default change, an owner's
   choice), the next boot replayed a session that had been down for one or more boots.
2. Two more row shapes passed the same check: an exit with cause `terminal_quit_or_crash` or
   `host_reboot` recorded before this boot started, and a `relaunched` reservation from an
   earlier boot whose launch never reached `running`.
3. `ai sessions mark` without a sequence ordered the transition by `time.monotonic_ns()`. That
   clock restarts at boot, so a supervisor that died with the host left a sequence larger than
   any reading taken after the reboot, and every manual mark was refused.

## Fix

- `session_run_state.not_running_at_reboot` vetoes the three shapes above, naming them as
  `... so it was not running when the host went down`. A maintenance wind-down recorded before
  the boot still replays: it stops a session for the reboot. A crash exit recorded during this
  boot still replays as before. The veto runs after the policy and cause checks, so their
  messages are unchanged.
- The boot pass records an exit for each earlier-boot run it does not replay, once tmux and the
  process table both show it gone: `host_reboot` when the reboot ended it, `unknown` when it was
  already down. The evidence keeps the previous exit, the veto and both boot ids. Remote rows,
  rows with a deny-boot safety file and rows whose liveness could not be established are left
  as they are.
- An unsequenced transition is ordered after the stored one (`max(monotonic, stored + 1)`),
  the same rule reconcile already used.

No session's relaunch policy is changed by any of this.

## Verification

Ten new tests failed before the fix and pass after it; five more guard the behaviour that must
survive (a running row replayed at the next boot, a same-boot crash, a wind-down before the
boot, a remote row neither replayed nor settled).

## Lessons Learned

- A clock that restarts at boot cannot order writes across a boot.
- A judge that leaves no record of its verdict makes the next judge repeat the question with
  less evidence.

<!-- /doc:region name="summary" -->
