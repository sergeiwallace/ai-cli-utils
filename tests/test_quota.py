"""Tests for quota tracking — hidden pane scraper, notification, quota_watch, quota_record."""

import os
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from ai_cli.quota import (
    QuotaSnapshot,
    _get_claude_usage_snapshot,
    _maybe_trigger_background_scrape,
    _parse_reset_datetime,
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
    quota_sync_from_remote,
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

    def test_when_scanning_in_progress_then_returns_none(self):
        """Reject output while the local-session scan is still running.

        In CC v2.1.112+, "Scanning local sessions…" is accompanied by the
        "does not include other devices" disclaimer.  strict=True rejects via
        the disclaimer; strict=False rejects via the Scanning guard.
        """
        output = (
            "  Current week (all models)\n"
            "  █                                                  1% used\n\n"
            "  Approximate, based on local sessions on this \n"
            "  machine — does not include other devices or \n"
            "  API usage\n"
            "  Scanning local sessions…\n"
        )
        assert _parse_usage_output(output) is None  # strict=True rejects via disclaimer
        assert _parse_usage_output(output, strict=False) is None  # non-strict rejects via Scanning

    def test_when_strict_and_scanning_without_disclaimer_then_parsed(self):
        """strict=True parses valid data even when 'Scanning' is in scrollback.

        On some hosts the pane retains the 'Scanning local sessions' message in
        scrollback alongside already-rendered API data.  strict=True should not
        reject this case — only the 'does not include other devices' disclaimer
        signals a local-session estimate in strict mode.
        """
        output = (
            "  Current week (all models)\n"
            "  ████████████████   4% used\n\n"
            "  Scanning local sessions…\n"  # residual scrollback — real data already above
        )
        snap = _parse_usage_output(output)  # strict=True
        assert snap is not None
        assert snap.weekly_all_models_pct == 4.0

    def test_when_strict_and_disclaimer_present_then_returns_none(self):
        """In strict mode, local-session estimates (with disclaimer) are rejected.

        The "does not include other devices" disclaimer signals that the Anthropic
        quota API was unreachable and CC fell back to a local-session count.  In
        strict mode this is rejected so only real account-wide data is stored.
        """
        output = (
            "  Current week (all models)\n"
            "  █                                                  1% used\n\n"
            "  Approximate, based on local sessions on this \n"
            "  machine — does not include other devices or \n"
            "  API usage\n"
        )
        assert _parse_usage_output(output) is None  # strict=True by default
        assert _parse_usage_output(output, strict=True) is None

    def test_when_non_strict_and_disclaimer_present_then_parsed(self):
        """strict=False accepts local-session estimates as a fallback."""
        output = (
            "  Current week (all models)\n"
            "  █                                                  3% used\n\n"
            "  Approximate, based on local sessions on this \n"
            "  machine — does not include other devices or claude.ai\n"
        )
        snap = _parse_usage_output(output, strict=False)
        assert snap is not None
        assert snap.weekly_all_models_pct == 3.0

    def test_when_non_strict_and_scanning_then_still_returns_none(self):
        """strict=False still rejects output while scanning is in progress."""
        output = (
            "  Current week (all models)\n"
            "  █                                                  1% used\n\n"
            "  Scanning local sessions…\n"
            "  does not include other devices\n"
        )
        assert _parse_usage_output(output, strict=False) is None

    def test_when_real_account_data_without_disclaimer_then_parsed(self):
        """Real API data (no disclaimer) is always accepted."""
        output = "  Current week (all models)\n  ████████████████   42% used\n\n"
        snap = _parse_usage_output(output)
        assert snap is not None
        assert snap.weekly_all_models_pct == 42.0


# --- _parse_reset_datetime ---


class TestParseResetDatetime:
    # --- Real CC format: time-only label embedded in week row ---

    def test_when_real_cc_format_with_minutes_then_returns_future_utc(self):
        # "Current week (all models) · Resets 6:59am" — no date, no timezone
        # The minute component (:59) must be preserved; UTC hour varies by local TZ.
        text = "  Current week (all models) · Resets 6:59am \n  65% used\n"
        result = _parse_reset_datetime(text)
        assert result is not None
        # Minutes must be preserved regardless of local timezone
        assert ":59:00Z" in result
        # Must be in the future
        from datetime import datetime, timezone

        assert datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)

    def test_when_real_cc_format_hour_only_pm_then_returns_future_utc(self):
        # "Resets 11pm" — no minutes, no date, no timezone
        text = "  Current week (all models) · Resets 11pm \n  65% used\n"
        result = _parse_reset_datetime(text)
        assert result is not None
        # Local midnight → UTC time should be in the future
        from datetime import datetime, timezone

        assert datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)

    def test_when_no_all_models_line_then_returns_none(self):
        # "Current week (Sonnet only) · Resets 11pm" — not the all-models row, skip it
        text = "  Current week (Sonnet only) · Resets 11pm \n  78% used\n"
        assert _parse_reset_datetime(text) is None

    def test_when_no_reset_label_then_returns_none(self):
        text = "Current week (all models): 64% used\n"
        assert _parse_reset_datetime(text) is None

    # --- Fallback: standalone full date+time line (future-proofing) ---

    def test_when_full_format_with_year_and_est_then_converts_to_utc(self):
        text = "Resets April 18, 2026 at 6:59 AM EST"
        result = _parse_reset_datetime(text)
        assert result == "2026-04-18T11:59:00Z"

    def test_when_full_format_with_edt_then_converts_to_utc(self):
        text = "Resets April 18, 2026 at 6:59 AM EDT"
        result = _parse_reset_datetime(text)
        assert result == "2026-04-18T10:59:00Z"

    def test_when_utc_timezone_then_no_offset(self):
        text = "Resets April 18, 2026 at 11:59 PM UTC"
        result = _parse_reset_datetime(text)
        assert result == "2026-04-18T23:59:00Z"

    # --- CC v2.1.112 IANA timezone format ---

    def test_when_iana_format_with_hour_only_then_converts_to_utc(self):
        # "Resets Apr 23 at 3pm (America/New_York)" — CC v2.1.112 format
        # Apr 23 is in EDT (UTC-4), so 3pm EDT = 19:00 UTC.
        text = "Resets Apr 23 at 3pm (America/New_York)            3% used"
        result = _parse_reset_datetime(text)
        assert result is not None
        assert result == "2026-04-23T19:00:00Z"

    def test_when_iana_format_with_minutes_then_converts_to_utc(self):
        # "Resets Apr 23 at 3:30pm (America/New_York)"
        text = "Resets Apr 23 at 3:30pm (America/New_York)"
        result = _parse_reset_datetime(text)
        assert result is not None
        assert result == "2026-04-23T19:30:00Z"

    def test_when_iana_format_utc_then_no_offset(self):
        text = "Resets Apr 23 at 7pm (UTC)"
        result = _parse_reset_datetime(text)
        assert result is not None
        assert result == "2026-04-23T19:00:00Z"

    # --- Integration: _parse_usage_output ---

    def test_parse_usage_output_captures_reset_at_from_real_format(self):
        output = (
            "  Current week (all models) · Resets 6:59am \n"
            "  ████████████████   65% used\n\n"
            "  Current week (Sonnet only) · Resets 11pm \n"
            "  78% used\n"
        )
        snap = _parse_usage_output(output)
        assert snap is not None
        assert snap.reset_at is not None
        # Must be a valid UTC ISO string in the future
        from datetime import datetime, timezone

        dt = datetime.strptime(snap.reset_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        assert dt > datetime.now(timezone.utc)

    def test_parse_usage_output_reset_at_none_when_absent(self):
        output = "Current week (all models): 64% used\n"
        snap = _parse_usage_output(output)
        assert snap is not None
        assert snap.reset_at is None

    def test_parse_usage_output_captures_reset_at_from_v2112_format(self):
        """CC v2.1.112 format: IANA timezone in parens, reset date on separate line."""
        output = (
            "  Current week (all models)\n"
            "  Resets Apr 23 at 3pm (America/New_York)            3% used\n"
            "  Current week (Sonnet only)\n"
            "  Resets Apr 23 at 3pm (America/New_York)            5% used\n"
        )
        snap = _parse_usage_output(output, strict=False)
        assert snap is not None
        assert snap.weekly_all_models_pct == 3.0
        assert snap.reset_at == "2026-04-23T19:00:00Z"


# --- _parse_reset_datetime anchor persistence ---


class TestResetAnchorPersistence:
    def test_when_record_snapshot_with_reset_at_then_anchor_file_written(self, tmp_path):
        import ai_cli.quota_db as qdb

        anchor_file = tmp_path / "quota-reset-anchor.txt"
        qdb.set_db_path(tmp_path / "quota.db")
        try:
            with patch.object(qdb, "_get_reset_anchor_path", return_value=anchor_file):
                qdb.record_quota_snapshot(
                    usage_percent=64.0,
                    reset_at="2026-04-18T11:59:00Z",
                )
            assert anchor_file.exists()
            assert anchor_file.read_text().strip() == "2026-04-18T11:59:00Z"
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_when_anchor_file_exists_then_get_reset_anchor_uses_it(self, tmp_path):
        import ai_cli.quota_db as qdb

        anchor_file = tmp_path / "quota-reset-anchor.txt"
        anchor_file.write_text("2026-04-18T11:59:00Z")
        with patch.object(qdb, "_get_reset_anchor_path", return_value=anchor_file):
            anchor = qdb._get_reset_anchor_utc()
        assert anchor.year == 2026
        assert anchor.month == 4
        assert anchor.day == 18
        assert anchor.hour == 11
        assert anchor.minute == 59

    def test_when_anchor_file_absent_then_falls_back_to_default(self, tmp_path):
        import ai_cli.quota_db as qdb

        missing = tmp_path / "no-anchor.txt"
        with patch.object(qdb, "_get_reset_anchor_path", return_value=missing):
            with patch("ai_cli.quota_db.load_config", side_effect=Exception("no config"), create=True):
                anchor = qdb._get_reset_anchor_utc()
        # Should return the default anchor, not raise
        assert anchor is not None

    def test_when_anchor_is_stale_then_week_start_advances_from_anchor(self, tmp_path):
        """Stale anchor (reset already passed) must not push week_start back a full week.

        This is the root cause of the remote statusline disappearing: statusline-command.sh
        inline Python computed week_start = anchor - 7 days even when anchor was in the past,
        producing a week_start one week before the stored snapshots.
        """
        import ai_cli.quota_db as qdb
        from datetime import datetime, timezone

        # Anchor is April 11 10:59 UTC — the reset that ALREADY OCCURRED
        stale_anchor = "2026-04-11T10:59:00Z"
        anchor_file = tmp_path / "quota-reset-anchor.txt"
        anchor_file.write_text(stale_anchor)
        # Simulate "now" = April 13 02:00 UTC — two days after the reset
        now = datetime(2026, 4, 13, 2, 0, 0, tzinfo=timezone.utc)
        with patch.object(qdb, "_get_reset_anchor_path", return_value=anchor_file):
            week_start = qdb._get_current_week_start(now)
        # Week started AT the reset anchor, not one week before it
        assert week_start == "2026-04-11T10:59:00Z"

    def test_when_record_snapshot_without_reset_at_then_anchor_file_unchanged(self, tmp_path):
        import ai_cli.quota_db as qdb

        anchor_file = tmp_path / "quota-reset-anchor.txt"
        anchor_file.write_text("2026-04-18T11:59:00Z")
        qdb.set_db_path(tmp_path / "quota.db")
        try:
            with patch.object(qdb, "_get_reset_anchor_path", return_value=anchor_file):
                qdb.record_quota_snapshot(usage_percent=50.0)  # no reset_at
            assert anchor_file.read_text().strip() == "2026-04-18T11:59:00Z"  # unchanged
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]


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

    def test_when_real_data_arrives_late_then_preferred_over_local_estimate(self):
        """Scraper prefers real API data over local-session estimate.

        On geo-restricted hosts, real Anthropic API data takes 25-35s to load.
        Until then /usage shows a local-sessions-only estimate (with disclaimer).
        The scraper saves the local estimate as a fallback but keeps polling and
        uses the real data when it arrives.
        """
        new_win = self._make_new_window_result("5")
        ok = MagicMock()
        ok.returncode = 0

        prompt_output = self._make_cap_result("❯\n")
        # Local-sessions estimate (disclaimer present, no "Scanning").
        local_only_output = self._make_cap_result(
            "Current week (all models): 1% used\ndoes not include other devices\n"
        )
        # Real API data arrives later (no disclaimer).
        real_output = self._make_cap_result(TestParseUsageOutput._REAL_USAGE_OUTPUT)

        call_count = 0

        def fake_run(cmd, **kwargs):
            nonlocal call_count
            if cmd[0] == "tmux" and cmd[1] == "new-window":
                return new_win
            if cmd[0] == "tmux" and cmd[1] == "capture-pane":
                call_count += 1
                if call_count == 1:
                    return prompt_output  # startup poll
                if call_count <= 5:
                    return local_only_output  # early polls: local-sessions estimate
                return real_output  # later poll: real API data (no disclaimer)
            return ok

        with patch("subprocess.run", side_effect=fake_run), patch("time.sleep"):
            result = _scrape_usage_hidden_pane()

        assert isinstance(result, QuotaSnapshot), "should return a snapshot"
        assert result.weekly_all_models_pct == 26.0, "should use real API data, not local 1%"

    def test_when_only_local_estimate_available_then_falls_back_to_it(self):
        """Scraper falls back to local-session estimate when API data never loads.

        On machines where the quota API never responds in a hidden pane session
        (e.g. Mac hidden pane sessions), the scraper exhausts its poll budget and
        returns the best local-session estimate seen instead of None.
        """
        new_win = self._make_new_window_result("6")
        ok = MagicMock()
        ok.returncode = 0

        prompt_output = self._make_cap_result("❯\n")
        # Local-sessions estimate that never transitions to real API data.
        local_only_output = self._make_cap_result(
            "Current week (all models)\n"
            "Resets Apr 23 at 3pm (America/New_York)   3% used\n"
            "does not include other devices or claude.ai\n"
        )

        call_count = 0

        def fake_run(cmd, **kwargs):
            nonlocal call_count
            if cmd[0] == "tmux" and cmd[1] == "new-window":
                return new_win
            if cmd[0] == "tmux" and cmd[1] == "capture-pane":
                call_count += 1
                if call_count == 1:
                    return prompt_output  # startup poll
                return local_only_output  # all subsequent polls: local only
            return ok

        with patch("subprocess.run", side_effect=fake_run), patch("time.sleep"):
            result = _scrape_usage_hidden_pane()

        assert isinstance(result, QuotaSnapshot), "should fall back to local estimate, not None"
        assert result.weekly_all_models_pct == 3.0, "should return the local session value"

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

    def test_window_size_latest_restored_after_resize(self):
        """resize-window sets window-size=manual as a tmux side effect.

        The scraper must call `set-option window-size latest` immediately after
        resize-window so that iTerm2 pane resize keeps working in the host session.
        """
        new_win = self._make_new_window_result("3")
        ok = MagicMock()
        ok.returncode = 0
        cmds_seen = []

        def fake_run(cmd, **kwargs):
            if isinstance(cmd, list) and cmd[0] == "tmux":
                cmds_seen.append(cmd[1:])
            if cmd[0] == "tmux" and cmd[1] == "new-window":
                return new_win
            return ok

        with patch("subprocess.run", side_effect=fake_run), patch("time.sleep"):
            _scrape_usage_hidden_pane()

        resize_idx = next((i for i, c in enumerate(cmds_seen) if c[0] == "resize-window"), None)
        restore_idx = next(
            (i for i, c in enumerate(cmds_seen) if c[:3] == ["set-option", "window-size", "latest"]),
            None,
        )
        assert resize_idx is not None, "resize-window must be called"
        assert restore_idx is not None, "set-option window-size latest must be called"
        assert restore_idx == resize_idx + 1, "set-option window-size latest must follow resize-window immediately"


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

    def test_when_scrape_fails_then_lock_file_cleaned_up(self, tmp_path):
        """quota_scrape must always remove the lock file, even on failure."""
        lock_path = tmp_path / "quota-scrape.lock"
        lock_path.touch()
        with (
            patch("ai_cli.quota._SCRAPE_LOCK_PATH", lock_path),
            patch("ai_cli.quota._scrape_usage_hidden_pane", return_value=None),
        ):
            quota_scrape()
        assert not lock_path.exists()

    def test_when_scrape_succeeds_then_lock_file_cleaned_up(self, tmp_path, capsys):
        """quota_scrape removes the lock file on success too."""
        import ai_cli.quota_db as qdb

        qdb.set_db_path(tmp_path / "quota.db")
        lock_path = tmp_path / "quota-scrape.lock"
        lock_path.touch()
        try:
            snap = QuotaSnapshot(weekly_all_models_pct=50.0)
            with (
                patch("ai_cli.quota._SCRAPE_LOCK_PATH", lock_path),
                patch("ai_cli.quota._scrape_usage_hidden_pane", return_value=snap),
                patch("ai_cli.quota._publish_quota_snapshot"),
            ):
                quota_scrape()
            assert not lock_path.exists()
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]


# --- _publish_quota_snapshot ---


class TestPublishQuotaSnapshot:
    def _make_mock_client(self, connected: bool = True):
        mock_client = MagicMock()
        mock_client.nc = MagicMock() if connected else None
        mock_client.js = None  # No JetStream by default — KV write path skipped

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

    def _make_mock_client_with_js(self, connected: bool = True):
        """Client with JetStream + KV mock for testing the quota.claude.current write."""
        mock_client = self._make_mock_client(connected=connected)
        mock_kv = MagicMock()
        kv_put_calls = []

        async def fake_kv_put(key, value):
            kv_put_calls.append((key, value))

        async def fake_key_value(bucket):
            return mock_kv

        mock_kv.put = MagicMock(side_effect=fake_kv_put)
        mock_kv._put_calls = kv_put_calls
        mock_client.js = MagicMock()
        mock_client.js.key_value = MagicMock(side_effect=fake_key_value)
        return mock_client, mock_kv

    def test_when_nats_available_then_publishes_snapshot(self):
        snap = QuotaSnapshot(weekly_all_models_pct=55.0, session_pct=10.0, weekly_sonnet_pct=30.0, extra_pct=0.0)
        mock_client = self._make_mock_client(connected=True)
        with patch("ai_cli.messaging.NATSClient", return_value=mock_client):
            _publish_quota_snapshot(snap)
        subject, payload = mock_client.publish.call_args_list[0][0]
        assert subject == "quota.snapshot"
        assert payload["usage_percent"] == 55.0
        assert payload["session_pct"] == 10.0
        assert payload["weekly_sonnet_pct"] == 30.0
        assert payload["extra_pct"] == 0.0
        assert "ts" in payload

    def test_when_snapshot_has_reset_at_then_included_in_payload(self):
        """reset_at must be included so receiving machines can update their
        week-start anchor — without it, the receiving machine's week_start
        diverges and Mac quota snapshots end up in a different DB bucket."""
        snap = QuotaSnapshot(
            weekly_all_models_pct=22.0,
            reset_at="2026-04-18T09:59:00Z",
        )
        mock_client = self._make_mock_client(connected=True)
        with patch("ai_cli.messaging.NATSClient", return_value=mock_client):
            _publish_quota_snapshot(snap)
        _, payload = mock_client.publish.call_args_list[0][0]
        assert payload["reset_at"] == "2026-04-18T09:59:00Z"

    def test_when_snapshot_has_no_reset_at_then_payload_has_none(self):
        snap = QuotaSnapshot(weekly_all_models_pct=10.0, reset_at=None)
        mock_client = self._make_mock_client(connected=True)
        with patch("ai_cli.messaging.NATSClient", return_value=mock_client):
            _publish_quota_snapshot(snap)
        _, payload = mock_client.publish.call_args_list[0][0]
        assert "reset_at" in payload
        assert payload["reset_at"] is None

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

    def test_when_js_available_then_writes_snapshot_to_kv(self):
        snap = QuotaSnapshot(weekly_all_models_pct=42.0, session_pct=5.0, weekly_sonnet_pct=20.0, extra_pct=1.0)
        mock_client, mock_kv = self._make_mock_client_with_js(connected=True)
        with patch("ai_cli.messaging.NATSClient", return_value=mock_client):
            _publish_quota_snapshot(snap)
        mock_kv.put.assert_called_once()
        key, value = mock_kv.put.call_args[0]
        assert key == "quota.claude.current"
        import json as _json

        written = _json.loads(value.decode())
        assert written["usage_percent"] == 42.0
        assert written["session_pct"] == 5.0
        assert written["weekly_sonnet_pct"] == 20.0

    def test_when_kv_write_raises_then_publish_still_completes(self):
        snap = QuotaSnapshot(weekly_all_models_pct=30.0)
        mock_client, mock_kv = self._make_mock_client_with_js(connected=True)

        async def fake_kv_put_raises(key, value):
            raise RuntimeError("kv unavailable")

        mock_kv.put = MagicMock(side_effect=fake_kv_put_raises)
        with patch("ai_cli.messaging.NATSClient", return_value=mock_client):
            _publish_quota_snapshot(snap)  # must not raise
        # Both publishes (quota.snapshot + hw.events.usage.claude.snapshot) completed
        # despite KV failure.
        assert mock_client.publish.call_count == 2

    def test_when_js_available_then_kv_key_is_quota_claude_current(self):
        """KV key must be quota.claude.current (renamed from the older
        quota.claude.weekly) so consumers read the latest snapshot from a
        single canonical key."""
        snap = QuotaSnapshot(weekly_all_models_pct=33.0)
        mock_client, mock_kv = self._make_mock_client_with_js(connected=True)
        with patch("ai_cli.messaging.NATSClient", return_value=mock_client):
            _publish_quota_snapshot(snap)
        key, _ = mock_kv.put.call_args[0]
        assert key == "quota.claude.current"
        # Regression guard: the old key name must not be written.
        written_keys = [call.args[0] for call in mock_kv.put.call_args_list]
        assert "quota.claude.weekly" not in written_keys

    def test_when_publish_then_also_publishes_to_humanware_subject(self, monkeypatch):
        """The second publish call targets hw.events.usage.claude.snapshot with a
        UsageConsumer-compatible payload shape (id, machine, used_pct, raw, ...)."""
        monkeypatch.setenv("AI_CLI_HOST", "hetzner")
        snap = QuotaSnapshot(weekly_all_models_pct=77.5, reset_at="2026-04-18T09:59:00Z")
        mock_client = self._make_mock_client(connected=True)
        with patch("ai_cli.messaging.NATSClient", return_value=mock_client):
            _publish_quota_snapshot(snap)

        subjects = [call.args[0] for call in mock_client.publish.call_args_list]
        assert "hw.events.usage.claude.snapshot" in subjects

        hw_call = next(
            call for call in mock_client.publish.call_args_list if call.args[0] == "hw.events.usage.claude.snapshot"
        )
        hw_payload = hw_call.args[1]
        assert hw_payload["machine"] == "hetzner"
        assert hw_payload["used_pct"] == 77.5
        assert hw_payload["tokens_used"] is None
        assert hw_payload["tokens_limit"] is None
        assert hw_payload["reset_at"] == "2026-04-18T09:59:00Z"
        assert hw_payload["scraped_at"].endswith("Z")
        assert isinstance(hw_payload["id"], str) and len(hw_payload["id"]) > 0
        assert isinstance(hw_payload["raw"], str)  # JSON-serialized original payload
        import json as _json

        raw_parsed = _json.loads(hw_payload["raw"])
        assert raw_parsed["usage_percent"] == 77.5


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
    def test_when_no_snapshot_then_shows_placeholder_and_triggers_scrape(self, tmp_path, capsys):
        """No data for current week → shows '📊 -' placeholder and launches a background scrape."""
        import ai_cli.quota_db as qdb
        import ai_cli.quota as q

        qdb.set_db_path(tmp_path / "quota.db")
        # Initialize the DB schema without inserting any snapshots
        qdb._get_conn().close()
        q._SCRAPE_LOCK_PATH.unlink(missing_ok=True)
        try:
            with patch("subprocess.Popen") as mock_popen:
                result = quota_statusline_part()
            assert result == 0
            out = capsys.readouterr().out
            assert "📊" in out
            assert "-" in out
            # Background scrape should have been launched
            mock_popen.assert_called_once()
            assert "scrape" in mock_popen.call_args[0][0]
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]
            q._SCRAPE_LOCK_PATH.unlink(missing_ok=True)

    def test_when_under_pace_then_shows_green_icon_and_steady_arrow(self, tmp_path, capsys):
        """delta < -5 with single snapshot → ✅ icon, → arrow (insufficient data for acceleration)."""
        import ai_cli.quota_db as qdb

        # Pin `now` to 50% through the billing week so week_elapsed_pct ≈ 50%
        # and delta = 5 - 50 = -45 << -5 regardless of when the test actually runs.
        week_start_str = qdb._get_current_week_start()
        week_start_dt = datetime.strptime(week_start_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        fixed_now = week_start_dt + timedelta(days=3, hours=12)  # 50% of 7 days

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            with patch("datetime.datetime") as MockDT:
                MockDT.now.return_value = fixed_now
                MockDT.strptime.side_effect = datetime.strptime
                MockDT.fromisoformat.side_effect = datetime.fromisoformat
                qdb.record_quota_snapshot(usage_percent=5.0)
                result = quota_statusline_part()
            assert result == 0
            out = capsys.readouterr().out
            assert "5%" in out
            assert "✅" in out
            assert "→" in out  # steady: only 1 snapshot, no acceleration data
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_when_over_pace_then_shows_alert_icon(self, tmp_path, capsys):
        """delta > +5 in normal phase (≥24h elapsed) → 🚨 icon."""
        import ai_cli.quota_db as qdb

        # Pin `now` to 25h into the week (post-seedling) so normal-phase bands apply.
        # week_elapsed_pct ≈ 14.9%, delta = 95 - 14.9 ≈ 80 >> 5 → 🚨
        week_start_str = qdb._get_current_week_start()
        week_start_dt = datetime.strptime(week_start_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        fixed_now = week_start_dt + timedelta(hours=25)

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            with patch("datetime.datetime") as MockDT:
                MockDT.now.return_value = fixed_now
                MockDT.strptime.side_effect = datetime.strptime
                MockDT.fromisoformat.side_effect = datetime.fromisoformat
                qdb.record_quota_snapshot(usage_percent=95.0)
                result = quota_statusline_part()
            assert result == 0
            out = capsys.readouterr().out
            assert "95%" in out
            assert "🚨" in out
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_when_seedling_calm_then_shows_leaf_and_blue_delta(self, tmp_path, capsys):
        """First 24h, delta < 10% → 🌱 icon, no alert, delta shown (blue = no ANSI color assertion needed)."""
        import ai_cli.quota_db as qdb

        week_start_str = qdb._get_current_week_start()
        week_start_dt = datetime.strptime(week_start_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        fixed_now = week_start_dt + timedelta(hours=6)  # 6h in — seedling, delta = 5-3.6 = 1.4%

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            with patch("datetime.datetime") as MockDT:
                MockDT.now.return_value = fixed_now
                MockDT.strptime.side_effect = datetime.strptime
                MockDT.fromisoformat.side_effect = datetime.fromisoformat
                qdb.record_quota_snapshot(usage_percent=5.0)
                result = quota_statusline_part()
            assert result == 0
            out = capsys.readouterr().out
            assert "🌱" in out
            assert "✅" not in out
            assert "🚨" not in out

        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_when_seedling_elevated_then_shows_leaf_no_alarm(self, tmp_path, capsys):
        """First 24h: 🌱 always shown regardless of delta — no alarms during seedling phase."""
        import ai_cli.quota_db as qdb

        week_start_str = qdb._get_current_week_start()
        week_start_dt = datetime.strptime(week_start_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        fixed_now = week_start_dt + timedelta(hours=6)  # elapsed ~3.6%, usage 18% → delta ≈ 14%

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            with patch("datetime.datetime") as MockDT:
                MockDT.now.return_value = fixed_now
                MockDT.strptime.side_effect = datetime.strptime
                MockDT.fromisoformat.side_effect = datetime.fromisoformat
                qdb.record_quota_snapshot(usage_percent=18.0)
                result = quota_statusline_part()
            assert result == 0
            out = capsys.readouterr().out
            assert "🌱" in out
            assert "⚠️" not in out
            assert "🚨" not in out
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_when_seedling_high_delta_then_shows_leaf_no_alarm(self, tmp_path, capsys):
        """First 24h, even high delta → 🌱 with no alarm icon (informational only)."""
        import ai_cli.quota_db as qdb

        week_start_str = qdb._get_current_week_start()
        week_start_dt = datetime.strptime(week_start_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        fixed_now = week_start_dt + timedelta(hours=6)  # elapsed ~3.6%, usage 30% → delta ≈ 26%

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            with patch("datetime.datetime") as MockDT:
                MockDT.now.return_value = fixed_now
                MockDT.strptime.side_effect = datetime.strptime
                MockDT.fromisoformat.side_effect = datetime.fromisoformat
                qdb.record_quota_snapshot(usage_percent=30.0)
                result = quota_statusline_part()
            assert result == 0
            out = capsys.readouterr().out
            assert "🌱" in out
            assert "⚠️" not in out
            assert "🚨" not in out
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_when_accelerating_then_shows_up_arrow(self, tmp_path, capsys):
        """Three snapshots with increasing burn rate → ↑ arrow."""
        import ai_cli.quota_db as qdb
        from datetime import datetime, timezone, timedelta

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            conn = qdb._get_conn()
            now = datetime.now(timezone.utc)
            week_start = qdb._get_current_week_start(now)
            # Snapshots spaced 30 min apart: slow burn then fast burn
            # t2 (oldest): 20% at -60min, t1: 21% at -30min (rate=2%/hr), t0: 23% at now (rate=4%/hr)
            # accel = 4 - 2 = 2 %/hr > 1.0 → ↑
            for mins_ago, pct in [(60, 20.0), (30, 21.0), (0, 23.0)]:
                ts = (now - timedelta(minutes=mins_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
                conn.execute(
                    "INSERT INTO quota_snapshots (week_start, usage_percent, snapshotted_at) VALUES (?,?,?)",
                    (week_start, pct, ts),
                )
            conn.commit()
            conn.close()
            result = quota_statusline_part()
            assert result == 0
            out = capsys.readouterr().out
            assert "↑" in out
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_when_decelerating_then_shows_down_arrow(self, tmp_path, capsys):
        """Three snapshots with decreasing burn rate → ↓ arrow."""
        import ai_cli.quota_db as qdb
        from datetime import datetime, timezone, timedelta

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            conn = qdb._get_conn()
            now = datetime.now(timezone.utc)
            week_start = qdb._get_current_week_start(now)
            # t2: 20% at -60min, t1: 23% at -30min (rate=6%/hr), t0: 24% at now (rate=2%/hr)
            # accel = 2 - 6 = -4 %/hr < -1.0 → ↓
            for mins_ago, pct in [(60, 20.0), (30, 23.0), (0, 24.0)]:
                ts = (now - timedelta(minutes=mins_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
                conn.execute(
                    "INSERT INTO quota_snapshots (week_start, usage_percent, snapshotted_at) VALUES (?,?,?)",
                    (week_start, pct, ts),
                )
            conn.commit()
            conn.close()
            result = quota_statusline_part()
            assert result == 0
            out = capsys.readouterr().out
            assert "↓" in out
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

    def test_when_snapshot_stale_then_shows_clock_indicator(self, tmp_path, capsys):
        """Snapshot >2h old → ⏱ stale indicator appended to output."""
        import ai_cli.quota_db as qdb

        week_start_str = qdb._get_current_week_start()
        week_start_dt = datetime.strptime(week_start_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        fixed_now = week_start_dt + timedelta(hours=30)  # post-seedling

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            # Insert a snapshot timestamped 3h before fixed_now
            conn = qdb._get_conn()
            stale_ts = (fixed_now - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
            conn.execute(
                "INSERT INTO quota_snapshots (week_start, usage_percent, snapshotted_at) VALUES (?,?,?)",
                (week_start_str, 40.0, stale_ts),
            )
            conn.commit()
            conn.close()
            with patch("datetime.datetime") as MockDT:
                MockDT.now.return_value = fixed_now
                MockDT.strptime.side_effect = datetime.strptime
                MockDT.fromisoformat.side_effect = datetime.fromisoformat
                result = quota_statusline_part()
            assert result == 0
            out = capsys.readouterr().out
            assert "⏱" in out
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_when_snapshot_fresh_then_no_clock_indicator(self, tmp_path, capsys):
        """Snapshot <2h old → no ⏱ indicator."""
        import ai_cli.quota_db as qdb

        week_start_str = qdb._get_current_week_start()
        week_start_dt = datetime.strptime(week_start_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        fixed_now = week_start_dt + timedelta(hours=30)

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            conn = qdb._get_conn()
            fresh_ts = (fixed_now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
            conn.execute(
                "INSERT INTO quota_snapshots (week_start, usage_percent, snapshotted_at) VALUES (?,?,?)",
                (week_start_str, 40.0, fresh_ts),
            )
            conn.commit()
            conn.close()
            with patch("datetime.datetime") as MockDT:
                MockDT.now.return_value = fixed_now
                MockDT.strptime.side_effect = datetime.strptime
                MockDT.fromisoformat.side_effect = datetime.fromisoformat
                result = quota_statusline_part()
            assert result == 0
            out = capsys.readouterr().out
            assert "⏱" not in out
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_when_snapshot_exists_then_calls_maybe_trigger_with_snapshotted_at(self, tmp_path, capsys):
        """quota_statusline_part must call _maybe_trigger_background_scrape with the snapshot timestamp."""
        import ai_cli.quota_db as qdb

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            qdb.record_quota_snapshot(usage_percent=50.0)
            with patch("ai_cli.quota._maybe_trigger_background_scrape") as mock_trigger:
                quota_statusline_part()
            mock_trigger.assert_called_once()
            ts_arg = mock_trigger.call_args[0][0]
            assert isinstance(ts_arg, str) and len(ts_arg) > 0
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]


class TestQuotaSyncFromRemote:
    """Tests for quota_sync_from_remote — SSH-based pull from remote DB."""

    def _make_config(self, host="remote.example.com", user="testuser", port=22, identity=""):
        return {
            "remote": {
                "host": host,
                "user": user,
                "port": port,
                "identity_file": identity,
            }
        }

    def test_when_no_remote_host_then_returns_1(self, capsys):
        """Missing host/user in config → returns 1 without running SSH."""
        with (
            patch("ai_cli.quota.subprocess.run") as mock_run,
            patch("ai_cli.main.load_config", return_value={"remote": {}}),
        ):
            result = quota_sync_from_remote()

        assert result == 1
        mock_run.assert_not_called()

    def test_when_ssh_fails_then_returns_1(self, capsys):
        """SSH non-zero exit → returns 1 with error message."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Connection refused"
        with (
            patch("ai_cli.quota.subprocess.run", return_value=mock_result),
            patch("ai_cli.main.load_config", return_value=self._make_config()),
        ):
            result = quota_sync_from_remote()

        assert result == 1
        out = capsys.readouterr()
        assert "remote command failed" in out.err

    def test_when_ssh_raises_then_returns_1(self, capsys):
        """SSH raises (e.g. timeout) → returns 1 with error message."""
        with (
            patch("ai_cli.quota.subprocess.run", side_effect=TimeoutError("timed out")),
            patch("ai_cli.main.load_config", return_value=self._make_config()),
        ):
            result = quota_sync_from_remote()

        assert result == 1
        out = capsys.readouterr()
        assert "SSH failed" in out.err

    def test_when_remote_output_empty_then_returns_0(self, capsys):
        """SSH succeeds but remote DB empty → returns 0 with informational message."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        with (
            patch("ai_cli.quota.subprocess.run", return_value=mock_result),
            patch("ai_cli.main.load_config", return_value=self._make_config()),
        ):
            result = quota_sync_from_remote()

        assert result == 0
        out = capsys.readouterr()
        assert "no snapshots" in out.out

    def test_when_new_rows_then_inserts_into_local_db(self, tmp_path, capsys):
        """Valid remote rows not yet in local DB → inserts and returns 0."""
        import ai_cli.quota_db as qdb

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "55.0|10.0|30.0|0.0|2026-04-07T00:00:00Z|2026-04-07T10:00:00Z\n"
            with (
                patch("ai_cli.quota.subprocess.run", return_value=mock_result),
                patch("ai_cli.main.load_config", return_value=self._make_config()),
            ):
                result = quota_sync_from_remote()

            assert result == 0
            out = capsys.readouterr()
            assert "1 new snapshot" in out.out

            # Verify the row is actually in the DB
            conn = qdb._get_conn()
            rows = conn.execute("SELECT usage_percent FROM quota_snapshots").fetchall()
            conn.close()
            assert len(rows) == 1
            assert rows[0][0] == 55.0
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_when_rows_already_present_then_skips_duplicates(self, tmp_path, capsys):
        """Rows already in local DB (same snapshotted_at) → skips, returns 0."""
        import ai_cli.quota_db as qdb

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            # Pre-insert the row
            conn = qdb._get_conn()
            conn.execute(
                "INSERT INTO quota_snapshots (usage_percent, week_start, snapshotted_at)"
                " VALUES (55.0, '2026-04-07T00:00:00Z', '2026-04-07T10:00:00Z')"
            )
            conn.commit()
            conn.close()

            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "55.0|10.0|30.0|0.0|2026-04-07T00:00:00Z|2026-04-07T10:00:00Z\n"
            with (
                patch("ai_cli.quota.subprocess.run", return_value=mock_result),
                patch("ai_cli.main.load_config", return_value=self._make_config()),
            ):
                result = quota_sync_from_remote()

            assert result == 0
            out = capsys.readouterr()
            assert "already up to date" in out.out
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_when_identity_file_set_then_ssh_cmd_includes_i_flag(self):
        """identity_file in config → -i flag added to SSH command."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        with (
            patch("ai_cli.quota.subprocess.run", return_value=mock_result) as mock_run,
            patch(
                "ai_cli.main.load_config",
                return_value=self._make_config(identity="~/.ssh/id_ed25519"),
            ),
        ):
            quota_sync_from_remote()

        call_args = mock_run.call_args[0][0]
        assert "-i" in call_args

    def test_when_config_load_fails_then_returns_1(self, capsys):
        """Exception loading config → returns 1."""
        with patch("ai_cli.main.load_config", side_effect=RuntimeError("no config")):
            result = quota_sync_from_remote()

        assert result == 1
        out = capsys.readouterr()
        assert "could not load config" in out.err


# --- _maybe_trigger_background_scrape ---


class TestMaybeBackgroundScrape:
    def _stale_ts(self, minutes_ago: int = 35) -> str:
        now = datetime.now(timezone.utc)
        return (now - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _fresh_ts(self, minutes_ago: int = 5) -> str:
        now = datetime.now(timezone.utc)
        return (now - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_when_snapshot_fresh_then_no_scrape_triggered(self, tmp_path):
        lock_path = tmp_path / "quota-scrape.lock"
        with (
            patch("ai_cli.quota._SCRAPE_LOCK_PATH", lock_path),
            patch("subprocess.Popen") as mock_popen,
        ):
            _maybe_trigger_background_scrape(self._fresh_ts())
        mock_popen.assert_not_called()
        assert not lock_path.exists()

    def test_when_snapshot_stale_and_no_lock_then_triggers_scrape_and_creates_lock(self, tmp_path):
        lock_path = tmp_path / "quota-scrape.lock"
        with (
            patch("ai_cli.quota._SCRAPE_LOCK_PATH", lock_path),
            patch("subprocess.Popen") as mock_popen,
        ):
            _maybe_trigger_background_scrape(self._stale_ts())
        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        assert args == ["ai", "quota", "scrape"]
        assert lock_path.exists()

    def test_when_snapshot_stale_and_fresh_lock_then_no_scrape(self, tmp_path):
        """Fresh lock = scrape already running — skip."""
        lock_path = tmp_path / "quota-scrape.lock"
        lock_path.touch()  # lock written just now
        with (
            patch("ai_cli.quota._SCRAPE_LOCK_PATH", lock_path),
            patch("subprocess.Popen") as mock_popen,
        ):
            _maybe_trigger_background_scrape(self._stale_ts())
        mock_popen.assert_not_called()

    def test_when_snapshot_stale_and_stale_lock_then_removes_lock_and_triggers_scrape(self, tmp_path):
        """Stale lock (crashed scrape) is removed and a new scrape is launched."""
        lock_path = tmp_path / "quota-scrape.lock"
        lock_path.touch()
        stale_lock_time = time.time() - (16 * 60)  # 16 min old > _SCRAPE_LOCK_STALE_MINUTES
        os.utime(lock_path, (stale_lock_time, stale_lock_time))
        with (
            patch("ai_cli.quota._SCRAPE_LOCK_PATH", lock_path),
            patch("subprocess.Popen") as mock_popen,
        ):
            _maybe_trigger_background_scrape(self._stale_ts())
        mock_popen.assert_called_once()
        assert lock_path.exists()  # new lock created after stale one removed

    def test_when_popen_raises_then_no_exception_propagated(self, tmp_path):
        """Errors must never propagate — statusline path must be silent."""
        lock_path = tmp_path / "quota-scrape.lock"
        with (
            patch("ai_cli.quota._SCRAPE_LOCK_PATH", lock_path),
            patch("subprocess.Popen", side_effect=OSError("popen failed")),
        ):
            _maybe_trigger_background_scrape(self._stale_ts())  # must not raise

    def test_when_invalid_timestamp_then_no_exception_propagated(self, tmp_path):
        """Malformed snapshotted_at must be handled silently."""
        lock_path = tmp_path / "quota-scrape.lock"
        with patch("ai_cli.quota._SCRAPE_LOCK_PATH", lock_path):
            _maybe_trigger_background_scrape("not-a-timestamp")  # must not raise
