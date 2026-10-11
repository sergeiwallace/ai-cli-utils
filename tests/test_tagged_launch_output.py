"""Through the real `ai` entry point, every line a launch prints carries a log type tag.

The founding observation was a launch where every line read `[launch] ...` except
the Dolt supervisor's `dolt_server: healthy (socket accepted)`. These tests drive
`ai_cli.main.cli()` with `sys.argv` set, capture both streams, and require every
emitted line to match `^\\[[a-z0-9_-]+\\] `, the supervisor's relayed line included.
Machine-readable output is the deliberate opposite: `ai internal
allocate-session-name` must still print one bare JSON document.
"""

from __future__ import annotations

import json
import re
import subprocess
from unittest.mock import patch

import pytest

from ai_cli.main import _REMOTE_SHELL_PROBE_CMD, cli

TAGGED = re.compile(r"^\[[a-z0-9_-]+\] ")
DOLT_LINE = "dolt_server: healthy (socket accepted)"

_REMOTE_CONFIG = {
    "remote": {
        "default": "example-box",
        "machines": {
            "example-box": {
                "host": "192.0.2.10",
                "user": "exampleuser",
                "transport": "ssh",
                "reconnect_attempts": 1,
                "reconnect_backoff": 0.0,
            }
        },
    },
}


@pytest.fixture
def dolt_script(tmp_path):
    """A supervisor script path for `[dolt_server] script_path`; its run is stubbed below."""
    script = tmp_path / "dolt_server.py"
    script.write_text("", encoding="utf-8")
    return script


@pytest.fixture(autouse=True)
def _state_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))


def _untagged(text: str) -> list[str]:
    return [line for line in text.splitlines() if not TAGGED.match(line)]


def _preflight(command, **_kwargs):
    """Answer every subprocess the launch runs; the supervisor answers as it does on a real host."""
    if isinstance(command, (list, tuple)) and any("dolt_server.py" in str(part) for part in command):
        return subprocess.CompletedProcess(command, 0, stdout=f"{DOLT_LINE}\n", stderr=None)
    if isinstance(command, (list, tuple)) and command and command[-1] == _REMOTE_SHELL_PROBE_CMD:
        return subprocess.CompletedProcess(command, 0, stdout="zsh\n", stderr="")
    return subprocess.CompletedProcess(command, 0, stdout="current", stderr="")


def _launch_remote(argv, config, ssh_exit_codes):
    with (
        patch("sys.argv", argv),
        patch("ai_cli.main.sys.platform", "linux"),
        patch("ai_cli.main.shutil.which", return_value="/usr/bin/tmux"),
        patch("ai_cli.config.load_config", return_value=config),
        patch("ai_cli.main.load_config", return_value=config),
        patch("ai_cli.session.get_project_prefix", return_value="myproject"),
        patch("ai_cli.session.is_current_project_resolved", return_value=True),
        patch("ai_cli.config.get_project_aliases", return_value={}),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.main.trigger_background_update"),
        patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
        patch("ai_cli.iterm2._emit_iterm2_profile_setup"),
        patch("ai_cli.iterm2._current_pane_tty", return_value=None),
        patch("ai_cli.main.subprocess.run", side_effect=_preflight),
        patch("ai_cli.transport.time.sleep"),
        patch("ai_cli.transport.subprocess.call", side_effect=list(ssh_exit_codes)),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli()
    return exc_info.value.code


def test_given_a_remote_launch_with_a_dolt_supervisor_when_run_through_cli_then_every_line_is_tagged(
    dolt_script, capsys
):
    config = {**_REMOTE_CONFIG, "dolt_server": {"script_path": str(dolt_script)}}

    _launch_remote(["ai", "c", "-R", "2"], config, ssh_exit_codes=[0])

    captured = capsys.readouterr()
    emitted = captured.out + captured.err
    assert "[dolt_server] healthy (socket accepted)" in captured.err
    assert DOLT_LINE not in emitted.replace("[dolt_server] healthy", "")
    assert "[launch] Ready: handing off to Claude Code" in captured.err
    assert not _untagged(emitted), _untagged(emitted)


def test_given_a_dropped_remote_link_when_reconnecting_through_cli_then_the_transport_lines_are_tagged(
    dolt_script, capsys
):
    config = {**_REMOTE_CONFIG, "dolt_server": {"script_path": str(dolt_script)}}

    _launch_remote(["ai", "c", "-R", "2"], config, ssh_exit_codes=[255])

    captured = capsys.readouterr()
    emitted = captured.out + captured.err
    assert "[transport] " in captured.err
    assert not _untagged(emitted), _untagged(emitted)


def test_given_a_local_dry_run_when_run_through_cli_then_every_plan_and_launch_line_is_tagged(capsys):
    with (
        patch("sys.argv", ["ai", "c", "7", "--dry-run"]),
        patch("ai_cli.session.detect_repo_root", return_value="/tmp/repo"),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.session.get_project_prefix", return_value="myproject"),
        patch("ai_cli.session.is_current_project_resolved", return_value=True),
        patch("ai_cli.session.build_session_name", return_value=("c-myproject-7", "myproject-7")),
        patch("ai_cli.tmux_setup.tmux_present", return_value=True),
        patch("ai_cli.main.os.execvp", side_effect=AssertionError("a dry run must not exec")),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli()

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    emitted = captured.out + captured.err
    assert "[plan] dry run -- nothing was created" in captured.out
    assert "[launch] Starting Claude Code session" in captured.err
    assert not _untagged(emitted), _untagged(emitted)


def test_given_an_unknown_option_when_run_through_cli_then_the_usage_error_is_tagged(capsys):
    with patch("sys.argv", ["ai", "register", "--no-such-option"]):
        with pytest.raises(SystemExit) as exc_info:
            cli()

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "[cli] Error: " in err
    assert not _untagged(err), _untagged(err)


def test_given_allocate_session_name_when_run_through_cli_then_stdout_is_one_bare_json_document(capsys):
    with (
        patch("sys.argv", ["ai", "internal", "allocate-session-name", "c", "myproject", "4"]),
        patch("ai_cli.main._session.build_session_name", return_value=("c-myproject-4", "myproject-4")),
        patch("ai_cli.main._tmux_setup.config_opts_out", return_value=False),
        patch("ai_cli.config.load_config", return_value={}),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli()

    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert json.loads(out) == {"session_id": "c-myproject-4", "ai_name": "myproject-4"}
    assert not TAGGED.match(out)


def test_given_registry_json_when_run_through_cli_then_stdout_parses_as_json(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "config"))
    with (
        patch("sys.argv", ["ai", "iterm2", "sessions", "--json"]),
        patch("ai_cli.main._session_registry.load_registry", return_value={"version": 1, "sessions": []}),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli()

    assert exc_info.value.code == 0
    assert json.loads(capsys.readouterr().out) == {"version": 1, "sessions": []}


def test_given_a_child_line_without_a_tag_when_the_relay_is_bypassed_then_the_contract_test_fails(capsys):
    """Negative arm: the line check really does reject the founding untagged line."""
    print(DOLT_LINE)
    assert _untagged(capsys.readouterr().out) == [DOLT_LINE]
