"""Tests for notifications module — Notifier, channel drivers, NotificationManager."""

import json
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_cli.notifications import (
    NotificationManager,
    NotificationResult,
    Notifier,
    _send_discord,
    _send_ntfy,
    _send_os_notification,
)


_ENV_KEYS = (
    "DISCORD_AI_NOTIFICATIONS_BOT_WEB_HOOK_URL",
    "NTFY_BASE_URL",
    "NTFY_TOPIC",
    "NTFY_TOKEN",
)


def _clear_env(base: dict | None = None) -> dict:
    import os

    env = {k: v for k, v in os.environ.items() if k not in _ENV_KEYS}
    if base:
        env.update(base)
    return env


class TestNotificationResult:
    def test_when_created_then_fields_set(self):
        r = NotificationResult(channel="discord", success=True, status_code=200)
        assert r.channel == "discord"
        assert r.success is True
        assert r.status_code == 200
        assert r.error is None

    def test_when_failure_then_error_field_set(self):
        r = NotificationResult(channel="ntfy", success=False, error="timeout")
        assert r.success is False
        assert r.error == "timeout"


class TestNotifierResolveChannels:
    def test_when_no_env_vars_then_no_channels(self):
        with patch.dict("os.environ", _clear_env(), clear=True):
            n = Notifier(channels_config={})
            assert n._resolve_channels(None) == []

    def test_when_discord_url_env_set_then_discord_available(self):
        env = _clear_env({"DISCORD_AI_NOTIFICATIONS_BOT_WEB_HOOK_URL": "https://discord.com/api/webhooks/1/x"})
        with patch.dict("os.environ", env, clear=True):
            n = Notifier(channels_config={})
            assert "discord" in n._resolve_channels(None)

    def test_when_ntfy_base_and_topic_set_then_ntfy_available(self):
        env = _clear_env({"NTFY_BASE_URL": "https://ntfy.example.com", "NTFY_TOPIC": "alerts"})
        with patch.dict("os.environ", env, clear=True):
            n = Notifier(channels_config={})
            assert "ntfy" in n._resolve_channels(None)

    def test_when_discord_disabled_in_config_then_excluded(self):
        env = _clear_env({"DISCORD_AI_NOTIFICATIONS_BOT_WEB_HOOK_URL": "https://discord.com/api/webhooks/1/x"})
        with patch.dict("os.environ", env, clear=True):
            n = Notifier(channels_config={"discord": {"enabled": False}})
            assert "discord" not in n._resolve_channels(None)

    def test_when_requested_channels_filter_applied(self):
        env = _clear_env(
            {
                "DISCORD_AI_NOTIFICATIONS_BOT_WEB_HOOK_URL": "https://discord.com/api/webhooks/1/x",
                "NTFY_BASE_URL": "https://ntfy.example.com",
                "NTFY_TOPIC": "alerts",
            }
        )
        with patch.dict("os.environ", env, clear=True):
            n = Notifier(channels_config={})
            result = n._resolve_channels(["discord"])
            assert result == ["discord"]
            assert "ntfy" not in result


class TestNotifierSend:
    def test_when_no_channels_then_os_fallback_fires(self):
        with patch.dict("os.environ", _clear_env(), clear=True):
            n = Notifier(channels_config={"os_fallback": True})
            with patch("ai_cli.notifications._send_os_notification") as mock_os:
                mock_os.return_value = NotificationResult(channel="os", success=True)
                results = n.send("Test", "Body")
        mock_os.assert_called_once()
        assert any(r.channel == "os" for r in results)

    def test_when_os_fallback_disabled_and_no_channels_then_no_results(self):
        with patch.dict("os.environ", _clear_env(), clear=True):
            n = Notifier(channels_config={"os_fallback": False})
            results = n.send("Test", "Body")
        assert results == []

    def test_when_discord_succeeds_then_no_os_fallback(self):
        env = _clear_env({"DISCORD_AI_NOTIFICATIONS_BOT_WEB_HOOK_URL": "https://discord.com/api/webhooks/1/x"})
        with patch.dict("os.environ", env, clear=True):
            n = Notifier(channels_config={"os_fallback": True})
            with (
                patch("ai_cli.notifications._send_discord") as mock_discord,
                patch("ai_cli.notifications._send_os_notification") as mock_os,
            ):
                mock_discord.return_value = NotificationResult(channel="discord", success=True)
                results = n.send("Test", "Body")
        mock_os.assert_not_called()
        assert any(r.channel == "discord" and r.success for r in results)

    def test_when_all_primaries_fail_then_os_fallback_fires(self):
        env = _clear_env({"DISCORD_AI_NOTIFICATIONS_BOT_WEB_HOOK_URL": "https://discord.com/api/webhooks/1/x"})
        with patch.dict("os.environ", env, clear=True):
            n = Notifier(channels_config={"os_fallback": True})
            with (
                patch("ai_cli.notifications._send_discord") as mock_discord,
                patch("ai_cli.notifications._send_os_notification") as mock_os,
            ):
                mock_discord.return_value = NotificationResult(channel="discord", success=False, error="fail")
                mock_os.return_value = NotificationResult(channel="os", success=True)
                n.send("Test", "Body")
        mock_os.assert_called_once()

    def test_when_send_called_then_logs_to_db(self):
        with patch.dict("os.environ", _clear_env(), clear=True):
            n = Notifier(channels_config={"os_fallback": True})
            with (
                patch("ai_cli.notifications._send_os_notification") as mock_os,
                patch("ai_cli.notifications.Notifier._log") as mock_log,
            ):
                mock_os.return_value = NotificationResult(channel="os", success=True)
                n.send("Test", "Body", source="test-source")
        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args[0][5] == "test-source"  # source arg (positional index 5)

    def test_when_log_db_raises_then_send_does_not_crash(self):
        with patch.dict("os.environ", _clear_env(), clear=True):
            n = Notifier(channels_config={"os_fallback": True})
            with (
                patch("ai_cli.notifications._send_os_notification") as mock_os,
                patch("ai_cli.quota_db.log_notification", side_effect=RuntimeError("db error")),
            ):
                mock_os.return_value = NotificationResult(channel="os", success=True)
                results = n.send("Test", "Body")
        assert len(results) > 0  # delivery still happened


class TestNotifierListChannels:
    def test_when_discord_url_set_then_discord_shows_enabled(self):
        env = _clear_env({"DISCORD_AI_NOTIFICATIONS_BOT_WEB_HOOK_URL": "https://discord.com/api/webhooks/1/x"})
        with patch.dict("os.environ", env, clear=True):
            n = Notifier(channels_config={})
            channels = n.list_channels()
        discord = next(c for c in channels if c["name"] == "discord")
        assert discord["enabled"] is True
        assert discord["credentials"]["webhook_url"] == "set"

    def test_when_no_credentials_then_shows_missing(self):
        with patch.dict("os.environ", _clear_env(), clear=True):
            n = Notifier(channels_config={})
            channels = n.list_channels()
        discord = next(c for c in channels if c["name"] == "discord")
        assert discord["enabled"] is False
        assert discord["credentials"]["webhook_url"] == "missing"

    def test_always_returns_three_channels(self):
        with patch.dict("os.environ", _clear_env(), clear=True):
            n = Notifier(channels_config={})
            channels = n.list_channels()
        names = {c["name"] for c in channels}
        assert names == {"discord", "ntfy", "os"}

    def test_os_channel_has_no_credentials(self):
        with patch.dict("os.environ", _clear_env(), clear=True):
            n = Notifier(channels_config={})
            channels = n.list_channels()
        os_ch = next(c for c in channels if c["name"] == "os")
        assert os_ch["credentials"] == {}


class TestSendDiscord:
    def _make_request_capture(self):
        captured = []

        class FakeReq:
            def __init__(self, url, data, headers, method):
                captured.append({"url": url, "payload": json.loads(data.decode()), "headers": headers})

        return captured, FakeReq

    def test_when_discord_url_then_posts_content_field(self):
        captured, FakeReq = self._make_request_capture()
        with (
            patch("urllib.request.Request", side_effect=FakeReq),
            patch.object(urllib.request, "urlopen"),
        ):
            _send_discord("https://discord.com/api/webhooks/1/x", "Title", "Body", "default")
        assert captured and "content" in captured[0]["payload"]
        assert "text" not in captured[0]["payload"]

    def test_when_discordapp_url_then_posts_content_field(self):
        captured, FakeReq = self._make_request_capture()
        with (
            patch("urllib.request.Request", side_effect=FakeReq),
            patch.object(urllib.request, "urlopen"),
        ):
            _send_discord("https://discordapp.com/api/webhooks/1/x", "Title", "Body", "default")
        assert captured and "content" in captured[0]["payload"]
        assert "text" not in captured[0]["payload"]

    def test_when_slack_url_then_posts_text_field(self):
        captured, FakeReq = self._make_request_capture()
        with (
            patch("urllib.request.Request", side_effect=FakeReq),
            patch.object(urllib.request, "urlopen"),
        ):
            _send_discord("https://hooks.slack.com/test", "Title", "Body", "default")
        assert captured and "text" in captured[0]["payload"]

    def test_when_urlopen_succeeds_then_returns_success(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        with (
            patch("urllib.request.Request"),
            patch.object(urllib.request, "urlopen", return_value=mock_resp),
        ):
            r = _send_discord("https://discord.com/api/webhooks/1/x", "Title", "Body", "default")
        assert r.success is True
        assert r.status_code == 200

    def test_when_urlopen_raises_http_error_then_returns_failure_with_status(self):
        err = urllib.error.HTTPError("url", 403, "Forbidden", {}, None)
        with (
            patch("urllib.request.Request"),
            patch.object(urllib.request, "urlopen", side_effect=err),
        ):
            r = _send_discord("https://discord.com/api/webhooks/1/x", "Title", "Body", "default")
        assert r.success is False
        assert r.status_code == 403

    def test_when_network_error_then_returns_failure(self):
        with (
            patch("urllib.request.Request"),
            patch.object(urllib.request, "urlopen", side_effect=OSError("network")),
        ):
            r = _send_discord("https://discord.com/api/webhooks/1/x", "Title", "Body", "default")
        assert r.success is False
        assert r.channel == "discord"

    def test_when_user_agent_header_set(self):
        captured, FakeReq = self._make_request_capture()
        with (
            patch("urllib.request.Request", side_effect=FakeReq),
            patch.object(urllib.request, "urlopen"),
        ):
            _send_discord("https://discord.com/api/webhooks/1/x", "Title", "Body", "default")
        assert captured[0]["headers"].get("User-Agent") == "ai-cli-utils/1.0"


class TestSendNtfy:
    def _capture(self, url, token, title, body, priority, tags):
        captured = []

        class FakeReq:
            def __init__(self, u, data, headers, method):
                captured.append({"url": u, "body": data.decode(), "headers": headers})

        with (
            patch("urllib.request.Request", side_effect=FakeReq),
            patch.object(urllib.request, "urlopen"),
        ):
            _send_ntfy(url, token, title, body, priority, tags)
        return captured

    def test_when_token_provided_then_bearer_header_set(self):
        c = self._capture("https://ntfy.example.com/alerts", "tk_123", "Title", "Body", "default", [])
        assert c and c[0]["headers"].get("Authorization") == "Bearer tk_123"

    def test_when_no_token_then_no_auth_header(self):
        c = self._capture("https://ntfy.example.com/alerts", "", "Title", "Body", "default", [])
        assert c and "Authorization" not in c[0]["headers"]

    def test_when_urgent_priority_then_urgent_header(self):
        c = self._capture("https://ntfy.example.com/alerts", "", "Title", "Body", "urgent", [])
        assert c and c[0]["headers"].get("Priority") == "urgent"

    def test_when_high_priority_then_high_header(self):
        c = self._capture("https://ntfy.example.com/alerts", "", "Title", "Body", "high", [])
        assert c and c[0]["headers"].get("Priority") == "high"

    def test_when_tags_provided_then_tags_header_set(self):
        c = self._capture("https://ntfy.example.com/alerts", "", "Title", "Body", "default", ["warning", "robot"])
        assert c and c[0]["headers"].get("Tags") == "warning,robot"

    def test_when_no_tags_and_urgent_then_rotating_light_tag(self):
        c = self._capture("https://ntfy.example.com/alerts", "", "Title", "Body", "urgent", [])
        assert c and c[0]["headers"].get("Tags") == "rotating_light"

    def test_when_urlopen_succeeds_then_returns_success(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        with (
            patch("urllib.request.Request"),
            patch.object(urllib.request, "urlopen", return_value=mock_resp),
        ):
            r = _send_ntfy("https://ntfy.example.com/alerts", "", "Title", "Body", "default", [])
        assert r.success is True
        assert r.channel == "ntfy"

    def test_when_http_error_then_returns_failure_with_status(self):
        err = urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)
        with (
            patch("urllib.request.Request"),
            patch.object(urllib.request, "urlopen", side_effect=err),
        ):
            r = _send_ntfy("https://ntfy.example.com/alerts", "bad_token", "Title", "Body", "default", [])
        assert r.success is False
        assert r.status_code == 401

    def test_when_network_error_then_returns_failure(self):
        with (
            patch("urllib.request.Request"),
            patch.object(urllib.request, "urlopen", side_effect=OSError("timeout")),
        ):
            r = _send_ntfy("https://ntfy.example.com/alerts", "", "Title", "Body", "default", [])
        assert r.success is False
        assert r.channel == "ntfy"


class TestSendOsNotification:
    def test_when_darwin_then_calls_osascript(self):
        with (
            patch("sys.platform", "darwin"),
            patch("subprocess.run") as mock_run,
        ):
            r = _send_os_notification("Title", "Body")
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0][0] == "osascript"
        assert r.success is True

    def test_when_linux_then_calls_notify_send(self):
        with (
            patch("sys.platform", "linux"),
            patch("subprocess.run") as mock_run,
        ):
            r = _send_os_notification("Title", "Body")
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0][0] == "notify-send"
        assert r.success is True

    def test_when_subprocess_raises_then_returns_failure(self):
        with (
            patch("sys.platform", "linux"),
            patch("subprocess.run", side_effect=FileNotFoundError("not found")),
        ):
            r = _send_os_notification("Title", "Body")
        assert r.success is False
        assert r.channel == "os"

    def test_when_win32_and_plyer_installed_then_calls_plyer_notify(self):
        mock_plyer = MagicMock()
        with (
            patch("sys.platform", "win32"),
            patch.dict("sys.modules", {"plyer": mock_plyer, "plyer.notification": mock_plyer.notification}),
            patch("builtins.__import__", wraps=_make_plyer_import(mock_plyer)),
        ):
            r = _send_os_notification("Alert", "Details")
        assert r.success is True
        assert r.channel == "os"

    def test_when_win32_and_plyer_not_installed_then_returns_success(self):
        """Silently degrades when plyer is not installed — no crash, success=True."""

        def raise_import(name, *args, **kwargs):
            if name == "plyer":
                raise ImportError("plyer not installed")
            return original_import(name, *args, **kwargs)

        import builtins

        original_import = builtins.__import__
        with (
            patch("sys.platform", "win32"),
            patch("builtins.__import__", side_effect=raise_import),
        ):
            r = _send_os_notification("Alert", "Details")
        assert r.success is True

    def test_when_win32_and_subprocess_not_called(self):
        """On Windows, subprocess.run must never be called (no osascript / notify-send)."""
        mock_plyer = MagicMock()
        with (
            patch("sys.platform", "win32"),
            patch("subprocess.run") as mock_run,
            patch("builtins.__import__", wraps=_make_plyer_import(mock_plyer)),
        ):
            _send_os_notification("Alert", "Body")
        mock_run.assert_not_called()


def _make_plyer_import(mock_plyer):
    """Helper: returns an __import__ side-effect that resolves 'plyer' to mock_plyer."""
    import builtins

    original = builtins.__import__

    def _import(name, *args, **kwargs):
        if name == "plyer":
            return mock_plyer
        return original(name, *args, **kwargs)

    return _import


class TestNotifierLog:
    def test_when_quota_db_raises_then_no_crash(self):
        n = Notifier(channels_config={})
        with patch("ai_cli.quota_db.log_notification", side_effect=RuntimeError("db error")):
            n._log("Title", "Body", "default", [], [], "test")  # must not raise


class TestNotificationManager:
    def test_is_suppressed_when_lock_exists_then_true(self, tmp_path):
        mgr = NotificationManager("test-session")
        mgr.lock_file = tmp_path / "test.lock"
        mgr.lock_file.touch()
        assert mgr._is_suppressed() is True

    def test_is_suppressed_when_no_lock_then_false(self, tmp_path):
        mgr = NotificationManager("test-session")
        mgr.lock_file = tmp_path / "nonexistent.lock"
        assert mgr._is_suppressed() is False

    def test_emit_badge_when_session_num_set_then_writes_escape(self, monkeypatch, capfd):
        monkeypatch.setenv("ITERM2_SESSION_NUM", "1")
        mgr = NotificationManager("test-session")
        mgr.lock_file = Path("/tmp/nonexistent-lock-file-xyz.lock")
        mgr.emit_badge("done")
        captured = capfd.readouterr()
        assert "\033]1337;SetBadgeFormat=" in captured.err
        assert "\007" in captured.err

    def test_emit_badge_when_no_session_num_then_silent(self, monkeypatch, capfd):
        monkeypatch.delenv("ITERM2_SESSION_NUM", raising=False)
        mgr = NotificationManager("test-session")
        mgr.lock_file = Path("/tmp/nonexistent-lock-file-xyz.lock")
        mgr.emit_badge("done")
        captured = capfd.readouterr()
        assert captured.err == ""

    def test_emit_badge_when_suppressed_then_silent(self, monkeypatch, tmp_path, capfd):
        monkeypatch.setenv("ITERM2_SESSION_NUM", "1")
        mgr = NotificationManager("test-session")
        mgr.lock_file = tmp_path / "test.lock"
        mgr.lock_file.touch()
        mgr.emit_badge("done")
        captured = capfd.readouterr()
        assert captured.err == ""

    def test_emit_badge_encodes_message_as_base64(self, monkeypatch, capfd):
        import base64

        monkeypatch.setenv("ITERM2_SESSION_NUM", "1")
        mgr = NotificationManager("test-session")
        mgr.lock_file = Path("/tmp/nonexistent-lock-file-xyz.lock")
        mgr.emit_badge("hello")
        captured = capfd.readouterr()
        expected = base64.b64encode("✓ hello".encode()).decode()
        assert expected in captured.err

    def test_notify_when_called_then_delegates_to_emit_badge(self, monkeypatch):
        monkeypatch.setenv("ITERM2_SESSION_NUM", "1")
        mgr = NotificationManager("test-session")
        mgr.lock_file = Path("/tmp/nonexistent-lock-file-xyz.lock")
        with patch.object(mgr, "emit_badge") as mock_badge:
            mgr.notify("task complete")
        mock_badge.assert_called_once_with("task complete")

    def test_notify_when_suppressed_then_emits_nothing(self, tmp_path, capfd):
        mgr = NotificationManager("test-session")
        mgr.lock_file = tmp_path / "test.lock"
        mgr.lock_file.touch()
        mgr.notify("task complete")
        captured = capfd.readouterr()
        assert captured.err == ""
