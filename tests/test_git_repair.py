"""Tests for the core.bare=true / stale core.worktree corruption fix (AI-CLI-99),
plus the AIH-443 phantom-deletion detection guards."""

import subprocess
from unittest.mock import MagicMock, patch

from ai_cli.git_repair import (
    _GIT_TARGETING_VARS,
    _git_env,
    detect_missing_tracked_symlinks,
    detect_stranded_autostash,
    repair_bare_worktree_config,
)


# --- _git_env ---


def test_git_env_when_targeting_vars_present_then_strips_them():
    base = {
        "GIT_DIR": "/tmp/somewhere/.git",
        "GIT_WORK_TREE": "/tmp/somewhere",
        "GIT_INDEX_FILE": "/tmp/somewhere/.git/index",
        "GIT_OBJECT_DIRECTORY": "/tmp/somewhere/.git/objects",
        "GIT_COMMON_DIR": "/tmp/somewhere/.git",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/tmp/other/objects",
        "GIT_PREFIX": "sub/dir/",
        "GIT_CONFIG": "/tmp/somewhere/.git/config",
        "GIT_CONFIG_GLOBAL": "/tmp/home/.gitconfig",
        "PATH": "/usr/bin",
    }

    result = _git_env(base)

    for var in _GIT_TARGETING_VARS:
        assert var not in result
    assert result["PATH"] == "/usr/bin"


def test_git_env_when_innocuous_git_vars_present_then_keeps_them():
    base = {
        "GIT_DIR": "/tmp/somewhere/.git",
        "GIT_SSH": "/usr/bin/ssh",
        "GIT_SSH_COMMAND": "ssh -o foo=bar",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    result = _git_env(base)

    assert "GIT_DIR" not in result
    assert result["GIT_SSH"] == "/usr/bin/ssh"
    assert result["GIT_SSH_COMMAND"] == "ssh -o foo=bar"
    assert result["GIT_TERMINAL_PROMPT"] == "0"
    assert result["GIT_AUTHOR_NAME"] == "Test"
    assert result["GIT_AUTHOR_EMAIL"] == "t@t"
    assert result["GIT_COMMITTER_NAME"] == "Test"
    assert result["GIT_COMMITTER_EMAIL"] == "t@t"


def test_git_env_when_no_base_given_then_scrubs_os_environ():
    with patch.dict("os.environ", {"GIT_DIR": "/leaked", "SOME_VAR": "1"}, clear=False):
        result = _git_env()
    assert "GIT_DIR" not in result
    assert result.get("SOME_VAR") == "1"


# --- repair_bare_worktree_config ---


def test_repair_when_bare_true_then_resets_false_and_reports_repaired(tmp_path):
    def mock_run(cmd, **kwargs):
        if "core.bare" in cmd and "--get" in cmd:
            return MagicMock(returncode=0, stdout="true\n")
        if "core.worktree" in cmd and "--get" in cmd:
            return MagicMock(returncode=1, stdout="")
        return MagicMock(returncode=0, stdout="")

    with patch("subprocess.run", side_effect=mock_run) as mock_subprocess:
        result = repair_bare_worktree_config(tmp_path)

    assert result is True
    set_calls = [c.args[0] for c in mock_subprocess.call_args_list if "config" in c.args[0] and "false" in c.args[0]]
    assert any("core.bare" in c for c in set_calls)


def test_repair_when_stale_worktree_config_then_unsets_and_reports_repaired(tmp_path):
    def mock_run(cmd, **kwargs):
        if "core.bare" in cmd and "--get" in cmd:
            return MagicMock(returncode=0, stdout="false\n")
        if "core.worktree" in cmd and "--get" in cmd:
            return MagicMock(returncode=0, stdout="/some/sibling/.worktrees/aih-37\n")
        return MagicMock(returncode=0, stdout="")

    with patch("subprocess.run", side_effect=mock_run) as mock_subprocess:
        result = repair_bare_worktree_config(tmp_path)

    assert result is True
    unset_calls = [c.args[0] for c in mock_subprocess.call_args_list if "--unset" in c.args[0]]
    assert any("core.worktree" in c for c in unset_calls)


def test_repair_when_both_bare_true_and_stale_worktree_then_repairs_both(tmp_path):
    def mock_run(cmd, **kwargs):
        if "core.bare" in cmd and "--get" in cmd:
            return MagicMock(returncode=0, stdout="true\n")
        if "core.worktree" in cmd and "--get" in cmd:
            return MagicMock(returncode=0, stdout="/some/sibling/.worktrees/aih-37\n")
        return MagicMock(returncode=0, stdout="")

    with patch("subprocess.run", side_effect=mock_run) as mock_subprocess:
        result = repair_bare_worktree_config(tmp_path)

    assert result is True
    calls = [c.args[0] for c in mock_subprocess.call_args_list]
    assert any("core.bare" in c and "false" in c for c in calls)
    assert any("--unset" in c and "core.worktree" in c for c in calls)


def test_repair_when_healthy_repo_then_noop_and_returns_false(tmp_path):
    def mock_run(cmd, **kwargs):
        if "core.bare" in cmd and "--get" in cmd:
            return MagicMock(returncode=0, stdout="false\n")
        if "core.worktree" in cmd and "--get" in cmd:
            return MagicMock(returncode=1, stdout="")
        return MagicMock(returncode=0, stdout="")

    with patch("subprocess.run", side_effect=mock_run) as mock_subprocess:
        result = repair_bare_worktree_config(tmp_path)

    assert result is False
    set_calls = [c.args[0] for c in mock_subprocess.call_args_list if "false" in c.args[0] or "--unset" in c.args[0]]
    assert set_calls == []


def test_repair_when_real_repo_corrupted_then_fixes_it_end_to_end(tmp_path):
    """Integration-style repro: a real git repo flipped to core.bare=true (± a
    stale core.worktree) is restored to a normal working tree by the guard."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        ],
        check=True,
    )

    # Simulate the corruption directly (the documented failure mode): flip
    # core.bare=true and point core.worktree at a sibling worktree directory
    # (the sibling dir must exist — git rejects config ops that reference a
    # nonexistent core.worktree path with "fatal: Invalid path").
    sibling_worktree = tmp_path / ".worktrees" / "aih-37"
    sibling_worktree.mkdir(parents=True)
    subprocess.run(["git", "-C", str(repo), "config", "--local", "core.bare", "true"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "core.worktree", str(sibling_worktree)],
        check=True,
    )

    result = repair_bare_worktree_config(repo)

    assert result is True
    bare = subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "--get", "core.bare"], capture_output=True, text=True
    )
    assert bare.stdout.strip() == "false"
    worktree_cfg = subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "--get", "core.worktree"], capture_output=True
    )
    assert worktree_cfg.returncode != 0  # unset


# --- detect_stranded_autostash (AIH-443) ---


def _git(repo, *args, **kwargs):
    return subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True, text=True, check=False, **kwargs)


def test_detect_stranded_autostash_when_pop_conflicts_then_finds_it_end_to_end(tmp_path):
    """Reproduces the exact failure this guard exists for: a same-line local vs.
    remote edit makes `git pull --rebase --autostash` return 0 while leaving a
    conflict and a stash entry behind — the launcher's `pull.returncode != 0`
    check alone would never see this."""
    remote = tmp_path / "remote"
    remote.mkdir()
    _git(remote, "init", "-q", "-b", "main")
    _git(remote, "config", "user.email", "t@t")
    _git(remote, "config", "user.name", "t")
    (remote / "f.txt").write_text("line1\n")
    _git(remote, "add", "f.txt")
    _git(remote, "commit", "-q", "-m", "init")

    local = tmp_path / "local"
    subprocess.run(["git", "clone", "-q", str(remote), str(local)], check=True)
    _git(local, "config", "user.email", "t@t")
    _git(local, "config", "user.name", "t")

    # Local: uncommitted change, never staged or committed.
    (local / "f.txt").write_text("local-change\n")

    # Remote: someone else edits the same line and pushes.
    (remote / "f.txt").write_text("remote-change\n")
    _git(remote, "commit", "-q", "-am", "remote edits same line")

    _git(local, "fetch", "-q", "origin", "main")
    pull = _git(local, "pull", "--rebase", "--autostash", "origin", "main")

    assert pull.returncode == 0  # the exact lie this guard exists to catch

    stranded = detect_stranded_autostash(local)

    assert stranded is not None
    assert "stash@{0}" in stranded


def test_detect_stranded_autostash_when_clean_pull_then_none(tmp_path):
    remote = tmp_path / "remote"
    remote.mkdir()
    _git(remote, "init", "-q", "-b", "main")
    _git(remote, "config", "user.email", "t@t")
    _git(remote, "config", "user.name", "t")
    (remote / "f.txt").write_text("line1\n")
    _git(remote, "add", "f.txt")
    _git(remote, "commit", "-q", "-m", "init")

    local = tmp_path / "local"
    subprocess.run(["git", "clone", "-q", str(remote), str(local)], check=True)
    _git(local, "config", "user.email", "t@t")
    _git(local, "config", "user.name", "t")

    (remote / "g.txt").write_text("unrelated\n")
    _git(remote, "add", "g.txt")
    _git(remote, "commit", "-q", "-m", "unrelated remote change")

    _git(local, "fetch", "-q", "origin", "main")
    pull = _git(local, "pull", "--rebase", "--autostash", "origin", "main")

    assert pull.returncode == 0
    assert detect_stranded_autostash(local) is None


# --- detect_missing_tracked_symlinks (AIH-443 Shape A) ---


def test_detect_missing_tracked_symlinks_when_symlink_missing_from_disk_then_reports_it(tmp_path):
    """Reproduces AIH-443 Shape A's exact signature: a tracked symlink HEAD still
    lists is absent from disk (verified `os.path.lexists` failure), with no git
    error anywhere — the same shape a Claude Code `isolation: worktree` checkout
    produced for 21 real symlinks in one sub-agent worktree."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "docs").mkdir()
    (repo / "docs" / "real.md").write_text("real content\n")
    link = repo / "docs" / "link.md"
    link.symlink_to("real.md")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init with a tracked symlink")

    assert detect_missing_tracked_symlinks(repo) == []  # sanity: present and tracked

    # Simulate the checkout dropping the symlink from disk (Shape A) — the index/
    # HEAD are untouched, exactly matching `git status` reporting a deletion
    # nobody made while `git show HEAD:<path>` still succeeds.
    link.unlink()

    missing = detect_missing_tracked_symlinks(repo)

    assert missing == ["docs/link.md"]
    show = _git(repo, "show", "HEAD:docs/link.md")
    assert show.returncode == 0  # still present in HEAD — a real "phantom" deletion


def test_detect_missing_tracked_symlinks_when_regular_file_missing_then_ignored(tmp_path):
    """A missing REGULAR file (Shape B's signature, not Shape A's) must not be
    reported here — the two shapes are confirmed distinct root causes and this
    detector is scoped to the symlink-specific mechanism only."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("content\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    (repo / "f.txt").unlink()

    assert detect_missing_tracked_symlinks(repo) == []
