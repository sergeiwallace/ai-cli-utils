"""Tests for sync_watch PID file guard and deduplication."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from ai_cli.sync import _acquire_pid_file, _release_pid_file, _pid_file_path


class TestPidFileGuard:
    def test_acquire_when_no_existing_pid_then_writes_current_pid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("ai_cli.sync._pid_file_path", return_value=Path(tmpdir) / "test.pid"):
                assert _acquire_pid_file("test") is True
                pid_path = Path(tmpdir) / "test.pid"
                assert pid_path.exists()
                assert int(pid_path.read_text().strip()) == os.getpid()

    def test_acquire_when_stale_pid_then_overwrites(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = Path(tmpdir) / "test.pid"
            pid_path.write_text("99999999")  # PID that doesn't exist
            with patch("ai_cli.sync._pid_file_path", return_value=pid_path):
                assert _acquire_pid_file("test") is True
                assert int(pid_path.read_text().strip()) == os.getpid()

    def test_acquire_when_live_pid_then_returns_false(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = Path(tmpdir) / "test.pid"
            pid_path.write_text(str(os.getpid()))  # Our own PID is alive
            with patch("ai_cli.sync._pid_file_path", return_value=pid_path):
                assert _acquire_pid_file("test") is False

    def test_release_when_pid_exists_then_removes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pid_path = Path(tmpdir) / "test.pid"
            pid_path.write_text(str(os.getpid()))
            with patch("ai_cli.sync._pid_file_path", return_value=pid_path):
                _release_pid_file("test")
                assert not pid_path.exists()

    def test_release_when_no_pid_then_no_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("ai_cli.sync._pid_file_path", return_value=Path(tmpdir) / "nonexistent.pid"):
                _release_pid_file("test")  # Should not raise

    def test_pid_file_path_returns_expected_location(self):
        path = _pid_file_path("sync-watch")
        assert path == Path.home() / ".ai-cli" / "sync-watch.pid"
