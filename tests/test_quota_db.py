"""Tests for quota_db — SQLite storage layer for Claude usage telemetry."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import ai_cli.quota_db as quota_db


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path):
    """Each test gets a fresh in-memory quota DB."""
    db_path = tmp_path / "quota.db"
    quota_db.set_db_path(db_path)
    yield
    quota_db.set_db_path(None)  # type: ignore[arg-type]


# --- _get_current_week_start ---


class TestGetCurrentWeekStart:
    def test_when_before_anchor_then_returns_prior_week_start(self):
        now = datetime(2026, 4, 2, 12, 0, 0, tzinfo=timezone.utc)
        with patch(
            "ai_cli.quota_db._get_reset_anchor_utc", return_value=datetime(2026, 4, 4, 6, 0, 0, tzinfo=timezone.utc)
        ):
            result = quota_db._get_current_week_start(now)
        assert result == "2026-03-28T06:00:00Z"

    def test_when_after_anchor_then_returns_anchor(self):
        now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)
        with patch(
            "ai_cli.quota_db._get_reset_anchor_utc", return_value=datetime(2026, 4, 4, 6, 0, 0, tzinfo=timezone.utc)
        ):
            result = quota_db._get_current_week_start(now)
        assert result == "2026-04-04T06:00:00Z"

    def test_when_exact_reset_boundary_then_returns_that_reset(self):
        now = datetime(2026, 4, 4, 6, 0, 0, tzinfo=timezone.utc)
        with patch(
            "ai_cli.quota_db._get_reset_anchor_utc", return_value=datetime(2026, 4, 4, 6, 0, 0, tzinfo=timezone.utc)
        ):
            result = quota_db._get_current_week_start(now)
        assert result == "2026-04-04T06:00:00Z"

    def test_when_two_weeks_after_anchor_then_returns_correct_week(self):
        now = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
        with patch(
            "ai_cli.quota_db._get_reset_anchor_utc", return_value=datetime(2026, 4, 4, 6, 0, 0, tzinfo=timezone.utc)
        ):
            result = quota_db._get_current_week_start(now)
        assert result == "2026-04-11T06:00:00Z"


# --- _get_reset_at ---


class TestGetResetAt:
    def test_when_before_anchor_then_returns_anchor(self):
        now = datetime(2026, 4, 2, 12, 0, 0, tzinfo=timezone.utc)
        with patch(
            "ai_cli.quota_db._get_reset_anchor_utc", return_value=datetime(2026, 4, 4, 6, 0, 0, tzinfo=timezone.utc)
        ):
            result = quota_db._get_reset_at(now)
        assert result == "2026-04-04T06:00:00Z"

    def test_when_after_anchor_then_returns_next_reset(self):
        now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)
        with patch(
            "ai_cli.quota_db._get_reset_anchor_utc", return_value=datetime(2026, 4, 4, 6, 0, 0, tzinfo=timezone.utc)
        ):
            result = quota_db._get_reset_at(now)
        assert result == "2026-04-11T06:00:00Z"


# --- record_usage ---


class TestRecordUsage:
    def test_when_first_record_then_delta_equals_total(self):
        quota_db.record_usage("sess-1", "hetzner", "claude-sonnet", 1000)
        conn = quota_db._get_conn()
        row = conn.execute("SELECT delta_tokens FROM usage_records WHERE session_id = 'sess-1'").fetchone()
        conn.close()
        assert row["delta_tokens"] == 1000

    def test_when_second_record_for_same_session_then_delta_is_increment(self):
        quota_db.record_usage("sess-1", "hetzner", "claude-sonnet", 1000)
        quota_db.record_usage("sess-1", "hetzner", "claude-sonnet", 1500)
        conn = quota_db._get_conn()
        rows = conn.execute("SELECT delta_tokens FROM usage_records WHERE session_id = 'sess-1' ORDER BY id").fetchall()
        conn.close()
        assert rows[0]["delta_tokens"] == 1000
        assert rows[1]["delta_tokens"] == 500

    def test_when_record_inserted_then_weekly_state_updated(self):
        quota_db.record_usage("sess-1", "hetzner", "claude-sonnet", 800)
        conn = quota_db._get_conn()
        row = conn.execute("SELECT total_consumed FROM weekly_state").fetchone()
        conn.close()
        assert row["total_consumed"] == 800

    def test_when_multiple_sessions_then_weekly_state_accumulates(self):
        quota_db.record_usage("sess-1", "hetzner", "claude-sonnet", 400)
        quota_db.record_usage("sess-2", "mac", "claude-opus", 600)
        conn = quota_db._get_conn()
        row = conn.execute("SELECT total_consumed FROM weekly_state").fetchone()
        conn.close()
        assert row["total_consumed"] == 1000

    def test_when_total_tokens_decreases_then_delta_is_zero(self):
        """Defensive: if reported total decreases (e.g. new session), delta should not be negative."""
        quota_db.record_usage("sess-1", "hetzner", "claude-sonnet", 1000)
        quota_db.record_usage("sess-1", "hetzner", "claude-sonnet", 800)
        conn = quota_db._get_conn()
        rows = conn.execute("SELECT delta_tokens FROM usage_records WHERE session_id = 'sess-1' ORDER BY id").fetchall()
        conn.close()
        assert rows[1]["delta_tokens"] == 0


# --- record_quota_snapshot ---


class TestRecordQuotaSnapshot:
    def test_when_snapshot_recorded_then_stored_in_db(self):
        quota_db.record_quota_snapshot(
            usage_percent=72.5,
            session_pct=15.0,
            weekly_sonnet_pct=40.0,
            extra_pct=0.0,
        )
        conn = quota_db._get_conn()
        row = conn.execute("SELECT * FROM quota_snapshots").fetchone()
        conn.close()
        assert row["usage_percent"] == 72.5
        assert row["session_pct"] == 15.0
        assert row["weekly_sonnet_pct"] == 40.0
        assert row["extra_pct"] == 0.0

    def test_when_snapshot_recorded_then_weekly_state_updated(self):
        quota_db.record_quota_snapshot(usage_percent=50.0)
        conn = quota_db._get_conn()
        row = conn.execute("SELECT last_snapshot_at FROM weekly_state").fetchone()
        conn.close()
        assert row["last_snapshot_at"] is not None

    def test_when_optional_fields_omitted_then_stored_as_null(self):
        quota_db.record_quota_snapshot(usage_percent=30.0)
        conn = quota_db._get_conn()
        row = conn.execute("SELECT session_pct, weekly_sonnet_pct, extra_pct FROM quota_snapshots").fetchone()
        conn.close()
        assert row["session_pct"] is None
        assert row["weekly_sonnet_pct"] is None
        assert row["extra_pct"] is None


# --- compute_burn_rate ---


class TestComputeBurnRate:
    def _week_start(self):
        with patch(
            "ai_cli.quota_db._get_reset_anchor_utc", return_value=datetime(2026, 4, 4, 6, 0, 0, tzinfo=timezone.utc)
        ):
            return quota_db._get_current_week_start(datetime(2026, 4, 5, 0, 0, 0, tzinfo=timezone.utc))

    def test_when_no_snapshots_then_returns_zeros(self):
        result = quota_db.compute_burn_rate("2026-04-04T06:00:00Z")
        assert result["actual_pct_per_day"] == 0.0
        assert result["expected_pct_per_day"] == 0.0
        assert result["multiplier"] == 0.0

    def test_when_one_snapshot_then_estimates_from_week_start(self):
        # Insert a snapshot manually at 1 day after week start
        conn = quota_db._get_conn()
        conn.execute(
            "INSERT INTO quota_snapshots (usage_percent, week_start, snapshotted_at) VALUES (?, ?, ?)",
            (14.3, "2026-04-04T06:00:00Z", "2026-04-05T06:00:00Z"),
        )
        conn.commit()
        conn.close()

        result = quota_db.compute_burn_rate("2026-04-04T06:00:00Z")
        assert result["latest_percent"] == 14.3
        assert abs(result["actual_pct_per_day"] - 14.3) < 0.1  # 14.3% over 1 day

    def test_when_two_snapshots_then_computes_rate_from_delta(self):
        conn = quota_db._get_conn()
        conn.execute(
            "INSERT INTO quota_snapshots (usage_percent, week_start, snapshotted_at) VALUES (?, ?, ?)",
            (10.0, "2026-04-04T06:00:00Z", "2026-04-04T06:00:00Z"),
        )
        conn.execute(
            "INSERT INTO quota_snapshots (usage_percent, week_start, snapshotted_at) VALUES (?, ?, ?)",
            (24.3, "2026-04-04T06:00:00Z", "2026-04-05T06:00:00Z"),
        )
        conn.commit()
        conn.close()

        result = quota_db.compute_burn_rate("2026-04-04T06:00:00Z")
        assert abs(result["actual_pct_per_day"] - 14.3) < 0.1
        assert result["expected_pct_per_day"] == pytest.approx(100.0 / 7.0)
        assert result["multiplier"] > 0


# --- get_current_status ---


class TestGetCurrentStatus:
    def test_when_no_data_then_returns_empty_state(self):
        result = quota_db.get_current_status()
        assert result["latest_snapshot"] is None
        assert result["total_consumed"] == 0
        assert result["per_session_breakdown"] == []
        assert result["alerts"] == []

    def test_when_snapshot_exists_then_returns_it(self):
        quota_db.record_quota_snapshot(usage_percent=60.0)
        result = quota_db.get_current_status()
        assert result["latest_snapshot"] is not None
        assert result["latest_snapshot"]["usage_percent"] == 60.0

    def test_when_usage_over_50_then_alert_generated(self):
        quota_db.record_quota_snapshot(usage_percent=55.0)
        result = quota_db.get_current_status()
        assert any("half" in a for a in result["alerts"])

    def test_when_usage_over_75_then_higher_alert_generated(self):
        quota_db.record_quota_snapshot(usage_percent=80.0)
        result = quota_db.get_current_status()
        assert any("approaching" in a for a in result["alerts"])

    def test_when_usage_over_90_then_critical_alert_generated(self):
        quota_db.record_quota_snapshot(usage_percent=92.0)
        result = quota_db.get_current_status()
        assert any("critical" in a for a in result["alerts"])

    def test_when_usage_records_exist_then_per_session_breakdown_populated(self):
        quota_db.record_usage("sess-A", "hetzner", "claude-sonnet", 500)
        quota_db.record_usage("sess-B", "mac", "claude-opus", 1200)
        result = quota_db.get_current_status()
        assert len(result["per_session_breakdown"]) == 2

    def test_when_called_then_days_remaining_is_positive(self):
        result = quota_db.get_current_status()
        assert result["days_remaining"] >= 0


# --- get_weekly_history ---


class TestGetWeeklyHistory:
    def test_when_no_data_then_returns_empty_list(self):
        result = quota_db.get_weekly_history()
        assert result == []

    def test_when_snapshots_exist_then_groups_by_week(self):
        conn = quota_db._get_conn()
        conn.execute(
            "INSERT INTO quota_snapshots (usage_percent, week_start, snapshotted_at) VALUES (?, ?, ?)",
            (30.0, "2026-04-04T06:00:00Z", "2026-04-05T06:00:00Z"),
        )
        conn.execute(
            "INSERT INTO quota_snapshots (usage_percent, week_start, snapshotted_at) VALUES (?, ?, ?)",
            (55.0, "2026-04-04T06:00:00Z", "2026-04-06T06:00:00Z"),
        )
        conn.commit()
        conn.close()

        result = quota_db.get_weekly_history()
        assert len(result) == 1
        assert result[0]["week_start"] == "2026-04-04T06:00:00Z"
        assert result[0]["peak_percent"] == 55.0
        assert result[0]["snapshot_count"] == 2


# --- _get_reset_anchor_utc ---


class TestGetResetAnchorUtc:
    def test_when_load_config_raises_then_uses_default(self):
        """Exception in load_config falls back to _DEFAULT_RESET_ANCHOR (lines 27-28)."""
        with patch("ai_cli.main.load_config", side_effect=Exception("no config")):
            result = quota_db._get_reset_anchor_utc()
        assert result == datetime(2026, 4, 4, 6, 0, 0, tzinfo=timezone.utc)


# --- _get_quota_db_path ---


class TestGetQuotaDbPath:
    def test_when_no_override_then_uses_home_state_dir(self, tmp_path):
        """Lines 44-46: default path is ~/.local/state/ai-cli/quota.db."""
        # Temporarily clear the module-level override set by autouse fixture
        with patch.object(quota_db, "_DB_PATH_OVERRIDE", None):
            with patch("pathlib.Path.home", return_value=tmp_path):
                result = quota_db._get_quota_db_path()
        expected = tmp_path / ".local" / "state" / "ai-cli" / "quota.db"
        assert result == expected
        assert (tmp_path / ".local" / "state" / "ai-cli").exists()


# --- _get_current_week_start now=None ---


class TestGetCurrentWeekStartNowNone:
    def test_when_now_is_none_then_returns_valid_iso_string(self):
        """Line 98: now=None path uses datetime.now(). Verifies the branch runs."""
        anchor = datetime(2026, 4, 4, 6, 0, 0, tzinfo=timezone.utc)
        with patch("ai_cli.quota_db._get_reset_anchor_utc", return_value=anchor):
            result = quota_db._get_current_week_start()  # no now arg
        assert result.endswith("Z")
        parsed = datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ")
        assert parsed is not None


# --- _get_reset_at now=None and reset <= now edge case ---


class TestGetResetAtEdgeCases:
    def test_when_now_is_none_then_returns_valid_iso_string(self):
        """Line 114: now=None path uses datetime.now(). Verifies the branch runs."""
        anchor = datetime(2026, 4, 4, 6, 0, 0, tzinfo=timezone.utc)
        with patch("ai_cli.quota_db._get_reset_anchor_utc", return_value=anchor):
            result = quota_db._get_reset_at()  # no now arg
        assert result.endswith("Z")
        parsed = datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ")
        assert parsed is not None

    def test_when_reset_equals_now_then_advances_by_one_week(self):
        """Line 121: when computed reset == now, it must advance by one week."""
        from datetime import timedelta
        anchor = datetime(2026, 4, 4, 6, 0, 0, tzinfo=timezone.utc)
        # now is exactly one week before anchor:
        # diff = WEEK_SECONDS, periods = 1, reset = anchor - 1w = now → reset <= now
        now = anchor - timedelta(seconds=quota_db._WEEK_SECONDS)
        with patch("ai_cli.quota_db._get_reset_anchor_utc", return_value=anchor):
            result = quota_db._get_reset_at(now=now)
        assert result == "2026-04-04T06:00:00Z"
