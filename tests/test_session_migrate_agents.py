"""``ai migrate-session OLD NEW`` carries the session's agent worktrees and branches in every repo.

Each test drives the real command against a temp projects dir holding the slot's repo
and a second repo. The second repo carries what a delegating session leaves behind: an
agent worktree under ``.worktrees/<old>-agents/`` on ``wt/<old>/leaf-a``, a parked
``wt/<old>/leaf-b`` and a session-home marker naming the old slot.
"""

from __future__ import annotations

import importlib.util
import json
import os
import py_compile
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import run_cli as _run_cli
from conftest import snapshot_tree, with_environment_config

from ai_cli.branch_rename import anchor_ref
from ai_cli.venv_relocate import relocate_venv

OLD, NEW = "myrepo-3", "myrepo-box-3"


def run_cli(argv, config=None, env=None):
    """``conftest.run_cli`` with the environment's marker prefix and chief homes configured."""
    return _run_cli(argv, config=with_environment_config(config), env=env)


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
    _git("config", f"branch.wt/{OLD}/leaf-c.hub-session-home", str(slot), cwd=repo)

    other = _init(projects / "otherrepo")
    leaf_a = other / ".worktrees" / f"{OLD}-agents" / "leaf-a"
    _git("worktree", "add", "-q", str(leaf_a), "-b", f"wt/{OLD}/leaf-a", cwd=other)
    (leaf_a / "wip.txt").write_text("agent work\n")
    tip_b = _branch_at_main(other, f"wt/{OLD}/leaf-b")
    _git("config", f"branch.wt/{OLD}/leaf-a.hub-session-home", str(slot), cwd=other)
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
        return run_cli(["ai", "migrate-session", OLD, NEW, "-r", str(fleet["repo"]), *flags])


def _live(cwd: Path, name: str = "other-session-1") -> None:
    sessions = Path.home() / ".claude" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{os.getpid()}.json").write_text(json.dumps({"pid": os.getpid(), "name": name, "cwd": str(cwd)}))


def _branches(repo: Path) -> list[str]:
    return _git("for-each-ref", "--format=%(refname:short)", "refs/heads/wt/", cwd=repo).split()


def _markers(repo: Path) -> str:
    return _git("config", "--get-regexp", r"hub-session-home$", cwd=repo, check=False)


def _provenance(worktree: Path) -> Path:
    path = Path(_git("rev-parse", "--git-path", "hub-provenance.json", cwd=worktree).strip())
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
        _git("config", f"branch.{branch}.hub-agent-worktree", str(worktree), cwd=repo)
    fleet["ownership_records"] = records
    return fleet


def test_given_owned_worktrees_when_dry_run_then_rebinds_are_listed_and_ownership_is_unchanged(owned_fleet):
    before = {
        worktree: (
            _provenance(worktree).read_bytes(),
            _git("config", "--get", f"branch.{branch}.hub-agent-worktree", cwd=repo),
        )
        for worktree, repo, branch in owned_fleet["ownership_records"]
    }

    code, out, err = _migrate(owned_fleet, "-n")

    assert code == 0, out + err
    assert out.count("provenance owner:") == 2
    assert f"provenance record: {owned_fleet['leaf_c']} (skipped: missing)" in out
    assert out.count("agent-worktree marker:") == 2
    for worktree, repo, branch in owned_fleet["ownership_records"]:
        assert _provenance(worktree).read_bytes() == before[worktree][0]
        assert _git("config", "--get", f"branch.{branch}.hub-agent-worktree", cwd=repo) == before[worktree][1]


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
        assert _git("config", "--get", f"branch.{branch}.hub-agent-worktree", cwd=repo).strip() == str(worktree)


def test_given_an_unreadable_provenance_record_when_migrated_then_it_is_reported_and_skipped(fleet):
    provenance = _provenance(fleet["leaf_a"])
    provenance.write_text("not json\n", encoding="utf-8")

    code, out, err = _migrate(fleet, "-y")

    assert code == 0, out + err
    assert f"provenance record: {fleet['leaf_a']} (skipped: unreadable)" in out
    new_worktree = fleet["other"] / ".worktrees" / f"{NEW}-agents" / "leaf-a"
    assert new_worktree.is_dir()
    assert _provenance(new_worktree).read_text(encoding="utf-8") == "not json\n"


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
    assert f"branch.wt/{NEW}/leaf-a.hub-session-home = {new_slot}" in out
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
    assert _markers(other) == f"branch.wt/{NEW}/leaf-a.hub-session-home {new_slot}\n"
    slot_markers = _markers(repo)
    assert f"branch.wt/{NEW}/leaf-c.hub-session-home {new_slot}\n" in slot_markers
    assert f"wt/{OLD}/" not in slot_markers


def test_given_a_live_process_in_one_agent_worktree_when_migrated_then_it_is_left_and_the_rest_move(fleet):
    _live(fleet["leaf_a"] / "src")

    code, out, err = _migrate(fleet, "-y")

    assert code == 1, out + err
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


def _symlinks_available(directory: Path) -> bool:
    """Whether this platform lets the test create a directory symlink; probed before any fixture is built."""
    probe = directory / "symlink-probe"
    try:
        probe.symlink_to(directory, target_is_directory=True)
        return True
    except OSError:
        return False
    finally:
        probe.unlink(missing_ok=True)


def _venv_in_root(root: Path, name: str, worktree: Path) -> Path:
    """A venv kept outside its worktree, carrying an editable install of it and a deep package tree."""
    venv = root / name
    site = venv / "lib" / "python3.12" / "site-packages"
    (site / "pkg-1.0.dist-info").mkdir(parents=True)
    (site / "__editable__.pkg.pth").write_text(f"{worktree}/src\n", encoding="utf-8")
    (site / "pkg-1.0.dist-info" / "direct_url.json").write_text(
        json.dumps({"url": worktree.as_uri(), "dir_info": {"editable": True}}), encoding="utf-8"
    )
    deep = site / "heavypkg" / "driver" / "package" / "lib"
    for i in range(3):
        (deep / f"mod{i}").mkdir(parents=True)
        (deep / f"mod{i}" / "index.js").write_text("module.exports = 1\n", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    return venv


def test_given_agent_venvs_in_a_shared_venv_root_when_migrated_then_only_the_sessions_install_records_are_read(
    fleet, tmp_path
):
    if not _symlinks_available(tmp_path):
        pytest.skip("this platform cannot create directory symlinks")
    venvs = tmp_path / "local-io" / "venvs"
    own = _venv_in_root(venvs, "otherrepo--leaf-a", fleet["leaf_a"])
    unrelated = _venv_in_root(venvs, "otherrepo--unrelated-1", fleet["other"] / ".worktrees" / "unrelated-1")
    (fleet["leaf_a"] / ".venv").symlink_to(own, target_is_directory=True)
    listed: list[Path] = []
    real_scandir, real_listdir = os.scandir, os.listdir

    def _record(path):
        if isinstance(path, (str, os.PathLike)):
            listed.append(Path(os.fspath(path)).resolve())

    def scandir(path="."):
        _record(path)
        return real_scandir(path)

    def listdir(path="."):
        _record(path)
        return real_listdir(path)

    with patch("os.scandir", scandir), patch("os.listdir", listdir):
        code, out, err = _migrate(fleet, "-y")

    assert code == 0, out + err
    new_a = fleet["other"] / ".worktrees" / f"{NEW}-agents" / "leaf-a"
    site = own / "lib" / "python3.12" / "site-packages"
    assert (site / "__editable__.pkg.pth").read_text(encoding="utf-8") == f"{new_a}/src\n"
    assert json.loads((site / "pkg-1.0.dist-info" / "direct_url.json").read_text(encoding="utf-8"))["url"] == (
        new_a.as_uri()
    )
    heavy = (site / "heavypkg").resolve()
    assert [p for p in listed if p == heavy or heavy in p.parents] == []
    assert [p for p in listed if p == unrelated.resolve() or unrelated.resolve() in p.parents] == []


def test_given_a_destination_branch_exists_when_migrated_then_that_entry_is_skipped_and_left(fleet):
    other = fleet["other"]
    _git("branch", f"wt/{NEW}/leaf-b", "main", cwd=other)

    code, out, err = _migrate(fleet, "-y")

    assert code == 1, out + err
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

    assert code == 2
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


# A session already migrated OLD -> NEW leaves, in another repo it edited, a slot still named
# ``.worktrees/OLD``: its branch was renamed with the session's other ``wt/<old>/*`` branches,
# but nothing moved or retired the folder.

RETIRE_ANCHORS = "refs/preserved/retire-session"


def _init_pushed(projects: Path, name: str) -> Path:
    """A repo under ``projects`` whose ``main`` is pushed to a bare ``origin``."""
    origin = projects.parent / f"{name}-origin.git"
    _git("init", "-q", "--bare", "-b", "main", str(origin), cwd=projects.parent)
    repo = _init(projects / name)
    _git("config", "maintenance.auto", "false", cwd=repo)
    _git("config", "gc.auto", "0", cwd=repo)
    _git("remote", "add", "origin", str(origin), cwd=repo)
    _git("push", "-q", "origin", "main", cwd=repo)
    _git("fetch", "-q", "origin", cwd=repo)
    return repo


@pytest.fixture
def leftover(tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    home = _init_pushed(projects, "homerepo")
    home_slot = home / ".worktrees" / NEW
    _git("worktree", "add", "-q", str(home_slot), "-b", f"wt-{NEW}", cwd=home)
    second = _init_pushed(projects, "secondrepo")
    slot = second / ".worktrees" / OLD
    branch = f"wt/{NEW}/fix-thing"
    _git("worktree", "add", "-q", "--no-track", str(slot), "-b", branch, "origin/main", cwd=second)
    return {
        "projects": projects,
        "home": home,
        "home_slot": home_slot,
        "second": second,
        "slot": slot,
        "branch": branch,
        "tip": _git("rev-parse", branch, cwd=second).strip(),
    }


def _run_in(fleet, repo: Path, *flags: str):
    with (
        patch("ai_cli.session._tmux_session_names", return_value=[]),
        patch("ai_cli.config._get_projects_dir", return_value=fleet["projects"]),
    ):
        return run_cli(["ai", "migrate-session", OLD, NEW, "-r", str(repo), *flags])


def _registered(repo: Path) -> str:
    return _git("worktree", "list", "--porcelain", cwd=repo)


@pytest.mark.parametrize("flags", [("-y",), ("--finish", "-y")])
def test_given_a_landed_old_slot_in_another_repo_when_migrated_there_while_new_is_live_then_it_is_retired(
    leftover, flags
):
    _live(leftover["home_slot"], name=NEW)

    code, out, err = _run_in(leftover, leftover["second"], *flags)

    assert code == 0, out + err
    slot, second = leftover["slot"], leftover["second"]
    assert not slot.exists()
    assert f"worktree {slot.as_posix()}\n" not in _registered(second)
    assert _git("rev-parse", f"{RETIRE_ANCHORS}/{leftover['branch']}", cwd=second).strip() == leftover["tip"]
    assert _git("rev-parse", leftover["branch"], cwd=second).strip() == leftover["tip"]
    assert f"leftover slot: {slot} (removed" in out
    assert leftover["home_slot"].is_dir()


def test_given_a_landed_old_slot_in_another_repo_when_dry_run_then_its_removal_is_listed_and_nothing_changes(
    leftover,
):
    _live(leftover["home_slot"], name=NEW)
    before = snapshot_tree(leftover["projects"])

    code, out, err = _run_in(leftover, leftover["second"], "-n")

    assert code == 0, out + err
    assert f"leftover slot: {leftover['slot']} (would be removed" in out
    assert snapshot_tree(leftover["projects"]) == before


def test_given_a_landed_old_slot_in_another_repo_when_the_home_slot_is_migrated_then_it_is_retired(tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    home = _init_pushed(projects, "homerepo")
    _git("worktree", "add", "-q", str(home / ".worktrees" / OLD), "-b", f"wt-{OLD}", cwd=home)
    second = _init_pushed(projects, "secondrepo")
    slot = second / ".worktrees" / OLD
    _git("worktree", "add", "-q", "--no-track", str(slot), "-b", f"wt/{OLD}/fix-thing", "origin/main", cwd=second)
    fleet = {"projects": projects}

    code, out, err = _run_in(fleet, home, "-y")

    assert code == 0, out + err
    assert (home / ".worktrees" / NEW).is_dir()
    assert not slot.exists()
    assert _git("rev-parse", f"{RETIRE_ANCHORS}/wt/{NEW}/fix-thing", cwd=second).strip()
    assert f"leftover slot: {slot} (removed" in out


@pytest.mark.parametrize("kind", ["uncommitted", "unlanded"])
def test_given_an_old_slot_holding_unlanded_work_when_migrated_then_it_is_kept_named_and_exits_1(leftover, kind):
    slot = leftover["slot"]
    (slot / "wip.txt").write_text("not committed\n")
    if kind == "unlanded":
        _git("add", "wip.txt", cwd=slot)
        _git("commit", "-qm", "local only", cwd=slot)
    _live(leftover["home_slot"], name=NEW)
    before = snapshot_tree(slot)

    code, out, err = _run_in(leftover, leftover["second"], "-y")

    assert code == 1, out + err
    assert snapshot_tree(slot) == before
    assert f"worktree {slot.as_posix()}\n" in _registered(leftover["second"])
    assert f"leftover slot: {slot} (kept:" in out
    assert ("untracked: wip.txt" if kind == "uncommitted" else "not in") in out + err


def test_given_old_agent_containers_when_a_leftover_is_cleaned_then_empty_ones_go_and_files_are_carried(leftover):
    empty = leftover["home"] / ".worktrees" / f"{OLD}-agents"
    (empty / "nested").mkdir(parents=True)
    full = leftover["second"] / ".worktrees" / f"{OLD}-agents"
    full.mkdir(parents=True)
    (full / "notes.txt").write_text("keep me\n")
    _live(leftover["home_slot"], name=NEW)

    code, out, err = _run_in(leftover, leftover["second"], "-y")

    assert code == 0, out + err
    assert not empty.exists()
    assert f"agent container: {empty} (removed: empty)" in out
    assert not full.exists()
    assert (full.with_name(f"{NEW}-agents") / "notes.txt").read_text() == "keep me\n"
    assert f"agent container: {full} (removed: empty)" in out


def test_given_the_live_new_session_works_in_the_old_slot_when_migrated_there_then_it_refuses(leftover):
    _live(leftover["slot"] / "src", name=NEW)
    before = snapshot_tree(leftover["second"])

    code, out, err = _run_in(leftover, leftover["second"], "-y")

    assert code == 2
    assert "still running" in out + err
    assert snapshot_tree(leftover["second"]) == before


# Every path that embeds the old session's agents container moves with it: a container named
# ``<host>--<old>-agents`` as well as ``<old>-agents``, and the Claude Code project directory of
# an agent whose worktree was reaped before the migration.

HOST_CONTAINER = f"myhost--{OLD}-agents"
NEW_HOST_CONTAINER = f"myhost--{NEW}-agents"


def _slug(path: Path) -> str:
    return "".join(c if c.isascii() and c.isalnum() else "-" for c in str(path))


def _project_dir(cwd: Path, sid: str = "abc123") -> Path:
    project = Path.home() / ".claude" / "projects" / _slug(cwd)
    project.mkdir(parents=True)
    (project / f"{sid}.jsonl").write_text(json.dumps({"type": "user", "cwd": str(cwd)}) + "\n", encoding="utf-8")
    return project


@pytest.fixture
def host_fleet(fleet):
    leaf = fleet["other"] / ".worktrees" / HOST_CONTAINER / "leaf-h"
    _git("worktree", "add", "-q", str(leaf), "-b", f"wt/{OLD}/leaf-h", cwd=fleet["other"])
    fleet["leaf_h"] = leaf
    return fleet


def test_given_a_host_prefixed_agent_worktree_when_dry_run_then_its_move_is_listed_and_nothing_changes(host_fleet):
    before = snapshot_tree(host_fleet["projects"])

    code, out, err = _migrate(host_fleet, "-n")

    assert code == 0, out + err
    new_h = host_fleet["other"] / ".worktrees" / NEW_HOST_CONTAINER / "leaf-h"
    assert f"agent worktree: {host_fleet['leaf_h']} -> {new_h}" in out
    assert snapshot_tree(host_fleet["projects"]) == before


def test_given_a_host_prefixed_agent_worktree_when_migrated_then_directory_and_branch_follow_the_new_name(host_fleet):
    code, out, err = _migrate(host_fleet, "-y")

    assert code == 0, out + err
    other = host_fleet["other"]
    new_h = other / ".worktrees" / NEW_HOST_CONTAINER / "leaf-h"
    assert f"worktree {new_h.as_posix()}\n" in _registered(other)
    assert _git("symbolic-ref", "--short", "HEAD", cwd=new_h).strip() == f"wt/{NEW}/leaf-h"
    assert not (other / ".worktrees" / HOST_CONTAINER).exists()


def test_given_a_host_prefixed_worktree_whose_branch_was_already_renamed_when_finished_then_it_moves(fleet):
    code, out, err = _migrate(fleet, "-y")
    assert code == 0, out + err
    other = fleet["other"]
    stranded = other / ".worktrees" / HOST_CONTAINER / "leaf-x"
    _git("worktree", "add", "-q", str(stranded), "-b", f"wt/{NEW}/leaf-x", cwd=other)

    code, out, err = _migrate(fleet, "--finish", "-y")

    assert code == 0, out + err
    new_x = other / ".worktrees" / NEW_HOST_CONTAINER / "leaf-x"
    assert f"worktree {new_x.as_posix()}\n" in _registered(other)
    assert _git("symbolic-ref", "--short", "HEAD", cwd=new_x).strip() == f"wt/{NEW}/leaf-x"
    assert not stranded.exists()


def test_given_a_host_prefixed_container_holding_another_sessions_worktree_when_migrated_then_it_is_left(fleet):
    other = fleet["other"]
    foreign = other / ".worktrees" / HOST_CONTAINER / "leaf-f"
    _git("worktree", "add", "-q", str(foreign), "-b", "wt/session-1/leaf-f", cwd=other)

    code, out, err = _migrate(fleet, "-y")

    assert code == 0, out + err
    assert f"worktree {foreign.as_posix()}\n" in _registered(other)
    assert f"agent worktree: {foreign}" not in out


@pytest.mark.parametrize("container", [f"{OLD}-agents", HOST_CONTAINER])
def test_given_a_reaped_agents_project_dir_when_dry_run_then_its_move_is_listed_and_nothing_changes(fleet, container):
    gone = fleet["other"] / ".worktrees" / container / "gone-leaf"
    project = _project_dir(gone)
    before = snapshot_tree(project.parent)

    code, out, err = _migrate(fleet, "-n")

    assert code == 0, out + err
    dest = project.parent / _slug(gone.parent.with_name(container.replace(OLD, NEW)) / "gone-leaf")
    assert f"agent project dir: {project} -> {dest}" in out
    assert snapshot_tree(project.parent) == before


@pytest.mark.parametrize("container", [f"{OLD}-agents", HOST_CONTAINER])
def test_given_a_reaped_agents_project_dir_when_migrated_then_it_moves_to_the_new_slug(fleet, container):
    gone = fleet["other"] / ".worktrees" / container / "gone-leaf"
    project = _project_dir(gone)

    code, out, err = _migrate(fleet, "-y")

    assert code == 0, out + err
    new_gone = gone.parent.with_name(container.replace(OLD, NEW)) / "gone-leaf"
    dest = project.parent / _slug(new_gone)
    assert not project.exists()
    record = json.loads((dest / "abc123.jsonl").read_text(encoding="utf-8"))
    assert record["cwd"] == str(new_gone)


def test_given_a_reaped_agents_project_dir_when_finished_then_it_moves_to_the_new_slug(fleet):
    code, out, err = _migrate(fleet, "-y")
    assert code == 0, out + err
    gone = fleet["other"] / ".worktrees" / HOST_CONTAINER / "gone-leaf"
    project = _project_dir(gone)

    code, out, err = _migrate(fleet, "--finish", "-y")

    assert code == 0, out + err
    assert not project.exists()
    assert (project.parent / _slug(gone.parent.with_name(NEW_HOST_CONTAINER) / "gone-leaf")).is_dir()


def test_given_the_new_agents_project_dir_exists_when_migrated_then_the_collision_is_reported_and_both_kept(fleet):
    gone = fleet["other"] / ".worktrees" / f"{OLD}-agents" / "gone-leaf"
    project = _project_dir(gone)
    taken = _project_dir(fleet["other"] / ".worktrees" / f"{NEW}-agents" / "gone-leaf", sid="def456")
    before = (snapshot_tree(project), snapshot_tree(taken))

    code, out, err = _migrate(fleet, "-y")

    assert code == 1, out + err
    assert f"agent project dir: {project} -> {taken} SKIPPED (target exists)" in out
    assert (snapshot_tree(project), snapshot_tree(taken)) == before


def test_given_a_project_dir_whose_transcript_records_another_cwd_when_migrated_then_it_is_left(fleet):
    # ``<old>-agents-extra-agents`` slugs to a name that starts with ``<old>-agents``'s own slug.
    elsewhere = fleet["other"] / ".worktrees" / f"{OLD}-agents-extra-agents" / "leaf-z"
    project = _project_dir(elsewhere)
    before = snapshot_tree(project)

    code, out, err = _migrate(fleet, "-y")

    assert code == 0, out + err
    assert snapshot_tree(project) == before
    assert str(project) not in out


def test_given_editable_finder_and_egg_link_records_when_migrated_then_both_name_the_new_worktree(fleet, tmp_path):
    if not _symlinks_available(tmp_path):
        pytest.skip("this platform cannot create directory symlinks")
    venvs = tmp_path / "local-io" / "venvs"
    own = _venv_in_root(venvs, "otherrepo--leaf-a", fleet["leaf_a"])
    site = own / "lib" / "python3.12" / "site-packages"
    old = fleet["leaf_a"]
    (site / "__editable___pkg_1_0_finder.py").write_text(
        f"MAPPING = {{'pkg': '{old}/src/pkg'}}\nNAMESPACES = {{}}\n", encoding="utf-8"
    )
    (site / "legacypkg.egg-link").write_text(f"{old}\n.\n", encoding="utf-8")
    (old / ".venv").symlink_to(own, target_is_directory=True)

    code, out, err = _migrate(fleet, "-y")

    assert code == 0, out + err
    new_a = fleet["other"] / ".worktrees" / f"{NEW}-agents" / "leaf-a"
    assert (site / "__editable___pkg_1_0_finder.py").read_text(encoding="utf-8") == (
        f"MAPPING = {{'pkg': '{new_a}/src/pkg'}}\nNAMESPACES = {{}}\n"
    )
    assert (site / "legacypkg.egg-link").read_text(encoding="utf-8") == f"{new_a}\n.\n"


def test_given_a_direct_url_in_file_uri_form_when_relocated_then_the_uri_names_the_new_root(tmp_path):
    # pip writes ``direct_url.json`` as ``Path.as_uri()``: forward slashes and a drive on Windows,
    # percent-encoded on every platform. A migration cannot produce that form on a POSIX runner, so
    # this calls the relocation directly with a root the URI encodes (a space), the form a native
    # path spelling never matches.
    old = tmp_path / "my repo" / "leaf-a"
    new = tmp_path / "my repo" / "leaf-b"
    site = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
    (site / "pkg-1.0.dist-info").mkdir(parents=True)
    record = site / "pkg-1.0.dist-info" / "direct_url.json"
    record.write_text(json.dumps({"url": old.as_uri(), "dir_info": {"editable": True}}), encoding="utf-8")
    (site / "__editable__.pkg.pth").write_text(f"{old}/src\n", encoding="utf-8")

    changed = relocate_venv(tmp_path / "venv", old, new)

    assert json.loads(record.read_text(encoding="utf-8"))["url"] == new.as_uri()
    assert (site / "__editable__.pkg.pth").read_text(encoding="utf-8") == f"{new}/src\n"
    assert sorted(changed) == sorted([record, site / "__editable__.pkg.pth"])
    assert relocate_venv(tmp_path / "venv", old, new) == []


def _store_venv(root: Path, name: str, owner: Path) -> tuple[Path, Path]:
    """A venv in a shared store with the back-pointer record naming ``owner``: ``(venv, record)``."""
    venv = _venv_in_root(root, name, owner)
    record = venv / ".hub-checkout"
    record.write_bytes(f"{owner}\n".encode())
    return venv, record


@pytest.fixture
def store(fleet, tmp_path):
    """A venv store whose records name the slot, its agent worktree and two checkouts the migration never moves."""
    if not _symlinks_available(tmp_path):
        pytest.skip("this platform cannot create directory symlinks")
    root = tmp_path / "local-io" / "venvs"
    agent_venv, agent_record = _store_venv(root, "otherrepo--leaf-a", fleet["leaf_a"])
    slot_venv, slot_record = _store_venv(root, f"myrepo--{OLD}", fleet["slot"])
    unrelated = [
        _store_venv(root, "otherrepo--unrelated-1", fleet["other"] / ".worktrees" / "unrelated-1")[1],
        # A sibling whose path extends the moved worktree's: a prefix match would wrongly take it.
        _store_venv(root, "otherrepo--leaf-a-2", fleet["leaf_a"].with_name("leaf-a-2"))[1],
    ]
    return {
        "agent": (agent_venv, agent_record),
        "slot": (slot_venv, slot_record),
        "unrelated": {record: record.read_bytes() for record in unrelated},
        "new_a": fleet["other"] / ".worktrees" / f"{NEW}-agents" / "leaf-a",
        "new_slot": fleet["repo"] / ".worktrees" / NEW,
    }


def test_given_venv_back_pointers_naming_moved_checkouts_when_migrated_then_they_name_the_new_paths(fleet, store):
    (fleet["leaf_a"] / ".venv").symlink_to(store["agent"][0], target_is_directory=True)
    (fleet["slot"] / ".venv").symlink_to(store["slot"][0], target_is_directory=True)

    code, out, err = _migrate(fleet, "-y")

    assert code == 0, out + err
    assert store["agent"][1].read_bytes() == f"{store['new_a']}\n".encode()
    assert store["slot"][1].read_bytes() == f"{store['new_slot']}\n".encode()
    assert {record: record.read_bytes() for record in store["unrelated"]} == store["unrelated"]


def test_given_a_verified_pair_whose_venv_back_pointers_name_old_paths_when_finished_then_they_are_repointed(
    fleet, store
):
    from ai_cli.config import get_xdg_state_home
    from ai_cli.session_migrate_status import finish_state

    code, out, err = _migrate(fleet, "-y")
    assert code == 0, out + err
    assert finish_state(OLD, NEW, get_xdg_state_home()) == "verified"
    # What a migration that never carried the back-pointers left: the checkouts moved, their records did not.
    (store["new_a"] / ".venv").symlink_to(store["agent"][0], target_is_directory=True)
    (store["new_slot"] / ".venv").symlink_to(store["slot"][0], target_is_directory=True)
    stale = {store["agent"][1]: store["agent"][1].read_bytes(), store["slot"][1]: store["slot"][1].read_bytes()}

    code, out, err = _migrate(fleet, "--finish", "-n")

    assert code == 0, out + err
    assert "venv back-pointer" in out
    assert {record: record.read_bytes() for record in stale} == stale

    code, out, err = _migrate(fleet, "--finish", "-y")

    assert code == 0, out + err
    assert store["agent"][1].read_bytes() == f"{store['new_a']}\n".encode()
    assert store["slot"][1].read_bytes() == f"{store['new_slot']}\n".encode()
    assert {record: record.read_bytes() for record in store["unrelated"]} == store["unrelated"]


# A moved checkout keeps its ``__pycache__/*.pyc`` files, and each one's ``co_filename`` still
# names the old, now missing, directory: anything reading source through a code object then
# finds nothing. The migration removes those caches (Python rebuilds them on the next import),
# but never inside a virtual environment, never a file git tracks and never through a symlink.


def _compiled(directory: Path, name: str = "mod") -> Path:
    """A module under ``directory`` with its bytecode compiled beside it; returns the ``.pyc``."""
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / f"{name}.py"
    source.write_text("def f():\n    return 1\n", encoding="utf-8")
    return Path(py_compile.compile(str(source), cfile=importlib.util.cache_from_source(str(source)), doraise=True))


def _caches(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("__pycache__"))


@pytest.fixture
def compiled_fleet(fleet, tmp_path, monkeypatch):
    # With a cache prefix set, bytecode is written elsewhere and no __pycache__ exists to check.
    monkeypatch.delenv("PYTHONPYCACHEPREFIX", raising=False)
    monkeypatch.setattr(sys, "pycache_prefix", None)
    slot, leaf_c = fleet["slot"], fleet["leaf_c"]
    _compiled(slot / "pkg")
    _compiled(slot / "pkg" / "sub")
    _compiled(leaf_c / "src")
    _compiled(slot / ".venv" / "lib" / "site-packages" / "dep")
    _compiled(slot / "tools" / "env" / "lib" / "dep")
    (slot / "tools" / "env" / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    # A tracked file under a __pycache__ path, committed on the agent branch, with an untracked
    # compiled sibling in the same directory.
    tracked = leaf_c / "fixtures" / "__pycache__" / "kept.pyc"
    tracked.parent.mkdir(parents=True)
    tracked.write_bytes(b"tracked fixture\n")
    _git("add", "-f", str(tracked), cwd=leaf_c)
    _git("commit", "-qm", "tracked cache fixture", cwd=leaf_c)
    (tracked.parent / "untracked.pyc").write_bytes(b"stale\n")
    fleet["tracked"] = tracked.relative_to(leaf_c)
    return fleet


def test_given_moved_checkouts_with_compiled_modules_when_migrated_then_no_cache_is_left_outside_a_venv(
    compiled_fleet,
):
    code, out, err = _migrate(compiled_fleet, "-y")
    assert code == 0, out + err

    new_slot = compiled_fleet["repo"] / ".worktrees" / NEW
    new_c = compiled_fleet["repo"] / ".worktrees" / f"{NEW}-agents" / "leaf-c"
    assert _caches(new_slot) == [
        ".venv/lib/site-packages/dep/__pycache__",
        "tools/env/lib/dep/__pycache__",
    ]
    assert list((new_slot / ".venv" / "lib" / "site-packages" / "dep" / "__pycache__").glob("*.pyc"))
    assert list((new_slot / "tools" / "env" / "lib" / "dep" / "__pycache__").glob("*.pyc"))
    assert (new_slot / "pkg" / "mod.py").is_file()
    assert _caches(new_c) == ["fixtures/__pycache__"]
    tracked = new_c / compiled_fleet["tracked"]
    assert tracked.read_bytes() == b"tracked fixture\n"
    assert sorted(p.name for p in tracked.parent.iterdir()) == ["kept.pyc"]
    assert _git("status", "--porcelain", "--untracked-files=all", cwd=new_c) == "?? src/mod.py\n"


def test_given_a_symlink_to_an_outside_cache_when_migrated_then_the_outside_cache_survives(
    fleet, tmp_path, monkeypatch
):
    monkeypatch.delenv("PYTHONPYCACHEPREFIX", raising=False)
    monkeypatch.setattr(sys, "pycache_prefix", None)
    if not _symlinks_available(tmp_path):
        pytest.skip("this platform does not let the test create a directory symlink")
    outside = tmp_path / "shared"
    pyc = _compiled(outside / "lib")
    (fleet["slot"] / "linked").symlink_to(outside, target_is_directory=True)
    (fleet["slot"] / "linkedcache").symlink_to(pyc.parent, target_is_directory=True)

    code, out, err = _migrate(fleet, "-y")
    assert code == 0, out + err

    assert pyc.is_file()
    new_slot = fleet["repo"] / ".worktrees" / NEW
    assert (new_slot / "linked").is_symlink()
    assert (new_slot / "linkedcache").is_symlink()
