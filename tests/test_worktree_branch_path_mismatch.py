"""The session's branch checked out at the wrong path used to dead-end the launch.

``create_worktree`` had exactly two strategies, and both are impossible in this
state:

* ``git worktree add <slot> -b wt-<name> <base>`` fails because the branch exists;
* ``git worktree add <slot> wt-<name>`` fails because git permits one checkout per
  branch, and something else already holds it.

The slot itself is unregistered, so the reuse probe finds nothing either, and the
launch raised. There is no user action that reaches a working state through the
launcher, and the escape hatch its refusal advertised — ``-W/--no-worktree`` —
lands in the repository root, the exact outcome the same message refuses.

The state is not exotic; the launcher produces it. A session worktree directory
lost while its git registration survives, or relocated by hand, leaves the branch
attached to a path that is not the slot. The wanted checkout already exists and is
merely in the wrong place, so the repair is a **move**: ``git worktree move``
relocates the checkout and rewrites the registration together, preserving
uncommitted changes, unpushed commits and HEAD.

These tests use real repositories because the whole defect lives in what git
actually permits.
"""

import subprocess
from pathlib import Path

import pytest

from ai_cli.session import _worktree_holding_branch, create_worktree, registered_worktrees


def _git(*args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A clone of a bare remote, so ``origin/main`` exists.

    ``create_worktree`` refuses to base a session worktree on anything but a
    branch that exists on a remote, so a plain ``git init`` is not a repository it
    will accept.
    """
    remote = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)

    seed = tmp_path / "seed"
    seed.mkdir()
    _git("init", "-q", "-b", "main", cwd=seed)
    _git("config", "user.email", "t@example.com", cwd=seed)
    _git("config", "user.name", "T", cwd=seed)
    (seed / "README.md").write_text("base\n")
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "init", cwd=seed)
    _git("push", "-q", str(remote), "main", cwd=seed)

    checkout = tmp_path / "myproject"
    _git("clone", "-q", str(remote), str(checkout), cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=checkout)
    _git("config", "user.name", "T", cwd=checkout)
    return checkout


def _misplaced_session_worktree(repo: Path, name: str, wrong_leaf: str) -> Path:
    """Put ``wt-<name>`` at a path that is not ``name``'s slot, holding real work.

    Both kinds of loss-bearing state are present — an unpushed commit and an
    uncommitted edit — because a repair that silently recreated the worktree from
    the integration branch would still leave a directory and a branch behind, and
    only the content proves the original was carried across.
    """
    wrong = repo / ".worktrees" / wrong_leaf
    _git("worktree", "add", "-q", str(wrong), "-b", f"wt-{name}", "origin/main", cwd=repo)
    (wrong / "committed.md").write_text("a commit that exists nowhere else\n")
    _git("add", "-A", cwd=wrong)
    _git("commit", "-q", "-m", "unpushed session work", cwd=wrong)
    (wrong / "dirty.md").write_text("an uncommitted edit\n")
    return wrong


def test_given_the_branch_held_at_another_path_when_created_then_it_is_relocated_to_the_slot(repo, monkeypatch):
    """The repair: the launch succeeds, at the canonical slot, with the work intact."""
    monkeypatch.chdir(repo)
    wrong = _misplaced_session_worktree(repo, "session-1", "session-1r")
    slot = repo / ".worktrees" / "session-1"
    head_before = _git("rev-parse", "HEAD", cwd=wrong).stdout.strip()
    assert not slot.exists(), "precondition: the canonical slot is absent"

    created = create_worktree("session-1")

    assert created == slot
    assert slot in registered_worktrees(repo), "git must report a worktree at the slot"
    assert not wrong.exists(), "the misplaced directory must not be left behind as a duplicate"
    assert (slot / "committed.md").read_text() == "a commit that exists nowhere else\n"
    assert (slot / "dirty.md").read_text() == "an uncommitted edit\n", "uncommitted work must survive"
    assert _git("rev-parse", "HEAD", cwd=slot).stdout.strip() == head_before, "the unpushed commit must survive"
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=slot).stdout.strip() == "wt-session-1"


def test_given_the_branch_held_at_another_path_when_created_then_git_registration_follows_the_move(repo, monkeypatch):
    """A plain ``mv`` would leave git pointing at the old path; this must not.

    The discriminating probe is ``git worktree prune --dry-run``: it names any
    registration whose directory is gone, so a relocation that moved only the
    files reports the old path here while the assertions above still pass.
    """
    monkeypatch.chdir(repo)
    wrong = _misplaced_session_worktree(repo, "session-2", "session-2r")

    create_worktree("session-2")

    prune = _git("worktree", "prune", "--dry-run", cwd=repo).stdout
    assert prune.strip() == "", f"no stale registration may remain, got {prune!r}"
    assert wrong not in registered_worktrees(repo)
    assert _git("status", "--porcelain", cwd=repo / ".worktrees" / "session-2").stdout == "?? dirty.md\n"


def test_given_an_empty_slot_and_the_branch_held_elsewhere_when_created_then_it_is_still_relocated(repo, monkeypatch):
    """``git worktree move`` needs an absent destination, so an empty slot is cleared.

    Distinct from the absent-slot case: the launcher must remove the empty
    directory itself rather than refusing over a path holding nothing.
    """
    monkeypatch.chdir(repo)
    _misplaced_session_worktree(repo, "session-3", "session-3r")
    slot = repo / ".worktrees" / "session-3"
    slot.mkdir()

    created = create_worktree("session-3")

    assert created == slot
    assert (slot / "committed.md").read_text() == "a commit that exists nowhere else\n"


def test_given_a_live_session_in_the_misplaced_worktree_when_created_then_it_refuses_and_moves_nothing(
    repo, monkeypatch
):
    """Failure path: never relocate a checkout an engine is still running in.

    Moving it under a live process leaves that session writing through a stale
    path, so the only correct behaviour is to refuse — and the refusal must name
    the command that finishes the job once the session exits.
    """
    monkeypatch.chdir(repo)
    wrong = _misplaced_session_worktree(repo, "session-4", "session-4r")
    slot = repo / ".worktrees" / "session-4"
    monkeypatch.setattr("ai_cli.session._worktree_has_live_session", lambda path: path == wrong)

    with pytest.raises(RuntimeError) as caught:
        create_worktree("session-4")

    message = str(caught.value)
    assert f"worktree move {wrong} {slot}" in message, (
        f"the refusal must hand over a working next step, got {message!r}"
    )
    assert wrong.is_dir(), "the live worktree must survive untouched"
    assert (wrong / "dirty.md").read_text() == "an uncommitted edit\n"
    assert not slot.exists(), "nothing may be created at the slot"


def test_given_a_locked_misplaced_worktree_when_created_then_it_refuses_with_gits_reason_and_the_command(repo):
    """Failure path: a move git itself rejects must not be reported as a launch.

    ``git worktree lock`` is a real refusal rather than a mocked one — git declines
    to move a locked working tree — so this exercises the launcher's handling of a
    non-zero ``git worktree move`` without replacing any part of the code under
    test.
    """
    wrong = _misplaced_session_worktree(repo, "session-5", "session-5r")
    slot = repo / ".worktrees" / "session-5"
    _git("worktree", "lock", str(wrong), "--reason", "held by hand", cwd=repo)

    with pytest.raises(RuntimeError) as caught:
        create_worktree("session-5", repo_root=repo)

    message = str(caught.value)
    assert "locked" in message.lower(), f"git's own reason must reach the user, got {message!r}"
    assert f"worktree move {wrong} {slot}" in message
    assert wrong.is_dir()
    assert (wrong / "dirty.md").read_text() == "an uncommitted edit\n"
    assert not slot.exists()


def test_given_a_branch_no_worktree_holds_when_probed_then_no_holder_is_reported(repo, monkeypatch):
    """Negative control for the probe the whole repair depends on.

    Without this, ``_worktree_holding_branch`` could return the first worktree it
    parses regardless of branch, and every test above would still pass while the
    launcher relocated arbitrary worktrees.
    """
    monkeypatch.chdir(repo)
    wrong = _misplaced_session_worktree(repo, "session-6", "session-6r")

    assert _worktree_holding_branch(repo, "wt-session-6") == wrong
    assert _worktree_holding_branch(repo, "wt-session-nobody") is None
    assert _worktree_holding_branch(repo, "wt-session-6-suffix") is None, "matching must not be a prefix test"


def test_given_a_detached_worktree_when_probed_then_it_is_not_reported_as_holding_a_branch(repo, monkeypatch):
    """Porcelain emits ``detached`` instead of ``branch``, and pairing must respect it.

    A parser that carried the previous record's ``branch`` line forward would
    attribute the session's branch to the detached worktree that follows it.
    """
    monkeypatch.chdir(repo)
    held = _misplaced_session_worktree(repo, "session-7", "session-7r")
    detached = repo / ".worktrees" / "detached-leaf"
    _git("worktree", "add", "-q", "--detach", str(detached), cwd=repo)

    assert _worktree_holding_branch(repo, "wt-session-7") == held
    assert _worktree_holding_branch(repo, "HEAD") is None
