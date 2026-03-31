"""Tests for notifications module."""

from pathlib import Path

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

    def test_emit_osc9_when_not_suppressed_then_writes_escape(self, capfd):
        mgr = NotificationManager("test-session")
        mgr.lock_file = Path("/tmp/nonexistent-lock-file-xyz.lock")
        mgr.emit_osc9("hello")
        captured = capfd.readouterr()
        assert "\033]9;hello\007" in captured.err

    def test_emit_osc9_when_suppressed_then_silent(self, tmp_path, capfd):
        mgr = NotificationManager("test-session")
        mgr.lock_file = tmp_path / "test.lock"
        mgr.lock_file.touch()
        mgr.emit_osc9("hello")
        captured = capfd.readouterr()
        assert captured.err == ""

    def test_emit_iterm2_when_not_suppressed_then_writes_notify(self, capfd):
        mgr = NotificationManager("test-session")
        mgr.lock_file = Path("/tmp/nonexistent-lock-file-xyz.lock")
        mgr.emit_iterm2("done")
        captured = capfd.readouterr()
        assert "NOTIFY: done\n" in captured.err

    def test_emit_iterm2_when_suppressed_then_silent(self, tmp_path, capfd):
        mgr = NotificationManager("test-session")
        mgr.lock_file = tmp_path / "test.lock"
        mgr.lock_file.touch()
        mgr.emit_iterm2("done")
        captured = capfd.readouterr()
        assert captured.err == ""

    def test_notify_when_not_suppressed_then_emits_both(self, capfd):
        mgr = NotificationManager("test-session")
        mgr.lock_file = Path("/tmp/nonexistent-lock-file-xyz.lock")
        mgr.notify("task complete")
        captured = capfd.readouterr()
        assert "\033]9;task complete\007" in captured.err
        assert "NOTIFY: task complete\n" in captured.err

    def test_notify_when_suppressed_then_emits_nothing(self, tmp_path, capfd):
        mgr = NotificationManager("test-session")
        mgr.lock_file = tmp_path / "test.lock"
        mgr.lock_file.touch()
        mgr.notify("task complete")
        captured = capfd.readouterr()
        assert captured.err == ""
