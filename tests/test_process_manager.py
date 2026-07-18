"""quota-watch auto-start config gate.

Root cause: `session_script.py`'s per-session `ai quota watch start` was
unconditional, so the notification-firing daemon silently re-registered on
every `ai c`/`ai g` launch and stayed running — redundant with the CC
statusline, which already surfaces weekly usage. `_cmd_quota_watch_start(auto=)`
now gates the *auto-start* path only on `[quota_watch] auto_start` (default
off); an explicit, user-typed `ai quota watch start` always registers.
"""

from __future__ import annotations

from unittest.mock import patch

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
