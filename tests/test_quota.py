"""Tests for quota tracking."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from ai_cli.quota import _get_claude_usage_percent, _send_notification, _find_claude_pane


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


class TestFindClaudePane:
    def _make_tmux_result(self, lines):
        result = MagicMock()
        result.returncode = 0
        result.stdout = "\n".join(lines) + "\n"
        return result

    def test_find_claude_pane_when_session_starts_with_c_dash_then_matches(self):
        """c-sw-5, c-art-2, c-hm-1 must all match — the c- prefix is the convention."""
        lines = [
            "%10 bash c-sw-5",
            "%11 bash unrelated-session",
        ]
        with patch("subprocess.run", return_value=self._make_tmux_result(lines)):
            pane = _find_claude_pane()
        assert pane == "%10"

    def test_find_claude_pane_when_remote_session_then_matches(self):
        """c-r-sw-1 (remote Hetzner sessions) must match since they start with c-."""
        lines = ["%20 bash c-r-sw-1"]
        with patch("subprocess.run", return_value=self._make_tmux_result(lines)):
            pane = _find_claude_pane()
        assert pane == "%20"

    def test_find_claude_pane_when_no_c_prefix_session_then_returns_none(self):
        """Sessions not starting with c- (e.g. plain bash, other tools) must not match."""
        lines = [
            "%30 bash sw-1",
            "%31 bash cc-myapp",
            "%32 bash someothersession",
        ]
        with patch("subprocess.run", return_value=self._make_tmux_result(lines)):
            pane = _find_claude_pane()
        assert pane is None

    def test_find_claude_pane_when_node_process_then_matches_command(self):
        """Primary path: pane running node/claude process takes priority over session name."""
        lines = ["%40 node other-session", "%41 bash c-sw-1"]
        with patch("subprocess.run", return_value=self._make_tmux_result(lines)):
            pane = _find_claude_pane()
        assert pane == "%40"


class TestQuotaWatch:
    def test_quota_watch_when_already_running_then_returns_2(self):
        with patch("ai_cli.sync._acquire_pid_file", return_value=False):
            from ai_cli.quota import quota_watch

            result = quota_watch()
        assert result == 2

    def test_quota_watch_when_nats_unavailable_then_returns_1(self):
        mock_client = MagicMock()
        mock_client.nc = None

        async def fake_connect():
            pass  # nc stays None

        mock_client.connect = fake_connect

        with (
            patch("ai_cli.sync._acquire_pid_file", return_value=True),
            patch("ai_cli.sync._release_pid_file"),
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
        ):
            from ai_cli.quota import quota_watch

            result = quota_watch()
        assert result == 1

    def test_quota_watch_when_interrupt_then_returns_0(self):
        mock_client = MagicMock()
        mock_client.nc = None

        async def fake_connect():
            mock_client.nc = MagicMock()

        async def fake_close():
            pass

        mock_client.connect = fake_connect
        mock_client.close = fake_close

        def fake_sleep(_interval):
            raise KeyboardInterrupt

        with (
            patch("ai_cli.sync._acquire_pid_file", return_value=True),
            patch("ai_cli.sync._release_pid_file") as mock_release,
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("ai_cli.quota._get_claude_usage_percent", return_value=None),
            patch("time.sleep", fake_sleep),
        ):
            from ai_cli.quota import quota_watch

            result = quota_watch()
        assert result == 0
        mock_release.assert_called_with("quota-watch")

    def test_quota_watch_when_usage_crosses_threshold_then_publishes(self):
        mock_client = MagicMock()
        mock_client.nc = None

        async def fake_connect():
            mock_client.nc = MagicMock()

        async def fake_publish(subject, payload):
            pass

        async def fake_close():
            pass

        mock_client.connect = fake_connect
        mock_client.publish = MagicMock(side_effect=fake_publish)
        mock_client.close = MagicMock(side_effect=fake_close)

        call_count = 0

        def fake_sleep(_interval):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                raise KeyboardInterrupt

        with (
            patch("ai_cli.sync._acquire_pid_file", return_value=True),
            patch("ai_cli.sync._release_pid_file"),
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("ai_cli.quota._get_claude_usage_percent", return_value=80.0),
            patch("ai_cli.quota._send_notification"),
            patch("time.sleep", fake_sleep),
        ):
            from ai_cli.quota import quota_watch

            result = quota_watch()

        assert result == 0
        # Should have published for thresholds 50 and 75 (both <= 80)
        assert mock_client.publish.call_count >= 2
        subjects = [call.args[0] for call in mock_client.publish.call_args_list]
        assert "quota.threshold.50" in subjects
        assert "quota.threshold.75" in subjects

    def test_quota_watch_when_connect_raises_then_handles_unavailable(self):
        """Covers lines 80-81: except Exception: pass after loop.run_until_complete(connect())."""
        mock_client = MagicMock()
        mock_client.nc = None

        async def fake_connect_raises():
            raise Exception("connection error")

        mock_client.connect = fake_connect_raises

        with (
            patch("ai_cli.sync._acquire_pid_file", return_value=True),
            patch("ai_cli.sync._release_pid_file"),
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
        ):
            from ai_cli.quota import quota_watch

            result = quota_watch()
        assert result == 1  # NATS unavailable after connect raised

    def test_quota_watch_when_publish_raises_then_logs_error(self, capsys):
        """Covers lines 113-114: except Exception in publish block."""
        mock_client = MagicMock()
        mock_client.nc = None

        async def fake_connect():
            mock_client.nc = MagicMock()

        async def fake_publish_raises(*args, **kwargs):
            raise Exception("publish failed")

        async def fake_close():
            pass

        mock_client.connect = fake_connect
        mock_client.publish = MagicMock(side_effect=fake_publish_raises)
        mock_client.close = MagicMock(side_effect=fake_close)

        call_count = 0

        def fake_sleep(_interval):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                raise KeyboardInterrupt

        with (
            patch("ai_cli.sync._acquire_pid_file", return_value=True),
            patch("ai_cli.sync._release_pid_file"),
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("ai_cli.quota._get_claude_usage_percent", return_value=80.0),
            patch("ai_cli.quota._send_notification"),
            patch("time.sleep", fake_sleep),
        ):
            from ai_cli.quota import quota_watch

            result = quota_watch()

        assert result == 0
        assert "failed to publish" in capsys.readouterr().err
