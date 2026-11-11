"""A migrated transcript answers to the new name in its ``agent-name`` records too.

Claude Code restores a resumed session's name from the transcript's last ``agent-name``
record, so a rename that rewrites only ``custom-title`` brings the session back under
the old name on resume.
"""

from __future__ import annotations

import json
from pathlib import Path

import test_session_migrate
from test_session_migrate import NEW, OLD, UUID, _migrate, run_cli

from ai_cli import session_migrate as migration
from ai_cli.cc_migrate import cc_project_dir

slot = test_session_migrate.slot


def _agent_name(name: str) -> dict:
    return {"type": "agent-name", "agentName": name, "sessionId": UUID}


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps(record) + "\n")


def _new_transcript(slot) -> Path:
    return cc_project_dir(slot["repo"] / ".worktrees" / NEW, slot["claude"]) / f"{UUID}.jsonl"


def _finish(repo: Path, *flags: str):
    from unittest.mock import patch

    with (
        patch("ai_cli.session._tmux_session_names", return_value=[]),
        patch("ai_cli.config._get_projects_dir", return_value=repo.parent),
    ):
        return run_cli(["ai", "migrate-session", OLD, NEW, "-r", str(repo), "--finish", *flags])


def test_given_a_transcript_with_old_agent_name_records_when_migrated_then_no_record_names_old(slot):
    transcript = cc_project_dir(slot["wt"], slot["claude"]) / f"{UUID}.jsonl"
    _append(transcript, _agent_name(OLD))
    _append(transcript, {"type": "custom-title", "customTitle": OLD})
    _append(transcript, _agent_name(OLD))

    code, out, err = _migrate(slot["repo"], "-y")

    assert code == 0, out + err
    records = _records(_new_transcript(slot))
    assert [r["agentName"] for r in records if "agentName" in r] == [NEW, NEW]
    assert all(r.get("customTitle") in (None, NEW) for r in records)


def test_given_only_agent_name_records_naming_old_when_migrated_then_they_are_rewritten(slot):
    transcript = cc_project_dir(slot["wt"], slot["claude"]) / f"{UUID}.jsonl"
    kept = [r for r in _records(transcript) if r.get("type") != "custom-title"]
    transcript.write_text("".join(json.dumps(r) + "\n" for r in [*kept, _agent_name(OLD)]), encoding="utf-8")

    code, out, err = _migrate(slot["repo"], "-y")

    assert code == 0, out + err
    assert [r["agentName"] for r in _records(_new_transcript(slot)) if "agentName" in r] == [NEW]


def test_given_a_migrated_transcript_left_with_an_old_agent_name_when_finished_then_it_is_reported_and_repaired(slot):
    assert _migrate(slot["repo"], "-y")[0] == 0
    transcript = _new_transcript(slot)
    _append(transcript, _agent_name(OLD))

    code, out, err = _finish(slot["repo"], "-n")
    assert code == 0, out + err
    assert "transcript title | would do" in out
    assert _agent_name(OLD) in _records(transcript)

    code, out, err = _finish(slot["repo"], "-y")
    assert code == 0, out + err
    assert "transcript title | done now" in out
    assert all(r.get("agentName") != OLD for r in _records(transcript))

    code, out, err = _finish(slot["repo"], "-y")
    assert code == 0, out + err
    assert "transcript title | done earlier" in out


def test_given_the_old_agent_name_restored_during_migration_when_migrated_then_the_resolve_check_fails(
    slot, monkeypatch
):
    real = migration.neutralise_worktree_state

    def restore_old_name(path, dest_root):
        changed = real(path, dest_root)
        _append(path, _agent_name(OLD))
        return changed

    monkeypatch.setattr(migration, "neutralise_worktree_state", restore_old_name)

    code, out, err = _migrate(slot["repo"], "-y")

    assert code == 1, out + err
    assert "post-migrate check FAILED" in out + err
