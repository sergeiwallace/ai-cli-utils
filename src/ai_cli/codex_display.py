"""Give a Codex child the display of the user's own verified local graphical session.

Codex copies to the native clipboard through the X11/Wayland display. A launch from a
remote shell has neither ``DISPLAY`` nor ``XAUTHORITY``, so that copy fails even while
the same user is signed in to a local desktop (for example over remote desktop). This
module finds that desktop and returns its pair, but only when it can verify it:

* exactly one logind session of the caller's UID is ``State=active``, ``Remote=no`` and
  of ``Type`` ``x11`` or ``wayland``;
* the pair comes from the same user's systemd user manager environment, and for an X11
  session whose logind record names a display, that display matches;
* the ``XAUTHORITY`` file exists, is owned by the caller and is not older than the session.

Anything else returns no pair and a reason for the launch log. Nothing here guesses a
display number or an authority path.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

_PROBE_TIMEOUT_SECONDS = 5
_GRAPHICAL_TYPES = ("x11", "wayland")
_DISPLAY_PATTERN = re.compile(r"[A-Za-z0-9._/-]*:[0-9]+(\.[0-9]+)?")
_SESSION_PROPERTIES = ("Id", "User", "State", "Remote", "Type", "Display", "TimestampMonotonic")


@dataclass(frozen=True)
class DisplayResolution:
    """The variables to add to the Codex child, and why none were added when empty."""

    values: dict[str, str] = field(default_factory=dict)
    reason: str = ""


def _run(command: list[str], env: Mapping[str, str]) -> str | None:
    try:
        completed = subprocess.run(
            command,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            errors="replace",
            timeout=_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _properties(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def resolve_codex_display(env: Mapping[str, str]) -> DisplayResolution:
    """Return the verified ``DISPLAY``/``XAUTHORITY`` pair for a Codex child of ``env``.

    A variable the caller already set is never replaced, and when only one of the two is
    set no detection runs, since the other half could belong to a different display.
    """
    # Linux only: logind and a systemd user manager are Linux facilities, and macOS and
    # Windows clipboards do not go through DISPLAY. Elsewhere this returns nothing, silently.
    if not sys.platform.startswith("linux"):
        return DisplayResolution()
    has_display = bool(env.get("DISPLAY"))
    has_authority = bool(env.get("XAUTHORITY"))
    if has_display and has_authority:
        return DisplayResolution()
    if has_display or has_authority:
        kept = "DISPLAY" if has_display else "XAUTHORITY"
        return DisplayResolution(reason=f"{kept} is set by the caller; not pairing it with a detected display")

    uid = os.getuid()
    probe_env = dict(env)
    # `systemctl --user` reaches the user manager through the runtime directory.
    probe_env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")

    listed = _run(["loginctl", "list-sessions", "--no-legend"], probe_env)
    if listed is None:
        return DisplayResolution(reason="loginctl list-sessions failed; no graphical session verified")
    candidates: list[dict[str, str]] = []
    for line in listed.splitlines():
        columns = line.split()
        if len(columns) < 2 or columns[1] != str(uid):
            continue
        shown = _run(
            ["loginctl", "show-session", columns[0], *(f"--property={name}" for name in _SESSION_PROPERTIES)],
            probe_env,
        )
        if shown is None:
            continue
        session = _properties(shown)
        if (
            session.get("User") == str(uid)
            and session.get("State") == "active"
            and session.get("Remote") == "no"
            and session.get("Type") in _GRAPHICAL_TYPES
        ):
            candidates.append(session)
    if len(candidates) != 1:
        return DisplayResolution(
            reason=f"{len(candidates)} active local graphical sessions for this user; need exactly one"
        )
    session = candidates[0]
    label = f"session {session.get('Id', '?')} ({session['Type']})"

    manager = _run(["systemctl", "--user", "show-environment"], probe_env)
    if manager is None:
        return DisplayResolution(reason=f"systemctl --user show-environment failed; {label} not paired")
    manager_env = _properties(manager)
    display = manager_env.get("DISPLAY", "")
    authority = manager_env.get("XAUTHORITY", "")
    if not display or not authority:
        return DisplayResolution(reason=f"the user manager environment has no DISPLAY and XAUTHORITY pair for {label}")
    if not _DISPLAY_PATTERN.fullmatch(display) or not Path(authority).is_absolute():
        return DisplayResolution(
            reason=f"the user manager's DISPLAY or XAUTHORITY is not a plain value; {label} not paired"
        )
    session_display = session.get("Display", "")
    if session_display and session_display != display:
        return DisplayResolution(
            reason=f"the user manager's DISPLAY {display} does not match {label}'s display {session_display}"
        )

    try:
        stat = Path(authority).stat()
    except OSError:
        return DisplayResolution(reason=f"XAUTHORITY {authority} for {label} is missing")
    if stat.st_uid != uid:
        return DisplayResolution(reason=f"XAUTHORITY {authority} for {label} is not owned by this user")
    try:
        started_monotonic = int(session.get("TimestampMonotonic", "")) / 1_000_000
    except ValueError:
        return DisplayResolution(reason=f"{label} has no start time; XAUTHORITY {authority} not verified")
    started_at = time.time() - (time.clock_gettime(time.CLOCK_MONOTONIC) - started_monotonic)
    if stat.st_mtime < started_at:
        return DisplayResolution(reason=f"XAUTHORITY {authority} is older than {label}; treating it as stale")
    return DisplayResolution(
        values={"DISPLAY": display, "XAUTHORITY": authority},
        reason=f"using DISPLAY {display} and XAUTHORITY {authority} from {label}",
    )
