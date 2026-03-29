"""Tests for telemetry module."""

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from ai_cli.telemetry import init_db, write_event, record_event, _is_enabled, _SCHEMA


class TestTelemetryDB:
    def test_init_db_when_called_then_creates_events_table(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'")
        assert cursor.fetchone() is not None
        conn.close()

    def test_init_db_when_called_then_uses_wal_mode(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        conn.close()

    def test_write_event_when_valid_then_persists(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)
        write_event(conn, "telemetry.action.click", {"button": "save"},
                    machine="test-host", session="c-sw-1", ts=1000.0)

        row = conn.execute("SELECT * FROM events").fetchone()
        assert row is not None
        assert row[1] == 1000.0  # ts
        assert row[2] == "telemetry.action.click"  # subject
        assert row[3] == "test-host"  # machine
        assert row[4] == "c-sw-1"  # session
        assert json.loads(row[5]) == {"button": "save"}  # data
        conn.close()

    def test_write_event_when_multiple_then_all_stored(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)
        for i in range(5):
            write_event(conn, f"telemetry.action.event{i}", {"i": i},
                        machine="host", ts=float(i))

        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        assert count == 5
        conn.close()


class TestRecordEvent:
    def test_record_event_when_disabled_then_returns_false(self):
        with patch("ai_cli.telemetry._is_enabled", return_value=False):
            result = record_event("click", {"button": "save"})
        assert result is False

    def test_record_event_when_enabled_then_writes_to_db(self, tmp_path):
        db_path = tmp_path / "test.db"
        with patch("ai_cli.telemetry._is_enabled", return_value=True):
            with patch("ai_cli.telemetry._DB_PATH", db_path):
                with patch("ai_cli.telemetry._get_machine_id", return_value="test"):
                    # Mock NATS to avoid connection
                    from nats.errors import NoServersError
                    with patch("nats.connect", side_effect=NoServersError):
                        with patch("asyncio.sleep"):
                            result = record_event("click", {"button": "save"})

        assert result is True
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT subject, data FROM events").fetchone()
        assert row[0] == "telemetry.action.click"
        assert json.loads(row[1]) == {"button": "save"}
        conn.close()


class TestIsEnabled:
    def test_is_enabled_when_config_true_then_true(self):
        with patch("ai_cli.main.load_config", return_value={"telemetry": {"enabled": True}}):
            assert _is_enabled() is True

    def test_is_enabled_when_config_false_then_false(self):
        with patch("ai_cli.main.load_config", return_value={"telemetry": {"enabled": False}}):
            assert _is_enabled() is False

    def test_is_enabled_when_no_config_then_defaults_true(self):
        with patch("ai_cli.main.load_config", return_value={}):
            assert _is_enabled() is True
