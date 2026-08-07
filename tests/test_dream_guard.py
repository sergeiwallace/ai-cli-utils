"""Tests for sync push dream safety guard."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from ai_cli.sync import _wait_for_dream_completion


class TestDreamGuard:
    def test_wait_when_no_pid_file_then_returns_immediately(self, tmp_path):
        """No memory watcher running means no guard needed."""
        with patch("ai_cli.sync._pid_file_path", return_value=tmp_path / "nonexistent.pid"):
            _wait_for_dream_completion(verbose=False)

    def test_wait_when_nats_unavailable_then_proceeds_silently(self, tmp_path):
        """Dream guard is non-fatal if NATS is down."""
        pid_path = tmp_path / "memory-watch.pid"
        pid_path.write_text("1")

        from nats.errors import NoServersError

        with patch("ai_cli.sync._pid_file_path", return_value=pid_path):
            with patch("nats.connect", new=AsyncMock(side_effect=NoServersError)):
                with patch("asyncio.sleep", new=AsyncMock()):
                    _wait_for_dream_completion(verbose=False)

    def test_wait_when_no_recent_memory_writes_then_skips_guard(self, tmp_path):
        """NATS up but no recent MEMORY.md writes = no dream active."""
        pid_path = tmp_path / "memory-watch.pid"
        pid_path.write_text("1")

        mock_nc = MagicMock()
        mock_nc.close = AsyncMock()

        with patch("ai_cli.sync._pid_file_path", return_value=pid_path):
            with patch("nats.connect", new=AsyncMock(return_value=mock_nc)):
                # No CC projects dir = no recent writes
                with patch.object(Path, "home", return_value=tmp_path):
                    _wait_for_dream_completion(verbose=False)

    def test_wait_when_active_marker_then_waits_until_watcher_clears_it(self, tmp_path):
        """The on-disk marker closes the gap between detecting and subscribing to NATS."""
        state_path = tmp_path / "memory-dream-active"
        state_path.write_text("123")

        def settle_write(_seconds):
            state_path.unlink()

        with patch("ai_cli.sync._dream_state_path", return_value=state_path):
            with patch("ai_cli.config._pid_alive", return_value=True):
                with patch("time.sleep", side_effect=settle_write) as sleep:
                    _wait_for_dream_completion(verbose=False)

        sleep.assert_called_once()
