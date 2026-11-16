- The test suite now bounds every test at 300 seconds by default (thread method, so it works on
  Windows too), so one stuck test fails with every thread's stack instead of holding a whole run
  for hours. The bare-pty launch tests also end their stand-in agent when a drive gives up before
  the agent has started, which had left a run waiting for over three hours on a loaded host.
