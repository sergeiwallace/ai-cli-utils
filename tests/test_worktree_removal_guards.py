"""Worktree removal paths must never destroy state a human or another slot still owns.

Two launch-time cleanups used to remove more than they could prove disposable:

* ``ai copier-update`` cleared its temp worktree with ``git worktree remove --force``
  and ``git branch -D`` at the START of every per-repo update. A conflict or a
  rejected push deliberately leaves that worktree in place for a human, so the next
  run destroyed it, and any resolution in progress with it.
* ``ai c`` ran a repo-wide ``git worktree prune`` on every launch, dropping every
  registration whose directory was missing (and its index and HEAD), not just the
  launching slot's.

Every test drives the real entry point (``run_copier_update`` / ``create_worktree``)
against real git repositories.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_cli import copier_update
from ai_cli.copier_update import EX_PARTIAL_MUTATION, run_copier_update
from ai_cli.git_repair import _creator_env
from ai_cli.session import create_worktree


def _git(*args, cwd, check=True, env=None):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True, env=env)


# ---------------------------------------------------------------------------
# copier-update pre-clean (AC-1, AC-3)
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path):
    """A copier-managed repo under a projects dir, with no remote (base = HEAD)."""
    projects = tmp_path / "projects"
    repo = projects / "myproject"
    repo.mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "T", cwd=repo)
    (repo / ".copier-answers.yml").write_text(f"_src_path: {tmp_path / 'project-template'}\n_commit: previous\n")
    (repo / "tracked.txt").write_text("base\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "init", cwd=repo)
    return repo


def _leftover(repo: Path) -> Path:
    """Recreate the temp worktree a prior conflicted/pushfail run left behind."""
    wt = repo / ".worktrees" / "copier-update"
    wt.parent.mkdir(exist_ok=True)
    _git(
        "worktree",
        "add",
        "-q",
        str(wt),
        "-b",
        "copier-update-tmp",
        "HEAD",
        cwd=repo,
        env=_creator_env("ai-copier-update"),
    )
    return wt


def _run_update(repo: Path):
    """Run the real copier-update entry; the per-worktree copier step is stubbed."""
    calls = []

    def fake_update(wt_dir, root, copier_bin, push, resolved_source=None, inspect=False):
        calls.append(Path(wt_dir))
        return "nochange", ""

    with patch("ai_cli.copier_update.shutil.which", return_value="/usr/bin/copier"):
        with patch("ai_cli.copier_update._do_update_in_worktree", side_effect=fake_update):
            rc = run_copier_update(projects_dir=repo.parent, project_filter=repo.name, push=False)
    return rc, calls


def _branch_tip(repo: Path, branch: str) -> str:
    return _git("for-each-ref", "--format=%(objectname)", f"refs/heads/{branch}", cwd=repo).stdout.strip()


def _registered(repo: Path) -> str:
    return _git("worktree", "list", "--porcelain", cwd=repo).stdout


def _is_registered(repo: Path, path: Path) -> bool:
    """Compare parsed, resolved paths: git prints ``D:/...`` where ``str(path)`` gives ``D:\\...``."""
    listed = {
        Path(line[len("worktree ") :]).resolve()
        for line in _registered(repo).splitlines()
        if line.startswith("worktree ")
    }
    return path.resolve() in listed


def test_given_leftover_with_uncommitted_edit_when_copier_update_runs_then_it_is_kept_and_reported(project, capsys):
    wt = _leftover(project)
    (wt / "tracked.txt").write_text("<<<<<<< a human is resolving this\n")

    rc, calls = _run_update(project)

    assert (wt / "tracked.txt").read_text() == "<<<<<<< a human is resolving this\n"
    assert calls == [], "the update must not run over a kept leftover"
    out = capsys.readouterr().out
    assert str(wt) in out
    assert "uncommitted or staged changes" in out
    assert rc == EX_PARTIAL_MUTATION


def test_given_leftover_with_staged_change_when_copier_update_runs_then_it_is_kept(project, capsys):
    wt = _leftover(project)
    (wt / "resolved.txt").write_text("staged resolution\n")
    _git("add", "resolved.txt", cwd=wt)

    _run_update(project)

    assert _git("diff", "--cached", "--name-only", cwd=wt).stdout == "resolved.txt\n"
    assert "uncommitted or staged changes" in capsys.readouterr().out


def test_given_leftover_with_commit_not_on_base_when_copier_update_runs_then_it_is_kept(project, capsys):
    wt = _leftover(project)
    (wt / "tracked.txt").write_text("committed update awaiting a manual push\n")
    _git("commit", "-q", "-am", "chore: copier update from project-template", cwd=wt)
    tip = _git("rev-parse", "HEAD", cwd=wt).stdout.strip()

    _run_update(project)

    assert wt.is_dir()
    assert _branch_tip(project, "copier-update-tmp") == tip
    out = capsys.readouterr().out
    assert str(wt) in out
    assert "commit(s) not on" in out


def test_given_clean_leftover_with_merge_in_progress_when_copier_update_runs_then_it_is_kept(project, capsys):
    """A clean tree is not enough: a MERGE_HEAD alone marks an operation in flight."""
    wt = _leftover(project)
    git_dir = Path(_git("rev-parse", "--absolute-git-dir", cwd=wt).stdout.strip())
    (git_dir / "MERGE_HEAD").write_text(_git("rev-parse", "HEAD", cwd=project).stdout)
    assert _git("status", "--porcelain", cwd=wt).stdout == "", "precondition: the tree itself is clean"

    _run_update(project)

    assert (git_dir / "MERGE_HEAD").exists()
    assert wt.is_dir()
    assert "MERGE_HEAD" in capsys.readouterr().out


def test_given_branch_only_leftover_with_commits_when_copier_update_runs_then_branch_is_kept(project, capsys):
    """The worktree dir may be gone while its branch still holds the only copy of a commit."""
    _git("branch", "copier-update-tmp", cwd=project)
    _git("checkout", "-q", "copier-update-tmp", cwd=project)
    (project / "tracked.txt").write_text("branch-only work\n")
    _git("commit", "-q", "-am", "work", cwd=project)
    tip = _git("rev-parse", "HEAD", cwd=project).stdout.strip()
    _git("checkout", "-q", "main", cwd=project)

    rc, calls = _run_update(project)

    assert _branch_tip(project, "copier-update-tmp") == tip
    assert calls == []
    assert rc == EX_PARTIAL_MUTATION


def test_given_clean_leftover_at_base_when_copier_update_runs_then_it_is_cleared_and_update_proceeds(project, capsys):
    """Negative control for AC-1/AC-3: an interrupted run's clean worktree is still removed."""
    wt = _leftover(project)
    assert wt.is_dir(), "precondition: the leftover exists"

    rc, calls = _run_update(project)

    assert calls == [wt], "the update must run in a freshly created worktree"
    assert rc == 0
    assert "kept" not in capsys.readouterr().out.lower()
    assert not wt.exists(), "a nochange run tears its own worktree down"
    assert not _is_registered(project, wt)


def test_given_leftover_commit_since_pushed_elsewhere_when_copier_update_runs_then_it_is_cleared(project, tmp_path):
    """The base is fetched before the check, so a hand-pushed pushfail commit is on the base."""
    remote = tmp_path / "remote.git"
    _git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    _git("remote", "add", "origin", str(remote), cwd=project)
    _git("push", "-q", "origin", "main", cwd=project)
    _git("fetch", "-q", "origin", cwd=project)
    wt = _leftover(project)
    (wt / "tracked.txt").write_text("the update a push rejected\n")
    _git("commit", "-q", "-am", "chore: copier update from project-template", cwd=wt)
    _git("push", "-q", str(remote), "HEAD:main", cwd=wt)
    _git("update-ref", "refs/remotes/origin/main", "HEAD~1", cwd=wt)

    rc, calls = _run_update(project)

    assert calls == [wt]
    assert rc == 0


def test_given_leftover_registered_with_missing_dir_when_copier_update_runs_then_registration_is_kept(project, capsys):
    """AC-3: a checkout whose state cannot be read is kept, never deleted."""
    wt = _leftover(project)
    (wt / "resolved.txt").write_text("staged\n")
    _git("add", "resolved.txt", cwd=wt)
    wt.rename(wt.with_name("moved-away"))

    rc, calls = _run_update(project)

    assert _is_registered(project, wt), "the registration (and its index) must survive"
    assert calls == []
    out = capsys.readouterr().out
    assert str(wt) in out
    assert rc == EX_PARTIAL_MUTATION


def test_given_unregistered_directory_at_leftover_path_when_copier_update_runs_then_it_is_kept(project, capsys):
    """AC-3: a directory git does not recognise cannot be evaluated, so it is not removed."""
    wt = project / ".worktrees" / "copier-update"
    wt.mkdir(parents=True)
    (wt / "notes.txt").write_text("not a worktree\n")

    _, calls = _run_update(project)

    assert (wt / "notes.txt").read_text() == "not a worktree\n"
    assert calls == []
    assert str(wt) in capsys.readouterr().out


def test_given_status_read_times_out_when_copier_update_runs_then_leftover_is_kept(project, capsys):
    """AC-3: a git timeout while reading the leftover's state means keep."""
    wt = _leftover(project)
    real_run = subprocess.run
    removals = []

    def run(command, **kwargs):
        if "status" in command and str(wt) in command:
            raise subprocess.TimeoutExpired(command, kwargs.get("timeout") or 0)
        if "worktree" in command and "remove" in command:
            removals.append(command)
        return real_run(command, **kwargs)

    with patch.object(copier_update.subprocess, "run", side_effect=run):
        _, calls = _run_update(project)

    assert removals == []
    assert wt.is_dir()
    assert calls == []
    assert "could not read" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# ai c worktree slot (AC-2, AC-3)
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A clone of its own bare remote, so ``origin/main`` exists for the slot base."""
    remote = tmp_path / "remote.git"
    _git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git("init", "-q", "-b", "main", cwd=seed)
    _git("config", "user.email", "t@example.com", cwd=seed)
    _git("config", "user.name", "T", cwd=seed)
    (seed / "README.md").write_text("seed\n")
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "init", cwd=seed)
    _git("push", "-q", str(remote), "main", cwd=seed)
    checkout = tmp_path / "myproject"
    _git("clone", "-q", str(remote), str(checkout), cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=checkout)
    _git("config", "user.name", "T", cwd=checkout)
    monkeypatch.chdir(checkout)
    with patch("ai_cli.trust.ensure_workspace_trusted"):
        yield checkout


def _stale_registration(repo: Path, leaf: str, branch: str) -> Path:
    """A registered worktree whose directory has gone, with staged work in its index."""
    wt = repo / ".worktrees" / leaf
    _git("worktree", "add", "-q", str(wt), "-b", branch, "origin/main", cwd=repo)
    (wt / "staged.txt").write_text("staged in the index only\n")
    _git("add", "staged.txt", cwd=wt)
    wt.rename(repo.parent / f"{leaf}-moved-away")
    return wt


def test_given_another_slots_stale_registration_when_ai_c_launches_then_it_survives(repo):
    other = _stale_registration(repo, "session-9", "wt-session-9")
    assert "prunable" in _registered(repo), "precondition: the other slot is prunable"

    slot = create_worktree("session-1")

    assert slot == repo / ".worktrees" / "session-1"
    assert _is_registered(repo, other), "another slot's registration must not be pruned"


def test_given_this_slots_stale_registration_when_ai_c_launches_then_only_it_is_cleared(repo):
    """Negative control for AC-2: this slot's own stale entry is still cleared."""
    _stale_registration(repo, "session-1", "wt-session-1")
    other = _stale_registration(repo, "session-9", "wt-session-9")

    slot = create_worktree("session-1")

    assert slot == repo / ".worktrees" / "session-1"
    assert slot.is_dir()
    assert _is_registered(repo, slot)
    assert _is_registered(repo, other)


def test_given_this_slots_branch_held_by_stale_registration_elsewhere_when_ai_c_launches_then_it_is_cleared(repo):
    """The old prune also freed this slot's own branch from a vanished checkout."""
    stale = _stale_registration(repo, "session-1r", "wt-session-1")
    other = _stale_registration(repo, "session-9", "wt-session-9")

    slot = create_worktree("session-1")

    assert slot == repo / ".worktrees" / "session-1"
    assert not _is_registered(repo, stale)
    assert _is_registered(repo, other)


def test_given_worktree_listing_fails_when_ai_c_launches_then_no_registration_is_removed(repo):
    """AC-3: when the registrations cannot be read, nothing is pruned or removed."""
    _stale_registration(repo, "session-1", "wt-session-1")
    real_run = subprocess.run
    removals = []

    def run(command, *args, **kwargs):
        if command[:3] == ["git", "worktree", "list"]:
            return subprocess.CompletedProcess(command, 128, "", "fatal: simulated read failure")
        if command[:3] in (["git", "worktree", "prune"], ["git", "worktree", "remove"]):
            removals.append(command)
        return real_run(command, *args, **kwargs)

    with patch("subprocess.run", side_effect=run):
        with pytest.raises(RuntimeError):
            create_worktree("session-1")

    assert removals == []
    assert _is_registered(repo, repo / ".worktrees" / "session-1")
