"""Tests for quota tracking — hidden pane scraper, notification, quota_watch, quota_record."""

import os
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_cli.quota import (
    EX_CONFIG,
    EX_TEMPFAIL,
    QuotaSnapshot,
    _get_claude_usage_snapshot,
    _get_usage_via_print_mode,
    _maybe_trigger_background_scrape,
    _parse_reset_datetime,
    _parse_usage_output,
    _publish_quota_snapshot,
    _run_nats_quota_listener,
    _scrape_usage_hidden_pane,
    _try_read_kv_snapshot,
    quota_history,
    quota_record,
    quota_scrape,
    quota_status,
    quota_statusline_part,
    quota_sync_from_remote,
    reap_cc_update_staging,
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

    # AIH-120: CC v2.1.207 replaced "Current week (Sonnet only)" with a per-model
    # secondary line whose label is now a model NAME ("Fable"), with NO progress bar
    # before "N% used". The old hardcoded "Sonnet only" regex matched nothing, so
    # weekly_sonnet_pct went permanently None and the statusline dropped it.
    _REAL_FABLE_OUTPUT = (
        "  Current session\n"
        "  ██████████████████                                 36% used\n"
        "  Resets 7:59pm (America/New_York)\n\n"
        "  Current week (all models)\n"
        "  ██████                                             12% used\n"
        "  Resets Jul 14 at 1:59pm (America/New_York)\n\n"
        "  Current week (Fable)\n"
        "                                                     0% used\n"
    )

    def test_when_fable_secondary_line_then_parsed_generically(self):
        snap = _parse_usage_output(self._REAL_FABLE_OUTPUT)
        assert snap is not None
        assert snap.weekly_all_models_pct == 12.0
        assert snap.session_pct == 36.0
        # Secondary per-model weekly line parsed generically (label != "all models"):
        assert snap.weekly_sonnet_pct == 0.0
        assert snap.weekly_model_name == "Fable"

    def test_when_legacy_sonnet_only_then_model_name_captured(self):
        # Back-compat: the old "Sonnet only" label still parses, and its name is captured.
        snap = _parse_usage_output(self._REAL_USAGE_OUTPUT)
        assert snap is not None
        assert snap.weekly_sonnet_pct == 24.0
        assert snap.weekly_model_name == "Sonnet only"

    def test_when_no_secondary_weekly_line_then_model_name_none(self):
        output = "  Current week (all models)\n  █████                                              40% used\n"
        snap = _parse_usage_output(output)
        assert snap is not None
        assert snap.weekly_all_models_pct == 40.0
        assert snap.weekly_sonnet_pct is None
        assert snap.weekly_model_name is None

    def test_when_all_models_label_not_mistaken_for_secondary(self):
        # The "all models" aggregate must never be captured as the secondary model line.
        snap = _parse_usage_output(self._REAL_FABLE_OUTPUT)
        assert snap.weekly_model_name != "all models"

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

    def test_when_cc_2114_format_with_scanning_and_disclaimer_then_parsed(self):
        """CC v2.1.114+ always shows 'does not include other devices' and
        'Scanning local sessions' in the contributing-factors section, even when
        the main weekly figure is valid API data.  Both must be ignored."""
        output = (
            "  Current week (all models)\n"
            "  █████████████                                      26% used\n"
            "  Resets Apr 23 at 3pm (America/New_York)\n\n"
            "  What's contributing to your limits usage?\n"
            "  Approximate, based on local sessions on this \n"
            "  machine — does not include other devices or claude.ai\n"
            "  Scanning local sessions…\n"
        )
        snap = _parse_usage_output(output)
        assert snap is not None
        assert snap.weekly_all_models_pct == 26.0

    def test_when_disclaimer_only_no_scanning_then_parsed(self):
        """'does not include other devices' in the detail section must not block
        parsing of the headline weekly figure."""
        output = (
            "  Current week (all models)\n"
            "  █                                                  3% used\n\n"
            "  Approximate, based on local sessions on this \n"
            "  machine — does not include other devices or claude.ai\n"
        )
        snap = _parse_usage_output(output)
        assert snap is not None
        assert snap.weekly_all_models_pct == 3.0

    def test_when_scanning_without_weekly_section_then_returns_none(self):
        """Return None when 'Current week (all models)' line has not rendered yet."""
        output = "  Loading usage data…\n  Scanning local sessions…\n"
        assert _parse_usage_output(output) is None

    def test_when_real_account_data_without_disclaimer_then_parsed(self):
        """Clean API data (no disclaimers) is always accepted."""
        output = "  Current week (all models)\n  ████████████████   42% used\n\n"
        snap = _parse_usage_output(output)
        assert snap is not None
        assert snap.weekly_all_models_pct == 42.0

    def test_when_scanning_with_weekly_section_then_parsed(self):
        """'Scanning local sessions' in the detail section must not block parsing
        when the weekly headline is already present."""
        output = "  Current week (all models)\n  ████████████████   4% used\n\n  Scanning local sessions…\n"
        snap = _parse_usage_output(output)
        assert snap is not None
        assert snap.weekly_all_models_pct == 4.0


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
        from datetime import datetime

        assert datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) > datetime.now(UTC)

    def test_when_real_cc_format_hour_only_pm_then_returns_future_utc(self):
        # "Resets 11pm" — no minutes, no date, no timezone
        text = "  Current week (all models) · Resets 11pm \n  65% used\n"
        result = _parse_reset_datetime(text)
        assert result is not None
        # Local midnight → UTC time should be in the future
        from datetime import datetime

        assert datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) > datetime.now(UTC)

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
    # These tests use year-less dates; mock datetime.now so the year-inference
    # logic always resolves to the current-year candidate rather than rolling
    # forward to next year when the test date has already passed.

    def test_when_iana_format_with_hour_only_then_converts_to_utc(self):
        # "Resets Apr 23 at 3pm (America/New_York)" — CC v2.1.112 format
        # Apr 23 is in EDT (UTC-4), so 3pm EDT = 19:00 UTC.
        text = "Resets Apr 23 at 3pm (America/New_York)            3% used"
        _before_apr23 = datetime(2026, 4, 22, 0, 0, 0, tzinfo=UTC)
        with patch("datetime.datetime") as MockDT:
            MockDT.now.return_value = _before_apr23
            MockDT.side_effect = datetime
            result = _parse_reset_datetime(text)
        assert result is not None
        assert result == "2026-04-23T19:00:00Z"

    def test_when_iana_format_with_minutes_then_converts_to_utc(self):
        # "Resets Apr 23 at 3:30pm (America/New_York)"
        text = "Resets Apr 23 at 3:30pm (America/New_York)"
        _before_apr23 = datetime(2026, 4, 22, 0, 0, 0, tzinfo=UTC)
        with patch("datetime.datetime") as MockDT:
            MockDT.now.return_value = _before_apr23
            MockDT.side_effect = datetime
            result = _parse_reset_datetime(text)
        assert result is not None
        assert result == "2026-04-23T19:30:00Z"

    def test_when_iana_format_utc_then_no_offset(self):
        text = "Resets Apr 23 at 7pm (UTC)"
        _before_apr23 = datetime(2026, 4, 22, 0, 0, 0, tzinfo=UTC)
        with patch("datetime.datetime") as MockDT:
            MockDT.now.return_value = _before_apr23
            MockDT.side_effect = datetime
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
        from datetime import datetime

        dt = datetime.strptime(snap.reset_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        assert dt > datetime.now(UTC)

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
        _before_apr23 = datetime(2026, 4, 22, 0, 0, 0, tzinfo=UTC)
        with patch("datetime.datetime") as MockDT:
            MockDT.now.return_value = _before_apr23
            MockDT.side_effect = datetime
            snap = _parse_usage_output(output)
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
        from datetime import datetime

        import ai_cli.quota_db as qdb

        # Anchor is April 11 10:59 UTC — the reset that ALREADY OCCURRED
        stale_anchor = "2026-04-11T10:59:00Z"
        anchor_file = tmp_path / "quota-reset-anchor.txt"
        anchor_file.write_text(stale_anchor)
        # Simulate "now" = April 13 02:00 UTC — two days after the reset
        now = datetime(2026, 4, 13, 2, 0, 0, tzinfo=UTC)
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


# --- CC auto-update staging leak (AI-CLI-131) ---


class TestCcUpdateStagingLeak:
    """The scrape kills its CC session ~15s after launch, which truncates any update
    download CC started and orphans the partial binary. On the Hetzner box that reached
    402 entries / 23 GB under the */10 cron, taking the disk to 0 bytes free.
    """

    @pytest.fixture
    def staging(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        d = tmp_path / "claude" / "staging"
        d.mkdir(parents=True)
        return d

    def _entry(self, staging: Path, name: str, age_s: float, size: int = 1024) -> Path:
        d = staging / name
        d.mkdir()
        (d / "claude").write_bytes(b"\0" * size)
        t = time.time() - age_s
        os.utime(d, (t, t))
        return d

    def test_given_cc_session_when_scrape_launches_it_then_autoupdater_is_disabled(self):
        """Root cause: CC must not start a background download it will be killed mid-way
        through. The launch command has to carry DISABLE_AUTOUPDATER=1."""
        sent = []

        def fake_run(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 0
            r.stdout = ""
            if cmd[0] == "tmux" and cmd[1] == "send-keys":
                sent.append(cmd[4])
            return r

        with patch("subprocess.run", side_effect=fake_run), patch("time.sleep"):
            _scrape_usage_hidden_pane()

        launch = [s for s in sent if "claude" in s]
        assert launch, "scrape never launched claude"
        assert launch[0] == "DISABLE_AUTOUPDATER=1 claude --dangerously-skip-permissions"

    def test_given_stale_orphaned_entries_when_reaping_then_all_removed(self, staging):
        for i in range(5):
            self._entry(staging, f"2.1.220.{100 + i}.178500000000{i}", age_s=7200)

        assert reap_cc_update_staging() == 5
        assert list(staging.iterdir()) == []

    def test_given_download_still_in_flight_when_reaping_then_it_survives(self, staging):
        """A real download takes minutes; reaping one mid-write would break another
        session's update. Only entries idle past the age bound may go."""
        self._entry(staging, "2.1.220.4242.1785000000000", age_s=30)

        assert reap_cc_update_staging() == 0
        assert (staging / "2.1.220.4242.1785000000000").exists()

    def test_given_unrecognised_sibling_when_reaping_then_it_survives(self, staging):
        """The reaper deletes only what it positively recognises as a staging entry."""
        other = staging / "locks"
        other.mkdir()
        (other / "cc.lock").write_text("held")
        os.utime(other, (time.time() - 99999, time.time() - 99999))

        assert reap_cc_update_staging() == 0
        assert (other / "cc.lock").exists()

    def test_given_no_staging_dir_when_reaping_then_returns_zero_without_raising(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "nonexistent"))

        assert reap_cc_update_staging() == 0

    def test_given_stale_entries_when_scrape_runs_then_they_are_reaped(self, staging):
        """The guard must be wired into the scrape, not merely importable — an unreferenced
        reaper would leave the box growing exactly as before."""
        for i in range(3):
            self._entry(staging, f"2.1.220.{200 + i}.178500000100{i}", age_s=7200)

        fail = MagicMock()
        fail.returncode = 1
        with patch("subprocess.run", return_value=fail), patch("time.sleep"):
            _scrape_usage_hidden_pane()

        assert list(staging.iterdir()) == []

    def test_given_reap_fails_when_scrape_runs_then_snapshot_still_returned(self, staging):
        """Bounding disk must never cost a scrape."""
        self._entry(staging, "2.1.220.300.1785000002000", age_s=7200)

        with patch("ai_cli.quota.shutil.rmtree", side_effect=OSError("read-only fs")):
            assert reap_cc_update_staging() == 0


# --- _scrape_usage_hidden_pane ---


class TestScrapeUsageHiddenPane:
    def _make_cap_result(self, stdout: str, returncode: int = 0) -> MagicMock:
        r = MagicMock()
        r.returncode = returncode
        r.stdout = stdout
        return r

    def test_when_tmux_new_session_fails_then_returns_none(self):
        ok = MagicMock()
        ok.returncode = 0
        fail = MagicMock()
        fail.returncode = 1

        def fake_run(cmd, **kwargs):
            if cmd[0] == "tmux" and cmd[1] == "new-session":
                return fail
            return ok

        with patch("subprocess.run", side_effect=fake_run):
            result = _scrape_usage_hidden_pane()
        assert result is None

    def _make_new_session_result(self) -> MagicMock:
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        return r

    def test_when_cc_prompt_never_appears_then_returns_none(self):
        """No ❯ in capture output → timeout → returns None."""
        no_prompt = self._make_cap_result("Starting claude...\n")
        new_sess = self._make_new_session_result()
        ok = MagicMock()
        ok.returncode = 0
        killed = []

        def fake_run(cmd, **kwargs):
            if cmd[0] == "tmux" and cmd[1] == "new-session":
                return new_sess
            if cmd[0] == "tmux" and cmd[1] == "kill-session":
                killed.append(True)
                return ok
            if cmd[0] == "tmux" and cmd[1] == "capture-pane":
                return no_prompt
            return ok

        with patch("subprocess.run", side_effect=fake_run), patch("time.sleep"):
            result = _scrape_usage_hidden_pane()
        assert result is None
        assert killed, "kill-session must be called on timeout path"

    def test_when_session_name_used_as_capture_target(self):
        """Session-name target is used for capture-pane, not index-based :N."""
        new_sess = self._make_new_session_result()
        ok = MagicMock()
        ok.returncode = 0
        targets_seen = []

        def fake_run(cmd, **kwargs):
            if cmd[0] == "tmux" and cmd[1] == "new-session":
                return new_sess
            if cmd[0] == "tmux" and cmd[1] == "capture-pane":
                if "-t" in cmd:
                    targets_seen.append(cmd[cmd.index("-t") + 1])
                return self._make_cap_result("Starting...\n")  # never shows ❯ → timeout
            return ok

        with patch("subprocess.run", side_effect=fake_run), patch("time.sleep"):
            _scrape_usage_hidden_pane()

        assert targets_seen, "capture-pane must be called"
        assert all(t == "ai-quota-scrape" for t in targets_seen), f"expected ai-quota-scrape, got {targets_seen}"

    def test_when_usage_output_captured_then_returns_snapshot(self):
        """Happy path: prompt appears, /usage output appears, snapshot returned."""
        new_sess = self._make_new_session_result()
        ok = MagicMock()
        ok.returncode = 0

        prompt_only = self._make_cap_result("❯\n")
        # Use the real multi-line /usage format to verify regex + scraper work together.
        usage_output = TestParseUsageOutput._REAL_USAGE_OUTPUT
        cap_with_usage = self._make_cap_result(usage_output)

        call_count = 0

        def fake_run(cmd, **kwargs):
            nonlocal call_count
            if cmd[0] == "tmux" and cmd[1] == "new-session":
                return new_sess
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

    def test_when_data_arrives_returns_first_parseable_result(self):
        """Scraper returns the first parseable result.

        In CC v2.1.114+, the 'does not include other devices' disclaimer is always
        present in the contributing-factors section and is no longer a signal that
        the headline figure is a local-only estimate.  The scraper accepts the first
        output that contains 'Current week (all models)' and '% used'.
        """
        new_sess = self._make_new_session_result()
        ok = MagicMock()
        ok.returncode = 0

        prompt_output = self._make_cap_result("❯\n")
        first_output = self._make_cap_result("Current week (all models): 1% used\ndoes not include other devices\n")

        call_count = 0

        def fake_run(cmd, **kwargs):
            nonlocal call_count
            if cmd[0] == "tmux" and cmd[1] == "new-session":
                return new_sess
            if cmd[0] == "tmux" and cmd[1] == "capture-pane":
                call_count += 1
                if call_count == 1:
                    return prompt_output
                return first_output
            return ok

        with patch("subprocess.run", side_effect=fake_run), patch("time.sleep"):
            result = _scrape_usage_hidden_pane()

        assert isinstance(result, QuotaSnapshot), "should return a snapshot"
        assert result.weekly_all_models_pct == 1.0, "should return first parseable result"

    def test_when_only_local_estimate_available_then_falls_back_to_it(self):
        """Scraper falls back to local-session estimate when API data never loads.

        On machines where the quota API never responds in a hidden pane session
        (e.g. Mac hidden pane sessions), the scraper exhausts its poll budget and
        returns the best local-session estimate seen instead of None.
        """
        new_sess = self._make_new_session_result()
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
            if cmd[0] == "tmux" and cmd[1] == "new-session":
                return new_sess
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

    def test_when_exception_raised_then_returns_none_and_kills_session(self):
        """Exception mid-scrape must not propagate; kill-session must still fire."""
        killed = []

        def fake_run(cmd, **kwargs):
            if cmd[0] == "tmux" and cmd[1] == "kill-session":
                killed.append(True)
                r = MagicMock()
                r.returncode = 0
                return r
            raise RuntimeError("tmux broken")

        with patch("subprocess.run", side_effect=fake_run), patch("time.sleep"):
            result = _scrape_usage_hidden_pane()

        assert result is None
        assert killed, "kill-session must be called even on exception"

    def test_window_size_latest_restored_after_resize(self):
        """resize-window sets window-size=manual as a tmux side effect.

        The scraper must call `set-option window-size latest` immediately after
        resize-window so that iTerm2 pane resize keeps working in the host session.
        """
        new_sess = self._make_new_session_result()
        ok = MagicMock()
        ok.returncode = 0
        cmds_seen = []

        def fake_run(cmd, **kwargs):
            if isinstance(cmd, list) and cmd[0] == "tmux":
                cmds_seen.append(cmd[1:])
            if cmd[0] == "tmux" and cmd[1] == "new-session":
                return new_sess
            return ok

        with patch("subprocess.run", side_effect=fake_run), patch("time.sleep"):
            _scrape_usage_hidden_pane()

        resize_idx = next((i for i, c in enumerate(cmds_seen) if c[0] == "resize-window"), None)
        restore_idx = next(
            (i for i, c in enumerate(cmds_seen) if c[0] == "set-option" and "window-size" in c and "latest" in c),
            None,
        )
        assert resize_idx is not None, "resize-window must be called"
        assert restore_idx is not None, "set-option window-size latest must be called"
        assert restore_idx == resize_idx + 1, "set-option window-size latest must follow resize-window immediately"

    def test_when_sonnet_line_renders_after_all_models_then_waits_for_it(self):
        """Scraper must not exit immediately if Sonnet % is absent on first valid parse.

        CC renders 'Current week (all models)' before 'Current week (Sonnet only)'.
        Without the grace-period, the scraper exits early and weekly_sonnet_pct is
        silently dropped — the root cause of the intermittent Sonnet % bug (AI-CLI-82).
        """
        new_sess = self._make_new_session_result()
        ok = MagicMock()
        ok.returncode = 0

        prompt_only = self._make_cap_result("❯\n")
        all_models_only = self._make_cap_result("Current week (all models)\nResets May 5 at 3pm   26% used\n")
        full_output = self._make_cap_result(
            "Current week (all models)\nResets May 5 at 3pm   26% used\nCurrent week (Sonnet only)\n  24% used\n"
        )

        call_count = 0

        def fake_run(cmd, **kwargs):
            nonlocal call_count
            if cmd[0] == "tmux" and cmd[1] == "new-session":
                return new_sess
            if cmd[0] == "tmux" and cmd[1] == "capture-pane":
                call_count += 1
                if call_count == 1:
                    return prompt_only
                if call_count == 2:
                    return all_models_only  # first valid parse — no Sonnet yet
                return full_output  # grace-period poll — Sonnet now rendered
            return ok

        with patch("subprocess.run", side_effect=fake_run), patch("time.sleep"):
            result = _scrape_usage_hidden_pane()

        assert isinstance(result, QuotaSnapshot)
        assert result.weekly_all_models_pct == 26.0
        assert result.weekly_sonnet_pct == 24.0, "must wait for Sonnet line to render"

    def test_when_sonnet_line_never_renders_then_returns_snapshot_without_it(self):
        """Scraper must not block forever if account has no Sonnet quota line."""
        new_sess = self._make_new_session_result()
        ok = MagicMock()
        ok.returncode = 0

        prompt_only = self._make_cap_result("❯\n")
        all_models_only = self._make_cap_result("Current week (all models)\nResets May 5 at 3pm   26% used\n")

        call_count = 0

        def fake_run(cmd, **kwargs):
            nonlocal call_count
            if cmd[0] == "tmux" and cmd[1] == "new-session":
                return new_sess
            if cmd[0] == "tmux" and cmd[1] == "capture-pane":
                call_count += 1
                if call_count == 1:
                    return prompt_only
                return all_models_only  # Sonnet line never appears
            return ok

        with patch("subprocess.run", side_effect=fake_run), patch("time.sleep"):
            result = _scrape_usage_hidden_pane()

        assert isinstance(result, QuotaSnapshot)
        assert result.weekly_all_models_pct == 26.0
        assert result.weekly_sonnet_pct is None, "no Sonnet line means weekly_sonnet_pct is None"


# --- _get_claude_usage_snapshot ---


# Real `claude -p /usage` print-mode output (CC v2.1.207): inline "label: N% used · resets"
# form, per-model line always present. This is the deterministic replacement for the flaky
# interactive-TUI scrape (AIH-120 follow-up).
_PRINT_MODE_USAGE_OUTPUT = (
    "You are currently using your subscription to power your Claude Code usage\n\n"
    "Current session: 4% used · resets Jul 13 at 5:30pm (America/New_York)\n"
    "Current week (all models): 17% used · resets Jul 14 at 2pm (America/New_York)\n"
    "Current week (Fable): 0% used\n\n"
    "What's contributing to your limits usage?\n"
)


class TestGetUsageViaPrintMode:
    def test_when_print_mode_output_then_parsed(self):
        proc = MagicMock(returncode=0, stdout=_PRINT_MODE_USAGE_OUTPUT)
        with patch("ai_cli.quota.subprocess.run", return_value=proc) as run:
            snap = _get_usage_via_print_mode()
        # Invoked non-interactively — no tmux, just `claude -p /usage`.
        run.assert_called_once()
        assert run.call_args.args[0] == ["claude", "-p", "/usage"]
        assert snap is not None
        assert snap.weekly_all_models_pct == 17.0
        assert snap.session_pct == 4.0
        assert snap.weekly_sonnet_pct == 0.0
        assert snap.weekly_model_name == "Fable"

    def test_when_nonzero_returncode_then_none(self):
        proc = MagicMock(returncode=1, stdout="")
        with patch("ai_cli.quota.subprocess.run", return_value=proc):
            assert _get_usage_via_print_mode() is None

    def test_when_no_percent_used_then_none(self):
        proc = MagicMock(returncode=0, stdout="some unrelated output\n")
        with patch("ai_cli.quota.subprocess.run", return_value=proc):
            assert _get_usage_via_print_mode() is None

    def test_when_subprocess_raises_then_none(self):
        with patch("ai_cli.quota.subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 30)):
            assert _get_usage_via_print_mode() is None


class TestGetClaudeUsageSnapshot:
    def test_print_mode_retired_scrape_is_sole_path(self):
        """AIH-164: print mode is retired from the capture path (dead on CC 2.1.207); the
        hidden-pane scrape is the sole fallback and is called without consulting print mode."""
        snap = QuotaSnapshot(weekly_all_models_pct=17.0, session_pct=4.0, weekly_sonnet_pct=0.0)
        with (
            patch("ai_cli.quota._get_usage_via_print_mode") as print_mode,
            patch("ai_cli.quota._scrape_usage_hidden_pane", return_value=snap) as scrape,
        ):
            result = _get_claude_usage_snapshot()
        assert result is snap
        scrape.assert_called_once()
        print_mode.assert_not_called()

    def test_when_print_mode_none_then_falls_back_to_scraper(self):
        snap = QuotaSnapshot(weekly_all_models_pct=72.0, session_pct=10.0, weekly_sonnet_pct=30.0, extra_pct=0.0)
        with (
            patch("ai_cli.quota._get_usage_via_print_mode", return_value=None),
            patch("ai_cli.quota._scrape_usage_hidden_pane", return_value=snap) as scrape,
        ):
            result = _get_claude_usage_snapshot()
        assert result is snap
        scrape.assert_called_once()

    def test_when_both_none_then_returns_none(self):
        with (
            patch("ai_cli.quota._get_usage_via_print_mode", return_value=None),
            patch("ai_cli.quota._scrape_usage_hidden_pane", return_value=None),
        ):
            assert _get_claude_usage_snapshot() is None


# --- _notify_threshold ---


class TestNotifyThreshold:
    """Tests for _notify_threshold — formats quota alerts and calls Notifier.send()."""

    def _make_snapshot(self, pct: float = 78.5) -> QuotaSnapshot:
        return QuotaSnapshot(weekly_all_models_pct=pct, session_pct=12.0, weekly_sonnet_pct=40.0)

    def test_when_threshold_90_then_urgent_priority(self):
        from ai_cli.quota import _notify_threshold

        mock_notifier = MagicMock()
        _notify_threshold(mock_notifier, 90, self._make_snapshot(91.0))
        mock_notifier.send.assert_called_once()
        _, kwargs = mock_notifier.send.call_args
        assert kwargs.get("priority") == "urgent"

    def test_when_threshold_75_then_high_priority(self):
        from ai_cli.quota import _notify_threshold

        mock_notifier = MagicMock()
        _notify_threshold(mock_notifier, 75, self._make_snapshot(76.0))
        _, kwargs = mock_notifier.send.call_args
        assert kwargs.get("priority") == "high"

    def test_when_threshold_50_then_default_priority(self):
        from ai_cli.quota import _notify_threshold

        mock_notifier = MagicMock()
        _notify_threshold(mock_notifier, 50, self._make_snapshot(51.0))
        _, kwargs = mock_notifier.send.call_args
        assert kwargs.get("priority") == "default"

    def test_when_threshold_90_then_slow_down_in_body(self):
        from ai_cli.quota import _notify_threshold

        mock_notifier = MagicMock()
        _notify_threshold(mock_notifier, 90, self._make_snapshot(92.0))
        args, _ = mock_notifier.send.call_args
        body = args[1]
        assert "slow down" in body.lower()

    def test_when_sonnet_and_session_present_then_included_in_body(self):
        from ai_cli.quota import _notify_threshold

        snap = QuotaSnapshot(weekly_all_models_pct=80.0, session_pct=15.0, weekly_sonnet_pct=50.0)
        mock_notifier = MagicMock()
        _notify_threshold(mock_notifier, 75, snap)
        args, _ = mock_notifier.send.call_args
        body = args[1]
        assert "Sonnet" in body
        assert "Session" in body

    def test_when_source_is_quota_watch(self):
        from ai_cli.quota import _notify_threshold

        mock_notifier = MagicMock()
        _notify_threshold(mock_notifier, 75, self._make_snapshot())
        _, kwargs = mock_notifier.send.call_args
        assert kwargs.get("source") == "quota-watch"

    def test_when_notifier_raises_then_no_crash(self, capsys):
        from ai_cli.quota import _notify_threshold

        mock_notifier = MagicMock()
        mock_notifier.send.side_effect = RuntimeError("notifier error")
        _notify_threshold(mock_notifier, 75, self._make_snapshot())  # must not raise
        assert "notification failed" in capsys.readouterr().err


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
    @pytest.fixture
    def scrape_prerequisites_available(self):
        """Make scraper-result tests independent of executables on the test runner's PATH."""
        with patch("ai_cli.quota.shutil.which", return_value="/test/bin"):
            yield

    def test_given_no_output_when_scrape_runs_then_returns_ex_tempfail(self, scrape_prerequisites_available, capsys):
        with patch("ai_cli.quota._scrape_usage_hidden_pane", return_value=None):
            result = quota_scrape()
        assert result == EX_TEMPFAIL
        assert "Could not extract" in capsys.readouterr().err

    @pytest.mark.parametrize("missing_binary", ["tmux", "claude"])
    def test_given_missing_prerequisite_when_scrape_runs_then_returns_ex_config(self, missing_binary, capsys):
        with (
            patch(
                "ai_cli.quota.shutil.which",
                side_effect=lambda binary: None if binary == missing_binary else f"/bin/{binary}",
            ),
            patch("ai_cli.quota._scrape_usage_hidden_pane") as scrape,
        ):
            result = quota_scrape()

        assert result == EX_CONFIG
        assert missing_binary in capsys.readouterr().err
        scrape.assert_not_called()

    def test_given_format_mismatch_when_scrape_runs_then_returns_ex_config(self, scrape_prerequisites_available):
        def format_mismatch() -> None:
            import ai_cli.quota as quota

            quota._last_scrape_had_format_mismatch = True

        with patch("ai_cli.quota._scrape_usage_hidden_pane", side_effect=format_mismatch):
            result = quota_scrape()

        assert result == EX_CONFIG

    def test_given_stale_mismatch_flag_when_scrape_has_no_output_then_returns_ex_tempfail(
        self, scrape_prerequisites_available
    ):
        import ai_cli.quota as quota

        quota._last_scrape_had_format_mismatch = True
        with patch("ai_cli.quota._scrape_usage_hidden_pane", return_value=None):
            result = quota_scrape()

        assert result == EX_TEMPFAIL

    def test_when_scrape_succeeds_then_stores_snapshot_and_returns_0(
        self, scrape_prerequisites_available, tmp_path, capsys
    ):
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

    def test_when_scrape_fails_then_lock_file_cleaned_up(self, scrape_prerequisites_available, tmp_path):
        """quota_scrape must always remove the lock file, even on failure."""
        lock_path = tmp_path / "quota-scrape.lock"
        lock_path.touch()
        with (
            patch("ai_cli.quota._SCRAPE_LOCK_PATH", lock_path),
            patch("ai_cli.quota._scrape_usage_hidden_pane", return_value=None),
        ):
            quota_scrape()
        assert not lock_path.exists()

    def test_when_scrape_succeeds_then_lock_file_cleaned_up(self, scrape_prerequisites_available, tmp_path, capsys):
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

    def test_when_js_available_then_writes_snapshot_to_kv(self, monkeypatch):
        monkeypatch.delenv("AI_HOST", raising=False)
        snap = QuotaSnapshot(weekly_all_models_pct=42.0, session_pct=5.0, weekly_sonnet_pct=20.0, extra_pct=1.0)
        mock_client, mock_kv = self._make_mock_client_with_js(connected=True)
        with patch("ai_cli.messaging.NATSClient", return_value=mock_client):
            _publish_quota_snapshot(snap)
        # Snapshot key written (ack skipped when machine is empty)
        assert mock_kv.put.call_count == 1
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

    def test_when_js_available_then_kv_key_is_machine_suffixed(self, monkeypatch):
        """KV key must be quota.claude.current.{machine} when AI_HOST is set.
        Falls back to quota.claude.current (no suffix) when AI_HOST is unset.
        The old quota.claude.weekly key must never be written."""
        monkeypatch.setenv("AI_HOST", "test-host")
        snap = QuotaSnapshot(weekly_all_models_pct=33.0)
        mock_client, mock_kv = self._make_mock_client_with_js(connected=True)
        with patch("ai_cli.messaging.NATSClient", return_value=mock_client):
            _publish_quota_snapshot(snap)
        written_keys = [call.args[0] for call in mock_kv.put.call_args_list]
        assert "quota.claude.current.test-host" in written_keys
        # Regression guard: old key names must not be written.
        assert "quota.claude.weekly" not in written_keys
        assert "quota.claude.current" not in written_keys  # bare key replaced by suffixed

    def test_when_publish_then_also_publishes_to_platform_usage_subject(self, monkeypatch):
        """The second publish call targets hw.events.usage.claude.snapshot with a
        UsageConsumer-compatible payload shape (id, machine, used_pct, raw, ...)."""
        monkeypatch.setenv("AI_HOST", "hetzner")
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
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("ai_cli.quota._get_claude_usage_snapshot", return_value=None),
            patch("time.sleep", fake_sleep),
        ):
            from ai_cli.quota import quota_watch

            result = quota_watch()
        assert result == 0

    def test_when_nats_unavailable_then_still_starts(self):
        """NATS unavailable is no longer fatal — quota-watch runs with notifications only."""
        mock_client = MagicMock()
        mock_client.nc = None

        async def fake_connect():
            pass

        mock_client.connect = fake_connect

        def fake_sleep(_):
            raise KeyboardInterrupt

        with (
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("ai_cli.quota._get_claude_usage_snapshot", return_value=None),
            patch("time.sleep", fake_sleep),
        ):
            from ai_cli.quota import quota_watch

            result = quota_watch()
        assert result == 0

    def test_when_usage_crosses_threshold_then_publishes_and_notifies(self):
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
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("ai_cli.quota._get_claude_usage_snapshot", return_value=snap),
            patch("ai_cli.quota._notify_threshold") as mock_notify,
            patch("time.sleep", fake_sleep),
        ):
            from ai_cli.quota import quota_watch

            result = quota_watch()

        assert result == 0
        assert mock_client.publish.call_count >= 2
        subjects = [call.args[0] for call in mock_client.publish.call_args_list]
        assert "quota.threshold.50" in subjects
        assert "quota.threshold.75" in subjects
        assert mock_notify.call_count >= 2

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
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("ai_cli.quota._get_claude_usage_snapshot", return_value=snap),
            patch("ai_cli.quota._notify_threshold"),
            patch("time.sleep", fake_sleep),
        ):
            from ai_cli.quota import quota_watch

            result = quota_watch()

        assert result == 0
        assert "failed to publish" in capsys.readouterr().err

    def test_starts_nats_listener_thread_when_machine_is_set(self, monkeypatch):
        """quota_watch starts the NATS listener daemon thread when AI_HOST is set."""
        monkeypatch.setenv("AI_HOST", "hetzner")

        mock_client = MagicMock()
        mock_client.nc = None

        async def fake_connect():
            pass

        mock_client.connect = fake_connect

        def fake_sleep(_):
            raise KeyboardInterrupt

        started_names = []

        real_thread = __import__("threading").Thread

        def capturing_thread(*args, **kwargs):
            t = real_thread(*args, **kwargs)
            started_names.append(kwargs.get("name", ""))
            return t

        with (
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("ai_cli.quota._get_claude_usage_snapshot", return_value=None),
            patch("ai_cli.quota._run_nats_quota_listener"),
            patch("time.sleep", fake_sleep),
            patch("threading.Thread", side_effect=capturing_thread),
        ):
            from ai_cli.quota import quota_watch

            quota_watch()

        assert "nats-quota-listener" in started_names

    def test_no_listener_thread_when_machine_is_empty(self, monkeypatch):
        """quota_watch does NOT start the listener thread when AI_HOST is unset."""
        monkeypatch.delenv("AI_HOST", raising=False)

        mock_client = MagicMock()
        mock_client.nc = None

        async def fake_connect():
            pass

        mock_client.connect = fake_connect

        def fake_sleep(_):
            raise KeyboardInterrupt

        started_names = []
        real_thread = __import__("threading").Thread

        def capturing_thread(*args, **kwargs):
            t = real_thread(*args, **kwargs)
            started_names.append(kwargs.get("name", ""))
            return t

        with (
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("ai_cli.quota._get_claude_usage_snapshot", return_value=None),
            patch("time.sleep", fake_sleep),
            patch("threading.Thread", side_effect=capturing_thread),
        ):
            from ai_cli.quota import quota_watch

            quota_watch()

        assert "nats-quota-listener" not in started_names


# --- _run_nats_quota_listener ---


class TestRunNATSQuotaListener:
    def _make_mock_client(self):
        mock_client = MagicMock()
        mock_client.nc = MagicMock()
        mock_client.js = None

        async def fake_connect():
            pass

        async def fake_close():
            pass

        mock_client.connect = fake_connect
        mock_client.close = fake_close
        return mock_client

    def test_subscribes_to_machine_specific_subject(self):
        """Listener subscribes to quota.scrape.request.{machine}."""
        import threading

        stop_event = threading.Event()
        subscribed_subjects = []

        mock_client = self._make_mock_client()

        async def fake_subscribe(subject, cb):
            subscribed_subjects.append(subject)
            return MagicMock()

        mock_client.nc.subscribe = fake_subscribe

        async def fake_wait_for(coro, timeout):
            return await coro

        async def fake_sleep(n):
            stop_event.set()

        with (
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("asyncio.wait_for", side_effect=fake_wait_for),
            patch("asyncio.sleep", fake_sleep),
        ):
            _run_nats_quota_listener("hetzner", stop_event=stop_event)

        assert "quota.scrape.request.hetzner" in subscribed_subjects

    def test_launches_scrape_when_lock_absent(self, tmp_path):
        """On scrape request, _launch_background_scrape called when no lock file."""
        import threading

        stop_event = threading.Event()
        lock_path = tmp_path / "quota-scrape.lock"

        mock_client = self._make_mock_client()
        captured_cb = []

        async def fake_subscribe(subject, cb):
            captured_cb.append(cb)
            return MagicMock()

        mock_client.nc.subscribe = fake_subscribe

        async def fake_sleep(n):
            # Deliver a fake scrape request then stop
            if captured_cb:
                await captured_cb[0](MagicMock())
            stop_event.set()

        async def fake_wait_for(coro, timeout):
            return await coro

        with (
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("asyncio.wait_for", side_effect=fake_wait_for),
            patch("asyncio.sleep", fake_sleep),
            patch("ai_cli.quota._SCRAPE_LOCK_PATH", lock_path),
            patch("ai_cli.quota._launch_background_scrape") as mock_launch,
        ):
            _run_nats_quota_listener("hetzner", stop_event=stop_event)

        mock_launch.assert_called_once()

    def test_skips_scrape_when_lock_exists(self, tmp_path):
        """On scrape request, skip launch if lock file exists (scrape already running)."""
        import threading

        stop_event = threading.Event()
        lock_path = tmp_path / "quota-scrape.lock"
        lock_path.touch()

        mock_client = self._make_mock_client()
        captured_cb = []

        async def fake_subscribe(subject, cb):
            captured_cb.append(cb)
            return MagicMock()

        mock_client.nc.subscribe = fake_subscribe

        async def fake_sleep(n):
            if captured_cb:
                await captured_cb[0](MagicMock())
            stop_event.set()

        async def fake_wait_for(coro, timeout):
            return await coro

        with (
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("asyncio.wait_for", side_effect=fake_wait_for),
            patch("asyncio.sleep", fake_sleep),
            patch("ai_cli.quota._SCRAPE_LOCK_PATH", lock_path),
            patch("ai_cli.quota._launch_background_scrape") as mock_launch,
        ):
            _run_nats_quota_listener("hetzner", stop_event=stop_event)

        mock_launch.assert_not_called()

    def test_publishes_heartbeat_via_kv(self):
        """Listener writes heartbeat to hw_state[quota_watch.heartbeat.{machine}]."""
        import threading

        stop_event = threading.Event()
        mock_client = self._make_mock_client()
        mock_client.js = MagicMock()

        heartbeat_keys = []

        mock_kv = MagicMock()

        async def fake_kv_put(key, value):
            heartbeat_keys.append(key)

        mock_kv.put = fake_kv_put

        async def fake_key_value(bucket):
            return mock_kv

        mock_client.js.key_value = fake_key_value

        async def fake_subscribe(subject, cb):
            return MagicMock()

        mock_client.nc.subscribe = fake_subscribe

        call_count = 0

        async def fake_sleep(n):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                stop_event.set()

        async def fake_wait_for(coro, timeout):
            return await coro

        with (
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("asyncio.wait_for", side_effect=fake_wait_for),
            patch("asyncio.sleep", fake_sleep),
            patch("ai_cli.quota.time") as mock_time,
        ):
            # Force heartbeat_interval condition to always be true
            mock_time.time.return_value = 9999.0
            _run_nats_quota_listener("hetzner", stop_event=stop_event)

        assert any("quota_watch.heartbeat.hetzner" in k for k in heartbeat_keys)

    def test_exits_cleanly_when_nats_unavailable(self):
        """If NATS connect fails, listener exits without raising."""
        import threading

        stop_event = threading.Event()

        mock_client = MagicMock()
        mock_client.nc = None

        async def fake_connect():
            pass

        mock_client.connect = fake_connect

        async def fake_wait_for(coro, timeout):
            return await coro

        with (
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("asyncio.wait_for", side_effect=fake_wait_for),
        ):
            _run_nats_quota_listener("hetzner", stop_event=stop_event)
        # No assertion needed — must not raise


# --- _publish_quota_snapshot — KV key rename + ack (AI-CLI-57) ---


class TestPublishQuotaSnapshotKV:
    def _snap(self, pct: float = 60.0) -> QuotaSnapshot:
        return QuotaSnapshot(weekly_all_models_pct=pct, session_pct=5.0)

    def _make_mock_client(self):
        mock_client = MagicMock()
        mock_client.nc = MagicMock()
        mock_client.js = MagicMock()

        async def fake_connect():
            pass

        async def fake_close():
            pass

        mock_client.connect = fake_connect
        mock_client.close = fake_close

        async def fake_publish(subject, payload):
            pass

        mock_client.publish = fake_publish
        return mock_client

    def test_kv_write_uses_machine_suffix_when_ai_cli_host_set(self, monkeypatch):
        """KV key is quota.claude.current.{machine} when AI_HOST is set."""
        monkeypatch.setenv("AI_HOST", "hetzner")

        mock_client = self._make_mock_client()
        kv_written = {}
        mock_kv = MagicMock()

        async def fake_put(key, value):
            kv_written[key] = value

        mock_kv.put = fake_put

        async def fake_key_value(bucket):
            return mock_kv

        mock_client.js.key_value = fake_key_value

        with patch("ai_cli.messaging.NATSClient", return_value=mock_client):
            _publish_quota_snapshot(self._snap())

        assert "quota.claude.current.hetzner" in kv_written

    def test_kv_write_uses_legacy_key_when_machine_empty(self, monkeypatch):
        """KV key is quota.claude.current (no suffix) when AI_HOST is unset."""
        monkeypatch.delenv("AI_HOST", raising=False)

        mock_client = self._make_mock_client()
        kv_written = {}
        mock_kv = MagicMock()

        async def fake_put(key, value):
            kv_written[key] = value

        mock_kv.put = fake_put

        async def fake_key_value(bucket):
            return mock_kv

        mock_client.js.key_value = fake_key_value

        with patch("ai_cli.messaging.NATSClient", return_value=mock_client):
            _publish_quota_snapshot(self._snap())

        assert "quota.claude.current" in kv_written
        assert "quota.claude.current." not in kv_written

    def test_writes_ack_key_when_machine_set(self, monkeypatch):
        """Ack is written to quota.scrape.ack.{machine} after snapshot."""
        monkeypatch.setenv("AI_HOST", "hetzner")

        mock_client = self._make_mock_client()
        kv_written = {}
        mock_kv = MagicMock()

        async def fake_put(key, value):
            kv_written[key] = value

        mock_kv.put = fake_put

        async def fake_key_value(bucket):
            return mock_kv

        mock_client.js.key_value = fake_key_value

        with patch("ai_cli.messaging.NATSClient", return_value=mock_client):
            _publish_quota_snapshot(self._snap())

        assert "quota.scrape.ack.hetzner" in kv_written
        import json

        ack = json.loads(kv_written["quota.scrape.ack.hetzner"])
        assert "scraped_at" in ack
        assert isinstance(ack["scraped_at"], float)

    def test_no_ack_key_when_machine_empty(self, monkeypatch):
        """No ack key written when AI_HOST is unset."""
        monkeypatch.delenv("AI_HOST", raising=False)

        mock_client = self._make_mock_client()
        kv_written = {}
        mock_kv = MagicMock()

        async def fake_put(key, value):
            kv_written[key] = value

        mock_kv.put = fake_put

        async def fake_key_value(bucket):
            return mock_kv

        mock_client.js.key_value = fake_key_value

        with patch("ai_cli.messaging.NATSClient", return_value=mock_client):
            _publish_quota_snapshot(self._snap())

        assert not any("scrape.ack" in k for k in kv_written)


# --- _try_read_kv_snapshot — machine-suffixed key (AI-CLI-57) ---


class TestTryReadKvSnapshotMachineKey:
    def _make_mock_client_with_kv(self, read_keys: list):
        mock_entry = MagicMock()
        mock_entry.value = b'{"usage_percent": 55.0}'

        mock_kv = MagicMock()

        async def fake_get(key):
            read_keys.append(key)
            return mock_entry

        mock_kv.get = fake_get

        mock_client = MagicMock()
        mock_client.js = MagicMock()
        mock_client.nc = MagicMock()

        async def fake_connect():
            pass

        async def fake_close():
            pass

        mock_client.connect = fake_connect
        mock_client.close = fake_close

        async def fake_key_value(bucket):
            return mock_kv

        mock_client.js.key_value = fake_key_value
        return mock_client

    def test_reads_machine_suffixed_key_when_ai_cli_host_set(self, monkeypatch):
        """_try_read_kv_snapshot reads quota.claude.current.{machine} when AI_HOST set."""
        monkeypatch.setenv("AI_HOST", "hetzner")

        read_keys: list = []
        mock_client = self._make_mock_client_with_kv(read_keys)

        with (
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch(
                "ai_cli.config.load_config",
                return_value={"messaging": {"nats_servers": ["nats://localhost:4222"]}},
            ),
        ):
            _try_read_kv_snapshot()

        assert any(k == "quota.claude.current.hetzner" for k in read_keys)

    def test_reads_legacy_key_when_ai_cli_host_unset(self, monkeypatch):
        """_try_read_kv_snapshot reads quota.claude.current (no suffix) when AI_HOST unset."""
        monkeypatch.delenv("AI_HOST", raising=False)

        read_keys: list = []
        mock_client = self._make_mock_client_with_kv(read_keys)

        with (
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch(
                "ai_cli.config.load_config",
                return_value={"messaging": {"nats_servers": ["nats://localhost:4222"]}},
            ),
        ):
            _try_read_kv_snapshot()

        assert any(k == "quota.claude.current" for k in read_keys)


# --- quota_statusline_part ---


class TestConftestQuotaHermeticity:
    """AI-CLI-97: the conftest _isolate_quota_state fixture must keep every test off
    real user quota state — the source of the fixed xdist-only flake (real ~/.local/
    state/ai-cli/quota.db carries live Fable data; _launch/_maybe_trigger spawn a real
    `ai quota scrape` subprocess that writes the real DB + NATS)."""

    def test_quota_db_path_is_isolated_not_real(self):
        import ai_cli.quota_db as qdb

        p = str(qdb._get_quota_db_path())
        # Redirected to a per-test tmp dir, never the real state dir.
        assert "quota_state" in p
        assert str(Path.home() / ".local" / "state" / "ai-cli") not in p

    def test_scrape_spawners_are_neutralized(self):
        import ai_cli.quota as q

        # The autouse fixture no-ops both spawners, so no real `ai quota scrape`
        # subprocess is launched (which would touch real DB/NATS). Prove no Popen fires.
        with patch("subprocess.Popen") as mock_popen:
            q._launch_background_scrape()
            q._maybe_trigger_background_scrape("2020-01-01T00:00:00Z")  # stale ts
        mock_popen.assert_not_called()

    def test_scrape_lock_path_is_isolated_not_real(self):
        import ai_cli.quota as q

        assert "scrape_lock" in str(q._SCRAPE_LOCK_PATH)
        assert Path.home() / ".local" / "state" / "ai-cli" / "quota-scrape.lock" != q._SCRAPE_LOCK_PATH


class TestQuotaStatuslinePart:
    """Statusline pace rendering, driven by an injected clock rather than the real one.

    AI-CLI-180: every scenario here is "usage X% at Y hours into the quota week", which needs
    a week_start and a `now` a fixed offset apart — nothing about it needs the real date. The
    tests used to *derive* week_start from `_get_current_week_start()` with no argument, i.e.
    from `datetime.now()`, and only then pin `now` to an offset from it. That made the fixture's
    own timing premise a function of when the suite happened to run, so a run straddling the
    real week boundary got a week_start from one week and rows recorded against another; the
    query matched nothing and the render emitted its `📊 -` placeholder instead of a
    percentage. Injecting a fixed reference instant removes the race at its source (same
    approach as AI-CLI-158 for the adaptive-labels class).
    """

    # An arbitrary fixed instant, deliberately NOT the real clock. Any instant works: the
    # week_start derived from it is exact, so `hours_elapsed` offsets below are exact too.
    _FIXED_REFERENCE_NOW = datetime(2026, 1, 5, 12, 0, 0, tzinfo=UTC)  # Monday noon UTC

    @pytest.fixture
    def frozen_week(self):
        """The injected clock: the current quota week's start, as of a fixed reference instant.

        Returns ``(week_start_str, week_start_dt)``. Tests add a `timedelta` to `week_start_dt`
        to place `now` at a chosen point in the week, then patch `datetime.datetime` so the
        production code sees exactly that instant.
        """
        import ai_cli.quota_db as qdb

        week_start_str = qdb._get_current_week_start(now=self._FIXED_REFERENCE_NOW)
        week_start_dt = datetime.strptime(week_start_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        return week_start_str, week_start_dt

    @pytest.mark.real_quota_scrape
    def test_when_no_snapshot_then_shows_placeholder_and_triggers_scrape(self, tmp_path, capsys):
        """No data for current week → shows '📊 -' placeholder and launches a background scrape."""
        import ai_cli.quota as q
        import ai_cli.quota_db as qdb

        qdb.set_db_path(tmp_path / "quota.db")
        # Initialize the DB schema without inserting any snapshots
        qdb._get_conn().close()
        q._SCRAPE_LOCK_PATH.unlink(missing_ok=True)
        try:
            with (
                patch("subprocess.Popen") as mock_popen,
                # Ensure NATS KV returns no data so the no-snapshot path is taken.
                patch("ai_cli.quota._try_read_kv_snapshot", return_value=None),
            ):
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

    def test_when_under_pace_then_shows_green_signed_delta_and_steady_arrow(self, tmp_path, capsys, frozen_week):
        """delta < -5 uses the green truecolor anchor and preserves its negative sign."""
        import ai_cli.quota_db as qdb

        # Pin `now` to 50% through the billing week so week_elapsed_pct ≈ 50%
        # and delta = 5 - 50 = -45 << -5.
        _, week_start_dt = frozen_week
        fixed_now = week_start_dt + timedelta(days=3, hours=12)  # 50% of 7 days

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            with patch("datetime.datetime") as MockDT:
                MockDT.now.return_value = fixed_now
                MockDT.strptime.side_effect = datetime.strptime
                MockDT.fromisoformat.side_effect = datetime.fromisoformat
                qdb.record_quota_snapshot(usage_percent=5.0, weekly_sonnet_pct=40.0)
                result = quota_statusline_part()
            assert result == 0
            out = capsys.readouterr().out
            assert "5%" in out
            assert "\033[38;2;34;197;94m" in out  # green truecolor anchor = on track
            assert re.search(r"-\d+%", out)  # signed negative delta, plain percent (AIH-784)
            assert "→" in out  # steady: only 1 snapshot, no acceleration data
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_when_over_pace_then_shows_red_signed_delta(self, tmp_path, capsys, frozen_week):
        """delta > +10 uses the red truecolor anchor and preserves its positive sign."""
        import ai_cli.quota_db as qdb

        # Pin `now` to 25h into the week (post-seedling) so normal-phase bands apply.
        # week_elapsed_pct ≈ 14.9%, delta = 95 - 14.9 ≈ 80 >> 5 → RED
        _, week_start_dt = frozen_week
        fixed_now = week_start_dt + timedelta(hours=25)

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            with patch("datetime.datetime") as MockDT:
                MockDT.now.return_value = fixed_now
                MockDT.strptime.side_effect = datetime.strptime
                MockDT.fromisoformat.side_effect = datetime.fromisoformat
                qdb.record_quota_snapshot(usage_percent=95.0, weekly_sonnet_pct=40.0)
                result = quota_statusline_part()
            assert result == 0
            out = capsys.readouterr().out
            assert "95%" in out
            assert "\033[38;2;220;38;38m" in out  # red truecolor anchor = significantly over pace
            assert re.search(r"\+\d+%", out)  # signed positive delta, plain percent (AIH-784)
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_when_seedling_calm_then_shows_leaf_and_blue_delta(self, tmp_path, capsys, frozen_week):
        """First 24h, delta < 10% → 🌱 icon, no alert, delta shown (blue = no ANSI color assertion needed)."""
        import ai_cli.quota_db as qdb

        _, week_start_dt = frozen_week
        fixed_now = week_start_dt + timedelta(hours=6)  # 6h in — seedling, delta = 5-3.6 = 1.4%

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            with patch("datetime.datetime") as MockDT:
                MockDT.now.return_value = fixed_now
                MockDT.strptime.side_effect = datetime.strptime
                MockDT.fromisoformat.side_effect = datetime.fromisoformat
                qdb.record_quota_snapshot(usage_percent=5.0, weekly_sonnet_pct=40.0)
                result = quota_statusline_part()
            assert result == 0
            out = capsys.readouterr().out
            assert "🌱" in out
            assert "✅" not in out
            assert "🚨" not in out

        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_when_seedling_elevated_then_shows_leaf_no_alarm(self, tmp_path, capsys, frozen_week):
        """First 24h: 🌱 always shown regardless of delta — no alarms during seedling phase."""
        import ai_cli.quota_db as qdb

        _, week_start_dt = frozen_week
        fixed_now = week_start_dt + timedelta(hours=6)  # elapsed ~3.6%, usage 18% → delta ≈ 14%

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            with patch("datetime.datetime") as MockDT:
                MockDT.now.return_value = fixed_now
                MockDT.strptime.side_effect = datetime.strptime
                MockDT.fromisoformat.side_effect = datetime.fromisoformat
                qdb.record_quota_snapshot(usage_percent=18.0, weekly_sonnet_pct=40.0)
                result = quota_statusline_part()
            assert result == 0
            out = capsys.readouterr().out
            assert "🌱" in out
            assert "⚠️" not in out
            assert "🚨" not in out
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_when_seedling_high_delta_then_shows_leaf_no_alarm(self, tmp_path, capsys, frozen_week):
        """First 24h, even high delta → 🌱 with no alarm icon (informational only)."""
        import ai_cli.quota_db as qdb

        _, week_start_dt = frozen_week
        fixed_now = week_start_dt + timedelta(hours=6)  # elapsed ~3.6%, usage 30% → delta ≈ 26%

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            with patch("datetime.datetime") as MockDT:
                MockDT.now.return_value = fixed_now
                MockDT.strptime.side_effect = datetime.strptime
                MockDT.fromisoformat.side_effect = datetime.fromisoformat
                qdb.record_quota_snapshot(usage_percent=30.0, weekly_sonnet_pct=40.0)
                result = quota_statusline_part()
            assert result == 0
            out = capsys.readouterr().out
            assert "🌱" in out
            assert "⚠️" not in out
            assert "🚨" not in out
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_when_accelerating_then_shows_up_arrow(self, tmp_path, capsys, frozen_week):
        """Three snapshots with increasing burn rate → ↑ arrow."""
        import ai_cli.quota_db as qdb

        week_start, week_start_dt = frozen_week
        # 30h into the week, so all three snapshots sit inside it with room to spare.
        fixed_now = week_start_dt + timedelta(hours=30)
        qdb.set_db_path(tmp_path / "quota.db")
        try:
            conn = qdb._get_conn()
            # Snapshots spaced 30 min apart: slow burn then fast burn
            # t2 (oldest): 20% at -60min, t1: 21% at -30min (rate=2%/hr), t0: 23% at now (rate=4%/hr)
            # accel = 4 - 2 = 2 %/hr > 1.0 → ↑
            for mins_ago, pct in [(60, 20.0), (30, 21.0), (0, 23.0)]:
                ts = (fixed_now - timedelta(minutes=mins_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
                conn.execute(
                    "INSERT INTO quota_snapshots (week_start, usage_percent, weekly_sonnet_pct, snapshotted_at) VALUES (?,?,?,?)",
                    (week_start, pct, 40.0, ts),
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
            assert "↑" in out
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_when_decelerating_then_shows_down_arrow(self, tmp_path, capsys, frozen_week):
        """Three snapshots with decreasing burn rate → ↓ arrow."""
        import ai_cli.quota_db as qdb

        week_start, week_start_dt = frozen_week
        fixed_now = week_start_dt + timedelta(hours=30)
        qdb.set_db_path(tmp_path / "quota.db")
        try:
            conn = qdb._get_conn()
            # t2: 20% at -60min, t1: 23% at -30min (rate=6%/hr), t0: 24% at now (rate=2%/hr)
            # accel = 2 - 6 = -4 %/hr < -1.0 → ↓
            for mins_ago, pct in [(60, 20.0), (30, 23.0), (0, 24.0)]:
                ts = (fixed_now - timedelta(minutes=mins_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
                conn.execute(
                    "INSERT INTO quota_snapshots (week_start, usage_percent, weekly_sonnet_pct, snapshotted_at) VALUES (?,?,?,?)",
                    (week_start, pct, 40.0, ts),
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

    def test_when_snapshot_stale_then_shows_clock_indicator(self, tmp_path, capsys, frozen_week):
        """Snapshot >2h old → ⏱ stale indicator appended to output."""
        import ai_cli.quota_db as qdb

        week_start_str, week_start_dt = frozen_week
        fixed_now = week_start_dt + timedelta(hours=30)  # post-seedling

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            # Insert a snapshot timestamped 3h before fixed_now
            conn = qdb._get_conn()
            stale_ts = (fixed_now - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
            conn.execute(
                "INSERT INTO quota_snapshots (week_start, usage_percent, weekly_sonnet_pct, snapshotted_at) VALUES (?,?,?,?)",
                (week_start_str, 40.0, 40.0, stale_ts),
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

    def test_when_snapshot_fresh_then_no_clock_indicator(self, tmp_path, capsys, frozen_week):
        """Snapshot <2h old → no ⏱ indicator."""
        import ai_cli.quota_db as qdb

        week_start_str, week_start_dt = frozen_week
        fixed_now = week_start_dt + timedelta(hours=30)

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            conn = qdb._get_conn()
            fresh_ts = (fixed_now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
            conn.execute(
                "INSERT INTO quota_snapshots (week_start, usage_percent, weekly_sonnet_pct, snapshotted_at) VALUES (?,?,?,?)",
                (week_start_str, 40.0, 40.0, fresh_ts),
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
            qdb.record_quota_snapshot(usage_percent=50.0, weekly_sonnet_pct=40.0)
            with patch("ai_cli.quota._maybe_trigger_background_scrape") as mock_trigger:
                quota_statusline_part()
            mock_trigger.assert_called_once()
            ts_arg = mock_trigger.call_args[0][0]
            assert isinstance(ts_arg, str) and len(ts_arg) > 0
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    # AI-CLI-180 AC-3: the guard on the injection above. Every scenario in this class is
    # positioned relative to `frozen_week`, so if that fixture ever silently goes back to
    # reading the real clock, the whole class becomes non-hermetic again and the failure
    # resurfaces as a ~1-in-4 flake under xdist rather than as an honest red test. These two
    # tests pin the property directly: move the *real* clock somewhere far away and the
    # fixture's answer must not move with it.
    #
    # Both freeze dates are checked because a single one cannot distinguish "frozen" from
    # "coincidentally equal" — they are six months apart and in different quota weeks, and
    # each test carries its own positive control asserting the real-clock derivation really
    # does differ there. Without that control the assertion could pass vacuously.
    @staticmethod
    def _assert_frozen_under(real_clock_iso, request):
        from freezegun import freeze_time

        import ai_cli.quota_db as qdb

        expected_week_start = qdb._get_current_week_start(now=TestQuotaStatuslinePart._FIXED_REFERENCE_NOW)
        with freeze_time(real_clock_iso):
            # Positive control: at this real instant the wall-clock derivation gives a
            # DIFFERENT week, so the assertion below can genuinely fail.
            assert qdb._get_current_week_start() != expected_week_start, (
                f"probe is vacuous — real clock at {real_clock_iso} already yields {expected_week_start}"
            )
            # Fixture is created lazily here, i.e. while the real clock is frozen far away.
            week_start_str, week_start_dt = request.getfixturevalue("frozen_week")
        assert week_start_str == expected_week_start
        assert week_start_dt == datetime.strptime(expected_week_start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)

    def test_when_real_clock_is_far_in_the_past_then_frozen_week_is_unchanged(self, request):
        """The injected clock ignores the real one — no wall-clock read survives in this class."""
        self._assert_frozen_under("2026-03-01T00:00:00Z", request)

    def test_when_real_clock_is_far_in_the_future_then_frozen_week_is_unchanged(self, request):
        """Same property from the other side of the reference instant."""
        self._assert_frozen_under("2026-09-01T00:00:00Z", request)


class TestQuotaStatuslinePartLegacyDb:
    """AI-CLI-56 regression: quota_statusline_part must survive legacy DBs missing weekly_sonnet_pct."""

    def test_when_legacy_db_without_sonnet_col_then_produces_quota_output(self, tmp_path, capsys):
        """quota_statusline_part must not silently fail on a DB created before weekly_sonnet_pct.

        Without the fix, SELECT weekly_sonnet_pct from an old table raises OperationalError, which
        the outer except catches silently — producing empty stdout. Empty stdout bypasses the 30s
        bash cache and causes every statusLine render to fire the 1.4s Python call, resulting in
        overlapping blocking calls that produce duplicate prompt boxes in the scrollback buffer.
        """
        import sqlite3

        import ai_cli.quota_db as qdb

        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE quota_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usage_percent REAL NOT NULL,
                session_pct REAL,
                extra_pct REAL,
                week_start TEXT NOT NULL,
                snapshotted_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS weekly_state (
                week_start TEXT PRIMARY KEY,
                total_consumed INTEGER NOT NULL DEFAULT 0,
                last_snapshot_at TEXT,
                reset_at TEXT
            )
            """
        )
        week_start = qdb._get_current_week_start()
        now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "INSERT INTO quota_snapshots (usage_percent, session_pct, extra_pct, week_start, snapshotted_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (42.0, None, None, week_start, now_iso),
        )
        conn.commit()
        conn.close()

        qdb.set_db_path(db_path)
        try:
            with patch("ai_cli.quota._try_read_kv_snapshot", return_value=None):
                result = quota_statusline_part()
            out = capsys.readouterr().out
            assert result == 0
            assert "42%" in out, (
                f"Expected quota output but got empty — legacy DB migration likely missing. out={out!r}"
            )
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_when_legacy_db_and_empty_output_then_does_not_raise(self, tmp_path, capsys):
        """quota_statusline_part must return 0 even when the DB has no snapshots at all."""
        import sqlite3

        import ai_cli.quota_db as qdb

        db_path = tmp_path / "empty_legacy.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE quota_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usage_percent REAL NOT NULL,
                session_pct REAL,
                extra_pct REAL,
                week_start TEXT NOT NULL,
                snapshotted_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS weekly_state (
                week_start TEXT PRIMARY KEY,
                total_consumed INTEGER NOT NULL DEFAULT 0,
                last_snapshot_at TEXT,
                reset_at TEXT
            )
            """
        )
        conn.commit()
        conn.close()

        qdb.set_db_path(db_path)
        try:
            with (
                patch("ai_cli.quota._try_read_kv_snapshot", return_value=None),
                patch("ai_cli.quota._launch_background_scrape"),
            ):
                result = quota_statusline_part()
            assert result == 0
            out = capsys.readouterr().out
            assert "📊" in out
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]


class TestTryReadKvSnapshot:
    def test_returns_none_when_nats_servers_not_configured(self):
        """_try_read_kv_snapshot returns None when config has no nats_servers."""
        with patch("ai_cli.config.load_config", return_value={}):
            result = _try_read_kv_snapshot()
        assert result is None

    def test_returns_none_on_load_config_failure(self):
        """_try_read_kv_snapshot returns None when load_config raises."""
        with patch("ai_cli.config.load_config", side_effect=Exception("no config")):
            result = _try_read_kv_snapshot()
        assert result is None


class TestQuotaStatuslinePartKvSync:
    def test_stale_local_data_uses_fresher_kv_value(self, tmp_path, capsys):
        """When local SQLite is stale and NATS KV has a fresher value, the KV value is used."""
        import time
        from datetime import datetime, timedelta

        import ai_cli.quota as q
        import ai_cli.quota_db as qdb

        qdb.set_db_path(tmp_path / "quota.db")
        try:
            # Insert a stale local snapshot (older than TTL) using the real schema
            stale_time = datetime.now(UTC) - timedelta(minutes=q._SCRAPE_TTL_MINUTES + 10)
            stale_ts = stale_time.strftime("%Y-%m-%dT%H:%M:%SZ")
            week_start = qdb._get_current_week_start()
            # Use _get_conn() to initialise the full schema, then insert with all columns
            conn = qdb._get_conn()
            conn.execute(
                "INSERT INTO quota_snapshots "
                "(usage_percent, session_pct, weekly_sonnet_pct, extra_pct, week_start, snapshotted_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (10.0, None, None, None, week_start, stale_ts),
            )
            conn.commit()

            # KV has a fresher value (higher usage_percent and more recent ts)
            kv_payload = {
                "usage_percent": 42.0,
                "ts": time.time(),
                "session_pct": None,
                "weekly_sonnet_pct": 55.0,
                "extra_pct": None,
                "reset_at": None,
            }
            with (
                patch("ai_cli.quota._try_read_kv_snapshot", return_value=kv_payload),
                patch("ai_cli.quota._maybe_trigger_background_scrape"),
            ):
                result = quota_statusline_part()
            assert result == 0
            out = capsys.readouterr().out
            assert "42%" in out
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

    def test_when_no_remote_host_then_returns_ex_config(self, capsys):
        """Missing host/user in config → returns EX_CONFIG without running SSH."""
        with (
            patch("ai_cli.quota.subprocess.run") as mock_run,
            patch("ai_cli.config.load_config", return_value={"remote": {}}),
        ):
            result = quota_sync_from_remote()

        assert result == EX_CONFIG
        mock_run.assert_not_called()

    def test_when_ssh_fails_then_returns_ex_tempfail(self, capsys):
        """SSH non-zero exit → returns EX_TEMPFAIL with error message."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Connection refused"
        with (
            patch("ai_cli.quota.subprocess.run", return_value=mock_result),
            patch("ai_cli.config.load_config", return_value=self._make_config()),
        ):
            result = quota_sync_from_remote()

        assert result == EX_TEMPFAIL
        out = capsys.readouterr()
        assert "remote command failed" in out.err

    def test_when_ssh_raises_then_returns_ex_tempfail(self, capsys):
        """SSH raises (e.g. timeout) → returns EX_TEMPFAIL with error message."""
        with (
            patch("ai_cli.quota.subprocess.run", side_effect=TimeoutError("timed out")),
            patch("ai_cli.config.load_config", return_value=self._make_config()),
        ):
            result = quota_sync_from_remote()

        assert result == EX_TEMPFAIL
        out = capsys.readouterr()
        assert "SSH failed" in out.err

    def test_when_remote_output_empty_then_returns_0(self, capsys):
        """SSH succeeds but remote DB empty → returns 0 with informational message."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        with (
            patch("ai_cli.quota.subprocess.run", return_value=mock_result),
            patch("ai_cli.config.load_config", return_value=self._make_config()),
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
                patch("ai_cli.config.load_config", return_value=self._make_config()),
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
                patch("ai_cli.config.load_config", return_value=self._make_config()),
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
                "ai_cli.config.load_config",
                return_value=self._make_config(identity="~/.ssh/id_ed25519"),
            ),
        ):
            quota_sync_from_remote()

        call_args = mock_run.call_args[0][0]
        assert "-i" in call_args

    def test_when_config_load_fails_then_returns_ex_config(self, capsys):
        """Exception loading config → returns EX_CONFIG."""
        with patch("ai_cli.config.load_config", side_effect=RuntimeError("no config")):
            result = quota_sync_from_remote()

        assert result == EX_CONFIG
        out = capsys.readouterr()
        assert "could not load config" in out.err


# --- _maybe_trigger_background_scrape ---


@pytest.mark.real_quota_scrape
class TestMaybeBackgroundScrape:
    def _stale_ts(self, minutes_ago: int = 35) -> str:
        now = datetime.now(UTC)
        return (now - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _fresh_ts(self, minutes_ago: int = 5) -> str:
        now = datetime.now(UTC)
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


# --- statusline single-line contract ---


class TestQuotaStatuslinePartSingleLine:
    """quota_statusline_part() must always emit exactly one line.

    CC's statusLine hook contract requires single-line output. Multi-line output causes
    CC to leave orphaned rows in the scrollback buffer on every re-render, producing the
    duplicate-boxes symptom (AI-CLI-56). These tests enforce the contract at the Python level.
    """

    # AI-CLI-180: a fixed reference instant, not the real clock — `hours_elapsed` below is only
    # meaningful relative to a week_start that does not move between the two derivations.
    _FIXED_REFERENCE_NOW = datetime(2026, 1, 5, 12, 0, 0, tzinfo=UTC)  # Monday noon UTC

    def _run_and_capture(self, usage_percent, hours_elapsed, tmp_path, capsys):
        import ai_cli.quota_db as qdb

        week_start_str = qdb._get_current_week_start(now=self._FIXED_REFERENCE_NOW)
        week_start_dt = datetime.strptime(week_start_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        fixed_now = week_start_dt + timedelta(hours=hours_elapsed)
        qdb.set_db_path(tmp_path / "quota.db")
        try:
            with patch("datetime.datetime") as MockDT:
                MockDT.now.return_value = fixed_now
                MockDT.strptime.side_effect = datetime.strptime
                MockDT.fromisoformat.side_effect = datetime.fromisoformat
                qdb.record_quota_snapshot(usage_percent=usage_percent)
                quota_statusline_part()
            return capsys.readouterr().out
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_when_normal_phase_output_is_single_line(self, tmp_path, capsys):
        """Normal phase (>24h elapsed): output must be exactly one non-empty line."""
        out = self._run_and_capture(50.0, hours_elapsed=25, tmp_path=tmp_path, capsys=capsys)
        non_empty = [l for l in out.split("\n") if l]
        assert len(non_empty) == 1, f"Expected 1 line, got {len(non_empty)}: {out!r}"

    def test_when_seedling_phase_output_is_single_line(self, tmp_path, capsys):
        """Seedling phase (<24h elapsed): output must be exactly one non-empty line."""
        out = self._run_and_capture(10.0, hours_elapsed=6, tmp_path=tmp_path, capsys=capsys)
        non_empty = [l for l in out.split("\n") if l]
        assert len(non_empty) == 1, f"Expected 1 line, got {len(non_empty)}: {out!r}"

    def test_when_no_data_placeholder_is_single_line(self, tmp_path, capsys):
        """No snapshot data: placeholder output must also be a single line."""
        import ai_cli.quota as q
        import ai_cli.quota_db as qdb

        qdb.set_db_path(tmp_path / "quota.db")
        qdb._get_conn().close()
        q._SCRAPE_LOCK_PATH.unlink(missing_ok=True)
        try:
            with patch("subprocess.Popen"):
                quota_statusline_part()
            out = capsys.readouterr().out
            non_empty = [l for l in out.split("\n") if l]
            assert len(non_empty) == 1, f"Expected 1 line, got {len(non_empty)}: {out!r}"
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]
            q._SCRAPE_LOCK_PATH.unlink(missing_ok=True)


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not available")
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="statusline-command.sh requires a POSIX bash environment and utilities",
)
class TestStatuslineScript:
    """Integration tests for statusline-command.sh.

    CC's statusLine hook renders the script's stdout as the status bar. Two invariants
    must hold to prevent duplicate-box artifacts in the scrollback buffer (AI-CLI-56):

    1. Output must be exactly one line — multi-line output spans multiple rows and CC
       only erases one row per re-render, leaving orphaned rows that accumulate.
    2. Output must end with ESC[K (erase-to-EOL) — when CC positions the cursor at the
       start of the status row and writes our output, ESC[K clears any leftover characters
       from a previously longer render, preventing stale character artifacts.
    """

    _SCRIPT = Path(__file__).parent.parent / "src/ai_cli/data/statusline-command.sh"
    _SAMPLE_INPUT = (
        '{"model":"claude-sonnet-4-6",'
        '"context_window":{"used_percentage":42,"total_input_tokens":1000,'
        '"total_output_tokens":200,"current_usage":{'
        '"cache_creation_input_tokens":0,"cache_read_input_tokens":0}},'
        '"workspace":{"project_dir":"/tmp"}}'
    )

    def _run(self, stdin=None, extra_env=None):
        env = {
            **os.environ,
            "TMUX": "",
            "COLUMNS": "200",
            "GIT_BRANCH_CACHE": "main",
            # The quota block only runs on the subscription branch. Pin it rather than
            # inheriting the developer's CC_BILLING_MODE, which routes the script down
            # the api/cache-hit branch and skips the quota code these tests exercise.
            "CC_BILLING_MODE": "subscription",
        }
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", str(self._SCRIPT)],
            input=stdin or self._SAMPLE_INPUT,
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
            check=False,
        )

    def test_given_valid_input_when_run_then_exits_zero(self):
        result = self._run()
        assert result.returncode == 0, f"stderr: {result.stderr!r}"

    def test_given_valid_input_when_run_then_outputs_exactly_one_line(self):
        """Enforces the single-line contract — multi-line output breaks CC's statusLine rendering."""
        result = self._run()
        assert result.returncode == 0
        non_empty = [line for line in result.stdout.split("\n") if line]
        assert len(non_empty) == 1, (
            f"statusline-command.sh must output exactly 1 line; got {len(non_empty)}: {result.stdout!r}"
        )

    def test_given_valid_input_when_run_then_output_ends_with_erase_to_eol(self):
        """ESC[K at end of output clears leftover chars when CC overwrites the status line in place."""
        result = self._run()
        assert result.returncode == 0
        output = result.stdout.rstrip("\n")
        assert output.endswith("\033[K"), f"statusline output must end with ESC[K but ends with: {output[-20:]!r}"

    def test_given_malformed_json_when_run_then_outputs_one_line(self):
        """Malformed jq input degrades gracefully — must still produce exactly one line."""
        result = self._run(stdin="not-valid-json")
        assert result.returncode == 0
        non_empty = [line for line in result.stdout.split("\n") if line]
        assert len(non_empty) == 1, f"Expected 1 line on bad JSON input, got {len(non_empty)}: {result.stdout!r}"

    def test_given_quota_part_with_embedded_newline_when_assembled_then_single_line(self):
        """Embedded newline in quota_part output is stripped — single-line invariant holds."""
        fake_ai = self._SCRIPT.parent.parent.parent.parent / "tests" / "_fake_ai_newline.sh"
        fake_ai.write_text(
            '#!/usr/bin/env bash\nif [[ "$*" == *"statusline-part"* ]]; then printf "line1\\nline2\\n"; fi\n'
        )
        fake_ai.chmod(0o755)
        fake_bin = fake_ai.parent / "_fake_bin_newline"
        fake_bin.mkdir(exist_ok=True)
        (fake_bin / "ai").symlink_to(fake_ai)
        try:
            result = self._run(extra_env={"PATH": f"{fake_bin}:{os.environ['PATH']}"})
            assert result.returncode == 0
            non_empty = [line for line in result.stdout.split("\n") if line]
            assert len(non_empty) == 1, f"Embedded newline in quota_part must be stripped; got: {result.stdout!r}"
        finally:
            fake_ai.unlink(missing_ok=True)
            (fake_bin / "ai").unlink(missing_ok=True)
            fake_bin.rmdir()

    def test_given_fresh_quota_cache_when_run_then_ai_statusline_part_not_called(self):
        """When the quota cache file is fresh (<30s), ai quota statusline-part is not called.

        Cache format: line 1 = Unix timestamp, line 2 = quota output (no stat() dependency).
        """
        import tempfile
        import time

        uid = os.getuid() if hasattr(os, "getuid") else 0
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / f".ai-sl-quota-{uid}"
            # Write cache in the format the script expects: timestamp on line 1, content on line 2.
            cache_file.write_text(f"{int(time.time())}\n42% on-track")

            sentinel = Path(tmpdir) / "ai_statusline_called"
            fake_ai = Path(tmpdir) / "ai"
            fake_ai.write_text(
                f'#!/usr/bin/env bash\nif [[ "$*" == *"statusline-part"* ]]; then touch "{sentinel}"; fi\n'
            )
            fake_ai.chmod(0o755)

            result = self._run(
                extra_env={
                    "PATH": f"{tmpdir}:{os.environ['PATH']}",
                    "TMPDIR": tmpdir,
                }
            )
            assert result.returncode == 0
            assert not sentinel.exists(), "ai quota statusline-part should NOT be called when cache is fresh"

    def test_given_stale_quota_cache_when_run_then_ai_statusline_part_called(self):
        """When the quota cache timestamp is >30s old, ai quota statusline-part is called as a background refresh.

        Stale-while-revalidate: the stale value is returned immediately, and one background refresh
        fires asynchronously. The sentinel is created by the background job, so we wait briefly.
        """
        import tempfile
        import time

        uid = os.getuid() if hasattr(os, "getuid") else 0
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / f".ai-sl-quota-{uid}"
            # Write cache with a timestamp 35s in the past — stale zone (30s–300s).
            old_ts = int(time.time()) - 35
            cache_file.write_text(f"{old_ts}\nstale-value")

            sentinel = Path(tmpdir) / "ai_statusline_called"
            fake_ai = Path(tmpdir) / "ai"
            fake_ai.write_text(
                f'#!/usr/bin/env bash\nif [[ "$*" == *"statusline-part"* ]]; then touch "{sentinel}"; echo ""; fi\n'
            )
            fake_ai.chmod(0o755)

            result = self._run(
                extra_env={
                    "PATH": f"{tmpdir}:{os.environ['PATH']}",
                    "TMPDIR": tmpdir,
                }
            )
            assert result.returncode == 0
            # Background refresh fires asynchronously — wait for it to complete.
            time.sleep(1.5)
            assert sentinel.exists(), (
                "ai quota statusline-part should be called as a background refresh when cache is stale (>30s)"
            )


class TestQuotaStatuslinePartAdaptiveLabels:
    """Statusline labels and primary weekly pace output."""

    # AI-CLI-158: a fixed reference instant, NOT real wall-clock time. _capture() must establish
    # week_start_str from a deterministic "now" — passing no `now` makes _get_current_week_start()
    # fall back to datetime.now(timezone.utc), so the "fixed" hours_elapsed offset below was
    # silently computed relative to whatever real moment the suite happened to run, making the
    # scenario non-hermetic (a real regression: -79% expected became -76% as real time drifted).
    _FIXED_REFERENCE_NOW = datetime(2026, 1, 5, 12, 0, 0, tzinfo=UTC)  # arbitrary Monday noon UTC

    def _capture(self, usage_percent, hours_elapsed, tmp_path, capsys, cols=0):
        import os

        import ai_cli.quota_db as qdb

        week_start_str = qdb._get_current_week_start(now=self._FIXED_REFERENCE_NOW)
        week_start_dt = datetime.strptime(week_start_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        fixed_now = week_start_dt + timedelta(hours=hours_elapsed)
        qdb.set_db_path(tmp_path / "quota.db")
        try:
            with patch("datetime.datetime") as MockDT:
                MockDT.now.return_value = fixed_now
                MockDT.strptime.side_effect = datetime.strptime
                MockDT.fromisoformat.side_effect = datetime.fromisoformat
                qdb.record_quota_snapshot(usage_percent=usage_percent)
                with patch("ai_cli.quota._launch_background_scrape"):
                    with patch.dict(os.environ, {"AI_CLI_STATUSLINE_COLS": str(cols)}):
                        quota_statusline_part()
            return capsys.readouterr().out
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_narrow_terminal_uses_ccwk_label(self, tmp_path, capsys):
        """The primary label is fixed regardless of terminal width."""
        out = self._capture(42.0, hours_elapsed=30, tmp_path=tmp_path, capsys=capsys, cols=79)
        assert "ccWk" in out
        assert "Week" not in out
        assert "ccF" not in out and "ccS" not in out

    def test_wide_terminal_still_uses_ccwk_label(self, tmp_path, capsys):
        """Terminal width does not change the primary label."""
        out = self._capture(42.0, hours_elapsed=30, tmp_path=tmp_path, capsys=capsys, cols=80)
        assert "ccWk" in out
        assert "Week" not in out
        assert "ccF" not in out and "ccS" not in out

    def test_weekly_pace_shows_signed_percentage_points(self, tmp_path, capsys):
        """Under pace keeps the negative sign; color remains a supporting signal."""
        # ~161h elapsed = ~96% of week; usage 17% → delta = 17 - 96 ≈ -79% (way under pace)
        out = self._capture(17.0, hours_elapsed=161, tmp_path=tmp_path, capsys=capsys, cols=120)
        import re

        clean = re.sub(r"\033\[[0-9;]*m", "", out)
        assert "→ -79%" in clean
        assert "\033[38;2;34;197;94m" in out  # green truecolor anchor = on track

    def test_zero_cols_still_uses_ccwk_label(self, tmp_path, capsys):
        """An unset width does not change the primary label."""
        out = self._capture(42.0, hours_elapsed=30, tmp_path=tmp_path, capsys=capsys, cols=0)
        assert "ccWk" in out
        assert "Week" not in out
        assert "ccF" not in out and "ccS" not in out

    def test_labels_appear_left_of_percentages(self, tmp_path, capsys):
        """Label (W or Week) appears before the usage percentage in the output."""
        out = self._capture(42.0, hours_elapsed=30, tmp_path=tmp_path, capsys=capsys, cols=0)
        # Strip ANSI codes to check order
        import re

        clean = re.sub(r"\033\[[0-9;]*m", "", out)
        w_pos = clean.index("W")
        pct_pos = clean.index("42%")
        assert w_pos < pct_pos, f"Label W should precede 42% but got: {clean!r}"
