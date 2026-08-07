"""AIH-164 T-06: rate-limit-aware Fable (secondary per-model cap) scrape.

The Fable `Current week (<model>)` line is the only per-model datum /usage exposes and is NOT
in the stdin rate_limits, so it still needs the TUI scrape. Because T-02's env path keeps the
all-models snapshot fresh, the scrape must be triggered on a FABLE-specific cadence with
progressive backoff (so a rate-limited breakdown is not hammered), and the last-good Fable value
must survive past the 3 rows the render reads.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import ai_cli.quota as q
import ai_cli.quota_db as qdb
from ai_cli.quota import _get_last_fable_snapshot, _maybe_trigger_fable_scrape, quota_statusline_part


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# --- _maybe_trigger_fable_scrape backoff state machine ---


def test_fresh_fable_resets_backoff_no_scrape(tmp_path, monkeypatch):
    state = tmp_path / "fb.json"
    state.write_text('{"last_attempt": 0.0, "misses": 3}')
    monkeypatch.setattr(q, "_FABLE_BACKOFF_STATE", state)
    now = datetime.now(UTC)
    with patch("ai_cli.quota._launch_background_scrape") as scrape:
        _maybe_trigger_fable_scrape(now, _iso(now - timedelta(minutes=5)))  # 5 min old = fresh
    scrape.assert_not_called()
    import json

    assert json.loads(state.read_text())["misses"] == 0  # reset


def test_stale_fable_no_prior_attempt_scrapes(tmp_path, monkeypatch):
    state = tmp_path / "fb.json"
    monkeypatch.setattr(q, "_FABLE_BACKOFF_STATE", state)
    now = datetime.now(UTC)
    with patch("ai_cli.quota._launch_background_scrape") as scrape:
        _maybe_trigger_fable_scrape(now, _iso(now - timedelta(hours=2)))  # stale
    scrape.assert_called_once()
    import json

    assert json.loads(state.read_text())["misses"] == 1


def test_stale_fable_within_backoff_does_not_scrape(tmp_path, monkeypatch):
    state = tmp_path / "fb.json"
    now = datetime.now(UTC)
    # misses=1 → interval 20 min; last attempt 5 min ago → within backoff.
    state.write_text(f'{{"last_attempt": {(now - timedelta(minutes=5)).timestamp()}, "misses": 1}}')
    monkeypatch.setattr(q, "_FABLE_BACKOFF_STATE", state)
    with patch("ai_cli.quota._launch_background_scrape") as scrape:
        _maybe_trigger_fable_scrape(now, _iso(now - timedelta(hours=2)))
    scrape.assert_not_called()


def test_stale_fable_after_backoff_interval_scrapes_and_grows(tmp_path, monkeypatch):
    state = tmp_path / "fb.json"
    now = datetime.now(UTC)
    # misses=1 → interval 20 min; last attempt 25 min ago → elapsed.
    state.write_text(f'{{"last_attempt": {(now - timedelta(minutes=25)).timestamp()}, "misses": 1}}')
    monkeypatch.setattr(q, "_FABLE_BACKOFF_STATE", state)
    with patch("ai_cli.quota._launch_background_scrape") as scrape:
        _maybe_trigger_fable_scrape(now, _iso(now - timedelta(hours=2)))
    scrape.assert_called_once()
    import json

    assert json.loads(state.read_text())["misses"] == 2  # backoff grew


def test_backoff_caps_at_max_misses(tmp_path, monkeypatch):
    state = tmp_path / "fb.json"
    now = datetime.now(UTC)
    state.write_text(f'{{"last_attempt": {(now - timedelta(hours=5)).timestamp()}, "misses": 9}}')
    monkeypatch.setattr(q, "_FABLE_BACKOFF_STATE", state)
    with patch("ai_cli.quota._launch_background_scrape") as scrape:
        _maybe_trigger_fable_scrape(now, None)  # missing Fable
    scrape.assert_called_once()
    import json

    assert json.loads(state.read_text())["misses"] == q._FABLE_BACKOFF_MAX_MISSES


# --- integration: the statusline no longer drives the Fable scrape or its rendering ---
#
# The ccF segment was removed from quota_statusline_part() entirely (the upstream /usage line
# it depended on is gone for good; see docs/bugs/fable-statusline-unavailable.md). The two
# tests this replaces asserted that the render path fires (or skips) a Fable-specific scrape
# and displays a last-good Fable value — both premises are now intentionally false, not a
# regression. _maybe_trigger_fable_scrape() and _get_last_fable_snapshot() themselves are
# untouched (tested directly above/below); only their use from the render path is gone.


def test_statusline_never_triggers_fable_scrape_even_with_no_fable_data(monkeypatch, capsys):
    """quota_statusline_part() no longer calls _maybe_trigger_fable_scrape at all -- confirms
    the removal is complete, not an oversight that happens to not fire in this particular case."""
    monkeypatch.setenv("AI_CLI_QUOTA_SEVEN_DAY_PCT", "20")
    monkeypatch.setenv("AI_CLI_QUOTA_SEVEN_DAY_RESET", str(int(datetime.now(UTC).timestamp()) + 86400))
    qdb.record_quota_snapshot(usage_percent=20.0)  # fresh all-models, NO Fable
    with patch("ai_cli.quota._maybe_trigger_fable_scrape") as fable_scrape:
        quota_statusline_part()
    capsys.readouterr()
    fable_scrape.assert_not_called()


def test_last_good_fable_survives_lookup_but_render_never_shows_it(monkeypatch, capsys):
    """_get_last_fable_snapshot's unbounded past-limit-3 lookup still works (untouched, real
    coverage), but the statusline render must never surface that value, even when it exists."""
    week_start = qdb._get_current_week_start()
    conn = sqlite3.connect(str(qdb._get_quota_db_path()))
    qdb._init_db(conn)
    base = datetime.now(UTC) - timedelta(minutes=50)
    # Oldest row carries the Fable value; four newer rows are all-models-only.
    conn.execute(
        "INSERT INTO quota_snapshots (usage_percent, weekly_sonnet_pct, weekly_model_name, week_start, snapshotted_at)"
        " VALUES (?,?,?,?,?)",
        (20.0, 7.0, "Fable", week_start, _iso(base)),
    )
    for i in range(1, 5):
        conn.execute(
            "INSERT INTO quota_snapshots (usage_percent, week_start, snapshotted_at) VALUES (?,?,?)",
            (20.0 + i, week_start, _iso(base + timedelta(minutes=10 * i))),
        )
    conn.commit()
    conn.close()

    pct, name, ts = _get_last_fable_snapshot(week_start)
    assert pct == 7.0 and name == "Fable"

    monkeypatch.setenv("AI_CLI_QUOTA_SEVEN_DAY_PCT", "24")
    with patch("ai_cli.quota._launch_background_scrape"):
        quota_statusline_part()
    out = capsys.readouterr().out
    # Check the Fable label specifically, not a raw "7%" substring -- the real (unmocked)
    # ccWk pace-delta is computed from wall-clock time and can coincidentally contain "7%"
    # on an unrelated run, which flaked this assertion under randomized test ordering.
    assert "ccF" not in out
    assert "ccS" not in out
