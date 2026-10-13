"""``ai session-migrate OLD NEW`` carries the session's agent worktrees and branches in every repo.

Each test drives the real command against a temp projects dir holding the slot's repo
and a second repo. The second repo carries what a delegating session leaves behind: an
agent worktree under ``.worktrees/<old>-agents/`` on ``wt/<old>/leaf-a``, a parked
``wt/<old>/leaf-b`` and a session-home marker naming the old slot.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import run_cli, snapshot_tree

from ai_cli.branch_rename import anchor_ref

OLD, NEW = "myrepo-3", "myrepo-box-3"


def _git(*args: str, cwd: Path, check: bool = True) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True).stdout


def _init(repo: Path) -> Path:
    repo.mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "T", cwd=repo)
    (repo / "README.md").write_text("hi\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "init", cwd=repo)
    return repo.resolve()


def _branch_at_main(repo: Path, branch: str) -> str:
    _git("branch", branch, "main", cwd=repo)
    return _git("rev-parse", branch, cwd=repo).strip()


@pytest.fixture
def fleet(tmp_path):
    projects = tmp_path / "projects"
    repo = _init(projects / "myrepo")
    slot = repo / ".worktrees" / OLD
    _git("worktree", "add", "-q", str(slot), "-b", f"wt-{OLD}", cwd=repo)

    # An agent worktree in the slot's own repo, whose marker names the slot.
    leaf_c = repo / ".worktrees" / f"{OLD}-agents" / "leaf-c"
    _git("worktree", "add", "-q", str(leaf_c), "-b", f"wt/{OLD}/leaf-c", cwd=repo)
    _git("config", f"branch.wt/{OLD}/leaf-c.aih-session-home", str(slot), cwd=repo)

    other = _init(projects / "otherrepo")
    leaf_a = other / ".worktrees" / f"{OLD}-agents" / "leaf-a"
    _git("worktree", "add", "-q", str(leaf_a), "-b", f"wt/{OLD}/leaf-a", cwd=other)
    (leaf_a / "wip.txt").write_text("agent work\n")
    tip_b = _branch_at_main(other, f"wt/{OLD}/leaf-b")
    _git("config", f"branch.wt/{OLD}/leaf-a.aih-session-home", str(slot), cwd=other)
    tip_a = _git("rev-parse", f"wt/{OLD}/leaf-a", cwd=other).strip()
    return {
        "projects": projects,
        "repo": repo,
        "slot": slot,
        "other": other,
        "leaf_a": leaf_a,
        "leaf_c": leaf_c,
        "tips": {"leaf-a": tip_a, "leaf-b": tip_b},
    }


def _migrate(fleet, *flags: str):
    with (
        patch("ai_cli.session._tmux_session_names", return_value=[]),
        patch("ai_cli.config._get_projects_dir", return_value=fleet["projects"]),
    ):
        return run_cli(["ai", "session-migrate", OLD, NEW, "-r", str(fleet["repo"]), *flags])


def _live(cwd: Path, name: str = "other-session-1") -> None:
    sessions = Path.home() / ".claude" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{os.getpid()}.json").write_text(json.dumps({"pid": os.getpid(), "name": name, "cwd": str(cwd)}))


def _branches(repo: Path) -> list[str]:
    return _git("for-each-ref", "--format=%(refname:short)", "refs/heads/wt/", cwd=repo).split()


def _markers(repo: Path) -> str:
    return _git("config", "--get-regexp", r"aih-session-home$", cwd=repo, check=False)


def _provenance(worktree: Path) -> Path:
    path = Path(_git("rev-parse", "--git-path", "aih-provenance.json", cwd=worktree).strip())
    return path if path.is_absolute() else worktree / path


@pytest.fixture
def owned_fleet(fleet):
    records = (
        (fleet["slot"], fleet["repo"], f"wt-{OLD}"),
        (fleet["leaf_a"], fleet["other"], f"wt/{OLD}/leaf-a"),
    )
    for worktree, repo, branch in records:
        _provenance(worktree).write_text(
            json.dumps({"owner_session": OLD, "created_by": "test fixture"}),
            encoding="utf-8",
        )
        _git("config", f"branch.{branch}.aih-agent-worktree", str(worktree), cwd=repo)
    fleet["ownership_records"] = records
    return fleet


def test_given_owned_worktrees_when_dry_run_then_rebinds_are_listed_and_ownership_is_unchanged(owned_fleet):
    before = {
        worktree: (
            _provenance(worktree).read_bytes(),
            _git("config", "--get", f"branch.{branch}.aih-agent-worktree", cwd=repo),
        )
        for worktree, repo, branch in owned_fleet["ownership_records"]
    }

    code, out, err = _migrate(owned_fleet, "-n")

    assert code == 0, out + err
    assert out.count("provenance owner:") == 2
    assert out.count("agent-worktree marker:") == 2
    for worktree, repo, branch in owned_fleet["ownership_records"]:
        assert _provenance(worktree).read_bytes() == before[worktree][0]
        assert _git("config", "--get", f"branch.{branch}.aih-agent-worktree", cwd=repo) == before[worktree][1]


def test_given_owned_worktrees_when_migrated_then_provenance_and_path_stamps_follow(owned_fleet):
    code, out, err = _migrate(owned_fleet, "-y")
    assert code == 0, out + err

    records = (
        (owned_fleet["repo"] / ".worktrees" / NEW, owned_fleet["repo"], f"wt-{NEW}"),
        (
            owned_fleet["other"] / ".worktrees" / f"{NEW}-agents" / "leaf-a",
            owned_fleet["other"],
            f"wt/{NEW}/leaf-a",
        ),
    )
    for worktree, repo, branch in records:
        assert json.loads(_provenance(worktree).read_text(encoding="utf-8")) == {
            "owner_session": NEW,
            "created_by": "test fixture",
        }
        assert _git("config", "--get", f"branch.{branch}.aih-agent-worktree", cwd=repo).strip() == str(worktree)


def test_given_agent_worktrees_and_a_parked_branch_when_dry_run_then_each_is_listed_and_nothing_changes(fleet):
    before = snapshot_tree(fleet["projects"])
    before_refs = _branches(fleet["other"])

    code, out, err = _migrate(fleet, "-n")

    assert code == 0, out + err
    new_a = fleet["other"] / ".worktrees" / f"{NEW}-agents" / "leaf-a"
    assert f"agent worktree: {fleet['leaf_a']} -> {new_a}" in out
    assert f"agent branch: wt/{OLD}/leaf-a ({fleet['other']}) -> wt/{NEW}/leaf-a" in out
    assert f"agent branch: wt/{OLD}/leaf-b ({fleet['other']}) -> wt/{NEW}/leaf-b" in out
    new_slot = fleet["repo"] / ".worktrees" / NEW
    assert f"branch.wt/{NEW}/leaf-a.aih-session-home = {new_slot}" in out
    assert snapshot_tree(fleet["projects"]) == before
    assert _branches(fleet["other"]) == before_refs


def test_given_agent_worktrees_and_a_parked_branch_when_migrated_then_all_follow_the_new_name(fleet):
    code, out, err = _migrate(fleet, "-y")
    assert code == 0, out + err

    other, repo = fleet["other"], fleet["repo"]
    new_slot = repo / ".worktrees" / NEW
    new_a = other / ".worktrees" / f"{NEW}-agents" / "leaf-a"
    listed = _git("worktree", "list", "--porcelain", cwd=other)
    assert f"worktree {new_a.as_posix()}\n" in listed
    assert (new_a / "wip.txt").read_text(encoding="utf-8") == "agent work\n"
    assert not (other / ".worktrees" / f"{OLD}-agents").exists()
    assert not (repo / ".worktrees" / f"{OLD}-agents").exists()

    assert _branches(other) == [f"wt/{NEW}/leaf-a", f"wt/{NEW}/leaf-b"]
    for leaf, tip in fleet["tips"].items():
        assert _git("rev-parse", f"wt/{NEW}/{leaf}", cwd=other).strip() == tip
        assert _git("rev-parse", anchor_ref(f"wt/{OLD}/{leaf}"), cwd=other).strip() == tip
    assert _git("symbolic-ref", "--short", "HEAD", cwd=new_a).strip() == f"wt/{NEW}/leaf-a"

    # The marker follows the renamed branch and names the new slot, in both repos.
    assert _markers(other) == f"branch.wt/{NEW}/leaf-a.aih-session-home {new_slot}\n"
    slot_markers = _markers(repo)
    assert f"branch.wt/{NEW}/leaf-c.aih-session-home {new_slot}\n" in slot_markers
    assert f"wt/{OLD}/" not in slot_markers


def test_given_a_live_process_in_one_agent_worktree_when_migrated_then_it_is_left_and_the_rest_move(fleet):
    _live(fleet["leaf_a"] / "src")

    code, out, err = _migrate(fleet, "-y")

    assert code == 0, out + err
    other = fleet["other"]
    assert f"agent worktree: {fleet['leaf_a']} (left alone: held by pid {os.getpid()})" in out
    assert fleet["leaf_a"].is_dir()
    assert _branches(other) == [f"wt/{OLD}/leaf-a", f"wt/{NEW}/leaf-b"]
    assert (fleet["repo"] / ".worktrees" / f"{NEW}-agents" / "leaf-c").is_dir()


def test_given_git_refuses_one_agent_worktree_when_migrated_then_it_warns_continues_and_exits_1(fleet):
    _git("worktree", "lock", str(fleet["leaf_a"]), cwd=fleet["other"])

    code, out, err = _migrate(fleet, "-y")

    assert code == 1, out + err
    assert f"agent worktree {fleet['leaf_a']} FAILED to move" in out + err
    assert "locked" in out + err
    assert fleet["leaf_a"].is_dir()
    assert _branches(fleet["other"]) == [f"wt/{OLD}/leaf-a", f"wt/{NEW}/leaf-b"]


def test_given_a_destination_branch_exists_when_migrated_then_that_entry_is_skipped_and_left(fleet):
    other = fleet["other"]
    _git("branch", f"wt/{NEW}/leaf-b", "main", cwd=other)

    code, out, err = _migrate(fleet, "-y")

    assert code == 0, out + err
    assert f"agent branch: wt/{OLD}/leaf-b ({other}) -> wt/{NEW}/leaf-b SKIPPED (target exists)" in out
    assert _git("rev-parse", f"wt/{OLD}/leaf-b", cwd=other).strip() == fleet["tips"]["leaf-b"]
    assert f"wt/{NEW}/leaf-a" in _branches(other)


def test_given_non_repos_under_the_projects_dir_when_migrated_then_they_are_skipped_silently(fleet):
    (fleet["projects"] / "notes" / ".worktrees" / f"{OLD}-agents").mkdir(parents=True)
    (fleet["projects"] / "plain").mkdir()

    code, out, err = _migrate(fleet, "-y")

    assert code == 0, out + err
    assert "notes" not in out + err
    assert "plain" not in out + err


def test_given_the_session_itself_is_live_when_migrated_then_no_agent_worktree_is_touched(fleet):
    _live(fleet["slot"], name=OLD)
    before = snapshot_tree(fleet["other"])
    before_refs = _branches(fleet["other"])

    code, out, err = _migrate(fleet, "-y")

    assert code == 1
    assert "still running" in out + err
    assert snapshot_tree(fleet["other"]) == before
    assert _branches(fleet["other"]) == before_refs


def _with_submodule(repo: Path, sub_origin: Path) -> None:
    """Commit a submodule ``third/sub`` (cloned from ``sub_origin``) into ``repo``'s main."""
    _git("-c", "protocol.file.allow=always", "submodule", "-q", "add", str(sub_origin), "third/sub", cwd=repo)
    _git("commit", "-qm", "add submodule", cwd=repo)


def _init_submodule(worktree: Path) -> None:
    _git("-c", "protocol.file.allow=always", "submodule", "-q", "update", "--init", cwd=worktree)


@pytest.fixture
def submodule_fleet(tmp_path):
    """A repo whose slot and agent worktree both hold an initialised submodule."""
    projects = tmp_path / "projects"
    sub_origin = _init(tmp_path / "sub-origin")
    repo = _init(projects / "myrepo")
    _with_submodule(repo, sub_origin)
    slot = repo / ".worktrees" / OLD
    _git("worktree", "add", "-q", str(slot), "-b", f"wt-{OLD}", cwd=repo)
    _init_submodule(slot)
    leaf = repo / ".worktrees" / f"{OLD}-agents" / "leaf-s"
    _git("worktree", "add", "-q", str(leaf), "-b", f"wt/{OLD}/leaf-s", cwd=repo)
    _init_submodule(leaf)
    refused = subprocess.run(
        ["git", "worktree", "move", str(leaf), str(tmp_path / "probe")],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "submodules" in refused.stderr, "the fixture must reproduce git's own refusal"
    return {"projects": projects, "repo": repo, "slot": slot, "leaf": leaf}


def _usable(worktree: Path) -> None:
    for where in (worktree, worktree / "third" / "sub"):
        status = subprocess.run(["git", "status", "--short"], cwd=where, capture_output=True, text=True, check=False)
        assert status.returncode == 0, f"{where}: {status.stderr}"
    listed = _git("submodule", "status", cwd=worktree)
    assert listed.startswith(" "), f"submodule not initialised after the move: {listed!r}"


def test_given_slot_and_agent_worktrees_with_submodules_when_migrated_then_both_move_and_stay_usable(
    submodule_fleet,
):
    code, out, err = _migrate(submodule_fleet, "-y")

    assert code == 0, out + err
    repo = submodule_fleet["repo"]
    new_slot = repo / ".worktrees" / NEW
    new_leaf = repo / ".worktrees" / f"{NEW}-agents" / "leaf-s"
    listed = _git("worktree", "list", "--porcelain", cwd=repo)
    for path in (new_slot, new_leaf):
        assert f"worktree {path.as_posix()}\n" in listed
        _usable(path)
    assert not submodule_fleet["slot"].exists()
    assert not submodule_fleet["leaf"].exists()
    assert _git("symbolic-ref", "--short", "HEAD", cwd=new_leaf).strip() == f"wt/{NEW}/leaf-s"


def test_given_a_locked_agent_worktree_with_a_submodule_when_migrated_then_it_is_not_moved_by_hand(
    submodule_fleet,
):
    _git("worktree", "lock", str(submodule_fleet["leaf"]), cwd=submodule_fleet["repo"])

    code, out, err = _migrate(submodule_fleet, "-y")

    assert code == 1, out + err
    assert "locked" in out + err
    assert submodule_fleet["leaf"].is_dir()
    _usable(submodule_fleet["leaf"])
