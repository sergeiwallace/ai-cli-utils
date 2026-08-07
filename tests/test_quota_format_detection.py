"""Tests for AI-CLI-68: scrape format-change detection.

Covers:
- _record_scrape_format_mismatch: writes debug file and increments DB counter
- _clear_scrape_format_mismatch: resets counter to 0
- quota_statusline_part: prepends 🚨 BROKEN 🚨 prefix when mismatch count > 0
- quota_statusline_part: no prefix when mismatch count is 0
- _parse_reset_datetime: advances one week when time-only format is past
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

import ai_cli.quota_db as quota_db
from ai_cli.quota import (
    _SCRAPER_BROKEN_PREFIX,
    _clear_scrape_format_mismatch,
    _parse_reset_datetime,
    _record_scrape_format_mismatch,
    quota_statusline_part,
)
from ai_cli.quota_db import _get_quota_meta, _set_quota_meta


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path):
    """Each test gets a fresh quota DB."""
    db_path = tmp_path / "quota.db"
    quota_db.set_db_path(db_path)
    yield
    quota_db.set_db_path(None)  # type: ignore[arg-type]


# --- _record_scrape_format_mismatch ---


class TestRecordScrapeMismatch:
    def test_writes_raw_output_to_debug_file(self, tmp_path: Path):
        raw = "some unexpected CC /usage output"
        debug_path = tmp_path / "quota-scrape-debug.txt"
        with patch("ai_cli.quota._SCRAPE_DEBUG_PATH", debug_path):
            _record_scrape_format_mismatch(raw)
        assert debug_path.exists()
        assert debug_path.read_text(encoding="utf-8") == raw

    def test_increments_count_from_zero(self, tmp_path: Path):
        debug_path = tmp_path / "quota-scrape-debug.txt"
        with patch("ai_cli.quota._SCRAPE_DEBUG_PATH", debug_path):
            _record_scrape_format_mismatch("output")
        count = _get_quota_meta("scrape_format_mismatch_count")
        assert count == "1"

    def test_increments_count_across_multiple_calls(self, tmp_path: Path):
        debug_path = tmp_path / "quota-scrape-debug.txt"
        with patch("ai_cli.quota._SCRAPE_DEBUG_PATH", debug_path):
            _record_scrape_format_mismatch("output 1")
            _record_scrape_format_mismatch("output 2")
            _record_scrape_format_mismatch("output 3")
        count = _get_quota_meta("scrape_format_mismatch_count")
        assert count == "3"

    def test_overwrites_debug_file_with_latest_raw(self, tmp_path: Path):
        debug_path = tmp_path / "quota-scrape-debug.txt"
        with patch("ai_cli.quota._SCRAPE_DEBUG_PATH", debug_path):
            _record_scrape_format_mismatch("first output")
            _record_scrape_format_mismatch("second output")
        assert debug_path.read_text(encoding="utf-8") == "second output"

    def test_sets_mismatch_at_timestamp(self, tmp_path: Path):
        debug_path = tmp_path / "quota-scrape-debug.txt"
        with patch("ai_cli.quota._SCRAPE_DEBUG_PATH", debug_path):
            _record_scrape_format_mismatch("output")
        at = _get_quota_meta("scrape_format_mismatch_at")
        assert at is not None
        # Should be a valid ISO UTC timestamp
        dt = datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ")
        assert dt.year >= 2026


# --- _clear_scrape_format_mismatch ---


class TestClearScrapeMismatch:
    def test_resets_count_to_zero(self, tmp_path: Path):
        debug_path = tmp_path / "quota-scrape-debug.txt"
        with patch("ai_cli.quota._SCRAPE_DEBUG_PATH", debug_path):
            _record_scrape_format_mismatch("output")
        # Confirm it was set
        assert _get_quota_meta("scrape_format_mismatch_count") == "1"

        _clear_scrape_format_mismatch()
        assert _get_quota_meta("scrape_format_mismatch_count") == "0"

    def test_reset_when_already_zero_is_idempotent(self):
        _set_quota_meta("scrape_format_mismatch_count", "0")
        _clear_scrape_format_mismatch()
        assert _get_quota_meta("scrape_format_mismatch_count") == "0"

    def test_reset_when_count_is_high(self, tmp_path: Path):
        debug_path = tmp_path / "quota-scrape-debug.txt"
        with patch("ai_cli.quota._SCRAPE_DEBUG_PATH", debug_path):
            for _ in range(5):
                _record_scrape_format_mismatch("output")
        _clear_scrape_format_mismatch()
        assert _get_quota_meta("scrape_format_mismatch_count") == "0"


# --- quota_statusline_part: broken indicator ---


class TestStatuslineBrokenIndicator:
    """The statusline must prepend 🚨 BROKEN 🚨 when mismatch count > 0."""

    def _capture_statusline(self) -> str:
        buf = StringIO()
        with patch.object(sys, "stdout", buf):
            quota_statusline_part()
        return buf.getvalue()

    def test_prepends_broken_prefix_when_mismatch_count_positive(self):
        _set_quota_meta("scrape_format_mismatch_count", "1")
        output = self._capture_statusline()
        assert output.startswith(_SCRAPER_BROKEN_PREFIX)

    def test_broken_prefix_present_for_count_greater_than_one(self):
        _set_quota_meta("scrape_format_mismatch_count", "5")
        output = self._capture_statusline()
        assert _SCRAPER_BROKEN_PREFIX in output

    def test_no_broken_prefix_when_count_is_zero(self):
        _set_quota_meta("scrape_format_mismatch_count", "0")
        # Also mock scraping to avoid tmux calls — statusline should work without data
        with patch("ai_cli.quota._launch_background_scrape"):
            output = self._capture_statusline()
        assert _SCRAPER_BROKEN_PREFIX not in output

    def test_no_broken_prefix_when_key_absent(self):
        # Key not set at all — no mismatch flag
        with patch("ai_cli.quota._launch_background_scrape"):
            output = self._capture_statusline()
        assert _SCRAPER_BROKEN_PREFIX not in output

    def test_quota_data_still_rendered_after_prefix(self):
        """Quota data must appear to the right of the prefix — not replaced."""
        from ai_cli.quota_db import record_quota_snapshot

        _set_quota_meta("scrape_format_mismatch_count", "2")
        record_quota_snapshot(usage_percent=42.0)
        output = self._capture_statusline()
        # Prefix comes first
        assert output.startswith(_SCRAPER_BROKEN_PREFIX)
        # Quota data follows — 42% should appear somewhere
        assert "42" in output


# --- _parse_reset_datetime: week-advancement when time is past ---


class TestParseResetDatetimePastTime:
    """Old CC format ('Resets 6:59am') must advance by one week if time is past."""

    def test_when_time_already_passed_today_then_result_is_future(self):
        text = "  Current week (all models) · Resets 6:59am \n  65% used\n"
        result = _parse_reset_datetime(text)
        assert result is not None
        dt = datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        assert dt > datetime.now(UTC)

    def test_when_time_passed_result_is_approximately_one_week_out(self):
        """If 6:59am has passed today, the result should be 6–8 days from 6:59am today."""
        from datetime import timedelta

        import freezegun

        text = "  Current week (all models) · Resets 6:59am \n  65% used\n"
        # Freeze at noon on a Tuesday — 6:59am is definitely in the past
        with freezegun.freeze_time("2026-04-28 12:00:00"):
            result = _parse_reset_datetime(text)

        assert result is not None
        result_dt = datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        noon_utc = datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)
        diff = result_dt - noon_utc
        # One week minus the ~5h elapsed since 6:59am, so roughly 6.7–7.3 days ahead
        assert timedelta(days=6) <= diff <= timedelta(days=8)

    def test_minutes_preserved_after_week_advancement(self):
        text = "  Current week (all models) · Resets 11:45pm \n  10% used\n"
        result = _parse_reset_datetime(text)
        assert result is not None
        assert ":45:00Z" in result

    def test_when_time_is_future_today_no_week_added(self):
        """If reset time is still in the future today, result should be within 24h."""
        from datetime import timedelta

        text = "  Current week (all models) · Resets 11:59pm \n  10% used\n"
        result = _parse_reset_datetime(text)
        assert result is not None
        dt = datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        now_utc = datetime.now(UTC)
        diff = dt - now_utc
        # Should be in the future and at most 7 days away (7 if just passed 11:59pm)
        assert timedelta(0) < diff <= timedelta(days=7, hours=1)
