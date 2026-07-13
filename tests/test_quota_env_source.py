"""AIH-164 T-02: quota_statusline_part consumes AI_CLI_QUOTA_* env vars as authoritative.

The statusline (ai-harness) now extracts CC's `rate_limits` from stdin and exports
AI_CLI_QUOTA_{SEVEN_DAY,FIVE_HOUR}_{PCT,RESET} before calling `ai quota statusline-part`.
This module verifies quota_statusline_part uses them as the authoritative all-models source
by recording a THROTTLED snapshot (so the value flows through the existing render + history
path while the acceleration arrow keeps its sparse cadence), routes the reset epoch through
the anchor, and that print-mode is retired from the capture fallback.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import ai_cli.quota_db as qdb
from ai_cli.quota import _get_claude_usage_snapshot, quota_statusline_part


def _count_snapshots() -> int:
    conn = sqlite3.connect(str(qdb._get_quota_db_path()))
    try:
        qdb._init_db(conn)
        return conn.execute("SELECT COUNT(*) FROM quota_snapshots").fetchone()[0]
    finally:
        conn.close()


def _set_env(monkeypatch, *, seven_day_pct="20", five_hour_pct="23"):
    now = datetime.now(timezone.utc)
    seven_day_reset = int((now + timedelta(days=2)).timestamp())
    five_hour_reset = int((now + timedelta(hours=3)).timestamp())
    monkeypatch.setenv("AI_CLI_QUOTA_SEVEN_DAY_PCT", str(seven_day_pct))
    monkeypatch.setenv("AI_CLI_QUOTA_SEVEN_DAY_RESET", str(seven_day_reset))
    monkeypatch.setenv("AI_CLI_QUOTA_FIVE_HOUR_PCT", str(five_hour_pct))
    monkeypatch.setenv("AI_CLI_QUOTA_FIVE_HOUR_RESET", str(five_hour_reset))
    return seven_day_reset


def test_env_seven_day_records_snapshot_and_renders(monkeypatch, capsys):
    """Env var present + empty DB → records a snapshot from the env value and renders it."""
    _set_env(monkeypatch, seven_day_pct="42")
    assert _count_snapshots() == 0
    result = quota_statusline_part()
    assert result == 0
    assert _count_snapshots() == 1, "env value not persisted as a snapshot"
    assert "42%" in capsys.readouterr().out


def test_env_throttle_skips_duplicate_snapshot(monkeypatch, capsys):
    """A fresh snapshot with the SAME pct exists → env render does NOT add another
    (throttle preserves the acceleration-arrow cadence; audit F-04/AD-1)."""
    qdb.record_quota_snapshot(usage_percent=20.0)
    assert _count_snapshots() == 1
    _set_env(monkeypatch, seven_day_pct="20")
    quota_statusline_part()
    capsys.readouterr()
    assert _count_snapshots() == 1, "throttle failed — duplicate per-render snapshot written"


def test_env_records_when_pct_changed(monkeypatch, capsys):
    """A changed pct is always recorded even within the throttle window."""
    qdb.record_quota_snapshot(usage_percent=20.0)
    _set_env(monkeypatch, seven_day_pct="35")
    quota_statusline_part()
    capsys.readouterr()
    assert _count_snapshots() == 2, "changed pct not recorded"


def test_env_records_after_throttle_window(monkeypatch, capsys):
    """Same pct but the last snapshot is older than the throttle window → record (keeps
    the ~10-min history cadence going)."""
    old = (datetime.now(timezone.utc) - timedelta(minutes=11)).strftime("%Y-%m-%dT%H:%M:%SZ")
    week_start = qdb._get_current_week_start()
    conn = sqlite3.connect(str(qdb._get_quota_db_path()))
    qdb._init_db(conn)
    conn.execute(
        "INSERT INTO quota_snapshots (usage_percent, week_start, snapshotted_at) VALUES (?,?,?)",
        (20.0, week_start, old),
    )
    conn.commit()
    conn.close()
    assert _count_snapshots() == 1
    _set_env(monkeypatch, seven_day_pct="20")
    quota_statusline_part()
    capsys.readouterr()
    assert _count_snapshots() == 2, "did not record after throttle window elapsed"


def test_env_reset_persisted_as_anchor(monkeypatch, capsys):
    """The env reset epoch is routed through record_quota_snapshot(reset_at=…) → weekly_state
    reset_at reflects it, so the statusline + `ai quota status` share one week boundary (F-08)."""
    _set_env(monkeypatch, seven_day_pct="20")
    quota_statusline_part()
    capsys.readouterr()
    conn = sqlite3.connect(str(qdb._get_quota_db_path()))
    try:
        row = conn.execute("SELECT reset_at FROM weekly_state").fetchone()
    finally:
        conn.close()
    assert row is not None and row[0], "reset anchor not persisted from env"


def test_no_env_var_leaves_existing_behavior(monkeypatch, capsys):
    """No env vars → falls back to the existing SQLite-read render (no crash, no new snapshot
    from a phantom env value)."""
    monkeypatch.delenv("AI_CLI_QUOTA_SEVEN_DAY_PCT", raising=False)
    qdb.record_quota_snapshot(usage_percent=55.0)
    result = quota_statusline_part()
    assert result == 0
    assert "55%" in capsys.readouterr().out
    assert _count_snapshots() == 1


def test_get_claude_usage_snapshot_retires_print_mode():
    """Print mode (dead on CC 2.1.207) is retired from the capture fallback — the scrape is
    the sole fallback path now (T-02 parity: dropped intentionally)."""
    with (
        patch("ai_cli.quota._scrape_usage_hidden_pane", return_value="SCRAPED") as scrape,
        patch("ai_cli.quota._get_usage_via_print_mode") as print_mode,
    ):
        result = _get_claude_usage_snapshot()
    assert result == "SCRAPED"
    scrape.assert_called_once()
    print_mode.assert_not_called()
