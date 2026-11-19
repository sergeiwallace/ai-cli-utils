"""Through the real `ai c` entry point, configured launch extensions run before the session starts.

An extension is a command named in ``[extensions.run.<name>]`` of config.toml. It runs once
per launch, in a directory that is absolute or relative to ``[project] projects_dir``, and its
error policy (``on_error``, a default in ``[extensions]`` with a per-extension override)
decides whether a failure warns and launches or refuses the launch. Every test drives
``cli()``; only the session start itself and the launch's other preflights are stubbed, and
each extension is a real child process.
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_cli.launch_reporter import InstallOrigin
from ai_cli.main import cli

GREET = """\
import os, sys
with open(sys.argv[1], "a", encoding="utf-8") as marker:
    marker.write(os.getcwd() + "\\n")
print("hello from the greeting extension")
"""

FAIL = """\
import sys
print("greeting extension could not reach its greeting", file=sys.stderr)
sys.exit(3)
"""

SLOW = """\
import time
time.sleep(30)
"""


@pytest.fixture(autouse=True)
def _state_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))


@pytest.fixture
def projects(tmp_path):
    root = tmp_path / "projects"
    (root / "myextensions").mkdir(parents=True)
    return root


def _script(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / name
    script.write_text(body, encoding="utf-8")
    return script


def _launch(config: dict, argv=("ai", "c", "session-1")):
    """Run the launch; return (exit code, stderr text, session-launch mock)."""
    session_launch = MagicMock()
    stderr = io.StringIO()
    code = 0
    with (
        patch("sys.argv", list(argv)),
        patch("ai_cli.config.load_config", return_value=config),
        patch("ai_cli.main._launch_install_origin", return_value=InstallOrigin.EDITABLE_CHECKOUT),
        patch("ai_cli.main._auto_update_if_stale", return_value=False),
        patch("ai_cli.main._ensure_dolt_server"),
        patch("ai_cli.main._ensure_wake_sidecar"),
        patch("ai_cli.main.trigger_background_update"),
        patch("ai_cli.tunnel._ensure_nats_tunnel"),
        patch("ai_cli.process_manager._start_stale_session_reaper_for_launch"),
        patch("ai_cli.main._do_session_launch", session_launch),
        patch("sys.stderr", stderr),
    ):
        try:
            cli()
        except SystemExit as exc:
            code = exc.code or 0
    return code, stderr.getvalue(), session_launch


def _launch_logs(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "state").rglob("launch-logs/session-1-*.log"))


def _log_text(tmp_path: Path) -> str:
    logs = _launch_logs(tmp_path)
    assert len(logs) == 1, logs
    return logs[0].read_text(encoding="utf-8")


def _config(projects: Path, extensions: dict, **defaults) -> dict:
    return {"project": {"projects_dir": str(projects)}, "extensions": {**defaults, "run": extensions}}


def test_given_no_extensions_configured_when_launching_then_the_launch_runs_nothing_and_reports_nothing(
    tmp_path, projects
):
    code, stderr, session_launch = _launch({"project": {"projects_dir": str(projects)}})

    assert code == 0
    session_launch.assert_called_once()
    assert "extension" not in stderr
    assert "extension" not in _log_text(tmp_path)


def test_given_an_empty_extensions_table_when_launching_then_the_launch_runs_nothing_and_reports_nothing(
    tmp_path, projects
):
    code, stderr, session_launch = _launch({"project": {"projects_dir": str(projects)}, "extensions": {}})

    assert code == 0
    session_launch.assert_called_once()
    assert "extension" not in stderr
    assert "extension" not in _log_text(tmp_path)


def test_given_a_relative_extension_path_when_launching_then_it_runs_once_in_the_projects_root_directory(
    tmp_path, projects
):
    _script(projects / "myextensions", "greet.py", GREET)
    marker = tmp_path / "marker.txt"
    config = _config(
        projects,
        {"greeting": {"path": "myextensions", "command": [sys.executable, "greet.py", str(marker)]}},
    )

    code, stderr, session_launch = _launch(config)

    assert code == 0
    session_launch.assert_called_once()
    runs = marker.read_text(encoding="utf-8").splitlines()
    assert [Path(run).resolve() for run in runs] == [(projects / "myextensions").resolve()]
    assert "greeting" in stderr


def test_given_a_relative_command_file_when_launching_then_it_resolves_inside_the_extension_path(tmp_path, projects):
    script = _script(projects / "myextensions" / "bin", "greet.py", GREET)
    marker = tmp_path / "marker.txt"
    config = _config(
        projects,
        {
            "greeting": {
                "path": "myextensions",
                "command": [sys.executable, str(Path("bin") / script.name), str(marker)],
            }
        },
    )

    code, _stderr, session_launch = _launch(config)

    assert code == 0
    session_launch.assert_called_once()
    assert marker.read_text(encoding="utf-8").count("\n") == 1


def test_given_an_absolute_extension_path_when_launching_then_it_is_used_as_is(tmp_path, projects):
    elsewhere = tmp_path / "elsewhere"
    _script(elsewhere, "greet.py", GREET)
    marker = tmp_path / "marker.txt"
    config = _config(
        projects, {"greeting": {"path": str(elsewhere), "command": [sys.executable, "greet.py", str(marker)]}}
    )

    code, _stderr, session_launch = _launch(config)

    assert code == 0
    session_launch.assert_called_once()
    assert [Path(run).resolve() for run in marker.read_text(encoding="utf-8").splitlines()] == [elsewhere.resolve()]


def test_given_a_failing_extension_under_warn_when_launching_then_it_warns_logs_the_error_and_still_launches(
    tmp_path, projects
):
    _script(projects / "myextensions", "fail.py", FAIL)
    config = _config(
        projects,
        {"greeting": {"path": "myextensions", "command": [sys.executable, "fail.py"]}},
        on_error="warn",
    )

    code, stderr, session_launch = _launch(config)

    assert code == 0
    session_launch.assert_called_once()
    warning = [line for line in stderr.splitlines() if "Warning:" in line and "greeting" in line]
    assert warning, stderr
    assert "status 3" in warning[0]
    log = _log_text(tmp_path)
    assert "Warning:" in log
    assert "greeting extension could not reach its greeting" in log


def test_given_an_extension_that_prints_a_status_line_when_launching_then_the_terminal_line_carries_its_last_line(
    tmp_path, projects
):
    _script(
        projects / "myextensions",
        "status.py",
        'print("probing the watcher")\nprint("watcher disabled for this host")\n',
    )
    config = _config(projects, {"status": {"path": "myextensions", "command": [sys.executable, "status.py"]}})

    code, stderr, session_launch = _launch(config)

    assert code == 0
    session_launch.assert_called_once()
    line = [entry for entry in stderr.splitlines() if "status: exited with status 0" in entry]
    assert line, stderr
    assert line[0].endswith(": watcher disabled for this host"), line[0]
    assert "probing the watcher" not in stderr
    assert "probing the watcher" in _log_text(tmp_path)


def test_given_a_failing_extension_under_warn_when_launching_then_the_warning_carries_its_last_output_line(
    tmp_path, projects
):
    _script(projects / "myextensions", "fail.py", FAIL)
    config = _config(
        projects, {"greeting": {"path": "myextensions", "command": [sys.executable, "fail.py"]}}, on_error="warn"
    )

    code, stderr, session_launch = _launch(config)

    assert code == 0
    session_launch.assert_called_once()
    warning = [line for line in stderr.splitlines() if "Warning:" in line and "greeting" in line]
    assert warning, stderr
    assert "greeting extension could not reach its greeting" in warning[0]


def test_given_a_failing_extension_under_fail_when_launching_then_it_logs_an_error_exits_nonzero_and_does_not_launch(
    tmp_path, projects
):
    _script(projects / "myextensions", "fail.py", FAIL)
    config = _config(
        projects,
        {"greeting": {"path": "myextensions", "command": [sys.executable, "fail.py"]}},
        on_error="fail",
    )

    code, stderr, session_launch = _launch(config)

    assert code != 0
    session_launch.assert_not_called()
    error = [line for line in stderr.splitlines() if "Error:" in line and "greeting" in line]
    assert error, stderr
    assert "status 3" in error[0]
    log = _log_text(tmp_path)
    assert "Error:" in log
    assert "greeting extension could not reach its greeting" in log


def test_given_no_policy_set_when_an_extension_fails_then_the_shipped_default_refuses_the_launch(tmp_path, projects):
    _script(projects / "myextensions", "fail.py", FAIL)
    config = _config(projects, {"greeting": {"path": "myextensions", "command": [sys.executable, "fail.py"]}})

    code, _stderr, session_launch = _launch(config)

    assert code != 0
    session_launch.assert_not_called()


@pytest.mark.parametrize(("policy", "launched"), [("warn", True), ("fail", False)])
def test_given_a_missing_command_when_launching_then_it_follows_the_policy(tmp_path, projects, policy, launched):
    config = _config(
        projects,
        {"greeting": {"path": "myextensions", "command": ["no-such-greeting-command-for-tests"]}},
        on_error=policy,
    )

    code, stderr, session_launch = _launch(config)

    assert (code == 0) is launched
    assert session_launch.called is launched
    assert "no-such-greeting-command-for-tests" in stderr
    assert "not found" in stderr
    assert "not found" in _log_text(tmp_path)


@pytest.mark.parametrize(("policy", "launched"), [("warn", True), ("fail", False)])
def test_given_a_missing_extension_directory_when_launching_then_it_follows_the_policy(
    tmp_path, projects, policy, launched
):
    config = _config(
        projects,
        {"greeting": {"path": "no-such-directory", "command": [sys.executable, "greet.py"]}},
        on_error=policy,
    )

    code, stderr, session_launch = _launch(config)

    assert (code == 0) is launched
    assert session_launch.called is launched
    assert "no-such-directory" in stderr


def test_given_a_warn_override_on_a_fail_default_when_the_extension_fails_then_the_launch_continues(tmp_path, projects):
    _script(projects / "myextensions", "fail.py", FAIL)
    config = _config(
        projects,
        {"greeting": {"path": "myextensions", "command": [sys.executable, "fail.py"], "on_error": "warn"}},
        on_error="fail",
    )

    code, _stderr, session_launch = _launch(config)

    assert code == 0
    session_launch.assert_called_once()


def test_given_a_fail_override_on_a_warn_default_when_the_extension_fails_then_the_launch_is_refused(
    tmp_path, projects
):
    _script(projects / "myextensions", "fail.py", FAIL)
    config = _config(
        projects,
        {"greeting": {"path": "myextensions", "command": [sys.executable, "fail.py"], "on_error": "fail"}},
        on_error="warn",
    )

    code, _stderr, session_launch = _launch(config)

    assert code != 0
    session_launch.assert_not_called()


def test_given_an_extension_that_outlives_its_timeout_when_launching_then_it_is_an_error_under_the_policy(
    tmp_path, projects
):
    _script(projects / "myextensions", "slow.py", SLOW)
    config = _config(
        projects,
        {"greeting": {"path": "myextensions", "command": [sys.executable, "slow.py"], "timeout_seconds": 0.5}},
        on_error="fail",
    )

    code, stderr, session_launch = _launch(config)

    assert code != 0
    session_launch.assert_not_called()
    assert "timed out" in stderr


def test_given_an_invalid_policy_when_launching_then_the_launch_is_refused_and_names_the_key(tmp_path, projects):
    config = _config(
        projects,
        {"greeting": {"path": "myextensions", "command": [sys.executable, "greet.py"]}},
        on_error="sometimes",
    )

    code, stderr, session_launch = _launch(config)

    assert code != 0
    session_launch.assert_not_called()
    assert "extensions.on_error" in stderr


def test_given_two_extensions_when_launching_then_each_attempt_lands_in_the_timestamped_launch_log(tmp_path, projects):
    _script(projects / "myextensions", "greet.py", GREET)
    marker = tmp_path / "marker.txt"
    config = _config(
        projects,
        {
            "greeting": {"path": "myextensions", "command": [sys.executable, "greet.py", str(marker)]},
            "second-greeting": {"path": "myextensions", "command": [sys.executable, "greet.py", str(marker)]},
        },
    )

    code, _stderr, session_launch = _launch(config)

    assert code == 0
    session_launch.assert_called_once()
    (log,) = _launch_logs(tmp_path)
    assert re.fullmatch(r"session-1-\d{8}T\d{6}\.\d{6}Z-\d+(-\d+)?\.log", log.name)
    text = log.read_text(encoding="utf-8")
    for name in ("greeting", "second-greeting"):
        assert re.search(rf"{name}: running .*greet\.py", text), text
        assert re.search(rf"{name}: exited with status 0 in \d+\.\d+s", text), text
    assert "hello from the greeting extension" in text
    assert text.index("greeting: running") < text.index("second-greeting: running")
