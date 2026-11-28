"""Regression coverage for circusd test-spawn refusal, pidfile teardown, and session leak detection."""

from __future__ import annotations

import subprocess
import sys
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psutil
import pytest
from process_ownership import (
    exited_pid,
    managed_circusd,
    reap,
    spawn_owned,
    spawn_owned_sleeper,
    stop_circusd_by_pidfile,
)

from ai_cli import process_manager
from tests import conftest as suite_conftest

_PARENT_WITH_CHILD = (
    "import subprocess, sys, time\n"
    "kid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
    "print(kid.pid, flush=True)\n"
    "time.sleep(30)\n"
)


def _pid_is_gone(pid: int, timeout: float = 10.0) -> bool:
    """Return True once *pid* is no longer running (or is already a zombie)."""
    deadline = monotonic() + timeout
    while True:
        try:
            proc = psutil.Process(pid)
            if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        if monotonic() >= deadline:
            return False
        sleep(0.05)


def test_given_unmocked_ensure_circusd_when_called_then_spawn_guard_refuses_it(tmp_path):
    """Unmocked _ensure_circusd must fail loudly instead of daemonizing a real circusd."""
    state_dir = tmp_path / "xdg_state"
    with patch("ai_cli.process_manager.get_xdg_state_home", return_value=state_dir):
        with pytest.raises(RuntimeError, match=r"attempted to spawn a real `circusd(\.exe)?` process"):
            process_manager._ensure_circusd()


def test_given_live_daemon_and_child_when_stop_circusd_by_pidfile_runs_then_stops_both_and_removes_pidfile(
    tmp_path,
):
    """stop_circusd_by_pidfile must stop both the daemon PID and its spawned watcher children."""
    proc = spawn_owned([sys.executable, "-c", _PARENT_WITH_CHILD], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout is not None
        child_pid = int(proc.stdout.readline().strip())
        proc.stdout.close()
        assert psutil.pid_exists(child_pid)

        pidfile = tmp_path / "circusd.pid"
        pidfile.write_text(f"{proc.pid}\n", encoding="utf-8")

        assert stop_circusd_by_pidfile(pidfile) is True
        proc.wait(timeout=10)
        assert _pid_is_gone(proc.pid)
        assert _pid_is_gone(child_pid)
        assert not pidfile.exists()
    finally:
        reap(proc)


def test_given_missing_invalid_or_stale_pidfile_when_stop_circusd_by_pidfile_runs_then_returns_false(tmp_path):
    """Missing, malformed, or dead pidfiles must return False cleanly without raising."""
    missing = tmp_path / "missing.pid"
    assert stop_circusd_by_pidfile(missing) is False

    malformed = tmp_path / "bad.pid"
    malformed.write_text("not-a-pid", encoding="utf-8")
    assert stop_circusd_by_pidfile(malformed) is False

    stale = tmp_path / "stale.pid"
    stale.write_text(str(exited_pid()), encoding="utf-8")
    assert stop_circusd_by_pidfile(stale) is False
    assert not stale.exists()


def test_given_managed_circusd_context_when_body_raises_then_stops_process_by_pidfile(tmp_path):
    """managed_circusd must stop the pidfile's process even when the test body fails."""
    proc = spawn_owned_sleeper()
    pidfile = tmp_path / "circusd.pid"

    def _run_failing_body() -> None:
        with managed_circusd(pidfile):
            pidfile.write_text(str(proc.pid), encoding="utf-8")
            raise RuntimeError("boom")

    try:
        with pytest.raises(RuntimeError, match="boom"):
            _run_failing_body()
        proc.wait(timeout=10)
        assert _pid_is_gone(proc.pid)
        assert not pidfile.exists()
    finally:
        reap(proc)


def test_given_mixed_process_table_when_scanning_for_leaked_circusd_then_matches_only_session_roots(tmp_path):
    """The leak scanner matches pytest-owned circusd daemons while ignoring real and sibling daemons."""
    session_root = tmp_path / "ai-state-this-run"
    basetemp = tmp_path / "pytest-42"
    other_run = tmp_path / "pytest-99"
    user_state = tmp_path / "user-home" / ".local" / "state" / "ai-cli-utils"
    for directory in (session_root, basetemp, other_run, user_state):
        directory.mkdir(parents=True)

    fake_processes = [
        {
            "pid": 1001,
            "cmdline": [
                str(tmp_path / "venv" / "bin" / "python"),
                str(tmp_path / "venv" / "bin" / "circusd"),
                "--daemon",
                "--pidfile",
                str(session_root / "circusd.pid"),
                str(session_root / "circus.ini"),
            ],
        },
        {
            "pid": 1002,
            "cmdline": [
                str(tmp_path / "venv" / "bin" / "circusd"),
                "--daemon",
                f"--pidfile={basetemp / 'popen-gw0' / 'circusd.pid'}",
                str(basetemp / "popen-gw0" / "circus.ini"),
            ],
        },
        {
            "pid": 1003,
            "cmdline": [
                str(tmp_path / "venv" / "bin" / "python3"),
                "-m",
                "circus.circusd",
                str(session_root / "circus.ini"),
            ],
        },
        {
            "pid": 2001,
            "cmdline": [
                str(tmp_path / "venv" / "bin" / "circusd"),
                "--daemon",
                "--pidfile",
                str(user_state / "circusd.pid"),
                str(user_state / "circus.ini"),
            ],
        },
        {
            "pid": 2002,
            "cmdline": [
                str(tmp_path / "venv" / "bin" / "python"),
                str(tmp_path / "venv" / "bin" / "circusd"),
                "--daemon",
                "--pidfile",
                str(other_run / "circusd.pid"),
                str(other_run / "circus.ini"),
            ],
        },
        {
            "pid": 2003,
            "cmdline": [
                sys.executable,
                "-m",
                "pytest",
                str(session_root / "test_circusd_leak_guard.py"),
            ],
        },
    ]

    found = suite_conftest.find_surviving_pytest_circusd(
        (str(session_root), str(basetemp)),
        processes=fake_processes,
    )

    assert [pid for pid, _cmdline in found] == [1001, 1002, 1003]


def test_given_leaked_circusd_when_sessionfinish_runs_then_reaps_process_and_fails_session(monkeypatch):
    """pytest_sessionfinish must reap surviving pytest circusd processes and fail the session."""
    leaked = spawn_owned_sleeper()
    reporter = MagicMock()
    pluginmanager = MagicMock()
    pluginmanager.get_plugin.return_value = reporter
    session = SimpleNamespace(
        config=SimpleNamespace(pluginmanager=pluginmanager),
        exitstatus=pytest.ExitCode.OK,
    )
    fake_leaks = [(leaked.pid, ["circusd", "--daemon", "--pidfile", "/tmp/pytest-test/circusd.pid"])]
    monkeypatch.setattr(suite_conftest, "tmux_skip_regression_message", lambda _skips, _ok: None)
    monkeypatch.setattr(suite_conftest, "tmux_runnable", lambda: (True, ""))
    monkeypatch.setattr(suite_conftest, "find_surviving_pytest_circusd", lambda _roots: fake_leaks)

    try:
        suite_conftest.pytest_sessionfinish(session, pytest.ExitCode.OK)
        leaked.wait(timeout=10)
        assert _pid_is_gone(leaked.pid)
        assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
        reporter.write_sep.assert_called_once()
        reporter.write_line.assert_called_once()
        assert str(leaked.pid) in reporter.write_line.call_args.args[0]
    finally:
        reap(leaked)


def test_given_no_leaked_circusd_when_sessionfinish_runs_then_leaves_exitstatus_unchanged(monkeypatch):
    """When no pytest circusd survives, pytest_sessionfinish leaves exitstatus intact."""
    reporter = MagicMock()
    pluginmanager = MagicMock()
    pluginmanager.get_plugin.return_value = reporter
    session = SimpleNamespace(
        config=SimpleNamespace(pluginmanager=pluginmanager),
        exitstatus=pytest.ExitCode.OK,
    )
    monkeypatch.setattr(suite_conftest, "tmux_skip_regression_message", lambda _skips, _ok: None)
    monkeypatch.setattr(suite_conftest, "tmux_runnable", lambda: (True, ""))
    monkeypatch.setattr(suite_conftest, "find_surviving_pytest_circusd", lambda _roots: [])

    suite_conftest.pytest_sessionfinish(session, pytest.ExitCode.OK)

    assert session.exitstatus == pytest.ExitCode.OK
    reporter.write_sep.assert_not_called()
