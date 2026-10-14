"""The session script resets an agent's terminal with the transport's own sequence.

Two hand-written copies of the reset drifted apart: the session script lost the
autowrap and attribute resets and sent a different modifyOtherKeys form
(AI-CLI-no36). The generated ``_reset_agent_terminal`` now renders the transport
constant, so this runs that function from a real rendered script on a real pty and
compares what reaches the terminal with what the transport sends.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

from ai_cli.session_script import get_engine_script
from ai_cli.transport import _TERMINAL_RESTORE

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty and bash")


def _reset_function(script: str) -> str:
    match = re.search(r"^\s*_reset_agent_terminal\(\) \{\n.*?^\s*\}\n", script, re.MULTILINE | re.DOTALL)
    assert match, "the rendered script defines no _reset_agent_terminal"
    return match.group(0)


def _run_on_pty(command: str) -> bytes:
    # Local: the pty module does not exist on Windows, where this file is skipped.
    import pty
    import select

    leader, follower = pty.openpty()
    chunks: list[bytes] = []
    try:
        proc = subprocess.Popen(["bash", "-c", command], stdin=follower, stdout=follower, stderr=subprocess.DEVNULL)
        # Drain while the child runs: macOS discards unread pty output once the last
        # follower descriptor closes, so reading only afterwards sees nothing.
        while True:
            ready, _, _ = select.select([leader], [], [], 0.1)
            if ready:
                chunks.append(os.read(leader, 65536))
            elif proc.poll() is not None:
                break
        assert proc.wait(timeout=60) == 0
        return b"".join(chunks)
    finally:
        os.close(follower)
        os.close(leader)


def test_given_a_rendered_session_script_when_the_agent_reset_runs_on_a_terminal_then_it_sends_the_transport_restore():
    script = get_engine_script("c", "myproject-1", "c-myproject-1", "c-myproject-", "myproject")

    written = _run_on_pty(_reset_function(script) + "_reset_agent_terminal\n")

    assert _TERMINAL_RESTORE.encode() in written
