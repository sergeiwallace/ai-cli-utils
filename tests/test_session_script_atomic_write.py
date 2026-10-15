"""A session script is replaced, never rewritten under the shell running it (AI-CLI-59g7).

The supervisor executes its session's stable script, and bash and zsh read a running script
incrementally. `ai update` refreshed that file with an in-place write (same inode, truncated
and refilled), so the running supervisor resumed at its old byte offset inside the new text.
Measured live: it ran the new file's `tmux_session="$1"` line with no argument and then read
`session-reload-reason-` with an empty session name.
"""

import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ai_cli.main import _write_launch_script_if_changed

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="session scripts are POSIX shell scripts")

_WAIT_SECONDS = 30


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_given_a_shell_running_the_script_when_it_is_refreshed_then_the_shell_finishes_its_own_program(
    tmp_path: Path, shell: str
):
    shell_path = shutil.which(shell)
    if shell_path is None:
        pytest.skip(f"{shell} is not installed")
    out = tmp_path / "out"
    go = tmp_path / "go"
    script_path = tmp_path / "sessions" / "c-session-1.sh"
    head = f"echo start > '{out}'\nwhile [ ! -f '{go}' ]; do sleep 0.02; done\n"
    running = head + f"echo old-end >> '{out}'\n"
    # The refreshed text puts a different command exactly where the running shell's next read
    # begins, so a shell that reads the new bytes at its old offset runs it.
    refreshed = "#" + "x" * (len(head.encode()) - 2) + "\n" + f"echo HIJACK >> '{out}'\n"
    _write_launch_script_if_changed(script_path, running)
    process = subprocess.Popen([shell_path, str(script_path)], stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        deadline = time.monotonic() + _WAIT_SECONDS
        while time.monotonic() < deadline and not (out.exists() and out.read_text().strip()):
            time.sleep(0.02)
        assert out.read_text().strip() == "start", "the shell never started the script"

        assert _write_launch_script_if_changed(script_path, refreshed) is True
        go.write_text("")
        process.wait(timeout=_WAIT_SECONDS)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

    assert out.read_text().splitlines() == ["start", "old-end"]
    assert script_path.read_text(encoding="utf-8") == refreshed
