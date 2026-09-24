"""A session worktree must survive its session exiting.

Regression coverage for the defect where exiting an ``ai c`` session deleted that
session's git worktree. The supervisor teardown trap invoked
``ai internal cleanup-worktree "$ai_name"``, which removed
``<repo>/.worktrees/<ai_name>`` whenever ``git status --porcelain`` was empty.

Measured 2026-09-15 on a real session: ``.worktrees/kg-1`` and
``.git/worktrees/kg-1`` both vanished the moment the session exited cleanly, the
worktree deregistered, and it disappeared from the editor's source-control view.
Only the branch survived, because ``git worktree remove`` never touches one.

That contradicted the ratified fleet rule (AIH-771, ai-harness
``docs/procedures/worktree-workflow.md``): a canonical ``ai c``/``ai g`` session
worktree is a long-lived session home and is "never a cleanup candidate whatever
their merge status or cleanliness". Cleanliness was precisely the condition the
teardown used to justify removing one.

These tests exercise real git, a real filesystem and the real CLI dispatcher.
The boundary the defect lived on is git-plus-filesystem, so nothing there is
mocked: a mocked ``git worktree remove`` would assert the old wiring rather than
the behaviour that actually broke.
"""

from __future__ import annotations

import contextlib
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_cli.main import cli
from ai_cli.session_script import get_engine_script


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _repo_with_session_worktree(tmp_path: Path, ai_name: str, *, with_remote: bool = False) -> tuple[Path, Path]:
    """A real repo whose ``.worktrees/<ai_name>`` is a real, clean worktree.

    ``with_remote`` adds a bare origin and pushes ``main`` to it, so a later
    commit made only in the worktree genuinely exists on no remote.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", ".", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    _git("config", "commit.gpgsign", "false", cwd=repo)
    (repo / "a.txt").write_text("hi\n", encoding="utf-8")
    _git("add", "a.txt", cwd=repo)
    _git("commit", "-q", "--no-verify", "-m", "init", cwd=repo)

    if with_remote:
        origin = tmp_path / "origin.git"
        _git("init", "-q", "--bare", str(origin), cwd=tmp_path)
        _git("remote", "add", "origin", str(origin), cwd=repo)
        _git("push", "-q", "origin", "main", cwd=repo)

    worktree = repo / ".worktrees" / ai_name
    _git("worktree", "add", "-q", str(worktree), "-b", f"wt-{ai_name}", cwd=repo)
    return repo, worktree


def _commits_on_no_remote(cwd: Path) -> str:
    """Commits reachable from HEAD that exist on no remote-tracking ref.

    ``HEAD`` must be named explicitly: ``git log --not --remotes`` supplies a rev
    argument, which suppresses the implicit HEAD default, so without it the
    command returns nothing and would falsely report that everything is pushed.
    """
    return _git("log", "--oneline", "HEAD", "--not", "--remotes", cwd=cwd).stdout


def _run_teardown_cleanup(ai_name: str) -> None:
    """Invoke the exact command the supervisor teardown trap used to run.

    Once the reap is gone this is no longer a valid internal action, so a
    ``SystemExit`` is an acceptable outcome. What is never acceptable is the
    worktree being removed.
    """
    with patch("sys.argv", ["ai", "internal", "cleanup-worktree", ai_name]):
        with patch("ai_cli.config.load_config", return_value={}):
            with contextlib.suppress(SystemExit):
                cli()


def _registered(repo: Path, worktree: Path) -> bool:
    """Whether git still lists this worktree, compared as paths rather than as text.

    ``git worktree list --porcelain`` prints POSIX-style separators even on Windows
    (``D:/repo/.worktrees/x``), while ``str(WindowsPath)`` renders ``D:\\repo\\...``,
    so a substring test over the raw output reported a perfectly registered worktree
    as deregistered. Comparing parsed paths normalizes the separator and the
    drive-letter case, and it also drops the substring test's false positive on a
    worktree whose path is a prefix of another's.
    """
    listed = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    prefix = "worktree "
    registered = {Path(line[len(prefix) :]).resolve() for line in listed.stdout.splitlines() if line.startswith(prefix)}
    return worktree.resolve() in registered


class TestSessionWorktreeSurvivesExit:
    def test_generated_session_script_never_reaps_the_session_worktree(self):
        """The teardown trap must carry no worktree-removal command at all.

        This is the negative constraint: the defect was a line in the generated
        script, so the contract is asserted against the real rendered artifact
        that bash actually executes.
        """
        script = get_engine_script(
            engine="c",
            ai_name="kg-1",
            session="ai-kg-1",
            prefix="ai",
            project_prefix="semkg",
            worktree_dir="/tmp/repo/.worktrees/kg-1",
        )

        assert "cleanup-worktree" not in script, (
            "the supervisor teardown still invokes the worktree reap; "
            "a canonical session worktree must outlive its session (AIH-771)"
        )
        assert "worktree remove" not in script, "the generated session script still contains a git worktree removal"

    def test_teardown_leaves_a_clean_session_worktree_intact(self, tmp_path, monkeypatch):
        """A clean session worktree must still exist after teardown.

        This is the positive contract, and the exact scenario measured on kg-1:
        nothing uncommitted, nothing untracked, session exits, worktree gone.
        """
        repo, worktree = _repo_with_session_worktree(tmp_path, "kg-9")
        assert not _git("status", "--porcelain", cwd=worktree).stdout.strip(), (
            "fixture precondition: the worktree must be clean, which is the only state the old reap acted on"
        )

        monkeypatch.chdir(worktree)
        _run_teardown_cleanup("kg-9")

        assert worktree.is_dir(), "the session worktree checkout was deleted on session exit"
        assert (repo / ".git" / "worktrees" / "kg-9").is_dir(), (
            "the worktree's git admin entry was deleted, so it is deregistered"
        )
        assert _registered(repo, worktree), "the worktree is no longer registered with git"

    def test_teardown_leaves_a_worktree_holding_unpushed_commits_intact(self, tmp_path, monkeypatch):
        """Porcelain-clean is not evidence the work is safe elsewhere.

        ``git status --porcelain`` was the reap's only safety test, so a worktree
        whose branch carried commits that existed on no remote was still treated
        as disposable. The branch kept the commits reachable, but the checkout
        was destroyed.
        """
        repo, worktree = _repo_with_session_worktree(tmp_path, "kg-8", with_remote=True)
        (worktree / "unpushed.txt").write_text("work that is on no remote\n", encoding="utf-8")
        _git("add", "unpushed.txt", cwd=worktree)
        _git("commit", "-q", "--no-verify", "-m", "unpushed work", cwd=worktree)

        assert not _git("status", "--porcelain", cwd=worktree).stdout.strip()
        assert "unpushed work" in _commits_on_no_remote(worktree), (
            "fixture precondition: the commit must exist on no remote"
        )

        monkeypatch.chdir(worktree)
        _run_teardown_cleanup("kg-8")

        assert worktree.is_dir(), "a worktree holding commits that exist on no remote was deleted on session exit"
        assert (worktree / "unpushed.txt").is_file()


@pytest.mark.parametrize("ai_name", ["kg-1", "aih-4", "ai-cli-1"])
def test_no_internal_action_removes_a_canonical_session_worktree(tmp_path, monkeypatch, ai_name):
    """Every conventionally named session worktree is covered, not just kg-1.

    ai-harness's own ``is_canonical_session`` accepts each of these, so each was
    exposed to the same teardown reap.
    """
    repo, worktree = _repo_with_session_worktree(tmp_path, ai_name)
    monkeypatch.chdir(worktree)

    _run_teardown_cleanup(ai_name)

    assert worktree.is_dir(), f"the {ai_name} session worktree was deleted on session exit"
    assert (repo / ".git" / "worktrees" / ai_name).is_dir()
