"""A child body restores stderr from the descriptor its supervisor holds, whatever generation wrote it.

The supervisor is the pane leader and lives for the whole session, but every child body is started
from the stable script as it is on disk now. An `ai c` that attaches to a running session rewrites
that script, so a long-lived supervisor rendered without a launch log (and so without the saved
terminal on fd 3) starts children from a script rendered with one, and the reverse. The child used
to decide from its own text alone: `exec 2>&3` against a supervisor that never opened fd 3 printed
`<script>:<line>: 3: bad file descriptor` on every restart, and a child rendered without a log left
its agent's stderr in a supervisor's launch-log relay.

These tests run the real generated script on a pty as `ai c` hands it to tmux, then replace the
stable script with the other generation through the launcher's own writer, as an attaching launch
does, and let the first agent exit so the supervisor starts a child from the new text.
"""

from __future__ import annotations

import sys

import pytest

if sys.platform == "win32":
    pytest.skip("POSIX-only: the session supervisor is a POSIX shell script on a pty", allow_module_level=True)

import os
import shutil
import time
from pathlib import Path

import test_reload_progress
from test_reload_progress import SESSION, Session, session_factory  # noqa: F401

from ai_cli import main, session_script


@pytest.fixture
def generation_swap(monkeypatch, tmp_path, session_factory):  # noqa: F811
    original = Session._fakes
    release = tmp_path / "release-first-agent"

    def start(*, shell: str, supervisor_log: Path | None, child_log: Path | None) -> Session:
        shell_path = shutil.which(shell)
        if shell_path is None:
            pytest.skip(f"{shell} is not installed on this runner")

        def fakes(bin_dir, *args):
            original(bin_dir, *args)
            # The first agent runs until the test releases it; every later one reports on stderr.
            (bin_dir / "claude").write_text(
                f"#!{sys.executable}\n"
                "import os, sys, time\n"
                f"started = {str(tmp_path / 'agent-started')!r}\n"
                "with open(started, 'a') as log:\n"
                "    log.write(f'{os.getpid()}\\n')\n"
                "index = len(open(started).read().split())\n"
                "if index == 1:\n"
                f"    while not os.path.exists({str(release)!r}):\n"
                "        time.sleep(0.05)\n"
                "    sys.exit(0)\n"
                "sys.stderr.write(f'agent-stderr {index}\\n')\n"
                "sys.stderr.flush()\n"
                "time.sleep(600)\n"
            )

        monkeypatch.setattr(Session, "_fakes", staticmethod(fakes))
        monkeypatch.setattr(test_reload_progress, "resolve_session_shell", lambda: shell_path)
        monkeypatch.setattr(session_script, "resolve_session_shell", lambda: shell_path)
        session = session_factory(reload_on=(), launch_log=supervisor_log)
        session.wait_for_agents(1)
        script = tmp_path / "state" / "ai-cli-utils" / "sessions" / f"{SESSION}.sh"
        replacement = session_script.get_engine_script(
            "c",
            "session-1",
            SESSION,
            "test-",
            "myproject",
            worktree_dir=str(tmp_path / "worktree"),
            launch_log_path=str(child_log) if child_log is not None else None,
        )
        assert main._write_launch_script_if_changed(script, replacement)
        stamp = int(time.time()) + 3600
        os.utime(script, (stamp, stamp))
        release.write_text("", encoding="utf-8")
        session.wait_for_agents(2)
        return session

    return start


@pytest.mark.parametrize("shell", ["zsh", "bash"])
def test_given_a_supervisor_without_a_launch_log_when_a_child_from_a_logged_script_starts_then_no_descriptor_error(
    generation_swap, tmp_path, shell
):
    session = generation_swap(shell=shell, supervisor_log=None, child_log=tmp_path / "launch.log")
    text = "\n".join(session.rows())

    assert "bad file descriptor" not in text.lower(), text
    assert "agent-stderr 2" in session.rows(), text


@pytest.mark.parametrize("shell", ["zsh", "bash"])
def test_given_a_logged_supervisor_when_a_child_from_a_script_without_a_log_starts_then_agent_stderr_stays_out_of_the_log(
    generation_swap, tmp_path, shell
):
    launch_log = tmp_path / "launch.log"
    session = generation_swap(shell=shell, supervisor_log=launch_log, child_log=None)
    rows = session.rows()

    assert "agent-stderr 2" in rows, rows
    assert launch_log.exists(), "the supervisor never relayed to its launch log"
    assert "agent-stderr" not in launch_log.read_text(encoding="utf-8")
