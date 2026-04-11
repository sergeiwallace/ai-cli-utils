"""Quota storage — SQLite persistence for Claude usage telemetry.

Stores usage records, quota snapshots, and weekly state in
~/.local/state/ai-cli/quota.db. This is the storage layer for the
quota tracking subsystem; quota.py owns the scraping and watching logic.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Default weekly quota reset anchor (fallback only — overridden by scraped value).
# Override via [quota] reset_anchor_utc in ~/.config/ai-cli-utils/config.toml,
# or automatically by record_quota_snapshot() when the /usage dialog includes a
# reset datetime.
_DEFAULT_RESET_ANCHOR = "2026-04-04T06:00:00Z"
_WEEK_SECONDS = 7 * 24 * 3600


def _get_reset_anchor_path() -> Path:
    """Path to the file that caches the reset anchor observed from /usage scrapes."""
    state_dir = Path.home() / ".local" / "state" / "ai-cli"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "quota-reset-anchor.txt"


def _save_reset_anchor(reset_utc_str: str) -> None:
    """Persist a reset anchor observed from a /usage scrape."""
    try:
        _get_reset_anchor_path().write_text(reset_utc_str.strip())
    except Exception:
        pass


def _get_reset_anchor_utc() -> datetime:
    """Return the weekly quota reset anchor.

    Priority (highest to lowest):
      1. Anchor file — written by record_quota_snapshot() when /usage includes a reset time.
      2. Config file — [quota] reset_anchor_utc in config.toml.
      3. Hardcoded default — last-known fallback.
    """
    # 1. Scraped anchor file
    try:
        anchor_file = _get_reset_anchor_path()
        if anchor_file.exists():
            anchor_str = anchor_file.read_text().strip()
            if anchor_str:
                return datetime.strptime(anchor_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        pass

    # 2. Config file
    try:
        from .main import load_config

        anchor_str = load_config().get("quota", {}).get("reset_anchor_utc", _DEFAULT_RESET_ANCHOR)
    except Exception:
        anchor_str = _DEFAULT_RESET_ANCHOR

    return datetime.strptime(anchor_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


_DB_PATH_OVERRIDE: Path | None = None


def set_db_path(path: Path) -> None:
    """Override DB path — used in tests."""
    global _DB_PATH_OVERRIDE
    _DB_PATH_OVERRIDE = path


def _get_quota_db_path() -> Path:
    if _DB_PATH_OVERRIDE is not None:
        return _DB_PATH_OVERRIDE
    state_dir = Path.home() / ".local" / "state" / "ai-cli"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "quota.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_get_quota_db_path()))
    conn.row_factory = sqlite3.Row
    _init_db(conn)
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS usage_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            machine_id TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL,
            total_tokens INTEGER NOT NULL,
            delta_tokens INTEGER,
            cost_usd REAL,
            recorded_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_usage_records_session
            ON usage_records(session_id, recorded_at DESC);
        CREATE INDEX IF NOT EXISTS idx_usage_records_recorded
            ON usage_records(recorded_at DESC);

        CREATE TABLE IF NOT EXISTS quota_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usage_percent REAL NOT NULL,
            session_pct REAL,
            weekly_sonnet_pct REAL,
            extra_pct REAL,
            week_start TEXT NOT NULL,
            snapshotted_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_quota_snapshots_week
            ON quota_snapshots(week_start, snapshotted_at DESC);

        CREATE TABLE IF NOT EXISTS weekly_state (
            week_start TEXT PRIMARY KEY,
            total_consumed INTEGER NOT NULL DEFAULT 0,
            last_snapshot_at TEXT,
            reset_at TEXT
        );
    """)
    conn.commit()


def _get_current_week_start(now: datetime | None = None) -> str:
    """Return ISO 8601 datetime string for the start of the current quota week."""
    if now is None:
        now = datetime.now(timezone.utc)
    anchor = _get_reset_anchor_utc()
    diff = (anchor - now).total_seconds()
    if diff > 0:
        periods_back = int(diff / _WEEK_SECONDS) + 1
        week_start = anchor - timedelta(seconds=periods_back * _WEEK_SECONDS)
    else:
        elapsed = -diff
        full_periods = int(elapsed / _WEEK_SECONDS)
        week_start = anchor + timedelta(seconds=full_periods * _WEEK_SECONDS)
    return week_start.strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_reset_at(now: datetime | None = None) -> str:
    """Return ISO 8601 datetime string for the next quota reset."""
    if now is None:
        now = datetime.now(timezone.utc)
    anchor = _get_reset_anchor_utc()
    diff = (anchor - now).total_seconds()
    if diff > 0:
        periods = int(diff / _WEEK_SECONDS)
        reset = anchor - timedelta(seconds=periods * _WEEK_SECONDS)
        if reset <= now:
            reset += timedelta(seconds=_WEEK_SECONDS)
        return reset.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        elapsed = -diff
        full_periods = int(elapsed / _WEEK_SECONDS)
        reset = anchor + timedelta(seconds=(full_periods + 1) * _WEEK_SECONDS)
        return reset.strftime("%Y-%m-%dT%H:%M:%SZ")


def record_usage(
    session_id: str,
    machine_id: str,
    model: str,
    total_tokens: int,
    cost_usd: float | None = None,
) -> None:
    """Insert a usage record and update weekly state."""
    conn = _get_conn()
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    week_start = _get_current_week_start(now)

    row = conn.execute(
        "SELECT MAX(total_tokens) FROM usage_records WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    prev_max = row[0] if row and row[0] is not None else 0
    delta = max(total_tokens - prev_max, 0) if prev_max > 0 else total_tokens

    conn.execute(
        """INSERT INTO usage_records
           (session_id, machine_id, model, total_tokens, delta_tokens, cost_usd, recorded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (session_id, machine_id, model, total_tokens, delta, cost_usd, now_iso),
    )

    conn.execute(
        """INSERT INTO weekly_state (week_start, total_consumed, reset_at)
           VALUES (?, ?, ?)
           ON CONFLICT(week_start) DO UPDATE SET
               total_consumed = total_consumed + ?""",
        (week_start, delta, _get_reset_at(now), delta),
    )
    conn.commit()
    conn.close()


def record_quota_snapshot(
    usage_percent: float,
    session_pct: float | None = None,
    weekly_sonnet_pct: float | None = None,
    extra_pct: float | None = None,
    reset_at: str | None = None,
) -> None:
    """Insert a quota snapshot for the current week and update weekly state.

    If ``reset_at`` is provided (scraped from the /usage dialog), it is
    persisted as the new reset anchor so future week-start calculations are
    accurate even when the reset time changes.
    """
    # Update the anchor before computing week_start so that _get_current_week_start()
    # immediately uses the freshly scraped reset time for this and all future calls.
    if reset_at:
        _save_reset_anchor(reset_at)

    conn = _get_conn()
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    week_start = _get_current_week_start(now)
    derived_reset_at = _get_reset_at(now)

    conn.execute(
        """INSERT INTO quota_snapshots
           (usage_percent, session_pct, weekly_sonnet_pct, extra_pct, week_start, snapshotted_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (usage_percent, session_pct, weekly_sonnet_pct, extra_pct, week_start, now_iso),
    )

    conn.execute(
        """INSERT INTO weekly_state (week_start, total_consumed, last_snapshot_at, reset_at)
           VALUES (?, 0, ?, ?)
           ON CONFLICT(week_start) DO UPDATE SET
               last_snapshot_at = ?,
               reset_at = ?""",
        (week_start, now_iso, derived_reset_at, now_iso, derived_reset_at),
    )
    conn.commit()
    conn.close()


def compute_burn_rate(week_start: str, reset_at: str | None = None) -> dict:
    """Compute actual vs expected burn rate from quota snapshots."""
    conn = _get_conn()
    snapshots = conn.execute(
        """SELECT usage_percent, snapshotted_at FROM quota_snapshots
           WHERE week_start = ? ORDER BY snapshotted_at ASC""",
        (week_start,),
    ).fetchall()
    conn.close()

    result = {
        "actual_pct_per_day": 0.0,
        "expected_pct_per_day": 0.0,
        "multiplier": 0.0,
        "latest_percent": 0.0,
    }

    if not snapshots:
        return result

    latest_pct = snapshots[-1]["usage_percent"]
    result["latest_percent"] = latest_pct
    result["expected_pct_per_day"] = 100.0 / 7.0

    if len(snapshots) >= 2:
        first = snapshots[0]
        last = snapshots[-1]
        t0 = datetime.strptime(first["snapshotted_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        t1 = datetime.strptime(last["snapshotted_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        elapsed_days = (t1 - t0).total_seconds() / 86400.0
        if elapsed_days > 0:
            pct_change = last["usage_percent"] - first["usage_percent"]
            result["actual_pct_per_day"] = pct_change / elapsed_days
    elif len(snapshots) == 1:
        ws = datetime.strptime(week_start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        snap_time = datetime.strptime(snapshots[0]["snapshotted_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        elapsed_days = (snap_time - ws).total_seconds() / 86400.0
        if elapsed_days > 0:
            result["actual_pct_per_day"] = latest_pct / elapsed_days

    if result["expected_pct_per_day"] > 0:
        result["multiplier"] = round(result["actual_pct_per_day"] / result["expected_pct_per_day"], 2)

    return result


def get_current_status() -> dict:
    """Return current quota status with burn rate and alerts."""
    conn = _get_conn()
    now = datetime.now(timezone.utc)
    week_start = _get_current_week_start(now)
    reset_at = _get_reset_at(now)

    snap = conn.execute(
        """SELECT usage_percent, snapshotted_at FROM quota_snapshots
           WHERE week_start = ? ORDER BY snapshotted_at DESC LIMIT 1""",
        (week_start,),
    ).fetchone()

    latest_snapshot = None
    if snap:
        latest_snapshot = {
            "usage_percent": snap["usage_percent"],
            "snapshotted_at": snap["snapshotted_at"],
        }

    ws_row = conn.execute(
        "SELECT total_consumed FROM weekly_state WHERE week_start = ?",
        (week_start,),
    ).fetchone()
    total_consumed = ws_row["total_consumed"] if ws_row else 0

    sessions = conn.execute(
        """SELECT session_id, machine_id, model,
                  SUM(delta_tokens) as tokens,
                  MAX(recorded_at) as last_seen
           FROM usage_records
           WHERE recorded_at >= ?
           GROUP BY session_id, machine_id, model
           ORDER BY tokens DESC""",
        (week_start,),
    ).fetchall()

    per_session = [
        {
            "session_id": s["session_id"],
            "machine_id": s["machine_id"],
            "model": s["model"],
            "tokens": s["tokens"],
            "last_seen": s["last_seen"],
        }
        for s in sessions
    ]

    conn.close()

    reset_dt = datetime.strptime(reset_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    days_remaining = max((reset_dt - now).total_seconds() / 86400.0, 0.0)

    burn_rate = compute_burn_rate(week_start, reset_at)

    alerts = []
    if latest_snapshot:
        pct = latest_snapshot["usage_percent"]
        if pct >= 90:
            alerts.append(f"Usage at {pct:.1f}% — critical, slow down")
        elif pct >= 75:
            alerts.append(f"Usage at {pct:.1f}% — approaching limit")
        elif pct >= 50:
            alerts.append(f"Usage at {pct:.1f}% — half of weekly quota consumed")

    if burn_rate["multiplier"] >= 1.5:
        alerts.append(f"Burn rate {burn_rate['multiplier']:.1f}x expected pace")

    return {
        "latest_snapshot": latest_snapshot,
        "week_start": week_start,
        "reset_at": reset_at,
        "days_remaining": round(days_remaining, 2),
        "total_consumed": total_consumed,
        "per_session_breakdown": per_session,
        "burn_rate": burn_rate,
        "alerts": alerts,
    }


def get_weekly_history() -> list[dict]:
    """Return one entry per week: week_start, peak_percent, total_consumed, snapshot_count."""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT week_start,
                  MAX(usage_percent) as peak_percent,
                  COUNT(*) as snapshot_count
           FROM quota_snapshots
           GROUP BY week_start
           ORDER BY week_start DESC"""
    ).fetchall()

    weeks = []
    for r in rows:
        ws_row = conn.execute(
            "SELECT total_consumed FROM weekly_state WHERE week_start = ?",
            (r["week_start"],),
        ).fetchone()
        weeks.append(
            {
                "week_start": r["week_start"],
                "peak_percent": r["peak_percent"],
                "total_consumed": ws_row["total_consumed"] if ws_row else 0,
                "snapshot_count": r["snapshot_count"],
            }
        )

    conn.close()
    return weeks
