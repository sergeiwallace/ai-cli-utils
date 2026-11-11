"""The original command finishes an interrupted rename without repeating completed work."""

from __future__ import annotations

import json

import pytest
import test_session_migrate
from test_session_migrate import NEW, OLD, UUID, _everything, _git, _migrate

from ai_cli import session_migrate as migration
from ai_cli.cc_migrate import cc_project_dir, transcript_title
from ai_cli.config import get_xdg_config_home

slot = test_session_migrate.slot


def test_given_a_completed_migration_when_original_command_repeats_then_nothing_changes(slot):
    assert _migrate(slot["repo"], "-y")[0] == 0
    before = _everything(slot)
    for _ in range(2):
        code, out, err = _migrate(slot["repo"], "-y")
        assert code == 0, out + err
        assert "worktree | done earlier" in out
        assert _everything(slot) == before


@pytest.mark.parametrize(
    "boundary",
    [
        "_record_migration",
        "relocate_venv",
        "rename_branch_anchored",
        "move_canonical_worktree",
        "_apply_memory",
        "_rewrite_transcript_cwd",
        "retitle_transcript",
        "neutralise_worktree_state",
        "_remove_stale_locks",
        "_apply_task_rekey",
        "_apply_state",
        "_apply_session_maps",
        "_apply_iterm2",
        "_apply_launch_logs",
        "_apply_chief",
        "_run_live_hooks",
        "_apply_beads",
    ],
)
def test_given_an_interruption_at_a_part_when_original_command_repeats_then_remaining_parts_finish(
    slot, monkeypatch, boundary
):
    venv = slot["wt"] / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "tool").write_text(f"#!{slot['wt']}/.venv/bin/python\n")
    real = getattr(migration, boundary)
    stopped = False

    def interrupt(*args, **kwargs):
        nonlocal stopped
        planning = kwargs.get("dry_run", False)
        if not planning and not stopped:
            stopped = True
            raise KeyboardInterrupt("simulated interruption")
        return real(*args, **kwargs)

    with monkeypatch.context() as changes:
        changes.setattr(migration, boundary, interrupt)
        code, out, err = _migrate(slot["repo"], "-y")
    assert code != 0, out + err
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    wt = slot["repo"] / ".worktrees" / NEW
    assert _git("symbolic-ref", "--short", "HEAD", cwd=wt).strip() == f"wt-{NEW}"
    assert transcript_title(cc_project_dir(wt, slot["claude"]) / f"{UUID}.jsonl") == NEW
    assert str(slot["wt"]) not in (wt / ".venv" / "bin" / "tool").read_text(encoding="utf-8")
    assert not (slot["claude"] / "tasks" / OLD).exists()
    script = (slot["state"] / "sessions" / f"c-{NEW}.sh").read_text(encoding="utf-8")
    assert f"ai_name={NEW}" in script
    assert f"ai_name={OLD}" not in script
    before = _everything(slot)
    assert _migrate(slot["repo"], "-y")[0] == 0
    assert _everything(slot) == before


def test_given_a_hook_failing_then_passing_when_original_command_repeats_then_it_finishes(slot, tmp_path):
    hook = tmp_path / "notes.py"
    pending, fail = tmp_path / "pending", tmp_path / "fail"
    pending.write_text("notes")
    fail.write_text("locked")
    hook.write_text(
        "import pathlib, sys\n"
        f"pending = pathlib.Path({str(pending)!r})\nfail = pathlib.Path({str(fail)!r})\n"
        "if '--dry-run' in sys.argv:\n"
        "    print('migrate-status: pending' if pending.exists() else 'migrate-status: done')\n"
        "elif pending.exists():\n"
        "    if fail.exists():\n        sys.exit(3)\n"
        "    pending.unlink()\n"
    )
    directory = get_xdg_config_home() / "migrate-session.d"
    directory.mkdir(parents=True)
    (directory / "notes.toml").write_text(f'name = "notes"\nargv = [{json.dumps(str(hook))}]\n')
    assert _migrate(slot["repo"], "-y")[0] == 1
    fail.unlink()
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    assert not pending.exists()
    before = _everything(slot)
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    assert "hook notes | done earlier" in out
    assert _everything(slot) == before


def test_given_an_unrelated_destination_when_migrating_then_refused_without_changes(slot):
    _git("worktree", "add", "-q", str(slot["repo"] / ".worktrees" / NEW), "-b", "unrelated", cwd=slot["repo"])
    before = _everything(slot)
    for flag in ("-n", "-y"):
        code, out, err = _migrate(slot["repo"], flag)
        assert code == 2, out + err
        assert "failed:" in out + err
        assert _everything(slot) == before


def test_given_a_live_session_when_original_command_repeats_then_refused(slot):
    assert _migrate(slot["repo"], "-y")[0] == 0
    before = _everything(slot)
    code, out, err = _migrate(slot["repo"], "-y", tmux=[f"c-{NEW}"])
    assert code == 2, out + err
    assert _everything(slot) == before


def test_given_no_migration_evidence_when_only_destination_exists_then_refused(slot):
    _git("worktree", "move", str(slot["wt"]), str(slot["repo"] / ".worktrees" / NEW), cwd=slot["repo"])
    _git("branch", "-m", f"wt-{OLD}", "unrelated", cwd=slot["repo"])
    before = _everything(slot)
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 2, out + err
    assert _everything(slot) == before
