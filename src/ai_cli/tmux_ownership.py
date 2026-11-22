"""Generation-fenced ownership checks for destructive tmux operations."""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

import psutil

from . import tmux_binary

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
    tmux_command: Sequence[str] | None = None,
) -> TmuxSessionIdentity | None:
    """Capture a validated opaque ID and managed-generation marker."""
    try:
        result = subprocess.run(
            [*tmux_binary.command(tmux_command), "display-message", "-p", "-t", target, _IDENTITY_FORMAT],
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
    tmux_command: Sequence[str] | None = None,
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
                *tmux_binary.command(tmux_command),
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


#: Where a client that ``ai c`` switched into a session came from: its session, and its tty.
RETURN_SESSION_OPTION = "@ai_cli_return_session"
RETURN_CLIENT_OPTION = "@ai_cli_return_client"


def _tmux(args: Sequence[str], tmux_command: Sequence[str] | None) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            [*tmux_binary.command(tmux_command), *args], capture_output=True, text=True, check=False, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None


def configure_session_end(session_id: str, *, tmux_command: Sequence[str] | None = None) -> list[str]:
    """Make tmux end session ``session_id`` for whoever is looking at it once its pane is dead.

    The session's supervisor ends its own tmux session on every ending it reaches. These
    settings end it when the supervisor cannot: it exited before it owned the session, or
    it was killed. ``remain-on-exit`` keeps a dead pane for diagnosis only while no client
    is attached. The ``pane-died`` hook ends the session if one is, and ``client-attached``
    ends it when one attaches to a dead pane later. A destroyed session detaches its clients
    (``detach-on-destroy on``) whatever the tmux configuration says, so a client is never
    moved into an unrelated session; one switched in from another session is sent back by
    :func:`return_switched_client` first. Hooks need tmux 2.2 or later, and the delayed end on
    attach tmux 3.2 (``run-shell -d -C``).

    Returns the names of the settings tmux refused, empty when every one applied.
    """
    if not _SESSION_ID_RE.fullmatch(session_id):
        return ["session id"]
    end = f"kill-session -t '{session_id}'"
    settings = (
        ("remain-on-exit", ("set-window-option", "-t", session_id, "remain-on-exit", "on")),
        ("detach-on-destroy", ("set-option", "-t", session_id, "detach-on-destroy", "on")),
        ("pane-died", ("set-hook", "-t", session_id, "pane-died", f"if-shell -F '#{{session_attached}}' \"{end}\"")),
        # A second's delay lets the new client read its terminal's replies to the queries it
        # sends on attach; ended at once, those replies arrive at the shell as typed text.
        (
            "client-attached",
            (
                "set-hook",
                "-t",
                session_id,
                "client-attached",
                f'if-shell -F \'#{{pane_dead}}\' "run-shell -d 1 -C \\"{end}\\""',
            ),
        ),
    )
    refused = []
    for name, args in settings:
        result = _tmux(args, tmux_command)
        if result is None or result.returncode != 0:
            refused.append(name)
    return refused


def session_id_of(target: str, *, tmux_command: Sequence[str] | None = None) -> str | None:
    """The opaque id (``$N``) of the session ``target`` names, or None.

    ``display-message`` takes a pane target, where an exact session name (``=NAME``) needs
    its trailing ``:``; a session id, a pane id or a full pane target is used as given.
    """
    if target.startswith("=") and ":" not in target:
        target = f"{target}:"
    result = _tmux(("display-message", "-p", "-t", target, "#{session_id}"), tmux_command)
    value = result.stdout.strip() if result is not None and result.returncode == 0 else ""
    return value if _SESSION_ID_RE.fullmatch(value) else None


def record_return(
    target: str, origin_session_id: str, client_tty: str, *, tmux_command: Sequence[str] | None = None
) -> bool:
    """Record on session ``target`` that ``client_tty`` came from ``origin_session_id``.

    Refused (False) when ``target`` is the origin itself or tmux cannot record it.
    """
    target_id = session_id_of(target, tmux_command=tmux_command)
    if target_id is None or target_id == origin_session_id or not _SESSION_ID_RE.fullmatch(origin_session_id):
        return False
    for option, value in ((RETURN_SESSION_OPTION, origin_session_id), (RETURN_CLIENT_OPTION, client_tty)):
        result = _tmux(("set-option", "-t", target_id, option, value), tmux_command)
        if result is None or result.returncode != 0:
            return False
    return True


def return_switched_client(session_id: str, *, tmux_command: Sequence[str] | None = None) -> bool:
    """Send the client ``ai c`` switched into ``session_id`` back to the session it came from.

    Only while that client still shows ``session_id`` and its own session still exists: a
    client that has moved on is left where it is, and one whose session is gone is detached
    with the rest when ``session_id`` ends. Returns whether a client was sent back.
    """
    if not _SESSION_ID_RE.fullmatch(session_id):
        return False
    values = []
    for option in (RETURN_SESSION_OPTION, RETURN_CLIENT_OPTION):
        result = _tmux(("show-options", "-qv", "-t", session_id, option), tmux_command)
        values.append(result.stdout.strip() if result is not None and result.returncode == 0 else "")
    origin, client = values
    if not _SESSION_ID_RE.fullmatch(origin) or not client:
        return False
    showing = _tmux(("display-message", "-p", "-c", client, "#{session_id}"), tmux_command)
    if showing is None or showing.returncode != 0 or showing.stdout.strip() != session_id:
        return False
    if session_id_of(origin, tmux_command=tmux_command) != origin:
        return False
    switched = _tmux(("switch-client", "-c", client, "-t", origin), tmux_command)
    return switched is not None and switched.returncode == 0


def _capture_pane_process_tree(target: str, tmux_command: Sequence[str] | None) -> list[psutil.Process]:
    """Return the pane process and descendants while their relationship exists."""
    try:
        result = subprocess.run(
            [*tmux_binary.command(tmux_command), "display-message", "-p", "-t", target, _PANE_PID_FORMAT],
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
