import pytest
import sys
import time
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from ai_cli.main import build_session_name, cleanup_stale_sessions, _find_project_dir, get_current_project_name


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
