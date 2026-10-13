"""A test run must not hand the runner's direnv state to the shells its tests start.

A suite launched from inside a direnv-loaded checkout carries ``DIRENV_DIFF`` and its siblings.
A real session script started by a test then runs ``direnv export`` from its temporary worktree,
which unloads the developer's checkout and restores the PATH saved before it: the test's stand-in
binaries vanish and the real agent CLI starts instead of the fake one.

**Why this drives a real inner pytest run.** Asserting ``DIRENV_DIFF not in os.environ`` from an
ordinary test passes whether or not the scrub exists whenever the runner happens to have no
direnv loaded, which is the normal CI case, so it could not fail. Here the outer test sets the
variable in the inner run's environment itself, so the inner test sees it unless the conftest
strips it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_INNER = "AI_CLI_DIRENV_ISOLATION_INNER"


@pytest.mark.skipif(not os.environ.get(_INNER), reason="only runs inside the outer test's pytest")
def test_inner_given_a_runner_with_direnv_loaded_when_a_test_runs_then_no_direnv_var_is_visible():
    leaked = sorted(name for name in os.environ if name.startswith("DIRENV_"))
    assert leaked == []


def test_given_a_runner_with_direnv_loaded_when_the_suite_runs_then_tests_do_not_inherit_it():
    env = {
        **os.environ,
        _INNER: "1",
        "DIRENV_DIFF": "eJzKSM3JyVcozy_KSQEEAAD__w",
        "DIRENV_DIR": "-/path/to/myproject",
        "DIRENV_WATCHES": "eJzKSM3JyVcozy_KSQEEAAD__w",
    }
    this_file = Path(__file__).resolve()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            f"{this_file}::test_inner_given_a_runner_with_direnv_loaded_when_a_test_runs_then_no_direnv_var_is_visible",
            "-q",
            "-p",
            "no:cacheprovider",
            "-n",
            "0",
        ],
        cwd=this_file.parent.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert "1 passed" in result.stdout
