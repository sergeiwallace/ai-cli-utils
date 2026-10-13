"""Classification of ``tmux new-session -P -F '#{session_id}'`` output.

Regression coverage for a launch failure that reported only "failed to establish
ownership": a tmux whose format expansion is not working echoes the format back
literally, so the launcher received ``#session_id`` where it required ``$0``. The
opaque-id regex rejects both that and a genuinely empty answer, but the two need
different messages -- a tmux that does not expand formats will not begin doing so
on retry, so the operator's remedy is to launch without tmux.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil
import pytest

from ai_cli.tmux_ownership import capture_tmux_session_identity, classify_new_session_output, kill_owned_tmux_session


@pytest.mark.parametrize("raw", ["$0", "$3", "$42", "  $7  ", "$7\n"])
def test_given_valid_opaque_id_when_classified_then_ok(raw: str) -> None:
    status, value = classify_new_session_output(raw)
    assert status == "ok"
    assert value == raw.strip()


@pytest.mark.parametrize(
    "raw",
    [
        "#session_id",
        "#{session_id}",
        "#session_id\n",
        "#version",
    ],
)
def test_given_unexpanded_format_when_classified_then_reported_as_not_expanded(raw: str) -> None:
    """The defect this module exists to distinguish.

    ``#session_id`` is what a non-expanding tmux returns for ``#{session_id}``;
    ``#version`` is the same fault observed through a different format string.
    """
    status, value = classify_new_session_output(raw)
    assert status == "format-not-expanded"
    assert value == raw.strip()


@pytest.mark.parametrize("raw", ["", "   ", "\n", None, "garbage", "0", "$", "$abc", "12"])
def test_given_unusable_output_when_classified_then_reported_as_unusable(raw: str | None) -> None:
    status, _ = classify_new_session_output(raw)
    assert status == "unusable"


def test_given_each_class_when_classified_then_statuses_are_distinct() -> None:
    """Guards the whole point: the three classes must not collapse into one.

    A body replaced with a constant return, or one that dropped the format branch
    back into the generic failure path, passes every single-case assertion above
    while making the launcher's message wrong again.
    """
    assert classify_new_session_output("$1")[0] == "ok"
    assert classify_new_session_output("#session_id")[0] == "format-not-expanded"
    assert classify_new_session_output("")[0] == "unusable"
    statuses = {
        classify_new_session_output("$1")[0],
        classify_new_session_output("#session_id")[0],
        classify_new_session_output("")[0],
    }
    assert len(statuses) == 3


def _process_has_ended(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        return not process.is_running() or process.status() in {psutil.STATUS_DEAD, psutil.STATUS_ZOMBIE}
    except psutil.NoSuchProcess:
        return True


@pytest.mark.real_tmux
@pytest.mark.skipif(sys.platform == "win32", reason="tmux is not available natively on Windows")
def test_given_term_resistant_pane_tree_when_owned_session_is_killed_then_no_pane_process_survives(
    tmp_path: Path,
) -> None:
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not available")

    socket_dir = Path(tempfile.mkdtemp(prefix="ai-cli-quota-tmux-", dir="/tmp"))
    socket = str(socket_dir / "socket")
    child_pid_file = tmp_path / "child.pid"
    child_code = (
        "import signal,time;"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        "signal.signal(signal.SIGHUP, signal.SIG_IGN);"
        "time.sleep(30)"
    )
    pane_code = (
        "import pathlib,signal,subprocess,sys,time;"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        "signal.signal(signal.SIGHUP, signal.SIG_IGN);"
        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}]);"
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid));"
        "time.sleep(30)"
    )
    tmux_command = ("tmux", "-S", socket)
    child_pid = 0
    pane_pid = 0
    try:
        created = subprocess.run(
            [
                *tmux_command,
                "new-session",
                "-d",
                "-P",
                "-F",
                "#{session_id}",
                "-s",
                "test-quota-cleanup",
                sys.executable,
                "-c",
                pane_code,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert created.returncode == 0, created.stderr
        session_id = created.stdout.strip()
        marked = subprocess.run(
            [*tmux_command, "set-option", "-t", session_id, "@ai_cli_session_generation", "test-generation"],
            capture_output=True,
            check=False,
        )
        assert marked.returncode == 0
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not child_pid_file.exists():
            time.sleep(0.05)
        assert child_pid_file.exists(), "pane process did not start its child"
        child_pid = int(child_pid_file.read_text())
        pane = subprocess.run(
            [*tmux_command, "display-message", "-p", "-t", session_id, "#{pane_pid}"],
            capture_output=True,
            text=True,
            check=False,
        )
        pane_pid = int(pane.stdout.strip())
        identity = capture_tmux_session_identity(session_id, tmux_command=tmux_command)
        assert identity is not None
        assert psutil.pid_exists(pane_pid) and psutil.pid_exists(child_pid)

        assert kill_owned_tmux_session(
            identity,
            tmux_command=tmux_command,
            terminate_pane_processes=True,
            process_timeout=0.2,
        )

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not (_process_has_ended(pane_pid) and _process_has_ended(child_pid)):
            time.sleep(0.05)
        assert _process_has_ended(pane_pid)
        assert _process_has_ended(child_pid)
    finally:
        for pid in (child_pid, pane_pid):
            if pid > 0 and not _process_has_ended(pid):
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.kill(pid, signal.SIGKILL)
        subprocess.run([*tmux_command, "kill-server"], capture_output=True, check=False)
        with contextlib.suppress(OSError):
            socket_dir.rmdir()
