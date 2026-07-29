"""A session worktree must be based on ``origin/main``, not on whatever HEAD holds.

``create_worktree`` sets every worktree branch's upstream to ``origin/main``
(AI-CLI-128) but used to create the branch with no start-point, so git branched it
from the main tree's current HEAD. In a repo whose main tree sits on a long-running
workspace branch, base and upstream then disagree: the new branch inherits every
commit on that workspace branch while claiming to track ``origin/main``.

Two consequences, both invisible at launch time:

* The branch is not PR-clean. Promoting it opens a pull request containing unrelated
  commits — the exact scope leak per-session worktrees exist to prevent.
* ``git pull --rebase`` in the worktree tries to replay those inherited commits onto
  ``origin/main``. An unreachable or unauthenticated remote masks this: the pull fails
  before the rebase, so the only symptom is a sync warning. Repair the remote without
  repairing the base and the warning is replaced by a surprising rebase.

See ``docs/bugs/worktree-base-not-origin-main.md``.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_cli.session import create_worktree


def _git(*args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def _out(*args, cwd):
    return _git(*args, cwd=cwd).stdout.strip()


@pytest.fixture
def repo_on_workspace_branch(tmp_path):
    """A clone whose checked-out branch is NOT ``main`` and has diverged from it.

    Mirrors the reported layout: the main working tree sits on a long-running
    workspace branch carrying commits that never landed on ``main``, while
    ``origin/main`` has meanwhile moved on independently.
    """
    remote = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)

    repo = tmp_path / "myproject"
    seed = tmp_path / "seed"
    seed.mkdir()
    _git("init", "-q", "-b", "main", cwd=seed)
    _git("config", "user.email", "t@example.com", cwd=seed)
    _git("config", "user.name", "T", cwd=seed)
    (seed / "README.md").write_text("base\n")
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "shared base", cwd=seed)
    _git("push", "-q", str(remote), "main", cwd=seed)

    # Workspace branch forks from that base and gains its own commits.
    _git("checkout", "-q", "-b", "user/dev-workspace", cwd=seed)
    (seed / "workspace-notes.md").write_text("workspace only\n")
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "workspace only commit", cwd=seed)
    _git("push", "-q", str(remote), "user/dev-workspace", cwd=seed)

    # main advances separately, so the two are genuinely divergent.
    _git("checkout", "-q", "main", cwd=seed)
    (seed / "shipped.md").write_text("landed on main\n")
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "landed on main", cwd=seed)
    _git("push", "-q", str(remote), "main", cwd=seed)

    _git("clone", "-q", str(remote), str(repo), cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "T", cwd=repo)
    _git("checkout", "-q", "-b", "user/dev-workspace", "origin/user/dev-workspace", cwd=repo)
    return repo


def test_given_main_tree_on_workspace_branch_when_worktree_created_then_based_on_origin_main(
    repo_on_workspace_branch, monkeypatch, tmp_path
):
    """The regression: the new branch must start at ``origin/main``, not at HEAD."""
    repo = repo_on_workspace_branch
    monkeypatch.chdir(repo)
    assert _out("rev-parse", "--abbrev-ref", "HEAD", cwd=repo) == "user/dev-workspace"

    with patch("ai_cli.trust.ensure_workspace_trusted"):
        worktree = create_worktree("session-1")

    assert worktree is not None and worktree.is_dir()

    # Positive: the branch carries nothing that origin/main does not already have.
    ahead = _out("rev-list", "--count", "origin/main..wt-session-1", cwd=repo)
    assert ahead == "0", f"wt-session-1 must add no commits to origin/main, but is {ahead} ahead"
    assert _out("rev-parse", "wt-session-1", cwd=repo) == _out("rev-parse", "origin/main", cwd=repo)

    # Negative: nothing from the workspace branch leaked in. A base of HEAD would
    # have brought this commit — and its file — along.
    subjects = _out("log", "--format=%s", "wt-session-1", cwd=repo).splitlines()
    assert "workspace only commit" not in subjects, f"workspace-branch history leaked in: {subjects}"
    assert not (worktree / "workspace-notes.md").exists(), "workspace-only file leaked into the worktree"

    # And the checked-out worktree really is at that base, not merely the ref.
    assert _out("rev-parse", "HEAD", cwd=worktree) == _out("rev-parse", "origin/main", cwd=repo)
    assert (worktree / "shipped.md").exists(), "the worktree must contain what landed on main"

    # Upstream tracking (AI-CLI-128) is unchanged by this.
    assert _out("rev-parse", "--abbrev-ref", "--symbolic-full-name", "wt-session-1@{u}", cwd=repo) == "origin/main"


def test_given_main_tree_on_workspace_branch_when_worktree_created_then_that_branch_is_untouched(
    repo_on_workspace_branch, monkeypatch
):
    """Basing elsewhere must not move or rewrite the branch the main tree is on."""
    repo = repo_on_workspace_branch
    before = _out("rev-parse", "user/dev-workspace", cwd=repo)
    monkeypatch.chdir(repo)

    with patch("ai_cli.trust.ensure_workspace_trusted"):
        create_worktree("session-1")

    assert _out("rev-parse", "user/dev-workspace", cwd=repo) == before
    assert _out("rev-parse", "--abbrev-ref", "HEAD", cwd=repo) == "user/dev-workspace"


def test_given_no_origin_main_when_worktree_created_then_raises_instead_of_basing_on_head(tmp_path, monkeypatch):
    """No ``origin/main`` is a hard failure, never a silent fallback to HEAD.

    Consistent with AI-CLI-128's upstream hard-fail: a worktree branch that cannot be
    anchored to ``main`` is worse than no worktree, because the drift only shows up at
    push or PR time.
    """
    repo = tmp_path / "myproject"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "T", cwd=repo)
    (repo / "README.md").write_text("hi\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "init", cwd=repo)
    monkeypatch.chdir(repo)

    with patch("ai_cli.trust.ensure_workspace_trusted"), pytest.raises(RuntimeError, match="origin/main"):
        create_worktree("session-1")

    assert not (repo / ".worktrees" / "session-1").exists(), "no worktree may be left behind on an unresolvable base"


def test_given_existing_worktree_branch_when_worktree_recreated_then_its_history_is_preserved(
    repo_on_workspace_branch, monkeypatch
):
    """Re-creating a removed worktree directory must reuse the branch, not rebase it.

    The resume path checks out an existing ``wt-<name>``; forcing a start-point there
    would silently discard commits the previous session made.
    """
    repo = repo_on_workspace_branch
    monkeypatch.chdir(repo)

    with patch("ai_cli.trust.ensure_workspace_trusted"):
        worktree = create_worktree("session-1")

    (worktree / "session-work.md").write_text("work from the session\n")
    _git("add", "-A", cwd=worktree)
    _git("commit", "-q", "-m", "session work", cwd=worktree)
    session_tip = _out("rev-parse", "wt-session-1", cwd=repo)

    _git("worktree", "remove", "--force", str(worktree), cwd=repo)
    assert not worktree.exists()

    with patch("ai_cli.trust.ensure_workspace_trusted"):
        again = create_worktree("session-1")

    assert again == worktree
    assert _out("rev-parse", "wt-session-1", cwd=repo) == session_tip
    assert (Path(again) / "session-work.md").exists(), "the session's own commit must survive re-creation"
