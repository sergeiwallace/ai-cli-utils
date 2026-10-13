"""``rename_branch_anchored`` never renames a branch without a durable anchor at its tip."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ai_cli.branch_rename import rename_branch_anchored

ANCHOR = "refs/preserved/session-migrate/wt-session-1"


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _ref(repo: Path, ref: str) -> str | None:
    res = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref], cwd=repo, capture_output=True, text=True, check=False
    )
    return res.stdout.strip() if res.returncode == 0 else None


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "myrepo"
    root.mkdir()
    _git("init", "-q", "-b", "main", cwd=root)
    _git("config", "user.email", "t@example.com", cwd=root)
    _git("config", "user.name", "T", cwd=root)
    (root / "a.txt").write_text("a\n")
    _git("add", "-A", cwd=root)
    _git("commit", "-qm", "init", cwd=root)
    _git("branch", "wt-session-1", cwd=root)
    _git("pack-refs", "--all", cwd=root)
    return root


def test_given_a_packed_branch_when_renamed_then_the_anchor_and_new_branch_hold_the_old_tip(repo):
    tip = _ref(repo, "refs/heads/wt-session-1")
    result = rename_branch_anchored(repo, "wt-session-1", "wt-session-2")
    assert result.ok, result.error
    assert _ref(repo, "refs/heads/wt-session-2") == tip
    assert _ref(repo, ANCHOR) == tip
    assert _ref(repo, "refs/heads/wt-session-1") is None


def test_given_a_dry_run_when_renamed_then_no_ref_changes(repo):
    before = _git("for-each-ref", cwd=repo)
    result = rename_branch_anchored(repo, "wt-session-1", "wt-session-2", dry_run=True)
    assert result.ok and result.tip
    assert _git("for-each-ref", cwd=repo) == before


def test_given_the_target_exists_when_renamed_then_nothing_is_written(repo):
    _git("branch", "wt-session-2", cwd=repo)
    result = rename_branch_anchored(repo, "wt-session-1", "wt-session-2")
    assert result.error == "branch wt-session-2 already exists"
    assert _ref(repo, ANCHOR) is None
    assert _ref(repo, "refs/heads/wt-session-1") is not None


def test_given_git_refuses_the_rename_when_renamed_then_stderr_is_quoted_and_the_anchor_remains(repo):
    _git("branch", "wt-session-2/child", cwd=repo)
    result = rename_branch_anchored(repo, "wt-session-1", "wt-session-2")
    assert result.error is not None and "git branch -m wt-session-1 wt-session-2 was refused:" in result.error
    assert "wt-session-2" in result.error.split("refused:", 1)[1]
    assert _ref(repo, ANCHOR) == _ref(repo, "refs/heads/wt-session-1")


def test_given_an_anchor_at_another_commit_when_renamed_then_it_is_kept_and_the_rename_refused(repo):
    _git("commit", "-q", "--allow-empty", "-m", "later", cwd=repo)
    later = _ref(repo, "HEAD")
    _git("update-ref", ANCHOR, later, cwd=repo)
    result = rename_branch_anchored(repo, "wt-session-1", "wt-session-2")
    assert result.error is not None and "already points at" in result.error
    assert _ref(repo, ANCHOR) == later
    assert _ref(repo, "refs/heads/wt-session-2") is None
