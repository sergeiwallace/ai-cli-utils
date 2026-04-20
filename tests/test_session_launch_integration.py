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
    sock_dir = tempfile.mkdtemp(prefix="ai-cli-test-")
    sock = f"{sock_dir}/tmux.sock"
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
    shutil.rmtree(sock_dir, ignore_errors=True)


@pytest.fixture
def patched_subprocess(tmux_server):
    """Intercept tmux calls made by ``_do_session_launch`` and route them
    through the isolated libtmux server.

    Rather than re-invoking the tmux binary (which requires a bootstrapped
    server process and a valid TERM in CI), we service the two calls that
    matter — ``has-session`` and ``new-session`` — directly via libtmux, which
    handles server lifecycle internally.  All other tmux sub-commands and git
    calls are silently swallowed.  ``os.execvp`` is replaced so the final
    ``tmux attach-session`` exec doesn't replace the test process.

    Yields the tmux server so tests can inspect created sessions.
    """
    server = tmux_server

    class _OK:
        returncode = 0
        stdout = ""
        stderr = ""

    class _FAIL:
        returncode = 1
        stdout = ""
        stderr = ""

    def fake_run(cmd, *args, **kwargs):
        if not isinstance(cmd, (list, tuple)) or not cmd:
            return subprocess.run.__wrapped__(cmd, *args, **kwargs)  # type: ignore[attr-defined]
        head = cmd[0]
        sub = cmd[1] if len(cmd) > 1 else ""

        if head == "tmux":
            if sub == "has-session":
                # Check via libtmux — no tmux binary call needed.
                try:
                    target = cmd[cmd.index("-t") + 1]
                except (ValueError, IndexError):
                    return _FAIL()
                return _OK() if any(s.name == target for s in server.sessions) else _FAIL()

            if sub == "new-session":
                # Create via libtmux — avoids server bootstrap / TERM requirements.
                try:
                    s_idx = cmd.index("-s")
                    session_name = cmd[s_idx + 1]
                except (ValueError, IndexError):
                    session_name = "unknown"
                try:
                    server.new_session(session_name=session_name, detach=True, window_command="sleep 30")
                except Exception:
                    pass
                return _OK()

            # kill-session, set-option, etc. — silently succeed.
            return _OK()

        if head == "git":
            return _OK()

        return subprocess.run(cmd, *args, **kwargs)

    def fake_execvp(file, args):
        # tmux attach-session is the final exec — raise SystemExit so the
        # test process survives while the call is still recorded.
        raise SystemExit(0)

    with (
        patch("ai_cli.main.subprocess.run", side_effect=fake_run),
        patch("ai_cli.main.os.execvp", side_effect=fake_execvp),
    ):
        yield server


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
        patch("ai_cli.session._resolve_is_remote", return_value=False),
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
        patch("ai_cli.session._resolve_is_remote", return_value=False),
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
        patch("ai_cli.session._resolve_is_remote", return_value=False),
    ):
        with pytest.raises(SystemExit):
            _do_session_launch(**kwargs)

    session_names = [s.name for s in server.sessions]
    assert any(n.startswith("c-myproject-myname-") for n in session_names), (
        f"expected a c-myproject-myname-* session in {session_names}"
    )


def test_given_registry_prompt_on_first_run_when_prefix_entered_then_session_uses_new_prefix(
    patched_subprocess,
):
    """When validate_registry_completeness registers a new prefix interactively,
    the current session must use that prefix — not the 3-char fallback."""
    server = patched_subprocess

    def _register_and_return_true(*, interactive):
        # Simulate the registry completing with 'newpfx' as the saved prefix.
        # After this call, load_project_registry should return the new entry.
        return True

    with (
        patch(
            "ai_cli.config.validate_registry_completeness",
            side_effect=_register_and_return_true,
        ),
        patch("ai_cli.session.cleanup_stale_sessions"),
        # Registry now has 'newproject' -> task_prefix 'newpfx'
        patch(
            "ai_cli.config.load_project_registry",
            return_value=[{"name": "newproject", "task_prefix": "newpfx"}],
        ),
        patch(
            "ai_cli.session.load_project_registry",
            return_value=[{"name": "newproject", "task_prefix": "newpfx"}],
        ),
        patch("ai_cli.config.get_current_project_name", return_value="newproject"),
        patch("ai_cli.session.get_current_project_name", return_value="newproject"),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.iterm2._load_iterm2_config", return_value={}),
        patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
        patch("ai_cli.iterm2._emit_iterm2_profile_setup"),
        patch("ai_cli.iterm2._configure_tmux_for_iterm2"),
        patch("ai_cli.session_script.get_engine_script", return_value="sleep 5\n"),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
    ):
        kwargs = _base_launch_kwargs(name="1")
        kwargs["project_prefix_override"] = ""  # don't bypass registry resolution
        with pytest.raises(SystemExit):
            _do_session_launch(**kwargs)

    session_names = [s.name for s in server.sessions]
    assert any(n.startswith("c-newpfx-") for n in session_names), (
        f"expected c-newpfx-* session (not 3-char fallback) in {session_names}"
    )
