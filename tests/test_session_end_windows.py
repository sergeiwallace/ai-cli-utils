"""On native Windows the session program ends the way it does everywhere else.

Git Bash runs the same generated session program under psmux, where no test here can
attach a real client, so this pins the program the Windows launcher generates: no ending
hands the pane to a shell, and every ending reaches the supervisor's teardown, which
returns a client that was switched in and then ends the session's own tmux session.
"""

from __future__ import annotations

import re
import sys

import pytest

from ai_cli.session_script import get_engine_script


class _WindowsSys:
    platform = "win32"

    def __getattr__(self, name):
        return getattr(sys, name)


@pytest.mark.parametrize("remote", [False, True])
def test_given_windows_launcher_when_session_program_is_generated_then_no_ending_leaves_a_shell_in_the_pane(
    monkeypatch, remote
):
    # Only the generator sees Windows: the test process still resolves programs as itself.
    monkeypatch.setattr("ai_cli.session_script.sys", _WindowsSys())

    script = get_engine_script("c", "session-1", "c-session-1", "c-session-", "myproject", is_remote=remote)

    assert not re.search(r'exec "\$SHELL"|"\$SHELL";|exec \S+ -i\b', script), "an ending runs a shell in the pane"
    assert "_supervisor_return_to_shell" not in script
    assert "ai internal return-tmux-client" in script
    assert "kill-session -t '$_supervisor_tmux_session_id'" in script
    epilogue = script.rstrip().rsplit("\n", 1)[-1].strip()
    assert epilogue == "exit 77", epilogue
