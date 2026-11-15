"""Tests for quota tracking: statusline rendering, snapshot publishing, sync, status and record."""

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
    _publish_quota_snapshot,
    _try_read_kv_snapshot,
    quota_history,
    quota_record,
    quota_status,
    quota_statusline_part,
    quota_sync_from_remote,
)

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


# --- _get_claude_usage_snapshot ---


# Real `claude -p /usage` print-mode output (CC v2.1.207): inline "label: N% used · resets"
# form, per-model line always present. This is the deterministic replacement for the flaky
# interactive-TUI scrape.


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


# --- _run_nats_quota_listener ---


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

    def test_given_no_snapshot_when_rendered_then_prints_nothing_and_spawns_no_process(self, tmp_path, capsys):
        """No seven-day value from any source renders nothing; the statusline never starts a scrape."""
        import ai_cli.quota_db as qdb

        qdb.set_db_path(tmp_path / "quota.db")
        qdb._get_conn().close()
        try:
            with (
                patch("subprocess.Popen") as mock_popen,
                patch("subprocess.run") as mock_run,
                patch("ai_cli.quota._try_read_kv_snapshot", return_value=None),
            ):
                result = quota_statusline_part()
            assert result == 0
            assert capsys.readouterr().out == ""
            mock_popen.assert_not_called()
            mock_run.assert_not_called()
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

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
            assert re.search(r"-\d+%", out)  # signed negative delta, plain percent
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
            assert re.search(r"\+\d+%", out)  # signed positive delta, plain percent
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

    def test_given_stale_snapshot_without_native_input_when_rendered_then_spawns_no_process(
        self, tmp_path, capsys, monkeypatch
    ):
        """An hours-old snapshot and no rate_limits input once fired a scrape; now nothing is spawned."""
        import ai_cli.quota_db as qdb

        monkeypatch.delenv("AI_CLI_QUOTA_SEVEN_DAY_PCT", raising=False)
        qdb.set_db_path(tmp_path / "quota.db")
        try:
            conn = qdb._get_conn()
            stale = (datetime.now(UTC) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
            conn.execute(
                "INSERT INTO quota_snapshots (usage_percent, week_start, snapshotted_at) VALUES (?, ?, ?)",
                (50.0, qdb._get_current_week_start(), stale),
            )
            conn.commit()
            conn.close()
            with (
                patch("subprocess.Popen") as mock_popen,
                patch("subprocess.run") as mock_run,
                patch("ai_cli.quota._try_read_kv_snapshot", return_value=None),
            ):
                quota_statusline_part()
            assert "50%" in capsys.readouterr().out
            mock_popen.assert_not_called()
            mock_run.assert_not_called()
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
            ):
                result = quota_statusline_part()
            assert result == 0
            out = capsys.readouterr().out
            assert out == ""
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
            stale_time = datetime.now(UTC) - timedelta(minutes=q._LOCAL_SNAPSHOT_TTL_MINUTES + 10)
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


@pytest.mark.skipif(sys.platform == "win32", reason="the remote side of `ai quota sync` is a POSIX shell over ssh")
class TestQuotaSyncRemoteWithoutSqliteCli:
    """The remote side must not need the sqlite3 command line tool (Python's module is enough)."""

    _SEED = (
        (55.0, 10.0, 30.0, None, "2026-04-07T00:00:00Z", "2026-04-07T10:00:00Z"),
        (60.0, None, None, 5.0, "2026-04-07T00:00:00Z", "2026-04-07T11:00:00Z"),
    )

    @pytest.fixture
    def remote_home(self, tmp_path):
        """A fake remote home holding a seeded quota DB, plus a PATH that has python3 but no sqlite3."""
        import sqlite3

        home = tmp_path / "remote-home"
        db_path = home / ".local" / "state" / "ai-cli" / "quota.db"
        db_path.parent.mkdir(parents=True)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE quota_snapshots (usage_percent REAL, session_pct REAL, weekly_sonnet_pct REAL,"
            " extra_pct REAL, week_start TEXT, snapshotted_at TEXT)"
        )
        conn.executemany("INSERT INTO quota_snapshots VALUES (?, ?, ?, ?, ?, ?)", self._SEED)
        conn.commit()
        conn.close()

        bin_dir = tmp_path / "remote-bin"
        bin_dir.mkdir()
        (bin_dir / "python3").symlink_to(sys.executable)
        assert shutil.which("sqlite3", path=str(bin_dir)) is None
        return {"HOME": str(home), "PATH": str(bin_dir)}

    @staticmethod
    def _ssh_runs_remote_command_locally(env):
        """A stand-in for `subprocess.run` that runs ssh's remote command under `env` instead of over ssh."""

        real_run = subprocess.run  # bound now: `ai_cli.quota.subprocess.run` is patched while the fake runs

        def fake_run(cmd, **kwargs):
            return real_run(["/bin/sh", "-c", cmd[-1]], env=env, capture_output=True, text=True, check=False)

        return fake_run

    def test_given_no_sqlite3_cli_on_remote_when_syncing_then_rows_are_pulled(self, tmp_path, remote_home, capsys):
        import ai_cli.quota_db as qdb

        qdb.set_db_path(tmp_path / "local-quota.db")
        try:
            with (
                patch("ai_cli.quota.subprocess.run", side_effect=self._ssh_runs_remote_command_locally(remote_home)),
                patch(
                    "ai_cli.config.load_config",
                    return_value={"remote": {"host": "remote.example.com", "user": "testuser", "port": 22}},
                ),
            ):
                result = quota_sync_from_remote()

            out = capsys.readouterr()
            assert result == 0, out.err
            assert "2 new snapshot" in out.out

            conn = qdb._get_conn()
            rows = conn.execute(
                "SELECT usage_percent, session_pct, weekly_sonnet_pct, extra_pct, week_start, snapshotted_at"
                " FROM quota_snapshots ORDER BY snapshotted_at"
            ).fetchall()
            conn.close()
            assert [tuple(r) for r in rows] == list(self._SEED)
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

    def test_given_no_remote_quota_db_when_syncing_then_it_fails_without_creating_one(self, tmp_path, remote_home):
        import ai_cli.quota_db as qdb

        missing_home = tmp_path / "empty-home"
        missing_home.mkdir()
        env = {**remote_home, "HOME": str(missing_home)}
        qdb.set_db_path(tmp_path / "local-quota.db")
        try:
            with (
                patch("ai_cli.quota.subprocess.run", side_effect=self._ssh_runs_remote_command_locally(env)),
                patch(
                    "ai_cli.config.load_config",
                    return_value={"remote": {"host": "remote.example.com", "user": "testuser", "port": 22}},
                ),
            ):
                result = quota_sync_from_remote()
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]

        assert result == EX_TEMPFAIL
        assert not (missing_home / ".local").exists()


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

    def test_when_no_data_then_output_is_empty(self, tmp_path, capsys):
        """No snapshot data: nothing is printed, so no stray line reaches the statusline."""
        import ai_cli.quota_db as qdb

        qdb.set_db_path(tmp_path / "quota.db")
        qdb._get_conn().close()
        try:
            with patch("ai_cli.quota._try_read_kv_snapshot", return_value=None):
                quota_statusline_part()
            assert capsys.readouterr().out == ""
        finally:
            qdb.set_db_path(None)  # type: ignore[arg-type]


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

    @pytest.fixture(autouse=True)
    def _sandbox(self, tmp_path):
        # The script shells out to `ai quota statusline-part` / `ai quota record` and
        # writes $HOME/.claude/state and $TMPDIR caches. Without a sandbox every run
        # starts the developer's installed `ai` (Python startup, twice) and writes their
        # real home and /tmp, which is both a host-state leak and what pushed these runs
        # past their deadline under load (AI-CLI-2z9x).
        self._home = tmp_path / "home"
        self._home.mkdir()
        self._tmpdir = tmp_path / "tmp"
        self._tmpdir.mkdir()
        self._bin = tmp_path / "bin"
        self._bin.mkdir()
        self._ai_calls = tmp_path / "ai-calls"
        self._fake_ai(
            f'echo "$*" >> "{self._ai_calls}"\nif [[ "$*" == *"statusline-part"* ]]; then echo "42% on-track"; fi'
        )

    def _fake_ai(self, body):
        fake = self._bin / "ai"
        fake.write_text(f"#!/usr/bin/env bash\n{body}\n")
        fake.chmod(0o755)

    def _run(self, stdin=None):
        env = {
            **os.environ,
            "HOME": str(self._home),
            "TMPDIR": str(self._tmpdir),
            "PATH": f"{self._bin}{os.pathsep}{os.environ['PATH']}",
            "TMUX": "",
            "COLUMNS": "200",
            "GIT_BRANCH_CACHE": "main",
            # The quota block only runs on the subscription branch. Pin it rather than
            # inheriting the developer's CC_BILLING_MODE, which routes the script down
            # the api/cache-hit branch and skips the quota code these tests exercise.
            "CC_BILLING_MODE": "subscription",
        }
        return subprocess.run(
            ["bash", str(self._SCRIPT)],
            input=stdin or self._SAMPLE_INPUT,
            capture_output=True,
            text=True,
            env=env,
            # A hang guard, not a speed check. The script execs ~40 external commands
            # (jq, date, stat, cut, the stub `ai`); on a loaded host with an endpoint
            # scanner each exec costs 0.2-0.8 s, and a sandboxed run measured 4.1 s at a
            # load of 9.8 on 10 cores. 15 s left under 4x headroom for a parallel run.
            timeout=60,
            check=False,
        )

    def _quota_cache(self):
        uid = os.getuid() if hasattr(os, "getuid") else 0
        return self._tmpdir / f".ai-sl-quota-{uid}"

    def test_given_the_suite_runs_the_script_when_it_fetches_quota_then_only_the_sandboxed_ai_and_home_are_touched(
        self,
    ):
        result = self._run()
        assert result.returncode == 0, f"stderr: {result.stderr!r}"
        assert "statusline-part" in self._ai_calls.read_text()
        assert self._quota_cache().read_text().endswith("42% on-track")

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
        self._fake_ai('if [[ "$*" == *"statusline-part"* ]]; then printf "line1\\nline2\\n"; fi')
        result = self._run()
        assert result.returncode == 0
        non_empty = [line for line in result.stdout.split("\n") if line]
        assert len(non_empty) == 1, f"Embedded newline in quota_part must be stripped; got: {result.stdout!r}"

    def test_given_fresh_quota_cache_when_run_then_ai_statusline_part_not_called(self):
        """When the quota cache file is fresh (<30s), ai quota statusline-part is not called.

        Cache format: line 1 = Unix timestamp, line 2 = quota output (no stat() dependency).
        """
        self._quota_cache().write_text(f"{int(time.time())}\n42% on-track")
        sentinel = self._tmpdir / "ai_statusline_called"
        self._fake_ai(f'if [[ "$*" == *"statusline-part"* ]]; then touch "{sentinel}"; fi')

        result = self._run()
        assert result.returncode == 0
        assert not sentinel.exists(), "ai quota statusline-part should NOT be called when cache is fresh"

    def test_given_stale_quota_cache_when_run_then_ai_statusline_part_called(self):
        """When the quota cache timestamp is >30s old, ai quota statusline-part is called as a background refresh.

        Stale-while-revalidate: the stale value is returned immediately, and one background refresh
        fires asynchronously, so the test waits for its sentinel rather than for a fixed interval.
        """
        # Timestamp 35s in the past — stale zone (30s–300s).
        self._quota_cache().write_text(f"{int(time.time()) - 35}\nstale-value")
        sentinel = self._tmpdir / "ai_statusline_called"
        self._fake_ai(f'if [[ "$*" == *"statusline-part"* ]]; then touch "{sentinel}"; echo ""; fi')

        result = self._run()
        assert result.returncode == 0
        deadline = time.monotonic() + 15
        while not sentinel.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
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
