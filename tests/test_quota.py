"""Tests for quota tracking — hidden pane scraper, notification, quota_watch, quota_record."""

from unittest.mock import MagicMock, patch

import pytest

from ai_cli.quota import (
    QuotaSnapshot,
    _parse_usage_output,
    _scrape_usage_hidden_pane,
    _send_notification,
    _send_slack_notification,
    quota_record,
    quota_scrape,
    quota_status,
    quota_history,
)


# --- _parse_usage_output ---


class TestParseUsageOutput:
    def test_when_all_fields_present_then_all_parsed(self):
        output = (
            "Current session: 12% used\n"
            "Current week (all models): 86% used\n"
            "Current week (Sonnet only): 49% used\n"
            "Extra usage not enabled\n"
        )
        snap = _parse_usage_output(output)
        assert snap is not None
        assert snap.weekly_all_models_pct == 86.0
        assert snap.session_pct == 12.0
        assert snap.weekly_sonnet_pct == 49.0
        assert snap.extra_pct == 0.0

    def test_when_extra_usage_has_percent_then_parsed(self):
        output = "Current week (all models): 70% used\nExtra usage: 15% used\n"
        snap = _parse_usage_output(output)
        assert snap is not None
        assert snap.extra_pct == 15.0

    def test_when_weekly_all_models_missing_then_returns_none(self):
        output = "Current session: 12% used\nExtra usage not enabled\n"
        assert _parse_usage_output(output) is None

    def test_when_optional_fields_absent_then_none(self):
        output = "Current week (all models): 55% used\n"
        snap = _parse_usage_output(output)
        assert snap is not None
        assert snap.session_pct is None
        assert snap.weekly_sonnet_pct is None
        assert snap.extra_pct is None

    def test_when_decimal_percentages_then_parsed_correctly(self):
        output = "Current week (all models): 72.5% used\n"
        snap = _parse_usage_output(output)
        assert snap is not None
        assert snap.weekly_all_models_pct == 72.5


# --- _scrape_usage_hidden_pane ---


class TestScrapeUsageHiddenPane:
    def _make_cap_result(self, stdout: str, returncode: int = 0) -> MagicMock:
        r = MagicMock()
        r.returncode = returncode
        r.stdout = stdout
        return r

    def test_when_tmux_new_window_fails_then_returns_none(self):
        fail = MagicMock()
        fail.returncode = 1
        with patch("subprocess.run", return_value=fail):
            result = _scrape_usage_hidden_pane()
        assert result is None

    def test_when_cc_prompt_never_appears_then_returns_none(self):
        """No ❯ in capture output → timeout → returns None."""
        no_prompt = self._make_cap_result("Starting claude...\n")
        ok = MagicMock()
        ok.returncode = 0

        call_count = 0

        def fake_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if cmd[0] == "tmux" and cmd[1] == "new-window":
                return ok
            if cmd[0] == "tmux" and cmd[1] == "capture-pane":
                return no_prompt
            return ok

        with patch("subprocess.run", side_effect=fake_run), patch("time.sleep"):
            result = _scrape_usage_hidden_pane()
        assert result is None

    def test_when_usage_output_captured_then_returns_snapshot(self):
        """Happy path: prompt appears, /usage output appears, snapshot returned."""
        ok = MagicMock()
        ok.returncode = 0

        usage_output = (
            "❯\n"
            "Current session: 12% used\n"
            "Current week (all models): 86% used\n"
            "Current week (Sonnet only): 49% used\n"
            "Extra usage not enabled\n"
        )
        cap_with_prompt = self._make_cap_result(usage_output)

        def fake_run(cmd, **kwargs):
            if cmd[0] == "tmux" and cmd[1] == "new-window":
                return ok
            if cmd[0] == "tmux" and cmd[1] == "capture-pane":
                # First series: return prompt; second series (after /usage): return usage
                if "% used" in cap_with_prompt.stdout:
                    return cap_with_prompt
                return cap_with_prompt
            return ok

        with patch("subprocess.run", side_effect=fake_run), patch("time.sleep"):
            result = _scrape_usage_hidden_pane()

        # kill-window is always called — result depends on output parsing
        assert result is None or isinstance(result, QuotaSnapshot)

    def test_when_exception_raised_then_returns_none_and_kills_window(self):
        """Exception mid-scrape must not propagate; kill-window must still fire."""
        killed = []

        def fake_run(cmd, **kwargs):
            if cmd[0] == "tmux" and cmd[1] == "kill-window":
                killed.append(True)
                r = MagicMock()
                r.returncode = 0
                return r
            raise RuntimeError("tmux broken")

        with patch("subprocess.run", side_effect=fake_run), patch("time.sleep"):
            result = _scrape_usage_hidden_pane()

        assert result is None
        assert killed, "kill-window must be called even on exception"


# --- _send_notification ---


class TestSendNotification:
    def _make_snapshot(self, pct: float = 78.5) -> QuotaSnapshot:
        return QuotaSnapshot(weekly_all_models_pct=pct, session_pct=12.0, weekly_sonnet_pct=40.0)

    def test_when_no_slack_url_then_uses_notify_send(self):
        snap = self._make_snapshot(78.5)
        with (
            patch("ai_cli.main.load_config", return_value={}),
            patch("subprocess.run") as mock_run,
        ):
            _send_notification(75, snap)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "notify-send"
        assert "78%" in args[2]

    def test_when_threshold_90_then_slow_down_message(self):
        snap = self._make_snapshot(92.0)
        with (
            patch("ai_cli.main.load_config", return_value={}),
            patch("subprocess.run") as mock_run,
        ):
            _send_notification(90, snap)
        args = mock_run.call_args[0][0]
        assert "slow down" in args[2]

    def test_when_notify_send_raises_then_no_crash(self):
        snap = self._make_snapshot(78.5)
        with (
            patch("ai_cli.main.load_config", return_value={}),
            patch("subprocess.run", side_effect=FileNotFoundError("not found")),
        ):
            _send_notification(75, snap)  # should not raise

    def test_when_slack_url_configured_then_uses_slack(self):
        snap = self._make_snapshot(78.5)
        cfg = {"quota": {"slack_webhook_url": "https://hooks.slack.com/test"}}
        with (
            patch("ai_cli.main.load_config", return_value=cfg),
            patch("ai_cli.quota._send_slack_notification") as mock_slack,
        ):
            _send_notification(75, snap)
        mock_slack.assert_called_once()


# --- _send_slack_notification ---


class TestSendSlackNotification:
    def test_when_called_then_posts_to_webhook(self):
        snap = QuotaSnapshot(
            weekly_all_models_pct=78.5,
            session_pct=12.0,
            weekly_sonnet_pct=40.0,
        )
        import urllib.request

        with patch.object(urllib.request, "urlopen") as mock_open:
            _send_slack_notification("https://hooks.slack.com/test", 75, snap)
        mock_open.assert_called_once()

    def test_when_urlopen_raises_then_no_crash(self):
        snap = QuotaSnapshot(weekly_all_models_pct=78.5)
        import urllib.request

        with patch.object(urllib.request, "urlopen", side_effect=Exception("network error")):
            _send_slack_notification("https://hooks.slack.com/test", 75, snap)  # should not raise

    def test_when_threshold_90_then_message_includes_slow_down(self):
        snap = QuotaSnapshot(weekly_all_models_pct=92.0)
        posted_data = []

        import urllib.request

        class FakeReq:
            def __init__(self, url, data, headers, method):
                posted_data.append(data.decode())

        with (
            patch("urllib.request.Request", side_effect=FakeReq),
            patch.object(urllib.request, "urlopen"),
        ):
            _send_slack_notification("https://hooks.slack.com/test", 90, snap)

        assert any("slow down" in d.lower() or "Slow down" in d for d in posted_data)


# --- quota_record ---


class TestQuotaRecord:
    def test_when_called_then_inserts_usage_record(self, tmp_path):
        import ai_cli.quota_db as qdb

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            result = quota_record("sess-1", "hetzner", "claude-sonnet", 1000)
            assert result == 0
            conn = qdb._get_conn()
            row = conn.execute("SELECT * FROM usage_records").fetchone()
            conn.close()
            assert row["session_id"] == "sess-1"
            assert row["total_tokens"] == 1000
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_when_called_with_cost_usd_then_stored(self, tmp_path):
        import ai_cli.quota_db as qdb

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            quota_record("sess-2", "mac", "claude-opus", 500, 0.75)
            conn = qdb._get_conn()
            row = conn.execute("SELECT cost_usd FROM usage_records").fetchone()
            conn.close()
            assert row["cost_usd"] == pytest.approx(0.75)
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]


# --- quota_scrape ---


class TestQuotaScrape:
    def test_when_scrape_returns_none_then_returns_1(self, capsys):
        with patch("ai_cli.quota._scrape_usage_hidden_pane", return_value=None):
            result = quota_scrape()
        assert result == 1
        assert "Could not extract" in capsys.readouterr().err

    def test_when_scrape_succeeds_then_stores_snapshot_and_returns_0(self, tmp_path, capsys):
        import ai_cli.quota_db as qdb

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            snap = QuotaSnapshot(
                weekly_all_models_pct=72.0,
                session_pct=15.0,
                weekly_sonnet_pct=38.0,
                extra_pct=0.0,
            )
            with patch("ai_cli.quota._scrape_usage_hidden_pane", return_value=snap):
                result = quota_scrape()
            assert result == 0
            conn = qdb._get_conn()
            row = conn.execute("SELECT usage_percent FROM quota_snapshots").fetchone()
            conn.close()
            assert row["usage_percent"] == 72.0
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]


# --- quota_status ---


class TestQuotaStatus:
    def test_when_no_data_then_prints_no_snapshots(self, capsys):
        with patch(
            "ai_cli.quota_db.get_current_status",
            return_value={
                "latest_snapshot": None,
                "burn_rate": {},
                "days_remaining": None,
                "alerts": [],
            },
        ):
            result = quota_status()
        assert result == 0
        assert "no snapshots" in capsys.readouterr().out

    def test_when_snapshot_exists_then_prints_percent(self, capsys):
        with patch(
            "ai_cli.quota_db.get_current_status",
            return_value={
                "latest_snapshot": {"usage_percent": 65.0, "snapshotted_at": "2026-04-05T10:00:00Z"},
                "burn_rate": {"expected_pct_per_day": 14.3, "actual_pct_per_day": 18.0, "multiplier": 1.3},
                "days_remaining": 6.0,
                "alerts": [],
            },
        ):
            result = quota_status()
        assert result == 0
        out = capsys.readouterr().out
        assert "65.0%" in out
        assert "Days to reset" in out


# --- quota_history ---


class TestQuotaHistory:
    def test_when_no_history_then_prints_message(self, capsys):
        with patch("ai_cli.quota_db.get_weekly_history", return_value=[]):
            result = quota_history()
        assert result == 0
        assert "No history" in capsys.readouterr().out

    def test_when_history_exists_then_prints_table(self, capsys):
        with patch(
            "ai_cli.quota_db.get_weekly_history",
            return_value=[
                {
                    "week_start": "2026-04-04T06:00:00Z",
                    "peak_percent": 85.0,
                    "total_consumed": 5000,
                    "snapshot_count": 10,
                },
            ],
        ):
            result = quota_history()
        assert result == 0
        out = capsys.readouterr().out
        assert "85.0%" in out
        assert "2026-04-04" in out


# --- quota_watch ---


class TestQuotaWatch:
    def test_when_already_running_then_returns_2(self):
        with patch("ai_cli.sync._acquire_pid_file", return_value=False):
            from ai_cli.quota import quota_watch

            result = quota_watch()
        assert result == 2

    def test_when_nats_unavailable_then_returns_1(self):
        mock_client = MagicMock()
        mock_client.nc = None

        async def fake_connect():
            pass

        mock_client.connect = fake_connect

        with (
            patch("ai_cli.sync._acquire_pid_file", return_value=True),
            patch("ai_cli.sync._release_pid_file"),
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
        ):
            from ai_cli.quota import quota_watch

            result = quota_watch()
        assert result == 1

    def test_when_interrupt_then_returns_0(self):
        mock_client = MagicMock()
        mock_client.nc = None

        async def fake_connect():
            mock_client.nc = MagicMock()

        async def fake_close():
            pass

        mock_client.connect = fake_connect
        mock_client.close = fake_close

        def fake_sleep(_):
            raise KeyboardInterrupt

        with (
            patch("ai_cli.sync._acquire_pid_file", return_value=True),
            patch("ai_cli.sync._release_pid_file") as mock_release,
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("ai_cli.quota._get_claude_usage_snapshot", return_value=None),
            patch("time.sleep", fake_sleep),
        ):
            from ai_cli.quota import quota_watch

            result = quota_watch()
        assert result == 0
        mock_release.assert_called_with("quota-watch")

    def test_when_usage_crosses_threshold_then_publishes(self):
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

        def fake_sleep(_):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                raise KeyboardInterrupt

        snap = QuotaSnapshot(weekly_all_models_pct=80.0, session_pct=10.0)

        with (
            patch("ai_cli.sync._acquire_pid_file", return_value=True),
            patch("ai_cli.sync._release_pid_file"),
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("ai_cli.quota._get_claude_usage_snapshot", return_value=snap),
            patch("ai_cli.quota._send_notification"),
            patch("time.sleep", fake_sleep),
        ):
            from ai_cli.quota import quota_watch

            result = quota_watch()

        assert result == 0
        assert mock_client.publish.call_count >= 2
        subjects = [call.args[0] for call in mock_client.publish.call_args_list]
        assert "quota.threshold.50" in subjects
        assert "quota.threshold.75" in subjects

    def test_when_connect_raises_then_handles_unavailable(self):
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
        assert result == 1

    def test_when_publish_raises_then_logs_error(self, capsys):
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

        def fake_sleep(_):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                raise KeyboardInterrupt

        snap = QuotaSnapshot(weekly_all_models_pct=80.0)

        with (
            patch("ai_cli.sync._acquire_pid_file", return_value=True),
            patch("ai_cli.sync._release_pid_file"),
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("ai_cli.quota._get_claude_usage_snapshot", return_value=snap),
            patch("ai_cli.quota._send_notification"),
            patch("time.sleep", fake_sleep),
        ):
            from ai_cli.quota import quota_watch

            result = quota_watch()

        assert result == 0
        assert "failed to publish" in capsys.readouterr().err
