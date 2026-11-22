"""On Windows the launcher waits for the program it hands off to instead of exec'ing it.

Windows has no exec: CPython starts the program as a new process and ends the caller at
once. Under Git Bash that returned the shell's prompt while the tmux client it had just
started ran on the same console, and the two shared the keyboard, so the session took
almost no input.
"""

from __future__ import annotations

import signal
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from ai_cli import main
from ai_cli.main import cli

# Taken at import, before the suite's autouse fixture routes it to os.execvp.
_RUN_ON_THIS_CONSOLE = main._run_on_this_console


class _WindowsSys:
    """The launcher's view of ``sys`` on Windows, and only the launcher's."""

    platform = "win32"

    def __getattr__(self, name: str):
        return getattr(sys, name)


@pytest.fixture
def windows_launcher(monkeypatch):
    monkeypatch.setattr(main, "_run_on_this_console", _RUN_ON_THIS_CONSOLE)
    monkeypatch.setattr(main, "sys", _WindowsSys())
    previous = signal.getsignal(signal.SIGINT)
    yield
    signal.signal(signal.SIGINT, previous)


def test_given_windows_when_handing_off_then_launcher_waits_for_the_program_and_exits_with_its_status(
    windows_launcher, tmp_path, monkeypatch
):
    monkeypatch.setattr(main.os, "execvp", Mock(side_effect=AssertionError("Windows has no exec")))
    finished = tmp_path / "finished"
    program = "import pathlib, sys, time; time.sleep(0.3); pathlib.Path(sys.argv[1]).write_text('done'); sys.exit(7)"
    with pytest.raises(SystemExit) as exc_info:
        main._exec_handoff(sys.executable, [sys.executable, "-c", program, str(finished)])
    assert exc_info.value.code == 7
    assert finished.read_text() == "done"


def test_given_windows_when_handing_off_then_ctrl_c_is_left_to_the_program(windows_launcher, monkeypatch):
    seen = []
    monkeypatch.setattr(main.os, "execvp", Mock(side_effect=AssertionError("Windows has no exec")))
    program = "import sys; sys.exit(0)"
    real_popen = main.subprocess.Popen

    def popen(*args, **kwargs):
        seen.append(signal.getsignal(signal.SIGINT))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(main.subprocess, "Popen", popen)
    with pytest.raises(SystemExit):
        main._exec_handoff(sys.executable, [sys.executable, "-c", program])
    assert seen == [signal.SIG_IGN]


def test_given_windows_when_program_is_missing_then_the_error_surfaces_and_ctrl_c_is_restored(
    windows_launcher, tmp_path
):
    before = signal.getsignal(signal.SIGINT)
    missing = (tmp_path / "no-such-program").as_posix()
    with pytest.raises(FileNotFoundError):
        main._exec_handoff(missing, [missing])
    assert signal.getsignal(signal.SIGINT) == before


def test_given_windows_when_upgrading_then_the_launcher_still_execs_so_uv_can_replace_it(windows_launcher):
    uv_path = Path("/opt/uv/bin/uv").as_posix()
    with (
        patch("sys.argv", ["ai", "upgrade"]),
        patch("ai_cli.config.load_config", return_value={}),
        patch("shutil.which", return_value=uv_path),
        patch("ai_cli.main._should_use_uv_link_mode_copy", return_value=False),
        patch("os.execvp", side_effect=SystemExit(0)) as exec_call,
        patch("ai_cli.main._run_on_this_console", side_effect=AssertionError("must not wait on its own upgrade")),
        pytest.raises(SystemExit),
    ):
        cli()
    exec_call.assert_called_once_with(uv_path, [uv_path, "tool", "upgrade", "ai-cli-utils"])
