"""``ai internal resolve-continue-target`` prefers the transcript a migration moved over a stray under NEW.

A session relaunched under NEW before its migration finished starts a short transcript titled
NEW. Both it and the migrated transcript then carry NEW, and a resolver that picks the most
recently active one resumes the stray, losing the migrated conversation. The migration records
the transcripts it moved in its ledger, and the resolver resumes the recorded one, naming the
stray it skipped.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
import test_session_migrate
from test_session_migrate import NEW, OLD, UUID, _migrate, run_cli

from ai_cli.cc_migrate import cc_project_dir

slot = test_session_migrate.slot

STRAY = "dddddddd-0000-4000-8000-000000000004"
EARLIER = "eeeeeeee-0000-4000-8000-000000000005"


def _new_project(slot) -> Path:
    return cc_project_dir(slot["repo"] / ".worktrees" / NEW, slot["claude"])


def _write(path: Path, records: list[dict], age_seconds: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))
    return path


def _stray(slot, uuid: str = STRAY, age_seconds: float = 0) -> Path:
    """A transcript a relaunch under NEW started: titled NEW from its first record."""
    records = [
        {"type": "user", "sessionId": uuid, "timestamp": "2099-01-01T00:00:00Z", "message": {"content": "hi"}},
        {"type": "custom-title", "customTitle": NEW, "sessionId": uuid},
        {"type": "agent-name", "agentName": NEW, "sessionId": uuid},
    ]
    return _write(_new_project(slot) / f"{uuid}.jsonl", records, age_seconds)


def _age(path: Path, age_seconds: float) -> None:
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))


def _resolve(slot) -> tuple[int, str, str]:
    return run_cli(["ai", "internal", "resolve-continue-target", str(slot["repo"] / ".worktrees" / NEW), NEW])


def _finish(slot, *flags: str):
    return _migrate(slot["repo"], "--finish", *flags)


def _ledger(slot) -> Path:
    return slot["state"] / "session-migrations.jsonl"


def _rows(slot) -> list[dict]:
    return [json.loads(line) for line in _ledger(slot).read_text(encoding="utf-8").splitlines() if line.strip()]


def _strip_transcript_ids(slot) -> None:
    """The ledger an older ``ai`` wrote: no row names a transcript."""
    rows = [{key: value for key, value in row.items() if key != "transcripts"} for row in _rows(slot)]
    _ledger(slot).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


@pytest.mark.parametrize("stray_is_later", [True, False], ids=["stray-later", "migrated-later"])
def test_given_a_stray_titled_new_beside_the_migrated_transcript_when_resolving_then_resumes_the_migrated_one(
    slot, stray_is_later
):
    assert _migrate(slot["repo"], "-y")[0] == 0
    migrated = _new_project(slot) / f"{UUID}.jsonl"
    stray = _stray(slot)
    _age(stray if not stray_is_later else migrated, 600)

    code, out, err = _resolve(slot)

    assert code == 0, err
    assert out.strip() == str(migrated)
    assert f"Resuming {UUID}, the transcript migrated to '{NEW}'" in err
    assert f"skipped {STRAY}" in err


def test_given_an_already_finished_pair_without_transcript_ids_when_finished_again_then_the_migrated_one_resumes(
    slot,
):
    assert _migrate(slot["repo"], "-y")[0] == 0
    migrated = _new_project(slot) / f"{UUID}.jsonl"
    with migrated.open("a", encoding="utf-8") as out:
        # The session wrote its old name once more before it was stopped.
        out.write(json.dumps({"type": "agent-name", "agentName": OLD, "sessionId": UUID}) + "\n")
        out.write(json.dumps({"type": "agent-name", "agentName": NEW, "sessionId": UUID}) + "\n")
    _age(migrated, 600)
    stray = _stray(slot)
    _strip_transcript_ids(slot)
    with _ledger(slot).open("a", encoding="utf-8") as ledger:
        row = {"time": "2099-01-01T00:00:00+00:00", "old": OLD, "new": NEW, "repo": str(slot["repo"])}
        ledger.write(json.dumps({**row, "finished": True}) + "\n")
    assert _resolve(slot)[1].strip() == str(stray)

    code, out, err = _finish(slot, "-y")
    assert code == 0, out + err
    assert "transcript title | done now" in out

    assert [row["transcripts"] for row in _rows(slot) if "transcripts" in row] == [[UUID]]
    code, out, err = _resolve(slot)
    assert code == 0, err
    assert out.strip() == str(migrated)
    assert f"skipped {STRAY}" in err


def test_given_no_migration_on_record_when_two_transcripts_carry_the_name_then_the_most_recent_one_resumes(slot):
    newer = _stray(slot, STRAY, age_seconds=0)
    _stray(slot, EARLIER, age_seconds=600)
    assert not _ledger(slot).exists()

    code, out, err = _resolve(slot)

    assert code == 0, err
    assert out.strip() == str(newer)
    assert f"Resuming the most recently active one: {STRAY}" in err
    assert "skipped" not in err
