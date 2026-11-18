"""``ai internal resolve-continue-target`` after a migration that left the transcript under OLD.

The session supervisor resumes whatever this command prints, and starts a fresh
Claude when it prints nothing. A migration retitles the session's transcript to
NEW, but a session still running at that moment appends its old title again, so
the transcript the session lives in ends up titled OLD. The relaunch then found
no transcript titled NEW, started fresh, and that fresh transcript (titled NEW)
shadowed the real one on every later relaunch.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import run_cli

from ai_cli import config as _config
from ai_cli.main import _bare_engine_command, _cc_project_dir

OLD = "myproject-1"
NEW = "myproject-renamed"
MIGRATED_AT = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)

REAL = "aaaaaaaa-0000-4000-8000-000000000001"
FRESH = "bbbbbbbb-0000-4000-8000-000000000002"
OTHER = "cccccccc-0000-4000-8000-000000000003"
STRAY = "dddddddd-0000-4000-8000-000000000004"


@pytest.fixture
def slot(tmp_path, monkeypatch):
    """A slot whose project dir and migration ledger live under this test's tmp dirs."""
    state_base = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_base))
    monkeypatch.setenv("LOCALAPPDATA", str(state_base))
    cwd = tmp_path / "repo" / ".worktrees" / NEW
    cwd.mkdir(parents=True)
    return {"cwd": cwd, "project_dir": _cc_project_dir(cwd), "state": _config.get_xdg_state_home()}


def _transcript(slot, uuid: str, titles: list[str], started: datetime, mtime: datetime) -> Path:
    """A transcript whose first record is at ``started``, carrying ``titles`` in order."""
    slot["project_dir"].mkdir(parents=True, exist_ok=True)
    path = slot["project_dir"] / f"{uuid}.jsonl"
    stamp = started.isoformat().replace("+00:00", "Z")
    records = [{"type": "user", "sessionId": uuid, "timestamp": stamp, "message": {"role": "user", "content": "hi"}}]
    records += [{"type": "custom-title", "customTitle": title, "sessionId": uuid} for title in titles]
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    os.utime(path, (mtime.timestamp(), mtime.timestamp()))
    return path


def _record_migration(slot, old: str = OLD, new: str = NEW) -> None:
    """The ledger rows ``ai migrate-session`` and a later ``--finish`` append."""
    slot["state"].mkdir(parents=True, exist_ok=True)
    repo = str(slot["cwd"].parent.parent)
    rows = [
        {"time": MIGRATED_AT.isoformat(), "old": old, "new": new, "repo": repo, "worktree": str(slot["cwd"])},
        {"time": (MIGRATED_AT + timedelta(minutes=20)).isoformat(), "old": old, "new": new, "repo": repo,
         "context_announced": True},
        {"time": (MIGRATED_AT + timedelta(hours=3)).isoformat(), "old": old, "new": new, "repo": repo,
         "finished": True},
    ]  # fmt: skip
    with (slot["state"] / "session-migrations.jsonl").open("a", encoding="utf-8") as ledger:
        ledger.writelines(json.dumps(row) + "\n" for row in rows)


def _partly_migrated(slot) -> dict[str, Path]:
    """The real transcript retitled to NEW and then back to OLD, a fresh NEW one, and an unrelated one."""
    return {
        "real": _transcript(slot, REAL, [NEW, OLD], MIGRATED_AT - timedelta(days=5), MIGRATED_AT + timedelta(hours=2)),
        "fresh": _transcript(
            slot, FRESH, [NEW], MIGRATED_AT + timedelta(hours=2, minutes=1), MIGRATED_AT + timedelta(hours=2, minutes=5)
        ),
        "other": _transcript(
            slot, OTHER, [NEW, "myproject-1-side-quest"], MIGRATED_AT - timedelta(days=30), MIGRATED_AT
        ),
    }


def _resolve(slot) -> tuple[int, str, str]:
    return run_cli(["ai", "internal", "resolve-continue-target", str(slot["cwd"]), NEW])


def test_given_a_partly_migrated_slot_when_resolving_new_then_prints_the_transcript_still_titled_old(slot):
    transcripts = _partly_migrated(slot)
    _record_migration(slot)

    code, out, _err = _resolve(slot)

    assert code == 0
    assert out.strip() == str(transcripts["real"])


def test_given_no_transcript_titled_new_when_a_migration_is_recorded_then_prints_the_one_titled_old(slot):
    transcripts = _partly_migrated(slot)
    transcripts["fresh"].unlink()
    _record_migration(slot)

    code, out, _err = _resolve(slot)

    assert code == 0
    assert out.strip() == str(transcripts["real"])


def test_given_no_recorded_migration_when_resolving_new_then_keeps_the_newest_transcript_titled_new(slot):
    transcripts = _partly_migrated(slot)
    _record_migration(slot, old="myproject-9")

    code, out, _err = _resolve(slot)

    assert code == 0
    assert out.strip() == str(transcripts["fresh"])


def test_given_a_transcript_retitled_new_before_the_migration_when_resolving_then_it_beats_a_stray_one_titled_old(
    slot,
):
    retitled = _transcript(slot, REAL, [OLD, NEW], MIGRATED_AT - timedelta(days=5), MIGRATED_AT + timedelta(hours=1))
    _transcript(slot, STRAY, [OLD], MIGRATED_AT - timedelta(days=2), MIGRATED_AT + timedelta(hours=2))
    _record_migration(slot)

    code, out, _err = _resolve(slot)

    assert code == 0
    assert out.strip() == str(retitled)


def test_given_a_partly_migrated_slot_when_bare_launching_new_then_resumes_the_transcript_still_titled_old(slot):
    _partly_migrated(slot)
    _record_migration(slot)

    command = _bare_engine_command("c", NEW, slot["cwd"], None, "gemini", "--no-sandbox", [])

    assert command[-4:] == ["--resume", REAL, "--name", NEW]


def test_given_two_fresh_transcripts_titled_new_when_resolving_a_partly_migrated_slot_then_the_note_names_the_old_one(
    slot,
):
    transcripts = _partly_migrated(slot)
    _transcript(
        slot, STRAY, [NEW], MIGRATED_AT + timedelta(hours=2, minutes=2), MIGRATED_AT + timedelta(hours=2, minutes=3)
    )
    _record_migration(slot)

    code, out, err = _resolve(slot)

    assert code == 0
    assert out.strip() == str(transcripts["real"])
    assert f"Resuming {REAL}, which the migration to '{NEW}' left under its old title" in err
    assert "Resuming the most recently active one" not in err
