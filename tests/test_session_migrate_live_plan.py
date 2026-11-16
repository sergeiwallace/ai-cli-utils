"""``ai migrate-session --finish`` on a migration whose NEW session is live.

A live NEW session is either a Claude Code process record naming it, or the tmux session
whose wrapper restarts it. The dry run must print the plan, name the live session and write
nothing; the writing run carries every leftover that is not a file of the running session
(``tests/test_session_migrate_finish_while_live.py`` covers the ones it defers). A live
session in a leftover slot, or a live OLD, still refuses the writing run.
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
import test_session_migrate
import test_session_migrate_agents
from conftest import snapshot_tree
from test_session_migrate import NEW, OLD, UUID, _everything, _finish, _leftovers, _migrate

slot = test_session_migrate.slot
leftover = test_session_migrate_agents.leftover


def _live_process(slot, name: str = NEW) -> None:
    sessions = slot["claude"] / "sessions"
    sessions.mkdir(exist_ok=True)
    new_wt = slot["repo"] / ".worktrees" / NEW
    (sessions / f"{os.getpid()}.json").write_text(
        json.dumps({"pid": os.getpid(), "name": name, "cwd": str(new_wt), "sessionId": UUID})
    )


@pytest.fixture
def migrated(slot):
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    slot["left"] = _leftovers(slot)
    return slot


LIVE = [
    pytest.param({"tmux": [f"c-{NEW}"]}, f"c-{NEW}", id="tmux"),
    pytest.param({"process": True}, NEW, id="process"),
]


@pytest.mark.parametrize(("live", "named"), LIVE)
def test_given_new_is_live_when_finish_dry_runs_then_it_plans_names_the_session_and_writes_nothing(
    migrated, live, named
):
    if live.get("process"):
        _live_process(migrated)
    before = _everything(migrated)

    code, out, err = _finish(migrated["repo"], "-n", tmux=live.get("tmux"))

    assert code in (0, 1), out + err
    assert "slot lock:" in out and "memory file:" in out
    line = next(line for line in out.splitlines() if "live session:" in line)
    assert named in line and "a writing run does every other step and defers" in line
    assert "task namespace, transcript title until it is stopped" in line
    assert f"ai sessions stop {NEW}" in line
    assert _everything(migrated) == before


@pytest.mark.parametrize(("live", "named"), LIVE)
def test_given_new_is_live_with_nothing_of_its_own_left_when_finish_writes_then_it_carries_every_leftover(
    migrated, live, named
):
    """None of these leftovers is a file of the running NEW, so the finish completes while it runs."""
    if live.get("process"):
        _live_process(migrated)

    code, out, err = _finish(migrated["repo"], "-y", tmux=live.get("tmux"))

    assert code == 0, out + err
    assert named in out
    test_session_migrate._assert_leftovers_carried(migrated, migrated["left"])


def test_given_new_is_live_when_finish_writes_without_yes_then_it_asks_before_writing(migrated):
    from ai_cli import main

    before = _everything(migrated)

    with patch.object(main._out, "confirm", return_value=False) as confirm:
        code, out, err = _finish(migrated["repo"], tmux=[f"c-{NEW}"])

    assert code == 1, out + err
    assert confirm.call_args.args[2] == f"Migrate {OLD} to {NEW}?"
    assert "Aborted." in err
    assert _everything(migrated) == before


def test_given_a_live_session_in_a_leftover_slot_when_cleanup_dry_runs_then_it_plans_and_names_the_session(
    leftover,
):
    test_session_migrate_agents._live(leftover["slot"] / "src", name=NEW)
    before = snapshot_tree(leftover["projects"])

    code, out, err = test_session_migrate_agents._run_in(leftover, leftover["second"], "--finish", "-n")

    assert code in (0, 1), out + err
    assert "live session:" in out and "a writing run refuses until it is stopped" in out
    assert f"leftover slot: {leftover['slot']}" in out
    assert snapshot_tree(leftover["projects"]) == before


def test_given_a_live_session_in_a_leftover_slot_when_cleanup_writes_then_it_still_refuses(leftover):
    test_session_migrate_agents._live(leftover["slot"] / "src", name=NEW)
    before = snapshot_tree(leftover["second"])

    code, out, err = test_session_migrate_agents._run_in(leftover, leftover["second"], "--finish", "-y")

    assert code == 2, out + err
    assert "still running" in out + err
    assert snapshot_tree(leftover["second"]) == before


def test_given_old_is_live_when_a_full_migration_dry_runs_then_it_is_still_refused(slot):
    """Only a ``--finish`` plan runs on a live session; a full migration's plan still refuses."""
    _live_process(slot, name=OLD)

    code, _out, err = _migrate(slot["repo"], "-n")

    assert code == 2
    assert "still running" in err
