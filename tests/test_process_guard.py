"""Regression coverage for the live-process test isolation guard (AI-CLI-117)."""

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from conftest import _cleanup_test_tmux_sessions


class TestRealProcessGuard:
    def test_given_unmocked_tmux_launch_when_run_then_fails_loudly(self):
        """A missed launch mock must fail before tmux can create a session."""
        with pytest.raises(RuntimeError, match="attempted to spawn a real `tmux` process"):
            subprocess.run(["tmux", "new-session", "-d", "-s", "pytest-leak-guard-reproduction"], check=False)

    def test_given_unmocked_agent_exec_when_called_then_fails_loudly(self):
        with pytest.raises(RuntimeError, match="attempted to spawn a real `claude` process"):
            os.execvp("claude", ["claude"])

    def test_given_unmocked_agent_popen_when_called_then_fails_loudly(self):
        with pytest.raises(RuntimeError, match="attempted to spawn a real `gemini` process"):
            subprocess.Popen(["gemini"])

    def test_given_test_level_subprocess_mock_when_launch_runs_then_inner_mock_overrides_guard(self):
        expected = MagicMock(returncode=0)
        with patch("subprocess.run", return_value=expected):
            result = subprocess.run(["tmux", "new-session", "-d", "-s", "pytest-leak-guard-mocked"], check=False)
        assert result is expected


class TestTestTmuxCleanup:
    def test_given_mixed_tmux_sessions_when_cleanup_then_kills_only_test_named_sessions(self):
        session_list = MagicMock()
        session_list.returncode = 0
        session_list.stdout = "pytest-leak-guard-one\nuser-session\npytest-leak-guard-two\n"
        run = MagicMock(side_effect=[session_list, MagicMock(returncode=0), MagicMock(returncode=0)])

        _cleanup_test_tmux_sessions(run)

        assert run.call_args_list == [
            ((["tmux", "list-sessions", "-F", "#{session_name}"],), {"capture_output": True, "text": True}),
            ((["tmux", "kill-session", "-t", "pytest-leak-guard-one"],), {"capture_output": True}),
            ((["tmux", "kill-session", "-t", "pytest-leak-guard-two"],), {"capture_output": True}),
        ]
