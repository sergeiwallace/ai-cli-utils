---
title: "A remote launch fails with \"remote session-name allocation failed\" and no detail"
category: bug
tags: [bug, launch, remote, ssh, naming]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# A remote launch fails with "remote session-name allocation failed" and no detail

**Status:** resolved

**Severity:** P1

**Created:** 2026-11-19

**Task:** AI-CLI-9zm2e

## Table of Contents

- [Symptoms](#symptoms)
- [Environment](#environment)
- [Reproduction Steps](#reproduction-steps)
- [Root Cause Analysis](#root-cause-analysis)
- [Prior Fix Attempts](#prior-fix-attempts)
- [Fix](#fix)
- [Verification](#verification)
- [Lessons Learned](#lessons-learned)

## Symptoms

`ai c NAME -R` stopped at the allocation step about 12 s after it started:

```text
[launch] session: allocating remote session
[launch] session: still allocating remote session (10s elapsed)
[launch] session: Error: remote session-name allocation failed
```

The next attempt was killed on the launching machine (`zsh: killed`). The attempt
after that launched normally.

## Environment

A managed macOS laptop launching onto a Linux host over ssh with a shared
ControlMaster. The remote step before allocation had just rebuilt the remote
install.

## Reproduction Steps

1. Make the allocator's ssh call exit non-zero with empty stdout and stderr, for
   example by killing the local ssh client while it waits.
2. The launch fails with the message above and no exit status.

## Root Cause Analysis

`_request_remote_session_allocation` in `src/ai_cli/main.py` built its error only from
the call's output. When the output was empty the message carried nothing, not even the
exit status.

The remote host shows why the output was empty. Every error path in
`ai internal allocate-session-name` reports through the launch reporter, and the remote
telemetry database recorded no error or warning between 17:08:39 and 17:12:16 local time.
The kernel log shows no OOM kill. So the allocator never refused the request; the call was
ended from outside before it answered. A signal-killed ssh client or remote command leaves
exactly this result: a non-zero status and no output. On the launching side the very next
`ai c` was SIGKILLed outright, which points to an external kill there. The old message
dropped the status, so which side was killed cannot be shown for the failed call itself.

The remote config's unknown `pair_host` key, which broke `ai sessions list` that day, is
not the cause: it was still present when the third attempt allocated successfully.

## Prior Fix Attempts

None.

## Fix

- The error names the exit code, plus the signal when one ended the call: a negative status
  for the local ssh client, 128+N for the remote command, and 255 when ssh could not reach the
  host. Empty output reads `no output`.
- A call cut off by SIGKILL or SIGTERM with no output is retried once after a one-second
  pause, with a warning. SIGINT and every other failure still fail at once.

## Verification

The tests in `tests/test_remote.py` under "a remote allocation that ends with no output" drive
`ai c chief-1 -R` through `cli()`. All 7 fail on the base commit and pass with the fix.

## Lessons Learned

An error built from a subprocess's output alone is empty exactly when the process was
killed, the case that most needs a cause. Report the exit status every time.

<!-- /doc:region -->
