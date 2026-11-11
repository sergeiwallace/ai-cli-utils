"""``ai migrate-session --finish`` merges a poller ``.notified`` marker filed under both names.

A ``pollers/<key>__<id>.notified`` marker holds one epoch: when that poller's fault was
last announced. A session relaunched under its new name before the migration finished
can leave one under each name. The two say the same thing, so the finish keeps the later
timestamp under the new name instead of refusing; every other resume artifact found
under both names still refuses.
"""

from __future__ import annotations

import json

import pytest
import test_session_migrate
from test_session_migrate import NEW, OLD, OUTBOX_KEYED, _everything, _finish, _migrate

slot = test_session_migrate.slot
stores = test_session_migrate.stores

POLLER_ID = "ab" * 16
EARLIER, LATER = 1700000000, 1700100000


def _ledger_finished(slot) -> list[dict]:
    path = slot["state"] / "session-migrations.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [row for row in rows if row.get("finished")]


def _part(out: str, name: str) -> str:
    row = f"migrate: {name} | "
    return next(line.partition(row)[2] for line in out.splitlines() if row in line)


def _moved_but_unfinished(slot, stores) -> None:
    """Migrate with one Beads store unreadable, so the pair is recorded but not finished."""
    data = json.loads(stores["db"].read_text(encoding="utf-8"))
    data["fail"][str(stores["workspace"])] = "error"
    stores["db"].write_text(json.dumps(data), encoding="utf-8")
    assert _migrate(slot["repo"], "-y")[0] == 1
    data["fail"] = {}
    stores["db"].write_text(json.dumps(data), encoding="utf-8")
    assert _ledger_finished(slot) == []


def _markers(slot, old_text: str, new_text: str):
    pollers = slot["claude"] / "resume" / "pollers"
    pollers.mkdir(parents=True, exist_ok=True)
    old_marker = pollers / f"{OLD}__{POLLER_ID}.notified"
    new_marker = pollers / f"{NEW}__{POLLER_ID}.notified"
    old_marker.write_text(old_text, encoding="utf-8")
    new_marker.write_text(new_text, encoding="utf-8")
    return old_marker, new_marker


@pytest.mark.parametrize(
    ("old_epoch", "new_epoch"), [(LATER, EARLIER), (EARLIER, LATER)], ids=["old-later", "new-later"]
)
def test_given_a_notified_marker_under_both_names_when_finished_then_the_later_timestamp_survives_under_new(
    slot, stores, old_epoch, new_epoch
):
    _moved_but_unfinished(slot, stores)
    old_marker, new_marker = _markers(slot, f"{old_epoch}\n", f"{new_epoch}\n")
    before = _everything(slot)

    code, out, err = _finish(slot["repo"], "-n")

    assert code == 0, out + err
    assert _everything(slot) == before
    assert (
        f"resume: pollers/{old_marker.name} -> {new_marker.name} merged (keeps the later timestamp {LATER})" in out
    ), out
    assert "NOT re-keyed" not in out + err
    assert _part(out, "resume") == "would do"

    code, out, err = _finish(slot["repo"], "-y")

    assert code == 0, out + err
    assert not old_marker.exists()
    assert new_marker.read_text(encoding="utf-8") == f"{LATER}\n"
    assert _part(out, "resume") == "done now"
    assert "NOT re-keyed" not in out + err
    assert _ledger_finished(slot) != []


def test_given_a_notified_marker_and_an_outbox_file_under_both_names_when_finished_then_only_the_outbox_conflicts(
    slot, stores
):
    _moved_but_unfinished(slot, stores)
    old_marker, new_marker = _markers(slot, f"{LATER}\n", f"{EARLIER}\n")
    outbox = slot["claude"] / "resume" / "outbox"
    old_outbox, new_outbox = outbox / OUTBOX_KEYED, outbox / OUTBOX_KEYED.replace(OLD, NEW, 1)
    assert new_outbox.is_file()
    old_outbox.write_text("a later hand-off\n", encoding="utf-8")
    before = _everything(slot)

    code, out, err = _finish(slot["repo"], "-n")

    assert _everything(slot) == before
    assert f"pollers/{old_marker.name} -> {new_marker.name} merged" in out
    assert f"resume artifact {old_outbox} was NOT re-keyed: {new_outbox} already exists" in err
    assert f"{old_marker} was NOT re-keyed" not in err

    code, out, err = _finish(slot["repo"], "-y")

    assert code == 1, out + err
    assert not old_marker.exists()
    assert new_marker.read_text(encoding="utf-8") == f"{LATER}\n"
    assert old_outbox.read_text(encoding="utf-8") == "a later hand-off\n"
    assert new_outbox.read_text(encoding="utf-8") == "hand-off\n"
    resume = _part(out, "resume")
    assert resume.startswith("failed: ") and str(old_outbox) in resume, resume
    assert str(old_marker) not in resume
    assert _ledger_finished(slot) == []


def test_given_a_notified_marker_that_holds_no_epoch_when_finished_then_it_still_conflicts(slot, stores):
    _moved_but_unfinished(slot, stores)
    old_marker, new_marker = _markers(slot, "not a timestamp\n", f"{EARLIER}\n")

    code, out, err = _finish(slot["repo"], "-y")

    assert code == 1, out + err
    assert old_marker.read_text(encoding="utf-8") == "not a timestamp\n"
    assert new_marker.read_text(encoding="utf-8") == f"{EARLIER}\n"
    assert f"resume artifact {old_marker} was NOT re-keyed: {new_marker} already exists" in err
    assert _part(out, "resume").startswith("failed: ")
