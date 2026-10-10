"""The iTerm2 session registry: launch records, liveness, refresh (AIH-krf96 T-1.2..T-1.4)."""

import json
import logging
import os
import shlex
import stat
import subprocess
import sys
import textwrap
from unittest.mock import MagicMock, patch

import pytest

from ai_cli import session_registry
from ai_cli.iterm2 import _PERSISTENCE_DEFAULTS
from ai_cli.main import _REMOTE_SHELL_PROBE_CMD, _do_session_launch, cli

_ITERM_SESSION_ID = "w0t3p1:ABCDEF01-2345-6789-ABCD-EF0123456789"
_PANE_TTY = "/dev/ttys042"

# A remote machine whose host, user and key path must never reach the registry.
_REMOTE_CONFIG = {
    "remote": {
        "default": "example-box",
        "machines": {
            "example-box": {
                "host": "192.0.2.10",
                "user": "exampleuser",
                "identity_file": "~/.ssh/id_example_key",
                "transport": "ssh",
                "reconnect_attempts": 3,
                "reconnect_backoff": 0.0,
            }
        },
    }
}


@pytest.fixture
def state_home(tmp_path, monkeypatch):
    """A per-test state home (XDG on POSIX, LOCALAPPDATA on Windows) that does not exist yet."""
    home = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home))
    return home


@pytest.fixture
def iterm2_toml(tmp_path, monkeypatch):
    """Write the iterm2.toml the launch reads; returns a writer taking the file text."""
    config_home = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("APPDATA", str(config_home))
    path = config_home / "ai-cli-utils" / "iterm2.toml"
    path.parent.mkdir(parents=True)

    def write(text: str) -> None:
        path.write_text(text, encoding="utf-8")

    write("[iterm2]\nenabled = true\n")
    return write


@pytest.fixture
def outside_iterm2(monkeypatch):
    """A launch from a terminal that is not iTerm2, so no launch-time refresh runs."""
    monkeypatch.delenv("LC_TERMINAL", raising=False)
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.setenv("ITERM_SESSION_ID", _ITERM_SESSION_ID)


def _registry_file(state_home):
    return state_home / "ai-cli-utils" / "iterm2-sessions.json"


def _sessions(state_home):
    return json.loads(_registry_file(state_home).read_text(encoding="utf-8"))["sessions"]


def _snapshot(state_home):
    path = _registry_file(state_home)
    return _sessions(state_home) if path.is_file() else None


def _launch_local(state_home, *, argv, name="1"):
    """Drive a local tmux launch through ``_do_session_launch`` up to its exec into tmux.

    tmux answers every call with success, so the launch re-attaches an existing session
    (`attach-session -d`). Returns ``(exec_argv, registry_at_exec)``: the registry as it
    stood at the moment the launcher handed off.
    """
    seen: dict = {}

    def fake_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_execvp(file, exec_argv):
        seen["exec"] = list(exec_argv)
        seen["registry"] = _snapshot(state_home)
        raise SystemExit(0)

    with (
        patch("sys.argv", argv),
        patch("ai_cli.main.subprocess.run", side_effect=fake_run),
        patch("ai_cli.main.os.execvp", side_effect=fake_execvp),
        patch("ai_cli.tmux_setup.tmux_runs", return_value=True),
        patch("ai_cli.tmux_setup.formats_expand", return_value=True),
        patch("ai_cli.tmux_setup.probe", return_value=MagicMock(versions_disagree=False)),
        patch("ai_cli.tmux_setup.report_lines", return_value=[]),
        patch(
            "ai_cli.compact_transport.resolve",
            return_value=MagicMock(must_refuse=False, platform_limited=False, detail="tmux"),
        ),
        patch("ai_cli.session.cleanup_stale_sessions"),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.trust.ensure_workspace_trusted"),
        patch("ai_cli.session_script.get_engine_script", return_value="true\n"),
        patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
        patch("ai_cli.iterm2._emit_iterm2_profile_setup"),
        patch("ai_cli.iterm2._current_pane_tty", return_value=_PANE_TTY),
    ):
        with pytest.raises(SystemExit):
            _do_session_launch(
                engine="c",
                name=name,
                resume=False,
                once=False,
                bare=False,
                notify=False,
                sandbox=False,
                no_worktree=True,
                no_direnv=True,
                remote=False,
                project="",
                is_remote=False,
                project_prefix_override="myproject",
                extra_args=[],
                config={"worktree": {"enabled": False}},
            )
    return seen.get("exec"), seen.get("registry")


def _remote_preflight(command, **_kwargs):
    """Answer the remote launch's probes; nothing here reaches a network."""
    if isinstance(command, (list, tuple)) and any("dolt_server.py" in str(part) for part in command):
        return subprocess.CompletedProcess(command, 0, stdout='{"status": "healthy"}', stderr="")
    if isinstance(command, (list, tuple)) and command and command[-1] == _REMOTE_SHELL_PROBE_CMD:
        return subprocess.CompletedProcess(command, 0, stdout="zsh\n", stderr="")
    return subprocess.CompletedProcess(command, 0, stdout="current", stderr="")


def _launch_remote(argv, *, runner=None, ssh_exit_codes=None):
    """Drive ``ai c -R`` over the pure-ssh transport through ``cli()``.

    With ``runner``, it replaces ``run_ssh_with_reconnect``. With ``ssh_exit_codes``, the
    real reconnect loop runs and each ssh attempt exits with the next code.
    """
    handoff = (
        patch("ai_cli.transport.run_ssh_with_reconnect", side_effect=runner)
        if runner is not None
        else patch("ai_cli.transport.subprocess.call", side_effect=list(ssh_exit_codes or [0]))
    )
    with (
        patch("sys.argv", argv),
        patch("ai_cli.main.sys.platform", "linux"),
        patch("ai_cli.main.shutil.which", return_value="/usr/bin/tmux"),
        patch("ai_cli.config.load_config", return_value=_REMOTE_CONFIG),
        patch("ai_cli.session.get_project_prefix", return_value="myproject"),
        patch("ai_cli.session.is_current_project_resolved", return_value=True),
        patch("ai_cli.config.get_project_aliases", return_value={}),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.main.trigger_background_update"),
        patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
        patch("ai_cli.iterm2._emit_iterm2_profile_setup"),
        patch("ai_cli.iterm2._current_pane_tty", return_value=_PANE_TTY),
        patch("ai_cli.main.subprocess.run", side_effect=_remote_preflight),
        patch("ai_cli.transport.time.sleep"),
        handoff,
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli()
    return exc_info.value.code


# --- T-1.2 registry write at launch -----------------------------------------------


def test_given_tracking_enabled_when_local_session_launched_then_one_record_exists_before_the_tmux_handoff(
    state_home, iterm2_toml, outside_iterm2, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    exec_argv, registry_at_exec = _launch_local(state_home, argv=["ai", "c", "1", "-W", "-D"])

    assert exec_argv == ["tmux", "attach-session", "-d", "-t", "c-myproject-1"]
    assert registry_at_exec is not None, "the record must exist before the launcher execs into tmux"
    assert len(registry_at_exec) == 1
    record = registry_at_exec[0]
    assert record["kind"] == "local"
    assert record["name"] == "c-myproject-1"
    assert record["relaunch_argv"] == ["ai", "c", "1", "-W", "-D"]
    assert record["cwd"] == str(tmp_path)
    assert record["remote"] is None
    assert record["iterm2"] == {
        "session_uuid": "ABCDEF01-2345-6789-ABCD-EF0123456789",
        "window": 0,
        "tab": 3,
        "pane": 1,
        "tty": _PANE_TTY,
    }
    assert record["launcher_pid"] == os.getpid()
    assert record["ended_at"] is None


def test_given_an_unnamed_launch_when_recorded_then_relaunch_argv_names_the_resolved_session(
    state_home, iterm2_toml, outside_iterm2
):
    """`ai c` alone allocates the next free slot, so re-running it would not re-attach."""
    _, registry_at_exec = _launch_local(state_home, argv=["ai", "c", "-W"], name="")

    record = registry_at_exec[0]
    assert record["relaunch_argv"] == ["ai", "c", "-W", record["name"]]


def test_given_tracking_enabled_when_remote_session_launched_then_record_holds_alias_and_no_host_user_or_key(
    state_home, iterm2_toml, outside_iterm2
):
    seen = {}

    def runner(ssh_args, cleanup_cmd, **kwargs):
        seen["registry"] = _snapshot(state_home)
        return 0

    _launch_remote(["ai", "c", "-R", "2"], runner=runner)

    assert seen["registry"] is not None, "the record must exist before the remote dial"
    (record,) = seen["registry"]
    assert record["kind"] == "remote"
    assert record["name"] == "c-r-myproject-2"
    assert record["remote"] == {"alias": "example-box", "session": "c-r-myproject-2"}
    assert record["relaunch_argv"] == ["ai", "c", "-R", "2"]
    stored = json.dumps(record)
    for private in ("192.0.2.10", "exampleuser", "id_example_key", "ssh"):
        assert private not in json.dumps(record["relaunch_argv"]), private
    for private in ("192.0.2.10", "exampleuser", "id_example_key"):
        assert private not in stored, private


@pytest.mark.parametrize(
    "toml_text",
    [
        "[iterm2.persistence.tracking]\nenabled = false\n",
        "[iterm2.persistence]\nenabled = false\n",
    ],
)
def test_given_tracking_disabled_when_session_launched_then_no_registry_file_is_written(
    state_home, iterm2_toml, outside_iterm2, toml_text
):
    iterm2_toml(toml_text)

    exec_argv, registry_at_exec = _launch_local(state_home, argv=["ai", "c", "1"])

    assert exec_argv is not None, "the launch must still hand off"
    assert registry_at_exec is None
    assert not _registry_file(state_home).exists()
    assert not _registry_file(state_home).with_name("iterm2-sessions.json.lock").exists()


@pytest.mark.parametrize(
    "toml_text",
    [
        "[iterm2.persistence.tracking]\nenabled = false\n",
        "[iterm2.persistence]\nenabled = false\n",
    ],
)
def test_given_tracking_disabled_when_launch_is_tracked_then_the_state_directory_is_not_created(
    state_home, iterm2_toml, toml_text
):
    """The full launch writes other state of its own, so the directory claim is made here."""
    iterm2_toml(toml_text)

    outcome = session_registry.track_launch(kind="local", name="c-myproject-1", relaunch_argv=["ai", "c", "1"])

    assert outcome.record_id is None
    assert not state_home.exists()


@pytest.mark.parametrize(
    ("toml_text", "rule"),
    [
        ('[iterm2.persistence.tracking]\nexclude = ["c-myproject-*"]\n', "exclude glob 'c-myproject-*'"),
        ('[iterm2.persistence.tracking]\ninclude = ["c-other-*"]\n', "include ['c-other-*'] has no matching glob"),
        ("[iterm2.persistence.tracking]\ninclude_local = false\n", "include_local = false"),
    ],
)
def test_given_a_filter_rule_when_session_launched_then_it_is_not_recorded_and_the_rule_is_logged_at_debug(
    state_home, iterm2_toml, outside_iterm2, caplog, toml_text, rule
):
    iterm2_toml(toml_text)
    caplog.set_level(logging.DEBUG, logger="ai_cli.session_registry")

    exec_argv, registry_at_exec = _launch_local(state_home, argv=["ai", "c", "1"])

    assert exec_argv is not None
    assert registry_at_exec is None
    debug = [
        r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG and r.name == "ai_cli.session_registry"
    ]
    assert any("c-myproject-1" in message and rule in message for message in debug), debug


@pytest.mark.parametrize("value", [None, "", "not-an-iterm-id", "w0t3:ABCDEF01-2345"])
def test_given_iterm_session_id_absent_or_malformed_when_launched_then_record_has_null_iterm2_and_launch_proceeds(
    state_home, iterm2_toml, outside_iterm2, monkeypatch, capsys, value
):
    if value is None:
        monkeypatch.delenv("ITERM_SESSION_ID", raising=False)
    else:
        monkeypatch.setenv("ITERM_SESSION_ID", value)

    exec_argv, registry_at_exec = _launch_local(state_home, argv=["ai", "c", "1"])

    assert exec_argv == ["tmux", "attach-session", "-d", "-t", "c-myproject-1"]
    (record,) = registry_at_exec
    assert record["iterm2"] is None
    assert "session registry" not in capsys.readouterr().err


def test_given_registry_cannot_be_written_when_launched_then_one_warning_and_the_launch_is_unchanged(
    state_home, iterm2_toml, outside_iterm2, capsys
):
    # A directory where the registry file belongs: the launcher's own state still works.
    _registry_file(state_home).mkdir(parents=True)

    exec_argv, _ = _launch_local(state_home, argv=["ai", "c", "1"])

    assert exec_argv == ["tmux", "attach-session", "-d", "-t", "c-myproject-1"]
    assert _registry_file(state_home).is_dir()
    warnings = [line for line in capsys.readouterr().err.splitlines() if "session registry" in line]
    assert len(warnings) == 1, warnings


def test_given_a_bad_persistence_key_when_launched_then_one_warning_names_it_and_the_launch_proceeds(
    state_home, iterm2_toml, outside_iterm2, capsys
):
    iterm2_toml('[iterm2.persistence.tracking]\ninclude = "c-*"\n')

    exec_argv, _ = _launch_local(state_home, argv=["ai", "c", "1"])

    assert exec_argv == ["tmux", "attach-session", "-d", "-t", "c-myproject-1"]
    warnings = [line for line in capsys.readouterr().err.splitlines() if "session registry" in line]
    assert len(warnings) == 1, warnings
    assert "include" in warnings[0] and "a list of strings" in warnings[0]


_CHILD_WRITER = textwrap.dedent(
    """
    import sys
    from ai_cli import session_registry
    from ai_cli.iterm2 import _PERSISTENCE_DEFAULTS
    worker = sys.argv[1]
    for i in range(int(sys.argv[2])):
        session_registry.record_launch(
            kind="local", name=f"c-myproject-{worker}-{i}", relaunch_argv=["ai", "c", worker],
            cwd=None, remote=None, tty="", iterm_session_id=None, launcher_pid=0,
            config=_PERSISTENCE_DEFAULTS,
        )
    """
)


def test_given_a_launch_mid_write_when_a_second_launch_records_then_both_records_survive(state_home, monkeypatch):
    """The second launch (a separate process) starts after the first has read the registry
    and before it writes. Without the lock it writes in that gap and the first launch's
    stale copy then overwrites it; with the lock it waits and re-reads."""
    env = {**os.environ, "XDG_STATE_HOME": str(state_home), "LOCALAPPDATA": str(state_home)}
    real_write = session_registry._write
    second: list[subprocess.Popen] = []

    def write_after_second_launch_started(path, doc):
        second.append(subprocess.Popen([sys.executable, "-c", _CHILD_WRITER, "second", "1"], env=env))
        try:
            second[0].wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass  # blocked on the lock, as it should be
        real_write(path, doc)

    monkeypatch.setattr(session_registry, "_write", write_after_second_launch_started)
    session_registry.record_launch(
        kind="local",
        name="c-myproject-first",
        relaunch_argv=["ai", "c", "first"],
        cwd=None,
        remote=None,
        tty="",
        iterm_session_id=None,
        launcher_pid=0,
        config=_PERSISTENCE_DEFAULTS,
    )
    assert second[0].wait(timeout=60) == 0

    assert {record["name"] for record in _sessions(state_home)} == {"c-myproject-first", "c-myproject-second-0"}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_given_a_recorded_launch_when_written_then_file_is_owner_only(state_home, iterm2_toml, outside_iterm2):
    _launch_local(state_home, argv=["ai", "c", "1"])

    assert stat.S_IMODE(_registry_file(state_home).stat().st_mode) == 0o600
    assert [p.name for p in _registry_file(state_home).parent.iterdir() if p.name.endswith(".tmp")] == []


def test_given_the_same_session_relaunched_when_recorded_again_then_it_still_has_one_record(
    state_home, iterm2_toml, outside_iterm2
):
    _launch_local(state_home, argv=["ai", "c", "1"])
    first_id = _sessions(state_home)[0]["id"]

    _launch_local(state_home, argv=["ai", "c", "1"])

    (record,) = _sessions(state_home)
    assert record["id"] != first_id


def test_given_refresh_on_launch_when_launched_in_iterm2_then_other_records_get_current_positions(
    state_home, iterm2_toml, monkeypatch
):
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.setenv("ITERM_SESSION_ID", _ITERM_SESSION_ID)
    monkeypatch.setattr(session_registry.sys, "platform", "darwin")
    session_registry.record_launch(
        kind="local",
        name="c-myproject-7",
        relaunch_argv=["ai", "c", "7"],
        cwd=None,
        remote=None,
        tty="",
        iterm_session_id=None,
        launcher_pid=0,
        config=_PERSISTENCE_DEFAULTS,
    )
    panes = {"/dev/ttys077": {"session_uuid": "FEDCBA98-0000-0000-0000-000000000000", "window": 1, "tab": 0, "pane": 2}}

    with (
        patch("ai_cli.iterm2._iterm_pane_tty_for_tmux_session", return_value="/dev/ttys077"),
        patch("ai_cli.iterm2._iterm2_panes_by_tty", return_value=panes) as lookup,
    ):
        _launch_local(state_home, argv=["ai", "c", "1"])

    assert lookup.call_count == 1
    other = next(r for r in _sessions(state_home) if r["name"] == "c-myproject-7")
    assert other["iterm2"] == {**panes["/dev/ttys077"], "tty": "/dev/ttys077"}


def test_given_refresh_on_launch_disabled_when_launched_in_iterm2_then_no_applescript_pass_runs(
    state_home, iterm2_toml, monkeypatch
):
    iterm2_toml("[iterm2.persistence.tracking]\nrefresh_on_launch = false\n")
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.setattr(session_registry.sys, "platform", "darwin")

    with patch("ai_cli.iterm2._iterm2_panes_by_tty") as lookup:
        _launch_local(state_home, argv=["ai", "c", "1"])
        _launch_local(state_home, argv=["ai", "c", "2"])

    assert lookup.call_count == 0


# --- T-1.4 clean-exit marking -----------------------------------------------------


def test_given_ssh_exits_zero_when_the_remote_wrapper_returns_then_the_record_is_marked_ended(
    state_home, iterm2_toml, outside_iterm2
):
    code = _launch_remote(["ai", "c", "-R", "2"], ssh_exit_codes=[0])

    assert code == 0
    (record,) = _sessions(state_home)
    assert record["ended_at"] is not None


@pytest.mark.parametrize(
    "exit_codes",
    [
        pytest.param([255, 255, 255], id="connection-lost-until-the-ladder-gives-up"),
        pytest.param([129, 255, 255], id="hangup-then-unreachable"),
        pytest.param([130], id="interrupted"),
    ],
)
def test_given_ssh_exits_non_zero_when_the_remote_wrapper_returns_then_the_record_stays_open(
    state_home, iterm2_toml, outside_iterm2, exit_codes
):
    code = _launch_remote(["ai", "c", "-R", "2"], ssh_exit_codes=exit_codes)

    assert code == exit_codes[-1]
    (record,) = _sessions(state_home)
    assert record["ended_at"] is None


def test_given_ssh_reconnects_then_exits_zero_when_the_wrapper_returns_then_the_record_is_marked_ended(
    state_home, iterm2_toml, outside_iterm2
):
    """A clean exit after a dropped link still means the remote session ended."""
    _launch_remote(["ai", "c", "-R", "2"], ssh_exit_codes=[255, 0])

    (record,) = _sessions(state_home)
    assert record["ended_at"] is not None


def test_given_no_record_written_when_ssh_exits_zero_then_the_launch_still_exits_cleanly(
    state_home, iterm2_toml, outside_iterm2
):
    iterm2_toml("[iterm2.persistence.tracking]\ninclude_remote = false\n")

    assert _launch_remote(["ai", "c", "-R", "2"], ssh_exit_codes=[0]) == 0
    assert not _registry_file(state_home).exists()


def test_remote_dial_argv_is_unchanged_by_tracking(state_home, iterm2_toml, outside_iterm2):
    """Bookkeeping must not alter what is dialled."""
    dialled = []

    def runner(ssh_args, cleanup_cmd, **kwargs):
        dialled.append(list(ssh_args))
        return 0

    _launch_remote(["ai", "c", "-R", "2"], runner=runner)
    iterm2_toml("[iterm2.persistence]\nenabled = false\n")
    _launch_remote(["ai", "c", "-R", "2"], runner=runner)

    assert dialled[0][0] == "ssh"
    assert shlex.join(dialled[0]) == shlex.join(dialled[1])
