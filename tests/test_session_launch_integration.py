"""Real tmux integration tests for `_do_session_launch`.

These tests run against a real ``tmux`` server on an isolated socket so we
exercise actual tmux ``new-session`` / ``has-session`` / ``kill-session``
behavior — not a mock. Everything downstream of tmux (the engine binary,
worktree creation, registry checks, etc.) is still mocked, except where a
test explicitly exercises production worktree creation or registry
resolution.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from unittest.mock import MagicMock, patch

import libtmux
import pytest

from ai_cli.main import _REMOTE_SHELL_PROBE_CMD, _do_session_launch


def _tmux_runnable() -> tuple[bool, str]:
    """Can ``tmux`` actually be executed here? Returns ``(runnable, reason)``.

    Presence on ``PATH`` is not the same question. A ``tmux`` that resolves but
    cannot start — an extracted bundle whose shared libraries are not on the
    loader path, a binary built against a different libc — makes every test in
    this file fail on an empty session list, which reads as a defect in the launch
    path rather than as a broken tool. ``shutil.which`` cannot see that: it stats
    the file and checks the executable bit, and the failure happens later, in the
    dynamic loader.

    So the probe is to run the thing: ``tmux -V`` exits 0 only when the binary
    really starts. Deliberately not a library-path check, which would be
    guessing at one cause of many.
    """
    if shutil.which("tmux") is None:
        return False, "tmux binary not available on PATH"
    try:
        probe = subprocess.run(["tmux", "-V"], capture_output=True, text=True, timeout=30, check=False)
    except OSError as exc:
        return False, f"tmux could not be executed: {exc}"
    except subprocess.TimeoutExpired:
        return False, "tmux -V timed out"
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout or "").strip().splitlines()
        return False, f"tmux is on PATH but does not run: {detail[0] if detail else f'exit {probe.returncode}'}"
    return True, ""


_TMUX_RUNNABLE, _TMUX_SKIP_REASON = _tmux_runnable()

pytestmark = [
    pytest.mark.real_tmux,
    # This drives a REAL tmux server (libtmux.Server) against MSYS2's tmux, same
    # mechanism as test_session_launch_shell_resolution.py -- that file's skip
    # (PR #35) did not cover this one, and it hung the Windows CI job for the
    # full 15-minute job timeout on 2026-08-16 (run 31930040855, stalled at 94%
    # with no progress for ~10 minutes before being killed). Skip until MSYS2's
    # tmux socket/session behavior under GitHub's Windows runner is verified
    # deliberately.
    pytest.mark.skipif(
        sys.platform == "win32", reason="real tmux server behavior unverified under MSYS2 CI (PR #35 hang)"
    ),
]


@pytest.fixture
def tmux_server():
    """Isolated tmux server for integration tests.

    Each test gets its own socket path so sessions created in one test do not
    leak into another.

    The skip lives here rather than on the module, so it covers exactly the tests
    that drive a live server. The last test in this file mocks every tmux call and
    needs no server at all; skipping it alongside these would drop real coverage
    for an unrelated reason.
    """
    if not _TMUX_RUNNABLE:
        pytest.skip(_TMUX_SKIP_REASON)
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
        subprocess.run(["tmux", "-S", sock, "kill-server"], capture_output=True, check=False)
    except Exception:
        pass
    shutil.rmtree(sock_dir, ignore_errors=True)


@pytest.fixture
def patched_subprocess(tmux_server, tmp_path):
    """Intercept tmux calls made by ``_do_session_launch`` and route them
    through the isolated libtmux server.

    Rather than re-invoking the tmux binary (which requires a bootstrapped
    server process and a valid TERM in CI), we service the two calls that
    matter — ``has-session`` and ``new-session`` — directly via libtmux, which
    handles server lifecycle internally.  All other tmux sub-commands and git
    calls are silently swallowed.  ``os.execvp`` is replaced so the final
    ``tmux attach-session`` exec doesn't replace the test process.

    Also patches ``get_xdg_state_home`` to redirect stable script writes to
    ``tmp_path`` instead of the real XDG state directory, and patches
    ``ai_cli.iterm2.subprocess.run`` so GUID eviction calls don't hit the
    real tmux binary.

    Yields the tmux server so tests can inspect created sessions.
    """
    server = tmux_server
    real_subprocess_run = subprocess.run

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

            if sub == "list-panes":
                # Query the real server so dead-pane detection reflects true state.
                try:
                    target = cmd[cmd.index("-t") + 1]
                except (ValueError, IndexError):
                    return _FAIL()
                match = next((s for s in server.sessions if s.name == target), None)
                if match is None:
                    return _FAIL()
                # Use the captured real subprocess.run, not the module-level name --
                # that name is itself patched to this same fake_run for the whole
                # test, so calling `subprocess.run(...)` here would recurse forever.
                return real_subprocess_run(
                    ["tmux", "-S", str(server.socket_path), "list-panes", "-t", target, "-F", "#{pane_dead}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )

            if sub == "kill-session":
                # Actually remove the matching session so a subsequent has-session
                # check (dead-pane recreate path) sees it gone, same as real tmux.
                try:
                    target = cmd[cmd.index("-t") + 1]
                except (ValueError, IndexError):
                    return _OK()
                match = next((s for s in server.sessions if s.name == target), None)
                if match is not None:
                    try:
                        match.kill()
                    except Exception:
                        pass
                return _OK()

            # set-option, set-window-option, etc. — silently succeed.
            return _OK()

        if head == "git":
            return _OK()

        return subprocess.run(cmd, *args, **kwargs, check=False)

    def fake_execvp(file, args):
        # tmux attach-session is the final exec — raise SystemExit so the
        # test process survives while the call is still recorded.
        raise SystemExit(0)

    with (
        patch("ai_cli.main.subprocess.run", side_effect=fake_run),
        patch("ai_cli.iterm2.subprocess.run", side_effect=fake_run),
        patch("ai_cli.main.os.execvp", side_effect=fake_execvp),
        patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
    ):
        yield server


def _base_launch_kwargs(name: str = "1") -> dict:
    return {
        "engine": "c",
        "name": name,
        "resume": False,
        "once": False,
        "bare": False,
        "notify": False,
        "sandbox": False,
        "no_worktree": True,  # skip worktree creation to keep the test hermetic
        "remote": False,
        "project": "",
        "is_remote": False,
        "project_prefix_override": "myproject",
        "extra_args": [],
        "config": {"worktree": {"enabled": False}},
    }


@pytest.fixture
def fleet_registered_repo(tmp_path, monkeypatch):
    """Create a clone and resolve its raw uppercase prefix through the fleet registry."""

    def git(*args, cwd):
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True, text=True)

    remote = tmp_path / "origin.git"
    git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "-q", "-b", "main", cwd=seed)
    git("config", "user.email", "test@example.com", cwd=seed)
    git("config", "user.name", "user", cwd=seed)
    (seed / "README.md").write_text("base\n")
    git("add", "README.md", cwd=seed)
    git("commit", "-q", "-m", "initial", cwd=seed)
    git("push", "-q", str(remote), "main", cwd=seed)

    repo = tmp_path / "myproject"
    git("clone", "-q", str(remote), str(repo), cwd=tmp_path)
    registry = tmp_path / "registry" / "config" / "fleet-projects.toml"
    registry.parent.mkdir(parents=True)
    registry.write_text('[[projects]]\nname = "myproject"\ntask_prefix = "APP"\ntype = "tool"\nactive = true\n')
    monkeypatch.setattr("ai_cli.config._get_projects_dir", lambda: tmp_path)
    monkeypatch.setattr("ai_cli.config._get_project_registry_path", lambda: None)
    monkeypatch.setattr("ai_cli.config.load_config", dict)
    monkeypatch.setattr("ai_cli.session._get_projects_dir", lambda: tmp_path)
    return repo


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


def _create_dead_session(server: "libtmux.Server", session_name: str) -> None:
    """Pre-create ``session_name`` with a pane that is already dead.

    A supervisor crash (AI-CLI-t8h5) leaves the tmux session alive with a dead
    pane; tmux only keeps it around because ``remain-on-exit`` is set (as the
    real launch path does at creation). Mirrors
    ``test_stale_session_reaper.py``'s ``_create_dead_managed_session`` -- but
    goes through ``server.cmd()`` (libtmux's own Popen-based transport)
    instead of ``subprocess.run``, because ``patched_subprocess`` replaces the
    real ``subprocess.run`` for the whole test (``ai_cli.main`` imports the
    ``subprocess`` module directly, so patching ``ai_cli.main.subprocess.run``
    replaces the same shared attribute a bare ``subprocess.run`` call would
    hit) -- a raw ``subprocess.run(["tmux", ...])`` call here would silently
    route through the fixture's fake instead of a real tmux invocation.
    """
    created = server.cmd("new-session", "-d", "-s", session_name, "sh", "-c", "read ignored; exit 0")
    assert created.returncode == 0, created.stderr
    assert server.cmd("set-window-option", "-t", session_name, "remain-on-exit", "on").returncode == 0
    assert server.cmd("send-keys", "-t", session_name, "done", "Enter").returncode == 0
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = server.cmd("list-panes", "-t", session_name, "-F", "#{pane_dead}")
        if result.returncode == 0 and result.stdout == ["1"]:
            return
        time.sleep(0.05)
    pytest.fail("tmux pane did not become dead")


def test_given_existing_session_with_dead_pane_when_relaunched_then_recreates_not_attaches(
    patched_subprocess,
):
    """A session left behind by a supervisor crash (AI-CLI-t8h5 sw-4 regression)
    has a dead pane but tmux keeps the session alive. A naive reattach shows
    the frozen final output forever; ``_do_session_launch`` must instead kill
    the dead session and create a genuinely fresh one."""
    server = patched_subprocess
    _create_dead_session(server, "c-myproject-3")

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
            _do_session_launch(**_base_launch_kwargs(name="3"))

    after = {s.name: s.id for s in server.sessions}
    assert "c-myproject-3" in after
    pane_dead = server.cmd("list-panes", "-t", "c-myproject-3", "-F", "#{pane_dead}")
    assert pane_dead.stdout == ["0"]


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


def test_given_uppercase_registered_prefix_when_new_session_launched_then_outputs_are_lowercase(
    patched_subprocess, tmp_path
):
    """A new session uses lowercase names while retaining the registry's raw prefix."""
    server = patched_subprocess
    worktree = tmp_path / ".worktrees" / "myproject-1"

    with (
        patch("ai_cli.session.cleanup_stale_sessions"),
        patch("ai_cli.session.resolve_project_prefix", return_value="MYPROJECT"),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.iterm2._load_iterm2_config", return_value={}),
        patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
        patch("ai_cli.iterm2._emit_iterm2_profile_setup") as emit_profile,
        patch("ai_cli.iterm2._configure_tmux_for_iterm2"),
        patch("ai_cli.session_script.get_engine_script", return_value="sleep 5\n"),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.session.detect_repo_root", return_value=tmp_path),
        patch("ai_cli.session.create_worktree", return_value=(worktree, True)) as create_worktree,
        patch("ai_cli.trust.ensure_workspace_trusted"),
        patch("ai_cli.main.repair_bare_worktree_config"),
        patch("ai_cli.main._has_conflict_or_unknown", return_value=False),
        patch("ai_cli.main.pull_rebase_autostash", return_value=(MagicMock(returncode=0), None)),
        patch("ai_cli.main.detect_missing_tracked_symlinks", return_value=[]),
        patch("ai_cli.main.detect_phantom_deleted_files", return_value=[]),
    ):
        kwargs = _base_launch_kwargs(name="1")
        kwargs["project_prefix_override"] = ""
        kwargs["no_worktree"] = False
        kwargs["config"] = {"worktree": {"enabled": True}}
        with pytest.raises(SystemExit):
            _do_session_launch(**kwargs)

    create_worktree.assert_called_once_with("myproject-1", with_status=True)
    assert emit_profile.call_args.args[:3] == ("myproject-1", "c", "c-myproject-1")
    session_names = [s.name for s in server.sessions]
    assert "c-myproject-1" in session_names


def test_given_uppercase_fleet_prefix_when_new_session_launches_then_real_artifacts_are_lowercase(
    tmux_server, fleet_registered_repo, monkeypatch, tmp_path
):
    """Exercise production registry resolution and worktree creation for an empty slot."""
    repo = fleet_registered_repo
    monkeypatch.chdir(repo)
    real_run = subprocess.run

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, *args, **kwargs):
        if cmd[0] == "tmux":
            subcommand = cmd[1]
            if subcommand == "list-sessions":
                names = [session.name for session in tmux_server.sessions]
                formatter = cmd[cmd.index("-F") + 1]
                output = "\n".join(f"{name} 0" if "session_activity" in formatter else name for name in names)
                return type("TmuxResult", (), {"returncode": 0, "stdout": output, "stderr": ""})()
            if subcommand == "has-session":
                target = cmd[cmd.index("-t") + 1]
                exists = any(session.name == target for session in tmux_server.sessions)
                return type("TmuxResult", (), {"returncode": 0 if exists else 1, "stdout": "", "stderr": ""})()
            if subcommand == "new-session":
                name = cmd[cmd.index("-s") + 1]
                tmux_server.new_session(session_name=name, detach=True, window_command="sleep 30")
            return _Result()
        return real_run(cmd, *args, **kwargs)

    with (
        patch("ai_cli.main.subprocess.run", side_effect=fake_run),
        patch("ai_cli.main.os.execvp", side_effect=SystemExit(0)),
        patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path / "state"),
        patch("ai_cli.session.cleanup_stale_sessions"),
        patch("ai_cli.trust.ensure_workspace_trusted"),
        patch("ai_cli.main.repair_bare_worktree_config"),
        patch("ai_cli.session.repair_bare_worktree_config"),
        patch("ai_cli.main._has_conflict_or_unknown", return_value=False),
        patch("ai_cli.main.pull_rebase_autostash", return_value=(MagicMock(returncode=0), None)),
        patch("ai_cli.main.detect_missing_tracked_symlinks", return_value=[]),
        patch("ai_cli.main.detect_phantom_deleted_files", return_value=[]),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.iterm2._load_iterm2_config", return_value={}),
        patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
        patch("ai_cli.iterm2._emit_iterm2_profile_setup") as emit_profile,
        patch("ai_cli.iterm2._configure_tmux_for_iterm2"),
        patch("ai_cli.session_script.get_engine_script", return_value="sleep 5\n"),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
    ):
        kwargs = _base_launch_kwargs(name="1")
        kwargs.update(
            project_prefix_override="",
            no_worktree=False,
            config={"worktree": {"enabled": True}},
        )
        with pytest.raises(SystemExit):
            _do_session_launch(**kwargs)

    assert (repo / ".worktrees" / "app-1").is_dir()
    assert [session.name for session in tmux_server.sessions] == ["c-app-1"]
    assert emit_profile.call_args.args[:3] == ("app-1", "c", "c-app-1")


def test_given_shell_metacharacter_prefix_when_remote_session_launches_then_rejected_before_any_side_effect():
    """A `--project-prefix` containing shell metacharacters must be rejected by
    `validate_task_prefix()` (AI-CLI-fae.13 F-21) before the launch reaches remote
    preflight, the iTerm2 profile, or exec — reject-outright is the intended
    behavior, not sanitize-and-proceed (superseded AI-CLI-209 expectation)."""
    cfg = {"remote": {"host": "example.com", "transport": "ssh"}}
    preflight_calls = []

    def fake_preflight(cmd, *_args, **_kwargs):
        if cmd[-1] == _REMOTE_SHELL_PROBE_CMD:
            return MagicMock(returncode=0, stdout="zsh\n", stderr="")
        preflight_calls.append(cmd)
        return MagicMock(returncode=0, stdout="{}", stderr="")

    with (
        patch("ai_cli.main.shutil.which", return_value="/usr/bin/tmux"),
        patch("ai_cli.main._session._resolve_is_remote", return_value=False),
        patch("ai_cli.main._config.validate_registry_completeness", return_value=True),
        patch("ai_cli.main._config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.main._iterm2._assign_iterm2_color_slot", return_value=None),
        patch("ai_cli.main._iterm2._emit_iterm2_profile_setup") as emit_profile,
        patch("ai_cli.main.subprocess.run", side_effect=fake_preflight),
        patch("ai_cli.main.os.execvp", side_effect=SystemExit(0)) as execute,
    ):
        with pytest.raises(SystemExit) as exc_info:
            _do_session_launch(
                engine="c",
                name="Planning",
                resume=False,
                once=False,
                bare=False,
                notify=False,
                sandbox=True,
                no_worktree=False,
                remote=True,
                project="",
                is_remote=False,
                project_prefix_override="APP; printf INJECTED",
                extra_args=[],
                config=cfg,
            )

    assert exc_info.value.code == 1
    assert preflight_calls == []
    emit_profile.assert_not_called()
    execute.assert_not_called()


def test_given_existing_session_when_relaunched_then_no_iterm_session_id_propagated(
    monkeypatch,
):
    """Re-attaching must NOT write ITERM_SESSION_ID into the tmux environment.

    The pane is renamed by its live client tty (resolved at set-name time), so
    there is no stored GUID to reconcile on re-attach.  Propagating a GUID was
    the racy dual-tracking that caused cross-session pane-title clobbering
    (AI-CLI-59) — this test guards that the propagation stays removed.
    """

    # Track tmux set-environment calls emitted during _do_session_launch.
    set_env_calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        is_text = kwargs.get("text", False)
        empty: str | bytes = "" if is_text else b""

        class _OK:
            returncode = 0
            stdout = empty
            stderr = empty

        if not isinstance(cmd, (list, tuple)) or not cmd:
            return _OK()
        head, sub = cmd[0], (cmd[1] if len(cmd) > 1 else "")
        if head == "tmux":
            if sub == "has-session":
                return _OK()  # session already exists → re-attach path
            if sub == "set-environment":
                set_env_calls.append(list(cmd))
            return _OK()
        if head == "git":
            return _OK()
        return _OK()

    new_guid = "w0t0p3:BBBB-NEW-GUID"
    with (
        patch("ai_cli.main.subprocess.run", side_effect=fake_run),
        patch("ai_cli.main.os.execvp", side_effect=SystemExit(0)),
        patch.dict(
            os.environ,
            {"ITERM_SESSION_ID": new_guid, "LC_TERMINAL": "iTerm2"},
            clear=False,
        ),
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

    iterm_env_updates = [c for c in set_env_calls if "ITERM_SESSION_ID" in c]
    assert iterm_env_updates == [], (
        f"re-attach must not propagate ITERM_SESSION_ID (tty-based rename), got {iterm_env_updates!r}"
    )
