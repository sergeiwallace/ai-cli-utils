"""Circus process management: the stale-session reaper watcher lifecycle."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from process_wait import scaled

from ai_cli import process_manager


@pytest.fixture
def posix_reaper_platform():
    """Run the Circus start path on every OS; Circus itself is mocked in these tests."""
    with patch("ai_cli.process_manager._native_windows", return_value=False):
        yield


@pytest.mark.usefixtures("posix_reaper_platform")
def test_given_reaper_start_when_circus_is_available_then_registers_one_60_second_watcher(tmp_path):
    client = MagicMock()
    with (
        patch("ai_cli.process_manager._ensure_circusd", return_value=f"ipc://{tmp_path}/circus.endpoint"),
        patch("ai_cli.process_manager.get_xdg_state_home", return_value=tmp_path),
        patch("circus.client.CircusClient", return_value=client),
        patch("ai_cli.process_manager.shutil.which", return_value="/usr/bin/ai"),
    ):
        assert process_manager._cmd_stale_session_reaper_start()

    add_calls = [call for call in client.send_message.call_args_list if call.args[:1] == ("add",)]
    assert len(add_calls) == 1
    assert add_calls[0].kwargs["name"] == "stale-session-reaper"
    assert add_calls[0].kwargs["cmd"] == "/usr/bin/ai session-reaper run"
    assert add_calls[0].kwargs["options"]["singleton"] is True
    assert add_calls[0].kwargs["start"] is True


@pytest.mark.usefixtures("posix_reaper_platform")
def test_given_reaper_start_when_circus_registration_fails_then_reports_failure_without_running_reaper(capsys):
    with patch("ai_cli.process_manager._ensure_circusd", side_effect=RuntimeError("unavailable")):
        assert not process_manager._cmd_stale_session_reaper_start()

    error = capsys.readouterr().err
    assert "failed to start" in error
    assert "ai session-reaper start" in error


@pytest.mark.usefixtures("posix_reaper_platform")
def test_given_running_reaper_when_start_requested_then_existing_watcher_is_left_running(tmp_path):
    client = MagicMock()
    client.send_message.return_value = {"statuses": {"stale-session-reaper": {"active": 1}}}
    with (
        patch("ai_cli.process_manager._ensure_circusd", return_value=f"ipc://{tmp_path}/circus.endpoint"),
        patch("ai_cli.process_manager.get_xdg_state_home", return_value=tmp_path),
        patch("circus.client.CircusClient", return_value=client),
    ):
        assert process_manager._cmd_stale_session_reaper_start()

    client.send_message.assert_called_once_with("status")


@pytest.mark.usefixtures("posix_reaper_platform")
def test_given_two_near_simultaneous_starts_when_reaper_is_missing_then_only_one_watcher_is_added(tmp_path):
    class ConcurrentCircusClient:
        state_lock = threading.Lock()
        active = False
        add_count = 0

        def __init__(self, **_kwargs):
            pass

        def send_message(self, command, **_kwargs):
            with self.state_lock:
                if command == "status":
                    return {"statuses": {"stale-session-reaper": {"active": 1}} if type(self).active else {}}
                if command == "add":
                    type(self).active = True
                    type(self).add_count += 1
                    return {"status": "ok"}
                if command == "rm":
                    type(self).active = False
                    return {"status": "ok"}
                raise AssertionError(f"unexpected Circus command: {command}")

    results = []
    with (
        patch("ai_cli.process_manager._ensure_circusd", return_value=f"ipc://{tmp_path}/circus.endpoint"),
        patch("ai_cli.process_manager.get_xdg_state_home", return_value=tmp_path),
        patch("circus.client.CircusClient", ConcurrentCircusClient),
        patch("ai_cli.process_manager.shutil.which", return_value="/usr/bin/ai"),
    ):
        threads = [
            threading.Thread(target=lambda: results.append(process_manager._cmd_stale_session_reaper_start()))
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert results == [True, True]
    assert ConcurrentCircusClient.add_count == 1


@pytest.mark.real_reaper_spawn
def test_given_claude_launch_when_reaper_start_is_scheduled_then_launch_does_not_wait_for_it():
    with (
        patch("ai_cli.process_manager.sys.platform", "linux"),
        patch("ai_cli.process_manager.shutil.which", return_value="/usr/bin/ai"),
        patch("ai_cli.process_manager.subprocess.Popen") as popen,
    ):
        process_manager._start_stale_session_reaper_for_launch()

    popen.assert_called_once_with(
        ["/usr/bin/ai", "session-reaper", "start"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        start_new_session=True,
    )


_REPLACING_THEN_REAL_REAPER_TESTS = """
from unittest.mock import Mock

import pytest

from ai_cli import process_manager

REAL_START = process_manager._start_stale_session_reaper_for_launch


def test_replaces_the_reaper_start(monkeypatch):
    monkeypatch.setattr(process_manager, "_start_stale_session_reaper_for_launch", Mock())


@pytest.mark.real_reaper_spawn
def test_then_sees_the_real_reaper_start():
    assert process_manager._start_stale_session_reaper_for_launch is REAL_START
"""


def test_given_a_test_monkeypatching_the_reaper_start_when_a_real_reaper_test_runs_next_then_it_gets_the_real_function(
    tmp_path,
):
    """The suite's reaper no-op must not outlive the test that overrode it on one worker.

    Run as a real pytest session, in a fixed order, with the suite's own conftest loaded as a
    plugin: the order xdist and random ordering only reach some of the time.
    """
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_reaper_order.py").write_text(_REPLACING_THEN_REAL_REAPER_TESTS, encoding="utf-8")
    tests_dir = Path(__file__).resolve().parent
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(tests_dir), str(tests_dir.parent / "src"), env.get("PYTHONPATH")])
    )

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "no:randomly", "-p", "conftest", "-q"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=scaled(120),
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout, result.stdout


@pytest.mark.real_reaper_spawn
def test_given_native_windows_launch_when_reaper_start_is_scheduled_then_it_is_a_noop():
    with (
        patch("ai_cli.process_manager._native_windows", return_value=True),
        patch("ai_cli.process_manager.subprocess.Popen") as popen,
    ):
        process_manager._start_stale_session_reaper_for_launch()

    popen.assert_not_called()


def test_given_reaper_stop_when_circus_is_unavailable_then_reports_failure_without_starting_it(tmp_path, capsys):
    with (
        patch("ai_cli.process_manager.get_xdg_state_home", return_value=tmp_path),
        patch("ai_cli.process_manager._ensure_circusd") as ensure,
        patch("circus.client.CircusClient", side_effect=RuntimeError("unavailable")),
    ):
        assert not process_manager._cmd_stale_session_reaper_stop()

    ensure.assert_not_called()
    assert "failed to stop" in capsys.readouterr().err


def test_given_reaper_stop_when_circus_removes_watcher_then_reports_success(tmp_path, capsys):
    client = MagicMock()
    with (
        patch("ai_cli.process_manager.get_xdg_state_home", return_value=tmp_path),
        patch("circus.client.CircusClient", return_value=client),
    ):
        assert process_manager._cmd_stale_session_reaper_stop()

    client.send_message.assert_called_once_with("rm", name="stale-session-reaper")
    assert capsys.readouterr().out.strip() == "[daemon] stop: stale-session-reaper: stopped"


def test_given_reaper_stop_when_circus_rejects_removal_then_reports_failure(tmp_path, capsys):
    client = MagicMock()
    client.send_message.return_value = {"status": "error"}
    with (
        patch("ai_cli.process_manager.get_xdg_state_home", return_value=tmp_path),
        patch("circus.client.CircusClient", return_value=client),
    ):
        assert not process_manager._cmd_stale_session_reaper_stop()

    assert "failed to stop" in capsys.readouterr().err


def test_given_reaper_status_when_watcher_is_running_then_reports_running(tmp_path, capsys):
    client = MagicMock()
    client.send_message.return_value = {"statuses": {"stale-session-reaper": {"active": 1}}}
    with (
        patch("ai_cli.process_manager.get_xdg_state_home", return_value=tmp_path),
        patch("circus.client.CircusClient", return_value=client),
    ):
        assert process_manager._cmd_stale_session_reaper_status()

    assert capsys.readouterr().out.strip() == "[daemon] status: stale-session-reaper: running"


def test_given_reaper_status_when_watcher_is_not_registered_then_reports_not_running(tmp_path, capsys):
    client = MagicMock()
    client.send_message.return_value = {"statuses": {}}
    with (
        patch("ai_cli.process_manager.get_xdg_state_home", return_value=tmp_path),
        patch("circus.client.CircusClient", return_value=client),
    ):
        assert process_manager._cmd_stale_session_reaper_status()

    assert capsys.readouterr().out.strip() == "[daemon] status: stale-session-reaper: not running"


def test_given_reaper_status_when_circus_is_unavailable_then_reports_failure_not_healthy(tmp_path, capsys):
    with (
        patch("ai_cli.process_manager.get_xdg_state_home", return_value=tmp_path),
        patch("circus.client.CircusClient", side_effect=RuntimeError("unavailable")),
    ):
        assert not process_manager._cmd_stale_session_reaper_status()

    output = capsys.readouterr()
    assert "failed to query status" in output.err
    assert "running" not in output.out


def test_given_reaper_status_when_circus_returns_no_watcher_state_then_reports_failure_not_healthy(tmp_path, capsys):
    client = MagicMock()
    client.send_message.return_value = {"status": "ok"}
    with (
        patch("ai_cli.process_manager.get_xdg_state_home", return_value=tmp_path),
        patch("circus.client.CircusClient", return_value=client),
    ):
        assert not process_manager._cmd_stale_session_reaper_status()

    output = capsys.readouterr()
    assert "failed to query status" in output.err
    assert "running" not in output.out
