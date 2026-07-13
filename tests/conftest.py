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
def _isolate_quota_state(request, tmp_path_factory):
    """Hermetic quota/statusline tests — never touch real user quota state (AI-CLI-97).

    Two independent breaches this closes:

    1. **Real quota DB.** ``_get_quota_db_path()`` falls back to the real
       ``~/.local/state/ai-cli/quota.db`` when no override is set, so an unisolated
       test reads/writes real quota history (which carries live ``Fable`` model data).
       Redirect it to a per-test tmp file.
    2. **Real background scrape subprocess.** ``quota_statusline_part()`` fires
       ``_launch_background_scrape`` / ``_maybe_trigger_background_scrape``, which
       ``subprocess.Popen(["ai","quota","scrape"], start_new_session=True)`` — a real
       detached process that scrapes live usage and writes the real DB + NATS KV.
       Under xdist these race across workers and inject real ``Fable`` data into a
       test expecting isolated state (the intermittent ``F 🤖`` vs ``S 🤖`` flake).
       No-op both spawners.

    Tests that exercise the scrape spawners themselves (they mock ``subprocess.Popen``
    locally and assert the real functions' behavior) opt out of the no-op via the
    ``real_quota_scrape`` marker. ``set_db_path`` tests likewise re-set the path.
    """
    import ai_cli.quota_db as _qdb

    tmp_db = tmp_path_factory.mktemp("quota_state") / "quota.db"
    _qdb.set_db_path(tmp_db)
    # AIH-164 T-06: redirect the Fable backoff-state file to a per-test tmp path so tests never
    # read/write the real ~/.local/state/ai-cli/fable-scrape-backoff.json (which would leak
    # scrape-scheduling state across tests / into the real user state).
    import ai_cli.quota as _q

    _orig_fable_state = _q._FABLE_BACKOFF_STATE
    _q._FABLE_BACKOFF_STATE = tmp_path_factory.mktemp("fable_state") / "fable-scrape-backoff.json"
    try:
        if request.node.get_closest_marker("real_quota_scrape"):
            yield  # test drives the real scrape functions (with its own Popen mock)
        else:
            with (
                patch("ai_cli.quota._launch_background_scrape"),
                patch("ai_cli.quota._maybe_trigger_background_scrape"),
            ):
                yield
    finally:
        _qdb.set_db_path(None)
        _q._FABLE_BACKOFF_STATE = _orig_fable_state


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
