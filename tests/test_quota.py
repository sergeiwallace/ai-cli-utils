"""Tests for quota tracking."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from ai_cli.quota import _get_claude_usage_percent, _dedup_key, _send_notification


class TestGetClaudeUsage:
    def test_usage_when_usage_file_exists_then_returns_percent(self, tmp_path):
        usage_file = tmp_path / ".claude" / "usage.json"
        usage_file.parent.mkdir(parents=True)
        usage_file.write_text(json.dumps({"used": 75, "limit": 100}))

        with patch.object(Path, "home", return_value=tmp_path):
            result = _get_claude_usage_percent()
        assert result == 75.0

    def test_usage_when_zero_limit_then_returns_none(self, tmp_path):
        usage_file = tmp_path / ".claude" / "usage.json"
        usage_file.parent.mkdir(parents=True)
        usage_file.write_text(json.dumps({"used": 50, "limit": 0}))

        with patch.object(Path, "home", return_value=tmp_path):
            result = _get_claude_usage_percent()
        # Falls through to CLI attempt, which will also fail in test
        # So returns None
        assert result is None

    def test_usage_when_no_file_and_no_cli_then_returns_none(self, tmp_path):
        with patch.object(Path, "home", return_value=tmp_path):
            with patch("subprocess.run", side_effect=FileNotFoundError):
                result = _get_claude_usage_percent()
        assert result is None

    def test_usage_when_cli_returns_percentage_then_parses(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Usage: 62.5% of monthly quota"

        with patch.object(Path, "home", return_value=tmp_path):
            with patch("subprocess.run", return_value=mock_result):
                result = _get_claude_usage_percent()
        assert result == 62.5


class TestDedupKey:
    def test_dedup_key_includes_threshold_and_date(self):
        key = _dedup_key(50)
        assert "quota-50-" in key
        # Should contain today's date
        from datetime import date

        assert date.today().isoformat() in key


class TestSendNotification:
    def test_send_notification_when_linux_then_uses_notify_send(self):
        with patch("subprocess.run") as mock_run:
            _send_notification(75, 78.5)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "notify-send"
        assert "78%" in args[2]

    def test_send_notification_when_90_threshold_then_warns(self):
        with patch("subprocess.run") as mock_run:
            _send_notification(90, 92.0)
        args = mock_run.call_args[0][0]
        assert "slow down" in args[2]

    def test_send_notification_when_subprocess_raises_then_no_crash(self):
        """Lines 138-139: exception path in _send_notification."""
        with patch("subprocess.run", side_effect=FileNotFoundError("notify-send not found")):
            _send_notification(75, 78.5)  # Should not raise


class TestGetClaudeUsageInvalidJson:
    def test_usage_when_invalid_json_then_falls_through(self, tmp_path):
        """Lines 32-33: json.loads raises on invalid JSON."""
        usage_file = tmp_path / ".claude" / "usage.json"
        usage_file.parent.mkdir(parents=True)
        usage_file.write_text("not valid json {{{")

        with patch.object(Path, "home", return_value=tmp_path):
            with patch("subprocess.run", side_effect=FileNotFoundError):
                result = _get_claude_usage_percent()
        assert result is None
