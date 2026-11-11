"""``ai migrate-session`` runs the external rekey hooks declared in ``migrate-session.d/``.

Each hook here is a small Python program that records how it was called (its argv
and the slot directories visible from its cwd) to a log outside every tree the
migration may touch, prints one line per artifact, and fails on request.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import test_session_migrate
from test_session_migrate import NEW, OLD, _assignees, _everything, _finish, _migrate

from ai_cli.config import get_xdg_config_home

slot = test_session_migrate.slot
stores = test_session_migrate.stores

HOOK = """\
import json, os, sys
args = sys.argv[1:]
with open({log!r}, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({{"hook": {name!r}, "args": args, "slots": sorted(os.listdir(".worktrees"))}}) + "\\n")
planning = "--dry-run" in args
print(("would move " if planning else "moved ") + "notes/" + args[args.index("--old") + 1])
if {fail!r} == ("plan" if planning else "live"):
    sys.stderr.write("notes store is locked\\n")
    sys.exit({code})
"""


@pytest.fixture
def hooks(tmp_path):
    """Declare hooks: ``add(filename, name, fail=None, code=3)``; calls land in ``calls()``."""
    directory = get_xdg_config_home() / "migrate-session.d"
    directory.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "hook-calls.jsonl"

    def add(filename: str, name: str, fail: str | None = None, program_dir: Path | None = None, code: int = 3) -> Path:
        script = (program_dir or tmp_path) / f"{filename}.py"
        script.write_text(HOOK.format(log=str(log), name=name, fail=fail, code=code), encoding="utf-8")
        (directory / f"{filename}.toml").write_text(
            f"name = {json.dumps(name)}\nargv = [{json.dumps(str(script))}]\n", encoding="utf-8"
        )
        return script

    def calls() -> list[dict]:
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    add.calls = calls  # type: ignore[attr-defined]
    add.directory = directory  # type: ignore[attr-defined]
    return add


def test_given_a_hook_when_dry_running_then_it_plans_with_dry_run_and_nothing_changes(slot, hooks):
    hooks("10-notes", "notes")
    before = _everything(slot)
    code, out, err = _migrate(slot["repo"], "--dry-run")
    assert code == 0, out + err
    assert f"hook notes: would move notes/{OLD}" in out
    assert [c["args"] for c in hooks.calls()] == [["--old", OLD, "--new", NEW, "--dry-run"]]
    assert _everything(slot) == before


def test_given_a_hook_when_migrated_then_it_runs_once_live_after_the_core_moves(slot, hooks):
    hooks("10-notes", "notes")
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    assert f"hook notes: moved notes/{OLD}" in out
    live = [c for c in hooks.calls() if "--dry-run" not in c["args"]]
    assert [c["args"] for c in live] == [["--old", OLD, "--new", NEW]]
    assert NEW in live[0]["slots"] and OLD not in live[0]["slots"]
    planned = [c for c in hooks.calls() if "--dry-run" in c["args"]]
    assert planned and all(OLD in c["slots"] for c in planned)


def test_given_a_hook_that_refuses_its_plan_when_migrating_then_nothing_moves(slot, hooks):
    hooks("10-notes", "notes", fail="plan")
    before = _everything(slot)
    code, _out, err = _migrate(slot["repo"], "-y")
    assert code == 2
    assert "hook notes" in err and "refused the migration (exit 3): notes store is locked" in err
    assert _everything(slot) == before
    assert all("--dry-run" in c["args"] for c in hooks.calls())


def test_given_a_hook_that_fails_live_when_migrated_then_the_rest_run_and_it_exits_one(slot, hooks):
    hooks("10-notes", "notes", fail="live")
    hooks("20-board", "board")
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 1
    assert "hook notes" in err and "FAILED (exit 3): notes store is locked" in err
    assert [c["hook"] for c in hooks.calls() if "--dry-run" not in c["args"]] == ["notes", "board"]
    assert f"hook board: moved notes/{OLD}" in out
    assert (slot["repo"] / ".worktrees" / NEW).is_dir()


def _ledger_finished(slot) -> list[dict]:
    path = slot["state"] / "session-migrations.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [row for row in rows if row.get("finished")]


def test_given_a_moved_slot_and_a_hook_refusing_its_plan_when_finished_then_the_rest_runs_and_it_is_pending(
    slot, stores, hooks
):
    data = json.loads(stores["db"].read_text(encoding="utf-8"))
    data["fail"][str(stores["workspace"])] = "error"
    stores["db"].write_text(json.dumps(data), encoding="utf-8")
    assert _migrate(slot["repo"], "-y")[0] == 1
    data = json.loads(stores["db"].read_text(encoding="utf-8"))
    data["fail"] = {}
    stores["db"].write_text(json.dumps(data), encoding="utf-8")
    leftover = slot["state"] / f"gg-reload-c-{OLD}"
    leftover.write_text("reload\n")
    hooks("10-board", "board", fail="plan", code=4)
    hooks("20-notes", "notes")

    code, out, err = _finish(slot["repo"], "-y")

    assert code == 1, out + err
    assert _assignees(stores)["WS-1"] == NEW
    assert not leftover.exists()
    assert (slot["state"] / f"gg-reload-c-{NEW}").read_text(encoding="utf-8") == "reload\n"
    assert "session state | done now" in out
    assert "beads issue | done now" in out
    live = [c["hook"] for c in hooks.calls() if "--dry-run" not in c["args"]]
    assert live == ["notes"]
    assert f"hook notes: moved notes/{OLD}" in out
    board = next(line.partition("hook board | ")[2] for line in out.splitlines() if "hook board | " in line)
    assert board.startswith("failed: ") and "pending" in board, board
    assert "refused its preflight (exit 4): notes store is locked" in board
    assert "hook board" in err and "NOT run" in err
    assert _ledger_finished(slot) == []


def test_given_a_hook_refusing_its_plan_with_exit_four_when_migrating_fresh_then_nothing_moves(slot, hooks):
    hooks("10-board", "board", fail="plan", code=4)
    hooks("20-notes", "notes")
    before = _everything(slot)

    code, _out, err = _migrate(slot["repo"], "-y")

    assert code == 2
    assert "hook board" in err and "refused the migration (exit 4): notes store is locked" in err
    assert _everything(slot) == before
    assert all("--dry-run" in c["args"] for c in hooks.calls())


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ('argv = ["prog"]\n', "'name'"),
        ('name = "notes"\n', "'argv'"),
        ('name = "notes"\nargv = "prog --flag"\n', "'argv'"),
        ('name = "notes"\nargv = ["prog", 3]\n', "'argv'"),
        ('name = "notes"\nargv = [\n', "TOML"),
    ],
)
def test_given_a_malformed_hook_file_when_migrating_then_it_is_refused_naming_file_and_field(slot, hooks, body, field):
    bad = hooks.directory / "bad.toml"
    bad.write_text(body, encoding="utf-8")
    before = _everything(slot)
    code, _out, err = _migrate(slot["repo"], "--dry-run")
    assert code == 2
    assert str(bad) in err and field in err
    assert _everything(slot) == before


def test_given_an_empty_hooks_dir_when_migrated_then_no_hook_line_appears(slot, hooks):
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    assert "hook " not in out + err
    assert (slot["repo"] / ".worktrees" / NEW).is_dir()


def test_given_a_hook_program_under_home_when_migrating_then_the_tilde_is_expanded(slot, hooks):
    script = hooks("10-notes", "notes", program_dir=Path.home())
    (hooks.directory / "10-notes.toml").write_text(f'name = "notes"\nargv = ["~/{script.name}"]\n', encoding="utf-8")
    code, out, err = _migrate(slot["repo"], "--dry-run")
    assert code == 0, out + err
    assert f"hook notes: would move notes/{OLD}" in out
