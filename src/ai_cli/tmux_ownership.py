"""Generation-fenced ownership checks for destructive tmux operations."""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

import psutil

_IDENTITY_FORMAT = "#{session_id}\t#{session_name}\t#{@ai_cli_session_generation}"
_FENCE_FORMAT = "#{session_id}|#{@ai_cli_session_generation}"
_FENCE_MISMATCH = "__ai_cli_ownership_mismatch__"
_PANE_PID_FORMAT = "#{pane_pid}"
_SESSION_ID_RE = re.compile(r"^\$\d+$")
_GENERATION_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_PROCESS_TERMINATION_TIMEOUT_SECONDS = 1.0


def classify_new_session_output(stdout: str | None) -> tuple[str, str]:
    """Classify the stdout of ``tmux new-session -P -F '#{session_id}'``.

    Returns ``(status, raw)`` where status is:

    - ``"ok"`` -- ``raw`` is a validated opaque session id such as ``$3``.
    - ``"format-not-expanded"`` -- tmux echoed the format back instead of expanding
      it, so the session almost certainly EXISTS but its opaque id is unobtainable
      by this route.
    - ``"unusable"`` -- any other answer, including empty.

    The middle case needs separating because the id regex alone cannot tell it from
    a generic failure, yet the remedy is different: a tmux that does not expand
    formats will not start expanding them on retry, so the operator needs to launch
    without tmux rather than try again. Observed as ``#{session_id}`` arriving as the
    literal ``#session_id``; a server reporting its version as the literal
    ``#version`` is the same fault seen through a different format.
    """
    raw = (stdout or "").strip()
    if _SESSION_ID_RE.match(raw):
        return "ok", raw
    if "#" in raw:
        return "format-not-expanded", raw
    return "unusable", raw


@dataclass(frozen=True)
class TmuxSessionIdentity:
    """Immutable identity captured from one managed tmux session generation."""

    session_id: str
    session_name: str
    generation: str


def capture_tmux_session_identity(
    target: str,
    *,
    expected_generation: str | None = None,
    tmux_command: Sequence[str] = ("tmux",),
) -> TmuxSessionIdentity | None:
    """Capture a validated opaque ID and managed-generation marker."""
    try:
        result = subprocess.run(
            [*tmux_command, "display-message", "-p", "-t", target, _IDENTITY_FORMAT],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    output = result.stdout if isinstance(result.stdout, str) else ""
    parts = output.rstrip("\n").split("\t")
    if len(parts) != 3:
        return None
    session_id, session_name, generation = parts
    if (
        not _SESSION_ID_RE.fullmatch(session_id)
        or not session_name
        or not _GENERATION_RE.fullmatch(generation)
        or (expected_generation is not None and generation != expected_generation)
    ):
        return None
    return TmuxSessionIdentity(session_id, session_name, generation)


def kill_owned_tmux_session(
    identity: TmuxSessionIdentity,
    *,
    tmux_command: Sequence[str] = ("tmux",),
    terminate_pane_processes: bool = False,
    process_timeout: float = _PROCESS_TERMINATION_TIMEOUT_SECONDS,
) -> bool:
    """Atomically revalidate ``identity`` and kill only that exact generation.

    When requested, snapshot the pane's process tree before killing the session,
    then terminate those exact processes with bounded graceful and forced phases.
    Capturing first matters because tmux teardown can orphan a TERM-resistant child,
    after which it is no longer discoverable from the pane process.
    """
    if not _SESSION_ID_RE.fullmatch(identity.session_id) or not _GENERATION_RE.fullmatch(identity.generation):
        return False
    pane_processes = _capture_pane_process_tree(identity.session_id, tmux_command) if terminate_pane_processes else []
    expected = f"{identity.session_id}|{identity.generation}"
    predicate = f"#{{==:{_FENCE_FORMAT},{expected}}}"
    try:
        result = subprocess.run(
            [
                *tmux_command,
                "if-shell",
                "-F",
                "-t",
                identity.session_id,
                predicate,
                f"kill-session -t '{identity.session_id}'",
                f"display-message -p {_FENCE_MISMATCH}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    output = result.stdout if isinstance(result.stdout, str) else ""
    killed = result.returncode == 0 and _FENCE_MISMATCH not in output
    if killed and pane_processes:
        _terminate_processes(pane_processes, process_timeout)
    return killed


def _capture_pane_process_tree(target: str, tmux_command: Sequence[str]) -> list[psutil.Process]:
    """Return the pane process and descendants while their relationship exists."""
    try:
        result = subprocess.run(
            [*tmux_command, "display-message", "-p", "-t", target, _PANE_PID_FORMAT],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    raw_pid = result.stdout.strip() if result.returncode == 0 and isinstance(result.stdout, str) else ""
    if not raw_pid.isascii() or not raw_pid.isdecimal():
        return []
    pane_pid = int(raw_pid)
    if pane_pid <= 0 or pane_pid == os.getpid():
        return []
    try:
        pane_process = psutil.Process(pane_pid)
        descendants = pane_process.children(recursive=True)
    except (OSError, psutil.Error):
        return []
    return [process for process in [*descendants, pane_process] if process.pid != os.getpid()]


def _terminate_processes(processes: list[psutil.Process], timeout: float) -> None:
    """Terminate exact process handles, escalating after a bounded wait."""
    for process in processes:
        with contextlib.suppress(OSError, psutil.Error):
            process.terminate()
    try:
        _, alive = psutil.wait_procs(processes, timeout=max(timeout, 0.0))
    except (OSError, psutil.Error):
        alive = processes
    for process in alive:
        with contextlib.suppress(OSError, psutil.Error):
            process.kill()
    with contextlib.suppress(OSError, psutil.Error):
        psutil.wait_procs(alive, timeout=max(timeout, 0.0))
