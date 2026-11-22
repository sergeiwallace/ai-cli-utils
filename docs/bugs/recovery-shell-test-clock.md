---
title: "The remote recovery-shell test could exercise automatic restart"
category: bugs
tags: [tests, tmux, macos]
status: fixed
template_version: "bug-1.0.0"
---

# The remote recovery-shell test could exercise automatic restart

## Symptoms

On busy macOS CI runners, the test that exits a remote recovery shell timed out
waiting for the recovery banner. Its pane capture showed host setup and a template
refresh diagnostic. The original caller shell was still attached to the session.

## Reproduction

On macOS, run the generated remote wrapper in a real tmux pane with a stand-in
agent that waits for an exit request and returns 1. After the agent starts,
advance the test clock by five seconds and request the exit. The wrapper enters
the restart path, logs the refresh diagnostic, and launches a second agent.
Repeat with the clock fixed at launch time. The recovery prompt appears, and
typing `exit` closes the managed session after a single agent launch.

## Root Cause Analysis

The test relied on starting its caller, attaching to the pane, and sending the
agent's exit request in under three seconds. At that boundary the wrapper treats
a nonzero exit as eligible for restart. The test's stub reports no installed
template version, so the restart path also tries to refresh the template. The
host and refresh messages in the assertion were captured pane output after the
test took that path.

## Fix

The existing end-to-end regression now uses the fixture's fixed clock for the
quick-failure scenario. It waits for the recovery prompt after checking the
banner, then verifies that `exit` returns control to the same calling shell and
that the agent launched only once.

## Parallel runs on macOS

The clock control fixes which path the recovery test exercises. The default
parallel run still had six failures among 85 interrupt tests in 471.22 seconds,
including agent startup timeouts and Codex launch confirmation refusals. The
two reported recovery and foreground-promotion cases passed all five runs
with `-n 0` on a busy Mac. The supervisor compares the interrupt count during
promotion with the count it recorded before spawning the child.

On macOS, pytest now schedules tests marked `real_tmux` or `real_pty` on the
same xdist worker within each CI job. A scheduling regression test checks
worker assignment with and without CI sharding and verifies that sharding
still selects tests by their original node IDs.

## Verification

The clock-advance and fixed-clock cases were verified using a real macOS tmux
pane and the generated wrapper. The focused pair subsequently ran on a macOS
host with process inspection available.

After grouping the terminal tests, three consecutive runs of
`uv run --no-sync pytest tests/test_session_interrupt_shell.py -q` passed all
85 cases per run in 1008.27, 961.00, and 997.43 seconds.
