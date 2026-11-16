"""``-A/--split`` starts the session detached and attaches it in an iTerm2 split that keeps the focus."""

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from conftest import run_cli
from test_session_wake_watcher import launch  # noqa: F401

from ai_cli import iterm2, main

SESSION = "c-myproject-tst-1"
CALLER_TTY = "/dev/ttys004"


@pytest.fixture
def split_launch(launch, monkeypatch):  # noqa: F811
    """The tmux launch fixture on a macOS host whose new session has a live pane."""
    monkeypatch.setattr(main.sys, "platform", "darwin", raising=False)
    monkeypatch.setattr(iterm2, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(main, "_tmux_session_has_live_pane", lambda name: True)
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr(iterm2, "_current_pane_tty", lambda: CALLER_TTY)
    launch.osascript = []

    def osascript(result):
        def run(argv, **kwargs):
            launch.osascript.append((argv, kwargs))
            if isinstance(result, Exception):
                raise result
            return result

        return run

    def use_osascript(run):
        # Its own subprocess namespace: the launch fixture patches ``subprocess.run`` on the
        # shared module, which would otherwise answer osascript in place of this fake.
        monkeypatch.setattr(iterm2, "subprocess", SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired))

    launch.with_osascript = osascript
    launch.use_osascript = use_osascript
    return launch


def _in_iterm2(monkeypatch):
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")


def test_given_iterm2_when_launching_with_split_then_the_caller_pane_is_split_to_attach_and_reselected(
    split_launch, monkeypatch, capsys
):
    _in_iterm2(monkeypatch)
    ok = subprocess.CompletedProcess(["osascript"], 0, "ok\n", "")

    split_launch.use_osascript(split_launch.with_osascript(ok))
    split_launch.invoke(attach=False, split=True)

    assert split_launch.execs == []
    creation = next(argv for argv, _ in split_launch.calls if argv[:2] == ["tmux", "new-session"])
    assert "-d" in creation
    assert len(split_launch.osascript) == 1
    argv, kwargs = split_launch.osascript[0]
    script = argv[-1]
    assert argv[:2] == ["osascript", "-e"]
    assert f'if tty of s is "{CALLER_TTY}" then set caller to s' in script
    assert f'split vertically with same profile command "tmux attach-session -t ={SESSION}"' in script
    assert script.index("split vertically") < script.index("select caller")
    assert kwargs["timeout"] == iterm2._SPLIT_TIMEOUT_SECONDS
    assert f"{SESSION} attached in an iTerm2 split pane" in capsys.readouterr().err


def test_given_a_launch_inside_tmux_when_splitting_then_the_tmux_client_terminal_is_the_one_split(
    split_launch, monkeypatch
):
    _in_iterm2(monkeypatch)
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1,0")
    ok = subprocess.CompletedProcess(["osascript"], 0, "ok\n", "")
    real_run = main.subprocess.run

    def client_tty(argv, **kwargs):
        if argv[:2] == ["tmux", "display-message"] and argv[-1] == "#{client_tty}":
            return subprocess.CompletedProcess(argv, 0, "/dev/ttys009\n", "")
        return real_run(argv, **kwargs)

    split_launch.use_osascript(split_launch.with_osascript(ok))
    with patch("ai_cli.main.subprocess.run", side_effect=client_tty):
        main._split_attach(SESSION, main._launch_reporter.active())

    assert 'if tty of s is "/dev/ttys009"' in split_launch.osascript[0][0][-1]


def test_given_no_iterm2_when_launching_with_split_then_it_warns_and_leaves_the_session_detached(
    split_launch, monkeypatch, capsys
):
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.delenv("LC_TERMINAL", raising=False)

    split_launch.use_osascript(split_launch.with_osascript(AssertionError("osascript must not run")))
    split_launch.invoke(attach=False, split=True)

    assert split_launch.osascript == []

    assert split_launch.execs == []
    err = capsys.readouterr().err
    assert err.count("split needs iTerm2 on macOS") == 1
    assert f"the session was started detached: attach with `ai attach {SESSION}`" in err
    assert f"{SESSION} running detached" in err


def test_given_linux_when_launching_with_split_then_no_osascript_runs_even_with_iterm2_variables(
    split_launch, monkeypatch, capsys
):
    _in_iterm2(monkeypatch)
    monkeypatch.setattr(main.sys, "platform", "linux", raising=False)

    split_launch.use_osascript(split_launch.with_osascript(AssertionError("osascript must not run")))
    split_launch.invoke(attach=False, split=True)

    assert split_launch.osascript == []

    assert "split needs iTerm2 on macOS" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("result", "reason"),
    [
        (subprocess.CompletedProcess(["osascript"], 1, "", "execution error: not allowed (-1743)\n"), "exited 1"),
        (subprocess.TimeoutExpired("osascript", 15), "did not answer within 15s"),
        (subprocess.CompletedProcess(["osascript"], 0, "miss\n", ""), f"no iTerm2 pane has tty {CALLER_TTY}"),
    ],
    ids=["non-zero", "timeout", "no-pane"],
)
def test_given_a_failing_osascript_when_splitting_then_it_warns_and_the_session_keeps_running(
    split_launch, monkeypatch, capsys, result, reason
):
    _in_iterm2(monkeypatch)

    split_launch.use_osascript(split_launch.with_osascript(result))
    split_launch.invoke(attach=False, split=True)

    err = capsys.readouterr().err
    assert "iTerm2 split failed" in err
    assert reason in err
    assert f"the session keeps running detached: attach with `ai attach {SESSION}`" in err
    assert not any(argv[:2] == ["tmux", "kill-session"] for argv, _ in split_launch.calls)
    assert split_launch.execs == []


def test_given_a_config_that_turns_the_launch_bare_when_launching_with_split_then_it_warns_nothing_is_split(
    split_launch, monkeypatch, capsys
):
    _in_iterm2(monkeypatch)
    split_launch.use_osascript(split_launch.with_osascript(AssertionError("osascript must not run")))
    config = {"worktree": {"enabled": False}, "session": {"use_tmux": False}}

    with pytest.raises(SystemExit):
        split_launch.invoke(attach=False, split=True, config=config)

    assert split_launch.osascript == []
    assert not any(argv[:2] == ["tmux", "new-session"] for argv, _ in split_launch.calls)
    err = capsys.readouterr().err
    assert err.count("--split needs a tmux session, and this launch runs bare") == 1
    assert "use_tmux = false" in err


@pytest.mark.parametrize("engine", ["c", "g", "p", "cx", "agy"])
@pytest.mark.parametrize("flag", ["-A", "--split"])
def test_given_the_split_flag_when_any_engine_launches_then_it_launches_detached_with_split(engine, flag):
    seen: dict = {}

    def record(**kwargs):
        seen.update(kwargs)

    with (
        patch("ai_cli.main._do_session_launch", side_effect=record),
        patch("ai_cli.config.load_config", return_value={}),
    ):
        code, _, stderr = run_cli(["ai", engine, flag, "-d", "1"])

    assert code == 0, stderr
    assert seen["split"] is True
    assert seen["attach"] is False


@pytest.mark.parametrize(("conflict", "named"), [("-R", "-R/--remote"), ("-b", "-b/--bare")])
def test_given_split_with_a_remote_or_bare_launch_when_launching_then_it_refuses_before_launching(conflict, named):
    with patch("ai_cli.main._do_session_launch", side_effect=AssertionError("must not launch")):
        code, _, stderr = run_cli(["ai", "agy", "-A", conflict, "1"])

    assert code == 1
    assert f"cannot be combined with {named}" in stderr
