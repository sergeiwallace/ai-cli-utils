"""Real tmux integration tests for `_do_session_launch`.

These tests run against a real ``tmux`` server on an isolated socket so we
exercise actual tmux ``new-session`` / ``has-session`` / ``kill-session``
behavior — not a mock. Everything downstream of tmux (the engine binary,
worktree creation, registry checks, etc.) is still mocked.
"""

import shutil
import subprocess
import tempfile
from unittest.mock import patch

import libtmux
import pytest

from ai_cli.main import _do_session_launch


pytestmark = pytest.mark.skipif(
    shutil.which("tmux") is None,
    reason="tmux binary not available on PATH",
)


@pytest.fixture
def tmux_server():
    """Isolated tmux server for integration tests.

    Each test gets its own socket path so sessions created in one test do not
    leak into another.
    """
    sock = tempfile.mktemp(prefix="ai-cli-test-", suffix=".sock")
    server = libtmux.Server(socket_path=sock)
    yield server
    try:
        for s in list(server.sessions):
            try:
                s.kill_session()
            except Exception:
                pass
    except Exception:
        pass
    # Final cleanup: kill the server process if still alive
    try:
        subprocess.run(["tmux", "-S", sock, "kill-server"], capture_output=True)
    except Exception:
        pass


@pytest.fixture
def patched_subprocess(tmux_server):
    """Patch ``subprocess.run`` and ``os.execvp`` in main.py so that all tmux
    calls are rerouted to our isolated server, and attach-session calls become
    a no-op instead of exec'ing.

    Yields the tmux server so tests can inspect created sessions.
    """
    sock = tmux_server.socket_path
    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and len(cmd) > 0 and cmd[0] == "tmux":
            # Reroute every tmux call to our isolated socket.
            new_cmd = ["tmux", "-S", sock] + list(cmd[1:])
            return real_run(new_cmd, *args, **kwargs)
        if isinstance(cmd, (list, tuple)) and len(cmd) > 0 and cmd[0] == "git":
            # No-op git (pull --rebase etc.) — return success without touching the FS
            class _R:
                returncode = 0
                stdout = ""
                stderr = ""

            return _R()
        return real_run(cmd, *args, **kwargs)

    def fake_execvp(file, args):
        # tmux attach-session is the final exec in _do_session_launch — swallow it
        # so the test process survives. Raise SystemExit to mimic process replacement.
        raise SystemExit(0)

    with (
        patch("ai_cli.main.subprocess.run", side_effect=fake_run),
        patch("ai_cli.main.os.execvp", side_effect=fake_execvp),
    ):
        yield tmux_server


def _base_launch_kwargs(name: str = "1") -> dict:
    return dict(
        engine="c",
        name=name,
        resume=False,
        once=False,
        bare=False,
        notify=False,
        sandbox=False,
        no_worktree=True,  # skip worktree creation to keep the test hermetic
        remote=False,
        project="",
        is_remote=False,
        project_prefix_override="myproject",
        extra_args=[],
        config={"worktree": {"enabled": False}},
    )


def test_given_new_session_when_launched_then_tmux_session_created(patched_subprocess):
    """A call to ``_do_session_launch`` must create a tmux session on the server."""
    server = patched_subprocess
    assert list(server.sessions) == []

    with (
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.session.cleanup_stale_sessions"),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.iterm2._load_iterm2_config", return_value={}),
        patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
        patch("ai_cli.iterm2._emit_iterm2_profile_setup"),
        patch("ai_cli.iterm2._configure_tmux_for_iterm2"),
        patch("ai_cli.session_script.get_engine_script", return_value="sleep 5\n"),
    ):
        with pytest.raises(SystemExit):
            _do_session_launch(**_base_launch_kwargs(name="1"))

    session_names = [s.name for s in server.sessions]
    assert "c-myproject-1" in session_names, f"expected c-myproject-1 in {session_names}"


def test_given_existing_session_when_relaunched_then_attaches_not_creates(patched_subprocess):
    """If a tmux session already exists, ``_do_session_launch`` must attach to it,
    not create a new one."""
    server = patched_subprocess
    # Pre-create the target session directly via libtmux
    server.new_session(session_name="c-myproject-2", detach=True, window_command="sleep 30")
    before_ids = {s.id for s in server.sessions}
    assert any(s.name == "c-myproject-2" for s in server.sessions)

    with (
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.session.cleanup_stale_sessions"),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.iterm2._load_iterm2_config", return_value={}),
        patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
        patch("ai_cli.iterm2._emit_iterm2_profile_setup"),
        patch("ai_cli.iterm2._configure_tmux_for_iterm2"),
        patch("ai_cli.session_script.get_engine_script", return_value="sleep 5\n"),
    ):
        with pytest.raises(SystemExit):
            _do_session_launch(**_base_launch_kwargs(name="2"))

    after = {s.name: s.id for s in server.sessions}
    # Must still have exactly one c-myproject-2, and its id must be unchanged
    assert "c-myproject-2" in after
    # The existing session id must survive — i.e. no new-session was issued
    assert after["c-myproject-2"] in before_ids


def test_given_extra_args_positional_name_when_launched_then_session_uses_positional_name(
    patched_subprocess,
):
    """When ``name`` is empty but ``extra_args=['myname']``, the session name
    must incorporate ``myname``: ``c-myproject-myname-1``."""
    server = patched_subprocess

    kwargs = _base_launch_kwargs(name="")
    kwargs["extra_args"] = ["myname"]

    with (
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.session.cleanup_stale_sessions"),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.iterm2._load_iterm2_config", return_value={}),
        patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
        patch("ai_cli.iterm2._emit_iterm2_profile_setup"),
        patch("ai_cli.iterm2._configure_tmux_for_iterm2"),
        patch("ai_cli.session_script.get_engine_script", return_value="sleep 5\n"),
    ):
        with pytest.raises(SystemExit):
            _do_session_launch(**kwargs)

    session_names = [s.name for s in server.sessions]
    assert any(n.startswith("c-myproject-myname-") for n in session_names), (
        f"expected a c-myproject-myname-* session in {session_names}"
    )
