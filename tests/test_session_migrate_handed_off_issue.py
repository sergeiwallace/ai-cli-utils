"""A ``--finish`` whose re-keyed issue was since handed to a third session still finishes.

The migration re-keys OLD's issues to NEW and journals each one. When a later finish finds a
journaled issue held by neither OLD nor NEW, the issue was handed on to another session, and
there is nothing left for the migration to re-key. That must not fail the Beads step.
"""

from __future__ import annotations

import json

import test_session_migrate
from test_session_migrate import NEW, OLD, _assignees, _finish, _migrate

slot = test_session_migrate.slot
stores = test_session_migrate.stores


def _pending(slot) -> None:
    """Drop the migration's own ``finished`` rows, so the pair needs a finish again."""
    ledger = slot["state"] / "session-migrations.jsonl"
    rows = ledger.read_text(encoding="utf-8").splitlines(keepends=True)
    ledger.write_text("".join(r for r in rows if not json.loads(r).get("finished")), encoding="utf-8")


def test_given_a_re_keyed_issue_handed_to_another_session_when_finished_then_the_beads_step_succeeds(stores, slot):
    code, out, err = _migrate(stores["repo"], "-y")
    assert code == 0, out + err
    assert _assignees(stores)["TST-1"] == NEW
    data = json.loads(stores["db"].read_text(encoding="utf-8"))
    next(i for i in data["stores"][str(stores["repo"])] if i["id"] == "TST-1")["assignee"] = "other-2"
    stores["db"].write_text(json.dumps(data), encoding="utf-8")
    _pending(slot)

    code, out, err = _finish(stores["repo"], "-y")

    assert code == 0, out + err
    assert "completion unverified" not in out + err
    held = _assignees(stores)
    assert (held["TST-1"], held["TST-2"], held["WS-1"]) == ("other-2", NEW, NEW)
    assert OLD not in (held["TST-1"], held["TST-2"], held["WS-1"])
