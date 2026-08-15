"""Interpreter/direnv resolution on the tmux launch path (AI-CLI zsh+direnv portability).

Two hard dependencies used to be baked, unguarded, into the path that starts a
session:

1. ``zsh`` as the tmux pane interpreter. On a host without zsh the pane's exec
   failed, the pane died, tmux tore the session down, and ``tmux attach`` printed
   a bare ``[exited]``.
2. ``direnv exec`` inside the generated session script's ``run_agent``. On a host
   without direnv every launch exited 127 before the agent ever started.

Both are asserted here **behaviourally against the real boundary**, never by
grepping the produced argv or script text for a tool name:

- the tmux tests drive ``_do_session_launch`` against a real ``tmux`` server on an
  isolated socket and assert the pane actually ran the script and survived;
- the direnv tests run the real ``run_agent`` body from the real generated
  template under a real ``bash`` and assert the agent command actually executed.

Each behaviour is pinned under *both* conditions — tool absent and tool present —
using a hermetic PATH, so neither test can pass merely because this machine
happens to lack (or have) zsh or direnv. The "present" cases are the negative
constraint: the fix must not stop preferring zsh/direnv where they exist.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import libtmux
import pytest

from ai_cli.main import _do_session_launch
from ai_cli.session_script import get_engine_script

# Binaries the launch path and the harness scripts legitimately reach for. The
# hermetic PATH is built from symlinks to exactly these, which is what makes
# "zsh is absent" / "direnv is absent" a property of the test rather than of the
# machine it happens to run on.
_CLEAN_BIN_TOOLS = (
    "tmux",
    "bash",
    "sh",
    "env",
    "git",
    "touch",
    "sleep",
    "stat",
    "date",
    "cat",
    "grep",
    "sed",
    "tr",
    "wc",
    "head",
    "tail",
    "mkdir",
    "rm",
    "ls",
    "uname",
    "true",
    "false",
)


def _clean_bin(tmp_path: Path, name: str = "cleanbin") -> Path:
    """A PATH directory holding only ``_CLEAN_BIN_TOOLS`` — never zsh or direnv."""
    bin_dir = tmp_path / name
    bin_dir.mkdir(parents=True, exist_ok=True)
    for tool in _CLEAN_BIN_TOOLS:
        resolved = shutil.which(tool)
        if resolved:
            target = bin_dir / tool
            if not target.exists():
                target.symlink_to(resolved)
    return bin_dir


def _tmux_runnable() -> tuple[bool, str]:
    if shutil.which("tmux") is None:
        return False, "tmux binary not available on PATH"
    try:
        probe = subprocess.run(["tmux", "-V"], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"tmux could not be executed: {exc}"
    return (probe.returncode == 0), "tmux is on PATH but does not run"


_TMUX_RUNNABLE, _TMUX_SKIP_REASON = _tmux_runnable()

pytestmark = [
    pytest.mark.real_tmux,
    pytest.mark.skipif(sys.platform == "win32", reason="tmux session template is POSIX-only"),
]


@pytest.fixture
def real_tmux_socket():
    """An isolated tmux socket path; the server is killed on teardown."""
    if not _TMUX_RUNNABLE:
        pytest.skip(_TMUX_SKIP_REASON)
    sock_dir = tempfile.mkdtemp(prefix="ai-cli-shellres-")
    sock = f"{sock_dir}/tmux.sock"
    yield sock
    try:
        server = libtmux.Server(socket_path=sock)
        for session in list(server.sessions):
            try:
                session.kill_session()
            except Exception:
                pass
    except Exception:
        pass
    subprocess.run(["tmux", "-S", sock, "kill-server"], capture_output=True, check=False)
    shutil.rmtree(sock_dir, ignore_errors=True)


def _launch_kwargs(name: str) -> dict:
    return {
        "engine": "c",
        "name": name,
        "resume": False,
        "once": False,
        "bare": False,
        "notify": False,
        "sandbox": False,
        "no_worktree": True,
        "remote": False,
        "project": "",
        "is_remote": False,
        "project_prefix_override": "myproject",
        "extra_args": [],
        "config": {"worktree": {"enabled": False}},
    }


def _drive_launch_against_real_tmux(sock: str, tmp_path: Path, name: str, script_body: str) -> None:
    """Run ``_do_session_launch`` so its real ``tmux new-session`` lands on ``sock``.

    Only two things are substituted: the tmux socket (so the test cannot touch the
    user's server) and ``git`` (kept hermetic). The ``new-session`` argv itself —
    including whichever interpreter the launch path chose — is executed verbatim
    by the real tmux binary. That is the boundary the defect lives on, so it is
    deliberately not mocked.
    """

    class _OK:
        returncode = 0
        stdout = ""
        stderr = ""

    # Bound before the patch below, otherwise `subprocess.run` inside fake_run
    # resolves back to the mock that is calling it.
    outer_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and cmd:
            if cmd[0] == "tmux":
                return outer_run(["tmux", "-S", sock, *cmd[1:]], *args, **kwargs)
            if cmd[0] == "git":
                return _OK()
        return outer_run(cmd, *args, **kwargs)

    def fake_execvp(file, argv):
        # The real code ends in `tmux attach-session`, which would replace the
        # pytest process. Stop here; the detached session is already created.
        raise SystemExit(0)

    with (
        patch("ai_cli.main.subprocess.run", side_effect=fake_run),
        patch("ai_cli.iterm2.subprocess.run", side_effect=fake_run),
        patch("ai_cli.main.os.execvp", side_effect=fake_execvp),
        patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.session.cleanup_stale_sessions"),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.iterm2._load_iterm2_config", return_value={}),
        patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
        patch("ai_cli.iterm2._emit_iterm2_profile_setup"),
        patch("ai_cli.iterm2._configure_tmux_for_iterm2"),
        patch("ai_cli.session_script.get_engine_script", return_value=script_body),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
    ):
        with pytest.raises(SystemExit):
            _do_session_launch(**_launch_kwargs(name))


def _wait_for_file(path: Path, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


def _session_alive(sock: str, session_id: str) -> bool:
    probe = subprocess.run(
        ["tmux", "-S", sock, "has-session", "-t", session_id],
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


def test_given_no_zsh_on_path_when_session_launched_then_pane_runs_the_script(real_tmux_socket, tmp_path, monkeypatch):
    """With no zsh installed, ``ai c`` must still start a live tmux session.

    The pane is asserted to have actually executed the session script (marker
    file) and to still be alive afterwards. When the interpreter is hardcoded to
    an absent zsh, tmux reports success on ``new-session`` but the pane's exec
    fails immediately, the session is torn down, and the user sees ``[exited]``.
    """
    bin_dir = _clean_bin(tmp_path)
    monkeypatch.setenv("PATH", str(bin_dir))
    assert shutil.which("zsh") is None, "hermetic PATH must not expose zsh"
    assert shutil.which("tmux") is not None, "hermetic PATH must still expose tmux"

    marker = tmp_path / "script-ran"
    _drive_launch_against_real_tmux(
        real_tmux_socket,
        tmp_path,
        name="1",
        script_body=f"touch {marker}\nsleep 30\n",
    )

    assert _wait_for_file(marker), (
        "session script never executed — the tmux pane died instead of starting, "
        "which is exactly the `[exited]` symptom"
    )
    assert _session_alive(real_tmux_socket, "c-myproject-1"), "tmux session did not survive its own launch"


def test_given_zsh_on_path_when_session_launched_then_zsh_is_still_the_interpreter(
    real_tmux_socket, tmp_path, monkeypatch
):
    """zsh must stay the *preferred* interpreter wherever it exists.

    Negative constraint for the portability fix: hosts that have zsh (Mac, Debian)
    must not silently switch to another shell. Asserted by the interpreter itself
    recording that it ran — not by inspecting the argv for a name.
    """
    bin_dir = _clean_bin(tmp_path)
    zsh_ran = tmp_path / "zsh-was-the-interpreter"
    fake_zsh = bin_dir / "zsh"
    fake_zsh.write_text(f'#!/bin/sh\necho ran > "{zsh_ran}"\nexec /bin/sh "$@"\n')
    fake_zsh.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    assert shutil.which("zsh") == str(fake_zsh)

    marker = tmp_path / "script-ran"
    _drive_launch_against_real_tmux(
        real_tmux_socket,
        tmp_path,
        name="2",
        script_body=f"touch {marker}\nsleep 30\n",
    )

    assert _wait_for_file(zsh_ran), "zsh was on PATH but the pane was not started with it"
    assert _wait_for_file(marker), "session script never executed"


# --- direnv guard in the generated session script -------------------------------


def _run_agent_body() -> str:
    """The real ``run_agent`` definition, sliced verbatim out of the real template."""
    script = get_engine_script("c", "sr-1", "c-sr-1", "c-sr-", "sr", worktree_dir="/tmp/wt", project_name="myproject")
    start = script.index("run_agent() {")
    end = script.index("\n    }\n", start) + len("\n    }\n")
    return script[start:end]


def _invoke_run_agent(tmp_path: Path, bin_dir: Path, agent_marker: Path) -> subprocess.CompletedProcess:
    """Execute the real ``run_agent`` under a real bash with a hermetic PATH."""
    body = tmp_path / "run_agent_body.sh"
    body.write_text(_run_agent_body())
    harness = tmp_path / "run_agent_harness.sh"
    harness.write_text(
        f'direnv_root="{tmp_path}"\n'
        f"agent_direnv_blocked=false\n"
        f'. "{body}"\n'
        f'run_agent touch "{agent_marker}"\n'
        f'echo "RUN_AGENT_RC=$?"\n'
    )
    bash = shutil.which("bash") or "/bin/bash"
    return subprocess.run(
        [bash, str(harness)],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PATH": str(bin_dir)},
        check=False,
    )


def test_given_no_direnv_when_run_agent_invoked_then_the_agent_command_still_runs(tmp_path):
    """direnv must be an enhancement, never a precondition for starting the agent.

    Without a guard, ``direnv exec`` is unresolvable, ``run_agent`` returns 127,
    the agent binary is never executed, and the session script's ``elapsed < 3``
    guard stops the session while blaming a direnv *approval* prompt.
    """
    bin_dir = _clean_bin(tmp_path)
    assert shutil.which("direnv", path=str(bin_dir)) is None, "hermetic PATH must not expose direnv"

    agent_marker = tmp_path / "agent-ran"
    result = _invoke_run_agent(tmp_path, bin_dir, agent_marker)

    assert "RUN_AGENT_RC=0" in result.stdout, (
        f"run_agent did not succeed without direnv: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert agent_marker.exists(), "the agent command was never executed when direnv was absent"
    assert "direnv allow" not in result.stderr, "a missing direnv binary must not be reported as an unapproved .envrc"


def test_given_direnv_on_path_when_run_agent_invoked_then_the_agent_runs_under_direnv(tmp_path):
    """Negative constraint: where direnv exists, the agent must still run under it."""
    bin_dir = _clean_bin(tmp_path)
    direnv_used = tmp_path / "direnv-was-used"
    fake_direnv = bin_dir / "direnv"
    fake_direnv.write_text(
        f'#!/bin/sh\nif [ "$1" = "exec" ]; then\n  echo used > "{direnv_used}"\n  shift 2\n  exec "$@"\nfi\nexit 0\n'
    )
    fake_direnv.chmod(0o755)

    agent_marker = tmp_path / "agent-ran"
    result = _invoke_run_agent(tmp_path, bin_dir, agent_marker)

    assert "RUN_AGENT_RC=0" in result.stdout, (
        f"run_agent failed with direnv present: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert direnv_used.exists(), "direnv was on PATH but the agent was not run under it"
    assert agent_marker.exists(), "the agent command was never executed"
