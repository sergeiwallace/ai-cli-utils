"""Tests for quota tracking — hidden pane scraper, notification, quota_watch, quota_record."""

from unittest.mock import MagicMock, patch

import pytest

from ai_cli.quota import (
    QuotaSnapshot,
    _get_claude_usage_snapshot,
    _parse_usage_output,
    _publish_quota_snapshot,
    _scrape_usage_hidden_pane,
    _send_notification,
    _send_slack_notification,
    quota_record,
    quota_scrape,
    quota_status,
    quota_history,
    quota_statusline_part,
)


# --- _parse_usage_output ---


class TestParseUsageOutput:
    # Actual /usage output format: label on one line, progress bar + % on next line.
    _REAL_USAGE_OUTPUT = (
        "  Current session    \n"
        "  ██                                                 4% used\n\n"
        "  Current week (all models)\n"
        "  █████████████                                      26% used\n\n"
        "  Current week (Sonnet only)\n"
        "  ████████████                                       24% used\n\n"
        "  Extra usage\n"
        "  Extra usage not enabled · /extra-usage to enable\n"
    )

    def test_when_real_multiline_format_then_all_parsed(self):
        snap = _parse_usage_output(self._REAL_USAGE_OUTPUT)
        assert snap is not None
        assert snap.weekly_all_models_pct == 26.0
        assert snap.session_pct == 4.0
        assert snap.weekly_sonnet_pct == 24.0
        assert snap.extra_pct == 0.0

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

    def _make_new_window_result(self, index: str = "3") -> MagicMock:
        r = MagicMock()
        r.returncode = 0
        r.stdout = f"{index}\n"
        return r

    def test_when_cc_prompt_never_appears_then_returns_none(self):
        """No ❯ in capture output → timeout → returns None."""
        no_prompt = self._make_cap_result("Starting claude...\n")
        new_win = self._make_new_window_result("3")
        ok = MagicMock()
        ok.returncode = 0
        killed = []

        def fake_run(cmd, **kwargs):
            if cmd[0] == "tmux" and cmd[1] == "new-window":
                return new_win
            if cmd[0] == "tmux" and cmd[1] == "kill-window":
                killed.append(True)
                return ok
            if cmd[0] == "tmux" and cmd[1] == "capture-pane":
                return no_prompt
            return ok

        with patch("subprocess.run", side_effect=fake_run), patch("time.sleep"):
            result = _scrape_usage_hidden_pane()
        assert result is None
        assert killed, "kill-window must be called on timeout path"

    def test_when_window_index_used_as_capture_target(self):
        """Index-based target (:N) from new-window is used for capture-pane, not =name."""
        new_win = self._make_new_window_result("7")
        ok = MagicMock()
        ok.returncode = 0
        targets_seen = []

        def fake_run(cmd, **kwargs):
            if cmd[0] == "tmux" and cmd[1] == "new-window":
                return new_win
            if cmd[0] == "tmux" and cmd[1] == "capture-pane":
                if "-t" in cmd:
                    targets_seen.append(cmd[cmd.index("-t") + 1])
                return self._make_cap_result("Starting...\n")  # never shows ❯ → timeout
            return ok

        with patch("subprocess.run", side_effect=fake_run), patch("time.sleep"):
            _scrape_usage_hidden_pane()

        assert targets_seen, "capture-pane must be called"
        assert all(t == ":7" for t in targets_seen), f"expected :7, got {targets_seen}"

    def test_when_usage_output_captured_then_returns_snapshot(self):
        """Happy path: prompt appears, /usage output appears, snapshot returned."""
        new_win = self._make_new_window_result("3")
        ok = MagicMock()
        ok.returncode = 0

        prompt_only = self._make_cap_result("❯\n")
        # Use the real multi-line /usage format to verify regex + scraper work together.
        usage_output = TestParseUsageOutput._REAL_USAGE_OUTPUT
        cap_with_usage = self._make_cap_result(usage_output)

        call_count = 0

        def fake_run(cmd, **kwargs):
            nonlocal call_count
            if cmd[0] == "tmux" and cmd[1] == "new-window":
                return new_win
            if cmd[0] == "tmux" and cmd[1] == "capture-pane":
                call_count += 1
                if call_count <= 1:
                    return prompt_only
                return cap_with_usage
            return ok

        with patch("subprocess.run", side_effect=fake_run), patch("time.sleep"):
            result = _scrape_usage_hidden_pane()

        assert isinstance(result, QuotaSnapshot)
        assert result.session_pct == 4.0
        assert result.weekly_all_models_pct == 26.0
        assert result.weekly_sonnet_pct == 24.0
        assert result.extra_pct == 0.0

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


# --- _get_claude_usage_snapshot ---


class TestGetClaudeUsageSnapshot:
    def test_when_scraper_returns_snapshot_then_returns_it(self):
        snap = QuotaSnapshot(weekly_all_models_pct=72.0, session_pct=10.0, weekly_sonnet_pct=30.0, extra_pct=0.0)
        with patch("ai_cli.quota._scrape_usage_hidden_pane", return_value=snap):
            result = _get_claude_usage_snapshot()
        assert result is snap

    def test_when_scraper_returns_none_then_returns_none(self):
        with patch("ai_cli.quota._scrape_usage_hidden_pane", return_value=None):
            result = _get_claude_usage_snapshot()
        assert result is None


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

    def test_when_load_config_raises_then_falls_back_to_notify_send(self):
        """lines 223-224: load_config exception → webhook_url="" → uses notify-send."""
        snap = self._make_snapshot(78.5)
        with (
            patch("ai_cli.main.load_config", side_effect=Exception("no config")),
            patch("subprocess.run") as mock_run,
        ):
            _send_notification(75, snap)
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0][0] == "notify-send"


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
            with (
                patch("ai_cli.quota._scrape_usage_hidden_pane", return_value=snap),
                patch("ai_cli.quota._publish_quota_snapshot") as mock_publish,
            ):
                result = quota_scrape()
            assert result == 0
            conn = qdb._get_conn()
            row = conn.execute("SELECT usage_percent FROM quota_snapshots").fetchone()
            conn.close()
            assert row["usage_percent"] == 72.0
            mock_publish.assert_called_once_with(snap)
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]


# --- _publish_quota_snapshot ---


class TestPublishQuotaSnapshot:
    def _make_mock_client(self, connected: bool = True):
        mock_client = MagicMock()
        mock_client.nc = MagicMock() if connected else None

        async def fake_connect():
            if connected:
                mock_client.nc = MagicMock()

        async def fake_publish(subject, payload):
            pass

        async def fake_close():
            pass

        mock_client.connect = fake_connect
        mock_client.publish = MagicMock(side_effect=fake_publish)
        mock_client.close = fake_close
        return mock_client

    def test_when_nats_available_then_publishes_snapshot(self):
        snap = QuotaSnapshot(weekly_all_models_pct=55.0, session_pct=10.0, weekly_sonnet_pct=30.0, extra_pct=0.0)
        mock_client = self._make_mock_client(connected=True)
        with patch("ai_cli.messaging.NATSClient", return_value=mock_client):
            _publish_quota_snapshot(snap)
        mock_client.publish.assert_called_once()
        subject, payload = mock_client.publish.call_args[0]
        assert subject == "quota.snapshot"
        assert payload["usage_percent"] == 55.0
        assert payload["session_pct"] == 10.0
        assert payload["weekly_sonnet_pct"] == 30.0
        assert payload["extra_pct"] == 0.0
        assert "ts" in payload

    def test_when_nats_unavailable_then_no_exception(self):
        snap = QuotaSnapshot(weekly_all_models_pct=28.0)
        mock_client = self._make_mock_client(connected=False)
        with patch("ai_cli.messaging.NATSClient", return_value=mock_client):
            _publish_quota_snapshot(snap)  # must not raise
        mock_client.publish.assert_not_called()

    def test_when_connect_raises_then_no_exception(self):
        snap = QuotaSnapshot(weekly_all_models_pct=28.0)
        mock_client = MagicMock()

        async def fake_connect_raises():
            raise ConnectionRefusedError("no server")

        async def fake_close():
            pass

        mock_client.connect = fake_connect_raises
        mock_client.close = fake_close
        with patch("ai_cli.messaging.NATSClient", return_value=mock_client):
            _publish_quota_snapshot(snap)  # must not raise


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

    def test_when_alerts_present_then_prints_them(self, capsys):
        """line 294: alerts list in status data gets printed."""
        status_data = {
            "latest_snapshot": {"usage_percent": 78.5, "snapshotted_at": "2026-04-04T06:00:00Z"},
            "burn_rate": {},
            "days_remaining": None,
            "alerts": ["Approaching weekly limit", "Consider slowing down"],
        }
        with patch("ai_cli.quota_db.get_current_status", return_value=status_data):
            result = quota_status()
        assert result == 0
        out = capsys.readouterr().out
        assert "Approaching weekly limit" in out
        assert "Consider slowing down" in out


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


# --- quota_statusline_part ---


class TestQuotaStatuslinePart:
    def test_when_no_snapshot_then_returns_0_and_no_output(self, tmp_path, capsys):
        import ai_cli.quota_db as qdb

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            result = quota_statusline_part()
            assert result == 0
            assert capsys.readouterr().out.strip() == ""
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_when_under_pace_then_shows_green_icon(self, tmp_path, capsys):
        """delta < -5 (usage well below expected) → ✅ icon."""
        import ai_cli.quota_db as qdb

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            # Snapshot at 5% but week is ~50% elapsed → delta ≈ -45
            qdb.record_quota_snapshot(usage_percent=5.0)
            result = quota_statusline_part()
            assert result == 0
            out = capsys.readouterr().out
            assert "5%" in out
            assert "✅" in out
            assert "↓" in out
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_when_over_pace_then_shows_alert_icon(self, tmp_path, capsys):
        """delta > +5 (usage above expected pace) → 🚨 icon."""
        import ai_cli.quota_db as qdb

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            # Snapshot at 95% — always over pace unless week is nearly done
            qdb.record_quota_snapshot(usage_percent=95.0)
            result = quota_statusline_part()
            assert result == 0
            out = capsys.readouterr().out
            assert "95%" in out
            assert "🚨" in out
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_when_db_error_then_returns_0_no_crash(self, tmp_path, capsys):
        """Exception in DB read must not propagate — statusline must never crash CC."""
        import ai_cli.quota_db as qdb

        qdb.set_db_path(tmp_path / "nonexistent" / "quota.db")
        try:
            result = quota_statusline_part()
            assert result == 0
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]
