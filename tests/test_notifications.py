"""Tests for notifications module."""

from pathlib import Path
from unittest.mock import patch

from ai_cli.notifications import NotificationManager


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
