"""``.worktrees/<name>`` means two incompatible things, and one of them deletes work.

``ai c <name>`` treats ``.worktrees/<name>`` as that session's own git checkout.
Other tooling — anything that gives an agent its own worktree per task — uses the
same path as a *container*, spelled ``.worktrees/<name>/<task>/<leaf>``. A session
launched from a repository root therefore finds its container sitting exactly
where its own checkout must go, and the two readings of that path collide.

Two defects met there, and masked each other:

* the registration check was a **substring** test against
  ``git worktree list --porcelain``, and ``…/.worktrees/session-1`` is a substring
  of the line ``worktree …/.worktrees/session-1/agent-a`` — so a container with no
  checkout of its own reported as a registered worktree;
* the branch taken when that test fails is an unconditional
  ``shutil.rmtree`` — which, reached with a container in place, deletes every
  nested worktree and any commit in it that was never pushed.

The safe outcome was luck: the substring matched, so the deletion was never
reached. Fixing only the substring test would have armed the deletion. These
tests pin both halves against real repositories, because the whole defect lives
in what git actually prints.
"""

import subprocess
from pathlib import Path

import pytest

from ai_cli.session import create_worktree, registered_worktrees


def _git(*args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A clone of a bare remote, so ``origin/main`` exists.

    ``create_worktree`` refuses to base a session worktree on anything but a
    branch that exists on a remote, so a plain ``git init`` is not a repository
    it will accept.
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


def _nested_worktree_with_unpushed_work(repo: Path, container: str, leaf: str) -> Path:
    """Register ``.worktrees/<container>/<leaf>`` and give it a commit only it has.

    The unpushed commit is what makes a deletion unrecoverable, so it is what the
    assertions look for: a directory that still exists but lost its history would
    pass a mere ``is_dir()`` check.
    """
    path = repo / ".worktrees" / container / leaf
    _git("worktree", "add", "-q", "--detach", str(path), cwd=repo)
    (path / "agent-work.md").write_text("work that only exists here\n")
    _git("add", "-A", cwd=path)
    _git("commit", "-q", "-m", "unpushed agent work", cwd=path)
    return path


def test_given_only_a_child_worktree_is_registered_when_the_parent_is_listed_then_the_parent_is_absent(repo):
    """The parent of a registered worktree must not itself read as registered.

    The discriminating probe: ``<parent>`` is a substring of the porcelain line for
    ``<parent>/<leaf>``, so a substring test answers "registered" for both paths
    and cannot tell them apart. An exact-line test answers for the leaf only. If
    the parent were genuinely registered this test would print it in the
    ``registered`` list and fail on the second assertion.
    """
    leaf = _nested_worktree_with_unpushed_work(repo, "session-1", "agent-a")
    container = repo / ".worktrees" / "session-1"

    registered = registered_worktrees(repo)

    assert leaf in registered, "the leaf worktree really is registered"
    assert container not in registered, f"the container is not a worktree, got {registered}"
    assert not (container / ".git").exists(), "the container has no checkout of its own"


def test_given_a_path_that_is_not_a_repository_when_worktrees_are_listed_then_none_are_reported(tmp_path):
    """A failed ``git worktree list`` reports nothing, rather than raising.

    The caller then falls through to the checkout guard, so "git could not answer"
    still cannot become "safe to delete". If this returned the porcelain output of
    some *other* repository — which is what happens when git resolves an unexpected
    directory — the list would be non-empty and this would fail.
    """
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()

    assert registered_worktrees(not_a_repo) == []


def test_given_an_unregistered_clone_in_the_session_slot_when_created_then_it_is_refused(repo, monkeypatch):
    """The slot itself being a checkout git does not know about must also refuse.

    Distinct from the nested case: here ``.git`` is directly in the slot, so the
    recursive scan never runs. A clone made by hand into that path is invisible to
    ``git worktree list`` and would otherwise be deleted outright.
    """
    monkeypatch.chdir(repo)
    slot = repo / ".worktrees" / "session-5"
    slot.mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=slot)
    _git("config", "user.email", "t@example.com", cwd=slot)
    _git("config", "user.name", "T", cwd=slot)
    (slot / "notes.md").write_text("hand-made clone\n")
    _git("add", "-A", cwd=slot)
    _git("commit", "-q", "-m", "by hand", cwd=slot)

    with pytest.raises(RuntimeError, match="refusing to delete"):
        create_worktree("session-5")

    assert (slot / "notes.md").read_text() == "hand-made clone\n"
    assert (slot / ".git").is_dir()


def test_given_a_container_of_nested_worktrees_when_a_session_worktree_is_created_then_it_is_refused(repo, monkeypatch):
    """A container in the session's slot must stop the launch, not be deleted.

    Relocating a nested worktree moves a human's unpushed work, so it is a human
    decision; the only correct automatic behaviour is to refuse and say what
    collided.
    """
    monkeypatch.chdir(repo)
    leaf = _nested_worktree_with_unpushed_work(repo, "session-1", "agent-a")
    before = _git("rev-parse", "HEAD", cwd=leaf).stdout.strip()

    with pytest.raises(RuntimeError, match="refusing to delete"):
        create_worktree("session-1")

    assert leaf.is_dir(), "the nested worktree must survive"
    assert (leaf / "agent-work.md").read_text() == "work that only exists here\n"
    assert _git("rev-parse", "HEAD", cwd=leaf).stdout.strip() == before, "its unpushed commit must survive"


def test_given_a_container_of_nested_worktrees_when_a_session_worktree_is_created_then_the_error_names_the_collision(
    repo, monkeypatch
):
    """The refusal has to be actionable: which path, and what to do about it."""
    monkeypatch.chdir(repo)
    _nested_worktree_with_unpushed_work(repo, "session-1", "agent-a")

    with pytest.raises(RuntimeError) as caught:
        create_worktree("session-1")

    message = str(caught.value)
    assert str(repo / ".worktrees" / "session-1") in message
    assert str(repo / ".worktrees" / "session-1" / "agent-a") in message
    assert "git worktree move" in message


def test_given_a_directory_holding_an_unregistered_checkout_when_a_session_worktree_is_created_then_it_is_refused(
    repo, monkeypatch
):
    """Any git checkout under the slot blocks the deletion, registered or not.

    A clone that this repository knows nothing about — a vendored dependency, a
    checkout someone made by hand — is not in ``git worktree list`` at all, so the
    registration check cannot see it. It still holds commits, so it still must not
    be deleted.
    """
    monkeypatch.chdir(repo)
    container = repo / ".worktrees" / "session-2"
    inner = container / "unrelated-clone"
    inner.mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=inner)
    _git("config", "user.email", "t@example.com", cwd=inner)
    _git("config", "user.name", "T", cwd=inner)
    (inner / "notes.md").write_text("independent history\n")
    _git("add", "-A", cwd=inner)
    _git("commit", "-q", "-m", "independent", cwd=inner)

    with pytest.raises(RuntimeError, match="refusing to delete"):
        create_worktree("session-2")

    assert (inner / ".git").is_dir(), "the unrelated repository must survive"
    assert (inner / "notes.md").read_text() == "independent history\n"


def test_given_an_unregistered_directory_when_a_session_worktree_is_created_then_it_is_refused(repo, monkeypatch):
    """A non-empty unregistered slot is never automatically recycled."""
    monkeypatch.chdir(repo)
    stale = repo / ".worktrees" / "session-3"
    stale.mkdir(parents=True)
    (stale / "leftover.txt").write_text("debris from an interrupted run\n")

    with pytest.raises(RuntimeError, match="refusing to delete"):
        create_worktree("session-3")

    assert (stale / "leftover.txt").read_text() == "debris from an interrupted run\n"


@pytest.mark.parametrize("state", ["uncommitted", "unpushed", "absent-from-integration"])
def test_given_case_differing_registered_worktree_with_work_when_created_then_it_is_reused(repo, monkeypatch, state):
    """A case-insensitive spelling of a live worktree must never be recycled.

    The identity probe is mocked because CI filesystems may be case-sensitive;
    the lower-case on-disk worktree and upper-case requested prefix reproduce the
    casing mismatch that case-insensitive filesystems collapse to one directory.
    """
    monkeypatch.chdir(repo)
    lower = create_worktree("session-6")
    assert lower is not None
    marker = lower / "in-progress.md"
    marker.write_text(f"{state}\n")
    if state != "uncommitted":
        _git("add", "in-progress.md", cwd=lower)
        _git("commit", "-q", "-m", state, cwd=lower)

    monkeypatch.setattr(
        "ai_cli.session._same_worktree_path",
        lambda requested, registered: requested.name.casefold() == registered.name.casefold(),
    )

    reused = create_worktree("SESSION-6")

    assert reused == lower
    assert marker.read_text() == f"{state}\n"


def test_given_a_registered_session_worktree_when_created_again_then_it_is_returned_untouched(repo, monkeypatch):
    """Control for the exact-match path: a real worktree is still reused as-is."""
    monkeypatch.chdir(repo)
    first = create_worktree("session-4")
    assert first is not None
    sentinel = first / "in-progress.txt"
    sentinel.write_text("uncommitted\n")

    again = create_worktree("session-4")

    assert again == first
    assert sentinel.read_text() == "uncommitted\n"
