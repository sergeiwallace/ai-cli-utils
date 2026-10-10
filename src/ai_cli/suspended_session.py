"""Resume a session that Ctrl+Z left stopped in its own terminal (AI-CLI-y6el).

Depends on: nothing (self-contained).

Claude Code turns Ctrl+Z into a SIGTSTP to its own process group. In an ``ai c``
pane that group is the terminal's foreground group, and nothing in the pane has
job control, so no key the operator types can continue it. The session script
carries an in-pane watchdog for that (``session_script.SUSPEND_WATCHDOG_SNIPPET``);
this module is the launcher's half, for a session that is found already stopped.

"Stopped AND still its terminal's foreground group" is what separates a session
suspended in place from AI-CLI-2139's abandoned process. The suspended one is still
what its pane shows, so it is resumed. A stopped process that does not own its
terminal's foreground has nothing attached to it, and 2139 reclaims that one.
"""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path


def _terminal_view(pid: int) -> tuple[int, int, str] | None:
    """``(pgid, terminal foreground pgid, state)`` for ``pid``, or None when unreadable."""
    if Path("/proc/self/stat").exists():
        try:
            line = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        # Fields after the last ")": state ppid pgrp session tty_nr tpgid ...
        fields = line.rpartition(")")[2].split()
        try:
            return int(fields[2]), int(fields[5]), fields[0]
        except (IndexError, ValueError):
            return None
    try:
        out = subprocess.run(
            ["ps", "-o", "pgid=,tpgid=,stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        ).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        return int(out[0]), int(out[1]), out[2][:1]
    except (IndexError, ValueError):
        return None


#: What each process probe calls a job-control stop: ``T`` from ``/proc`` and ``ps``,
#: ``stopped`` from psutil. A tracing stop is not one; that process belongs to its debugger.
JOB_CONTROL_STOP_STATES = frozenset({"T", "stopped"})


def resume_if_suspended_in_terminal(pid: int, state: str | None = None) -> int | None:
    """Continue ``pid``'s process group if it was suspended in place; return that group, else None.

    Suspended in place means: job-control stopped, its group owns its terminal's
    foreground, and that group is not the caller's own. The last condition matters
    for a launcher run from a terminal: it shares its foreground group with what it
    forked, but Ctrl+Z stops the whole group, and the caller is running.

    ``state`` is the caller's own fresh reading, when it has one. The state is the
    only thing a Ctrl+Z or the in-pane watchdog's SIGCONT changes; the group and the
    terminal's foreground stay put (nothing in an ``ai c`` pane has job control to
    move them). So a caller judging on its own state reading and this function's
    group reading is still judging one moment.
    """
    sigcont = getattr(signal, "SIGCONT", None)
    view = _terminal_view(pid)
    if sigcont is None or view is None:
        return None
    pgid, foreground, viewed_state = view
    if state is None:
        state = viewed_state
    own_group = os.getpgrp() if hasattr(os, "getpgrp") else -1
    if state not in JOB_CONTROL_STOP_STATES or pgid <= 0 or pgid != foreground or pgid == own_group:
        return None
    try:
        os.killpg(pgid, sigcont)
    except OSError:
        return None
    return pgid


def resume_suspended_panes(pane_pids: list[int]) -> list[int]:
    """Continue each pane's terminal foreground group that is stopped; return those groups.

    Looks only at each pane's foreground group, so it never continues a process the
    operator deliberately stopped in the background of an interactive pane shell.
    """
    resumed: list[int] = []
    for pane_pid in pane_pids:
        view = _terminal_view(pane_pid)
        if view is None or view[1] <= 0:
            continue
        group = resume_if_suspended_in_terminal(view[1])
        if group is not None:
            resumed.append(group)
    return resumed
