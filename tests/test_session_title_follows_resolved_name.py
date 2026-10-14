"""The iTerm2 pane names the session a launch resolved to, not the one it asked for (AI-CLI-6dft).

The reported symptom: a remote session migrated from ``myproject-1`` to
``myproject-host-1`` showed its new name in Claude Code's custom title and in its
statusline, while its iTerm2 pane still read ``c-r-myproject-1``.

Two causes, each pinned here through the real entry point:

* ``ai c 1 -R`` skipped the remote allocator for a numeric slot and titled the
  pane, keyed its transport state and its cleanup after a name it composed
  locally, ``<engine>-r-<repo alias>-<slot>``. That has not been the remote
  host's name for the slot since the host alias joined session names; only the
  remote host can say what its slot resolves to.
* Inside the session, the pane is re-titled through ``ai internal
  set-iterm2-name``, which drives iTerm2's AppleScript API and so can only work
  on the Mac running iTerm2. On the remote end of ``-R`` it renamed nothing, and
  the OSC 1 fallback fired only for a detached session, where no terminal
  receives it. So nothing the remote session knew about its own name ever
  reached the pane.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from conftest import tmux_runnable

from ai_cli import session_naming as _session_naming
from ai_cli.main import _REMOTE_SHELL_PROBE_CMD, cli
from ai_cli.session import build_session_name
from ai_cli.session_script import get_engine_script

if sys.platform != "win32":
    import pty

_TMUX_RUNNABLE, _TMUX_SKIP_REASON = tmux_runnable()
_OBSERVE_SECONDS = float(os.environ.get("AI_CLI_TEST_PROCESS_WAIT_SECONDS", "60"))

#: The remote host's alias, which its allocator joins into every session name.
_REMOTE_HOST_ALIAS = "host"


def _run_remote_launch(argv: list[str], live_remote_sessions: list[str]):
    """Drive ``ai c ... -R`` through ``cli()`` against a remote host whose alias is ``host``.

    The allocation reply is computed the way ``ai internal allocate-session-name``
    computes it on that host: the repo alias joined with the host's own alias, over
    its live tmux sessions. Returns the exit code, the iTerm2 setup mock, the
    launch-tracking mock, the transport handoff mock and stderr. The platform is pinned to
    Linux because remote SSH transport is refused on native Windows, and every boundary
    here is mocked.
    """
    config = {"remote": {"host": "192.0.2.10", "user": "user", "transport": "ssh"}}

    def remote_ssh(command, **_kwargs):
        if command[-1] == _REMOTE_SHELL_PROBE_CMD:
            return MagicMock(returncode=0, stdout="zsh\n", stderr="")
        if "ai update" in command[-1]:
            return MagicMock(returncode=0, stdout="current", stderr="")
        tokens = shlex.split(shlex.split(command[-1])[-1])
        engine, prefix, name = tokens[tokens.index("allocate-session-name") + 1 :][:3]
        with patch("ai_cli.session._tmux_session_names", return_value=live_remote_sessions):
            session_id, ai_name = build_session_name(
                engine,
                _session_naming.naming_prefix(prefix, _REMOTE_HOST_ALIAS),
                name,
                is_remote=True,
                legacy_prefix=prefix,
            )
        return MagicMock(returncode=0, stdout=json.dumps({"session_id": session_id, "ai_name": ai_name}), stderr="")

    handoff = MagicMock(return_value=0)
    stderr = io.StringIO()
    with (
        patch("sys.argv", argv),
        patch("ai_cli.main.sys.platform", "linux"),
        patch("ai_cli.main.shutil.which", return_value="/usr/bin/tmux"),
        patch("ai_cli.config.load_config", return_value=config),
        patch("ai_cli.session.get_project_prefix", return_value="myproject"),
        patch("ai_cli.config.get_project_aliases", return_value={}),
        patch("ai_cli.config.resolve_project_prefix_by_name", return_value="myproject"),
        patch("ai_cli.main.trigger_background_update"),
        patch("ai_cli.main._track_launch") as track,
        patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
        patch("ai_cli.iterm2._emit_iterm2_profile_setup") as iterm2_setup,
        patch("ai_cli.main.subprocess.run", side_effect=remote_ssh),
        patch("ai_cli.transport.run_ssh_with_reconnect", handoff),
        patch("sys.stderr", stderr),
        pytest.raises(SystemExit) as exc_info,
    ):
        cli()
    return exc_info.value.code, iterm2_setup, track, handoff, stderr.getvalue()


@pytest.mark.parametrize(
    "live_remote_sessions",
    [[], ["c-r-myproject-host-1"]],
    ids=["relaunch-after-migration", "reattach-to-the-running-migrated-session"],
)
def test_given_a_migrated_remote_session_when_launched_by_its_slot_number_then_the_pane_is_titled_after_the_resolved_session(
    live_remote_sessions,
):
    resolved = "c-r-myproject-host-1"

    code, iterm2_setup, track, handoff, stderr = _run_remote_launch(
        ["ai", "c", "1", "-R", "-p", "myproject"], live_remote_sessions
    )

    assert code in (0, None), stderr
    iterm2_setup.assert_called_once()
    title = iterm2_setup.call_args.args[2]
    assert title == resolved, f"the pane is titled {title!r}, not the session the remote host resolved"
    assert track.call_args.kwargs["session_name"] == resolved
    ssh_argv, cleanup_cmd = handoff.call_args.args[:2]
    assert shlex.split(shlex.split(ssh_argv[-1])[-1])[-1] == resolved, "the remote is told to open another session"
    assert cleanup_cmd == ["ai", "internal", "cleanup-session-files", resolved]


def test_given_a_remote_host_still_running_the_unmigrated_session_when_launched_by_its_slot_number_then_it_keeps_that_name():
    """The positive control: the migration window still reaches a session named before the host alias."""
    code, iterm2_setup, _track, handoff, stderr = _run_remote_launch(
        ["ai", "c", "1", "-R", "-p", "myproject"], ["c-r-myproject-1"]
    )

    assert code in (0, None), stderr
    assert iterm2_setup.call_args.args[2] == "c-r-myproject-1"
    assert shlex.split(shlex.split(handoff.call_args.args[0][-1])[-1])[-1] == "c-r-myproject-1"


def test_given_no_iterm2_api_on_this_host_when_set_iterm2_name_runs_then_it_exits_nonzero():
    """``set-iterm2-name`` reports a rename it could not make, so its caller can fall back."""
    with (
        patch("sys.argv", ["ai", "internal", "set-iterm2-name", "/dev/pts/3", "c-r-myproject-host-1"]),
        patch("ai_cli.iterm2.sys.platform", "linux"),
        pytest.raises(SystemExit) as exc_info,
    ):
        cli()

    assert exc_info.value.code == 1


# --- the session re-titles its own pane from the host it runs on ---


@pytest.fixture
def tmux_socket():
    if not _TMUX_RUNNABLE:
        pytest.skip(_TMUX_SKIP_REASON)
    sock_dir = tempfile.mkdtemp(prefix="ai-cli-title-")
    sock = f"{sock_dir}/tmux.sock"
    yield sock
    subprocess.run(["tmux", "-S", sock, "kill-server"], capture_output=True, check=False)
    shutil.rmtree(sock_dir, ignore_errors=True)


class _AttachedClient:
    """A tmux client on a real pty: what it reads is what iTerm2 would receive."""

    def __init__(self, argv: list[str], env: dict[str, str]):
        self._master, slave = pty.openpty()
        self.process = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, env=env, start_new_session=True)
        os.close(slave)
        self._chunks: list[bytes] = []
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    def _drain(self) -> None:
        while True:
            try:
                ready, _, _ = select.select([self._master], [], [], 0.2)
                if not ready:
                    if self.process.poll() is not None:
                        return
                    continue
                chunk = os.read(self._master, 65536)
            except OSError:
                return
            if not chunk:
                return
            with self._lock:
                self._chunks.append(chunk)

    def received(self) -> bytes:
        with self._lock:
            return b"".join(self._chunks)

    def close(self) -> None:
        with contextlib.suppress(OSError):
            os.killpg(self.process.pid, 9)
        self.process.wait(timeout=10)
        with contextlib.suppress(OSError):
            os.close(self._master)


@pytest.mark.real_tmux
@pytest.mark.skipif(os.name != "posix", reason="the session supervisor is a POSIX shell script")
def test_given_a_host_without_the_iterm2_api_when_the_attached_session_starts_then_its_name_reaches_the_terminal(
    tmux_socket, tmp_path
):
    """The remote end of ``-R``: an attached session's own name must reach the pane over the wire."""
    session = "c-r-myproject-host-1"
    started = tmp_path / "started"
    fake_bin = tmp_path / "fakes"
    fake_bin.mkdir()
    sessions = tmp_path / "state" / "ai-cli-utils" / "sessions"
    sessions.mkdir(parents=True)
    script = sessions / f"{session}.sh"
    # `ai internal set-iterm2-name` as it behaves on a host with no iTerm2 API: it renames
    # nothing and says so. Every other `ai` call the session makes is a no-op.
    (fake_bin / "ai").write_text(
        "#!/bin/sh\n"
        'if [ "$1" = internal ] && [ "$2" = set-iterm2-name ]; then exit 1; fi\n'
        'if [ "$1" = internal ] && [ "$2" = get-version ]; then printf "%s\\n" unknown; fi\n'
        "exit 0\n"
    )
    (fake_bin / "claude").write_text(
        f"#!{sys.executable}\nimport time\nopen({str(started)!r}, 'a').write('started\\n')\ntime.sleep(300)\n"
    )
    (fake_bin / "python3").symlink_to(sys.executable)
    for fake in fake_bin.iterdir():
        if not fake.is_symlink():
            fake.chmod(0o755)
    script.write_text(
        get_engine_script(
            "c", "myproject-host-1", session, "c-r-myproject-host-", "myproject", worktree_dir=str(tmp_path)
        )
    )
    script.chmod(0o700)
    tmux_conf = tmp_path / "tmux.conf"
    tmux_conf.write_text("set -g allow-passthrough on\n")
    zdotdir = tmp_path / "zdotdir"
    zdotdir.mkdir()
    inherited = {key: value for key, value in os.environ.items() if not key.startswith(("DIRENV_", "TMUX"))}
    env = {
        **inherited,
        "PATH": os.pathsep.join((str(fake_bin), os.environ.get("PATH", ""))),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "ZDOTDIR": str(zdotdir),
        "LC_TERMINAL": "iTerm2",
        "TERM": "xterm-256color",
    }
    client = _AttachedClient(
        ["tmux", "-S", tmux_socket, "-f", str(tmux_conf), "new-session", "-s", session, "bash", str(script)], env
    )
    expected = f"\033]1;* ▶ {session}\007".encode()
    try:
        deadline = time.monotonic() + _OBSERVE_SECONDS
        while time.monotonic() < deadline and expected not in client.received():
            time.sleep(0.05)
        history = subprocess.run(
            ["tmux", "-S", tmux_socket, "capture-pane", "-p", "-S", "-", "-t", session],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        assert expected in client.received(), (
            f"the attached terminal never received the session's name (agent started: {started.exists()}); "
            f"pane: {history[-3000:]!r}"
        )
    finally:
        client.close()
