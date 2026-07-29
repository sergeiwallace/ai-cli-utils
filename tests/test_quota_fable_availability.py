"""Regression coverage for removing the Fable statusline segment."""

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import ai_cli.quota_db as qdb
from ai_cli.quota import quota_statusline_part


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _render_statusline(monkeypatch, capsys, db_path, *, fable_percent: float | None) -> str:
    monkeypatch.delenv("AI_CLI_QUOTA_SEVEN_DAY_PCT", raising=False)
    qdb.set_db_path(db_path)
    try:
        week_start = qdb._get_current_week_start()
        week_start_dt = datetime.strptime(week_start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        fixed_now = week_start_dt + timedelta(hours=30)
        conn = sqlite3.connect(str(db_path))
        try:
            qdb._init_db(conn)
            conn.execute(
                "INSERT INTO quota_snapshots "
                "(usage_percent, weekly_sonnet_pct, weekly_model_name, week_start, snapshotted_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (21.0, fable_percent, "Fable" if fable_percent is not None else None, week_start, _iso(fixed_now)),
            )
            conn.commit()
        finally:
            conn.close()
        with patch("datetime.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            mock_datetime.strptime.side_effect = datetime.strptime
            mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
            assert quota_statusline_part() == 0
        return capsys.readouterr().out
    finally:
        qdb.set_db_path(None)  # type: ignore[arg-type]


def test_given_fable_data_or_its_absence_when_rendering_then_omits_secondary_segment(tmp_path, monkeypatch, capsys):
    """The all-models statusline is unchanged by stored Fable data."""
    with_fable = _render_statusline(monkeypatch, capsys, tmp_path / "with-fable.db", fable_percent=2.0)
    without_fable = _render_statusline(monkeypatch, capsys, tmp_path / "without-fable.db", fable_percent=None)

    for output in (with_fable, without_fable):
        assert "ccWk" in output
        assert "ccF" not in output
        assert "ccS" not in output
