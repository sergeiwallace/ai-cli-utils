"""Telemetry — event recording and SQLite persistence.

Events flow: caller -> record_event() -> NATS JetStream -> background writer -> SQLite.
Log records (AI-CLI-2gq3) skip the bus: :func:`store_log_records` writes the batch
:mod:`ai_cli.output` buffered straight to the local store, before a handoff or exit.
Opt-out via [telemetry] enabled = false in config.toml (default: enabled).
"""

import json
import os
import socket
import sqlite3
import time
from pathlib import Path

from . import output as _out
from .output import Tag

# Set only to point the store elsewhere; otherwise resolved under the home directory
# at call time, so a process whose HOME changes after import writes where HOME is.
_DB_PATH: Path | None = None
# A log-record batch waits at most this long, once, for a store another writer holds,
# so telemetry can never hold up a launch's handoff (the T-7 budget is 100 ms).
_LOG_BATCH_TIMEOUT_S = 0.03

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY,
    ts        REAL NOT NULL,
    subject   TEXT NOT NULL,
    machine   TEXT NOT NULL,
    session   TEXT,
    data      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_subject_ts ON events(subject, ts);
"""


def _get_machine_id() -> str:
    """Return a stable machine identifier.

    Resolved through the same tiers as the rest of the package rather than from the hostname
    directly: a hostname is not stable on a cloud host, and every event row this labels outlives
    the lease whose DNS name it would otherwise carry. Imported lazily, and with a fallback, so
    telemetry can never be the reason a launch fails.
    """
    try:
        from .config import detect_machine_profile

        return detect_machine_profile()["host_id"]
    except Exception:
        return socket.gethostname()


def _is_enabled() -> bool:
    """Check if telemetry is enabled in config."""
    try:
        from .config import load_config

        config = load_config()
        return config.get("telemetry", {}).get("enabled", True)
    except Exception:
        return True


def _db_path() -> Path:
    return _DB_PATH or Path.home() / ".ai-cli" / "telemetry.db"


def init_db(db_path: Path | None = None) -> sqlite3.Connection:
    """Initialize the telemetry SQLite database."""
    path = db_path or _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def write_event(
    conn: sqlite3.Connection,
    subject: str,
    data: dict,
    machine: str | None = None,
    session: str | None = None,
    ts: float | None = None,
) -> None:
    """Write a single event to the telemetry database."""
    conn.execute(
        "INSERT INTO events (ts, subject, machine, session, data) VALUES (?, ?, ?, ?, ?)",
        (
            ts if ts is not None else time.time(),
            subject,
            machine or _get_machine_id(),
            session or os.environ.get("AI_TMUX_SESSION"),
            json.dumps(data),
        ),
    )
    conn.commit()


def store_log_records(events: list[tuple[float, str, dict]]) -> None:
    """Write ``(unix time, subject, data)`` log records in one transaction; any failure raises.

    Not published to the bus (D-6): this runs right before an exec, where a
    network round trip has no place.
    """
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # SQLite's busy timeout applies to each statement that meets a lock, so it bounds a
    # batch only if one statement can wait. BEGIN IMMEDIATE takes the write lock first;
    # once it is held, nothing after it waits for another writer. The 30 ms leaves room
    # for a slow host: a macOS CI runner took 159 ms over a 50 ms timeout.
    conn = sqlite3.connect(str(path), timeout=_LOG_BATCH_TIMEOUT_S, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        # Only a batch that holds the lock pays for its rows' machine id: the first
        # lookup in a process costs tens of milliseconds, which a locked store, whose
        # batch is dropped anyway, must not spend (a macOS runner reached 111 ms).
        machine = _get_machine_id()
        session = os.environ.get("AI_TMUX_SESSION")
        for statement in _SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.executemany(
            "INSERT INTO events (ts, subject, machine, session, data) VALUES (?, ?, ?, ?, ?)",
            [(ts, subject, machine, session, json.dumps(data)) for ts, subject, data in events],
        )
        conn.execute("COMMIT")
    finally:
        conn.close()


def record_event(subject: str, data: dict) -> bool:
    """Record a telemetry event. Publishes to NATS and writes to SQLite.

    Returns True if the event was recorded, False if telemetry is disabled
    or an error occurred.
    """
    if not _is_enabled():
        return False

    full_subject = f"telemetry.action.{subject}" if not subject.startswith("telemetry.") else subject
    machine = _get_machine_id()
    session = os.environ.get("AI_TMUX_SESSION")
    ts = time.time()

    payload = {
        "subject": full_subject,
        "machine": machine,
        "session": session,
        "ts": ts,
        "data": data,
    }

    # Write to SQLite directly (foreground)
    try:
        conn = init_db()
        try:
            write_event(conn, full_subject, data, machine=machine, session=session, ts=ts)
        finally:
            conn.close()
    except Exception:
        pass

    # Publish to NATS (non-blocking, non-fatal)
    try:
        import asyncio

        from .messaging import NATSClient

        async def _publish_and_close(subject: str, payload: dict) -> None:
            client = NATSClient()
            try:
                await client.publish(subject, payload)
            finally:
                await client.close()

        asyncio.run(_publish_and_close(full_subject, payload))
    except Exception:
        pass

    return True


def telemetry_writer() -> int:
    """Background telemetry writer — consumes from NATS JetStream and writes to SQLite.

    Intended to run as a long-lived daemon.
    Exit codes: 0 = clean stop, 1 = error
    """
    import asyncio

    from .messaging import NATSClient
    from .sync import _acquire_pid_file, _release_pid_file

    if not _acquire_pid_file("telemetry-writer"):
        _out.emit(Tag.TELEMETRY, "watch", "telemetry writer is already running.", err=True)
        return 2

    conn = init_db()
    client = NATSClient()

    async def on_event(data: dict):
        try:
            write_event(
                conn,
                subject=data.get("subject", "unknown"),
                data=data.get("data", {}),
                machine=data.get("machine"),
                session=data.get("session"),
                ts=data.get("ts"),
            )
        except Exception as e:
            _out.emit(Tag.TELEMETRY_WRITER, "watch", f"write error: {e}", err=True)

    async def run():
        await client.connect()
        if not client.nc:
            _out.emit(Tag.TELEMETRY, "watch", "NATS unavailable — cannot start telemetry writer.", err=True)
            return False
        _out.emit(Tag.TELEMETRY_WRITER, "watch", "connected, consuming telemetry.action.>")
        await client.subscribe_durable("telemetry.action.>", "telemetry-writer", on_event)
        return True

    _out.emit(Tag.TELEMETRY, "watch", "ai telemetry writer — consuming events (Ctrl+C to stop)")
    try:
        ok = asyncio.run(run())
    except KeyboardInterrupt:
        ok = True
    finally:
        conn.close()
        asyncio.run(client.close())
        _release_pid_file("telemetry-writer")

    return 0 if ok else 1
