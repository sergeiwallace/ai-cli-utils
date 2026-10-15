"""``ai session-migrate OLD NEW`` moves a registered session to a new name, losing nothing.

Every test drives the real command against a fake slot in a temp HOME that carries
each artifact the session's name or worktree path keys: the worktree and its branch,
the session-home markers, the canonical registry entry, the transcript (title, cwd,
worktree binding, sidecar, memory), the task namespace, the resume tree, the
launcher's metadata, wrapper script, resume prompt and session map, and the iTerm2
restore record.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import run_cli, snapshot_tree

from ai_cli import session_registry
from ai_cli.canonical_worktrees import get_canonical_worktree_registry_path, register_canonical_worktree
from ai_cli.cc_migrate import cc_project_dir, transcript_title
from ai_cli.config import get_xdg_config_home, get_xdg_state_home

OLD, NEW = "myrepo-3", "myrepo-box-3"
UUID = "3a3a3a3a-0000-4000-8000-000000000003"
OUTBOX_KEYED = f"{OLD}-1791218749.md"
CONFIRMED_KEYED = f"{OLD}-{'ab' * 16}.md"


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def _jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def _dead_pid() -> int:
    """The pid of a process that has already exited."""
    child = subprocess.Popen([sys.executable, "-c", ""])
    child.wait()
    return child.pid


@pytest.fixture
def slot(tmp_path):
    """A repo whose slot ``.worktrees/myrepo-3`` carries every artifact a migration moves."""
    home = Path.home()
    claude = home / ".claude"
    state = get_xdg_state_home()

    repo = tmp_path / "myrepo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "T", cwd=repo)
    (repo / "README.md").write_text("hi\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "init", cwd=repo)
    repo = repo.resolve()
    wt = repo / ".worktrees" / OLD
    _git("worktree", "add", "-q", str(wt), "-b", f"wt-{OLD}", cwd=repo)
    (wt / "uncommitted.txt").write_text("work in progress\n")
    _git("config", f"branch.wt-{OLD}.aih-session-home", str(wt), cwd=repo)
    _git("config", f"branch.wt/{OLD}/task.aih-session-home", str(wt), cwd=repo)
    register_canonical_worktree(wt, engine="c", session_name=f"c-{OLD}")

    project = cc_project_dir(wt, claude)
    _jsonl(
        project / f"{UUID}.jsonl",
        [
            {"type": "user", "cwd": str(wt), "message": {"content": f"look at {wt}/README.md"}},
            {"type": "custom-title", "customTitle": OLD},
            {
                "type": "worktree-state",
                "worktreeSession": {"originalCwd": str(wt), "worktreePath": str(wt / "x"), "worktreeBranch": "b"},
            },
            {"type": "assistant", "cwd": str(wt / "src")},
        ],
    )
    _jsonl(project / UUID / "subagents" / "agent-1.jsonl", [{"cwd": str(wt)}])
    (project / "memory").mkdir()
    (project / "memory" / "MEMORY.md").write_text("# slot memory\n")

    tasks = claude / "tasks" / OLD
    tasks.mkdir(parents=True)
    for task_id in (1, 2):
        (tasks / f"{task_id}.json").write_text(json.dumps({"id": str(task_id), "subject": f"task {task_id}"}))
    (tasks / ".highwatermark").write_text("7")

    resume = claude / "resume"
    for sub, name in (("outbox", OUTBOX_KEYED), ("confirmed", CONFIRMED_KEYED)):
        (resume / sub).mkdir(parents=True, exist_ok=True)
        (resume / sub / name).write_text("hand-off\n")

    meta = {
        "engine": "c",
        "ai_name": OLD,
        "session": f"c-{OLD}",
        "prefix": "c-myrepo-",
        "project_prefix": "myrepo",
        "session_id_uuid": "",
        "worktree_dir": str(wt),
        "project_name": "myrepo",
    }
    (state / "sessions").mkdir(parents=True, exist_ok=True)
    (state / f"session-meta-c-{OLD}.json").write_text(json.dumps(meta))
    (state / "sessions" / f"c-{OLD}.sh").write_text(f"ai_name={OLD}\n")
    (state / f"cc-resume-prompt-c-{OLD}").write_text("resume me\n")
    (state / f"ai-watcher-lock-c-{OLD}").write_text(str(_dead_pid()))
    (claude / "cc-sessions.json").write_text(json.dumps({OLD: UUID, "other-1": "x"}))
    (state / "iterm2-sessions.json").write_text(
        json.dumps(
            {
                "schema": session_registry.SCHEMA_VERSION,
                "machine": "m",
                "sessions": [{"id": "r1", "kind": "local", "name": f"c-{OLD}", "relaunch_argv": ["ai", "c", "3"]}],
            }
        )
    )
    return {"repo": repo, "wt": wt, "claude": claude, "state": state, "project": project}


def _migrate(repo: Path, *flags: str, tmux: list[str] | None = None):
    # The agent-worktree scan reads every repo under projects_dir; keep it inside the fixture.
    with (
        patch("ai_cli.session._tmux_session_names", return_value=list(tmux or [])),
        patch("ai_cli.config._get_projects_dir", return_value=repo.parent),
    ):
        return run_cli(["ai", "session-migrate", OLD, NEW, "-r", str(repo), *flags])


def _everything(slot) -> dict:
    """A snapshot of every tree the migration may touch, for a nothing-changed check."""
    trees = {name: snapshot_tree(slot[name]) for name in ("repo", "claude", "state")}
    trees["git-config"] = (slot["repo"] / ".git" / "config").read_text(encoding="utf-8")
    trees["registry"] = get_canonical_worktree_registry_path().read_text(encoding="utf-8")
    return trees


def test_given_a_registered_slot_when_migrated_then_every_artifact_follows_the_new_name(slot):
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err

    repo, old_wt, claude, state = slot["repo"], slot["wt"], slot["claude"], slot["state"]
    new_wt = repo / ".worktrees" / NEW

    # worktree, uncommitted work, branch and markers
    assert not old_wt.exists()
    assert (new_wt / "uncommitted.txt").read_text(encoding="utf-8") == "work in progress\n"
    listed = _git("worktree", "list", "--porcelain", cwd=repo)
    # git prints worktree paths with forward slashes on every OS, Windows included.
    assert f"worktree {new_wt.as_posix()}\n" in listed
    assert f"worktree {old_wt.as_posix()}\n" not in listed
    assert _git("symbolic-ref", "--short", "HEAD", cwd=new_wt).strip() == f"wt-{NEW}"
    markers = _git("config", "--get-regexp", r"aih-session-home$", cwd=repo)
    assert f"branch.wt-{NEW}.aih-session-home {new_wt}" in markers
    assert f"branch.wt/{OLD}/task.aih-session-home {new_wt}" in markers
    assert str(old_wt) + "\n" not in markers

    # canonical registry
    entries = json.loads(get_canonical_worktree_registry_path().read_text(encoding="utf-8"))["worktrees"]
    assert [(e["path"], e["session_name"]) for e in entries] == [(str(new_wt), f"c-{NEW}")]

    # transcript: project dir, title, cwd, binding; history text untouched
    new_project = cc_project_dir(new_wt, claude)
    assert not slot["project"].exists()
    transcript = new_project / f"{UUID}.jsonl"
    assert transcript_title(transcript) == NEW
    records = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
    assert records[0]["cwd"] == str(new_wt)
    assert records[0]["message"]["content"] == f"look at {old_wt}/README.md"
    assert records[2]["worktreeSession"] is None
    assert records[3]["cwd"] == str(new_wt / "src")
    assert (new_project / UUID / "subagents" / "agent-1.jsonl").is_file()
    assert (new_project / "memory" / "MEMORY.md").read_text(encoding="utf-8") == "# slot memory\n"
    assert f"resolve probe: `ai c` finds {transcript}" in out

    # task namespace, keeping its high-water mark
    new_ns = claude / "tasks" / NEW
    assert sorted(p.name for p in new_ns.glob("*.json")) == ["1.json", "2.json"]
    assert (new_ns / ".highwatermark").read_text(encoding="utf-8") == "7"
    assert not list((claude / "tasks" / OLD).glob("*.json"))

    # resume tree
    assert (claude / "resume" / "outbox" / OUTBOX_KEYED.replace(OLD, NEW, 1)).is_file()
    assert (claude / "resume" / "confirmed" / CONFIRMED_KEYED.replace(OLD, NEW, 1)).is_file()
    assert not (claude / "resume" / "outbox" / OUTBOX_KEYED).exists()

    # launcher state: metadata, regenerated wrapper (pinned task list), resume prompt, maps
    meta = json.loads((state / f"session-meta-c-{NEW}.json").read_text(encoding="utf-8"))
    assert (meta["ai_name"], meta["session"], meta["worktree_dir"]) == (NEW, f"c-{NEW}", str(new_wt))
    script = (state / "sessions" / f"c-{NEW}.sh").read_text(encoding="utf-8")
    assert f"ai_name={NEW}" in script
    assert 'CLAUDE_CODE_TASK_LIST_ID="$ai_name"' in script
    assert str(new_wt) in script
    assert not (state / "sessions" / f"c-{OLD}.sh").exists()
    assert (state / f"cc-resume-prompt-c-{NEW}").read_text(encoding="utf-8") == "resume me\n"
    assert json.loads((claude / "cc-sessions.json").read_text(encoding="utf-8")) == {NEW: UUID, "other-1": "x"}
    record = json.loads((state / "iterm2-sessions.json").read_text(encoding="utf-8"))["sessions"][0]
    assert (record["name"], record["relaunch_argv"]) == (f"c-{NEW}", ["ai", "c", NEW])

    # a lock is never carried to the new name, and one whose pid is gone is removed
    assert not (state / f"ai-watcher-lock-c-{OLD}").exists()
    assert not (state / f"ai-watcher-lock-c-{NEW}").exists()


def test_given_a_dry_run_when_migrating_then_every_move_is_printed_and_nothing_changes(slot):
    before = _everything(slot)
    code, out, err = _migrate(slot["repo"], "--dry-run")
    assert code == 0, out + err
    assert _everything(slot) == before

    new_wt = slot["repo"] / ".worktrees" / NEW
    for expected in (
        f"worktree: {slot['wt']} -> {new_wt}",
        f"branch: wt-{OLD} -> wt-{NEW}",
        f"session-home marker: branch.wt/{OLD}/task.aih-session-home",
        "canonical registry:",
        f"project dir: {slot['project']} -> {cc_project_dir(new_wt, slot['claude'])}",
        f"transcript title: {UUID}.jsonl",
        "task: ",
        f"resume: outbox/{OUTBOX_KEYED} -> {OUTBOX_KEYED.replace(OLD, NEW, 1)}",
        f"session meta: {slot['state'] / f'session-meta-c-{OLD}.json'}",
        f"wrapper script: {slot['state'] / 'sessions' / f'c-{OLD}.sh'}",
        f"session state: {slot['state'] / f'cc-resume-prompt-c-{OLD}'}",
        "session map:",
        "iTerm2 restore record:",
        "watcher lock:",
    ):
        assert expected in out, f"{expected!r} missing from:\n{out}"


def test_given_the_session_is_running_when_migrating_then_it_is_refused_and_nothing_moves(slot):
    sessions = slot["claude"] / "sessions"
    sessions.mkdir()
    (sessions / f"{os.getpid()}.json").write_text(
        json.dumps({"pid": os.getpid(), "name": OLD, "cwd": str(slot["wt"]), "sessionId": UUID})
    )
    before = _everything(slot)
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 1
    assert "still running" in err
    assert _everything(slot) == before


def test_given_its_tmux_session_still_exists_when_migrating_then_it_is_refused(slot):
    before = _everything(slot)
    code, _out, err = _migrate(slot["repo"], "-y", tmux=[f"c-{OLD}"])
    assert code == 1
    assert f"tmux session c-{OLD} still hosts {OLD}" in err
    assert _everything(slot) == before


def test_given_the_new_slot_exists_when_migrating_then_it_is_refused(slot):
    (slot["repo"] / ".worktrees" / NEW).mkdir()
    code, _out, err = _migrate(slot["repo"], "-y")
    assert code == 1
    assert "already exists" in err
    assert slot["wt"].is_dir()


def test_given_an_unregistered_slot_when_migrating_then_it_points_at_session_adopt(slot, tmp_path):
    code, _out, err = run_cli(["ai", "session-migrate", "myrepo-9", NEW, "-r", str(slot["repo"]), "-y"])
    assert code == 1
    assert "not a registered worktree" in err and "session-adopt" in err


def test_given_a_new_name_that_is_not_a_session_name_when_migrating_then_it_is_refused(slot):
    code, _out, err = run_cli(["ai", "session-migrate", OLD, "My Repo", "-r", str(slot["repo"]), "-y"])
    assert code == 1
    assert "is not a session name" in err


def test_given_a_dry_run_or_a_refusal_when_migrating_then_every_line_carries_the_migrate_tag(slot):
    code, out, err = _migrate(slot["repo"], "--dry-run")
    assert code == 0, out + err
    lines = [line for line in (out + err).splitlines() if line.strip()]
    assert lines
    assert all("[migrate]" in line for line in lines), out + err

    code, out, err = run_cli(["ai", "session-migrate", "myrepo-9", NEW, "-r", str(slot["repo"]), "-y"])
    assert code == 1
    assert "[migrate]" in err and "[adopt]" not in out + err


def test_given_either_verb_when_help_is_printed_then_it_names_the_other_verb():
    code, out, _err = run_cli(["ai", "session-migrate", "--help"])
    assert code == 0 and "session-adopt" in out
    code, out, _err = run_cli(["ai", "session-adopt", "--help"])
    assert code == 0 and "session-migrate" in out


ANCHOR = f"refs/preserved/session-migrate/wt-{OLD}"


def _ref(repo: Path, ref: str) -> str | None:
    res = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref], cwd=repo, capture_output=True, text=True, check=False
    )
    return res.stdout.strip() if res.returncode == 0 else None


@pytest.fixture
def topic_slot(slot):
    """The slot with a topic branch checked out, so ``wt-<old>`` is parked."""
    _git("switch", "-q", "-c", "topic", cwd=slot["wt"])
    _git("commit", "-q", "--allow-empty", "-m", "topic work", cwd=slot["wt"])
    return slot


def test_given_a_parked_canonical_branch_when_migrated_then_it_is_renamed_through_an_anchor(topic_slot):
    repo = topic_slot["repo"]
    tip = _ref(repo, f"refs/heads/wt-{OLD}")
    code, out, err = _migrate(repo, "--dry-run")
    assert code == 0, out + err
    assert f"branch: wt-{OLD} -> wt-{NEW}" in out
    assert f"branch anchor: {ANCHOR} (write: {tip})" in out

    code, out, err = _migrate(repo, "-y")
    assert code == 0, out + err
    new_wt = repo / ".worktrees" / NEW
    assert _ref(repo, f"refs/heads/wt-{NEW}") == tip
    assert _ref(repo, ANCHOR) == tip
    assert _ref(repo, f"refs/heads/wt-{OLD}") is None
    assert _git("symbolic-ref", "--short", "HEAD", cwd=new_wt).strip() == "topic"
    markers = _git("config", "--get-regexp", r"aih-session-home$", cwd=repo)
    assert f"branch.wt-{NEW}.aih-session-home {new_wt}" in markers
    assert f"branch.wt-{OLD}." not in markers


def test_given_the_checked_out_canonical_branch_when_migrated_then_it_is_anchored_too(slot):
    tip = _ref(slot["repo"], f"refs/heads/wt-{OLD}")
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    assert _ref(slot["repo"], ANCHOR) == tip == _ref(slot["repo"], f"refs/heads/wt-{NEW}")


def test_given_the_new_canonical_branch_exists_when_migrated_then_the_parked_one_keeps_its_name(topic_slot):
    repo = topic_slot["repo"]
    _git("branch", f"wt-{NEW}", "main", cwd=repo)
    tip = _ref(repo, f"refs/heads/wt-{OLD}")
    code, out, err = _migrate(repo, "-y")
    assert code == 0, out + err
    assert f"branch wt-{NEW} already exists, so it keeps the name wt-{OLD}" in err
    assert _ref(repo, f"refs/heads/wt-{OLD}") == tip
    assert _ref(repo, ANCHOR) is None


def test_given_git_refuses_the_branch_rename_when_migrated_then_stderr_is_quoted_and_the_anchor_remains(topic_slot):
    repo = topic_slot["repo"]
    # A branch under wt-<new>/ makes `git branch -m ... wt-<new>` fail on a ref D/F conflict.
    _git("branch", f"wt-{NEW}/child", "main", cwd=repo)
    tip = _ref(repo, f"refs/heads/wt-{OLD}")
    code, out, err = _migrate(repo, "-y")
    assert code == 0, out + err
    assert f"branch rename to wt-{NEW} failed: git branch -m wt-{OLD} wt-{NEW} was refused: " in err
    assert f"the tip stays anchored at {ANCHOR}" in err
    assert _ref(repo, f"refs/heads/wt-{OLD}") == tip == _ref(repo, ANCHOR)
    markers = _git("config", "--get-regexp", r"aih-session-home$", cwd=repo)
    assert f"branch.wt-{OLD}.aih-session-home {repo / '.worktrees' / NEW}" in markers


HELD_BODY = b"# replay\r\nverbatim \xe2\x80\x94 bytes\n\x00tail"


@pytest.fixture
def held_slot(slot):
    """The slot plus held-output replay files in the main tree's and the slot's memory dirs."""
    main_memory = cc_project_dir(slot["repo"], slot["claude"]) / "memory"
    main_memory.mkdir(parents=True)
    (main_memory / f".held-output-{OLD}-01-review-queue.md").write_bytes(HELD_BODY)
    (main_memory / ".held-output-myrepo-30-01-other-session.md").write_bytes(b"not ours\n")
    (slot["project"] / "memory" / f".held-output-{OLD}-02-summary.md").write_bytes(HELD_BODY + b"2")
    slot["main_memory"] = main_memory
    return slot


def test_given_held_output_files_when_migrated_then_only_their_prefix_changes(held_slot):
    main_memory = held_slot["main_memory"]
    new_memory = cc_project_dir(held_slot["repo"] / ".worktrees" / NEW, held_slot["claude"]) / "memory"
    code, out, err = _migrate(held_slot["repo"], "--dry-run")
    assert code == 0, out + err
    old_main = main_memory / f".held-output-{OLD}-01-review-queue.md"
    new_main = main_memory / f".held-output-{NEW}-01-review-queue.md"
    assert f"held output: {old_main} -> {new_main}" in out
    old_slot = held_slot["project"] / "memory" / f".held-output-{OLD}-02-summary.md"
    assert f"held output: {old_slot} -> {new_memory / f'.held-output-{NEW}-02-summary.md'}" in out
    assert "other-session" not in out

    code, out, err = _migrate(held_slot["repo"], "-y")
    assert code == 0, out + err
    assert not old_main.exists()
    assert new_main.read_bytes() == HELD_BODY
    assert (new_memory / f".held-output-{NEW}-02-summary.md").read_bytes() == HELD_BODY + b"2"
    assert not list(new_memory.glob(f".held-output-{OLD}-*"))
    assert (main_memory / ".held-output-myrepo-30-01-other-session.md").read_bytes() == b"not ours\n"


def test_given_the_held_output_target_exists_when_migrated_then_both_files_are_left_alone(held_slot):
    main_memory = held_slot["main_memory"]
    old_main = main_memory / f".held-output-{OLD}-01-review-queue.md"
    new_main = main_memory / f".held-output-{NEW}-01-review-queue.md"
    new_main.write_bytes(b"belongs to the new name\n")
    code, out, err = _migrate(held_slot["repo"], "-y")
    assert code == 0, out + err
    assert f"held output: {old_main} -> {new_main} SKIPPED (target exists)" in out
    assert old_main.read_bytes() == HELD_BODY
    assert new_main.read_bytes() == b"belongs to the new name\n"
    new_memory = cc_project_dir(held_slot["repo"] / ".worktrees" / NEW, held_slot["claude"]) / "memory"
    assert (new_memory / f".held-output-{NEW}-02-summary.md").is_file()


def test_given_only_bookkeeping_is_left_in_the_old_task_namespace_when_migrated_then_it_is_removed(slot):
    old_ns, new_ns = slot["claude"] / "tasks" / OLD, slot["claude"] / "tasks" / NEW
    (old_ns / ".lock").write_text("")
    new_ns.mkdir()
    (new_ns / ".highwatermark").write_text("4")
    code, out, err = _migrate(slot["repo"], "--dry-run")
    assert code == 0, out + err
    assert f"task namespace: {old_ns} (removed after rekey)" in out

    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    assert not old_ns.exists()
    assert (new_ns / ".highwatermark").read_text(encoding="utf-8") == "7"
    assert sorted(p.name for p in new_ns.glob("*.json")) == ["1.json", "2.json"]


def test_given_another_entry_in_the_old_task_namespace_when_migrated_then_it_stays_and_is_named(slot):
    old_ns = slot["claude"] / "tasks" / OLD
    (old_ns / "notes.txt").write_text("keep me\n")
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    assert f"task namespace {old_ns} left in place: it still holds notes.txt" in err
    assert (old_ns / "notes.txt").read_text(encoding="utf-8") == "keep me\n"
    assert (old_ns / ".highwatermark").is_file()


def test_given_a_live_session_when_migrating_then_no_new_artifact_class_is_touched(held_slot, tmp_path):
    _git("switch", "-q", "-c", "topic", cwd=held_slot["wt"])
    (held_slot["claude"] / "tasks" / OLD / ".lock").write_text("")
    log = tmp_path / "hook-calls.jsonl"
    hooks = get_xdg_config_home() / "session-migrate.d"
    hooks.mkdir(parents=True)
    script = tmp_path / "record.py"
    script.write_text(f"open({str(log)!r}, 'a').write('called')\n")
    (hooks / "record.toml").write_text(f'name = "record"\nargv = [{json.dumps(str(script))}]\n')
    sessions = held_slot["claude"] / "sessions"
    sessions.mkdir()
    (sessions / f"{os.getpid()}.json").write_text(
        json.dumps({"pid": os.getpid(), "name": OLD, "cwd": str(held_slot["wt"]), "sessionId": UUID})
    )
    before = _everything(held_slot)
    refs = _git("for-each-ref", cwd=held_slot["repo"])
    code, _out, err = _migrate(held_slot["repo"], "-y")
    assert code == 1
    assert "still running" in err
    assert _everything(held_slot) == before
    assert _git("for-each-ref", cwd=held_slot["repo"]) == refs
    assert not log.exists()


def test_given_a_transcript_record_with_a_lone_surrogate_when_migrated_then_it_completes_and_keeps_the_text(slot):
    # Claude Code can persist half an emoji as an escaped lone surrogate (`\ud83d`); a
    # migration crashed re-serializing such a record, leaving the slot half-moved.
    transcript = slot["project"] / f"{UUID}.jsonl"
    with transcript.open("a", encoding="utf-8") as out:
        # json.dumps escapes the path, whose Windows backslashes are not valid JSON verbatim.
        out.write(
            '{"type":"user","cwd":' + json.dumps(str(slot["wt"])) + ',"message":{"content":"cut \\ud83d here"}}\n'
        )

    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err

    new_wt = slot["repo"] / ".worktrees" / NEW
    moved = cc_project_dir(new_wt, slot["claude"]) / f"{UUID}.jsonl"
    last = json.loads(moved.read_text(encoding="utf-8").splitlines()[-1])
    assert last["cwd"] == str(new_wt)
    assert last["message"]["content"] == "cut \ud83d here"
    assert not (slot["claude"] / "tasks" / OLD).exists()


# --- leftovers named for the old session (AI-CLI-96ja) -------------------------------------


def _leftovers(slot) -> dict:
    """The old-name artifacts two real migrations left behind, recreated on the fixture."""
    repo, claude = slot["repo"], slot["claude"]
    container = repo / ".worktrees" / f"{OLD}-agents"
    (container / "reaped-leaf").mkdir(parents=True)
    slot_lock = repo / ".worktrees" / f".{OLD}.lock"
    slot_lock.write_text("")
    memory = cc_project_dir(repo, claude) / "memory"
    memory.mkdir(parents=True, exist_ok=True)
    (memory / f"active-work-{OLD}.md").write_text("my state\n")
    (memory / f".resume-{OLD}-12.md").write_text("old resume\n")
    (claude / "tasks" / f"kg-{OLD}").mkdir(parents=True)
    (memory / f"active-work-kg-{OLD}.md").write_text("another session's state\n")
    (memory / "MEMORY.md").write_text(
        f"- **{OLD}** -- [state](active-work-{OLD}.md) and [[active-work-{OLD}]]\n"
        f"- **kg-{OLD}** -- [state](active-work-kg-{OLD}.md)\n"
    )
    return {"container": container, "slot_lock": slot_lock, "memory": memory}


def _assert_leftovers_carried(slot, left: dict) -> None:
    memory = left["memory"]
    assert not left["container"].exists()
    assert not left["slot_lock"].exists()
    assert not (memory / f"active-work-{OLD}.md").exists()
    assert (memory / f"active-work-{NEW}.md").read_text(encoding="utf-8") == "my state\n"
    assert (memory / f".resume-{NEW}-12.md").read_text(encoding="utf-8") == "old resume\n"
    assert (memory / f"active-work-kg-{OLD}.md").read_text(encoding="utf-8") == "another session's state\n"
    assert (memory / "MEMORY.md").read_text(encoding="utf-8") == (
        f"- **{OLD}** -- [state](active-work-{NEW}.md) and [[active-work-{NEW}]]\n"
        f"- **kg-{OLD}** -- [state](active-work-kg-{OLD}.md)\n"
    )
    assert not (slot["state"] / f"ai-watcher-lock-c-{OLD}").exists()


def test_given_old_name_leftovers_when_migrated_then_each_is_carried_or_removed_and_another_sessions_file_is_kept(
    slot,
):
    left = _leftovers(slot)

    code, out, err = _migrate(slot["repo"], "-y")

    assert code == 0, out + err
    _assert_leftovers_carried(slot, left)
    assert f"memory file: {left['memory'] / f'active-work-{OLD}.md'}" in out
    assert f"agent container: {left['container']}" in out


def test_given_old_name_leftovers_when_dry_run_then_each_is_planned_and_nothing_changes(slot):
    left = _leftovers(slot)
    before = _everything(slot)

    code, out, err = _migrate(slot["repo"], "--dry-run")

    assert code == 0, out + err
    assert _everything(slot) == before
    for expected in (
        f"agent container: {left['container']} (removed once empty)",
        f"slot lock: {left['slot_lock']} (removed: no live holder)",
        f"memory file: {left['memory'] / f'active-work-{OLD}.md'} -> ",
        f"memory index: {left['memory'] / 'MEMORY.md'} (rewrite: active-work-{OLD}.md)",
        f"watcher lock: {slot['state'] / f'ai-watcher-lock-c-{OLD}'} (removed: no live holder)",
    ):
        assert expected in out, f"{expected!r} missing from:\n{out}"


def test_given_a_watcher_lock_naming_a_live_pid_when_migrated_then_it_is_left_alone(slot):
    lock = slot["state"] / f"ai-watcher-lock-c-{OLD}"
    lock.write_text(str(os.getpid()))

    code, out, err = _migrate(slot["repo"], "-y")

    assert code == 0, out + err
    assert lock.read_text(encoding="utf-8") == str(os.getpid())
    assert f"its pid {os.getpid()} is running" in out


def test_given_a_slot_lock_a_process_holds_when_migrated_then_it_is_left_alone(slot):
    import portalocker

    lock = slot["repo"] / ".worktrees" / f".{OLD}.lock"
    with portalocker.Lock(str(lock), mode="a", timeout=0):
        code, out, err = _migrate(slot["repo"], "-y")
        assert code == 0, out + err
        assert lock.exists()
    assert "slot lock" in out and "left alone: a process holds it" in out


def _finish(repo: Path, *flags: str, tmux: list[str] | None = None):
    with (
        patch("ai_cli.session._tmux_session_names", return_value=list(tmux or [])),
        patch("ai_cli.config._get_projects_dir", return_value=repo.parent),
    ):
        return run_cli(["ai", "session-migrate", OLD, NEW, "-r", str(repo), "--finish", *flags])


def test_given_a_slot_migrated_before_these_steps_when_finished_then_every_leftover_is_carried_or_removed(slot):
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    left = _leftovers(slot)
    (slot["state"] / f"ai-watcher-lock-c-{OLD}").write_text(str(_dead_pid()))

    code, out, err = _finish(slot["repo"], "-y")

    assert code == 0, out + err
    _assert_leftovers_carried(slot, left)
    assert (slot["repo"] / ".worktrees" / NEW).is_dir()


def test_given_leftovers_when_finish_dry_runs_then_nothing_changes(slot):
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    _leftovers(slot)
    before = _everything(slot)

    code, out, err = _finish(slot["repo"], "--dry-run")

    assert code == 0, out + err
    assert _everything(slot) == before
    assert "memory file:" in out and "slot lock:" in out


def test_given_the_old_slot_is_still_registered_when_finished_then_it_is_refused(slot):
    before = _everything(slot)

    code, _out, err = _finish(slot["repo"], "-y")

    assert code == 1
    assert "migrate it without --finish first" in err
    assert _everything(slot) == before


def test_given_the_new_session_runs_in_tmux_when_finished_then_it_is_refused(slot):
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    left = _leftovers(slot)

    code, _out, err = _finish(slot["repo"], "-y", tmux=[f"c-{NEW}"])

    assert code == 1
    assert f"hosts {NEW}" in err
    assert left["slot_lock"].exists()


# A stand-in for ``bd``: it serves each store's issues from one JSON file and logs every call,
# both outside the stores, so a real Beads store is never read or written.
_FAKE_BD = """\
import json, os, sys, time
from pathlib import Path

db = Path(os.environ["FAKE_BD_DB"])
data = json.loads(db.read_text())
store = str(Path.cwd().resolve())
with open(os.environ["FAKE_BD_LOG"], "a") as log:
    log.write(json.dumps({"store": store, "argv": sys.argv[1:]}) + "\\n")
if data["fail"].get(store) == "error":
    sys.exit("Error: no beads database found")
if data["fail"].get(store) == "hang":
    time.sleep(20)
args = [a for a in sys.argv[1:] if a != "--sandbox"]
issues = data["stores"][store]


def opt(name):
    return args[args.index(name) + 1]


if args[0] == "list":
    # Looser than an exact match on purpose: the caller must check the assignee itself.
    print(json.dumps([i for i in issues if opt("--assignee") in (i.get("assignee") or "")]))
elif args[0] == "update":
    issue = next(i for i in issues if i["id"] == args[1])
    if issue["assignee"] != opt("--if-assignee"):
        sys.exit(13)
    issue["assignee"] = opt("--assignee")
elif args[:2] == ["comments", "add"]:
    next(i for i in issues if i["id"] == args[2]).setdefault("comments", []).append(" ".join(args[3:]))
db.write_text(json.dumps(data))
"""


@pytest.fixture
def stores(slot, tmp_path, monkeypatch):
    """Two Beads stores under the projects dir, the slot's repo and a workspace with no ``.worktrees/``."""
    workspace = tmp_path / "workspace"
    for store in (slot["repo"], workspace):
        (store / ".beads").mkdir(parents=True)
    fake = tmp_path / "fakebd"
    fake.mkdir()
    script = fake / "bd"
    script.write_text(f"#!{sys.executable}\n{_FAKE_BD}", encoding="utf-8")
    script.chmod(0o755)
    db = fake / "db.json"
    issues = {
        str(slot["repo"]): [
            {"id": "TST-1", "status": "open", "assignee": OLD},
            {"id": "TST-2", "status": "in_progress", "assignee": OLD},
            {"id": "TST-3", "status": "closed", "assignee": OLD},
            {"id": "TST-4", "status": "open", "assignee": f"{OLD}3"},
            {"id": "TST-5", "status": "open", "assignee": "other-1"},
        ],
        str(workspace.resolve()): [{"id": "WS-1", "status": "blocked", "assignee": OLD}],
    }
    db.write_text(json.dumps({"stores": issues, "fail": {}}), encoding="utf-8")
    monkeypatch.setenv("FAKE_BD_DB", str(db))
    monkeypatch.setenv("FAKE_BD_LOG", str(fake / "calls.jsonl"))
    monkeypatch.setenv("PATH", f"{fake}{os.pathsep}{os.environ['PATH']}")
    return {"repo": slot["repo"], "workspace": workspace.resolve(), "db": db, "log": fake / "calls.jsonl"}


def _assignees(stores) -> dict[str, str]:
    data = json.loads(stores["db"].read_text(encoding="utf-8"))
    return {i["id"]: i["assignee"] for store in data["stores"].values() for i in store}


def _bd_writes(stores) -> list[list[str]]:
    if not stores["log"].exists():
        return []
    calls = [json.loads(line)["argv"] for line in stores["log"].read_text(encoding="utf-8").splitlines()]
    return [argv for argv in calls if "list" not in argv]


def test_given_issues_assigned_to_old_when_migrated_then_new_holds_each_and_the_report_names_it(stores):
    code, out, err = _migrate(stores["repo"], "-y")

    assert code == 0, out + err
    held = _assignees(stores)
    assert (held["TST-1"], held["TST-2"], held["WS-1"]) == (NEW, NEW, NEW)
    for issue, store in (("TST-1", stores["repo"]), ("TST-2", stores["repo"]), ("WS-1", stores["workspace"])):
        assert f"beads issue: {issue} [{OLD}] ({store}) -> {NEW}" in out, out
    comments = json.loads(stores["db"].read_text(encoding="utf-8"))["stores"][str(stores["workspace"])][0]["comments"]
    assert comments == [f"Re-keyed from {OLD} to {NEW} by `ai session-migrate`."]
    for store in (stores["repo"], stores["workspace"]):
        assert f"beads store: {store} (publish needed" in out, out


def test_given_closed_and_similarly_named_issues_when_migrated_then_neither_is_rewritten(stores):
    code, out, err = _migrate(stores["repo"], "-y")

    assert code == 0, out + err
    held = _assignees(stores)
    assert (held["TST-3"], held["TST-4"], held["TST-5"]) == (OLD, f"{OLD}3", "other-1")
    touched = {argv[argv.index("update") + 1] for argv in _bd_writes(stores) if "update" in argv}
    assert touched == {"TST-1", "TST-2", "WS-1"}
    update = next(argv for argv in _bd_writes(stores) if "update" in argv)
    assert update == ["--sandbox", "update", "TST-1", "--assignee", NEW, "--if-assignee", OLD, "--force"]


def test_given_issues_assigned_to_old_when_dry_run_then_each_is_listed_per_store_and_nothing_is_written(stores):
    before = stores["db"].read_text(encoding="utf-8")

    code, out, err = _migrate(stores["repo"], "--dry-run")

    assert code == 0, out + err
    for issue, store in (("TST-1", stores["repo"]), ("TST-2", stores["repo"]), ("WS-1", stores["workspace"])):
        assert f"beads issue: {issue} [{OLD}] ({store}) -> {NEW}" in out, out
    assert "TST-3" not in out and "TST-4" not in out
    assert _bd_writes(stores) == []
    assert stores["db"].read_text(encoding="utf-8") == before


@pytest.mark.parametrize("failure", ["error", "hang"])
def test_given_an_unreadable_store_when_migrated_then_it_is_named_and_the_run_exits_1(stores, failure):
    data = json.loads(stores["db"].read_text(encoding="utf-8"))
    data["fail"][str(stores["workspace"])] = failure
    stores["db"].write_text(json.dumps(data), encoding="utf-8")

    with patch("ai_cli.session_migrate._BD_TIMEOUT", 1):
        code, out, err = _migrate(stores["repo"], "-y")

    assert code == 1, out + err
    assert f"beads store {stores['workspace']} FAILED to read" in err
    assert f"beads store: {stores['workspace']} (unreadable" in out
    assert _assignees(stores)["WS-1"] == OLD
    assert _assignees(stores)["TST-1"] == NEW


def test_given_a_store_unreadable_during_the_migration_when_finished_then_its_issues_are_re_keyed(stores):
    data = json.loads(stores["db"].read_text(encoding="utf-8"))
    data["fail"][str(stores["workspace"])] = "error"
    stores["db"].write_text(json.dumps(data), encoding="utf-8")
    code, out, err = _migrate(stores["repo"], "-y")
    assert code == 1, out + err
    data = json.loads(stores["db"].read_text(encoding="utf-8"))
    data["fail"] = {}
    stores["db"].write_text(json.dumps(data), encoding="utf-8")

    code, out, err = _finish(stores["repo"], "-y")

    assert code == 0, out + err
    assert _assignees(stores)["WS-1"] == NEW
    assert "session-migrations.jsonl records it" in out


def test_given_a_migration_from_before_the_re_key_when_finished_then_only_a_ledger_row_naming_the_pair_re_keys(
    stores, slot
):
    code, out, err = _migrate(stores["repo"], "-y")
    assert code == 0, out + err
    ledger = slot["state"] / "session-migrations.jsonl"
    ledger.unlink()
    data = json.loads(stores["db"].read_text(encoding="utf-8"))
    for store in data["stores"].values():
        for issue in store:
            if issue["assignee"] == NEW:
                issue["assignee"] = OLD
    stores["db"].write_text(json.dumps(data), encoding="utf-8")
    before = stores["db"].read_text(encoding="utf-8")

    # The session metadata and registry bind NEW to the slot but never name OLD: not evidence.
    assert (slot["state"] / f"session-meta-c-{NEW}.json").exists()
    code, out, err = _finish(stores["repo"], "-y")
    assert code == 0, out + err
    assert stores["db"].read_text(encoding="utf-8") == before
    assert f"beads issues on {OLD} were NOT re-keyed to {NEW}" in err

    # A row for another pair is not evidence either.
    ledger.write_text(json.dumps({"old": f"{OLD}x", "new": NEW}) + "\n", encoding="utf-8")
    code, out, err = _finish(stores["repo"], "-y")
    assert code == 0, out + err
    assert stores["db"].read_text(encoding="utf-8") == before

    with ledger.open("a", encoding="utf-8") as rows:
        rows.write(json.dumps({"old": OLD, "new": NEW, "source": "hand-written"}) + "\n")
    code, out, err = _finish(stores["repo"], "-y")

    assert code == 0, out + err
    held = _assignees(stores)
    assert (held["TST-1"], held["TST-2"], held["WS-1"], held["TST-3"]) == (NEW, NEW, NEW, OLD)
    assert "session-migrations.jsonl records it" in out


def test_given_no_record_of_the_migration_on_this_host_when_finished_then_nothing_is_re_keyed_and_it_says_why(
    stores, slot
):
    # Moved by hand: no ledger row, and the metadata and canonical registry still bind the old slot.
    _git("worktree", "move", str(slot["wt"]), str(stores["repo"] / ".worktrees" / NEW), cwd=stores["repo"])
    before = stores["db"].read_text(encoding="utf-8")

    code, out, err = _finish(stores["repo"], "-y")

    assert code == 0, out + err
    assert stores["db"].read_text(encoding="utf-8") == before
    assert _bd_writes(stores) == []
    reason = f"this host holds no record that {OLD} was migrated to {NEW} here"
    assert f"beads issue: TST-1 [{OLD}] ({stores['repo']}) (left alone: {reason}" in out, out
    assert f"beads issues on {OLD} were NOT re-keyed to {NEW}: {reason}" in err


def test_given_new_is_running_when_re_keyed_beads_only_then_only_the_issues_move(stores, slot):
    code, out, err = _migrate(stores["repo"], "-y")
    assert code == 0, out + err
    data = json.loads(stores["db"].read_text(encoding="utf-8"))
    for store in data["stores"].values():
        for issue in store:
            if issue["assignee"] == NEW:
                issue["assignee"] = OLD
    stores["db"].write_text(json.dumps(data), encoding="utf-8")
    sessions = slot["claude"] / "sessions"
    sessions.mkdir(exist_ok=True)
    (sessions / f"{os.getpid()}.json").write_text(
        json.dumps(
            {"pid": os.getpid(), "name": NEW, "cwd": str(stores["repo"] / ".worktrees" / NEW), "sessionId": UUID}
        )
    )
    code, out, err = _finish(stores["repo"], "-y")
    assert code == 1 and "still running" in err, out + err

    with patch("ai_cli.config._get_projects_dir", return_value=stores["repo"].parent):
        code, out, err = run_cli(["ai", "session-migrate", OLD, NEW, "-r", str(stores["repo"]), "--beads-only", "-y"])

    assert code == 0, out + err
    held = _assignees(stores)
    assert (held["TST-1"], held["TST-2"], held["WS-1"], held["TST-3"], held["TST-4"]) == (NEW, NEW, NEW, OLD, f"{OLD}3")
    assert "session-migrations.jsonl records it" in out


def test_given_no_ledger_row_when_re_keyed_beads_only_then_nothing_is_written(stores, slot):
    before = stores["db"].read_text(encoding="utf-8")
    with patch("ai_cli.config._get_projects_dir", return_value=stores["repo"].parent):
        code, out, err = run_cli(["ai", "session-migrate", OLD, NEW, "-r", str(stores["repo"]), "--beads-only", "-y"])
    assert code == 0, out + err
    assert stores["db"].read_text(encoding="utf-8") == before
    assert _bd_writes(stores) == []
    assert f"beads issues on {OLD} were NOT re-keyed to {NEW}" in err
