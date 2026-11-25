"""No process the launcher starts on Windows opens a console window of its own.

A Windows process started with no console (DETACHED_PROCESS) gives every console
program it then starts, tmux and git included, a new visible console window: the
"cmd windows" that popped up while a session ended itself.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

from ai_cli import session_exit

DETACHED_PROCESS = 0x00000008
CREATE_NEW_CONSOLE = 0x00000010
CREATE_NO_WINDOW = 0x08000000
PACKAGE = Path(session_exit.__file__).parent


def test_given_windows_when_a_session_exit_continues_detached_then_it_runs_on_a_hidden_console(tmp_path):
    with (
        patch.object(session_exit.sys, "platform", "win32"),
        patch.object(session_exit.subprocess, "Popen") as popen,
    ):
        session_exit.detach_cleanup("c-session-1", host="host-a", generation="generation-1", timeout=5)
    flags = popen.call_args.kwargs["creationflags"]
    assert flags & CREATE_NO_WINDOW
    assert not flags & (DETACHED_PROCESS | CREATE_NEW_CONSOLE)


def test_given_the_package_source_when_scanned_then_nothing_starts_a_console_less_or_new_console_process():
    pattern = re.compile(r"\b(DETACHED_PROCESS|CREATE_NEW_CONSOLE)\b|0x0*000000[18]\b")
    found = [
        f"{path.relative_to(PACKAGE)}:{number}: {line.strip()}"
        for path in sorted(PACKAGE.rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert found == []
