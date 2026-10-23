"""``ai adopt-session`` runs the ``migrate-session.d/`` rekey hooks when it renames a session.

A retitling adoption changes the session's name, so state other tools keep under that
name must follow it exactly as it does for ``ai migrate-session``: every hook plans with
``--dry-run`` before the first write (a refusal stops the adoption with nothing moved),
and runs live after the core moves (a failure is a ``FAILED`` warning and exit 1). An
adoption that keeps the name runs no hook.
"""

from __future__ import annotations

import test_session_adopt
import test_session_migrate_hooks
from conftest import run_cli
from test_session_adopt import UUID, _add_worktree

from ai_cli.cc_migrate import cc_project_dir

world = test_session_adopt.world
existing_worktree = test_session_adopt.existing_worktree
cli_world = test_session_adopt.cli_world
hooks = test_session_migrate_hooks.hooks

RETITLE = ["ai", "adopt-session", "myproject-2", "-c", "retitle", "-I", "4"]


def _retitle_target(cli_world):
    return _add_worktree(cli_world["repo"], "myproject-4")


def test_given_a_retitling_adoption_when_dry_run_then_every_hook_plans_old_to_new_and_nothing_moves(cli_world, hooks):
    target = _retitle_target(cli_world)
    hooks("10-notes", "notes")
    source = cli_world["src_dir"] / f"{UUID}.jsonl"
    before = source.read_bytes()

    code, out, err = run_cli([*RETITLE, "-n"])

    assert code == 0, err
    assert "hook notes: would move notes/myproject-2" in out
    assert [c["args"] for c in hooks.calls()] == [["--old", "myproject-2", "--new", "myproject-4", "--dry-run"]]
    assert source.read_bytes() == before
    assert not cc_project_dir(target, cli_world["home"]).exists()


def test_given_a_retitling_adoption_when_applied_then_the_hook_runs_live_once_after_planning(cli_world, hooks):
    _retitle_target(cli_world)
    hooks("10-notes", "notes")

    code, out, err = run_cli([*RETITLE, "-y"])

    assert code == 0, err
    calls = [c["args"] for c in hooks.calls()]
    assert calls[-1] == ["--old", "myproject-2", "--new", "myproject-4"]
    assert all("--dry-run" in args for args in calls[:-1]) and calls[:-1]
    assert "hook notes: moved notes/myproject-2" in out
    assert not (cli_world["src_dir"] / f"{UUID}.jsonl").exists(), "the core move ran before the live hook"


def test_given_a_hook_that_refuses_its_plan_when_adopting_then_the_adoption_is_refused_and_nothing_moves(
    cli_world, hooks
):
    target = _retitle_target(cli_world)
    hooks("10-notes", "notes", fail="plan")
    source = cli_world["src_dir"] / f"{UUID}.jsonl"
    before = source.read_bytes()

    code, out, err = run_cli([*RETITLE, "-y"])

    assert code == 1
    assert "hook notes" in err and "refused the migration (exit 3): notes store is locked" in err
    assert source.read_bytes() == before
    assert not cc_project_dir(target, cli_world["home"]).exists()
    assert all("--dry-run" in c["args"] for c in hooks.calls())


def test_given_a_hook_that_fails_live_when_adopting_then_it_warns_failed_and_exits_one(cli_world, hooks):
    _retitle_target(cli_world)
    hooks("10-notes", "notes", fail="live")
    hooks("20-board", "board")

    code, out, err = run_cli([*RETITLE, "-y"])

    assert code == 1
    assert "hook notes" in err and "FAILED (exit 3): notes store is locked" in err
    assert "hook board: moved notes/myproject-2" in out, "the remaining hooks still run"


def test_given_an_adoption_that_keeps_the_name_when_applied_then_no_hook_runs(cli_world, hooks):
    hooks("10-notes", "notes")

    code, out, err = run_cli(["ai", "adopt-session", "myproject-2", "-y"])

    assert code == 0, err
    assert hooks.calls() == []
    assert "hook notes" not in out
