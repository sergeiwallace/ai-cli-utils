"""A test run must not hand the runner's own launch reason to the session scripts its tests start.

A suite launched from inside a relaunched session carries that session's ``AIH_LAUNCH_REASON``.
A generated session script reads it to decide that its very first agent start is a restart, so
a fixture supervisor started by a test resumed a transcript on its first launch, or refused to
launch at all, where the test expected a fresh start.

**Why this drives a real inner pytest run.** CI never sets the variable, so an ordinary test
passes whether or not the conftest strips it. Here the outer test sets it in the inner run's
environment itself and runs a supervisor-restart test that depends on a fresh first launch.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_cli.iterm2_restore import LAUNCH_REASON_VAR

_RESTART_TEST = (
    "test_session_script_restart_finish.py"
    "::test_given_a_recorded_bg_transcript_when_the_supervisor_restarts_then_passes_its_uuid"
)


@pytest.mark.skipif(sys.platform == "win32", reason="the inner restart test drives a POSIX supervisor")
def test_given_a_runner_with_a_launch_reason_when_a_supervisor_test_runs_then_its_first_launch_is_fresh():
    tests_dir = Path(__file__).resolve().parent
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(tests_dir / _RESTART_TEST),
            "-q",
            "-p",
            "no:cacheprovider",
            "-n",
            "0",
        ],
        cwd=tests_dir.parent,
        env={**os.environ, LAUNCH_REASON_VAR: "host_reboot"},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    assert "1 passed" in result.stdout
