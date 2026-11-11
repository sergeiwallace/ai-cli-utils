"""A run killed right after the worktree move finishes every part on the next command.

The worktree move is the point after which a fresh migration can no longer start: the
old slot is gone. Each test kills ``ai migrate-session OLD NEW`` there, then runs either
the same command again, ``--finish``, or the launch-time finish ``ai c NEW`` runs, and checks every part the slot carries.
"""

from __future__ import annotations

import json

import pytest
import test_session_migrate
from test_session_migrate import NEW, OLD, OUTBOX_KEYED, UUID, _everything, _git, _migrate
from test_session_migrate_on_launch import _launch

from ai_cli import session_migrate as migration
from ai_cli.canonical_worktrees import get_canonical_worktree_registry_path
from ai_cli.cc_migrate import cc_project_dir, transcript_title

slot = test_session_migrate.slot


def _kill_after_move(slot, monkeypatch, stop: BaseException) -> None:
    move = migration.move_worktree

    def moved_then_killed(*args, **kwargs):
        assert move(*args, **kwargs)[0]
        raise stop

    venv = slot["wt"] / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "tool").write_text(f"#!{slot['wt']}/.venv/bin/python\n")
    with monkeypatch.context() as changes:
        changes.setattr(migration, "move_worktree", moved_then_killed)
        code, out, err = _migrate(slot["repo"], "-y")
    assert code != 0, out + err
    assert not slot["wt"].exists()
    assert (slot["repo"] / ".worktrees" / NEW).is_dir()


def _assert_no_part_left(out: str) -> None:
    statuses = [line.split(" | ", 1)[1] for line in out.splitlines() if " | " in line and "part | status" not in line]
    assert statuses, out
    assert not [s for s in statuses if s.startswith(("failed", "would do"))], out


def _assert_every_part_done(slot) -> None:
    repo, old_wt, claude, state = slot["repo"], slot["wt"], slot["claude"], slot["state"]
    new_wt = repo / ".worktrees" / NEW

    assert _git("symbolic-ref", "--short", "HEAD", cwd=new_wt).strip() == f"wt-{NEW}"
    markers = _git("config", "--get-regexp", r"hub-session-home$", cwd=repo)
    assert f"branch.wt-{NEW}.hub-session-home {new_wt}" in markers
    assert f"branch.wt/{OLD}/task.hub-session-home {new_wt}" in markers
    entries = json.loads(get_canonical_worktree_registry_path().read_text(encoding="utf-8"))["worktrees"]
    assert [(e["path"], e["session_name"]) for e in entries] == [(str(new_wt), f"c-{NEW}")]
    assert str(old_wt) not in (new_wt / ".venv" / "bin" / "tool").read_text(encoding="utf-8")

    new_project = cc_project_dir(new_wt, claude)
    assert not slot["project"].exists()
    transcript = new_project / f"{UUID}.jsonl"
    assert transcript_title(transcript) == NEW
    records = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
    assert records[0]["cwd"] == str(new_wt)
    assert records[2]["worktreeSession"] is None
    assert records[3]["cwd"] == str(new_wt / "src")
    assert (new_project / "memory" / "MEMORY.md").is_file()

    assert sorted(p.name for p in (claude / "tasks" / NEW).glob("*.json")) == ["1.json", "2.json"]
    assert not list((claude / "tasks" / OLD).glob("*.json"))
    assert (claude / "resume" / "outbox" / OUTBOX_KEYED.replace(OLD, NEW, 1)).is_file()
    assert not (claude / "resume" / "outbox" / OUTBOX_KEYED).exists()

    meta = json.loads((state / f"session-meta-c-{NEW}.json").read_text(encoding="utf-8"))
    assert (meta["ai_name"], meta["session"], meta["worktree_dir"]) == (NEW, f"c-{NEW}", str(new_wt))
    assert not (state / f"session-meta-c-{OLD}.json").exists()
    script = (state / "sessions" / f"c-{NEW}.sh").read_text(encoding="utf-8")
    assert f"ai_name={NEW}" in script
    assert f"ai_name={OLD}" not in script
    assert not (state / "sessions" / f"c-{OLD}.sh").exists()
    assert (state / f"cc-resume-prompt-c-{NEW}").is_file()
    assert json.loads((claude / "cc-sessions.json").read_text(encoding="utf-8")) == {NEW: UUID, "other-1": "x"}
    record = json.loads((state / "iterm2-sessions.json").read_text(encoding="utf-8"))["sessions"][0]
    assert (record["name"], record["boot_recipe"]["cwd"]) == (f"c-{NEW}", str(new_wt))
    assert not (state / f"ai-watcher-lock-c-{OLD}").exists()


@pytest.mark.parametrize(
    "stop",
    [KeyboardInterrupt("interrupted after the move"), SystemExit(137)],
    ids=["interrupt", "hard-kill"],
)
@pytest.mark.parametrize("resume", ["same-command", "finish", "launch"])
def test_given_a_run_killed_after_the_worktree_move_when_resumed_then_every_part_is_done(
    slot, monkeypatch, stop, resume
):
    _kill_after_move(slot, monkeypatch, stop)

    if resume == "launch":
        code, out, err, launched = _launch(slot)
        assert [entry["ai_name"] for entry in launched] == [NEW], out + err
        assert f"finished the migration {OLD} -> {NEW}" in out + err
    else:
        code, out, err = _migrate(slot["repo"], *(("--finish", "-y") if resume == "finish" else ("-y",)))
        assert code == 0, out + err
        _assert_no_part_left(out)

    _assert_every_part_done(slot)
    before = _everything(slot)
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    assert _everything(slot) == before
