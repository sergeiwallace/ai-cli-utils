---
title: "Mosh hides a remote failure written outside the launch log"
category: bug
tags: [bug, sessions, mosh]
status: resolved
template_version: "bug-1.0.0"
---

<!-- doc:region name="summary" kind="replaceable" -->

# Mosh hides a remote failure written outside the launch log

## Symptoms

A remote refusal can disappear when mosh restores the terminal. The client can
then retry the refused command over SSH or suggest a session-name collision.

## Reproduction Steps

Run the remote shell wrapper with a command that writes a migration refusal to
stderr before creating a launch log and exits 78. Exercise the client transport
loop at one second and twelve seconds. The regression uses a real shell and
fixture files for the remote diagnostic read.

## Root Cause Analysis

The mosh wrapper depended on the remote launcher copying stderr into its own
diagnostic sink. Output before that sink exists never reaches the file. The
early transport failure branch also consumed the status file without using it.
A terminal replay alone cannot restore a line once the remote process is gone.

## Fix

The wrapper copies remote stderr to an owner-only diagnostic file while displaying it
live. It keeps stdout on the terminal and records the remote command's exit
status separately. The client reads that result before classifying a short
transport exit.

## Verification

`test_given_remote_stderr_before_a_launch_log_exists_when_mosh_ends_then_the_refusal_and_status_survive`
failed at both timings because the client output lacked the refusal and retried
over SSH. It now checks that the refusal and exact finish command are visible,
that the client exits 78, and that the collision hint is absent.

## Lessons Learned

Test stderr recovery with a process that writes straight to stderr, and test
exits under both of the short durations the transport treats specially.

<!-- /doc:region name="summary" -->

<!-- doc:region name="reproduction" kind="replaceable" -->

<!-- /doc:region name="reproduction" -->

<!-- doc:region name="root_cause" kind="replaceable" -->

<!-- /doc:region name="root_cause" -->

<!-- doc:region name="fix_log" kind="append_only" -->

<!-- /doc:region name="fix_log" -->

<!-- doc:region name="appendix_evidence" kind="immutable" -->

<!-- /doc:region name="appendix_evidence" -->
