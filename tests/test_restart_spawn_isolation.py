"""Restart test mocks must not restore expired per-test spawn guards."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "target",
    [
        "test_session_restart.py::test_given_no_request_when_resolving_then_ordinary_launch",
        "test_session_restart.py::test_given_self_when_restart_then_detached",
        "test_session_restart.py::test_given_remote_slot_when_restart_then_remote_resolution_only",
        "test_restart_supervisor.py::test_given_old_supervisor_when_restart_then_pinned_conversation",
    ],
)
def test_given_restart_mocks_when_tests_finish_then_spawn_functions_are_restored(tmp_path, target):
    # An outer hook observes the actual fixture teardown order, after both the
    # spawn guard and monkeypatch have finished. No live tmux server is needed.
    (tmp_path / "restart_spawn_probe.py").write_text(
        "import subprocess\n"
        "import pytest\n"
        "original_run = subprocess.run\n"
        "original_popen = subprocess.Popen\n"
        "@pytest.hookimpl(wrapper=True, tryfirst=True)\n"
        "def pytest_runtest_teardown(item, nextitem):\n"
        "    result = yield\n"
        "    assert subprocess.run is original_run, 'subprocess.run leaked across tests'\n"
        "    assert subprocess.Popen is original_popen, 'subprocess.Popen leaked across tests'\n"
        "    return result\n"
    )
    tests = Path(__file__).resolve().parent
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(tests / target),
            "-q",
            "-n",
            "0",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:randomly",
            "-p",
            "restart_spawn_probe",
        ],
        cwd=tests.parent,
        env={**os.environ, "PYTHONPATH": os.pathsep.join((str(tmp_path), os.environ.get("PYTHONPATH", "")))},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-2000:]
