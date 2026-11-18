"""A writing ``ai migrate-session`` records ``finished`` only when a read-only re-plan finds nothing left.

Each test drives the CLI on the fixture slot. A part is made to stay left by a hook whose
``--dry-run`` keeps reporting ``migrate-status: pending`` after its live run, or prints no
status at all: the re-plan cannot read such a hook as done.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import test_migrate_to_name
import test_session_migrate
import test_session_migrate_hooks
from test_migrate_to_name import migrate as migrate_to_name
from test_session_migrate import NEW, OLD, _finish, _migrate

from ai_cli.config import get_xdg_config_home

slot = test_session_migrate.slot
hooks = test_session_migrate_hooks.hooks
fleet = test_migrate_to_name.fleet

STUCK = """\
import sys
if "--dry-run" in sys.argv:
    print("would rewrite board/" + sys.argv[sys.argv.index("--old") + 1])
    {status}
"""


def _stuck_hook(tmp_path: Path, status: str = 'print("migrate-status: pending")') -> None:
    """A hook named ``board`` whose plan never reads done, however often it runs live."""
    directory = get_xdg_config_home() / "migrate-session.d"
    directory.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "board_hook.py"
    script.write_text(STUCK.format(status=status), encoding="utf-8")
    (directory / "10-board.toml").write_text(f'name = "board"\nargv = [{json.dumps(str(script))}]\n')


def _rows(state: Path) -> list[dict]:
    path = state / "session-migrations.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _finished(state: Path) -> list[dict]:
    return [row for row in _rows(state) if row.get("finished")]


def _moved(slot) -> None:
    """Migrate the fixture with nothing left, then drop the verified row: a pair recorded but unfinished."""
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    ledger = slot["state"] / "session-migrations.jsonl"
    keep = [json.dumps(row) + "\n" for row in _rows(slot["state"]) if not row.get("finished")]
    ledger.write_text("".join(keep), encoding="utf-8")


def test_given_a_clean_slot_when_migrated_then_one_verified_finished_row_is_appended_and_it_exits_0(slot):
    code, out, err = _migrate(slot["repo"], "-y")

    assert code == 0, out + err
    finished = _finished(slot["state"])
    assert [(row["old"], row["new"], row.get("verified")) for row in finished] == [(OLD, NEW, True)]
    assert "NOT finished" not in out + err


def test_given_a_recorded_unfinished_pair_when_finished_with_nothing_left_then_one_verified_row_and_exit_0(slot):
    _moved(slot)

    code, out, err = _finish(slot["repo"], "-y")

    assert code == 0, out + err
    assert [row.get("verified") for row in _finished(slot["state"])] == [True]


def test_given_an_unverified_finished_row_when_finished_again_then_a_verified_row_is_appended(slot):
    _moved(slot)
    with (slot["state"] / "session-migrations.jsonl").open("a", encoding="utf-8") as ledger:
        ledger.write(json.dumps({"old": OLD, "new": NEW, "repo": str(slot["repo"]), "finished": True}) + "\n")

    code, out, err = _finish(slot["repo"], "-y")

    assert code == 0, out + err
    assert [row.get("verified") for row in _finished(slot["state"])] == [None, True]


@pytest.mark.parametrize(
    "status", [pytest.param('print("migrate-status: pending")', id="pending"), pytest.param("", id="no-status")]
)
def test_given_a_hook_left_pending_when_finished_then_no_finished_row_names_it_and_exits_1(slot, tmp_path, status):
    _moved(slot)
    _stuck_hook(tmp_path, status)

    code, out, err = _finish(slot["repo"], "-y")

    assert code == 1, out + err
    assert _finished(slot["state"]) == []
    warning = next(line for line in err.splitlines() if "NOT finished" in line)
    assert "hook board (would do)" in warning
    assert f"ai migrate-session {OLD} {NEW} --finish -r {slot['repo']}" in warning
    assert "hook board | failed: still left after this run (would do)" in out


def test_given_a_hook_left_pending_when_fully_migrated_then_no_finished_row_names_it_and_exits_1(slot, tmp_path):
    _stuck_hook(tmp_path)

    code, out, err = _migrate(slot["repo"], "-y")

    assert code == 1, out + err
    assert (slot["repo"] / ".worktrees" / NEW).is_dir()
    assert _finished(slot["state"]) == []
    assert [(row["old"], row["new"]) for row in _rows(slot["state"])] == [(OLD, NEW)]
    assert "NOT finished" in err and "hook board (would do)" in err


def test_given_the_re_plan_itself_refuses_when_finished_then_it_counts_as_left_and_exits_1(slot, tmp_path):
    """A hook that answers an invalid status once it has run live makes the re-plan refuse."""
    _moved(slot)
    ran = tmp_path / "odd-ran"
    script = tmp_path / "odd_hook.py"
    script.write_text(
        "import os, sys\n"
        f"ran = {str(ran)!r}\n"
        "if '--dry-run' not in sys.argv:\n"
        "    open(ran, 'w').close()\n"
        "else:\n"
        "    print('migrate-status: ' + ('unknown' if os.path.exists(ran) else 'pending'))\n",
        encoding="utf-8",
    )
    directory = get_xdg_config_home() / "migrate-session.d"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "10-odd.toml").write_text(f'name = "odd"\nargv = [{json.dumps(str(script))}]\n')

    code, out, err = _finish(slot["repo"], "-y")

    assert code == 1, out + err
    assert ran.exists()
    assert _finished(slot["state"]) == []
    assert "migration | failed: could not re-plan: hook odd returned an invalid migrate-status" in out
    assert "NOT finished" in err


def test_given_a_hook_left_pending_when_migrated_to_a_name_then_the_registry_entry_is_incomplete(fleet, tmp_path):
    _stuck_hook(tmp_path)

    code, out, err = migrate_to_name(fleet, OLD, "-t", "cleo")

    assert code == 1, out + err
    assert fleet.rows()["cleo"]["state"] == "migration-incomplete"
    assert "NOT finished" in out + err
    assert _finished(fleet.slot["state"]) == []
