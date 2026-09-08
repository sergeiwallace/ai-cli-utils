"""quota-watch auto-start config gate.

Root cause: `session_script.py`'s per-session `ai quota watch start` was
unconditional, so the notification-firing daemon silently re-registered on
every `ai c`/`ai g` launch and stayed running — redundant with the CC
statusline, which already surfaces weekly usage. `_cmd_quota_watch_start(auto=)`
now gates the *auto-start* path only on `[quota_watch] auto_start` (default
off); an explicit, user-typed `ai quota watch start` always registers.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ai_cli import process_manager


def test_auto_start_noop_when_config_disabled():
    with patch("ai_cli.config.load_config", return_value={"quota_watch": {"auto_start": False}}):
        with patch("ai_cli.process_manager._ensure_circusd") as mock_ensure:
            process_manager._cmd_quota_watch_start(auto=True)
    mock_ensure.assert_not_called()


def test_auto_start_noop_when_config_section_absent():
    # Default posture — no [quota_watch] section at all — must also no-op.
    with patch("ai_cli.config.load_config", return_value={}):
        with patch("ai_cli.process_manager._ensure_circusd") as mock_ensure:
            process_manager._cmd_quota_watch_start(auto=True)
    mock_ensure.assert_not_called()


def test_auto_start_registers_when_config_enabled(tmp_path):
    with patch("ai_cli.config.load_config", return_value={"quota_watch": {"auto_start": True}}):
        with patch(
            "ai_cli.process_manager._ensure_circusd",
            return_value=f"ipc://{tmp_path}/circus.endpoint",
        ) as mock_ensure:
            with patch("ai_cli.process_manager.get_xdg_state_home", return_value=tmp_path):
                with patch("circus.client.CircusClient") as mock_client_cls:
                    process_manager._cmd_quota_watch_start(auto=True)
    mock_ensure.assert_called_once()
    add_calls = [c for c in mock_client_cls.return_value.send_message.call_args_list if c.args[:1] == ("add",)]
    assert len(add_calls) == 1
    assert add_calls[0].kwargs["name"] == "quota-watch"
    assert add_calls[0].kwargs["start"] is True


def test_explicit_start_ignores_config_even_when_disabled(tmp_path):
    # `auto=False` (a bare, explicitly-typed `ai quota watch start`) must always
    # register regardless of the auto_start flag — explicit intent wins.
    with patch("ai_cli.config.load_config", return_value={"quota_watch": {"auto_start": False}}):
        with patch(
            "ai_cli.process_manager._ensure_circusd",
            return_value=f"ipc://{tmp_path}/circus.endpoint",
        ) as mock_ensure:
            with patch("ai_cli.process_manager.get_xdg_state_home", return_value=tmp_path):
                with patch("circus.client.CircusClient"):
                    process_manager._cmd_quota_watch_start(auto=False)
    mock_ensure.assert_called_once()


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


def test_given_reaper_start_when_circus_registration_fails_then_reports_failure_without_running_reaper(capsys):
    with patch("ai_cli.process_manager._ensure_circusd", side_effect=RuntimeError("unavailable")):
        assert not process_manager._cmd_stale_session_reaper_start()

    assert "failed to start" in capsys.readouterr().err


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
    assert capsys.readouterr().out.strip() == "stale-session-reaper: stopped"


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

    assert capsys.readouterr().out.strip() == "stale-session-reaper: running"


def test_given_reaper_status_when_watcher_is_not_registered_then_reports_not_running(tmp_path, capsys):
    client = MagicMock()
    client.send_message.return_value = {"statuses": {}}
    with (
        patch("ai_cli.process_manager.get_xdg_state_home", return_value=tmp_path),
        patch("circus.client.CircusClient", return_value=client),
    ):
        assert process_manager._cmd_stale_session_reaper_status()

    assert capsys.readouterr().out.strip() == "stale-session-reaper: not running"


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
