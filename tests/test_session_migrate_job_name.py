"""A migrated session keeps its new name when Claude Code resumes it.

Claude Code keeps a job record for a session it has run in the background,
``<config dir>/jobs/<first 8 characters of the session id>/state.json``. An interactive
session reads the record under its own short id, and when the record's ``name`` was set by
the user and differs from the session's current name, Claude Code renames the session to it:
it appends a ``custom-title`` and an ``agent-name`` record naming it and writes it into its
live session record. ``--name NEW`` on the launch does not stop that, so a migration that
leaves the job record naming OLD sends the resumed session back to OLD.
"""

from __future__ import annotations

import json
from pathlib import Path

import test_session_migrate
from test_session_migrate import NEW, OLD, UUID, _migrate, run_cli

from ai_cli.cc_migrate import cc_project_dir
from ai_cli.main import _find_migrated_cc_session

slot = test_session_migrate.slot

FOREIGN = f"{UUID[:8]}-ffff-4000-8000-00000000000f"


def _job(slot, session_id: str = UUID, name: str = OLD) -> Path:
    record = slot["claude"] / "jobs" / session_id[:8] / "state.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    job = {
        "state": "done",
        "name": name,
        "nameSource": "user",
        "sessionId": session_id,
        "resumeSessionId": session_id,
        "backend": "daemon",
        "cwd": str(slot["wt"]),
    }
    record.write_text(json.dumps(job, indent=2), encoding="utf-8")
    return record


def _job_name(record: Path) -> object:
    return json.loads(record.read_text(encoding="utf-8")).get("name")


def _new_wt(slot) -> Path:
    return slot["repo"] / ".worktrees" / NEW


def _new_transcript(slot) -> Path:
    return cc_project_dir(_new_wt(slot), slot["claude"]) / f"{UUID}.jsonl"


def _append(path: Path, *records: dict) -> None:
    with path.open("a", encoding="utf-8") as out:
        out.writelines(json.dumps(record) + "\n" for record in records)


def _names(path: Path, key: str) -> list[str]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [row[key] for row in rows if key in row]


def _resolve(slot, cwd: Path, name: str) -> tuple[int, str, str]:
    return run_cli(["ai", "internal", "resolve-continue-target", str(cwd), name])


def _resumed_under_old_name(transcript: Path) -> None:
    """What Claude Code appends when its job record renames the resumed session to OLD."""
    _append(
        transcript,
        {"type": "custom-title", "customTitle": OLD, "sessionId": UUID},
        {"type": "agent-name", "agentName": OLD, "sessionId": UUID},
    )


def test_given_a_job_record_naming_old_when_migrated_then_it_names_new(slot):
    _append(slot["project"] / f"{UUID}.jsonl", {"type": "agent-name", "agentName": OLD, "sessionId": UUID})
    record = _job(slot)

    code, out, err = _migrate(slot["repo"], "-y")

    assert code == 0, out + err
    assert "job name | done now" in out
    assert _job_name(record) == NEW
    assert _names(_new_transcript(slot), "customTitle")[-1] == NEW
    assert _names(_new_transcript(slot), "agentName")[-1] == NEW
    code, out, err = _resolve(slot, _new_wt(slot), NEW)
    assert code == 0, err
    assert out.strip() == str(_new_transcript(slot))


def test_given_a_resume_that_wrote_old_after_the_migration_when_finished_then_the_session_is_named_new(slot):
    _append(slot["project"] / f"{UUID}.jsonl", {"type": "agent-name", "agentName": OLD, "sessionId": UUID})
    record = _job(slot)
    assert _migrate(slot["repo"], "-y")[0] == 0
    # A pair migrated before the job record was renamed: the record still names OLD, so the
    # resume under NEW was renamed back and wrote OLD after the migration's NEW records.
    _job(slot)
    transcript = _new_transcript(slot)
    _resumed_under_old_name(transcript)
    assert _names(transcript, "customTitle")[-2:] == [NEW, OLD]

    assert _find_migrated_cc_session(_new_wt(slot), NEW, []) == transcript
    code, out, err = _resolve(slot, _new_wt(slot), NEW)
    assert code == 0, err
    assert out.strip() == str(transcript)

    code, out, err = _migrate(slot["repo"], "--finish", "-n")
    assert code == 0, out + err
    assert "job name | would do" in out
    assert _job_name(record) == OLD

    code, out, err = _migrate(slot["repo"], "--finish", "-y")
    assert code == 0, out + err
    assert "job name | done now" in out
    assert _job_name(record) == NEW
    assert OLD not in _names(transcript, "customTitle") + _names(transcript, "agentName")
    code, out, err = _resolve(slot, _new_wt(slot), NEW)
    assert code == 0, err
    assert out.strip() == str(transcript)

    code, out, err = _migrate(slot["repo"], "--finish", "-y")
    assert code == 0, out + err
    assert "job name | done earlier" in out


def test_given_another_sessions_job_record_under_the_same_short_id_when_migrated_then_it_is_left_alone(slot):
    record = _job(slot, session_id=FOREIGN)
    before = record.read_bytes()

    code, out, err = _migrate(slot["repo"], "-y")

    assert code == 0, out + err
    assert "job name | skipped: no matching artifact" in out
    assert record.read_bytes() == before


def test_given_a_session_never_migrated_when_resolved_then_its_transcript_and_job_record_are_as_today(slot):
    record = _job(slot)
    before = record.read_bytes()
    transcript = slot["project"] / f"{UUID}.jsonl"
    _resumed_under_old_name(transcript)

    code, out, err = _resolve(slot, slot["wt"], OLD)
    assert code == 0, err
    assert out.strip() == str(transcript)
    code, out, err = _resolve(slot, slot["wt"], NEW)
    assert code == 0, err
    assert out.strip() == ""
    assert _find_migrated_cc_session(slot["wt"], NEW, []) is None
    assert record.read_bytes() == before
