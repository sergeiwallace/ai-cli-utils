- `ai sessions relaunch --boot` replays only a session that was running when the host went down.
  It no longer replays a crash exit recorded before the boot, a reservation an earlier boot made
  that never started, or a row an earlier boot's relaunch kept down. A run from an earlier boot
  it does not replay is recorded as exited once tmux and the process table show it gone, so a
  later boot cannot replay it. `ai sessions mark <name> exited` now settles a row whose
  supervisor died with the host, which it refused as a stale transition sequence because the
  monotonic clock restarts at boot.
