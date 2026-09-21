"""Tests for argv-safe VS Code Remote-SSH file opening."""

from unittest.mock import MagicMock, patch

import pytest

from ai_cli.main import _handle_internal
from ai_cli.vscode import build_vscode_remote_command, open_vscode_remote


@pytest.mark.parametrize(
    "path",
    [
        "/srv/my project/file.py",
        "/srv/quote'and\"double.py",
        "/srv/$(printf injected).py",
        "/srv/`printf injected`.py",
        "/srv/file;printf injected.py",
        "/srv/資料.py",
    ],
)
def test_given_shell_metacharacters_when_building_command_then_path_remains_one_argument(path):
    command = build_vscode_remote_command("framework", path)

    assert command == ["code", "--remote", "ssh-remote+framework", "--goto", path]


def test_given_positive_line_when_building_command_then_appends_line_without_dangling_colon():
    assert build_vscode_remote_command("framework", "/srv/app.py", "12") == [
        "code",
        "--remote",
        "ssh-remote+framework",
        "--goto",
        "/srv/app.py:12",
    ]


def test_given_no_line_when_building_command_then_path_has_no_dangling_colon():
    assert build_vscode_remote_command("framework", "/srv/app.py", "")[-1] == "/srv/app.py"


@pytest.mark.parametrize("path", ["relative.py", "", "/srv/bad\npath.py", "/srv/bad\x00path.py"])
def test_given_invalid_path_when_building_command_then_rejects_before_spawn(path):
    with pytest.raises(ValueError, match="path"):
        build_vscode_remote_command("framework", path)


@pytest.mark.parametrize("line", ["0", "-1", "1.5", "line", "1\n2"])
def test_given_invalid_line_when_building_command_then_rejects_before_spawn(line):
    with pytest.raises(ValueError, match="line"):
        build_vscode_remote_command("framework", "/srv/app.py", line)


@pytest.mark.parametrize("authority", ["", "host name", "host/path", "host;command", "host\nname"])
def test_given_invalid_authority_when_building_command_then_rejects_before_spawn(authority):
    with pytest.raises(ValueError, match="authority"):
        build_vscode_remote_command(authority, "/srv/app.py")


def test_given_valid_inputs_when_opening_remote_then_runs_argv_without_shell():
    runner = MagicMock(return_value=MagicMock(returncode=0))

    result = open_vscode_remote("framework", "/srv/my project/app.py", "9", runner=runner)

    assert result == 0
    runner.assert_called_once_with(
        ["code", "--remote", "ssh-remote+framework", "--goto", "/srv/my project/app.py:9"],
        check=False,
        shell=False,
    )


def test_given_nonzero_vscode_exit_when_opening_remote_then_propagates_status():
    runner = MagicMock(return_value=MagicMock(returncode=17))

    assert open_vscode_remote("framework", "/srv/app.py", runner=runner) == 17


def test_given_internal_open_action_when_dispatched_then_forwards_arguments_and_exit_status():
    with (
        patch("ai_cli.main._config.load_config", return_value={}),
        patch("ai_cli.vscode.open_vscode_remote", return_value=7) as open_remote,
        pytest.raises(SystemExit) as exc_info,
    ):
        _handle_internal(["open-vscode-remote", "framework", "/srv/app.py", "12"])

    assert exc_info.value.code == 7
    open_remote.assert_called_once_with("framework", "/srv/app.py", "12")


def test_given_internal_open_action_with_missing_args_when_dispatched_then_exits_one(capsys):
    with (
        patch("ai_cli.main._config.load_config", return_value={}),
        pytest.raises(SystemExit) as exc_info,
    ):
        _handle_internal(["open-vscode-remote", "framework"])

    assert exc_info.value.code == 1
    assert "Usage: ai internal open-vscode-remote" in capsys.readouterr().err
