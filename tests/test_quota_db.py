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
    def test_when_load_config_raises_then_uses_default(self, tmp_path):
        """Exception in load_config falls back to _DEFAULT_RESET_ANCHOR."""
        missing = tmp_path / "no-anchor.txt"
        with patch.object(quota_db, "_get_reset_anchor_path", return_value=missing):
            with patch("ai_cli.config.load_config", side_effect=Exception("no config")):
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


# --- log_notification ---


class TestLogNotification:
    def test_when_called_then_row_inserted(self):
        quota_db.log_notification(
            source="quota-watch",
            title="Claude quota 75% threshold crossed",
            body="Weekly: 76.0%",
            priority="high",
            tags=["warning"],
            channels_attempted=["discord", "ntfy"],
            channels_succeeded=["discord"],
            channels_failed=["ntfy"],
        )
        conn = quota_db._get_conn()
        row = conn.execute("SELECT * FROM notification_log").fetchone()
        conn.close()
        assert row is not None
        assert row["source"] == "quota-watch"
        assert row["title"] == "Claude quota 75% threshold crossed"
        assert row["priority"] == "high"

    def test_when_multiple_rows_then_all_stored(self):
        for i in range(3):
            quota_db.log_notification(
                source=f"source-{i}",
                title=f"Title {i}",
                body="Body",
                priority="default",
                tags=[],
                channels_attempted=["discord"],
                channels_succeeded=["discord"],
                channels_failed=[],
            )
        conn = quota_db._get_conn()
        rows = conn.execute("SELECT COUNT(*) FROM notification_log").fetchone()
        conn.close()
        assert rows[0] == 3

    def test_when_tags_empty_then_null_stored(self):
        quota_db.log_notification(
            source="test",
            title="Test",
            body="Body",
            priority="default",
            tags=[],
            channels_attempted=[],
            channels_succeeded=[],
            channels_failed=[],
        )
        conn = quota_db._get_conn()
        row = conn.execute("SELECT tags FROM notification_log").fetchone()
        conn.close()
        assert row["tags"] is None


# --- query_notification_log ---


class TestQueryNotificationLog:
    def _insert(
        self,
        *,
        source="quota-watch",
        title="Alert",
        priority="high",
        channels_attempted=None,
        channels_succeeded=None,
        channels_failed=None,
        fired_at=None,
    ):
        if channels_attempted is None:
            channels_attempted = ["discord"]
        if channels_succeeded is None:
            channels_succeeded = ["discord"]
        if channels_failed is None:
            channels_failed = []
        quota_db.log_notification(
            source=source,
            title=title,
            body="Body",
            priority=priority,
            tags=[],
            channels_attempted=channels_attempted,
            channels_succeeded=channels_succeeded,
            channels_failed=channels_failed,
        )
        if fired_at:
            conn = quota_db._get_conn()
            conn.execute(
                "UPDATE notification_log SET fired_at = ? WHERE id = (SELECT MAX(id) FROM notification_log)",
                (fired_at,),
            )
            conn.commit()
            conn.close()

    def test_when_no_rows_then_returns_empty(self):
        rows = quota_db.query_notification_log()
        assert rows == []

    def test_when_rows_exist_then_returns_last_n(self):
        for i in range(15):
            self._insert(title=f"Alert {i}")
        rows = quota_db.query_notification_log(last=5)
        assert len(rows) == 5

    def test_when_channels_are_deserialized_as_lists(self):
        self._insert(
            channels_attempted=["discord", "ntfy"],
            channels_succeeded=["discord"],
            channels_failed=["ntfy"],
        )
        rows = quota_db.query_notification_log()
        assert isinstance(rows[0]["channels_attempted"], list)
        assert rows[0]["channels_attempted"] == ["discord", "ntfy"]
        assert rows[0]["channels_succeeded"] == ["discord"]
        assert rows[0]["channels_failed"] == ["ntfy"]

    def test_when_source_filter_applied_then_only_matching_rows(self):
        self._insert(source="quota-watch")
        self._insert(source="manual")
        rows = quota_db.query_notification_log(source="quota-watch")
        assert all(r["source"] == "quota-watch" for r in rows)
        assert len(rows) == 1

    def test_when_failed_only_then_only_rows_with_failures(self):
        self._insert(channels_failed=[])
        self._insert(channels_failed=["ntfy"])
        rows = quota_db.query_notification_log(failed_only=True)
        assert len(rows) == 1
        assert rows[0]["channels_failed"] == ["ntfy"]

    def test_when_since_relative_2h_then_filters_old_rows(self):
        from datetime import datetime as _dt
        from datetime import timedelta as _td
        from datetime import timezone as _tz

        # Insert old row (3h ago)
        old_ts = (_dt.now(_tz.utc) - _td(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._insert(title="Old", fired_at=old_ts)
        # Insert recent row (30min ago)
        recent_ts = (_dt.now(_tz.utc) - _td(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._insert(title="Recent", fired_at=recent_ts)
        rows = quota_db.query_notification_log(since="2h")
        titles = [r["title"] for r in rows]
        assert "Recent" in titles
        assert "Old" not in titles


# --- _parse_since_datetime ---


class TestParseSinceDatetime:
    def test_when_2h_then_returns_2_hours_ago(self):
        from datetime import timedelta

        result = quota_db._parse_since_datetime("2h")
        assert result is not None
        dt = datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        expected = datetime.now(timezone.utc) - timedelta(hours=2)
        assert abs((dt - expected).total_seconds()) < 5

    def test_when_30m_then_returns_30_minutes_ago(self):
        from datetime import timedelta

        result = quota_db._parse_since_datetime("30m")
        assert result is not None
        dt = datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        expected = datetime.now(timezone.utc) - timedelta(minutes=30)
        assert abs((dt - expected).total_seconds()) < 5

    def test_when_1d_then_returns_1_day_ago(self):
        from datetime import timedelta

        result = quota_db._parse_since_datetime("1d")
        assert result is not None
        dt = datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        expected = datetime.now(timezone.utc) - timedelta(days=1)
        assert abs((dt - expected).total_seconds()) < 5

    def test_when_yesterday_then_returns_start_of_yesterday(self):
        result = quota_db._parse_since_datetime("yesterday")
        assert result is not None
        assert "T00:00:00Z" in result

    def test_when_iso_datetime_then_passes_through(self):
        result = quota_db._parse_since_datetime("2026-04-20T10:00:00Z")
        assert result == "2026-04-20T10:00:00Z"

    def test_when_date_only_then_appends_time(self):
        result = quota_db._parse_since_datetime("2026-04-20")
        assert result == "2026-04-20T00:00:00Z"

    def test_when_unknown_format_then_returns_none(self):
        result = quota_db._parse_since_datetime("garbage")
        assert result is None
