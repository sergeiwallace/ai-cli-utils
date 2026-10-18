"""Per-session wake windows are detached from the agent and retry every exit."""

import json
import shlex
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ai_cli import main


@pytest.fixture
def launch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class LaunchSys:
        platform = "linux"

        def __getattr__(self, name):
            return getattr(sys, name)

    # Model a tmux-capable host even when the test runner is on Windows.
    monkeypatch.setattr(main, "sys", LaunchSys())
    # Privilege detection must match the modeled host, not the runner's OS APIs.
    monkeypatch.setattr(main, "_is_root", lambda: False)
    which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name: "tmux" if name == "tmux" else which(name))
    script = tmp_path / "watcher tools" / "wake_sidecar_cli.py"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")
    state = SimpleNamespace(
        mode="session",
        enabled=True,
        existing=False,
        failure=None,
        calls=[],
        execs=[],
        script=script,
        generation="",
        active_window="main",
        renames=[],
    )

    def fake_run(argv, **kwargs):
        state.calls.append((argv, kwargs))
        stdout = ""
        code = 0
        if argv[:2] == [sys.executable, str(script)]:
            assert argv[2:] == ["show", "--json"]
            if isinstance(state.failure, Exception):
                raise state.failure
            code = 1 if state.failure == "exit" else 0
            stdout = (
                "invalid" if state.failure == "json" else json.dumps({"enabled": state.enabled, "mode": state.mode})
            )
        elif argv[0] == "tmux":
            if argv[1] == "-V":
                stdout = "tmux 3.7c"
            elif argv[1] == "has-session":
                code = 0 if state.existing else 1
            elif argv[1] == "new-session":
                stdout = "$1\n"
            elif argv[1] == "set-option" and "@ai_cli_session_generation" in argv:
                state.generation = argv[-1]
            elif argv[1] == "display-message":
                if argv[-1] == "#{version}":
                    stdout = "3.7c"
                elif argv[-1] == "#{session_id}":
                    stdout = "$1"
                elif argv[-1] == "#{window_name}":
                    stdout = state.active_window
                elif "@ai_cli_session_generation" in argv[-1]:
                    stdout = f"$1\tc-myproject-tst-1\t{state.generation}"
        return subprocess.CompletedProcess(argv, code, stdout, "")

    def fake_execvp(file, argv):
        state.execs.append(argv)
        raise SystemExit(0)

    def invoke(**overrides):
        options = {
            "engine": "c",
            "name": "1",
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
        options.update(overrides)
        with (
            patch("ai_cli.main.subprocess.run", side_effect=fake_run),
            patch("ai_cli.main.os.execvp", side_effect=fake_execvp),
            patch("ai_cli.main._pty_handoff", return_value=main._exec_handoff),
            patch("ai_cli.main._session_shell_or_exit", return_value="sh"),
            patch("ai_cli.main._wake_sidecar_script", return_value=state.script),
            patch("ai_cli.config.validate_registry_completeness", return_value=True),
            patch("ai_cli.config.get_current_project_name", return_value="myproject"),
            patch("ai_cli.config.get_session_map", return_value={}),
            patch("ai_cli.session.cleanup_stale_sessions"),
            patch("ai_cli.trust.ensure_workspace_trusted"),
            patch("ai_cli.iterm2._load_iterm2_config", return_value={}),
            patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
            patch("ai_cli.iterm2._emit_iterm2_profile_setup"),
            patch("ai_cli.iterm2._configure_tmux_for_iterm2"),
            patch("ai_cli.iterm2._rename_tmux_window", side_effect=lambda *args: state.renames.append(args)),
        ):
            with pytest.raises(SystemExit) as exited:
                main._do_session_launch(**options)
        assert exited.value.code == 0
        return [argv for argv, _ in state.calls if argv[:2] == ["tmux", "new-window"]]

    state.invoke = invoke
    return state


@pytest.mark.parametrize("mode", ["session", "both"])
def test_given_session_mode_when_launching_then_one_detached_watcher_targets_slug(launch, mode):
    launch.mode = mode
    windows = launch.invoke()
    assert len(windows) == 1
    assert windows[0][:10] == ["tmux", "new-window", "-d", "-t", "$1:", "-n", "wake-watch", "sh", "-c", windows[0][-1]]
    assert "watch --session myproject-tst-1" in windows[0][-1]
    shows = [(argv, kw) for argv, kw in launch.calls if "show" in argv]
    assert len(shows) == 1
    assert shows[0][1]["timeout"] == 3
    assert shows[0][0][:2] == [sys.executable, str(launch.script)]


@pytest.mark.parametrize(("mode", "enabled"), [("machine", True), ("session", False), ("invalid", True)])
def test_given_inactive_session_mode_when_launching_then_no_watcher_window(launch, mode, enabled):
    launch.mode, launch.enabled = mode, enabled
    assert launch.invoke() == []


@pytest.mark.parametrize("failure", ["exit", "json", OSError(), subprocess.TimeoutExpired("show", 3)])
def test_given_failed_show_when_launching_then_no_watcher_window(launch, failure):
    launch.failure = failure
    assert launch.invoke() == []


@pytest.mark.parametrize("options", [{"is_remote": True}, {"bare": True}])
def test_given_excluded_launch_when_launching_then_no_watcher_query(launch, options):
    assert launch.invoke(**options) == []
    assert not any("show" in argv for argv, _ in launch.calls)


def test_given_existing_session_when_reattaching_then_no_watcher_query(launch):
    launch.existing = True
    assert launch.invoke() == []
    assert not any("show" in argv for argv, _ in launch.calls)
    assert launch.renames == [("c-myproject-tst-1", "myproject-tst-1")]


def test_given_selected_watcher_when_reattaching_then_keeps_window_name(launch):
    launch.existing = True
    launch.active_window = "wake-watch"
    assert launch.invoke() == []
    assert launch.renames == []
    assert not any("show" in argv for argv, _ in launch.calls)


def test_given_windows_when_launching_then_no_watcher_query(launch):
    class WindowsSys:
        platform = "win32"

        def __getattr__(self, name):
            return getattr(sys, name)

    with patch.object(main, "sys", WindowsSys()):
        assert launch.invoke() == []
    assert not any("show" in argv for argv, _ in launch.calls)


def test_given_once_launch_when_starting_then_watcher_precedes_attach(launch):
    windows = launch.invoke(once=True)
    assert len(windows) == 1
    assert windows[0][4] == "=c-myproject-tst-1:"
    creation = next(argv for argv, _ in launch.calls if argv[:2] == ["tmux", "new-session"])
    assert "-d" in creation
    assert launch.calls.index(next(call for call in launch.calls if call[0] == creation)) < launch.calls.index(
        next(call for call in launch.calls if call[0] == windows[0])
    )
    assert launch.execs == [["tmux", "attach-session", "-t", "=c-myproject-tst-1"]]
    assert sum("show" in argv for argv, _ in launch.calls) == 1


@pytest.mark.parametrize("options", [{"once": True}, {"bare": True}])
def test_given_host_without_getuid_when_launching_then_fixture_models_unprivileged_host(launch, monkeypatch, options):
    monkeypatch.delattr(main.os, "getuid", raising=False)
    windows = launch.invoke(**options)
    assert len(windows) == (0 if options.get("bare") else 1)


@pytest.mark.parametrize("mode", ["machine", "session"])
def test_given_no_session_watcher_when_once_launching_then_preserves_attached_creation(launch, mode):
    launch.mode = mode
    launch.enabled = mode == "machine"
    assert launch.invoke(once=True) == []
    assert launch.execs[0][:2] == ["tmux", "new-session"]
    assert "-d" not in launch.execs[0]
    assert not any(argv[:2] == ["tmux", "new-session"] for argv, _ in launch.calls)


@pytest.mark.parametrize(("mode", "enabled"), [("session", True), ("both", True), ("session", False)])
def test_given_local_mode_without_implementation_when_launching_then_skips_watcher(launch, mode, enabled):
    launch.script = None
    assert launch.invoke(config={"sidecar": {"mode": mode, "enabled": enabled}}) == []
    assert not any("show" in argv for argv, _ in launch.calls)


@pytest.mark.skipif(shutil.which("sh") is None, reason="requires a POSIX shell")
@pytest.mark.parametrize("exit_code", [0, 7])
def test_given_watcher_exit_when_window_loop_runs_then_restarts_within_two_seconds(launch, tmp_path, exit_code):
    """Execute the exact window loop; fake sleep records delays and bounds retries."""
    starts = tmp_path / "starts"
    launch.script.write_text(
        "import pathlib, sys\n"
        f"with pathlib.Path({str(starts)!r}).open('a') as log:\n"
        "    log.write(repr(sys.argv[1:]) + '\\n')\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    loop = launch.invoke()[0][-1]
    pauses = tmp_path / "pauses"
    # This substitutes only the clock delay, keeping the generated loop intact.
    clock = (
        "count=0; sleep() { "
        f"printf '%s\\n' \"$1\" >> {shlex.quote(str(pauses))}; "
        'count=$((count + 1)); if [ "$count" -ge 3 ]; then exit 0; fi; }; '
    )
    completed = subprocess.run(["sh", "-c", clock + loop], capture_output=True, text=True, timeout=5, check=False)
    assert completed.returncode == 0, completed.stderr
    assert starts.read_text().splitlines() == ["['watch', '--session', 'myproject-tst-1']"] * 3
    assert [float(value) for value in pauses.read_text().splitlines()] == [2, 2, 2]
