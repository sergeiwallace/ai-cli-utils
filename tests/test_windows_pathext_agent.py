"""An agent installed as ``claude.EXE`` starts from a launch that names it ``claude``.

On Windows a program is found through PATHEXT: ``claude`` names ``claude.EXE`` and
``codex`` names ``codex.cmd``. CreateProcess alone tries only ``.exe`` and cannot start
a ``.cmd`` by bare name, so every handoff resolves the program with ``shutil.which``
first. Elsewhere these tests stand a PATHEXT lookup in for the one ``shutil`` the
launcher's foreground runner uses; on Windows CI that lookup is the real one.
"""

from __future__ import annotations

import ast
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import run_cli

from ai_cli import compact_transport, foreground, main

# Taken at import, before the suite's autouse fixture routes it to os.execvp.
_RUN_ON_THIS_CONSOLE = main._run_on_this_console

# The real Popen, taken before the suite's spawn guard: the `claude` these tests start is
# the stand-in below, never a real agent.
_REAL_POPEN = subprocess.Popen

_AGENT = "import pathlib, sys\npathlib.Path(sys.argv[1]).write_text(repr(sys.argv[2:]))\nsys.exit(4)\n"


class _WindowsShutil:
    """``shutil`` as Windows answers ``which``: each PATHEXT extension in turn, never the bare name."""

    @staticmethod
    def which(name: str, path: str | None = None) -> str | None:
        if Path(name).name != name:
            return shutil.which(name, path=path)
        return next(
            (found for ext in (".COM", ".EXE", ".BAT", ".CMD") if (found := shutil.which(name + ext, path=path))), None
        )


class _WindowsSys:
    platform = "win32"

    def __getattr__(self, name: str):
        return getattr(sys, name)


@pytest.fixture
def pathext_agent(tmp_path, monkeypatch):
    """A ``claude`` that exists only under a PATHEXT name, first on PATH; returns its report file."""
    bin_dir = tmp_path / "agent-bin"
    bin_dir.mkdir()
    report = tmp_path / "agent-ran"
    source = bin_dir / "agent.py"
    source.write_text(_AGENT)
    python = Path(sys.executable).as_posix()
    if sys.platform == "win32":
        (bin_dir / "claude.cmd").write_text(f'@"{python}" "{source}" "{report}" %*\r\n')
    else:
        program = bin_dir / "claude.EXE"
        program.write_text(
            f'#!/bin/sh\nexec {shlex.quote(python)} {shlex.quote(str(source))} {shlex.quote(str(report))} "$@"\n'
        )
        program.chmod(0o755)
        monkeypatch.setattr(foreground, "shutil", _WindowsShutil, raising=False)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    guarded = subprocess.Popen

    def stand_in_only(args, **kwargs):
        """Start the stand-in agent for real; every other spawn still meets the suite's guard."""
        program = kwargs.get("executable")
        if program and Path(program).parent == bin_dir:
            return _REAL_POPEN(args, **kwargs)
        return guarded(args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", stand_in_only)
    return report


def test_given_a_pathext_only_agent_when_a_once_session_runs_it_then_the_agent_starts_and_its_status_returns(
    pathext_agent,
):
    with (
        patch("ai_cli.session_commands.state.begin", return_value="generation-1"),
        patch("ai_cli.session_commands.state.mark_running", return_value=True),
        patch("ai_cli.session_commands.state.mark_exited", return_value=True),
    ):
        code, _out, err = run_cli(["ai", "sessions", "_run", "c-session-1", "--", "claude", "--flag"])
    assert pathext_agent.read_text() == repr(["--flag"]), err
    assert code == 4


def test_given_windows_and_a_pathext_only_agent_when_a_bare_launch_hands_off_then_the_agent_starts(
    pathext_agent, tmp_path, monkeypatch
):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setattr(main, "sys", _WindowsSys())
    monkeypatch.setattr(main, "_run_on_this_console", _RUN_ON_THIS_CONSOLE)
    with (
        patch("sys.argv", ["ai", "c", "--bare"]),
        patch("ai_cli.config.load_config", return_value={}),
        patch("ai_cli.session.get_project_prefix", return_value="session"),
        patch("ai_cli.session.is_current_project_resolved", return_value=True),
        patch("ai_cli.session.create_worktree", return_value=(project, False)),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.main.trigger_background_update"),
        patch("ai_cli.direnv_setup.is_bypassed", return_value=True),
        patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")),
        patch("os.execvp", side_effect=AssertionError("Windows has no exec")),
        patch(
            "ai_cli.compact_transport.probe_pty",
            return_value=compact_transport.PtyProbe(False, "no termios module", platform_supported=False),
        ),
    ):
        with pytest.raises(SystemExit) as exit_info:
            main.cli()
    assert "--name" in ast.literal_eval(pathext_agent.read_text())
    assert exit_info.value.code == 4
