"""Tests for the core.bare=true / stale core.worktree corruption fix (AI-CLI-99)."""

import subprocess
from unittest.mock import MagicMock, patch

from ai_cli.git_repair import _GIT_TARGETING_VARS, _git_env, repair_bare_worktree_config


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
