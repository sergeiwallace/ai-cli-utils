"""A pending ``--finish`` does what is safe while NEW runs, defers the rest, and reports that once.

A session its supervisor restarts rather than exits never stops, so a finish that waited for
NEW to stop never ran. Each test migrates the fixture slot for real, drops the ``finished``
row so the pair is pending, then seeds leftovers of both kinds: OLD-keyed launch logs and
resume evidence, which a finish may carry while NEW runs, and a transcript still titled OLD
and a task still in OLD's namespace, which are the running session's own open files. NEW is
live either as a Claude Code process record inside its slot or as its tmux session.
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
import test_session_migrate
import test_session_migrate_on_launch
from test_session_migrate import NEW, OLD, _assert_logs_and_evidence_follow_new, _everything, _finish, run_cli

from ai_cli.cc_migrate import cc_project_dir

slot = test_session_migrate.slot
migrated = test_session_migrate_on_launch.migrated
_launch = test_session_migrate_on_launch._launch

STALE = "4b4b4b4b-0000-4000-8000-000000000004"


def _ledger(slot) -> list[dict]:
    path = slot["state"] / "session-migrations.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _live_record(slot, name: str = NEW) -> None:
    sessions = slot["claude"] / "sessions"
    sessions.mkdir(exist_ok=True)
    cwd = slot["repo"] / ".worktrees" / NEW
    (sessions / f"{os.getpid()}.json").write_text(json.dumps({"pid": os.getpid(), "name": name, "cwd": str(cwd)}))


def _stopped(slot) -> None:
    (slot["claude"] / "sessions" / f"{os.getpid()}.json").unlink()


@pytest.fixture
def pending(migrated):
    """The pending pair, plus a transcript still titled OLD and a task still in OLD's namespace.

    The task is the one an interrupted task step leaves: its id mapping is journaled, its
    source is back in OLD's namespace byte for byte, and its destination is not written.
    """
    project = cc_project_dir(migrated["repo"] / ".worktrees" / NEW, migrated["claude"])
    transcript = project / f"{STALE}.jsonl"
    transcript.write_text(json.dumps({"type": "custom-title", "customTitle": OLD}) + "\n", encoding="utf-8")
    journal = migrated["state"] / "session-migration-tasks.jsonl"
    first = json.loads(journal.read_text(encoding="utf-8").splitlines()[-1])["tasks"][0]
    (migrated["claude"] / "tasks" / NEW / f"{first['dest']}.json").unlink()
    task = migrated["claude"] / "tasks" / OLD / f"{first['source']}.json"
    task.parent.mkdir(parents=True)
    task.write_text(json.dumps({"id": first["source"], "subject": f"task {first['source']}"}))
    migrated["open_files"] = {path: path.read_bytes() for path in (transcript, task)}
    migrated["task_dest"] = migrated["claude"] / "tasks" / NEW / f"{first['dest']}.json"
    return migrated


def _open_files_untouched(slot) -> bool:
    return all(path.is_file() and path.read_bytes() == body for path, body in slot["open_files"].items())


def _deferred_rows(slot) -> list[dict]:
    return [row for row in _ledger(slot) if row.get("deferred_recorded")]


def _finished_rows(slot) -> list[dict]:
    return [row for row in _ledger(slot) if row.get("finished")]


def _warnings(err: str) -> list[str]:
    return [line for line in err.splitlines() if "Warning:" in line and f"{OLD} -> {NEW}" in line]


def test_given_new_is_live_when_finish_writes_then_safe_steps_run_and_its_open_files_are_deferred(pending):
    _live_record(pending)

    code, out, err = _finish(pending["repo"], "-y")

    assert code == 1, out + err
    _assert_logs_and_evidence_follow_new(pending, pending["seeded"])
    assert _open_files_untouched(pending), "a live session's transcript and task files are never written"
    for part in ("transcript title:", "task namespace:"):
        assert any(part in line and "(deferred while live" in line for line in out.splitlines()), out
    assert "deferred: " in out + err and f"ai sessions stop {NEW}" in out + err
    assert [row["deferred"] for row in _deferred_rows(pending)] == [["task namespace", "transcript title"]]
    assert _finished_rows(pending) == []


def test_given_old_is_live_when_finish_writes_then_it_still_refuses_and_writes_nothing(pending):
    _live_record(pending, name=OLD)
    before = _everything(pending)

    code, out, err = _finish(pending["repo"], "-y")

    assert code == 2, out + err
    assert "still running" in err
    assert _everything(pending) == before


def test_given_a_deferred_finish_when_new_stops_then_the_next_finish_completes_and_verifies(pending):
    _live_record(pending)
    code, out, err = _finish(pending["repo"], "-y")
    assert code == 1, out + err
    _stopped(pending)

    code, out, err = _finish(pending["repo"], "-y")

    assert code == 0, out + err
    assert not _open_files_untouched(pending), "the deferred transcript and task steps ran"
    assert pending["task_dest"].is_file() and not (pending["claude"] / "tasks" / OLD).exists()
    assert [row.get("verified") for row in _finished_rows(pending)] == [True]


def test_given_new_is_attached_live_twice_when_launched_then_the_deferral_warns_once(pending):
    first = _launch(pending, [f"c-{NEW}"], live=True)
    second = _launch(pending, [f"c-{NEW}"], live=True)

    for code, out, err, launched in (first, second):
        assert code == 0, out + err
        assert [entry["ai_name"] for entry in launched] == [NEW], "the launch goes on to the running session"
    assert first[3][0]["old_logs"] == [], "the safe steps ran under the live session"
    assert _open_files_untouched(pending)
    warned = _warnings(first[2])
    assert len(warned) == 1, first[2]
    assert "every step is done except task namespace, transcript title" in warned[0]
    assert f"ai sessions stop {NEW}" in warned[0] and f"--finish -r {pending['repo']}" in warned[0]
    assert _warnings(second[2]) == [], second[2]
    assert "still deferred while" in second[2]


def _restart_finish(slot) -> tuple[int, str, str]:
    """``ai internal finish-migrations c-NEW NEW``, as NEW's supervisor runs it between two agents."""
    with (
        patch("ai_cli.session._tmux_session_names", return_value=[]),
        patch("ai_cli.config._get_projects_dir", return_value=slot["repo"].parent),
        patch.dict(os.environ),
    ):
        os.environ.pop("TMUX", None)
        return run_cli(["ai", "internal", "finish-migrations", f"c-{NEW}", NEW])


def test_given_new_stays_live_when_its_supervisor_restarts_it_twice_then_the_deferral_warns_once(pending):
    _live_record(pending)

    first = _restart_finish(pending)
    second = _restart_finish(pending)

    assert first[0] == second[0] == 0, first + second
    _assert_logs_and_evidence_follow_new(pending, pending["seeded"])
    assert _open_files_untouched(pending)
    assert len(_warnings(first[2])) == 1, first[2]
    assert _warnings(second[2]) == [], second[2]
    assert "still deferred while" in second[2]
