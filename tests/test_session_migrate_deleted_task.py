"""A ``--finish`` whose journaled task file was deleted by the user still finishes.

The task step journals each task id before it moves anything, writes the destination before it
removes the source, and so never leaves an id with neither file. When neither exists, the user
deleted the task after the migration, and there is nothing left to move. That must not refuse
the finish's other steps.
"""

from __future__ import annotations

import json

import test_session_migrate
import test_session_migrate_on_launch
from test_session_migrate import NEW, _assert_logs_and_evidence_follow_new, _finish

slot = test_session_migrate.slot
migrated = test_session_migrate_on_launch.migrated


def _journaled_task(slot) -> dict:
    journal = slot["state"] / "session-migration-tasks.jsonl"
    return json.loads(journal.read_text(encoding="utf-8").splitlines()[-1])["tasks"][0]


def test_given_a_journaled_task_deleted_after_the_migration_when_finished_then_every_other_step_runs(migrated):
    task = _journaled_task(migrated)
    (migrated["claude"] / "tasks" / NEW / f"{task['dest']}.json").unlink()

    code, out, err = _finish(migrated["repo"], "-y")

    assert code == 0, out + err
    assert "restore the missing artifact" not in out + err
    _assert_logs_and_evidence_follow_new(migrated, migrated["seeded"])
    rows = [json.loads(line) for line in (migrated["state"] / "session-migrations.jsonl").read_text().splitlines()]
    assert [row.get("verified") for row in rows if row.get("finished")] == [True]
