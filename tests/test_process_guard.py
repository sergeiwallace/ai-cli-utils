"""Regression coverage for the live-process test isolation guard (AI-CLI-117)."""

import os
import re
import subprocess
from unittest.mock import MagicMock, patch

import pytest


class TestRealProcessGuard:
    def test_given_unmocked_tmux_launch_when_run_then_fails_loudly(self):
        """A missed launch mock must fail before tmux can create a session."""
        with pytest.raises(RuntimeError, match="attempted to spawn a real `tmux` process"):
            subprocess.run(["tmux", "new-session", "-d", "-s", "pytest-leak-guard-reproduction"], check=False)

    def test_given_unmocked_agent_exec_when_called_then_fails_loudly(self):
        with pytest.raises(RuntimeError, match="attempted to spawn a real `claude` process"):
            os.execvp("claude", ["claude"])

    @pytest.mark.parametrize("binary", ["agy", "agy.exe"])
    def test_given_unmocked_agy_popen_when_called_then_fails_loudly(self, binary, tmp_path):
        with pytest.raises(RuntimeError, match=rf"attempted to spawn a real `{re.escape(binary)}` process"):
            subprocess.Popen([str(tmp_path / binary)])

    @pytest.mark.parametrize("binary", ["circusd", "circusd.exe"])
    def test_given_unmocked_circusd_popen_when_called_then_fails_loudly(self, binary, tmp_path):
        with pytest.raises(RuntimeError, match=rf"attempted to spawn a real `{re.escape(binary)}` process"):
            subprocess.Popen([str(tmp_path / binary), "--daemon", "--pidfile", str(tmp_path / "circusd.pid")])

    def test_given_unmocked_ssh_process_when_run_then_fails_loudly(self):
        """A missed remote transport mock must fail before SSH can connect."""
        with pytest.raises(RuntimeError, match="attempted to spawn a real `ssh` process"):
            subprocess.run(["ssh", "-G", "example.invalid"], check=False)

    def test_given_unmocked_mosh_process_when_run_then_fails_loudly(self):
        """A missed remote transport mock must fail before mosh can connect."""
        with pytest.raises(RuntimeError, match="attempted to spawn a real `mosh` process"):
            subprocess.run(["mosh", "--version"], check=False)

    def test_given_test_level_subprocess_mock_when_launch_runs_then_inner_mock_overrides_guard(self):
        expected = MagicMock(returncode=0)
        with patch("subprocess.run", return_value=expected):
            result = subprocess.run(["tmux", "new-session", "-d", "-s", "pytest-leak-guard-mocked"], check=False)
        assert result is expected
