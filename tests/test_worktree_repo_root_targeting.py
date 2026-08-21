"""``create_worktree(repo_root=...)`` must add the worktree to THAT repository.

The function takes an explicit ``repo_root`` and honours it everywhere it asks git
a question — ``git worktree prune``, ``git worktree list``, branch resolution all
run with ``cwd=repo_root``. The two ``git worktree add`` calls did not, so the one
subprocess that *writes* fell back to the process's current directory and added
the worktree to whichever repository the caller happened to be standing in.

Nothing catches this from the outside. git creates the checkout at the requested
path, so the directory looks right and holds real files; only the registration and
the branch land in the wrong repository. The launch then dies further along, at
``_set_upstream_or_raise``, with ``fatal: branch 'wt-<name>' does not exist`` — a
message about a branch, several steps away from the repository mix-up that caused
it.

``ai c`` never hit this because it launches from the repository root, where cwd and
``repo_root`` agree. ``ai session-adopt`` is the exposed caller: it resolves
``repo_root`` from a recorded transcript and passes it explicitly, so it can be run
from anywhere — including another repository.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_cli.session import create_worktree, registered_worktrees


def _git(*args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def _clone_with_remote(tmp_path: Path, name: str) -> Path:
    """A clone of its own bare remote, so ``origin/main`` exists."""
    remote = tmp_path / f"{name}.git"
    _git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)

    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    _git("init", "-q", "-b", "main", cwd=seed)
    _git("config", "user.email", "t@example.com", cwd=seed)
    _git("config", "user.name", "T", cwd=seed)
    (seed / f"{name}.md").write_text(f"{name} content\n")
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "init", cwd=seed)
    _git("push", "-q", str(remote), "main", cwd=seed)

    checkout = tmp_path / name
    _git("clone", "-q", str(remote), str(checkout), cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=checkout)
    _git("config", "user.name", "T", cwd=checkout)
    return checkout


@pytest.fixture
def two_repos(tmp_path):
    """A repository to create the worktree in, and an unrelated one to stand in."""
    return _clone_with_remote(tmp_path, "myproject"), _clone_with_remote(tmp_path, "otherproject")


def test_given_cwd_in_another_repository_when_created_with_repo_root_then_the_worktree_is_added_to_repo_root(
    two_repos, monkeypatch
):
    """The explicit ``repo_root`` decides which repository gains the worktree.

    Asserted on registration and branch rather than on the directory existing: the
    checkout is written to the requested path either way, so ``is_dir()`` passes on
    the buggy code too and would not discriminate.
    """
    target, bystander = two_repos
    monkeypatch.chdir(bystander)

    with patch("ai_cli.trust.ensure_workspace_trusted"):
        worktree = create_worktree("session-1", repo_root=target)

    assert worktree is not None
    assert worktree == target / ".worktrees" / "session-1"
    assert worktree in registered_worktrees(target), "the target repository must own the new worktree"
    assert _git("rev-parse", "--verify", "wt-session-1", cwd=target, check=False).returncode == 0
    assert (worktree / "myproject.md").is_file(), "the target repository's content must be checked out"


def test_given_cwd_in_another_repository_when_created_with_repo_root_then_that_repository_is_left_untouched(
    two_repos, monkeypatch
):
    """The negative half: standing in a repository must not enlist it.

    This is the assertion the defect actually failed. A worktree registered in the
    bystander pointing at a path inside another project is cross-repository debris
    that no later launch of either project can see or clean up.
    """
    target, bystander = two_repos
    monkeypatch.chdir(bystander)
    before = registered_worktrees(bystander)

    with patch("ai_cli.trust.ensure_workspace_trusted"):
        create_worktree("session-1", repo_root=target)

    assert registered_worktrees(bystander) == before, "the repository the caller stood in gained a worktree"
    assert _git("rev-parse", "--verify", "wt-session-1", cwd=bystander, check=False).returncode != 0, (
        "the session branch was created in the wrong repository"
    )
