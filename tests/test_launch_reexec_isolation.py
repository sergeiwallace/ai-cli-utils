"""A launch's restart-after-update marker never leaks from one test into the next.

The launcher writes ``AI_CLI_LAUNCH_REEXEC=1`` straight into ``os.environ`` before it
re-execs itself. A test that patches ``execvp`` keeps that write, and the next launch in
the same worker printed "Continuing ... session" where it should print "Starting".

**Why this drives a real inner pytest run.** The leak only shows across two tests in one
process, in a fixed order, which an ordinary test cannot arrange for itself: the outer test
runs the writer and then the reader in one serial inner session.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_INNER = "AI_CLI_REEXEC_ISOLATION_INNER"
_MARKER = "AI_CLI_LAUNCH_REEXEC"
_inner_only = pytest.mark.skipif(not os.environ.get(_INNER), reason="only runs inside the outer test's pytest")


@_inner_only
def test_inner_1_given_code_under_test_writes_the_marker_when_the_test_ends_then_nothing_restores_it_itself():
    os.environ[_MARKER] = "1"


@_inner_only
def test_inner_2_given_an_earlier_test_wrote_the_marker_when_the_next_test_starts_then_it_is_absent():
    assert _MARKER not in os.environ


def test_given_a_test_leaves_the_reexec_marker_when_the_next_test_runs_then_it_does_not_see_it():
    this_file = Path(__file__).resolve()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            f"{this_file}::test_inner_1_given_code_under_test_writes_the_marker_when_the_test_ends_then_nothing_restores_it_itself",
            f"{this_file}::test_inner_2_given_an_earlier_test_wrote_the_marker_when_the_next_test_starts_then_it_is_absent",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:randomly",
            "-n",
            "0",
        ],
        cwd=this_file.parent.parent,
        env={**os.environ, _INNER: "1"},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert "2 passed" in result.stdout
