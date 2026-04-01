import asyncio
import builtins
import json
import pytest
import sys
import time
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
import os
import ai_cli.main as _main_module
from ai_cli.main import (
    build_session_name,
    _claim_handoff_for_signal,
    check_handoff,
    claim_handoff,
    cleanup_stale_sessions,
    cleanup_worktree,
    cli,
    complete_handoff,
    create_worktree,
    detect_repo_root,
    _emit_iterm2_profile_setup,
    _find_project_dir,
    find_next_index,
    find_recent_session,
    get_current_project_name,
    get_engine_script,
    get_latest_gemini_session_id,
    get_project_aliases,
    get_project_prefix,
    get_session_map,
    get_session_map_path,
    get_xdg_cache_home,
    get_xdg_state_home,
    _get_handoff_queue_dir,
    _get_main_project_dir,
    _get_main_project_name,
    _get_project_prefix_by_name,
    _get_project_registry_path,
    _get_projects_dir,
    _iterm2_register_session,
    _iterm2_compute_tab_title,
    _iterm2_heuristic_window_title,
    _iterm2_state_dir,
    _iterm2_update_window_title,
    _find_best_handoff,
    _log_handoff_event,
    check_handoff_project,
    load_config,
    load_project_registry,
    post_handoff,
    resolve_session,
    save_session_map,
    trigger_background_update,
    validate_registry_completeness,
    _ensure_circusd,
    _cmd_signal_watch_start,
    _cmd_signal_watch_stop,
    _cmd_signal_watch_status,
    _cmd_tunnel_start,
    _cmd_tunnel_stop,
    _cmd_tunnel_status,
)


@pytest.fixture(autouse=True)
def _reset_registry_cache():
    """Reset the project registry cache before each test."""
    _main_module._registry_cache = None
    yield
    _main_module._registry_cache = None


# --- _find_project_dir tests ---


def test_find_project_dir_when_lowercase_projects_exists_then_returns_it():
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        expected = home / "projects" / "myapp"
        expected.mkdir(parents=True)
        assert _find_project_dir("myapp", _home=home) == expected


@pytest.mark.skipif(
    sys.platform == "darwin", reason="macOS filesystem is case-insensitive; Projects/ and projects/ resolve identically"
)
def test_find_project_dir_when_only_uppercase_Projects_exists_then_returns_lowercase():
    """Function always returns lowercase projects/ path regardless of what exists on disk."""
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        (home / "Projects" / "myapp").mkdir(parents=True)
        assert _find_project_dir("myapp", _home=home) == home / "projects" / "myapp"


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS filesystem is case-insensitive")
def test_find_project_dir_when_lowercase_takes_priority_over_uppercase():
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        (home / "projects" / "myapp").mkdir(parents=True)
        (home / "Projects" / "myapp").mkdir(parents=True)
        assert _find_project_dir("myapp", _home=home) == home / "projects" / "myapp"


def test_find_project_dir_when_not_found_then_returns_lowercase_default():
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        result = _find_project_dir("nonexistent", _home=home)
        assert result == home / "projects" / "nonexistent"


# --- get_current_project_name tests ---


def test_get_current_project_name_when_in_normal_dir_then_returns_dir_name():
    with patch("pathlib.Path.cwd", return_value=Path("/home/sergei/projects/aurion")):
        assert get_current_project_name() == "aurion"


def test_get_current_project_name_when_in_worktree_then_returns_project_name():
    with patch("pathlib.Path.cwd", return_value=Path("/home/sergei/projects/sergei/.worktrees/sw-2")):
        assert get_current_project_name() == "sergei"


def test_get_current_project_name_when_worktree_nested_then_returns_project_name():
    with patch("pathlib.Path.cwd", return_value=Path("/Users/bob/Projects/myapp/.worktrees/feature-1")):
        assert get_current_project_name() == "myapp"


# --- build_session_name tests ---
# Session name format: {c|g}[-r]-{project}-{index}
# ai_name format: {project}-{index}


def test_build_session_name_no_name_when_no_sessions_then_uses_index_1():
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res

        session_id, ai_name = build_session_name("c", "sw", "")

        assert session_id == "c-sw-1"
        assert ai_name == "sw-1"


def test_build_session_name_gemini_when_no_name_then_uses_g_prefix():
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res

        session_id, ai_name = build_session_name("g", "sw", "")

        assert session_id == "g-sw-1"
        assert ai_name == "sw-1"


def test_build_session_name_with_short_prefix_when_called_then_strips_prefix():
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res

        session_id, ai_name = build_session_name("c", "sw", "sw-planning")

        assert session_id == "c-sw-planning-1"
        assert ai_name == "sw-planning-1"


def test_build_session_name_with_old_full_prefix_when_called_then_strips_prefix():
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res

        session_id, ai_name = build_session_name("c", "sw", "claude-sw-planning")

        assert session_id == "c-sw-planning-1"
        assert ai_name == "sw-planning-1"


def test_build_session_name_with_new_full_name_and_index_when_called_then_strips_all():
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res

        session_id, ai_name = build_session_name("c", "sw", "c-sw-1")

        assert session_id == "c-sw-1"
        assert ai_name == "sw-1"


def test_build_session_name_with_name_when_no_sessions_then_uses_name_index_1():
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res

        session_id, ai_name = build_session_name("c", "sw", "planning")

        assert session_id == "c-sw-planning-1"
        assert ai_name == "sw-planning-1"


def test_build_session_name_with_double_hyphens_when_called_then_cleans_up():
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res

        session_id, ai_name = build_session_name("c", "sw", "research--test")

        assert session_id == "c-sw-research-test-1"
        assert ai_name == "sw-research-test-1"
        assert "--" not in session_id


def test_build_session_name_with_index_when_called_then_respects_index():
    session_id, ai_name = build_session_name("c", "sw", "3")
    assert session_id == "c-sw-3"
    assert ai_name == "sw-3"


def test_build_session_name_never_produces_double_hyphen():
    """Final assembled session name must never contain --."""
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res

        for name in ["", "1", "planning", "-R", "-R-1", "research--test", "sw-1"]:
            session_id, _ = build_session_name("c", "sw", name)
            assert "--" not in session_id, f"Double hyphen in session_id={session_id!r} for name={name!r}"


def test_build_session_name_is_remote_when_true_then_inserts_r_segment():
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res

        session_id, ai_name = build_session_name("c", "sw", "1", is_remote=True)

        assert session_id == "c-r-sw-1"
        assert ai_name == "sw-1"  # ai_name does not include remote tag


def test_build_session_name_is_remote_when_false_then_no_r_segment():
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res

        session_id, ai_name = build_session_name("c", "sw", "1", is_remote=False)

        assert session_id == "c-sw-1"
        assert ai_name == "sw-1"


def test_build_session_name_is_remote_no_name_then_finds_next_index():
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_run.return_value = mock_res

        session_id, ai_name = build_session_name("c", "sw", "", is_remote=True)

        assert session_id == "c-r-sw-1"
        assert ai_name == "sw-1"


# --- --remote flag tests ---


def _run_cli_with_args(argv, config_override=None):
    """Helper: invoke cli() with argv, capturing execvp calls.

    os.execvp replaces the process in real usage, so we raise SystemExit
    to simulate that — otherwise execution falls through to later exec calls.
    """
    config = config_override or {}
    with (
        patch("sys.argv", argv),
        patch("ai_cli.main.load_config", return_value=config),
        patch("os.execvp", side_effect=SystemExit(0)) as mock_exec,
        patch("ai_cli.main.trigger_background_update"),
    ):
        from ai_cli.main import cli

        try:
            cli()
        except SystemExit:
            pass
        return mock_exec


def test_remote_flag_when_host_configured_then_sshs_to_host():
    config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "port": 22, "identity_file": "", "transport": "ssh"}}
    mock_exec = _run_cli_with_args(["ai", "c", "1", "--remote"], config)
    mock_exec.assert_called_once()
    cmd, args = mock_exec.call_args[0]
    assert cmd == "ssh"
    assert "ubuntu@1.2.3.4" in args
    assert "-t" in args
    assert any("--is-remote" in a and "1" in a for a in args)


def test_remote_flag_when_host_configured_then_passes_is_remote_flag():
    config = {"remote": {"host": "hetzner-dev", "user": "ubuntu", "port": 22, "identity_file": ""}}
    mock_exec = _run_cli_with_args(["ai", "g", "research", "--remote"], config)
    mock_exec.assert_called_once()
    _, args = mock_exec.call_args[0]
    assert "ubuntu@hetzner-dev" in args
    assert any("ai g --is-remote" in a and "research" in a for a in args)


def test_remote_flag_when_called_then_passes_project_prefix_to_server():
    """Server receives --project-prefix so session uses local project tag, not remote cwd."""
    config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "port": 22, "identity_file": ""}}
    with (
        patch("sys.argv", ["ai", "c", "1", "--remote"]),
        patch("ai_cli.main.load_config", return_value=config),
        patch("ai_cli.main.get_project_prefix", return_value="sw"),
        patch("os.execvp", side_effect=SystemExit(0)) as mock_exec,
        patch("ai_cli.main.trigger_background_update"),
    ):
        from ai_cli.main import cli

        try:
            cli()
        except SystemExit:
            pass
    _, args = mock_exec.call_args[0]
    assert any("--project-prefix sw" in a for a in args)


def test_remote_flag_with_resume_when_called_then_forwards_resume_to_server():
    config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "port": 22, "identity_file": ""}}
    mock_exec = _run_cli_with_args(["ai", "c", "-r", "1", "--remote"], config)
    mock_exec.assert_called_once()
    _, args = mock_exec.call_args[0]
    assert any("--resume" in a for a in args)
    assert any("--is-remote" in a and "--resume" in a and "1" in a for a in args)


def test_remote_flag_without_resume_when_called_then_no_resume_in_cmd():
    config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "port": 22, "identity_file": ""}}
    mock_exec = _run_cli_with_args(["ai", "c", "1", "--remote"], config)
    _, args = mock_exec.call_args[0]
    assert not any("--resume" in a for a in args)


def test_remote_flag_when_identity_file_set_then_passes_i_flag():
    config = {
        "remote": {
            "host": "1.2.3.4",
            "user": "ubuntu",
            "port": 22,
            "identity_file": "~/.ssh/id_ed25519",
            "transport": "ssh",
        }
    }
    mock_exec = _run_cli_with_args(["ai", "c", "--remote"], config)
    mock_exec.assert_called_once()
    _, args = mock_exec.call_args[0]
    assert "-i" in args


def test_remote_flag_when_host_not_configured_then_exits_with_error():
    config = {"remote": {"host": "", "user": "ubuntu", "port": 22, "identity_file": ""}}
    with (
        patch("sys.argv", ["ai", "c", "--remote"]),
        patch("ai_cli.main.load_config", return_value=config),
        patch("ai_cli.main.trigger_background_update"),
        patch("sys.stderr"),
    ):
        from ai_cli.main import cli

        with pytest.raises(SystemExit) as exc_info:
            cli()
        assert exc_info.value.code == 1


# --- cleanup_stale_sessions tests ---


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


def _cleanup(config, panes_output, now=None):
    """Run cleanup_stale_sessions with mocked tmux and time."""
    now = now or int(time.time())
    kill_calls = []

    def fake_run(cmd, **kwargs):
        if "list-panes" in cmd:
            return panes_output
        if "kill-session" in cmd:
            kill_calls.append(cmd[cmd.index("-t") + 1])
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_run), patch("ai_cli.main.time") as mock_time:
        mock_time.time.return_value = now
        cleanup_stale_sessions(config)
    return kill_calls


def test_cleanup_when_pane_is_shell_then_kills_session():
    now = int(time.time())
    panes = _make_list_panes_output(("c-sw-1", now - 61, "bash"))
    killed = _cleanup({}, panes, now)
    assert "c-sw-1" in killed


def test_cleanup_when_pane_is_claude_and_recent_then_preserves_session():
    now = int(time.time())
    panes = _make_list_panes_output(("c-sw-1", now - 60, "claude"))
    killed = _cleanup({}, panes, now)
    assert "c-sw-1" not in killed


def test_cleanup_when_claude_abandoned_beyond_timeout_then_kills_session():
    now = int(time.time())
    timeout_seconds = 15 * 60
    panes = _make_list_panes_output(("c-sw-2", now - timeout_seconds - 1, "claude"))
    killed = _cleanup({}, panes, now)
    assert "c-sw-2" in killed


def test_cleanup_when_claude_within_timeout_then_preserves_session():
    now = int(time.time())
    timeout_seconds = 15 * 60
    panes = _make_list_panes_output(("c-sw-2", now - timeout_seconds + 60, "claude"))
    killed = _cleanup({}, panes, now)
    assert "c-sw-2" not in killed


def test_cleanup_when_non_ai_session_then_ignores_it():
    now = int(time.time())
    panes = _make_list_panes_output(("my-server", now - 9999, "bash"))
    killed = _cleanup({}, panes, now)
    assert killed == []


def test_cleanup_when_gemini_session_abandoned_then_kills_it():
    now = int(time.time())
    timeout_seconds = 15 * 60
    panes = _make_list_panes_output(("g-sw-1", now - timeout_seconds - 1, "gemini"))
    killed = _cleanup({}, panes, now)
    assert "g-sw-1" in killed


def test_cleanup_when_remote_session_abandoned_then_kills_it():
    now = int(time.time())
    timeout_seconds = 15 * 60
    panes = _make_list_panes_output(("c-r-sw-1", now - timeout_seconds - 1, "claude"))
    killed = _cleanup({}, panes, now)
    assert "c-r-sw-1" in killed


def test_cleanup_when_custom_timeout_configured_then_uses_it():
    now = int(time.time())
    config = {"session": {"stale_session_timeout": 5}}  # 5 minutes
    timeout_seconds = 5 * 60
    panes = _make_list_panes_output(("c-sw-1", now - timeout_seconds - 1, "claude"))
    killed = _cleanup(config, panes, now)
    assert "c-sw-1" in killed


def test_cleanup_when_no_tmux_then_does_nothing():
    mock = MagicMock()
    mock.returncode = 1
    mock.stdout = ""
    killed = _cleanup({}, mock)
    assert killed == []


def test_cleanup_when_session_currently_attached_then_never_kills_it():
    """Session with clients attached must never be killed — this was the root bug."""
    now = int(time.time())
    # Session has been running for 2 hours (well beyond timeout) but is currently attached
    panes = _make_list_panes_output(("c-sw-1", now - 7200, "claude", 1))
    killed = _cleanup({}, panes, now)
    assert "c-sw-1" not in killed


def test_cleanup_when_session_detached_and_abandoned_then_kills_it():
    now = int(time.time())
    timeout_seconds = 15 * 60
    # Detached (attached=0) and past timeout
    panes = _make_list_panes_output(("c-sw-2", now - timeout_seconds - 1, "claude", 0))
    killed = _cleanup({}, panes, now)
    assert "c-sw-2" in killed


def test_cleanup_when_old_format_session_then_ignores_it():
    """Old claude-sw-1 format sessions are not matched by new regex — not killed."""
    now = int(time.time())
    panes = _make_list_panes_output(("claude-sw-1", now - 9999, "bash"))
    killed = _cleanup({}, panes, now)
    assert killed == []


# --- XDG helpers ---


class TestXdgHelpers:
    def test_get_xdg_state_home_when_env_var_set_then_uses_it(self, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", "/custom/state")
        result = get_xdg_state_home()
        assert str(result) == "/custom/state/ai-cli-utils"

    def test_get_xdg_state_home_when_no_env_var_then_uses_default(self, monkeypatch):
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        result = get_xdg_state_home()
        assert result.name == "ai-cli-utils"
        assert ".local/state" in str(result)

    def test_get_xdg_cache_home_when_env_var_set_then_uses_it(self, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", "/custom/cache")
        result = get_xdg_cache_home()
        assert str(result) == "/custom/cache/ai-cli-utils"

    def test_get_xdg_cache_home_when_no_env_var_then_uses_default(self, monkeypatch):
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        result = get_xdg_cache_home()
        assert result.name == "ai-cli-utils"
        assert ".cache" in str(result)


# --- load_config tests ---


class TestLoadConfig:
    def test_load_config_when_no_config_file_then_creates_default_with_known_keys(self, tmp_path):
        config_dir = tmp_path / "ai-cli-utils"
        with patch("ai_cli.main.get_xdg_config_home", return_value=config_dir):
            result = load_config()
        assert (config_dir / "config.toml").exists()
        # Default config has [behavior], [worktree], [session], [messaging] sections
        assert "behavior" in result
        assert result["behavior"]["notify_on_exit"] is True
        assert "worktree" in result
        assert result["worktree"]["enabled"] is True
        assert "messaging" in result

    def test_load_config_when_bad_toml_then_returns_empty(self, tmp_path):
        config_dir = tmp_path / "ai-cli-utils"
        config_dir.mkdir(parents=True)
        (config_dir / "config.toml").write_text("not valid toml [[[")
        with patch("ai_cli.main.get_xdg_config_home", return_value=config_dir):
            result = load_config()
        assert result == {}


# --- Session map tests ---


class TestSessionMap:
    def test_get_session_map_path_when_gemini_then_returns_gemini_path(self, tmp_path):
        with patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path):
            result = get_session_map_path(engine="g")
        assert "gemini_sessions.json" in str(result)

    def test_get_session_map_when_invalid_json_then_returns_empty(self, tmp_path):
        with patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path):
            path = tmp_path / "gemini_sessions.json"
            path.write_text("not json {{{")
            with patch("ai_cli.main.get_session_map_path", return_value=path):
                result = get_session_map(engine="g")
        assert result == {}

    def test_save_session_map_when_called_then_writes_json(self, tmp_path):
        path = tmp_path / "test_sessions.json"
        with patch("ai_cli.main.get_session_map_path", return_value=path):
            save_session_map({"sw-1": "uuid123"}, engine="c")
        assert json.loads(path.read_text()) == {"sw-1": "uuid123"}


# --- Project helpers ---


class TestProjectHelpers:
    def test_get_projects_dir_when_custom_configured_then_uses_it(self):
        with patch("ai_cli.main.load_config", return_value={"project": {"projects_dir": "/tmp/custom"}}):
            result = _get_projects_dir()
        assert result == Path("/tmp/custom")

    def test_find_project_dir_when_no_home_arg_then_uses_projects_dir(self):
        with patch("ai_cli.main._get_projects_dir", return_value=Path("/srv/projects")):
            result = _find_project_dir("foo")
        assert result == Path("/srv/projects/foo")

    def test_get_main_project_name_when_exception_then_returns_none(self):
        with patch("ai_cli.main.load_config", side_effect=RuntimeError("broken")):
            result = _get_main_project_name()
        assert result is None

    def test_get_main_project_dir_when_name_configured_then_returns_path(self):
        with patch("ai_cli.main._get_main_project_name", return_value="sergei"):
            with patch("ai_cli.main._find_project_dir", return_value=Path("/home/u/projects/sergei")):
                result = _get_main_project_dir()
        assert result == Path("/home/u/projects/sergei")

    def test_get_project_registry_path_when_toml_exists_then_returns_it(self, tmp_path):
        toml_file = tmp_path / "sergei.toml"
        toml_file.write_text("[projects]\n")
        with patch("ai_cli.main._get_main_project_dir", return_value=tmp_path):
            with patch("ai_cli.main._get_main_project_name", return_value="sergei"):
                result = _get_project_registry_path()
        assert result == toml_file

    def test_get_handoff_queue_dir_when_main_dir_exists_then_returns_path(self):
        with patch("ai_cli.main._get_main_project_dir", return_value=Path("/home/u/projects/sergei")):
            result = _get_handoff_queue_dir()
        assert result == Path("/home/u/projects/sergei/.handoff-queue")

    def test_get_project_prefix_by_name_when_found_then_returns_prefix(self, tmp_path):
        toml_file = tmp_path / "sergei.toml"
        toml_content = b'[[projects]]\nname = "myapp"\ntask_prefix = "MA"\n'
        toml_file.write_bytes(toml_content)
        with patch("ai_cli.main._get_project_registry_path", return_value=toml_file):
            result = _get_project_prefix_by_name("myapp")
        assert result == "ma"

    def test_get_project_prefix_by_name_when_not_found_then_truncates(self):
        with patch("ai_cli.main._get_project_registry_path", return_value=None):
            result = _get_project_prefix_by_name("myproject")
        assert result == "myp"

    def test_get_project_aliases_when_registry_exists_then_builds_map(self, tmp_path):
        toml_file = tmp_path / "sergei.toml"
        toml_content = (
            b'[[projects]]\nname = "sergei"\ntask_prefix = "SW"\n\n[[projects]]\nname = "ai-dojo"\ntask_prefix = "AD"\n'
        )
        toml_file.write_bytes(toml_content)
        with patch("ai_cli.main._get_project_registry_path", return_value=toml_file):
            result = get_project_aliases()
        assert result["sw"] == "sergei"
        assert result["ad"] == "ai-dojo"

    def test_get_project_aliases_when_no_registry_then_empty(self):
        with patch("ai_cli.main._get_project_registry_path", return_value=None):
            result = get_project_aliases()
        assert result == {}


# --- get_latest_gemini_session_id ---


class TestGetLatestGeminiSessionId:
    def test_latest_gemini_id_when_logs_exist_then_returns_last(self, tmp_path):
        logs_dir = tmp_path / ".gemini" / "tmp" / "testproject"
        logs_dir.mkdir(parents=True)
        logs_file = logs_dir / "logs.json"
        logs_file.write_text('{"sessionId": "abc123"}\n{"sessionId": "def456"}\n')

        with patch("pathlib.Path.cwd", return_value=tmp_path / "testproject"):
            with patch("pathlib.Path.home", return_value=tmp_path):
                with patch("ai_cli.main._get_main_project_name", return_value=None):
                    result = get_latest_gemini_session_id()
        assert result == "def456"

    def test_latest_gemini_id_when_no_logs_then_returns_none(self, tmp_path):
        with patch("pathlib.Path.cwd", return_value=tmp_path / "noproject"):
            with patch("pathlib.Path.home", return_value=tmp_path):
                with patch("ai_cli.main._get_main_project_name", return_value=None):
                    result = get_latest_gemini_session_id()
        assert result is None


# --- resolve_session ---


class TestResolveSession:
    def test_resolve_session_when_name_and_session_exists_then_returns_it(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result):
            result = resolve_session("c-sw-", "3")
        assert result == "c-sw-3"

    def test_resolve_session_when_name_not_found_then_finds_recent(self):
        def mock_run(cmd, **kwargs):
            m = MagicMock()
            if "has-session" in cmd:
                m.returncode = 1
            elif "list-sessions" in cmd:
                m.returncode = 1
                m.stdout = ""
            else:
                m.returncode = 0
                m.stdout = ""
            return m

        with patch("subprocess.run", side_effect=mock_run):
            result = resolve_session("c-sw-", "99")
        assert result == ""


# --- detect_repo_root ---


class TestDetectRepoRoot:
    def test_detect_repo_root_when_in_repo_then_returns_path(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "/home/user/projects/myapp\n"
        with patch("subprocess.run", return_value=mock_result):
            result = detect_repo_root()
        assert result == Path("/home/user/projects/myapp")

    def test_detect_repo_root_when_not_in_repo_then_returns_none(self):
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            result = detect_repo_root()
        assert result is None


# --- create_worktree ---


class TestCreateWorktree:
    def test_create_worktree_when_no_repo_then_returns_none(self):
        with patch("ai_cli.main.detect_repo_root", return_value=None):
            result = create_worktree("sw-1")
        assert result is None

    def test_create_worktree_when_existing_valid_wt_then_returns_it(self, tmp_path):
        wt_dir = tmp_path / ".worktrees" / "sw-1"
        wt_dir.mkdir(parents=True)

        mock_prune = MagicMock(returncode=0)
        mock_list = MagicMock(returncode=0, stdout=str(wt_dir))

        def mock_run(cmd, **kwargs):
            if "prune" in cmd:
                return mock_prune
            if "list" in cmd:
                return mock_list
            return MagicMock(returncode=0)

        with patch("ai_cli.main.detect_repo_root", return_value=tmp_path):
            with patch("subprocess.run", side_effect=mock_run):
                result = create_worktree("sw-1")
        assert result == wt_dir

    def test_create_worktree_when_new_then_creates_and_returns(self, tmp_path):
        wt_dir = tmp_path / ".worktrees" / "sw-2"

        call_log = []

        def mock_run(cmd, **kwargs):
            call_log.append(cmd)
            m = MagicMock(returncode=0)
            m.stdout = ""
            if "worktree" in cmd and "add" in cmd:
                wt_dir.mkdir(parents=True, exist_ok=True)
            return m

        with patch("ai_cli.main.detect_repo_root", return_value=tmp_path):
            with patch("subprocess.run", side_effect=mock_run):
                result = create_worktree("sw-2")
        assert result == wt_dir


# --- Handoff subcommands ---


class TestHandoff:
    def test_post_handoff_when_called_then_creates_file(self, tmp_path):
        queue_dir = tmp_path / ".handoff-queue"
        with patch("ai_cli.main._get_handoff_queue_dir", return_value=queue_dir):
            post_handoff("Fix bug", "P1", "myapp", "Details here")
        pending_files = list((queue_dir / "pending").glob("*.md"))
        assert len(pending_files) == 1
        content = pending_files[0].read_text()
        assert "Fix bug" in content
        assert "Details here" in content

    def test_post_handoff_publishes_to_nats_with_correct_subject(self, tmp_path):
        queue_dir = tmp_path / ".handoff-queue"
        mock_client = MagicMock()
        mock_client.publish = AsyncMock(return_value=True)

        with patch("ai_cli.main._get_handoff_queue_dir", return_value=queue_dir):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.messaging.NATSClient", return_value=mock_client):
                    post_handoff("Deploy done", "P1", "artelier", "Details")

        mock_client.publish.assert_called_once()
        subject, payload = mock_client.publish.call_args[0]
        assert subject == "handoff.artelier"
        assert payload["title"] == "Deploy done"
        assert payload["project"] == "artelier"
        assert payload["priority"] == "P1"

    def test_post_handoff_when_nats_fails_then_file_still_written(self, tmp_path):
        queue_dir = tmp_path / ".handoff-queue"
        with patch("ai_cli.main._get_handoff_queue_dir", return_value=queue_dir):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.messaging.NATSClient", side_effect=Exception("NATS unavailable")):
                    post_handoff("Fix bug", "P1", "myapp", "Details")
        pending_files = list((queue_dir / "pending").glob("*.md"))
        assert len(pending_files) == 1

    def test_post_handoff_when_no_main_project_then_exits(self):
        with patch("ai_cli.main._get_handoff_queue_dir", return_value=None):
            with pytest.raises(SystemExit) as exc:
                post_handoff("title", "P1", "proj", "msg")
            assert exc.value.code == 1

    def test_post_handoff_writes_for_machine_from_env(self, tmp_path):
        queue_dir = tmp_path / ".handoff-queue"
        with patch("ai_cli.main._get_handoff_queue_dir", return_value=queue_dir):
            with patch.dict("os.environ", {"AI_CLI_HOST": "hetzner"}):
                post_handoff("Task", "P1", "proj", "msg")
        content = list((queue_dir / "pending").glob("*.md"))[0].read_text()
        assert "for_machine: hetzner" in content

    def test_post_handoff_writes_explicit_for_machine(self, tmp_path):
        queue_dir = tmp_path / ".handoff-queue"
        with patch("ai_cli.main._get_handoff_queue_dir", return_value=queue_dir):
            post_handoff("Task", "P1", "proj", "msg", for_machine="mac")
        content = list((queue_dir / "pending").glob("*.md"))[0].read_text()
        assert "for_machine: mac" in content

    def test_post_handoff_includes_for_machine_in_nats_payload(self, tmp_path):
        queue_dir = tmp_path / ".handoff-queue"
        mock_client = MagicMock()
        mock_client.publish = AsyncMock(return_value=True)
        with patch("ai_cli.main._get_handoff_queue_dir", return_value=queue_dir):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.messaging.NATSClient", return_value=mock_client):
                    post_handoff("Task", "P1", "proj", "msg", for_machine="mac")
        _, payload = mock_client.publish.call_args[0]
        assert payload["for_machine"] == "mac"

    def test_check_handoff_when_pending_items_then_prints_best(self, tmp_path, capsys):
        queue_dir = tmp_path / ".handoff-queue"
        pending = queue_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "001-task-a.md").write_text("---\npriority: 2\n---\nTask A")
        (pending / "002-task-b.md").write_text("---\npriority: 0\n---\nTask B")

        with patch("ai_cli.main._get_handoff_queue_dir", return_value=queue_dir):
            check_handoff()
        output = capsys.readouterr().out
        assert "002-task-b.md" in output

    def test_check_handoff_when_no_pending_then_silent(self, tmp_path, capsys):
        queue_dir = tmp_path / ".handoff-queue"
        with patch("ai_cli.main._get_handoff_queue_dir", return_value=queue_dir):
            check_handoff()
        assert capsys.readouterr().out == ""

    def test_claim_handoff_when_file_exists_then_moves_to_claimed(self, tmp_path):
        queue_dir = tmp_path / ".handoff-queue"
        pending = queue_dir / "pending"
        pending.mkdir(parents=True)
        src = pending / "001-task.md"
        src.write_text("---\nclaimed_by: null\nclaimed_at: null\n---\nContent")

        with patch("ai_cli.main._get_handoff_queue_dir", return_value=queue_dir):
            claim_handoff(str(src), claimer="c-sw-1")
        claimed_files = list((queue_dir / "claimed").glob("*.md"))
        assert len(claimed_files) == 1
        content = claimed_files[0].read_text()
        assert "c-sw-1" in content

    def test_claim_handoff_when_no_main_project_then_exits(self):
        with patch("ai_cli.main._get_handoff_queue_dir", return_value=None):
            with pytest.raises(SystemExit) as exc:
                claim_handoff("/tmp/nonexistent.md")
            assert exc.value.code == 1

    def test_complete_handoff_when_file_exists_then_moves_to_completed(self, tmp_path):
        queue_dir = tmp_path / ".handoff-queue"
        claimed = queue_dir / "claimed"
        claimed.mkdir(parents=True)
        src = claimed / "001-task.md"
        src.write_text("---\ntitle: task\n---\n")

        with patch("ai_cli.main._get_handoff_queue_dir", return_value=queue_dir):
            complete_handoff(str(src))
        completed = list((queue_dir / "completed").glob("*.md"))
        assert len(completed) == 1

    def test_complete_handoff_when_no_main_project_then_noop(self):
        with patch("ai_cli.main._get_handoff_queue_dir", return_value=None):
            complete_handoff("/tmp/nonexistent.md")  # Should not raise


# --- _claim_handoff_for_signal ---


class TestClaimHandoffForSignal:
    def _make_pending(self, handoff_dir, handoff_id, title, claimer=None):
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True, exist_ok=True)
        slug = title.lower().replace(" ", "-")
        f = pending / f"{handoff_id:03d}-{slug}.md"
        f.write_text(f'---\nid: "{handoff_id}"\ntitle: "{title}"\nclaimed_by: null\nclaimed_at: null\n---\n')
        return f

    def test_when_pending_file_exists_then_moves_to_claimed(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        self._make_pending(handoff_dir, 1, "fix-bug")

        result = _claim_handoff_for_signal(handoff_dir, 1, "c-sw-1")

        assert result is not None
        assert result.parent.name == "claimed"
        assert result.name == "001-fix-bug.md"
        assert not (handoff_dir / "pending" / "001-fix-bug.md").exists()

    def test_when_pending_file_exists_then_updates_claimed_by(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        self._make_pending(handoff_dir, 2, "deploy")

        result = _claim_handoff_for_signal(handoff_dir, 2, "c-sw-2")

        assert result is not None
        text = result.read_text()
        assert "claimed_by: c-sw-2" in text
        assert "claimed_at: null" not in text

    def test_when_no_pending_file_then_returns_none(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        (handoff_dir / "pending").mkdir(parents=True)

        result = _claim_handoff_for_signal(handoff_dir, 99, "c-sw-1")

        assert result is None

    def test_when_file_already_claimed_by_other_session_then_returns_none(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        f = self._make_pending(handoff_dir, 1, "task")
        # Simulate race: another session moved the file first
        claimed = handoff_dir / "claimed"
        claimed.mkdir(parents=True)
        f.rename(claimed / f.name)

        result = _claim_handoff_for_signal(handoff_dir, 1, "c-sw-1")

        assert result is None

    def test_when_rename_raises_oserror_then_returns_none(self, tmp_path):
        """Covers the concurrent-rename race: another session's mv wins, ours gets OSError."""
        handoff_dir = tmp_path / ".handoff-queue"
        self._make_pending(handoff_dir, 1, "task")

        with patch("pathlib.Path.rename", side_effect=OSError("busy")):
            result = _claim_handoff_for_signal(handoff_dir, 1, "c-sw-1")

        assert result is None


# --- ai internal signal-watch CLI dispatch ---


class TestSignalWatchCli:
    def test_signal_watch_when_nats_unavailable_then_exits_cleanly(self):
        from nats.errors import NoServersError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-1"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main._get_handoff_queue_dir", return_value=None),
            patch("ai_cli.main.get_xdg_state_home", return_value=Path("/tmp")),
            patch("nats.connect", new=AsyncMock(side_effect=NoServersError)),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

    def test_signal_watch_when_message_received_and_claim_succeeds_then_writes_pending_file(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "003-fix-login.md").write_text(
            '---\nid: "3"\ntitle: "fix login"\nclaimed_by: null\nclaimed_at: null\n---\n'
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js
        received_callback = {}

        async def fake_js_subscribe(subject, durable, cb):
            received_callback["cb"] = cb

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            if "cb" in received_callback:
                msg = MagicMock()
                msg.data = b'{"id": 3, "title": "fix login", "priority": "P1", "message": "do it"}'
                msg.ack = AsyncMock()
                await received_callback["cb"](msg)
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-1"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.main.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run"),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        pending_file = state_dir / "handoff-pending-c-sw-1"
        assert pending_file.exists()
        content = pending_file.read_text()
        assert "fix login" in content
        assert not (pending / "003-fix-login.md").exists()

    def test_signal_watch_when_claim_lost_to_other_session_then_no_pending_file(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        (handoff_dir / "pending").mkdir(parents=True)
        # pending dir is empty — another session already claimed it
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js
        received_callback = {}

        async def fake_js_subscribe(subject, durable, cb):
            received_callback["cb"] = cb

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            if "cb" in received_callback:
                msg = MagicMock()
                msg.data = b'{"id": 5, "title": "gone", "priority": "P2", "message": "already claimed"}'
                msg.ack = AsyncMock()
                await received_callback["cb"](msg)
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-2"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.main.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run"),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        assert not (state_dir / "handoff-pending-c-sw-2").exists()

    def test_signal_watch_when_too_few_args_then_exits_with_error(self):
        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "only-one-arg"]),
            patch("ai_cli.main.load_config", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 1

    def test_signal_watch_when_handoff_dir_none_and_message_received_then_no_pending_file(self, tmp_path):
        """Covers the early-return branch when handoff_dir is None but a message is delivered."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js
        received_callback = {}

        async def fake_js_subscribe(subject, durable, cb):
            received_callback["cb"] = cb

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            if "cb" in received_callback:
                msg = MagicMock()
                msg.data = b'{"id": 7, "title": "t", "priority": "P1", "message": "m"}'
                msg.ack = AsyncMock()
                await received_callback["cb"](msg)
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-3"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main._get_handoff_queue_dir", return_value=None),
            patch("ai_cli.main.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        assert not (state_dir / "handoff-pending-c-sw-3").exists()

    def test_signal_watch_when_pane_is_idle_then_sends_nudge(self, tmp_path):
        """Covers the nudge branch: tmux reports non-claude command, send-keys called.

        Uses cross-machine payload (no local file pre-created) so startup scan does not
        claim the task first — only the real-time NATS path fires, which should nudge.
        """
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        # No local file — task arrives via NATS payload only (cross-machine delivery)
        file_content = '---\nid: "4"\ntitle: "nudge task"\nclaimed_by: null\nclaimed_at: null\n---\n'
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js
        received_callback = {}

        async def fake_js_subscribe(subject, durable, cb):
            received_callback["cb"] = cb

        mock_js.subscribe = fake_js_subscribe
        send_keys_calls = []

        def fake_subprocess_run(cmd, **kwargs):
            result = MagicMock()
            if "display-message" in cmd:
                result.returncode = 0
                result.stdout = "bash\n"  # pane is idle (not claude)
            else:
                send_keys_calls.append(cmd)
                result.returncode = 0
            return result

        async def fake_sleep(_):
            if "cb" in received_callback:
                msg = MagicMock()
                msg.data = json.dumps(
                    {
                        "id": 4,
                        "title": "nudge task",
                        "priority": "P1",
                        "message": "do it",
                        "content": file_content,
                        "filename": "004-nudge-task.md",
                    }
                ).encode()
                msg.ack = AsyncMock()
                await received_callback["cb"](msg)
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-4"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.main.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run", side_effect=fake_subprocess_run),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        assert any("send-keys" in cmd for cmd in send_keys_calls)

    def test_signal_watch_when_cross_machine_payload_then_writes_file_and_claims(self, tmp_path):
        """File doesn't exist locally; content in NATS payload should be written before claiming."""
        handoff_dir = tmp_path / ".handoff-queue"
        (handoff_dir / "pending").mkdir(parents=True)
        # No file pre-created — simulates cross-machine delivery
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js
        received_callback = {}

        async def fake_js_subscribe(subject, durable, cb):
            received_callback["cb"] = cb

        mock_js.subscribe = fake_js_subscribe

        file_content = '---\nid: "7"\ntitle: "remote task"\nclaimed_by: null\nclaimed_at: null\n---\n\nDo this.\n'

        async def fake_sleep(_):
            if "cb" in received_callback:
                msg = MagicMock()
                msg.data = json.dumps(
                    {
                        "id": 7,
                        "title": "remote task",
                        "priority": "P1",
                        "message": "Do this.",
                        "content": file_content,
                        "filename": "007-remote-task.md",
                    }
                ).encode()
                msg.ack = AsyncMock()
                await received_callback["cb"](msg)
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-3"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.main.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run"),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        pending_file = state_dir / "handoff-pending-c-sw-3"
        assert pending_file.exists()
        assert "remote task" in pending_file.read_text()
        # Original file must not remain in pending (claimed)
        assert not (handoff_dir / "pending" / "007-remote-task.md").exists()
        assert (handoff_dir / "claimed" / "007-remote-task.md").exists()

    def test_signal_watch_startup_scan_picks_up_existing_pending_files(self, tmp_path):
        """Files already in pending queue at startup should be claimed without a NATS trigger."""
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "004-pre-existing-task.md").write_text(
            '---\nid: "4"\ntitle: "pre-existing task"\nclaimed_by: null\nclaimed_at: null\n---\n\nDo this.\n'
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js

        async def fake_js_subscribe(subject, durable, cb):
            pass

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-4"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.main.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run"),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        pending_file = state_dir / "handoff-pending-c-sw-4"
        assert pending_file.exists()
        assert not (pending / "004-pre-existing-task.md").exists()
        assert (handoff_dir / "claimed" / "004-pre-existing-task.md").exists()

    def test_signal_watch_startup_scan_does_not_send_keys(self, tmp_path):
        """Startup scan must never send-keys — only write the pending marker.

        Sending keys during startup fires before CC is ready and causes multiple
        text injections without submission. Only real-time NATS delivery nudges.
        """
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "005-startup-task.md").write_text(
            '---\nid: "5"\ntitle: "startup task"\nclaimed_by: null\nclaimed_at: null\n---\n\nDo this.\n'
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js

        async def fake_js_subscribe(subject, durable, cb):
            pass

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            raise asyncio.CancelledError

        send_keys_calls = []

        def fake_subprocess_run(cmd, **kwargs):
            if "send-keys" in cmd:
                send_keys_calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stdout = "bash\n"
            return result

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-5"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.main.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run", side_effect=fake_subprocess_run),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        # Pending marker written — but no send-keys
        assert (state_dir / "handoff-pending-c-sw-5").exists()
        assert send_keys_calls == [], "startup scan must not send-keys"

    def test_signal_watch_when_for_machine_matches_then_claims(self, tmp_path):
        """Handoff with for_machine matching AI_CLI_HOST should be claimed."""
        handoff_dir = tmp_path / ".handoff-queue"
        (handoff_dir / "pending").mkdir(parents=True)
        file_content = '---\nid: "8"\ntitle: "local task"\nclaimed_by: null\nclaimed_at: null\n---\n'
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js
        received_callback = {}

        async def fake_js_subscribe(subject, durable, cb):
            received_callback["cb"] = cb

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            if "cb" in received_callback:
                msg = MagicMock()
                msg.data = json.dumps(
                    {
                        "id": 8,
                        "title": "local task",
                        "priority": "P1",
                        "message": "do it",
                        "for_machine": "hetzner",
                        "content": file_content,
                        "filename": "008-local-task.md",
                    }
                ).encode()
                msg.ack = AsyncMock()
                await received_callback["cb"](msg)
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-6"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.main.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run"),
            patch.dict("os.environ", {"AI_CLI_HOST": "hetzner"}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        assert (state_dir / "handoff-pending-c-sw-6").exists()

    def test_signal_watch_when_for_machine_mismatch_then_skips(self, tmp_path):
        """Handoff with for_machine not matching AI_CLI_HOST must be ignored."""
        handoff_dir = tmp_path / ".handoff-queue"
        (handoff_dir / "pending").mkdir(parents=True)
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js
        received_callback = {}

        async def fake_js_subscribe(subject, durable, cb):
            received_callback["cb"] = cb

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            if "cb" in received_callback:
                msg = MagicMock()
                msg.data = json.dumps(
                    {
                        "id": 9,
                        "title": "mac only task",
                        "priority": "P1",
                        "message": "mac work",
                        "for_machine": "mac",
                    }
                ).encode()
                msg.ack = AsyncMock()
                await received_callback["cb"](msg)
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-7"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.main.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run"),
            patch.dict("os.environ", {"AI_CLI_HOST": "hetzner"}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        assert not (state_dir / "handoff-pending-c-sw-7").exists()

    def test_signal_watch_startup_scan_when_for_machine_mismatch_then_skips(self, tmp_path):
        """Startup scan must skip files whose for_machine doesn't match AI_CLI_HOST."""
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "010-mac-task.md").write_text(
            '---\nid: "10"\ntitle: "mac task"\nfor_machine: mac\nclaimed_by: null\nclaimed_at: null\n---\n\nMac only.\n'
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js

        async def fake_js_subscribe(subject, durable, cb):
            pass

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-8"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.main.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run"),
            patch.dict("os.environ", {"AI_CLI_HOST": "hetzner"}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        # File must remain in pending — not claimed, not written to state
        assert (pending / "010-mac-task.md").exists()
        assert not (state_dir / "handoff-pending-c-sw-8").exists()


# --- ai handoff post --remote ---


class TestHandoffPostRemote:
    def test_post_handoff_remote_flag_when_no_remote_config_then_exits_1(self):
        with (
            patch("sys.argv", ["ai", "handoff", "post", "--remote", "Fix bug", "P1", "myapp", "Details"]),
            patch("ai_cli.main.load_config", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
        assert exc.value.code == 1

    def test_post_handoff_remote_flag_when_remote_config_set_then_sshs_to_configured_host(self):
        config = {"remote": {"host": "9.9.9.9", "user": "alice"}}
        with (
            patch("sys.argv", ["ai", "handoff", "post", "--remote", "Task", "P2", "proj", "Msg"]),
            patch("ai_cli.main.load_config", return_value=config),
            patch("os.execvp", side_effect=SystemExit(0)) as mock_exec,
        ):
            with pytest.raises(SystemExit):
                cli()

        mock_exec.assert_called_once()
        cmd, args = mock_exec.call_args[0]
        assert cmd == "ssh"
        assert "alice@9.9.9.9" in args

    def test_post_handoff_without_remote_flag_writes_local_file(self, tmp_path):
        queue_dir = tmp_path / ".handoff-queue"
        with patch("ai_cli.main._get_handoff_queue_dir", return_value=queue_dir):
            post_handoff("Local task", "P1", "myapp", "Details")
        files = list((queue_dir / "pending").glob("*.md"))
        assert len(files) == 1

    def test_post_handoff_nats_payload_includes_content_and_filename(self, tmp_path):
        queue_dir = tmp_path / ".handoff-queue"
        published_payloads = []

        class FakeNATSClient:
            def __init__(self, servers=None):
                pass

            async def publish(self, subject, data):
                published_payloads.append((subject, data))
                return True

        import ai_cli.messaging as msg_mod

        with (
            patch("ai_cli.main._get_handoff_queue_dir", return_value=queue_dir),
            patch("ai_cli.main.load_config", return_value={}),
            patch.object(msg_mod, "NATSClient", FakeNATSClient),
        ):
            post_handoff("Cross-machine task", "P1", "myapp", "Do this remotely")

        assert len(published_payloads) == 1
        subject, payload = published_payloads[0]
        assert "content" in payload
        assert "filename" in payload
        assert "Cross-machine task" in payload["content"]
        assert payload["filename"].endswith(".md")


# --- ai internal get-version ---


class TestGetVersion:
    def test_get_version_when_package_installed_then_prints_version(self, capsys):
        with (
            patch("sys.argv", ["ai", "internal", "get-version"]),
            patch("ai_cli.main.load_config", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0
        out = capsys.readouterr().out.strip()
        assert out  # non-empty
        assert "." in out  # looks like a version string

    def test_get_version_when_package_missing_then_prints_unknown(self, capsys):
        from importlib.metadata import PackageNotFoundError

        with (
            patch("sys.argv", ["ai", "internal", "get-version"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("importlib.metadata.version", side_effect=PackageNotFoundError("ai-cli-utils")),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0
        out = capsys.readouterr().out.strip()
        assert out == "unknown"


# --- get_engine_script self-update ---


class TestDeploy:
    def test_deploy_bumps_post_version_and_installs(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "ai-cli-utils"\nversion = "0.1.1"\n')

        with (
            patch("sys.argv", ["ai", "deploy"]),
            patch("ai_cli.main.load_config", return_value={"deploy": {"project_path": str(tmp_path)}}),
            patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        # pyproject.toml restored to original after install
        assert pyproject.read_text() == '[project]\nname = "ai-cli-utils"\nversion = "0.1.1"\n'
        # uv tool install was called with --force (no --reinstall)
        all_cmds = [call[0][0] for call in mock_run.call_args_list]
        uv_cmd = next((c for c in all_cmds if "uv" in c and "--force" in c and "--reinstall" not in c), None)
        assert uv_cmd is not None

    def test_deploy_strips_existing_post_before_bumping(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "0.1.1.post20260101000000"\n')

        with (
            patch("sys.argv", ["ai", "deploy"]),
            patch("ai_cli.main.load_config", return_value={"deploy": {"project_path": str(tmp_path)}}),
            patch("subprocess.run", return_value=MagicMock(returncode=0)),
            patch("builtins.print") as mock_print,
        ):
            with pytest.raises(SystemExit):
                cli()

        output = " ".join(str(c) for c in mock_print.call_args_list)
        # New version should be 0.1.1.post<new_ts>, not 0.1.1.post20260101000000.post<ts>
        assert "0.1.1.post20260101000000.post" not in output

    def test_deploy_when_no_pyproject_then_exits_with_error(self, tmp_path):
        with (
            patch("sys.argv", ["ai", "deploy"]),
            patch("ai_cli.main.load_config", return_value={"deploy": {"project_path": str(tmp_path)}}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 1

    def test_deploy_uses_cwd_when_no_config(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "0.2.0"\n')

        with (
            patch("sys.argv", ["ai", "deploy"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main.Path.cwd", return_value=tmp_path),
            patch("subprocess.run", return_value=MagicMock(returncode=0)),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

    def test_deploy_on_mac_pulls_before_install(self, tmp_path):
        """On Mac, git pull --rebase must run before uv install."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "0.1.0"\n')
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0)

        with (
            patch("sys.argv", ["ai", "deploy"]),
            patch("ai_cli.main.load_config", return_value={"deploy": {"project_path": str(tmp_path)}}),
            patch("subprocess.run", side_effect=fake_run),
            patch.dict("os.environ", {"AI_CLI_HOST": "mac"}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        cmds = [" ".join(c) for c in calls]
        pull_idx = next((i for i, c in enumerate(cmds) if "git" in c and "pull" in c), None)
        uv_idx = next((i for i, c in enumerate(cmds) if "uv" in c and "tool" in c and "install" in c), None)
        assert pull_idx is not None, "git pull not called"
        assert uv_idx is not None, "uv install not called"
        assert pull_idx < uv_idx, "git pull must run before uv install"

    def test_deploy_installs_into_extra_venvs_when_configured(self, tmp_path):
        """Installs into any venv paths listed in [update] extra_venvs."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "0.1.0"\n')
        extra_venv = tmp_path / "my-tool" / ".venv"
        extra_venv.mkdir(parents=True)
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0)

        with (
            patch("sys.argv", ["ai", "update"]),
            patch(
                "ai_cli.main.load_config",
                return_value={
                    "deploy": {"project_path": str(tmp_path)},
                    "update": {"extra_venvs": [str(extra_venv)]},
                },
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        cmds = [" ".join(c) for c in calls]
        assert any("pip" in c and "install" in c for c in cmds), "extra venv install not called"

    def test_deploy_skips_extra_venvs_when_path_missing(self, tmp_path):
        """If an extra venv path does not exist, skip it silently."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "0.1.0"\n')
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0)

        with (
            patch("sys.argv", ["ai", "update"]),
            patch(
                "ai_cli.main.load_config",
                return_value={
                    "deploy": {"project_path": str(tmp_path)},
                    "update": {"extra_venvs": [str(tmp_path / "nonexistent" / ".venv")]},
                },
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        cmds = [" ".join(c) for c in calls]
        assert not any("pip" in c and "install" in c for c in cmds), "should not call pip for missing venv"


class TestGetEngineScriptSelfUpdate:
    def test_engine_script_embeds_template_version(self):
        script = get_engine_script("c", "c-sw-1", "c-sw-1", "c", "myapp")
        assert "_template_version=" in script

    def test_engine_script_contains_version_check_and_exec(self):
        script = get_engine_script("c", "c-sw-1", "c-sw-1", "c", "myapp")
        assert "ai internal get-version" in script
        assert "exec ai" in script
        assert "_current_ver" in script


# --- trigger_background_update ---


class TestTriggerBackgroundUpdate:
    def test_trigger_update_when_stale_then_runs_upgrade(self, tmp_path):
        state_file = tmp_path / "update_check.json"
        state_file.write_text(json.dumps({"last_checked": 0}))

        with patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path):
            with patch("subprocess.Popen") as mock_popen:
                trigger_background_update()
        mock_popen.assert_called_once()
        assert "uv" in mock_popen.call_args[0][0]

    def test_trigger_update_when_recent_then_skips(self, tmp_path):
        state_file = tmp_path / "update_check.json"
        state_file.write_text(json.dumps({"last_checked": time.time()}))

        with patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path):
            with patch("subprocess.Popen") as mock_popen:
                trigger_background_update()
        mock_popen.assert_not_called()

    def test_trigger_update_when_no_state_file_then_runs(self, tmp_path):
        with patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path):
            with patch("subprocess.Popen") as mock_popen:
                trigger_background_update()
        mock_popen.assert_called_once()


# --- CLI dispatch tests ---


class TestCliDispatch:
    def test_cli_when_internal_get_latest_gemini_id_then_calls_function(self):
        with patch("sys.argv", ["ai", "internal", "get-latest-gemini-id"]):
            with patch("ai_cli.main.get_latest_gemini_session_id", return_value="abc123"):
                with patch("ai_cli.main.load_config", return_value={}):
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0

    def test_cli_when_internal_update_session_map_then_updates(self, tmp_path):
        with patch("sys.argv", ["ai", "internal", "update-session-map", "g", "sw-1", "uuid123"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.get_session_map", return_value={}):
                    with patch("ai_cli.main.save_session_map") as mock_save:
                        with pytest.raises(SystemExit) as exc:
                            cli()
                        assert exc.value.code == 0
                        mock_save.assert_called_once()

    def test_cli_when_internal_cleanup_worktree_then_calls_function(self):
        with patch("sys.argv", ["ai", "internal", "cleanup-worktree", "sw-1"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.cleanup_worktree") as mock_cleanup:
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0
                    mock_cleanup.assert_called_once_with("sw-1")

    def test_cli_when_internal_notify_then_calls_notification_manager(self):
        with patch("sys.argv", ["ai", "internal", "notify", "session1", "hello"]):
            with patch("ai_cli.main.load_config", return_value={}):
                mock_mgr = MagicMock()
                with patch("ai_cli.notifications.NotificationManager", return_value=mock_mgr):
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0

    def test_cli_when_handoff_check_then_calls_check_handoff(self):
        with patch("sys.argv", ["ai", "handoff", "check"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.check_handoff") as mock_check:
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0
                    mock_check.assert_called_once()

    def test_cli_when_memory_bad_args_then_exits_1(self):
        with patch("sys.argv", ["ai", "memory"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_quota_bad_args_then_exits_1(self):
        with patch("sys.argv", ["ai", "quota"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_gemini_then_calls_gemini_cli(self):
        with patch("sys.argv", ["ai", "gemini", "hello", "-m", "flash"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.gemini.gemini_cli", side_effect=SystemExit(0)) as mock_gemini:
                    with pytest.raises(SystemExit):
                        cli()
                    mock_gemini.assert_called_once()

    def test_cli_when_sync_push_then_calls_sync_push(self):
        with patch("sys.argv", ["ai", "sync", "push"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.sync.sync_push", return_value=0) as mock_push:
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0
                    mock_push.assert_called_once()

    def test_cli_when_sync_no_args_then_exits_1(self):
        with patch("sys.argv", ["ai", "sync"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_upgrade_then_calls_execvp(self):
        with patch("sys.argv", ["ai", "upgrade"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("os.execvp", side_effect=SystemExit(0)) as mock_exec:
                    with pytest.raises(SystemExit):
                        cli()
                    mock_exec.assert_called_once_with("uv", ["uv", "tool", "upgrade", "ai-cli-utils"])

    def test_cli_when_internal_no_action_then_exits_1(self):
        with patch("sys.argv", ["ai", "internal"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_telemetry_bad_args_then_exits_1(self):
        with patch("sys.argv", ["ai", "telemetry"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_internal_update_session_map_missing_args_then_exits_1(self):
        with patch("sys.argv", ["ai", "internal", "update-session-map"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_internal_cleanup_worktree_missing_args_then_exits_1(self):
        with patch("sys.argv", ["ai", "internal", "cleanup-worktree"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_internal_notify_missing_args_then_exits_1(self):
        with patch("sys.argv", ["ai", "internal", "notify"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_internal_publish_event_then_instantiates_nats_client(self):
        with patch("sys.argv", ["ai", "internal", "publish-event", "sess1", "START"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.messaging.NATSClient") as mock_nats:
                    mock_nats.return_value = MagicMock()
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0
                    mock_nats.assert_called_once()

    def test_cli_when_internal_publish_heartbeat_then_instantiates_nats_client(self):
        with patch("sys.argv", ["ai", "internal", "publish-heartbeat", "sess1", '{"cpu": 50}']):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.messaging.NATSClient") as mock_nats:
                    mock_nats.return_value = MagicMock()
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0
                    mock_nats.assert_called_once()

    def test_cli_when_internal_publish_heartbeat_bad_json_then_exits_1(self):
        with patch("sys.argv", ["ai", "internal", "publish-heartbeat", "sess1", "not-json"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_internal_publish_session_event_then_instantiates_nats_client(self):
        with patch("sys.argv", ["ai", "internal", "publish-session-event", "sess1", "started"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.messaging.NATSClient") as mock_nats:
                    mock_nats.return_value = MagicMock()
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0
                    mock_nats.assert_called_once()

    def test_cli_when_internal_publish_then_instantiates_nats_with_subject(self):
        with patch("sys.argv", ["ai", "internal", "publish", "test.topic", '{"key": "val"}']):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.messaging.NATSClient") as mock_nats:
                    mock_nats.return_value = MagicMock()
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0
                    mock_nats.assert_called_once()

    def test_cli_when_handoff_post_then_calls_post_handoff(self):
        with patch("sys.argv", ["ai", "handoff", "post", "title", "P1", "proj", "msg"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.post_handoff") as mock_post:
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0
                    mock_post.assert_called_once_with("title", "P1", "proj", "msg", for_machine=None)

    def test_cli_when_handoff_claim_then_calls_claim_handoff(self):
        with patch("sys.argv", ["ai", "handoff", "claim", "/tmp/file.md"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.claim_handoff") as mock_claim:
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0
                    mock_claim.assert_called_once_with("/tmp/file.md")

    def test_cli_when_handoff_complete_then_calls_complete_handoff(self):
        with patch("sys.argv", ["ai", "handoff", "complete", "/tmp/file.md"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.complete_handoff") as mock_complete:
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0
                    mock_complete.assert_called_once_with("/tmp/file.md")

    def test_cli_when_handoff_no_subcommand_then_exits_1(self):
        with patch("sys.argv", ["ai", "handoff"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_sync_pull_then_calls_sync_pull(self):
        with patch("sys.argv", ["ai", "sync", "pull"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.sync.sync_pull", return_value=0) as mock_pull:
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0
                    mock_pull.assert_called_once()

    def test_cli_when_sync_conflicts_then_calls_sync_conflicts(self):
        with patch("sys.argv", ["ai", "sync", "conflicts"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.sync.sync_conflicts", return_value=0) as mock_conflicts:
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0
                    mock_conflicts.assert_called_once()

    def test_cli_when_sync_watch_then_calls_sync_watch(self):
        with patch("sys.argv", ["ai", "sync", "watch"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.sync.sync_watch", return_value=0) as mock_watch:
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0
                    mock_watch.assert_called_once()

    def test_cli_when_sync_unknown_action_then_exits_1(self):
        with patch("sys.argv", ["ai", "sync", "badaction"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_reconnect_with_host_then_lists_sessions(self, capsys):
        config = {"remote": {"host": "1.2.3.4", "user": "ubuntu"}}
        probe_result = MagicMock()
        probe_result.returncode = 0
        probe_result.stdout = "c-r-sw-1\nc-r-sw-2\nother-session\n"

        with patch("sys.argv", ["ai", "reconnect"]):
            with patch("ai_cli.main.load_config", return_value=config):
                with patch("subprocess.run", return_value=probe_result):
                    with patch("ai_cli.main.get_project_aliases", return_value={}):
                        with pytest.raises(SystemExit) as exc:
                            cli()
                        assert exc.value.code == 0
        output = capsys.readouterr().out
        assert "2 remote session" in output
        assert "ai c" in output

    def test_cli_when_reconnect_no_host_then_exits_1(self):
        config = {"remote": {"host": ""}}
        with patch("sys.argv", ["ai", "reconnect"]):
            with patch("ai_cli.main.load_config", return_value=config):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_reconnect_no_sessions_then_exits_0(self, capsys):
        config = {"remote": {"host": "1.2.3.4", "user": "ubuntu"}}
        probe_result = MagicMock()
        probe_result.returncode = 0
        probe_result.stdout = "other-session\n"

        with patch("sys.argv", ["ai", "reconnect"]):
            with patch("ai_cli.main.load_config", return_value=config):
                with patch("subprocess.run", return_value=probe_result):
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0
        assert "No remote CC sessions" in capsys.readouterr().out

    def test_cli_when_reconnect_ssh_fails_then_exits_1(self):
        config = {"remote": {"host": "1.2.3.4", "user": "ubuntu"}}
        probe_result = MagicMock()
        probe_result.returncode = 1

        with patch("sys.argv", ["ai", "reconnect"]):
            with patch("ai_cli.main.load_config", return_value=config):
                with patch("subprocess.run", return_value=probe_result):
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 1

    def test_cli_when_reconnect_filtered_no_match_then_exits_0(self, capsys):
        config = {"remote": {"host": "1.2.3.4", "user": "ubuntu"}}
        probe_result = MagicMock()
        probe_result.returncode = 0
        probe_result.stdout = "c-r-sw-1\n"

        with patch("sys.argv", ["ai", "reconnect", "99"]):
            with patch("ai_cli.main.load_config", return_value=config):
                with patch("subprocess.run", return_value=probe_result):
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0
        assert "No matching" in capsys.readouterr().out

    def test_cli_when_reconnect_with_alias_then_shows_project_flag(self, capsys):
        config = {"remote": {"host": "1.2.3.4", "user": "ubuntu"}}
        probe_result = MagicMock()
        probe_result.returncode = 0
        probe_result.stdout = "c-r-sw-1\n"

        with patch("sys.argv", ["ai", "reconnect"]):
            with patch("ai_cli.main.load_config", return_value=config):
                with patch("subprocess.run", return_value=probe_result):
                    with patch("ai_cli.main.get_project_aliases", return_value={"sw": "sergei"}):
                        with pytest.raises(SystemExit) as exc:
                            cli()
                        assert exc.value.code == 0
        output = capsys.readouterr().out
        assert "-p sw" in output


# --- Additional coverage for edge cases ---


class TestSessionMapEdgeCases:
    def test_get_session_map_path_when_engine_c_then_returns_claude_path(self, tmp_path):
        with patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path):
            result = get_session_map_path(engine="c")
        assert "cc-sessions.json" in str(result)

    def test_get_session_map_when_file_not_exists_then_returns_empty(self, tmp_path):
        path = tmp_path / "nonexistent.json"
        with patch("ai_cli.main.get_session_map_path", return_value=path):
            result = get_session_map(engine="c")
        assert result == {}


class TestProjectsDirEdgeCases:
    def test_get_projects_dir_when_exception_then_returns_default(self):
        with patch("ai_cli.main.load_config", side_effect=RuntimeError("broken")):
            result = _get_projects_dir()
        assert result == Path.home() / "projects"

    def test_get_handoff_queue_dir_when_no_main_project_then_returns_none(self):
        with patch("ai_cli.main._get_main_project_dir", return_value=None):
            result = _get_handoff_queue_dir()
        assert result is None

    def test_get_project_registry_path_when_no_main_dir_then_returns_none(self):
        with patch("ai_cli.main._get_main_project_dir", return_value=None):
            result = _get_project_registry_path()
        assert result is None

    def test_get_main_project_dir_when_not_configured_then_returns_none(self):
        with patch("ai_cli.main._get_main_project_name", return_value=None):
            result = _get_main_project_dir()
        assert result is None


class TestGetLatestGeminiEdgeCases:
    def test_latest_gemini_id_when_main_project_set_then_checks_both_paths(self, tmp_path):
        main_logs = tmp_path / ".gemini" / "tmp" / "sergei" / "logs.json"
        main_logs.parent.mkdir(parents=True)
        main_logs.write_text('{"sessionId": "main-id-123"}\n')

        with patch("pathlib.Path.cwd", return_value=tmp_path / "otherproject"):
            with patch("pathlib.Path.home", return_value=tmp_path):
                with patch("ai_cli.main._get_main_project_name", return_value="sergei"):
                    result = get_latest_gemini_session_id()
        assert result == "main-id-123"

    def test_latest_gemini_id_when_large_file_then_seeks_tail(self, tmp_path):
        logs_dir = tmp_path / ".gemini" / "tmp" / "bigproject"
        logs_dir.mkdir(parents=True)
        logs_file = logs_dir / "logs.json"
        # Create a file > 4096 bytes
        padding = "x" * 5000 + "\n"
        logs_file.write_text(padding + '{"sessionId": "tail-id-456"}\n')

        with patch("pathlib.Path.cwd", return_value=tmp_path / "bigproject"):
            with patch("pathlib.Path.home", return_value=tmp_path):
                with patch("ai_cli.main._get_main_project_name", return_value=None):
                    result = get_latest_gemini_session_id()
        assert result == "tail-id-456"


class TestResolveSessionEdgeCases:
    def test_resolve_session_when_no_name_and_current_session_matches_then_returns_it(self):
        def mock_run(cmd, **kwargs):
            m = MagicMock()
            if "display-message" in cmd:
                m.returncode = 0
                m.stdout = "c-sw-5"
            else:
                m.returncode = 0
                m.stdout = ""
            return m

        with patch("subprocess.run", side_effect=mock_run):
            result = resolve_session("c-sw-", "")
        assert result == "c-sw-5"


class TestGetEngineScript:
    def test_get_engine_script_when_claude_then_contains_claude_commands(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        assert "claude" in script
        assert 'engine="c"' in script
        assert 'ai_name="sw-1"' in script
        assert "CC_TMUX_SESSION" in script

    def test_get_engine_script_when_gemini_then_contains_gemini_commands(self):
        script = get_engine_script("g", "sw-1", "g-sw-1", "g-sw-", "sw")
        assert "gemini" in script
        assert 'engine="g"' in script
        assert "GG_TMUX_SESSION" in script

    def test_get_engine_script_when_worktree_then_cds(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw", worktree_dir="/tmp/wt")
        assert "cd /tmp/wt" in script

    def test_get_engine_script_when_no_worktree_then_noop_cd(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        # The cd command should be ":" (noop)
        assert "    :" in script

    def test_get_engine_script_when_notify_then_includes_notify_cmd(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw", notify=True)
        assert "ai internal notify" in script

    def test_get_engine_script_when_no_notify_then_no_notify_cmd(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw", notify=False)
        assert "ai internal notify" not in script

    def test_get_engine_script_when_sandbox_then_has_s_flag(self):
        script = get_engine_script("g", "sw-1", "g-sw-1", "g-sw-", "sw", sandbox=True)
        assert "-s" in script

    def test_get_engine_script_when_no_sandbox_then_no_s_flag(self):
        script = get_engine_script("g", "sw-1", "g-sw-1", "g-sw-", "sw", sandbox=False)
        # The sandbox_flag variable should be empty
        assert "gemini -y  -r" in script or "gemini -y  -i" in script

    def test_get_engine_script_when_valid_uuid_then_includes_it(self):
        valid_uuid = "550e8400-e29b-41d4-a716-446655440000"
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw", session_id_uuid=valid_uuid)
        assert f'uuid="{valid_uuid}"' in script

    def test_get_engine_script_when_invalid_uuid_then_clears_it(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw", session_id_uuid="../../evil; rm -rf /")
        assert 'uuid=""' in script

    def test_get_engine_script_uses_xdg_state_dir_not_tmp(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        assert "/tmp/cc-exit" not in script
        assert "_ai_state_dir" in script
        assert 'mkdir -p "$_ai_state_dir/iterm2"' in script

    def test_get_engine_script_when_remote_then_execs_shell(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw", is_remote=True)
        assert "exec $SHELL" in script

    def test_get_engine_script_when_local_then_exits(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw", is_remote=False)
        assert "exit 0" in script


class TestCreateWorktreeEdgeCases:
    def test_create_worktree_when_stale_dir_then_recreates(self, tmp_path):
        wt_dir = tmp_path / ".worktrees" / "sw-3"
        wt_dir.mkdir(parents=True)

        call_log = []

        def mock_run(cmd, **kwargs):
            call_log.append(cmd)
            m = MagicMock(returncode=0)
            if "list" in cmd and "--porcelain" in cmd:
                m.stdout = ""  # Not registered as valid worktree
            elif "worktree" in cmd and "add" in cmd:
                wt_dir.mkdir(parents=True, exist_ok=True)
            else:
                m.stdout = ""
            return m

        with patch("ai_cli.main.detect_repo_root", return_value=tmp_path):
            with patch("subprocess.run", side_effect=mock_run):
                result = create_worktree("sw-3")
        assert result == wt_dir


# --- Coverage gap tests: project registry / alias error branches ---


class TestProjectRegistryExceptionBranches:
    def test_get_project_prefix_by_name_when_registry_read_fails_then_returns_fallback(self, tmp_path):
        """Covers lines 198-199: exception in _get_project_prefix_by_name."""
        registry = tmp_path / "broken.toml"
        registry.write_text("not valid toml {{{")
        with patch("ai_cli.main._get_project_registry_path", return_value=registry):
            result = _get_project_prefix_by_name("myproject")
        assert result == "myp"

    def test_get_project_aliases_when_registry_read_fails_then_returns_empty(self, tmp_path):
        """Covers lines 216-217: exception in get_project_aliases."""
        registry = tmp_path / "broken.toml"
        registry.write_text("not valid toml {{{")
        with patch("ai_cli.main._get_project_registry_path", return_value=registry):
            result = get_project_aliases()
        assert result == {}

    def test_get_latest_gemini_session_id_when_read_fails_then_returns_none(self, tmp_path):
        """Covers lines 241-242: exception reading logs.json."""
        logs_dir = tmp_path / ".gemini" / "tmp" / "testproj"
        logs_dir.mkdir(parents=True)
        logs_file = logs_dir / "logs.json"
        logs_file.write_text("")  # empty file

        with patch("pathlib.Path.cwd", return_value=tmp_path / "testproj"):
            with patch("pathlib.Path.home", return_value=tmp_path):
                with patch("ai_cli.main._get_main_project_name", return_value=None):
                    result = get_latest_gemini_session_id()
        assert result is None

    def test_get_project_prefix_when_registry_read_fails_then_returns_fallback(self, tmp_path):
        """Covers lines 250-257: exception in get_project_prefix."""
        registry = tmp_path / "broken.toml"
        registry.write_text("not valid toml {{{")
        with patch("ai_cli.main._get_project_registry_path", return_value=registry):
            with patch("ai_cli.main.get_current_project_name", return_value="myproject"):
                result = get_project_prefix()
        assert result == "myp"


class TestFindNextIndex:
    def test_find_next_index_when_first_slot_taken_then_returns_second(self):
        """Covers line 267: i += 1 (second iteration of the while loop)."""
        call_count = {"n": 0}

        def mock_run(cmd, **kwargs):
            call_count["n"] += 1
            m = MagicMock()
            if call_count["n"] == 1:
                m.returncode = 0  # prefix1 exists
            else:
                m.returncode = 1  # prefix2 does not exist
            return m

        with patch("subprocess.run", side_effect=mock_run):
            result = find_next_index("c-sw-")
        assert result == 2


class TestFindRecentSession:
    def test_find_recent_session_when_tmux_fails_then_returns_empty(self):
        """Covers line 276 (returncode != 0 early return path)."""
        m = MagicMock(returncode=1, stdout="")
        with patch("subprocess.run", return_value=m):
            result = find_recent_session("c-sw-")
        assert result == ""

    def test_find_recent_session_when_empty_lines_then_skips(self):
        """Covers lines 278-279: empty line in output."""
        m = MagicMock(returncode=0, stdout="\n\nc-sw-1 100\n\nc-sw-2 200\n")
        with patch("subprocess.run", return_value=m):
            result = find_recent_session("c-sw-")
        assert result == "c-sw-2"

    def test_find_recent_session_when_bad_timestamp_then_skips(self):
        """Covers lines 284-285: ValueError parsing timestamp."""
        m = MagicMock(returncode=0, stdout="c-sw-1 notanumber\nc-sw-2 200\n")
        with patch("subprocess.run", return_value=m):
            result = find_recent_session("c-sw-")
        assert result == "c-sw-2"

    def test_find_recent_session_when_no_matching_sessions_then_returns_empty(self):
        """Covers lines 286-287: no sessions match prefix."""
        m = MagicMock(returncode=0, stdout="other-1 100\nother-2 200\n")
        with patch("subprocess.run", return_value=m):
            result = find_recent_session("c-sw-")
        assert result == ""


class TestCleanupStaleSessions:
    def test_cleanup_stale_when_empty_line_then_skips(self):
        """Covers line 325: empty line in tmux output."""
        m = MagicMock(returncode=0, stdout="\n\n")
        with patch("subprocess.run", return_value=m):
            cleanup_stale_sessions({})  # should not raise

    def test_cleanup_stale_when_bad_format_then_skips(self):
        """Covers line 328: line with wrong number of parts."""
        m = MagicMock(returncode=0, stdout="badline\n")
        with patch("subprocess.run", return_value=m):
            cleanup_stale_sessions({})

    def test_cleanup_stale_when_bad_timestamp_then_skips(self):
        """Covers lines 334-335: ValueError parsing last_attached."""
        m = MagicMock(returncode=0, stdout="c-sw-1|notanumber|0|bash\n")
        with patch("subprocess.run", return_value=m):
            cleanup_stale_sessions({})


class TestResolveSessionFallback:
    def test_resolve_session_when_no_name_and_no_current_session_then_finds_recent(self):
        """Covers line 362: falls through to find_recent_session."""

        def mock_run(cmd, **kwargs):
            m = MagicMock()
            if "display-message" in cmd:
                m.returncode = 0
                m.stdout = "other-session"  # doesn't match prefix
            elif "list-sessions" in cmd:
                m.returncode = 0
                m.stdout = "c-sw-1 100\n"
            else:
                m.returncode = 1
            return m

        with patch("subprocess.run", side_effect=mock_run):
            result = resolve_session("c-sw-", "")
        assert result == "c-sw-1"


class TestCreateWorktreeEdgeCases2:
    def test_create_worktree_when_first_add_fails_then_retries_existing_branch(self, tmp_path):
        """Covers line 444: fallback to existing branch."""
        wt_dir = tmp_path / ".worktrees" / "sw-4"
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            m = MagicMock(returncode=0, stdout="")
            if "worktree" in cmd and "add" in cmd and "-b" in cmd:
                m.returncode = 1  # branch already exists
            elif "worktree" in cmd and "add" in cmd:
                wt_dir.mkdir(parents=True, exist_ok=True)
            return m

        with patch("ai_cli.main.detect_repo_root", return_value=tmp_path):
            with patch("subprocess.run", side_effect=mock_run):
                result = create_worktree("sw-4")
        assert result == wt_dir
        # Verify the fallback call was made (without -b)
        add_calls = [c for c in calls if "worktree" in c and "add" in c]
        assert len(add_calls) == 2

    def test_create_worktree_when_src_not_exists_then_skips_symlink(self, tmp_path):
        """Covers lines 454-455: src doesn't exist, so no symlink."""
        wt_dir = tmp_path / ".worktrees" / "sw-5"

        def mock_run(cmd, **kwargs):
            m = MagicMock(returncode=0, stdout="")
            if "worktree" in cmd and "add" in cmd:
                wt_dir.mkdir(parents=True, exist_ok=True)
            return m

        with patch("ai_cli.main.detect_repo_root", return_value=tmp_path):
            with patch("subprocess.run", side_effect=mock_run):
                result = create_worktree("sw-5")
        assert result == wt_dir
        # No .venv, .claude etc in repo_root, so no symlinks created
        assert not (wt_dir / ".venv").exists()

    def test_create_worktree_when_wt_dir_not_created_then_returns_none(self, tmp_path):
        """Covers line 457: wt_dir.exists() is False after git commands."""

        def mock_run(cmd, **kwargs):
            m = MagicMock(returncode=1, stdout="")
            return m

        with patch("ai_cli.main.detect_repo_root", return_value=tmp_path):
            with patch("subprocess.run", side_effect=mock_run):
                result = create_worktree("sw-6")
        assert result is None


class TestCleanupWorktree:
    def test_cleanup_worktree_when_no_repo_then_noop(self):
        """Covers lines 461-463: no repo root."""
        with patch("ai_cli.main.detect_repo_root", return_value=None):
            cleanup_worktree("sw-1")  # should not raise

    def test_cleanup_worktree_when_dir_not_exists_then_noop(self, tmp_path):
        """Covers lines 465-466: worktree dir doesn't exist."""
        with patch("ai_cli.main.detect_repo_root", return_value=tmp_path):
            cleanup_worktree("nonexistent")

    def test_cleanup_worktree_when_dirty_then_skips_remove(self, tmp_path):
        """Covers lines 469-472: diff returns nonzero, so no removal."""
        wt_dir = tmp_path / ".worktrees" / "sw-7"
        wt_dir.mkdir(parents=True)
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            m = MagicMock()
            if "diff" in cmd and "--cached" not in cmd:
                m.returncode = 1  # dirty
            else:
                m.returncode = 0
            return m

        with patch("ai_cli.main.detect_repo_root", return_value=tmp_path):
            with patch("subprocess.run", side_effect=mock_run):
                cleanup_worktree("sw-7")
        # Should NOT have called worktree remove
        remove_calls = [c for c in calls if "remove" in c]
        assert len(remove_calls) == 0

    def test_cleanup_worktree_when_clean_then_removes(self, tmp_path):
        """Covers lines 471-472: both diffs clean, calls worktree remove."""
        wt_dir = tmp_path / ".worktrees" / "sw-8"
        wt_dir.mkdir(parents=True)
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0)

        with patch("ai_cli.main.detect_repo_root", return_value=tmp_path):
            with patch("subprocess.run", side_effect=mock_run):
                cleanup_worktree("sw-8")
        remove_calls = [c for c in calls if "remove" in c]
        assert len(remove_calls) == 1


class TestPostHandoffIdScanning:
    def test_post_handoff_when_existing_files_with_bad_names_then_handles_valueerror(self, tmp_path):
        """Covers lines 740-746: ValueError parsing file IDs."""
        queue_dir = tmp_path / ".handoff-queue"
        pending = queue_dir / "pending"
        pending.mkdir(parents=True)
        # File with non-numeric prefix
        (pending / "bad-name.md").write_text("content")

        with patch("ai_cli.main._get_handoff_queue_dir", return_value=queue_dir):
            post_handoff("New task", "P1", "proj", "msg")
        files = list(pending.glob("001-*.md"))
        assert len(files) == 1


class TestCheckHandoffEdgeCases:
    def test_check_handoff_when_no_handoff_dir_then_returns(self):
        """Covers line 759: handoff_dir is None."""
        with patch("ai_cli.main._get_handoff_queue_dir", return_value=None):
            check_handoff()  # should not raise

    def test_check_handoff_when_bad_priority_then_skips(self, tmp_path, capsys):
        """Covers lines 770-771: ValueError parsing priority."""
        queue_dir = tmp_path / ".handoff-queue"
        pending = queue_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "001-task.md").write_text("---\npriority: notanumber\n---\nTask")

        with patch("ai_cli.main._get_handoff_queue_dir", return_value=queue_dir):
            check_handoff()
        output = capsys.readouterr().out
        # With invalid priority, prio defaults to 9. Since it's the only file
        # and 9 < initial best_prio (9) is false, but prio == best_prio and best_file is None
        assert "001-task.md" in output

    def test_check_handoff_when_equal_priority_and_first_is_none_then_picks_first(self, tmp_path, capsys):
        """Covers lines 775-776: equal priority, best_file is None initially."""
        queue_dir = tmp_path / ".handoff-queue"
        pending = queue_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "001-task.md").write_text("---\npriority: 5\n---\nTask A")

        with patch("ai_cli.main._get_handoff_queue_dir", return_value=queue_dir):
            check_handoff()
        output = capsys.readouterr().out
        assert "001-task.md" in output


class TestClaimHandoffException:
    def test_claim_handoff_when_rename_fails_then_exits(self, tmp_path):
        """Covers lines 793-794: exception during file rename."""
        queue_dir = tmp_path / ".handoff-queue"
        with patch("ai_cli.main._get_handoff_queue_dir", return_value=queue_dir):
            with pytest.raises(SystemExit) as exc:
                claim_handoff("/nonexistent/path.md")
            assert exc.value.code == 1


class TestCompleteHandoffException:
    def test_complete_handoff_when_rename_fails_then_silent(self, tmp_path):
        """Covers lines 814-815: exception during file rename."""
        queue_dir = tmp_path / ".handoff-queue"
        with patch("ai_cli.main._get_handoff_queue_dir", return_value=queue_dir):
            complete_handoff("/nonexistent/path.md")  # should not raise


class TestTriggerBackgroundUpdateBadJson:
    def test_trigger_background_update_when_bad_json_in_state_then_proceeds(self, tmp_path):
        """Covers lines 826-827: JSON parse exception in state file."""
        state_file = tmp_path / "update_check.json"
        state_file.write_text("not valid json {{{")

        with patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path):
            with patch("subprocess.Popen") as mock_popen:
                trigger_background_update()
        mock_popen.assert_called_once()


class TestCliInternalMissingArgs:
    def test_cli_when_internal_publish_event_missing_args_then_exits_1(self):
        """Covers lines 881-882."""
        with patch("sys.argv", ["ai", "internal", "publish-event"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_internal_publish_heartbeat_missing_args_then_exits_1(self):
        """Covers lines 897-898."""
        with patch("sys.argv", ["ai", "internal", "publish-heartbeat"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_internal_publish_session_event_missing_args_then_exits_1(self):
        """Covers lines 917-918."""
        with patch("sys.argv", ["ai", "internal", "publish-session-event"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_internal_publish_missing_args_then_exits_1(self):
        """Covers lines 934-935."""
        with patch("sys.argv", ["ai", "internal", "publish"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_internal_publish_bad_json_payload_then_uses_empty(self):
        """Covers lines 942-943: JSONDecodeError on publish payload."""
        with patch("sys.argv", ["ai", "internal", "publish", "topic", "not-json"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.messaging.NATSClient") as mock_nats:
                    mock_instance = MagicMock()
                    mock_nats.return_value = mock_instance
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0


class TestCliDispatchBranches:
    def test_cli_when_memory_no_watch_then_exits_1(self):
        """Covers lines 975-977: memory with wrong subcommand."""
        with patch("sys.argv", ["ai", "memory", "bad"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_quota_no_watch_then_exits_1(self):
        """Covers lines 983-985."""
        with patch("sys.argv", ["ai", "quota", "bad"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_telemetry_no_writer_then_exits_1(self):
        """Covers lines 991-993."""
        with patch("sys.argv", ["ai", "telemetry", "bad"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_gemini_no_args_then_calls_gemini_cli(self):
        """Covers line 999: gemini dispatch with empty args."""
        with patch("sys.argv", ["ai", "gemini"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.gemini.gemini_cli", side_effect=SystemExit(0)):
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0


class TestCliSessionSetupBranches:
    def test_cli_when_no_sandbox_flag_then_sandbox_false(self):
        """Covers line 1107: --no-sandbox sets use_sandbox = False."""
        with patch("sys.argv", ["ai", "c", "--no-sandbox", "--bare"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.get_project_prefix", return_value="sw"):
                    with patch("ai_cli.main.trigger_background_update"):
                        with patch("os.execvp", side_effect=SystemExit(0)):
                            with pytest.raises(SystemExit):
                                cli()

    def test_cli_when_sandbox_flag_then_sandbox_true(self):
        """Covers line 1109: --sandbox sets use_sandbox = True."""
        with patch("sys.argv", ["ai", "g", "--sandbox", "--bare"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.get_project_prefix", return_value="sw"):
                    with patch("ai_cli.main.trigger_background_update"):
                        with patch("os.execvp", side_effect=SystemExit(0)):
                            with pytest.raises(SystemExit):
                                cli()

    def test_cli_when_remote_with_project_flag_then_uses_project_prefix(self):
        """Covers lines 1134, 1145, 1147: remote with -p flag."""
        config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "transport": "mosh"}}
        with patch("sys.argv", ["ai", "c", "-R", "-p", "myproj"]):
            with patch("ai_cli.main.load_config", return_value=config):
                with patch("ai_cli.main.get_project_prefix", return_value="sw"):
                    with patch("ai_cli.main.get_project_aliases", return_value={}):
                        with patch("ai_cli.main._get_project_prefix_by_name", return_value="mp"):
                            with patch("ai_cli.main.trigger_background_update"):
                                with patch("os.execvp", side_effect=SystemExit(0)):
                                    with pytest.raises(SystemExit):
                                        cli()

    def test_cli_when_remote_mosh_non_standard_port_then_adds_ssh_flag(self):
        """Covers line 1145: non-standard port with mosh transport."""
        config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "port": 2222, "transport": "mosh"}}
        with patch("sys.argv", ["ai", "c", "-R"]):
            with patch("ai_cli.main.load_config", return_value=config):
                with patch("ai_cli.main.get_project_prefix", return_value="sw"):
                    with patch("ai_cli.main.get_project_aliases", return_value={}):
                        with patch("ai_cli.main.trigger_background_update"):
                            with patch("os.execvp", side_effect=SystemExit(0)) as mock_exec:
                                with pytest.raises(SystemExit):
                                    cli()
                            args = mock_exec.call_args[0][1]
                            assert "--ssh" in args

    def test_cli_when_remote_mosh_with_identity_file_only_then_adds_ssh_i(self):
        """Covers line 1147: identity_file without non-standard port."""
        config = {
            "remote": {
                "host": "1.2.3.4",
                "user": "ubuntu",
                "port": 22,
                "transport": "mosh",
                "identity_file": "~/.ssh/id_ed25519",
            }
        }
        with patch("sys.argv", ["ai", "c", "-R"]):
            with patch("ai_cli.main.load_config", return_value=config):
                with patch("ai_cli.main.get_project_prefix", return_value="sw"):
                    with patch("ai_cli.main.get_project_aliases", return_value={}):
                        with patch("ai_cli.main.trigger_background_update"):
                            with patch("os.execvp", side_effect=SystemExit(0)) as mock_exec:
                                with pytest.raises(SystemExit):
                                    cli()
                            args = mock_exec.call_args[0][1]
                            assert "--ssh" in args
                            ssh_idx = args.index("--ssh")
                            assert "-i" in args[ssh_idx + 1]


class TestTriggerBackgroundUpdateRecent:
    def test_trigger_background_update_when_recently_checked_then_skips(self, tmp_path):
        """Covers lines 824-825: recently checked, early return."""
        state_file = tmp_path / "update_check.json"
        state_file.write_text(json.dumps({"last_checked": time.time()}))

        with patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path):
            with patch("subprocess.Popen") as mock_popen:
                trigger_background_update()
        mock_popen.assert_not_called()


class TestCliGeminiDispatch:
    def test_cli_when_gemini_returns_normally_then_exits_0(self):
        """Covers line 999: sys.exit(0) after gemini_cli returns."""
        with patch("sys.argv", ["ai", "gemini", "hello"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.gemini.gemini_cli", return_value=None):
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0


class TestCliReconnectContinueBranch:
    def test_cli_when_reconnect_session_name_too_short_then_continues(self, capsys):
        """Covers line 1065: session with < 4 parts after split is skipped via continue."""
        config = {"remote": {"host": "1.2.3.4", "user": "ubuntu"}}
        # "c-r-x" has 3 parts when split by "-", which is < 4 -> continue
        # "c-r-sw-1" has 4 parts -> processed
        probe = MagicMock(returncode=0, stdout="c-r-x\nc-r-sw-1\n")
        with patch("sys.argv", ["ai", "reconnect"]):
            with patch("ai_cli.main.load_config", return_value=config):
                with patch("subprocess.run", return_value=probe):
                    with patch("ai_cli.main.get_project_aliases", return_value={}):
                        with pytest.raises(SystemExit) as exc:
                            cli()
                        assert exc.value.code == 0
        out = capsys.readouterr().out
        # 2 sessions matched c-r- prefix, but only 1 has enough parts
        assert "2 remote session" in out


class TestPostHandoffExistingFilesMultiDir:
    def test_post_handoff_when_existing_in_claimed_and_completed_then_scans_all(self, tmp_path):
        """Covers lines 743-744: scanning across pending/claimed/completed for max ID."""
        queue_dir = tmp_path / ".handoff-queue"
        claimed = queue_dir / "claimed"
        claimed.mkdir(parents=True)
        (claimed / "005-old-task.md").write_text("content")
        completed = queue_dir / "completed"
        completed.mkdir(parents=True)
        (completed / "010-done-task.md").write_text("content")

        with patch("ai_cli.main._get_handoff_queue_dir", return_value=queue_dir):
            post_handoff("New task", "P1", "proj", "msg")
        # Next ID should be 11
        pending_files = list((queue_dir / "pending").glob("*.md"))
        assert len(pending_files) == 1
        assert "011-" in pending_files[0].name


# ---------------------------------------------------------------------------
# get_latest_gemini_session_id — exception branch
# ---------------------------------------------------------------------------


class TestGetLatestGeminiSessionIdException:
    def test_get_latest_gemini_session_id_when_open_raises_then_returns_none(self, tmp_path):
        """Covers lines 241-242: exception in open() inside get_latest_gemini_session_id."""
        import builtins as _builtins

        log_dir = tmp_path / ".gemini" / "tmp" / "testproj"
        log_dir.mkdir(parents=True)
        log_file = log_dir / "logs.json"
        log_file.write_bytes(b'{"sessionId": "abc"}')

        real_open = _builtins.open

        def fail_on_log(path, *args, **kwargs):
            if str(path).endswith("logs.json"):
                raise OSError("permission denied")
            return real_open(path, *args, **kwargs)

        from ai_cli.main import get_latest_gemini_session_id

        with patch("pathlib.Path.home", return_value=tmp_path):
            with patch("pathlib.Path.cwd", return_value=tmp_path / "projects" / "testproj"):
                with patch("ai_cli.main._get_main_project_name", return_value=None):
                    with patch("builtins.open", side_effect=fail_on_log):
                        result = get_latest_gemini_session_id()
        assert result is None


# ---------------------------------------------------------------------------
# get_project_prefix — project found in registry
# ---------------------------------------------------------------------------


class TestGetProjectPrefixRegistryMatch:
    def test_get_project_prefix_when_project_matches_registry_then_returns_prefix(self, tmp_path):
        """Covers lines 253-255: project name found in registry, returns task_prefix."""
        registry_file = tmp_path / "sergei.toml"
        registry_file.write_bytes(b'[[projects]]\nname = "myproject"\ntask_prefix = "MP"\n')

        with patch("ai_cli.main.get_current_project_name", return_value="myproject"):
            with patch("ai_cli.main._get_project_registry_path", return_value=registry_file):
                result = get_project_prefix()
        assert result == "mp"


# ---------------------------------------------------------------------------
# create_worktree — symlink creation
# ---------------------------------------------------------------------------


class TestCreateWorktreeSymlink:
    def test_create_worktree_when_venv_exists_then_symlinks(self, tmp_path):
        """Covers line 455: os.symlink when src exists and dst does not."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        # Create a .venv at repo_root to trigger symlink
        (repo_root / ".venv").mkdir()
        wt_dir = repo_root / ".worktrees" / "sw-1"
        # Do NOT pre-create wt_dir — function creates it via git worktree add

        def fake_run(cmd, *args, **kwargs):
            # Simulate git worktree add by creating the directory
            if isinstance(cmd, list) and "worktree" in cmd and "add" in cmd:
                wt_dir.mkdir(parents=True, exist_ok=True)
            return MagicMock(returncode=0, stdout="")

        with patch("ai_cli.main.detect_repo_root", return_value=repo_root):
            with patch("ai_cli.main.get_project_prefix", return_value="sw"):
                with patch("subprocess.run", side_effect=fake_run):
                    result = create_worktree("sw-1")

        assert (wt_dir / ".venv").is_symlink()
        assert result == wt_dir


# ---------------------------------------------------------------------------
# Daemon dispatch (memory watch / quota watch / telemetry writer)
# ---------------------------------------------------------------------------


class TestCliDaemonDispatch:
    def test_cli_when_memory_watch_then_calls_memory_watch(self):
        """Covers lines 976-978: memory watch dispatch."""
        with patch("sys.argv", ["ai", "memory", "watch"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.memory.memory_watch", return_value=0) as mock_watch:
                    with pytest.raises(SystemExit) as exc:
                        cli()
        assert exc.value.code == 0
        mock_watch.assert_called_once()

    def test_cli_when_quota_watch_then_calls_quota_watch(self):
        """Covers lines 984-986: quota watch dispatch."""
        with patch("sys.argv", ["ai", "quota", "watch"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.quota.quota_watch", return_value=0) as mock_watch:
                    with pytest.raises(SystemExit) as exc:
                        cli()
        assert exc.value.code == 0
        mock_watch.assert_called_once()

    def test_cli_when_telemetry_writer_then_calls_telemetry_writer(self):
        """Covers lines 992-994: telemetry writer dispatch."""
        with patch("sys.argv", ["ai", "telemetry", "writer"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.telemetry.telemetry_writer", return_value=0) as mock_writer:
                    with pytest.raises(SystemExit) as exc:
                        cli()
        assert exc.value.code == 0
        mock_writer.assert_called_once()


# ---------------------------------------------------------------------------
# CLI session paths: resume, once, existing session, new session, is_remote
# ---------------------------------------------------------------------------


class TestCliResumePath:
    def test_cli_when_resume_and_session_found_then_attaches(self):
        """Covers lines 1181-1186: resume path when session exists."""
        with patch("sys.argv", ["ai", "c", "-r", "1"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.get_project_prefix", return_value="sw"):
                    with patch("ai_cli.main.trigger_background_update"):
                        with patch("ai_cli.main.resolve_session", return_value="c-sw-1"):
                            with patch("os.execvp", side_effect=SystemExit(0)) as mock_exec:
                                with pytest.raises(SystemExit):
                                    cli()
                            assert mock_exec.call_args[0][0] == "tmux"
                            assert "attach-session" in mock_exec.call_args[0][1]

    def test_cli_when_resume_and_no_session_then_exits_1(self, capsys):
        """Covers lines 1183-1185: resume path when no session found."""
        with patch("sys.argv", ["ai", "c", "-r", "nonexistent"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.get_project_prefix", return_value="sw"):
                    with patch("ai_cli.main.trigger_background_update"):
                        with patch("ai_cli.main.resolve_session", return_value=None):
                            with pytest.raises(SystemExit) as exc:
                                cli()
                            assert exc.value.code == 1


class TestCliOncePath:
    def test_cli_when_once_and_claude_non_root_then_execvp_with_perms(self):
        """Covers lines 1201-1217: once flag with claude, non-root user."""
        with patch("sys.argv", ["ai", "c", "-o", "1"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.get_project_prefix", return_value="sw"):
                    with patch("ai_cli.main.trigger_background_update"):
                        with patch("ai_cli.main.cleanup_stale_sessions"):
                            with patch("ai_cli.main.build_session_name", return_value=("c-sw-1", "sw-1")):
                                with patch("ai_cli.main.create_worktree", return_value=None):
                                    with patch("ai_cli.main.get_session_map", return_value={}):
                                        with patch("os.getuid", return_value=1000):
                                            with patch("os.execvp", side_effect=SystemExit(0)) as mock_exec:
                                                with pytest.raises(SystemExit):
                                                    cli()
                                            assert mock_exec.call_args[0][0] == "tmux"
                                            bash_cmd = mock_exec.call_args[0][1][-1]
                                            assert "--dangerously-skip-permissions" in bash_cmd

    def test_cli_when_once_and_claude_root_then_execvp_without_perms(self):
        """Covers lines 1201-1217: once flag with claude, root user (no perms flag)."""
        with patch("sys.argv", ["ai", "c", "-o", "1"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.get_project_prefix", return_value="sw"):
                    with patch("ai_cli.main.trigger_background_update"):
                        with patch("ai_cli.main.cleanup_stale_sessions"):
                            with patch("ai_cli.main.build_session_name", return_value=("c-sw-1", "sw-1")):
                                with patch("ai_cli.main.create_worktree", return_value=None):
                                    with patch("ai_cli.main.get_session_map", return_value={}):
                                        with patch("os.getuid", return_value=0):
                                            with patch("os.execvp", side_effect=SystemExit(0)) as mock_exec:
                                                with pytest.raises(SystemExit):
                                                    cli()
                                            bash_cmd = mock_exec.call_args[0][1][-1]
                                            assert "--dangerously-skip-permissions" not in bash_cmd

    def test_cli_when_once_and_gemini_with_uuid_then_resumes(self):
        """Covers lines 1219-1232: once flag with gemini and existing uuid."""
        with patch("sys.argv", ["ai", "g", "-o", "research"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.get_project_prefix", return_value="sw"):
                    with patch("ai_cli.main.trigger_background_update"):
                        with patch("ai_cli.main.cleanup_stale_sessions"):
                            with patch("ai_cli.main.build_session_name", return_value=("g-sw-research", "sw-research")):
                                with patch("ai_cli.main.create_worktree", return_value=None):
                                    with patch("ai_cli.main.get_session_map", return_value={"sw-research": "uuid123"}):
                                        with patch("os.execvp", side_effect=SystemExit(0)) as mock_exec:
                                            with pytest.raises(SystemExit):
                                                cli()
                                        bash_cmd = mock_exec.call_args[0][1][-1]
                                        assert "uuid123" in bash_cmd

    def test_cli_when_once_and_gemini_no_uuid_then_uses_resume_load(self):
        """Covers lines 1233-1246: once flag with gemini but no uuid."""
        with patch("sys.argv", ["ai", "g", "-o", "research"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.get_project_prefix", return_value="sw"):
                    with patch("ai_cli.main.trigger_background_update"):
                        with patch("ai_cli.main.cleanup_stale_sessions"):
                            with patch("ai_cli.main.build_session_name", return_value=("g-sw-research", "sw-research")):
                                with patch("ai_cli.main.create_worktree", return_value=None):
                                    with patch("ai_cli.main.get_session_map", return_value={}):
                                        with patch("os.execvp", side_effect=SystemExit(0)) as mock_exec:
                                            with pytest.raises(SystemExit):
                                                cli()
                                        bash_cmd = mock_exec.call_args[0][1][-1]
                                        assert "resume load" in bash_cmd


class TestCliSessionExecvp:
    def test_cli_when_existing_session_then_attaches_with_detach(self):
        """Covers line 1264: existing session attach with -d flag."""
        with patch("sys.argv", ["ai", "c", "1"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.get_project_prefix", return_value="sw"):
                    with patch("ai_cli.main.trigger_background_update"):
                        with patch("ai_cli.main.cleanup_stale_sessions"):
                            with patch("ai_cli.main.build_session_name", return_value=("c-sw-1", "sw-1")):
                                with patch("ai_cli.main.create_worktree", return_value=None):
                                    with patch("ai_cli.main.get_session_map", return_value={}):
                                        with patch("ai_cli.main.get_engine_script", return_value="script"):
                                            existing = MagicMock(returncode=0)
                                            with patch("subprocess.run", return_value=existing):
                                                with patch("os.execvp", side_effect=SystemExit(0)) as mock_exec:
                                                    with pytest.raises(SystemExit):
                                                        cli()
                                                assert "attach-session" in mock_exec.call_args[0][1]
                                                assert "-d" in mock_exec.call_args[0][1]

    def test_cli_when_no_existing_session_then_creates_new(self):
        """Covers line 1270: new session via tmux new-session."""
        with patch("sys.argv", ["ai", "c", "1"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.get_project_prefix", return_value="sw"):
                    with patch("ai_cli.main.trigger_background_update"):
                        with patch("ai_cli.main.cleanup_stale_sessions"):
                            with patch("ai_cli.main.build_session_name", return_value=("c-sw-1", "sw-1")):
                                with patch("ai_cli.main.create_worktree", return_value=None):
                                    with patch("ai_cli.main.get_session_map", return_value={}):
                                        with patch("ai_cli.main.get_engine_script", return_value="script"):
                                            not_existing = MagicMock(returncode=1)
                                            with patch("subprocess.run", return_value=not_existing):
                                                with patch("os.execvp", side_effect=SystemExit(0)) as mock_exec:
                                                    with pytest.raises(SystemExit):
                                                        cli()
                                                assert "new-session" in mock_exec.call_args[0][1]


class TestCliIsRemotePath:
    def test_cli_when_is_remote_and_project_dir_exists_then_chdirs(self, tmp_path):
        """Covers lines 1163-1169: --is-remote chdir to project directory."""
        project_dir = tmp_path / "projects" / "myproj"
        project_dir.mkdir(parents=True)

        with patch("sys.argv", ["ai", "c", "--is-remote", "--project", "myproj"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.get_project_prefix", return_value="sw"):
                    with patch("ai_cli.main.trigger_background_update"):
                        with patch("ai_cli.main.get_project_aliases", return_value={}):
                            with patch("ai_cli.main._find_project_dir", return_value=project_dir):
                                with patch("ai_cli.main.cleanup_stale_sessions"):
                                    with patch("ai_cli.main.build_session_name", return_value=("cr-sw-1", "sw-1")):
                                        with patch("ai_cli.main.create_worktree", return_value=None):
                                            with patch("ai_cli.main.get_session_map", return_value={}):
                                                with patch("ai_cli.main.get_engine_script", return_value="script"):
                                                    not_existing = MagicMock(returncode=1)
                                                    with patch("subprocess.run", return_value=not_existing):
                                                        with patch("os.chdir") as mock_chdir:
                                                            with patch("os.execvp", side_effect=SystemExit(0)):
                                                                with pytest.raises(SystemExit):
                                                                    cli()
                                                        mock_chdir.assert_called_once_with(project_dir)


class TestCliWorktreeGitPull:
    def test_cli_when_worktree_created_then_runs_git_pull(self, tmp_path):
        """Covers line 1192: subprocess.run git pull after create_worktree returns a path."""
        worktree_path = tmp_path / ".worktrees" / "sw-1"
        worktree_path.mkdir(parents=True)

        git_pull_calls = []

        def fake_subprocess_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and "pull" in cmd:
                git_pull_calls.append(cmd)
            return MagicMock(returncode=1)

        with patch("sys.argv", ["ai", "c", "1"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.get_project_prefix", return_value="sw"):
                    with patch("ai_cli.main.trigger_background_update"):
                        with patch("ai_cli.main.cleanup_stale_sessions"):
                            with patch("ai_cli.main.build_session_name", return_value=("c-sw-1", "sw-1")):
                                with patch("ai_cli.main.create_worktree", return_value=worktree_path):
                                    with patch("ai_cli.main.get_session_map", return_value={}):
                                        with patch("ai_cli.main.get_engine_script", return_value="script"):
                                            with patch("subprocess.run", side_effect=fake_subprocess_run):
                                                with patch("os.execvp", side_effect=SystemExit(0)):
                                                    with pytest.raises(SystemExit):
                                                        cli()

        assert any("pull" in cmd for cmd in git_pull_calls)


class TestEmitIterm2ProfileSetup:
    """Tests for _emit_iterm2_profile_setup — lines 496-507."""

    def test_when_not_iterm2_then_writes_nothing(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "", "TERM_PROGRAM": ""}, clear=False):
            _emit_iterm2_profile_setup("sw-1", "c")
        assert capsys.readouterr().out == ""

    def test_when_lc_terminal_is_iterm2_and_claude_engine_then_emits_profile_and_color(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            _emit_iterm2_profile_setup("sw-3", "c")
        out = capsys.readouterr().out
        assert "SetProfile=ClaudeCode" in out
        assert "SetColors=tab=" in out

    def test_when_lc_terminal_is_iterm2_and_gemini_engine_then_emits_gemini_profile(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            _emit_iterm2_profile_setup("ai-dojo-1", "g")
        out = capsys.readouterr().out
        assert "SetProfile=GeminiCLI" in out
        assert "SetColors" not in out  # Gemini path does not set tab color

    def test_when_term_program_is_iterm_app_then_also_activates(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "", "TERM_PROGRAM": "iTerm.app"}, clear=False):
            _emit_iterm2_profile_setup("sw-1", "c")
        assert "SetProfile=ClaudeCode" in capsys.readouterr().out

    def test_when_session_has_no_trailing_number_then_defaults_to_color_index_1(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            _emit_iterm2_profile_setup("no-number", "c")
        out = capsys.readouterr().out
        # num defaults to 1 → index 0 in the color list
        assert "SetColors=tab=" in out

    def test_when_unknown_engine_then_writes_nothing(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            _emit_iterm2_profile_setup("sw-1", "x")
        assert capsys.readouterr().out == ""

    def test_when_iterm_session_id_set_then_writes_color_profile_file(self, tmp_path):
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2", "ITERM_SESSION_ID": "w0t0p0:abc"}, clear=False):
            with patch("ai_cli.main._iterm2_update_window_title"):
                with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
                    _emit_iterm2_profile_setup("sw-1", "c")
        assert (tmp_path / "cc-color-w0t0").exists()

    def test_when_iterm_color_file_write_raises_oserror_then_silently_passes(self, tmp_path, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2", "ITERM_SESSION_ID": "w0t0p0:abc"}, clear=False):
            with patch("ai_cli.main._iterm2_update_window_title"):
                with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
                    with patch("builtins.open", side_effect=OSError("read-only fs")):
                        # Should not raise — OSError is silently swallowed
                        _emit_iterm2_profile_setup("sw-1", "c")
        # Profile and color still emitted to stdout before the file write
        assert "SetProfile=ClaudeCode" in capsys.readouterr().out


class TestProjectPathSeparatorValidation:
    """Tests for --project path separator guard."""

    def test_when_project_contains_slash_then_exits_1(self, capsys):
        with patch("sys.argv", ["ai", "c", "1", "-R", "--project", "../evil"]):
            with patch("ai_cli.main.load_config", return_value={"remote": {"host": "h", "user": "u"}}):
                with patch("ai_cli.main.get_project_aliases", return_value={}):
                    with patch("ai_cli.main.get_current_project_name", return_value=""):
                        with pytest.raises(SystemExit) as exc:
                            cli()
        assert exc.value.code == 1
        assert "path separator" in capsys.readouterr().err

    def test_when_project_has_no_separator_then_proceeds(self, capsys):
        with patch("sys.argv", ["ai", "c", "1", "-R", "--project", "myproject"]):
            with patch("ai_cli.main.load_config", return_value={"remote": {"host": "h", "user": "u"}}):
                with patch("ai_cli.main.get_project_aliases", return_value={}):
                    with patch("ai_cli.main.get_current_project_name", return_value=""):
                        with patch("ai_cli.main._get_project_prefix_by_name", return_value="my"):
                            with patch("os.execvp", side_effect=SystemExit(0)):
                                with pytest.raises(SystemExit) as exc:
                                    cli()
        assert exc.value.code == 0


class TestCliAttachDispatch:
    """Tests for `ai attach` subcommand — lines 1106-1114."""

    def test_when_no_session_name_then_exits_1_with_usage(self, capsys):
        with patch("sys.argv", ["ai", "attach"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
        assert exc.value.code == 1
        assert "Usage" in capsys.readouterr().err

    def test_when_session_does_not_exist_then_exits_1_with_message(self, capsys):
        with patch("sys.argv", ["ai", "attach", "c-sw-99"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("subprocess.run", return_value=MagicMock(returncode=1)):
                    with pytest.raises(SystemExit) as exc:
                        cli()
        assert exc.value.code == 1
        assert "c-sw-99" in capsys.readouterr().err

    def test_when_session_exists_then_execs_tmux_attach(self):
        with patch("sys.argv", ["ai", "attach", "c-sw-1"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("subprocess.run", return_value=MagicMock(returncode=0)):
                    with patch("os.execvp") as mock_exec:
                        mock_exec.side_effect = SystemExit(0)
                        with pytest.raises(SystemExit):
                            cli()
        mock_exec.assert_called_once_with("tmux", ["tmux", "attach-session", "-t", "c-sw-1"])


class TestCliLsDispatch:
    """Tests for `ai ls` subcommand — lines 1116-1209."""

    def _fake_tmux_sessions(self, sessions: list[tuple[str, int]]):
        """Build a fake `tmux list-sessions` stdout."""
        lines = "\n".join(f"{name} {ts}" for name, ts in sessions)
        return MagicMock(returncode=0, stdout=lines)

    def test_when_tmux_not_running_then_exits_0_with_stderr(self, capsys):
        with patch("sys.argv", ["ai", "ls"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
                    with pytest.raises(SystemExit) as exc:
                        cli()
        assert exc.value.code == 0
        assert "tmux" in capsys.readouterr().err.lower()

    def test_when_no_ai_sessions_then_exits_0_with_hint(self, capsys):
        # tmux has non-ai sessions; default filter should find nothing
        with patch("sys.argv", ["ai", "ls"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("subprocess.run", return_value=self._fake_tmux_sessions([("random-session", 1000)])):
                    with patch("shutil.which", return_value=None):
                        with pytest.raises(SystemExit) as exc:
                            cli()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "--all" in out  # should mention --all flag

    def test_when_no_sessions_with_all_flag_then_exits_0_with_no_sessions_message(self, capsys):
        with patch("sys.argv", ["ai", "ls", "--all"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
                    with pytest.raises(SystemExit) as exc:
                        cli()
        assert exc.value.code == 0
        assert "tmux" in capsys.readouterr().err.lower()

    def test_when_fzf_unavailable_then_prints_numbered_list(self, capsys):
        now = int(time.time())
        sessions = [("c-sw-1", now - 120), ("c-sw-2", now - 3600)]
        with patch("sys.argv", ["ai", "ls"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("subprocess.run", return_value=self._fake_tmux_sessions(sessions)):
                    with patch("shutil.which", return_value=None):
                        with pytest.raises(SystemExit) as exc:
                            cli()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "c-sw-1" in out
        assert "c-sw-2" in out
        assert "ai attach" in out  # fallback list should show attach hint

    def test_when_fzf_available_and_selection_made_then_execs_attach(self):
        now = int(time.time())
        sessions = [("c-sw-1", now - 60)]
        fzf_result = MagicMock(returncode=0, stdout="c-sw-1\tsw\t1m\n")

        def fake_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and "tmux" in cmd:
                return self._fake_tmux_sessions(sessions)
            return fzf_result  # fzf

        with patch("sys.argv", ["ai", "ls"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("subprocess.run", side_effect=fake_run):
                    with patch("shutil.which", return_value="/usr/bin/fzf"):
                        with patch("os.execvp") as mock_exec:
                            mock_exec.side_effect = SystemExit(0)
                            with pytest.raises(SystemExit):
                                cli()
        mock_exec.assert_called_once_with("tmux", ["tmux", "attach-session", "-t", "c-sw-1"])

    def test_when_fzf_cancelled_then_exits_0_without_attaching(self):
        now = int(time.time())
        sessions = [("c-sw-1", now - 60)]
        fzf_cancelled = MagicMock(returncode=130, stdout="")  # fzf exits 130 on Ctrl-C

        def fake_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and "tmux" in cmd:
                return self._fake_tmux_sessions(sessions)
            return fzf_cancelled

        with patch("sys.argv", ["ai", "ls"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("subprocess.run", side_effect=fake_run):
                    with patch("shutil.which", return_value="/usr/bin/fzf"):
                        with patch("os.execvp") as mock_exec:
                            with pytest.raises(SystemExit) as exc:
                                cli()
        assert exc.value.code == 0
        mock_exec.assert_not_called()

    def test_when_all_flag_set_then_shows_non_ai_sessions_too(self, capsys):
        now = int(time.time())
        sessions = [("c-sw-1", now - 60), ("random-session", now - 120)]
        with patch("sys.argv", ["ai", "ls", "--all"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("subprocess.run", return_value=self._fake_tmux_sessions(sessions)):
                    with patch("shutil.which", return_value=None):
                        with pytest.raises(SystemExit) as exc:
                            cli()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "random-session" in out  # non-ai session visible with --all

    def test_when_tmux_output_has_blank_lines_then_skips_them(self, capsys):
        now = int(time.time())
        # Output contains a blank line — should be skipped, not crash
        raw_output = f"c-sw-1 {now - 300}\n\nc-sw-2 {now - 600}\n"
        tmux_result = MagicMock(returncode=0, stdout=raw_output)
        with patch("sys.argv", ["ai", "ls"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("subprocess.run", return_value=tmux_result):
                    with patch("shutil.which", return_value=None):
                        with pytest.raises(SystemExit):
                            cli()
        out = capsys.readouterr().out
        assert "c-sw-1" in out
        assert "c-sw-2" in out

    def test_when_tmux_activity_is_non_integer_then_defaults_to_zero(self, capsys):
        # Malformed tmux output — activity field is not a number
        raw_output = "c-sw-1 not-a-number\n"
        tmux_result = MagicMock(returncode=0, stdout=raw_output)
        with patch("sys.argv", ["ai", "ls"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("subprocess.run", return_value=tmux_result):
                    with patch("shutil.which", return_value=None):
                        with pytest.raises(SystemExit):
                            cli()
        # Session still displayed; age defaults to 0 so delta = now → shows large value
        assert "c-sw-1" in capsys.readouterr().out

    def test_when_session_age_is_seconds_then_displays_s_suffix(self, capsys):
        now = int(time.time())
        raw_output = f"c-sw-1 {now - 10}\n"  # 10 seconds ago
        tmux_result = MagicMock(returncode=0, stdout=raw_output)
        with patch("sys.argv", ["ai", "ls"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("subprocess.run", return_value=tmux_result):
                    with patch("shutil.which", return_value=None):
                        with pytest.raises(SystemExit):
                            cli()
        assert "10s" in capsys.readouterr().out

    def test_when_session_age_is_days_then_displays_d_suffix(self, capsys):
        now = int(time.time())
        raw_output = f"c-sw-1 {now - 90000}\n"  # 25 hours ago
        tmux_result = MagicMock(returncode=0, stdout=raw_output)
        with patch("sys.argv", ["ai", "ls"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("subprocess.run", return_value=tmux_result):
                    with patch("shutil.which", return_value=None):
                        with pytest.raises(SystemExit):
                            cli()
        assert "1d" in capsys.readouterr().out

    def test_when_fzf_absent_but_apt_available_then_installs_fzf(self, capsys):
        now = int(time.time())
        raw_output = f"c-sw-1 {now - 60}\n"
        apt_install_calls = []

        def fake_which(cmd):
            # fzf not found initially; apt is found; fzf still not found after install
            if cmd == "fzf":
                return None
            if cmd == "apt":
                return "/usr/bin/apt"
            return None

        def fake_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and "tmux" in cmd:
                return MagicMock(returncode=0, stdout=raw_output)
            if isinstance(cmd, list) and "apt" in cmd:
                apt_install_calls.append(cmd)
                return MagicMock(returncode=0)
            return MagicMock(returncode=1)

        with patch("sys.argv", ["ai", "ls"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("subprocess.run", side_effect=fake_run):
                    with patch("shutil.which", side_effect=fake_which):
                        with pytest.raises(SystemExit) as exc:
                            cli()
        assert exc.value.code == 0
        assert any("fzf" in str(cmd) for cmd in apt_install_calls)
        assert "fzf not found" in capsys.readouterr().out


class TestIterm2RegisterSession:
    """Tests for _iterm2_register_session."""

    def test_when_file_absent_then_creates_with_new_entry(self, tmp_path):
        with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
            _iterm2_register_session("w0t0", "0", "sw-1", "cc", "init")
        assert "0:sw-1:cc:init" in (tmp_path / "cc-names-w0t0").read_text()

    def test_when_existing_entry_for_same_pane_then_updates_it(self, tmp_path):
        (tmp_path / "cc-names-w0t1").write_text("0:sw-1:cc:init\n1:sw-2:cc:running\n")
        with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
            _iterm2_register_session("w0t1", "0", "sw-1", "cc", "running")
        content = (tmp_path / "cc-names-w0t1").read_text()
        assert "0:sw-1:cc:running" in content
        assert "0:sw-1:cc:init" not in content
        assert "1:sw-2:cc:running" in content  # other pane preserved

    def test_when_oserror_on_write_then_silently_passes(self, tmp_path):
        with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
            with patch("builtins.open", side_effect=OSError("read-only")):
                _iterm2_register_session("w9t9", "0", "sw-1", "cc", "init")  # must not raise


class TestIterm2ComputeTabTitle:
    """Tests for _iterm2_compute_tab_title."""

    def test_when_file_absent_then_returns_empty(self, tmp_path):
        with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
            assert _iterm2_compute_tab_title("w0t0") == ""

    def test_when_file_has_only_blank_lines_then_returns_empty(self, tmp_path):
        (tmp_path / "cc-names-w0t0").write_text("\n   \n\n")
        with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
            assert _iterm2_compute_tab_title("w0t0") == ""

    def test_when_single_entry_then_returns_symbol_status_name(self, tmp_path):
        (tmp_path / "cc-names-w0t2").write_text("0:sw-1:cc:running\n")
        with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
            result = _iterm2_compute_tab_title("w0t2")
        assert "*" in result  # cc symbol
        assert "▶" in result  # running status
        assert "sw-1" in result

    def test_when_multi_entry_with_long_common_prefix_then_uses_prefix_format(self, tmp_path):
        (tmp_path / "cc-names-w0t3").write_text("0:aiproject-1:cc:running\n1:aiproject-2:cc:waiting\n")
        with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
            result = _iterm2_compute_tab_title("w0t3")
        # prefix "aiproject-" (>=4 chars) → uses prefix{suffix1|suffix2} format
        assert "aiproject-" in result
        assert "{" in result

    def test_when_multi_entry_without_common_prefix_then_joins_names(self, tmp_path):
        (tmp_path / "cc-names-w0t4").write_text("0:abc-1:cc:running\n1:xyz-2:cc:waiting\n")
        with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
            result = _iterm2_compute_tab_title("w0t4")
        # No long common prefix → all names listed
        assert "abc-1" in result
        assert "xyz-2" in result
        assert "{" not in result


class TestIterm2StateDir:
    """Tests for _iterm2_state_dir — creates XDG state/iterm2 dir."""

    def test_returns_xdg_state_iterm2_subdir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        result = _iterm2_state_dir()
        assert result == tmp_path / "ai-cli-utils" / "iterm2"
        assert result.is_dir()

    def test_creates_directory_if_absent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        target = tmp_path / "ai-cli-utils" / "iterm2"
        assert not target.exists()
        _iterm2_state_dir()
        assert target.is_dir()


class TestIterm2HeuristicWindowTitle:
    """Tests for _iterm2_heuristic_window_title — lines 605-623."""

    def test_when_empty_sessions_then_returns_cc_sessions(self):
        assert _iterm2_heuristic_window_title([]) == "CC Sessions"

    def test_when_sessions_match_pattern_then_extracts_project(self):
        result = _iterm2_heuristic_window_title(["c-myproject-1", "c-myproject-2"])
        assert "myproject" in result

    def test_when_only_remote_sessions_then_includes_remote(self):
        result = _iterm2_heuristic_window_title(["c-r-myproject-1"])
        assert "Remote" in result
        assert "Local" not in result

    def test_when_only_local_sessions_then_includes_local(self):
        result = _iterm2_heuristic_window_title(["c-myproject-1"])
        assert "Local" in result
        assert "Remote" not in result

    def test_when_both_local_and_remote_then_neither_suffix(self):
        result = _iterm2_heuristic_window_title(["c-myproject-1", "c-r-myproject-2"])
        assert "Remote" not in result
        assert "Local" not in result


class TestIterm2UpdateWindowTitle:
    """Tests for _iterm2_update_window_title."""

    def test_when_no_win_key_then_returns_early(self, capsys):
        _iterm2_update_window_title("", "w0t0", "sw-1", "cc")
        assert capsys.readouterr().out == ""

    def test_when_new_tab_then_creates_win_file_and_emits_heuristic_title(self, tmp_path, capsys):
        with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
            with patch("subprocess.Popen") as mock_popen:
                mock_popen.return_value = MagicMock()
                _iterm2_update_window_title("w0", "w0t0", "sw-1", "cc")
        out = capsys.readouterr().out
        assert "\033]2;" in out  # heuristic window title emitted
        assert "w0t0:sw-1:cc" in (tmp_path / "win-w0").read_text()

    def test_when_existing_tab_then_updates_entry(self, tmp_path):
        (tmp_path / "win-w1").write_text("w1t0:sw-1:cc\nw1t1:sw-2:cc\n")
        with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
            with patch("subprocess.Popen"):
                _iterm2_update_window_title("w1", "w1t0", "sw-1-updated", "cc")
        content = (tmp_path / "win-w1").read_text()
        assert "w1t0:sw-1-updated:cc" in content
        assert "w1t0:sw-1:cc" not in content
        assert "w1t1:sw-2:cc" in content

    def test_when_oserror_on_win_file_write_then_returns_early(self, tmp_path, capsys):
        _real_open = builtins.open

        def failing_write(path, mode="r", *a, **kw):
            if str(path) == str(tmp_path / "win-wfail") and "w" in mode:
                raise OSError("read-only")
            return _real_open(path, mode, *a, **kw)

        with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
            with patch("builtins.open", side_effect=failing_write):
                _iterm2_update_window_title("wfail", "wfailt0", "sw-1", "cc")
        assert "\033]2;" not in capsys.readouterr().out

    def test_when_popen_raises_then_writes_heuristic_to_title_file(self, tmp_path):
        with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
            with patch("subprocess.Popen", side_effect=FileNotFoundError("claude not found")):
                _iterm2_update_window_title("w2", "w2t0", "sw-1", "cc")
        title_file = tmp_path / "win-title-w2"
        assert title_file.exists()
        assert title_file.read_text() != ""  # heuristic written as fallback

    def test_when_popen_raises_and_title_file_write_fails_then_silently_passes(self, tmp_path):
        _real_open = builtins.open

        def failing_title_write(path, mode="r", *a, **kw):
            if str(path) == str(tmp_path / "win-title-w3") and "w" in mode:
                raise OSError("read-only title file")
            return _real_open(path, mode, *a, **kw)

        with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
            with patch("subprocess.Popen", side_effect=FileNotFoundError("no claude")):
                with patch("builtins.open", side_effect=failing_title_write):
                    # Must not raise — both OSErrors are swallowed
                    _iterm2_update_window_title("w3", "w3t0", "sw-1", "cc")


class TestEmitIterm2ProfileSetupGeminiWithTabKey:
    """Tests for gemini engine + tab_key path in _emit_iterm2_profile_setup."""

    def test_when_gemini_engine_and_iterm_session_id_set_then_registers_and_emits_title(self, capsys, tmp_path):
        (tmp_path / "cc-names-w0t0").write_text("0:g-research-1:gemini:init\n")
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2", "ITERM_SESSION_ID": "w0t0p0:abc"}, clear=False):
            with patch("ai_cli.main._iterm2_update_window_title"):
                with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
                    _emit_iterm2_profile_setup("research-1", "g")
        out = capsys.readouterr().out
        assert "SetProfile=GeminiCLI" in out
        assert "\033]0;" in out  # tab title emitted (file had an entry)


class TestInternalIterm2Dispatch:
    """Tests for `ai internal iterm2-*` CLI subcommands — lines 1244-1290."""

    def test_iterm2_tab_title_when_called_with_tab_key_then_prints_title(self, capsys, tmp_path):
        names_file = tmp_path / "iterm2-cc-names-test-tab"
        names_file.write_text("0:sw-1:cc:running\n")
        with patch("sys.argv", ["ai", "internal", "iterm2-tab-title", "test-tab"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main._iterm2_compute_tab_title", return_value="* ▶ sw-1"):
                    with pytest.raises(SystemExit) as exc:
                        cli()
        assert exc.value.code == 0
        assert "* ▶ sw-1" in capsys.readouterr().out

    def test_iterm2_tab_title_when_no_tab_key_arg_then_exits_0_silently(self, capsys):
        with patch("sys.argv", ["ai", "internal", "iterm2-tab-title"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
        assert exc.value.code == 0

    def test_iterm2_update_status_when_called_with_args_then_registers_and_prints_title(self, capsys):
        with patch("sys.argv", ["ai", "internal", "iterm2-update-status", "w0t0", "0", "sw-1", "cc", "running"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main._iterm2_register_session") as mock_reg:
                    with patch("ai_cli.main._iterm2_compute_tab_title", return_value="* ▶ sw-1"):
                        with pytest.raises(SystemExit) as exc:
                            cli()
        assert exc.value.code == 0
        mock_reg.assert_called_once_with("w0t0", "0", "sw-1", "cc", "running")
        assert "* ▶ sw-1" in capsys.readouterr().out

    def test_iterm2_exit_cleanup_when_called_then_removes_pane_and_exits_0(self, tmp_path, capsys):
        (tmp_path / "cc-names-w0t0").write_text("0:sw-1:cc:running\n1:sw-2:cc:running\n")
        (tmp_path / "win-w0").write_text("w0t0:sw-1:cc\n")
        with patch("sys.argv", ["ai", "internal", "iterm2-exit-cleanup", "w0t0", "0", "w0"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
                    with patch("ai_cli.main._iterm2_compute_tab_title", return_value="* sw-2"):
                        with pytest.raises(SystemExit) as exc:
                            cli()
        assert exc.value.code == 0
        assert "0:sw-1" not in (tmp_path / "cc-names-w0t0").read_text()
        assert "1:sw-2" in (tmp_path / "cc-names-w0t0").read_text()

    def test_iterm2_exit_cleanup_when_last_pane_then_unlinks_names_file(self, tmp_path):
        (tmp_path / "cc-names-w1t0").write_text("0:sw-1:cc:running\n")  # only one pane
        unlinked = []
        with patch("sys.argv", ["ai", "internal", "iterm2-exit-cleanup", "w1t0", "0", "w1"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
                    with patch("os.unlink", side_effect=lambda p: unlinked.append(p)):
                        with pytest.raises(SystemExit) as exc:
                            cli()
        assert exc.value.code == 0
        assert any("cc-names-w1t0" in str(p) for p in unlinked)

    def test_iterm2_exit_cleanup_when_names_file_raises_then_silently_continues(self, tmp_path):
        with patch("sys.argv", ["ai", "internal", "iterm2-exit-cleanup", "w9t9", "0", "w9"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
                    with patch("builtins.open", side_effect=OSError("no names file")):
                        with pytest.raises(SystemExit) as exc:
                            cli()
        assert exc.value.code == 0  # OSError swallowed, still exits cleanly

    def test_iterm2_exit_cleanup_when_win_file_absent_then_silently_continues(self, tmp_path):
        (tmp_path / "cc-names-w2t0").write_text("0:sw-1:cc:running\n1:sw-2:cc:running\n")
        _real_open = builtins.open

        def fail_on_win(path, *a, **kw):
            if str(path) == str(tmp_path / "win-w2"):
                raise OSError("no win file")
            return _real_open(path, *a, **kw)

        with patch("sys.argv", ["ai", "internal", "iterm2-exit-cleanup", "w2t0", "0", "w2"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
                    with patch("builtins.open", side_effect=fail_on_win):
                        with patch("ai_cli.main._iterm2_compute_tab_title", return_value=""):
                            with pytest.raises(SystemExit) as exc:
                                cli()
        assert exc.value.code == 0  # OSError on win_file silently swallowed


# ---------------------------------------------------------------------------
# load_project_registry — caching and validation
# ---------------------------------------------------------------------------


class TestLoadProjectRegistry:
    def test_load_project_registry_when_valid_then_returns_projects(self, tmp_path):
        registry = tmp_path / "sergei.toml"
        registry.write_bytes(b'[[projects]]\nname = "app"\ntask_prefix = "APP"\n')
        with patch("ai_cli.main._get_project_registry_path", return_value=registry):
            result = load_project_registry(_force=True)
        assert len(result) == 1
        assert result[0]["name"] == "app"

    def test_load_project_registry_when_cached_then_returns_same(self, tmp_path):
        registry = tmp_path / "sergei.toml"
        registry.write_bytes(b'[[projects]]\nname = "app"\ntask_prefix = "APP"\n')
        with patch("ai_cli.main._get_project_registry_path", return_value=registry):
            r1 = load_project_registry(_force=True)
            r2 = load_project_registry()
        assert r1 is r2

    def test_load_project_registry_when_no_registry_then_returns_empty(self):
        with patch("ai_cli.main._get_project_registry_path", return_value=None):
            result = load_project_registry(_force=True)
        assert result == []

    def test_load_project_registry_when_missing_name_then_exits(self, tmp_path):
        registry = tmp_path / "sergei.toml"
        registry.write_bytes(b'[[projects]]\ntask_prefix = "APP"\n')
        with patch("ai_cli.main._get_project_registry_path", return_value=registry):
            with pytest.raises(SystemExit) as exc:
                load_project_registry(_force=True)
        assert exc.value.code == 1

    def test_load_project_registry_when_missing_task_prefix_then_exits(self, tmp_path):
        registry = tmp_path / "sergei.toml"
        registry.write_bytes(b'[[projects]]\nname = "app"\n')
        with patch("ai_cli.main._get_project_registry_path", return_value=registry):
            with pytest.raises(SystemExit) as exc:
                load_project_registry(_force=True)
        assert exc.value.code == 1

    def test_load_project_registry_when_duplicate_name_then_exits(self, tmp_path):
        registry = tmp_path / "sergei.toml"
        registry.write_bytes(
            b'[[projects]]\nname = "app"\ntask_prefix = "A1"\n[[projects]]\nname = "app"\ntask_prefix = "A2"\n'
        )
        with patch("ai_cli.main._get_project_registry_path", return_value=registry):
            with pytest.raises(SystemExit) as exc:
                load_project_registry(_force=True)
        assert exc.value.code == 1

    def test_load_project_registry_when_duplicate_prefix_then_exits(self, tmp_path):
        registry = tmp_path / "sergei.toml"
        registry.write_bytes(
            b'[[projects]]\nname = "app1"\ntask_prefix = "AP"\n[[projects]]\nname = "app2"\ntask_prefix = "AP"\n'
        )
        with patch("ai_cli.main._get_project_registry_path", return_value=registry):
            with pytest.raises(SystemExit) as exc:
                load_project_registry(_force=True)
        assert exc.value.code == 1

    def test_load_project_registry_when_toml_parse_error_then_returns_empty(self, tmp_path):
        registry = tmp_path / "sergei.toml"
        registry.write_bytes(b"not valid toml {{{{")
        with patch("ai_cli.main._get_project_registry_path", return_value=registry):
            result = load_project_registry(_force=True)
        assert result == []


# ---------------------------------------------------------------------------
# validate_registry_completeness
# ---------------------------------------------------------------------------


class TestValidateRegistryCompleteness:
    def test_validate_when_no_registry_then_returns_true(self):
        with patch("ai_cli.main._get_project_registry_path", return_value=None):
            assert validate_registry_completeness(interactive=False) is True

    def test_validate_when_all_registered_then_returns_true(self, tmp_path):
        registry = tmp_path / "sergei.toml"
        registry.write_bytes(b'[[projects]]\nname = "app"\ntask_prefix = "APP"\n')
        projects_dir = tmp_path / "projects"
        (projects_dir / "app").mkdir(parents=True)
        with (
            patch("ai_cli.main._get_project_registry_path", return_value=registry),
            patch("ai_cli.main._get_projects_dir", return_value=projects_dir),
        ):
            assert validate_registry_completeness(interactive=False) is True

    def test_validate_when_unregistered_noninteractive_then_returns_false(self, tmp_path):
        registry = tmp_path / "sergei.toml"
        registry.write_bytes(b'[[projects]]\nname = "app"\ntask_prefix = "APP"\n')
        projects_dir = tmp_path / "projects"
        (projects_dir / "app").mkdir(parents=True)
        (projects_dir / "newapp").mkdir(parents=True)
        with (
            patch("ai_cli.main._get_project_registry_path", return_value=registry),
            patch("ai_cli.main._get_projects_dir", return_value=projects_dir),
        ):
            assert validate_registry_completeness(interactive=False) is False

    def test_validate_when_user_registers_then_returns_true(self, tmp_path):
        registry = tmp_path / "sergei.toml"
        registry.write_bytes(b'[[projects]]\nname = "app"\ntask_prefix = "APP"\n')
        projects_dir = tmp_path / "projects"
        (projects_dir / "app").mkdir(parents=True)
        (projects_dir / "newapp").mkdir(parents=True)
        with (
            patch("ai_cli.main._get_project_registry_path", return_value=registry),
            patch("ai_cli.main._get_projects_dir", return_value=projects_dir),
            patch("builtins.input", return_value="y"),
        ):
            assert validate_registry_completeness(interactive=True) is True
        # Check entry was appended to TOML
        content = registry.read_text()
        assert "newapp" in content

    def test_validate_when_user_declines_then_returns_false(self, tmp_path):
        registry = tmp_path / "sergei.toml"
        registry.write_bytes(b'[[projects]]\nname = "app"\ntask_prefix = "APP"\n')
        projects_dir = tmp_path / "projects"
        (projects_dir / "app").mkdir(parents=True)
        (projects_dir / "newapp").mkdir(parents=True)
        with (
            patch("ai_cli.main._get_project_registry_path", return_value=registry),
            patch("ai_cli.main._get_projects_dir", return_value=projects_dir),
            patch("builtins.input", return_value="n"),
        ):
            assert validate_registry_completeness(interactive=True) is False

    def test_validate_when_eof_then_returns_false(self, tmp_path):
        registry = tmp_path / "sergei.toml"
        registry.write_bytes(b'[[projects]]\nname = "app"\ntask_prefix = "APP"\n')
        projects_dir = tmp_path / "projects"
        (projects_dir / "app").mkdir(parents=True)
        (projects_dir / "newapp").mkdir(parents=True)
        with (
            patch("ai_cli.main._get_project_registry_path", return_value=registry),
            patch("ai_cli.main._get_projects_dir", return_value=projects_dir),
            patch("builtins.input", side_effect=EOFError),
        ):
            assert validate_registry_completeness(interactive=True) is False

    def test_validate_skips_hidden_dirs(self, tmp_path):
        registry = tmp_path / "sergei.toml"
        registry.write_bytes(b'[[projects]]\nname = "app"\ntask_prefix = "APP"\n')
        projects_dir = tmp_path / "projects"
        (projects_dir / "app").mkdir(parents=True)
        (projects_dir / ".hidden").mkdir(parents=True)
        with (
            patch("ai_cli.main._get_project_registry_path", return_value=registry),
            patch("ai_cli.main._get_projects_dir", return_value=projects_dir),
        ):
            assert validate_registry_completeness(interactive=False) is True

    def test_validate_when_custom_prefix_then_uses_it(self, tmp_path):
        registry = tmp_path / "sergei.toml"
        registry.write_bytes(b'[[projects]]\nname = "app"\ntask_prefix = "APP"\n')
        projects_dir = tmp_path / "projects"
        (projects_dir / "app").mkdir(parents=True)
        (projects_dir / "newapp").mkdir(parents=True)
        with (
            patch("ai_cli.main._get_project_registry_path", return_value=registry),
            patch("ai_cli.main._get_projects_dir", return_value=projects_dir),
            patch("builtins.input", return_value="CUSTOM"),
        ):
            assert validate_registry_completeness(interactive=True) is True
        content = registry.read_text()
        assert "CUSTOM" in content

    def test_validate_when_projects_dir_missing_then_returns_true(self, tmp_path):
        registry = tmp_path / "sergei.toml"
        registry.write_bytes(b'[[projects]]\nname = "app"\ntask_prefix = "APP"\n')
        nonexistent = tmp_path / "nonexistent"
        with (
            patch("ai_cli.main._get_project_registry_path", return_value=registry),
            patch("ai_cli.main._get_projects_dir", return_value=nonexistent),
        ):
            assert validate_registry_completeness(interactive=False) is True


# ---------------------------------------------------------------------------
# _log_handoff_event
# ---------------------------------------------------------------------------


class TestLogHandoffEvent:
    def test_log_event_when_called_then_writes_jsonl(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        with patch("ai_cli.main.get_xdg_state_home", return_value=state_dir):
            _log_handoff_event("handoff.posted", handoff_id=1, project="app")
        log_file = state_dir / "handoff-events.jsonl"
        assert log_file.exists()
        entry = json.loads(log_file.read_text().strip())
        assert entry["event"] == "handoff.posted"
        assert entry["handoff_id"] == 1
        assert "ts" in entry

    def test_log_event_when_dir_missing_then_creates_it(self, tmp_path):
        state_dir = tmp_path / "deep" / "state"
        with patch("ai_cli.main.get_xdg_state_home", return_value=state_dir):
            _log_handoff_event("handoff.claimed", session="c-sw-1")
        assert (state_dir / "handoff-events.jsonl").exists()

    def test_log_event_appends_multiple(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        with patch("ai_cli.main.get_xdg_state_home", return_value=state_dir):
            _log_handoff_event("handoff.posted", handoff_id=1)
            _log_handoff_event("handoff.claimed", handoff_id=1)
        lines = (state_dir / "handoff-events.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# post_handoff — event logging
# ---------------------------------------------------------------------------


class TestPostHandoffEventLogging:
    def test_post_handoff_logs_event(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        with (
            patch("ai_cli.main._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.main.get_xdg_state_home", return_value=state_dir),
            patch.dict(os.environ, {"AI_TMUX_SESSION": "c-sw-1"}),
        ):
            post_handoff("Test task", "P1", "myapp", "Do this thing")
        log_file = state_dir / "handoff-events.jsonl"
        assert log_file.exists()
        entry = json.loads(log_file.read_text().strip())
        assert entry["event"] == "handoff.posted"
        assert entry["title"] == "Test task"


# ---------------------------------------------------------------------------
# signal-watch — event logging and actionable nudge
# ---------------------------------------------------------------------------


class TestSignalWatchEventLogging:
    def test_signal_watch_when_claim_succeeds_then_logs_claimed_event(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        # Don't pre-create the file — use cross-machine delivery payload so the file
        # is created by _on_handoff itself (avoids startup scan claiming it first)
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js
        received_callback = {}

        async def fake_js_subscribe(subject, durable, cb):
            received_callback["cb"] = cb

        mock_js.subscribe = fake_js_subscribe
        file_content = '---\nid: "5"\ntitle: "test task"\nclaimed_by: null\nclaimed_at: null\n---\n'

        async def fake_sleep(_):
            if "cb" in received_callback:
                msg = MagicMock()
                msg.data = json.dumps(
                    {
                        "id": 5,
                        "title": "test task",
                        "priority": "P1",
                        "message": "do it",
                        "content": file_content,
                        "filename": "005-test-task.md",
                    }
                ).encode()
                msg.ack = AsyncMock()
                await received_callback["cb"](msg)
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-5"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.main.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run"),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        log_file = state_dir / "handoff-events.jsonl"
        assert log_file.exists()
        events = [json.loads(l) for l in log_file.read_text().strip().split("\n")]
        claimed = [e for e in events if e["event"] == "handoff.claimed"]
        assert len(claimed) == 1
        assert claimed[0]["handoff_id"] == 5
        assert claimed[0]["layer"] == "nats_realtime"

    def test_signal_watch_when_idle_pane_then_sends_actionable_nudge(self, tmp_path):
        """Nudge sends actual message (not empty string) when pane is idle."""
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        # Use cross-machine payload to avoid startup scan claiming first
        file_content = '---\nid: "6"\ntitle: "nudge me"\nclaimed_by: null\nclaimed_at: null\n---\n'
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js
        received_callback = {}

        async def fake_js_subscribe(subject, durable, cb):
            received_callback["cb"] = cb

        mock_js.subscribe = fake_js_subscribe
        send_keys_calls = []

        def fake_subprocess_run(cmd, **kwargs):
            result = MagicMock()
            if "display-message" in cmd:
                result.returncode = 0
                result.stdout = "bash\n"
            else:
                send_keys_calls.append(cmd)
                result.returncode = 0
            return result

        async def fake_sleep(_):
            if "cb" in received_callback:
                msg = MagicMock()
                msg.data = json.dumps(
                    {
                        "id": 6,
                        "title": "nudge me",
                        "priority": "P1",
                        "message": "do it",
                        "content": file_content,
                        "filename": "006-nudge-task.md",
                    }
                ).encode()
                msg.ack = AsyncMock()
                await received_callback["cb"](msg)
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-6"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.main.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run", side_effect=fake_subprocess_run),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        # Verify send-keys sent actionable message (not empty)
        nudge_calls = [c for c in send_keys_calls if "send-keys" in c]
        assert len(nudge_calls) == 1
        # The message should contain task title and be actionable
        assert "nudge me" in nudge_calls[0][4]
        assert "Enter" in nudge_calls[0]

        # Verify nudge event logged
        log_file = state_dir / "handoff-events.jsonl"
        events = [json.loads(l) for l in log_file.read_text().strip().split("\n")]
        nudge_events = [e for e in events if e["event"] == "handoff.nudge_sent"]
        assert len(nudge_events) == 1
        assert nudge_events[0]["pane_state"] == "bash"

    def test_signal_watch_startup_scan_logs_as_startup_scan_layer(self, tmp_path):
        """Startup scan claims should be logged with layer=startup_scan."""
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "007-scan-task.md").write_text(
            '---\nid: "7"\ntitle: "scan task"\npriority: P2\nclaimed_by: null\nclaimed_at: null\n---\n\nDo this.\n'
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js

        async def fake_js_subscribe(subject, durable, cb):
            pass

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-7"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.main.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run"),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        log_file = state_dir / "handoff-events.jsonl"
        events = [json.loads(l) for l in log_file.read_text().strip().split("\n")]
        claimed = [e for e in events if e["event"] == "handoff.claimed"]
        assert len(claimed) == 1
        assert claimed[0]["layer"] == "startup_scan"


# ---------------------------------------------------------------------------
# get_engine_script — project_name in template
# ---------------------------------------------------------------------------


class TestEngineScriptProjectName:
    def test_get_engine_script_when_project_name_set_then_included_in_template(self):
        script = get_engine_script(
            "c",
            "sw-1",
            "c-sw-1",
            "c-sw-",
            "sw",
            project_name="ai-cli-utils",
        )
        assert 'project_name="ai-cli-utils"' in script

    def test_get_engine_script_when_project_name_empty_then_signal_watch_not_started(self):
        script = get_engine_script(
            "c",
            "sw-1",
            "c-sw-1",
            "c-sw-",
            "sw",
            project_name="",
        )
        assert 'project_name=""' in script

    def test_get_engine_script_signal_watch_uses_project_name(self):
        script = get_engine_script(
            "c",
            "sw-1",
            "c-sw-1",
            "c-sw-",
            "sw",
            project_name="my-project",
        )
        assert 'ai signal-watch start "$project_name"' in script

    def test_get_engine_script_exit_trap_cleans_caught_file(self):
        script = get_engine_script(
            "c",
            "sw-1",
            "c-sw-1",
            "c-sw-",
            "sw",
            project_name="app",
        )
        assert "handoff-caught-$tmux_session" in script

    def test_get_engine_script_while_loop_logs_handoff_pickup(self):
        script = get_engine_script(
            "c",
            "sw-1",
            "c-sw-1",
            "c-sw-",
            "sw",
            project_name="app",
        )
        assert "handoff.while_loop_pickup" in script


# ---------------------------------------------------------------------------
# CLI — registry validation on session launch
# ---------------------------------------------------------------------------


class TestCliRegistryValidation:
    def test_cli_when_registry_incomplete_noninteractive_then_exits(self, tmp_path):
        registry = tmp_path / "sergei.toml"
        registry.write_bytes(b'[[projects]]\nname = "app"\ntask_prefix = "APP"\n')
        projects_dir = tmp_path / "projects"
        (projects_dir / "app").mkdir(parents=True)
        (projects_dir / "unregistered").mkdir(parents=True)

        with (
            patch("sys.argv", ["ai", "c", "1"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main.get_project_prefix", return_value="app"),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.main._get_project_registry_path", return_value=registry),
            patch("ai_cli.main._get_projects_dir", return_value=projects_dir),
            patch("sys.stdin", MagicMock(isatty=MagicMock(return_value=False))),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 1


# ---------------------------------------------------------------------------
# _find_best_handoff — project filtering
# ---------------------------------------------------------------------------


class TestFindBestHandoff:
    def test_find_best_handoff_when_no_filter_returns_highest_priority(self, tmp_path):
        queue_dir = tmp_path / "pending"
        queue_dir.mkdir()
        (queue_dir / "001-low.md").write_text("---\npriority: P3\nproject: app\n---\n")
        (queue_dir / "002-high.md").write_text("---\npriority: P1\nproject: other\n---\n")
        result = _find_best_handoff(queue_dir)
        assert result is not None
        assert result.name == "002-high.md"

    def test_find_best_handoff_when_project_filter_returns_only_matching(self, tmp_path):
        queue_dir = tmp_path / "pending"
        queue_dir.mkdir()
        (queue_dir / "001-other.md").write_text("---\npriority: P0\nproject: other\n---\n")
        (queue_dir / "002-mine.md").write_text("---\npriority: P2\nproject: myapp\n---\n")
        result = _find_best_handoff(queue_dir, project_filter="myapp")
        assert result is not None
        assert result.name == "002-mine.md"

    def test_find_best_handoff_when_no_match_for_project_returns_none(self, tmp_path):
        queue_dir = tmp_path / "pending"
        queue_dir.mkdir()
        (queue_dir / "001-other.md").write_text("---\npriority: P1\nproject: other\n---\n")
        result = _find_best_handoff(queue_dir, project_filter="myapp")
        assert result is None

    def test_find_best_handoff_when_dir_missing_returns_none(self, tmp_path):
        result = _find_best_handoff(tmp_path / "nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# check_handoff_project
# ---------------------------------------------------------------------------


class TestCheckHandoffProject:
    def test_check_handoff_project_when_match_then_prints_path(self, tmp_path, capsys):
        handoff_dir = tmp_path / ".handoff-queue"
        queue_dir = handoff_dir / "pending"
        queue_dir.mkdir(parents=True)
        (queue_dir / "001-task.md").write_text("---\npriority: P1\nproject: myapp\n---\n")
        with patch("ai_cli.main._get_handoff_queue_dir", return_value=handoff_dir):
            check_handoff_project("myapp")
        out = capsys.readouterr().out.strip()
        assert "001-task.md" in out

    def test_check_handoff_project_when_no_match_then_silent(self, tmp_path, capsys):
        handoff_dir = tmp_path / ".handoff-queue"
        queue_dir = handoff_dir / "pending"
        queue_dir.mkdir(parents=True)
        (queue_dir / "001-task.md").write_text("---\npriority: P1\nproject: other\n---\n")
        with patch("ai_cli.main._get_handoff_queue_dir", return_value=handoff_dir):
            check_handoff_project("myapp")
        assert capsys.readouterr().out == ""

    def test_check_handoff_project_when_no_handoff_dir_then_returns(self, capsys):
        with patch("ai_cli.main._get_handoff_queue_dir", return_value=None):
            check_handoff_project("myapp")
        assert capsys.readouterr().out == ""

    def test_cli_when_handoff_check_project_then_calls_function(self, capsys):
        with (
            patch("sys.argv", ["ai", "handoff", "check-project", "myapp"]),
            patch("ai_cli.main.check_handoff_project") as mock_check,
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0
        mock_check.assert_called_once_with("myapp")

    def test_cli_when_handoff_check_project_no_args_then_exits(self):
        with patch("sys.argv", ["ai", "handoff", "check-project"]):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 1


# ---------------------------------------------------------------------------
# bash template — startup handoff pickup before first CC launch
# ---------------------------------------------------------------------------


class TestEngineScriptStartupHandoffPickup:
    def test_get_engine_script_includes_startup_handoff_drain(self):
        script = get_engine_script(
            "c",
            "ai-cli-2",
            "c-ai-cli-2",
            "c-ai-cli-",
            "ai",
            project_name="ai-cli-utils",
        )
        assert "ai internal handoff-drain" in script

    def test_get_engine_script_startup_drain_only_on_first_run(self):
        script = get_engine_script(
            "c",
            "ai-cli-2",
            "c-ai-cli-2",
            "c-ai-cli-",
            "ai",
            project_name="ai-cli-utils",
        )
        assert "$first_run" in script

    def test_get_engine_script_startup_drain_uses_project_name(self):
        script = get_engine_script(
            "c",
            "sw-1",
            "c-sw-1",
            "c-sw-",
            "sw",
            project_name="myapp",
        )
        assert 'handoff-drain "$project_name"' in script

    def test_get_engine_script_startup_check_guarded_by_engine(self):
        # The startup handoff check is in the template for all engines but guarded
        # by a bash [[ "$engine" == "c" ]] condition so it only runs for cc sessions
        script = get_engine_script(
            "c",
            "sw-1",
            "c-sw-1",
            "c-sw-",
            "sw",
            project_name="myapp",
        )
        assert '"c"' in script  # engine guard present in template


# ---------------------------------------------------------------------------
# ai internal handoff-drain
# ---------------------------------------------------------------------------


class TestHandoffDrain:
    def test_handoff_drain_when_local_file_exists_then_claims_and_writes_prompt_file(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "001-task.md").write_text(
            '---\nid: "1"\ntitle: "local task"\npriority: P1\nproject: myapp\nclaimed_by: null\nclaimed_at: null\n---\n\nDo this.\n'
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        with (
            patch("sys.argv", ["ai", "internal", "handoff-drain", "myapp", "c-sw-1"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.main.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", side_effect=Exception("no nats")),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        prompt_file = state_dir / "cc-resume-prompt-c-sw-1"
        assert prompt_file.exists()
        assert "local task" in prompt_file.read_text()
        assert not (pending / "001-task.md").exists()

    def test_handoff_drain_when_no_local_file_and_nats_unavailable_then_exits_cleanly(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        (handoff_dir / "pending").mkdir(parents=True)
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        with (
            patch("sys.argv", ["ai", "internal", "handoff-drain", "myapp", "c-sw-1"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.main.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", side_effect=Exception("no nats")),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        assert not (state_dir / "cc-resume-prompt-c-sw-1").exists()

    def test_handoff_drain_when_too_few_args_then_exits_cleanly(self):
        with patch("sys.argv", ["ai", "internal", "handoff-drain"]):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

    def test_handoff_drain_filters_by_project(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "001-wrong.md").write_text(
            '---\nid: "1"\ntitle: "wrong project"\npriority: P0\nproject: other\nclaimed_by: null\nclaimed_at: null\n---\n\nSkip me.\n'
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        with (
            patch("sys.argv", ["ai", "internal", "handoff-drain", "myapp", "c-sw-1"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.main.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", side_effect=Exception("no nats")),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        # Task for "other" project should NOT be claimed by "myapp" session
        assert not (state_dir / "cc-resume-prompt-c-sw-1").exists()
        assert (pending / "001-wrong.md").exists()

    def test_handoff_drain_logs_claimed_event(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "002-task.md").write_text(
            '---\nid: "2"\ntitle: "log task"\npriority: P1\nproject: myapp\nclaimed_by: null\nclaimed_at: null\n---\n\nDo it.\n'
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        with (
            patch("sys.argv", ["ai", "internal", "handoff-drain", "myapp", "c-sw-2"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.main.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", side_effect=Exception("no nats")),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        log = state_dir / "handoff-events.jsonl"
        assert log.exists()
        events = [json.loads(l) for l in log.read_text().strip().split("\n")]
        claimed = [e for e in events if e["event"] == "handoff.claimed"]
        assert any(e["layer"] == "pre_launch_drain" for e in claimed)


# --- Circus / signal-watch tests ---


class TestSignalWatchCircus:
    def _mock_client(self, status_response=None):
        """Return a MagicMock CircusClient that can send_message."""
        client = MagicMock()
        if status_response is not None:
            client.send_message.return_value = status_response
        return client

    def test_ensure_circusd_when_already_running_then_no_popen(self, tmp_path):
        client = self._mock_client()
        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("circus.client.CircusClient", return_value=client),
            patch("subprocess.Popen") as mock_popen,
        ):
            endpoint = _ensure_circusd()
        assert endpoint == f"ipc://{tmp_path}/circus.endpoint"
        mock_popen.assert_not_called()

    def test_ensure_circusd_when_not_running_then_starts_daemon_and_writes_ini(self, tmp_path):
        call_count = 0

        def client_factory(endpoint, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionRefusedError("not running")
            return self._mock_client()

        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("circus.client.CircusClient", side_effect=client_factory),
            patch("subprocess.Popen") as mock_popen,
            patch("shutil.which", return_value="/usr/bin/circusd"),
            patch("time.sleep"),
        ):
            endpoint = _ensure_circusd()

        assert endpoint == f"ipc://{tmp_path}/circus.endpoint"
        mock_popen.assert_called_once()
        popen_args = mock_popen.call_args[0][0]
        assert "--daemon" in popen_args
        assert str(tmp_path / "circus.ini") in popen_args
        assert (tmp_path / "circus.ini").exists()
        ini = (tmp_path / "circus.ini").read_text()
        assert "pubsub_endpoint" in ini

    def test_cmd_signal_watch_start_registers_watcher_with_copy_env(self, tmp_path):
        client = self._mock_client()
        with (
            patch("ai_cli.main._ensure_circusd", return_value=f"ipc://{tmp_path}/circus.endpoint"),
            patch("circus.client.CircusClient", return_value=client),
            patch("shutil.which", return_value="/usr/bin/ai"),
        ):
            _cmd_signal_watch_start("myproject", "c-sw-1")

        add_call = next(c for c in client.send_message.call_args_list if c[0][0] == "add")
        options = add_call[1]["options"]
        assert options["copy_env"] is True
        assert add_call[1]["start"] is True

    def test_cmd_signal_watch_start_idempotent_on_second_call(self, tmp_path):
        client = self._mock_client()
        # First rm raises (watcher not found) — must be swallowed
        client.send_message.side_effect = [Exception("not found"), None]
        with (
            patch("ai_cli.main._ensure_circusd", return_value=f"ipc://{tmp_path}/circus.endpoint"),
            patch("circus.client.CircusClient", return_value=client),
            patch("shutil.which", return_value="/usr/bin/ai"),
        ):
            _cmd_signal_watch_start("myproject", "c-sw-1")
        # add was still called after rm failed
        calls = [c[0][0] for c in client.send_message.call_args_list]
        assert "rm" in calls
        assert "add" in calls

    def test_cmd_signal_watch_stop_when_circusd_running(self, tmp_path):
        client = self._mock_client()
        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("circus.client.CircusClient", return_value=client),
        ):
            _cmd_signal_watch_stop("c-sw-1")
        client.send_message.assert_called_once_with("rm", name="sw-c-sw-1")

    def test_cmd_signal_watch_stop_when_circusd_not_running_then_silent(self, tmp_path):
        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("circus.client.CircusClient", side_effect=Exception("zmq error")),
        ):
            _cmd_signal_watch_stop("c-sw-1")  # must not raise

    def test_cmd_signal_watch_status_filters_sw_prefix(self, tmp_path, capsys):
        client = self._mock_client(
            status_response={"statuses": {"sw-c-sw-1": "active", "other-watcher": "active", "sw-c-sw-2": "stopped"}}
        )
        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("circus.client.CircusClient", return_value=client),
        ):
            _cmd_signal_watch_status()
        out = capsys.readouterr().out
        assert "c-sw-1" in out
        assert "c-sw-2" in out
        assert "other-watcher" not in out

    def test_cmd_signal_watch_status_when_circusd_not_running_then_message(self, tmp_path, capsys):
        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("circus.client.CircusClient", side_effect=Exception("zmq error")),
        ):
            _cmd_signal_watch_status()
        out = capsys.readouterr().out
        assert "not running" in out

    def test_cli_signal_watch_start_dispatches(self, tmp_path):
        with (
            patch("sys.argv", ["ai", "signal-watch", "start", "myproject", "c-sw-1"]),
            patch("ai_cli.main._cmd_signal_watch_start") as mock_start,
            patch("ai_cli.main.load_config", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0
        mock_start.assert_called_once_with("myproject", "c-sw-1")

    def test_cli_signal_watch_stop_dispatches(self, tmp_path):
        with (
            patch("sys.argv", ["ai", "signal-watch", "stop", "c-sw-1"]),
            patch("ai_cli.main._cmd_signal_watch_stop") as mock_stop,
            patch("ai_cli.main.load_config", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0
        mock_stop.assert_called_once_with("c-sw-1")

    def test_cli_signal_watch_missing_args_exits_1(self):
        with (
            patch("sys.argv", ["ai", "signal-watch"]),
            patch("ai_cli.main.load_config", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 1


# --- Tunnel tests ---

_TUNNEL_CONFIG = {"remote": {"host": "178.104.70.139", "user": "sergei"}}


class TestTunnel:
    def test_cmd_tunnel_start_when_autossh_found_then_launches_reverse_tunnel(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("shutil.which", return_value="/usr/bin/autossh"),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            _cmd_tunnel_start(9222, 9222, config=_TUNNEL_CONFIG)
        args = mock_popen.call_args[0][0]
        assert "-R" in args
        assert "9222:localhost:9222" in args
        assert (tmp_path / "tunnel-9222.pid").read_text() == "12345"

    def test_cmd_tunnel_start_when_forward_flag_then_uses_dash_L(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.pid = 99
        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("shutil.which", return_value="/usr/bin/autossh"),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            _cmd_tunnel_start(8080, 8080, forward=True, config=_TUNNEL_CONFIG)
        args = mock_popen.call_args[0][0]
        assert "-L" in args
        assert "-R" not in args

    def test_cmd_tunnel_start_when_remote_port_omitted_then_defaults_to_local_port(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.pid = 1
        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("shutil.which", return_value="/usr/bin/autossh"),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            _cmd_tunnel_start(5000, 5000, config=_TUNNEL_CONFIG)
        args = mock_popen.call_args[0][0]
        assert "5000:localhost:5000" in args

    def test_cmd_tunnel_start_when_autossh_missing_then_exits_1(self, tmp_path):
        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("shutil.which", return_value=None),
        ):
            with pytest.raises(SystemExit) as exc:
                _cmd_tunnel_start(9222, 9222, config=_TUNNEL_CONFIG)
            assert exc.value.code == 1

    def test_cmd_tunnel_stop_when_pid_file_exists_then_kills_and_removes(self, tmp_path):
        (tmp_path / "tunnel-9222.pid").write_text("5678")
        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("os.kill") as mock_kill,
        ):
            _cmd_tunnel_stop(9222)
        mock_kill.assert_called_once_with(5678, 15)
        assert not (tmp_path / "tunnel-9222.pid").exists()

    def test_cmd_tunnel_stop_when_no_pid_file_then_silent(self, tmp_path):
        with patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path):
            _cmd_tunnel_stop(9222)  # must not raise

    def test_cmd_tunnel_status_lists_active_tunnels(self, tmp_path, capsys):
        (tmp_path / "tunnel-9222.pid").write_text("4242")
        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("os.kill"),  # process exists (no exception)
        ):
            _cmd_tunnel_status()
        out = capsys.readouterr().out
        assert "9222" in out
        assert "4242" in out
        assert "alive" in out

    def test_cli_tunnel_start_dispatches(self, tmp_path):
        with (
            patch("sys.argv", ["ai", "tunnel", "start", "9222"]),
            patch("ai_cli.main._cmd_tunnel_start") as mock_start,
            patch("ai_cli.main.load_config", return_value=_TUNNEL_CONFIG),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0
        mock_start.assert_called_once_with(9222, 9222, forward=False, config=_TUNNEL_CONFIG)

    def test_cli_tunnel_missing_args_exits_1(self):
        with (
            patch("sys.argv", ["ai", "tunnel", "start"]),
            patch("ai_cli.main.load_config", return_value=_TUNNEL_CONFIG),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 1
