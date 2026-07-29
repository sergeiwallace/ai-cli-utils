"""Regression coverage for explicit Fable availability in the statusline."""

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import ai_cli.quota_db as qdb
from ai_cli.quota import quota_statusline_part


_LABEL = "\033[1;38;2;217;119;87m"
_RESET = "\033[0m"
_GREEN = "\033[32m"
_DIM = "\033[2m"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _insert_snapshot(
    db_path, *, week_start: str, snapshotted_at: datetime, usage_percent: float, fable_percent: float | None = None
) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        qdb._init_db(conn)
        conn.execute(
            "INSERT INTO quota_snapshots "
            "(usage_percent, weekly_sonnet_pct, weekly_model_name, week_start, snapshotted_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                usage_percent,
                fable_percent,
                "Fable" if fable_percent is not None else None,
                week_start,
                _iso(snapshotted_at),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _render_statusline(monkeypatch, capsys, db_path, setup_rows) -> str:
    monkeypatch.delenv("AI_CLI_QUOTA_SEVEN_DAY_PCT", raising=False)
    qdb.set_db_path(db_path)
    try:
        week_start = qdb._get_current_week_start()
        week_start_dt = datetime.strptime(week_start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        fixed_now = week_start_dt + timedelta(hours=30)
        setup_rows(db_path, week_start, fixed_now)
        with patch("datetime.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            mock_datetime.strptime.side_effect = datetime.strptime
            mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
            assert quota_statusline_part() == 0
        return capsys.readouterr().out
    finally:
        qdb.set_db_path(None)  # type: ignore[arg-type]


def test_given_fable_snapshots_when_rendering_then_distinguishes_fresh_stale_and_unavailable(
    tmp_path, monkeypatch, capsys
):
    """The statusline must expose source loss rather than relabeling it as Sonnet data."""

    def fresh(db_path, week_start, now):
        _insert_snapshot(
            db_path,
            week_start=week_start,
            snapshotted_at=now - timedelta(minutes=10),
            usage_percent=21.0,
            fable_percent=2.0,
        )

    def stale(db_path, week_start, now):
        _insert_snapshot(
            db_path,
            week_start=week_start,
            snapshotted_at=now - timedelta(hours=3),
            usage_percent=20.0,
            fable_percent=2.0,
        )
        _insert_snapshot(
            db_path,
            week_start=week_start,
            snapshotted_at=now - timedelta(minutes=10),
            usage_percent=21.0,
        )

    def prior_week_only(db_path, week_start, now):
        previous_week = _iso(datetime.strptime(week_start, "%Y-%m-%dT%H:%M:%SZ") - timedelta(days=7))
        _insert_snapshot(
            db_path,
            week_start=previous_week,
            snapshotted_at=now - timedelta(days=1),
            usage_percent=20.0,
            fable_percent=2.0,
        )
        _insert_snapshot(
            db_path,
            week_start=week_start,
            snapshotted_at=now - timedelta(minutes=10),
            usage_percent=21.0,
        )

    def never_recorded(db_path, week_start, now):
        _insert_snapshot(
            db_path,
            week_start=week_start,
            snapshotted_at=now - timedelta(minutes=10),
            usage_percent=21.0,
        )

    fresh_out = _render_statusline(monkeypatch, capsys, tmp_path / "fresh.db", fresh)
    stale_out = _render_statusline(monkeypatch, capsys, tmp_path / "stale.db", stale)
    prior_week_out = _render_statusline(monkeypatch, capsys, tmp_path / "prior-week.db", prior_week_only)
    never_recorded_out = _render_statusline(monkeypatch, capsys, tmp_path / "never-recorded.db", never_recorded)

    assert fresh_out == (
        f"{_LABEL}ccWk{_RESET} {_GREEN}21%{_RESET} {_GREEN}→3%{_RESET} | "
        f"{_LABEL}ccF{_RESET} {_GREEN}2%{_RESET} {_GREEN}→16%{_RESET}\n"
    )
    assert stale_out == (
        f"{_LABEL}ccWk{_RESET} {_GREEN}21%{_RESET} {_GREEN}→3%{_RESET} | {_LABEL}ccF{_RESET} {_DIM}2% STALE{_RESET}\n"
    )
    assert prior_week_out == (
        f"{_LABEL}ccWk{_RESET} {_GREEN}21%{_RESET} {_GREEN}→3%{_RESET} | "
        f"{_LABEL}ccF{_RESET} {_DIM}UNAVAILABLE{_RESET}\n"
    )
    assert never_recorded_out == prior_week_out
