import io
import os
from unittest.mock import patch, MagicMock

import pytest

import ai_cli.config as _config_module


@pytest.fixture(autouse=True)
def _reset_registry_cache():
    """Reset the project registry cache before each test."""
    _config_module._registry_cache = None
    yield
    _config_module._registry_cache = None


@pytest.fixture(autouse=True)
def _suppress_auto_update():
    """Suppress _auto_update_if_stale for all tests.

    Without this, tests that call cli() trigger a real subprocess.run(["ai", ...])
    when the git HEAD doesn't match the last-update stamp file, causing
    FileNotFoundError in environments where 'ai' isn't on PATH (e.g. pre-push hook).
    """
    with patch("ai_cli.main._auto_update_if_stale"):
        yield


def _run_cli_with_args(argv, config_override=None):
    """Helper: invoke cli() with argv, capturing execvp calls.

    os.execvp replaces the process in real usage, so we raise SystemExit
    to simulate that — otherwise execution falls through to later exec calls.
    """
    config = config_override or {}
    with (
        patch("sys.argv", argv),
        patch("ai_cli.config.load_config", return_value=config),
        patch("os.execvp", side_effect=SystemExit(0)) as mock_exec,
        patch("ai_cli.main.trigger_background_update"),
        patch("ai_cli.main._auto_update_if_stale"),
    ):
        from ai_cli.main import cli

        try:
            cli()
        except SystemExit:
            pass
        return mock_exec


def run_cli(argv, config=None, env=None):
    """Invoke cli() with argv. Returns (exit_code, stdout, stderr)."""
    from ai_cli.main import cli

    _config = config or {}
    _env = env or {}
    with (
        patch("sys.argv", argv),
        patch("ai_cli.config.load_config", return_value=_config),
        patch("ai_cli.main.trigger_background_update"),
        patch("ai_cli.main._auto_update_if_stale"),
        patch.dict(os.environ, _env),
    ):
        stdout_cap = io.StringIO()
        stderr_cap = io.StringIO()
        try:
            with patch("sys.stdout", stdout_cap), patch("sys.stderr", stderr_cap):
                cli()
            exit_code = 0
        except SystemExit as e:
            exit_code = e.code if isinstance(e.code, int) else 0
        return exit_code, stdout_cap.getvalue(), stderr_cap.getvalue()


def make_subprocess_result(returncode=0, stdout="", stderr=""):
    """Factory for subprocess.run/check_output return value."""
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def make_iterm2_config(
    palette=None,
    enabled=True,
    color_enabled=True,
    collision_avoidance=True,
    projects=None,
    sessions=None,
    defaults=None,
):
    """Factory for iterm2 config dicts.

    ``projects``: dict mapping project_name → {tab_color, icon_color, ...}
    ``sessions``: dict mapping ai_name → {tab_color, icon_color, ...}
    ``defaults``: dict with default settings applied to all sessions
    """
    palette = palette or {"red": "#e74c3c", "blue": "#1e88e5", "green": "#2ecc71"}
    cfg = {
        "iterm2": {
            "enabled": enabled,
            "color": {"enabled": color_enabled, "collision_avoidance": collision_avoidance},
            "palette": palette,
        }
    }
    if defaults:
        cfg["iterm2"]["defaults"] = defaults
    if projects:
        cfg["iterm2"]["projects"] = projects
    if sessions:
        cfg["iterm2"]["sessions"] = sessions
    return cfg


def _make_list_panes_output(*entries):
    """Build mock tmux list-panes -a output.

    Each entry: (session_name, last_attached, pane_cmd) or
                (session_name, last_attached, pane_cmd, attached_count).
    attached_count defaults to 0 (no clients attached).
    """
    rows = []
    for entry in entries:
        if len(entry) == 3:
            name, last, cmd = entry
            attached = 0
        else:
            name, last, cmd, attached = entry
        rows.append(f"{name}|{last}|{attached}|{cmd}")
    lines = "\n".join(rows)
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = lines
    return mock
