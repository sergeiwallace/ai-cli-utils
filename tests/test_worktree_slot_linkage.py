"""A session's own checkout must be recognised even when git's linkage names another path.

A linked worktree is held together by two path records: ``<repo>/.git/worktrees/<id>/gitdir``
names the checkout, and the checkout's ``.git`` file names that admin directory. When either
record spells the path differently from where the checkout really is (a repository moved
or migrated, a Windows path written as ``/c/Users/...`` by one git and read as
``C:\\Users\\...`` by another), ``git worktree list`` reports the slot at the other spelling and
marks it ``prunable``. The launcher then removed that registration as stale because it held
the slot's branch, and refused its own checkout as "not a worktree", advising the user to
move the session's checkout into ``<name>-agents/``.

These tests drive the real ``create_worktree`` against real repositories. A path that does
not exist stands in for the other spelling, which is what a Windows-form path is to the
reader that cannot resolve it.
"""

import subprocess
from pathlib import Path

import pytest

from ai_cli.session import create_worktree, registered_worktrees


def _git(*args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, register_repo):
    """A clone of a bare remote, so ``origin/main`` exists for the session branch."""
    remote = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git("init", "-q", "-b", "main", cwd=seed)
    _git("config", "user.email", "t@example.com", cwd=seed)
    _git("config", "user.name", "T", cwd=seed)
    register_repo(seed, "myproject")
    (seed / "README.md").write_text("base\n")
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "init", cwd=seed)
    _git("push", "-q", str(remote), "main", cwd=seed)
    checkout = tmp_path / "myproject"
    _git("clone", "-q", str(remote), str(checkout), cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=checkout)
    _git("config", "user.name", "T", cwd=checkout)
    return checkout


def _session_with_uncommitted_work(repo: Path, name: str) -> Path:
    slot = create_worktree(name, repo_root=repo)
    assert slot is not None
    (slot / "in-progress.md").write_text("uncommitted session work\n")
    return slot


def _rewrite(path: Path, text: str) -> None:
    """Replace ``path``'s text in place. Git for Windows marks a worktree's ``.git`` file hidden,
    and Windows refuses to open a hidden file for writing with truncate-or-create (``"w"``)."""
    with path.open("r+", encoding="utf-8") as handle:
        handle.write(text)
        handle.truncate()


def _admin_dir(slot: Path) -> Path:
    return Path((slot / ".git").read_text().split("gitdir:", 1)[1].strip())


def _other_spelling(tmp_path: Path, slot: Path) -> Path:
    return tmp_path / "other-spelling" / slot.relative_to(tmp_path)


def test_given_the_registration_names_another_spelling_of_the_slot_when_relaunched_then_the_slot_is_reused(
    repo, tmp_path
):
    slot = _session_with_uncommitted_work(repo, "myproject-tst-1")
    (_admin_dir(slot) / "gitdir").write_text(f"{_other_spelling(tmp_path, slot) / '.git'}\n")

    result = create_worktree("myproject-tst-1", repo_root=repo, with_status=True)

    assert result == (slot.resolve(), False)
    assert slot.resolve() in registered_worktrees(repo)
    assert (slot / "in-progress.md").read_text() == "uncommitted session work\n"


def test_given_both_linkage_records_name_another_spelling_when_relaunched_then_the_slot_is_reused(repo, tmp_path):
    slot = _session_with_uncommitted_work(repo, "myproject-tst-2")
    admin = _admin_dir(slot)
    other = _other_spelling(tmp_path, slot)
    (admin / "gitdir").write_text(f"{other / '.git'}\n")
    _rewrite(slot / ".git", f"gitdir: {tmp_path / 'other-spelling' / admin.relative_to(tmp_path)}\n")

    result = create_worktree("myproject-tst-2", repo_root=repo, with_status=True)

    assert result == (slot.resolve(), False)
    assert (slot / "in-progress.md").read_text() == "uncommitted session work\n"


def test_given_the_slot_lost_its_registration_when_relaunched_then_it_is_moved_aside_never_into_agents(repo, tmp_path):
    """No git record refers to the slot, so the launcher moves it aside itself and relaunches.

    It used to refuse and print the ``mv`` for the user to run; that stopped every launch on a
    host where this state recurs, for a move the launcher can make with nothing to lose.
    """
    name = "myproject-tst-3"
    slot = _session_with_uncommitted_work(repo, name)
    admin = _admin_dir(slot)
    aside = tmp_path / "admin-aside"
    admin.rename(aside)

    created = create_worktree(name, repo_root=repo)

    assert created is not None and created.resolve() == slot.resolve()
    assert not (slot.parent / f"{name}-agents").exists(), "the session's own checkout never goes into -agents/"
    recovered = next(slot.parent.glob(f"{name}-orphaned-*"))
    assert (recovered / "in-progress.md").read_text() == "uncommitted session work\n"
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=slot).stdout.strip() == f"wt-{name}"
