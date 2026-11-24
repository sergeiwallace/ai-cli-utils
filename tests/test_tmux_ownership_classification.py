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
from process_ownership import watch_tmux_directory

from ai_cli.tmux_ownership import (
    TmuxSessionIdentity,
    capture_tmux_session_identity,
    classify_new_session_output,
    kill_owned_tmux_session,
    kill_unmarked_tmux_placeholder,
    respawn_owned_tmux_pane,
    set_owned_tmux_option,
)


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
    with watch_tmux_directory(socket_dir):
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
            # write_text creates the file before it writes, so wait for the pid itself.
            while time.monotonic() < deadline and not (child_pid_file.exists() and child_pid_file.read_text()):
                time.sleep(0.05)
            assert child_pid_file.exists() and child_pid_file.read_text(), "pane process did not start its child"
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


def test_given_owned_session_identity_when_set_option_fences_then_uses_if_shell_and_rejects_mismatch(monkeypatch):
    seen: list[list[str]] = []
    identity = TmuxSessionIdentity("$0", "g-myproject-1", "gen-abc123")

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("ai_cli.tmux_ownership.subprocess.run", fake_run)
    assert set_owned_tmux_option(identity, "@ai_cli_client", "agy", tmux_command=("tmux",)) is True
    assert len(seen) == 1
    assert seen[0][:5] == ["tmux", "if-shell", "-F", "-t", "$0"]
    assert "$0|gen-abc123" in seen[0][5]
    assert seen[0][6] == "set-option -t '$0' '@ai_cli_client' 'agy'"

    # Invalid option names (empty, flag-like, whitespace) are rejected BEFORE calling tmux.
    seen.clear()
    for bad_option in ("", "-g", "--flag", "@-bad", "bad option"):
        assert set_owned_tmux_option(identity, bad_option, "agy", tmux_command=("tmux",)) is False
    assert seen == []

    # Generation mismatch prints sentinel and returns False.
    monkeypatch.setattr(
        "ai_cli.tmux_ownership.subprocess.run",
        lambda cmd, **kw: type("R", (), {"returncode": 0, "stdout": "__ai_cli_ownership_mismatch__\n", "stderr": ""})(),
    )
    assert set_owned_tmux_option(identity, "@ai_cli_client", "agy", tmux_command=("tmux",)) is False


def test_given_owned_session_identity_when_respawn_pane_fences_then_retries_without_separator_only_on_fence_match(
    monkeypatch,
):
    identity = TmuxSessionIdentity("$0", "g-myproject-1", "gen-abc123")

    # 1. Fence mismatch MUST NOT retry without `--`.
    mismatch_calls: list[list[str]] = []

    def fake_mismatch(cmd, **kw):
        mismatch_calls.append(list(cmd))
        return type("R", (), {"returncode": 0, "stdout": "__ai_cli_ownership_mismatch__\n", "stderr": ""})()

    monkeypatch.setattr("ai_cli.tmux_ownership.subprocess.run", fake_mismatch)
    assert respawn_owned_tmux_pane(identity, ("/bin/sh", "-c", "echo hi"), tmux_command=("tmux",)) is False
    assert len(mismatch_calls) == 1

    # 2. Absent session or dead server (rc=1, no sentinel): capture returns None, so NO retry runs.
    dead_calls: list[list[str]] = []

    def fake_dead(cmd, **kw):
        dead_calls.append(list(cmd))
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": "can't find session: $0\n"})()

    monkeypatch.setattr("ai_cli.tmux_ownership.subprocess.run", fake_dead)
    assert respawn_owned_tmux_pane(identity, ("/bin/sh", "-c", "echo hi"), tmux_command=("tmux",)) is False
    assert [c[1] for c in dead_calls] == ["if-shell", "display-message"]

    # 3. Fence matched and session generation is positively live, but `--` failed (older tmux):
    #    retries once without `--` still under the same fence.
    retry_calls: list[list[str]] = []

    def fake_retry(cmd, **kw):
        retry_calls.append(list(cmd))
        if cmd[1] == "display-message":
            return type("R", (), {"returncode": 0, "stdout": "$0\tg-myproject-1\tgen-abc123\n", "stderr": ""})()
        rc = 1 if " -- " in cmd[6] else 0
        return type("R", (), {"returncode": rc, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("ai_cli.tmux_ownership.subprocess.run", fake_retry)
    assert respawn_owned_tmux_pane(identity, ("/bin/sh", "-c", "echo hi"), tmux_command=("tmux",)) is True
    if_shell_calls = [c for c in retry_calls if c[1] == "if-shell"]
    assert len(if_shell_calls) == 2
    assert " -- " in if_shell_calls[0][6]
    assert " -- " not in if_shell_calls[1][6]
    assert "$0|gen-abc123" in if_shell_calls[1][5]
    assert respawn_owned_tmux_pane(identity, (), tmux_command=("tmux",)) is False


def test_given_unmarked_placeholder_when_killed_then_fences_on_id_name_and_empty_markers(monkeypatch):
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("ai_cli.tmux_ownership.subprocess.run", fake_run)
    assert kill_unmarked_tmux_placeholder("$5", "g-myproject-1", tmux_command=("tmux",)) is True
    assert len(seen) == 1
    assert seen[0][:5] == ["tmux", "if-shell", "-F", "-t", "$5"]
    assert "$5|g-myproject-1||" in seen[0][5]
    assert seen[0][6] == "kill-session -t '$5'"

    # Invalid session IDs or unsafe session names are rejected BEFORE calling tmux.
    seen.clear()
    for bad_id, bad_name in (
        ("", "g-myproject-1"),
        ("not-an-id", "g-myproject-1"),
        ("$5", ""),
        ("$5", "bad,name"),
        ("$5", "bad}name"),
        ("$5", "bad name"),
    ):
        assert kill_unmarked_tmux_placeholder(bad_id, bad_name, tmux_command=("tmux",)) is False
    assert seen == []

    # Fence mismatch returns False.
    monkeypatch.setattr(
        "ai_cli.tmux_ownership.subprocess.run",
        lambda cmd, **kw: type("R", (), {"returncode": 0, "stdout": "__ai_cli_ownership_mismatch__\n", "stderr": ""})(),
    )
    assert kill_unmarked_tmux_placeholder("$5", "g-myproject-1", tmux_command=("tmux",)) is False

    # TimeoutExpired / OSError return False cleanly without raising.
    def raise_timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 10.0)

    monkeypatch.setattr("ai_cli.tmux_ownership.subprocess.run", raise_timeout)
    assert kill_unmarked_tmux_placeholder("$5", "g-myproject-1", tmux_command=("tmux",)) is False
    assert capture_tmux_session_identity("$5", tmux_command=("tmux",)) is None
    assert set_owned_tmux_option(TmuxSessionIdentity("$5", "g-myproject-1", "gen-1"), "@ai_cli_client", "agy") is False
