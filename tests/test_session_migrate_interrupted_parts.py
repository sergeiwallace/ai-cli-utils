"""Interruption inside an artifact step must preserve intent and avoid duplicate work."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_session_migrate
from test_session_migrate import NEW, OLD, _everything, _git, _migrate

slot = test_session_migrate.slot


@pytest.mark.parametrize("running", ["target tmux", "nested target process"])
def test_given_a_running_target_when_migrating_then_it_is_refused_without_changes(slot, monkeypatch, running):
    from ai_cli import session_migrate as migration

    if running == "nested target process":
        assert _migrate(slot["repo"], "-y")[0] == 0
        monkeypatch.setattr(
            migration,
            "live_sessions",
            lambda *a: [
                SimpleNamespace(name="other-session", pid=12345, cwd=str(slot["repo"] / ".worktrees" / NEW / "src"))
            ],
        )
    before = _everything(slot)
    code, out, err = _migrate(slot["repo"], "-y", tmux=[f"c-{NEW}"] if running == "target tmux" else [])
    assert code == 2, out + err
    assert _everything(slot) == before


def test_given_a_topic_branch_and_move_before_record_when_retried_then_saved_intent_proves_pair(slot, monkeypatch):
    from ai_cli import session_migrate as migration

    _git("switch", "-q", "-c", "topic", cwd=slot["wt"])
    move = migration.move_worktree
    with monkeypatch.context() as changes:

        def interrupt(*args, **kwargs):
            assert move(*args, **kwargs)[0]
            raise KeyboardInterrupt("moved before returning to the runner")

        changes.setattr(migration, "move_worktree", interrupt)
        assert _migrate(slot["repo"], "-y")[0] == 1
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    new_path = slot["repo"] / ".worktrees" / NEW
    assert _git("symbolic-ref", "--short", "HEAD", cwd=new_path).strip() == "topic"


def test_given_an_explicit_finish_when_complete_then_the_same_parts_table_is_printed(slot):
    assert _migrate(slot["repo"], "-y")[0] == 0
    code, out, err = _migrate(slot["repo"], "--finish", "-y")
    assert code == 0, out + err
    assert "part | status" in out
    assert "worktree | done earlier" in out


def test_given_an_existing_target_namespace_when_stopped_before_tasks_then_retry_uses_original_mapping(
    slot, monkeypatch
):
    from ai_cli import session_migrate as migration

    dest = slot["claude"] / "tasks" / NEW
    dest.mkdir()
    (dest / "1.json").write_text(json.dumps({"id": "1", "subject": "unrelated"}))
    with monkeypatch.context() as changes:
        changes.setattr(migration, "_apply_task_rekey", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
        assert _migrate(slot["repo"], "-y")[0] == 1
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    assert len(list(dest.glob("*.json"))) == 3


def test_given_migrated_tasks_updated_later_when_retried_then_completed_tasks_are_not_overwritten(slot):
    assert _migrate(slot["repo"], "-y")[0] == 0
    target = slot["claude"] / "tasks" / NEW / "1.json"
    task = json.loads(target.read_text())
    task["status"] = "completed"
    target.write_text(json.dumps(task))
    before = _everything(slot)
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    assert _everything(slot) == before


def test_given_an_incomplete_transcript_line_when_original_command_repeats_then_it_is_preserved(slot):
    from ai_cli.cc_migrate import cc_project_dir

    assert _migrate(slot["repo"], "-y")[0] == 0
    transcript = next(cc_project_dir(slot["repo"] / ".worktrees" / NEW, slot["claude"]).glob("*.jsonl"))
    with transcript.open("a", encoding="utf-8") as out:
        out.write('{"type":"assistant","message":"incomplete')
    before = _everything(slot)
    for flag in ("-n", "-y"):
        code, out, err = _migrate(slot["repo"], flag)
        assert code == 0, out + err
        assert _everything(slot) == before


def test_given_an_unreadable_transcript_when_previewed_then_refusal_has_a_parts_table(slot, monkeypatch):
    from ai_cli.cc_migrate import cc_project_dir

    assert _migrate(slot["repo"], "-y")[0] == 0
    transcript = next(cc_project_dir(slot["repo"] / ".worktrees" / NEW, slot["claude"]).glob("*.jsonl"))
    real = Path.open

    def unreadable(path, *args, **kwargs):
        if path == transcript:
            raise PermissionError("transcript is unreadable")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", unreadable)
    code, out, err = _migrate(slot["repo"], "-n")
    assert code == 2, out + err
    assert "part | status" in out
    assert "transcript is unreadable" in out + err


def test_given_a_task_copied_before_interruption_when_retried_then_no_duplicate_or_remapped_dependency(
    slot, monkeypatch
):
    source = slot["claude"] / "tasks" / OLD / "1.json"
    task2 = source.with_name("2.json")
    task2.write_text(json.dumps({"id": "2", "subject": "dependent", "blocks": ["1"]}))
    dest = slot["claude"] / "tasks" / NEW
    dest.mkdir()
    (dest / "1.json").write_text(json.dumps({"id": "1", "subject": "unrelated"}))
    real = Path.unlink
    with monkeypatch.context() as changes:

        def interrupt(path, *args, **kwargs):
            if path == source:
                raise KeyboardInterrupt("copied task, source remains")
            return real(path, *args, **kwargs)

        changes.setattr(Path, "unlink", interrupt)
        assert _migrate(slot["repo"], "-y")[0] == 1
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    tasks = {p.stem: json.loads(p.read_text()) for p in dest.glob("*.json")}
    assert len(tasks) == 3
    assert tasks["2"]["blocks"] == ["3"]
    assert tasks["1"]["subject"] == "unrelated"
    before = _everything(slot)
    assert _migrate(slot["repo"], "-y")[0] == 0
    assert _everything(slot) == before


def test_given_memory_renamed_before_index_rewrite_when_retried_then_only_index_is_finished(slot, monkeypatch):
    memory = slot["project"] / "memory"
    old_name, new_name = f"active-work-{OLD}.md", f"active-work-{NEW}.md"
    (memory / old_name).write_text("verbatim body")
    index = memory / "MEMORY.md"
    index.write_text(f"[{old_name}]({old_name})\n[[{old_name[:-3]}]]\n")
    real = Path.replace
    with monkeypatch.context() as changes:

        def interrupt(path, target):
            if Path(target) == index:
                raise KeyboardInterrupt("memory already renamed")
            return real(path, target)

        changes.setattr(Path, "replace", interrupt)
        assert _migrate(slot["repo"], "-y")[0] == 1
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    from ai_cli.cc_migrate import cc_project_dir

    moved = cc_project_dir(slot["repo"] / ".worktrees" / NEW, slot["claude"]) / "memory"
    assert old_name not in (moved / "MEMORY.md").read_text()
    assert new_name in (moved / "MEMORY.md").read_text()
    assert (moved / new_name).read_text() == "verbatim body"


def test_given_metadata_copied_before_source_removal_when_retried_then_metadata_is_finished(slot, monkeypatch):
    source = slot["state"] / f"session-meta-c-{OLD}.json"
    real = Path.unlink
    with monkeypatch.context() as changes:

        def interrupt(path, *args, **kwargs):
            if path == source:
                raise KeyboardInterrupt("metadata copied")
            return real(path, *args, **kwargs)

        changes.setattr(Path, "unlink", interrupt)
        assert _migrate(slot["repo"], "-y")[0] == 1
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    assert not source.exists()
    script = (slot["state"] / "sessions" / f"c-{NEW}.sh").read_text(encoding="utf-8")
    assert f"ai_name={NEW}" in script and f"ai_name={OLD}" not in script


def test_given_a_flat_owned_task_worktree_when_migrated_then_path_and_branch_follow(slot):
    old_path = slot["repo"] / ".worktrees" / f"{OLD}-task-build"
    new_path = old_path.with_name(f"{NEW}-task-build")
    _git("worktree", "add", "-q", str(old_path), "-b", f"wt/{OLD}/build", cwd=slot["repo"])
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    assert new_path.exists() and not old_path.exists()
    assert _git("symbolic-ref", "--short", "HEAD", cwd=new_path).strip() == f"wt/{NEW}/build"
    before = _everything(slot)
    assert _migrate(slot["repo"], "-y")[0] == 0
    assert _everything(slot) == before


def test_given_an_agent_moved_before_branch_rename_when_retried_then_ownership_follows(slot, monkeypatch):
    from test_session_migrate_agents import _provenance

    from ai_cli import session_migrate_agents as agents

    old_path = slot["repo"] / ".worktrees" / f"{OLD}-agents" / "build"
    new_path = slot["repo"] / ".worktrees" / f"{NEW}-agents" / "build"
    _git("worktree", "add", "-q", str(old_path), "-b", f"wt/{OLD}/build", cwd=slot["repo"])
    _provenance(old_path).write_text(json.dumps({"owner_session": OLD, "preserved": "value"}))
    _git("config", f"branch.wt/{OLD}/build.hub-agent-worktree", str(old_path), cwd=slot["repo"])
    with monkeypatch.context() as changes:
        changes.setattr(
            agents, "rename_branch_anchored", lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt())
        )
        assert _migrate(slot["repo"], "-y")[0] == 1
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    assert json.loads(_provenance(new_path).read_text()) == {"owner_session": NEW, "preserved": "value"}
    assert _git("config", "--get", f"branch.wt/{NEW}/build.hub-agent-worktree", cwd=slot["repo"]).strip() == str(
        new_path
    )


@pytest.mark.parametrize("layout", ["container", "flat"])
def test_given_an_agent_renamed_before_ownership_when_retried_then_all_markers_finish(slot, monkeypatch, layout):
    from test_session_migrate_agents import _provenance

    from ai_cli import session_migrate_agents as agents

    if layout == "container":
        old_path = slot["repo"] / ".worktrees" / f"{OLD}-agents" / "build"
        new_path = slot["repo"] / ".worktrees" / f"{NEW}-agents" / "build"
    else:
        old_path = slot["repo"] / ".worktrees" / f"{OLD}-task-build"
        new_path = slot["repo"] / ".worktrees" / f"{NEW}-task-build"
    _git("worktree", "add", "-q", str(old_path), "-b", f"wt/{OLD}/build", cwd=slot["repo"])
    _provenance(old_path).write_text(json.dumps({"owner_session": OLD}))
    for marker in ("agent-worktree", "session-home"):
        _git("config", f"branch.wt/{OLD}/build.hub-{marker}", str(old_path), cwd=slot["repo"])
    real = agents.apply_worktree_ownership
    with monkeypatch.context() as changes:

        def interrupt(plan, branch):
            if plan.dest == new_path:
                raise KeyboardInterrupt("branch already renamed")
            return real(plan, branch)

        changes.setattr(agents, "apply_worktree_ownership", interrupt)
        assert _migrate(slot["repo"], "-y")[0] == 1
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    assert json.loads(_provenance(new_path).read_text())["owner_session"] == NEW
    for marker in ("agent-worktree", "session-home"):
        assert _git("config", "--get", f"branch.wt/{NEW}/build.hub-{marker}", cwd=slot["repo"]).strip() == str(new_path)
    before = _everything(slot)
    assert _migrate(slot["repo"], "-y")[0] == 0
    assert _everything(slot) == before


@pytest.mark.parametrize("stopped", [False, True])
def test_given_an_agent_virtualenv_when_moved_or_resumed_then_its_scripts_follow_the_path(slot, monkeypatch, stopped):
    from ai_cli import session_migrate_agents as agents

    old_path = slot["repo"] / ".worktrees" / f"{OLD}-task-build"
    new_path = slot["repo"] / ".worktrees" / f"{NEW}-task-build"
    _git("worktree", "add", "-q", str(old_path), "-b", f"wt/{OLD}/build", cwd=slot["repo"])
    scripts = old_path / ".venv" / "bin"
    scripts.mkdir(parents=True)
    (scripts / "tool").write_text(f"#!{old_path}/.venv/bin/python\n")
    if stopped:
        move = agents.move_worktree
        with monkeypatch.context() as changes:

            def interrupt(*args, **kwargs):
                assert move(*args, **kwargs)[0]
                raise KeyboardInterrupt("agent moved before its environment")

            changes.setattr(agents, "move_worktree", interrupt)
            assert _migrate(slot["repo"], "-y")[0] == 1
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    assert (new_path / ".venv" / "bin" / "tool").read_text() == f"#!{new_path}/.venv/bin/python\n"
    before = _everything(slot)
    assert _migrate(slot["repo"], "-y")[0] == 0
    assert _everything(slot) == before


def test_given_an_interruption_before_tasks_when_reported_then_pending_tasks_are_not_done_now(slot, monkeypatch):
    from ai_cli import session_migrate as migration

    with monkeypatch.context() as changes:
        changes.setattr(migration, "_apply_task_rekey", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
        code, out, err = _migrate(slot["repo"], "-y")
    assert code == 1
    assert "task namespace | done now" not in out
    assert "task namespace | failed:" in out


def test_given_chief_registration_written_before_registry_when_retried_then_registry_finishes(slot, monkeypatch):
    from test_session_migrate import _assert_chief_names_new, _seed_chief

    from ai_cli import chief_of_staff as chief

    records = _seed_chief()
    real = chief._write_private_json
    with monkeypatch.context() as changes:

        def interrupt(path, data):
            if path == records["registry"]:
                raise KeyboardInterrupt("chief registration already moved")
            return real(path, data)

        changes.setattr(chief, "_write_private_json", interrupt)
        assert _migrate(slot["repo"], "-y")[0] == 1
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    _assert_chief_names_new(records)
