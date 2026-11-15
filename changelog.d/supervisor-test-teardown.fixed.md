- The supervisor tests no longer leave processes running after a failed test. Teardown
  already killed a supervisor that was still running, but skipped one that had already
  exited, so its child kept running under init after the run ended. Teardown now kills
  every process group and session the supervisor's tree was seen in. The test child
  bodies now idle on a blocking read instead of starting a new `sleep` every 50 ms.
