"""Tests for telemetry module."""

import json
import sqlite3
from unittest.mock import patch, MagicMock

from ai_cli.telemetry import init_db, write_event, record_event, _is_enabled


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
        write_event(
            conn, "telemetry.action.click", {"button": "save"}, machine="test-host", session="c-sw-1", ts=1000.0
        )

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
            write_event(conn, f"telemetry.action.event{i}", {"i": i}, machine="host", ts=float(i))

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

    def test_is_enabled_when_load_config_raises_then_defaults_true(self):
        """Lines 44-45: exception path in _is_enabled."""
        # _is_enabled imports load_config via relative import: from .main import load_config
        # We need to patch it where it's looked up
        with patch("ai_cli.main.load_config", side_effect=RuntimeError("broken")):
            # Force re-evaluation by calling directly
            result = _is_enabled()
        assert result is True


class TestGetMachineId:
    def test_get_machine_id_when_called_then_returns_hostname(self):
        """Line 34: actual body of _get_machine_id."""
        from ai_cli.telemetry import _get_machine_id
        import socket

        result = _get_machine_id()
        assert result == socket.gethostname()
        assert isinstance(result, str)
        assert len(result) > 0


class TestRecordEventExceptions:
    def test_record_event_when_init_db_raises_then_still_returns_true(self, tmp_path):
        """Lines 107-108: SQLite exception path in record_event."""
        with patch("ai_cli.telemetry._is_enabled", return_value=True):
            with patch("ai_cli.telemetry.init_db", side_effect=sqlite3.OperationalError("disk full")):
                with patch("ai_cli.telemetry._get_machine_id", return_value="test"):
                    with patch("ai_cli.messaging.NATSClient", side_effect=Exception("no nats")):
                        result = record_event("click", {"button": "save"})
        assert result is True


class TestTelemetryWriter:
    def test_telemetry_writer_when_already_running_then_returns_2(self):
        with patch("ai_cli.sync._acquire_pid_file", return_value=False):
            from ai_cli.telemetry import telemetry_writer

            result = telemetry_writer()
        assert result == 2

    def test_telemetry_writer_when_nats_unavailable_then_returns_1(self, tmp_path):
        mock_client = MagicMock()
        mock_client.nc = None

        async def fake_connect():
            pass  # nc stays None

        mock_client.connect = fake_connect

        with (
            patch("ai_cli.sync._acquire_pid_file", return_value=True),
            patch("ai_cli.sync._release_pid_file"),
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("ai_cli.telemetry.init_db") as mock_init_db,
        ):
            mock_conn = MagicMock()
            mock_init_db.return_value = mock_conn

            from ai_cli.telemetry import telemetry_writer

            result = telemetry_writer()
        assert result == 1
        mock_conn.close.assert_called_once()

    def test_telemetry_writer_when_interrupt_then_returns_0(self, tmp_path):
        mock_client = MagicMock()
        mock_client.nc = None

        async def fake_connect():
            mock_client.nc = MagicMock()

        async def fake_subscribe(*args, **kwargs):
            raise KeyboardInterrupt

        mock_client.connect = fake_connect
        mock_client.subscribe_durable = fake_subscribe

        with (
            patch("ai_cli.sync._acquire_pid_file", return_value=True),
            patch("ai_cli.sync._release_pid_file") as mock_release,
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("ai_cli.telemetry.init_db") as mock_init_db,
        ):
            mock_conn = MagicMock()
            mock_init_db.return_value = mock_conn

            from ai_cli.telemetry import telemetry_writer

            result = telemetry_writer()
        assert result == 0
        mock_conn.close.assert_called_once()
        mock_release.assert_called_with("telemetry-writer")

    def test_telemetry_writer_when_event_received_then_writes_to_db(self, tmp_path):
        """Covers lines 139-149 (on_event body) and 158 (return True after subscribe_durable)."""
        mock_client = MagicMock()
        mock_client.nc = None

        async def fake_connect():
            mock_client.nc = MagicMock()

        async def fake_subscribe_durable(subject, consumer, callback):
            # Invoke on_event with a valid event — covers lines 139-147
            await callback(
                {
                    "subject": "telemetry.action.click",
                    "data": {"btn": "ok"},
                    "machine": "host",
                    "session": "c-sw-1",
                    "ts": 1234.0,
                }
            )
            # Returns without blocking — covers line 158 (return True)

        mock_client.connect = fake_connect
        mock_client.subscribe_durable = fake_subscribe_durable

        db_path = tmp_path / "test.db"

        with (
            patch("ai_cli.sync._acquire_pid_file", return_value=True),
            patch("ai_cli.sync._release_pid_file"),
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("ai_cli.telemetry._DB_PATH", db_path),
        ):
            from ai_cli.telemetry import telemetry_writer

            result = telemetry_writer()

        assert result == 0
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT subject FROM events").fetchone()
        assert row[0] == "telemetry.action.click"
        conn.close()

    def test_telemetry_writer_when_write_event_raises_then_logs_error(self, tmp_path, capsys):
        """Covers lines 148-149: except Exception in on_event."""
        mock_client = MagicMock()
        mock_client.nc = None

        async def fake_connect():
            mock_client.nc = MagicMock()

        async def fake_subscribe_durable(subject, consumer, callback):
            await callback({"subject": "test"})

        mock_client.connect = fake_connect
        mock_client.subscribe_durable = fake_subscribe_durable

        db_path = tmp_path / "test.db"

        with (
            patch("ai_cli.sync._acquire_pid_file", return_value=True),
            patch("ai_cli.sync._release_pid_file"),
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("ai_cli.telemetry._DB_PATH", db_path),
            patch("ai_cli.telemetry.write_event", side_effect=Exception("db error")),
        ):
            from ai_cli.telemetry import telemetry_writer

            telemetry_writer()

        assert "write error" in capsys.readouterr().err
