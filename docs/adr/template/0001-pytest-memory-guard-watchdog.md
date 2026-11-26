# 0001. The pytest memory guard watches from a separate process

Governs: `pytest_memory_guard.py`, `tests/test_pytest_memory_guard.py`.

## Decision

The memory ceiling is enforced by a watchdog process that samples the test process's
resident memory and kills it when over the limit. It is not enforced with `RLIMIT_AS`.

## Why, and the constraints the code relies on

- `RLIMIT_AS` caps reserved address space, and children inherit it, so Go programs
  (gitleaks) and Chromium die reserving gigabytes they never touch. A separate process also
  stays schedulable when a runaway test monopolises the GIL. Rejected: `RLIMIT_AS`.
- The ceiling holds only once the watchdog has sampled the process. On a loaded host the
  watchdog's interpreter can take longer to start than the next test takes to run, so
  startup waits for the first sample instead of returning. The test that covers this delays
  only the watchdog's interpreter, which makes that race certain instead of occasional.
- A thread bounds that wait on every platform. Rejected: `select()`, which cannot poll a
  pipe on Windows.
- After an unconstrained test the guard waits for resident memory to fall below the limit
  before restarting the watchdog: macOS keeps a large freed allocation in the process's RSS
  for about two seconds, and a watchdog restarted at once would kill the process for memory
  already released. The tests reproduce this with an allocator that retains freed pages.
- The watchdog is the parent's direct child, so a changed parent pid means the parent has
  exited and its pid may already belong to another process; the probe then stops. On Linux
  a pidfd names the exact process, so a reused pid is never signalled.
- Linux reads `/proc` directly, so Linux repositories adopt the guard without psutil, which
  only macOS and Windows need.
- A failure to write the notice file must never stop the watchdog from enforcing the
  ceiling, so that write ignores `OSError`.
- The tests override only this plugin's platform decision and keep the host platform
  intact, because the helper is loaded in the controller and in every xdist worker.
