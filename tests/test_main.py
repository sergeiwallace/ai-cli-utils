import json
import os
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import make_iterm2_config, run_cli

from ai_cli.main import (
    _assign_iterm2_color_slot,
    _auto_update_if_stale,
    _bare_engine_command,
    _cmd_tunnel_start,
    _cmd_tunnel_status,
    _cmd_tunnel_stop,
    _find_aicli_project_path,
    _handle_internal,
    _installed_source_fingerprint,
    _load_iterm2_config,
    _migrate_xdg_dir,
    _release_iterm2_color_slot,
    _resolve_is_remote,
    _sweep_stale_iterm2_profiles,
    cli,
    get_engine_script,
    get_xdg_cache_home,
    get_xdg_state_home,
    load_config,
)
from ai_cli.session_script import resolve_session_shell

# --- XDG helpers ---


class TestXdgHelpers:
    def test_get_xdg_state_home_when_env_var_set_then_uses_it(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setenv("XDG_STATE_HOME", "/custom/state")
        result = get_xdg_state_home()
        assert result == Path("/custom/state") / "ai-cli-utils"

    def test_get_xdg_state_home_when_no_env_var_then_uses_default(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        result = get_xdg_state_home()
        assert result.name == "ai-cli-utils"
        assert ".local/state" in result.as_posix()

    def test_get_xdg_cache_home_when_env_var_set_then_uses_it(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setenv("XDG_CACHE_HOME", "/custom/cache")
        result = get_xdg_cache_home()
        assert result == Path("/custom/cache") / "ai-cli-utils"

    def test_get_xdg_cache_home_when_no_env_var_then_uses_default(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        result = get_xdg_cache_home()
        assert result.name == "ai-cli-utils"
        assert ".cache" in result.as_posix()


# --- load_config tests ---


class TestLoadConfig:
    def test_load_config_when_no_config_file_then_creates_default_with_known_keys(self, tmp_path):
        config_dir = tmp_path / "ai-cli-utils"
        with patch("ai_cli.config.get_xdg_config_home", return_value=config_dir):
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
        with patch("ai_cli.config.get_xdg_config_home", return_value=config_dir):
            result = load_config()
        assert result == {}


# --- Group 1: _migrate_xdg_dir ---


class TestMigrateXdgDir:
    def test_when_old_exists_and_new_does_not_then_renames(self, tmp_path):
        old_dir = tmp_path / "ai-cli"
        old_dir.mkdir()
        (old_dir / "somefile.txt").write_text("data")
        new_dir = tmp_path / "ai-cli-utils"
        result = _migrate_xdg_dir(old_dir, new_dir)
        assert result == new_dir
        assert new_dir.exists()
        assert not old_dir.exists()
        assert (new_dir / "somefile.txt").read_text() == "data"


# --- Group 2: sweep_stale_iterm2_profiles ---


class TestSweepStaleIterm2Profiles:
    def test_when_profile_dir_does_not_exist_then_returns(self, tmp_path):
        nonexistent = tmp_path / "no-such-dir"
        mock_ig = MagicMock()
        mock_ig._dynamic_profile_dir.return_value = nonexistent
        mock_ig._DYNAMIC_PROFILE_PREFIX = "ai-cli-"
        with patch.dict("sys.modules", {"ai_cli.icon_generator": mock_ig}):
            _sweep_stale_iterm2_profiles()

    def test_when_exception_occurs_then_suppressed(self):
        mock_ig = MagicMock()
        mock_ig._dynamic_profile_dir.side_effect = RuntimeError("broken")
        with patch.dict("sys.modules", {"ai_cli.icon_generator": mock_ig}):
            _sweep_stale_iterm2_profiles()


# --- Group 3: _load_iterm2_config ---


class TestLoadIterm2Config:
    def test_when_both_toml_parses_fail_then_returns_empty_dict(self, tmp_path):
        config_dir = tmp_path / "ai-cli-utils"
        config_dir.mkdir(parents=True)
        (config_dir / "iterm2.toml").write_text("valid = true")
        with (
            patch("ai_cli.config.get_xdg_config_home", return_value=config_dir),
            patch("tomllib.load", side_effect=Exception("parse fail")),
            patch("tomllib.loads", side_effect=Exception("parse fail")),
        ):
            result = _load_iterm2_config()
        assert result == {}


# --- Group 4: _assign_iterm2_color_slot ---


class TestAssignIterm2ColorSlot:
    def test_when_iterm2_disabled_via_config_then_returns_none(self):
        with (
            patch("ai_cli.iterm2._is_iterm2", return_value=True),
            patch(
                "ai_cli.iterm2._load_iterm2_config",
                return_value={"iterm2": {"enabled": False}},
            ),
        ):
            result = _assign_iterm2_color_slot("session-1", "c", "myproject")
        assert result is None

    def test_when_color_disabled_via_config_then_returns_none(self):
        with (
            patch("ai_cli.iterm2._is_iterm2", return_value=True),
            patch(
                "ai_cli.iterm2._load_iterm2_config",
                return_value={"iterm2": {"enabled": True, "color": {"enabled": False}}},
            ),
        ):
            result = _assign_iterm2_color_slot("session-1", "c", "myproject")
        assert result is None

    def test_when_palette_empty_then_returns_none(self):
        with (
            patch("ai_cli.iterm2._is_iterm2", return_value=True),
            patch(
                "ai_cli.iterm2._load_iterm2_config",
                return_value={
                    "iterm2": {
                        "enabled": True,
                        "color": {"enabled": True},
                        "palette": {},
                    }
                },
            ),
        ):
            result = _assign_iterm2_color_slot("session-1", "c", "myproject")
        assert result is None

    # Group 5: corrupt lease file
    def test_when_lease_file_is_corrupt_json_then_uses_empty_leases(self, tmp_path):
        state_dir = tmp_path / "iterm2"
        state_dir.mkdir(parents=True)
        (state_dir / "color-leases.json").write_text("CORRUPT")
        cfg = make_iterm2_config(palette={"red": "#e74c3c", "blue": "#1e88e5"})
        with (
            patch("ai_cli.iterm2._is_iterm2", return_value=True),
            patch("ai_cli.iterm2._iterm2_state_dir", return_value=state_dir),
            patch("ai_cli.iterm2._load_iterm2_config", return_value=cfg),
        ):
            result = _assign_iterm2_color_slot("session-1", "c", "myproject")
        assert result is not None
        assert len(result) == 6  # hex color without #


# --- Group 6: _release_iterm2_color_slot ---


class TestReleaseIterm2ColorSlot:
    def test_when_lease_file_is_corrupt_then_handles_gracefully(self, tmp_path):
        state_dir = tmp_path / "iterm2"
        state_dir.mkdir(parents=True)
        (state_dir / "color-leases.json").write_text("CORRUPT")
        (state_dir / "color-leases.lock").touch()
        with patch("ai_cli.iterm2._iterm2_state_dir", return_value=state_dir):
            _release_iterm2_color_slot("session-1")
        # Should have written a valid JSON file after handling corrupt input
        data = json.loads((state_dir / "color-leases.json").read_text())
        assert "leases" in data


# --- Group 7: get_engine_script pkg_version exception ---


class TestGetEngineScript:
    def test_when_pkg_version_raises_then_uses_unknown(self):
        with patch(
            "importlib.metadata.version",
            side_effect=Exception("not found"),
        ):
            result = get_engine_script(
                engine="c",
                ai_name="sw-1",
                session="c-sw-1",
                prefix="c-sw-",
                project_prefix="sw",
                session_id_uuid="",
                sandbox=False,
                notify=False,
                project_name="myproject",
            )
        assert "unknown" in result

    def test_given_no_worktree_when_generating_script_then_all_agent_starts_reuse_direnv_environment(self):
        script = get_engine_script(
            engine="c",
            ai_name="session-1",
            session="c-session-1",
            prefix="c-session-",
            project_prefix="session",
        )

        assert 'direnv_root="$PWD"' in script
        assert "agent_direnv_initialized=false" in script
        assert '"$(direnv export bash)"' in script
        assert 'eval "$_direnv_exports"' in script
        assert 'direnv exec "$direnv_root" "$@"' not in script
        # Prompt and regular launches each have exact-match and fresh-session paths.
        assert script.count("run_agent claude") == 4
        assert "--resume" in script
        # Hot-reload must exec an interpreter that exists here — a hardcoded one
        # that does not kills the pane on the session's first self-update.
        _shell = resolve_session_shell()
        assert _shell is not None and os.access(_shell, os.X_OK)
        assert '"$_supervisor_script" --ai-cli-child-body <&0 &' in script
        assert '"$_supervisor_script" --ai-cli-child-body &' not in script
        assert "acquire-generation-lease" in script
        assert "exit 78" in script
        assert f'exec "{_shell}" "$_script_stable_path"' not in script
        assert "starting without the project environment" in script

    def test_given_direnv_cannot_load_when_agent_starts_then_script_continues_without_project_environment(self):
        script = get_engine_script(
            engine="c",
            ai_name="session-1",
            session="c-session-1",
            prefix="c-session-",
            project_prefix="session",
            worktree_dir="/tmp/project-worktree",
        )

        assert "agent_direnv_initialized=false" in script
        assert 'if _direnv_exports="$(direnv export bash)"; then' in script
        assert 'eval "$_direnv_exports"' in script
        # The main agent invocation's stdio remains untouched — it is a long-running
        # interactive process, so only the one-time export is captured.
        assert '"$@" &\n' in script
        assert 'wait "$active_agent_pid"' in script
        assert "Warning: direnv could not load $direnv_root/.envrc" in script
        assert 'direnv exec "$direnv_root" "$@"' not in script

    def test_given_worktree_when_generating_script_then_direnv_uses_worktree_cwd(self):
        script = get_engine_script(
            engine="g",
            ai_name="session-1",
            session="g-session-1",
            prefix="g-session-",
            project_prefix="session",
            worktree_dir="/tmp/project-worktree",
            is_remote=True,
        )

        assert "cd /tmp/project-worktree" in script
        assert script.index('direnv_root="$PWD"') > script.index("cd /tmp/project-worktree")
        assert script.count("run_agent gemini") == 4
        assert '"$SHELL"; exit 79' in script

    def test_given_pi_engine_when_generating_script_then_launches_named_pi_session(self):
        script = get_engine_script(
            engine="p",
            ai_name="myproject-1",
            session="p-myproject-1",
            prefix="p-myproject-",
            project_prefix="myproject",
        )

        assert 'export PI_TMUX_SESSION="$tmux_session"' in script
        assert 'run_agent pi --name "$ai_name"' in script
        assert 'run_agent pi --continue --name "$ai_name"' in script
        assert '[[ "$engine" == "p" ]] && export CLAUDE_CODE_TASK_LIST_ID' not in script

    def test_given_codex_engine_when_generating_script_then_resumes_last_worktree_session(self):
        script = get_engine_script(
            engine="cx",
            ai_name="myproject-1",
            session="cx-myproject-1",
            prefix="cx-myproject-",
            project_prefix="myproject",
        )

        assert 'export CX_TMUX_SESSION="$tmux_session"' in script
        assert "if $first_run; then run_agent codex" in script
        assert "else run_agent codex resume --last" in script

    def _make_script(self, **kwargs):
        defaults = {
            "engine": "c",
            "ai_name": "session-1",
            "session": "c-session-1",
            "prefix": "c-session-",
            "project_prefix": "session",
            "session_id_uuid": "",
            "sandbox": False,
            "notify": False,
            "project_name": "myproject",
        }
        defaults.update(kwargs)
        return get_engine_script(**defaults)

    def test_ps_cron_runs_at_session_start(self):
        """Auto-hygiene must run at session start via 'ai ps cron'."""
        script = self._make_script()
        assert "ai ps cron" in script

    def test_stale_signal_files_cleaned_on_session_start(self):
        """Stale signal_file/config_changed_file from a previous killed session
        must be removed at startup — otherwise the watcher immediately injects
        /exit while CC is showing its startup UI (conversation rewind options)."""
        script = self._make_script()
        assert 'rm -f "$signal_file" "$config_changed_file"' in script

    def test_signal_file_processing_uses_correct_prompt_character(self):
        """The signal_file idle check must use CC's actual prompt character (❯),
        not '>'. Using '>' caused injection to fire in all states because the
        pattern never matched CC's prompt, triggering the rewind menu via Escape."""
        script = self._make_script()
        # Positive idle check: inject ONLY when at idle empty prompt
        assert "❯" in script
        assert "'^[[:space:]]*❯[[:space:]]*$'" in script

    def test_signal_file_injection_does_not_send_escape(self):
        """The injection sequence must not send Escape before /exit.
        Escape at an empty CC prompt triggers the conversation rewind menu."""
        script = self._make_script()
        sig_block_start = script.find('if [[ -f "$signal_file" ]];')
        sig_block_end = script.find("'/exit' C-m", sig_block_start)
        assert sig_block_start != -1
        assert sig_block_end != -1
        injection_sequence = script[sig_block_start:sig_block_end]
        assert 'send-keys -t "$tmux_session" Escape' not in injection_sequence

    def test_signal_file_injection_does_not_send_ctrl_u(self):
        """C-u must not be sent before /exit. CC uses React/Ink TUI — C-u behavior
        is undocumented and potentially harmful. The idle guard already confirms
        the prompt is empty, making C-u redundant."""
        script = self._make_script()
        sig_block_start = script.find('if [[ -f "$signal_file" ]];')
        sig_block_end = script.find("'/exit' C-m", sig_block_start)
        assert sig_block_start != -1
        assert sig_block_end != -1
        injection_sequence = script[sig_block_start:sig_block_end]
        assert 'send-keys -t "$tmux_session" C-u' not in injection_sequence

    def test_signal_file_injection_has_startup_grace_period(self):
        """Injection must be skipped during the first 10 watcher cycles (10s).
        When CC restarts with --continue, the pane still shows the previous
        conversation's ❯ for 1-3s during startup. Without a grace period the
        watcher fires into CC's initialization TUI, triggering the rewind menu."""
        script = self._make_script()
        sig_block_start = script.find('if [[ -f "$signal_file" ]];')
        sig_block_end = script.find("'/exit' C-m", sig_block_start)
        assert sig_block_start != -1
        assert sig_block_end != -1
        injection_sequence = script[sig_block_start:sig_block_end]
        assert "counter >= 10" in injection_sequence

    def test_signal_file_injection_double_verifies_prompt(self):
        """Injection must perform two back-to-back capture-pane checks before
        firing. A transient ❯ during startup or state transition fails the second
        check and prevents injection."""
        script = self._make_script()
        sig_block_start = script.find('if [[ -f "$signal_file" ]];')
        sig_block_end = script.find("'/exit' C-m", sig_block_start)
        assert sig_block_start != -1
        assert sig_block_end != -1
        injection_sequence = script[sig_block_start:sig_block_end]
        # Two distinct capture-pane calls must appear
        assert injection_sequence.count("capture-pane") >= 2

    def test_signal_file_deleted_after_injection_not_before(self):
        """signal_file must be deleted AFTER send-keys, not before.
        Deleting before injection loses the signal if the watcher is killed
        mid-sequence, preventing retry on the next watcher start."""
        script = self._make_script()
        exit_pos = script.find("'/exit' C-m")
        rm_pos = script.find('rm -f "$signal_file"', exit_pos)
        assert exit_pos != -1, "/exit injection not found"
        assert rm_pos != -1, "rm signal_file not found after /exit"

    def test_signal_file_injection_skips_when_exit_already_in_pane(self):
        """The watcher must not inject /exit if /exit is already visible in the
        pane. A concurrent watcher subshell SIGTERMed mid-sequence may have
        already injected /exit before it could clean up signal_file. Without
        this guard, the new watcher re-injects, causing 2+ /exit submissions."""
        script = self._make_script()
        sig_block_start = script.find('if [[ -f "$signal_file" ]];')
        injection_pos = script.find("'/exit' C-m", sig_block_start)
        assert sig_block_start != -1
        assert injection_pos != -1
        # The section BEFORE the /exit injection must contain a grep or check
        # using the literal '/exit' string (not just a comment mentioning it).
        # Discriminator: comments have /exit without quotes; the guard has '/exit'.
        pre_injection = script[sig_block_start:injection_pos]
        assert "'/exit'" in pre_injection, (
            "No pane content guard for '/exit' found before injection — "
            "watcher can inject duplicate /exit when pane already has one"
        )

    def test_signal_file_cleanup_fires_even_when_injection_skipped(self):
        """signal_file must be removed and the watcher must break even when the
        /exit pane guard fires (injection skipped). If signal_file persisted after
        a skip, the next watcher cycle would inject anyway — defeating the guard.
        Structural check: a `fi` must appear between the injection line and the
        rm, proving rm is OUTSIDE the guard's if-block."""
        script = self._make_script()
        injection_pos = script.find("'/exit' C-m")
        rm_pos = script.find('rm -f "$signal_file"', injection_pos)
        assert injection_pos != -1, "/exit injection not found"
        assert rm_pos != -1, "rm signal_file not found after injection"
        between = script[injection_pos:rm_pos]
        assert "fi" in between, (
            "rm signal_file is inside the /exit guard's if-block — "
            "cleanup won't fire when injection is skipped due to pane guard"
        )

    def test_config_change_detection_has_startup_grace_period(self):
        """Config change auto-restart must also skip the first 10s.
        Same stale-pane problem: config_changed_file from a prior run can
        exist when the watcher starts, and the pane shows old ❯ content."""
        script = self._make_script()
        config_block_start = script.find('if [[ -f "$config_changed_file"')
        assert config_block_start != -1
        config_block = script[config_block_start : config_block_start + 200]
        assert "counter >= 10" in config_block

    def test_given_claude_script_when_generated_then_uses_shared_continue_target_resolver(self):
        """Every Claude Code continuation must first resolve an exact title."""
        script = self._make_script()
        assert 'ai internal resolve-continue-target "$PWD" "$ai_name"' in script
        assert "resolve_status=$?" in script
        assert script.index('ai internal resolve-continue-target "$PWD" "$ai_name"') < script.index(
            'if [[ -f "$prompt_file" ]];'
        )
        assert 'run_agent claude $claude_perms_flag --resume "$session_id" --name "$ai_name" "$resume_msg"' in script
        assert 'run_agent claude $claude_perms_flag --resume "$session_id" --name "$ai_name"' in script
        assert 'run_agent claude $claude_perms_flag --name "$ai_name" "$resume_msg"' in script
        assert 'find "$HOME/.claude/projects' not in script


def test_given_pi_bare_launch_when_not_resuming_then_starts_named_session():
    command = _bare_engine_command("p", "myproject-1", Path.cwd(), None, "gemini", "--no-sandbox", [])

    assert command == ["pi", "--name", "myproject-1"]


def test_given_pi_bare_launch_when_resuming_then_continues_named_session():
    command = _bare_engine_command("p", "myproject-1", Path.cwd(), None, "gemini", "--no-sandbox", [], resume=True)

    assert command == ["pi", "--continue", "--name", "myproject-1"]


def test_given_codex_bare_launch_when_not_resuming_then_starts_interactive_codex():
    command = _bare_engine_command("cx", "myproject-1", Path.cwd(), None, "gemini", "--no-sandbox", [])

    assert command == ["codex"]


def test_given_codex_bare_launch_when_resuming_then_resumes_last_session_in_worktree():
    command = _bare_engine_command("cx", "myproject-1", Path.cwd(), None, "gemini", "--no-sandbox", [], resume=True)

    assert command == ["codex", "resume", "--last"]


def test_given_matching_claude_transcript_when_bare_launching_then_resumes_its_session_id():
    transcript = Path("/tmp/aaaaaaaa-0000-4000-8000-000000000001.jsonl")

    with (
        patch("ai_cli.main._find_cc_session_candidates_by_title", return_value=[transcript]),
        patch("ai_cli.main._cc_session_is_live", return_value=(False, None)),
    ):
        command = _bare_engine_command("c", "session-1", Path("/tmp"), None, "gemini", "--no-sandbox", [])

    assert command[-4:] == ["--resume", transcript.stem, "--name", "session-1"]


def test_given_invalid_claude_transcript_id_when_bare_launching_then_fails_loudly():
    with patch("ai_cli.main._find_cc_session_candidates_by_title", return_value=[Path("/tmp/not-a-session-id.jsonl")]):
        with pytest.raises(RuntimeError, match="invalid session UUID"):
            _bare_engine_command("c", "session-1", Path("/tmp"), None, "gemini", "--no-sandbox", [])


def test_given_resolve_continue_target_with_lone_mismatch_confirmed_then_prints_it(capsys):
    """AI-CLI-8xvd: `ai internal resolve-continue-target` must honor the same
    lone-mismatched-title confirm path `_bare_engine_command` does, since the
    tmux session script resolves through this CLI action, not that function."""
    transcript = Path("/tmp/aaaaaaaa-0000-4000-8000-00000000000f.jsonl")

    with (
        patch("ai_cli.main._find_cc_session_candidates_by_title", return_value=[]),
        patch("ai_cli.main._find_lone_mismatched_cc_session", return_value=transcript),
        patch("ai_cli.main._confirm_mismatched_title_resume", return_value=True),
        patch("ai_cli.main._cc_session_is_live", return_value=(False, None)),
        pytest.raises(SystemExit) as exc_info,
    ):
        _handle_internal(["resolve-continue-target", "/tmp", "session-1"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == str(transcript)


def test_given_resolve_continue_target_with_lone_mismatch_declined_then_prints_nothing(capsys):
    transcript = Path("/tmp/aaaaaaaa-0000-4000-8000-000000000010.jsonl")

    with (
        patch("ai_cli.main._find_cc_session_candidates_by_title", return_value=[]),
        patch("ai_cli.main._find_lone_mismatched_cc_session", return_value=transcript),
        patch("ai_cli.main._confirm_mismatched_title_resume", return_value=False),
        pytest.raises(SystemExit) as exc_info,
    ):
        _handle_internal(["resolve-continue-target", "/tmp", "session-1"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == ""


def test_given_pi_missing_from_path_when_launching_then_reports_pi(capsys):
    from ai_cli.main import _do_session_launch

    with (
        patch("ai_cli.main.shutil.which", return_value=None),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        pytest.raises(SystemExit) as exc_info,
    ):
        _do_session_launch(
            engine="p",
            name="1",
            resume=False,
            once=False,
            bare=True,
            notify=False,
            sandbox=False,
            no_worktree=False,
            remote=False,
            project="",
            is_remote=False,
            project_prefix_override="myproject",
            extra_args=[],
            config={},
        )

    assert exc_info.value.code == 1
    assert "pi executable not found" in capsys.readouterr().err


def test_given_codex_missing_from_path_when_launching_then_reports_codex(capsys):
    from ai_cli.main import _do_session_launch

    with (
        patch("ai_cli.main.shutil.which", return_value=None),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        pytest.raises(SystemExit) as exc_info,
    ):
        _do_session_launch(
            engine="cx",
            name="1",
            resume=False,
            once=False,
            bare=True,
            notify=False,
            sandbox=False,
            no_worktree=False,
            remote=False,
            project="",
            is_remote=False,
            project_prefix_override="myproject",
            extra_args=[],
            config={},
        )

    assert exc_info.value.code == 1
    assert "codex executable not found" in capsys.readouterr().err


# --- Group 9: _find_aicli_project_path ---


class TestFindAicliProjectPath:
    def test_when_importlib_raises_then_falls_through_to_cwd_check(self, tmp_path):
        with (
            patch("importlib.util.find_spec", side_effect=Exception("broken")),
            patch("pathlib.Path.cwd", return_value=tmp_path),
        ):
            result = _find_aicli_project_path({})
        assert result is None


# --- Group 10: _auto_update_if_stale ---


class TestAutoUpdateIfStaleFailure:
    def test_given_failed_update_when_auto_update_runs_then_warns_and_leaves_stamp_unset(self, tmp_path, capsys):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "ai-cli-utils"\nversion = "0.1.0"\n')
        head_result = MagicMock()
        head_result.returncode = 0
        head_result.stdout = "abc123\n"
        update_result = MagicMock()
        update_result.returncode = 1

        def fake_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return head_result
            return update_result

        with (
            patch("ai_cli.main._find_aicli_project_path", return_value=tmp_path),
            patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path),
            patch("subprocess.run", side_effect=fake_run),
            patch("shutil.which", return_value="/usr/bin/ai"),
        ):
            updated = _auto_update_if_stale({"deploy": {"project_path": str(tmp_path)}})
        assert updated is False
        assert "Warning" in capsys.readouterr().err
        assert not (tmp_path / "last_update_commit.txt").exists()


class TestAutoUpdateIfStaleLockContention:
    """The lock LOSER must not keep running while a peer reinstalls it.

    AI-CLI-ai-c-loser-auto-update-lock-bvzd. `ai update --force` runs `uv tool
    install`, which tears down and rewrites this process's own installed tree. The
    winner re-execs (AI-CLI-an5r / c5b33d04 added that), but the loser returned
    False -- "no restart needed" -- and carried on executing deferred imports
    against a directory being deleted. Measured 2026-08-17: five `ai c` launches
    within 34 seconds, and the two that had a prior transcript to resume died on
    `from .session_adopt import _pid_is_live` while three siblings survived.

    `False` conflated "no update was needed" with "a peer is replacing my files
    right now". Only the first is safe to continue on.
    """

    def _project(self, tmp_path):
        """Build the project and return the stamp value the launcher will look for.

        The staleness signal is a content fingerprint of the packaged source
        (`AI-CLI-ww8o`), not the repository's HEAD, so the stamp value is computed
        from the tree rather than mocked out of `git rev-parse`. Deriving it from
        the real function is deliberate: hard-coding a digest here would let the
        test pass against a fingerprint that no longer matches what the launcher
        computes, which is the whole condition these tests exist to pin.
        """
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "ai-cli-utils"\nversion = "0.1.0"\n')
        head = MagicMock()
        head.returncode = 0
        head.stdout = "abc123\n"
        return head, _installed_source_fingerprint(tmp_path)

    def test_given_peer_holds_lock_and_completes_update_when_auto_update_runs_then_requests_reexec(self, tmp_path):
        """The peer finished and the stamp advanced, so our imports are stale -> re-exec."""
        head, fingerprint = self._project(tmp_path)
        lock = tmp_path / "last_install_fingerprint.lock"
        lock.write_text("")  # a peer already claimed the update

        def fake_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return head
            raise AssertionError("the loser must not run `ai update` itself")

        def peer_finishes(*_args, **_kwargs):
            # Model the winner completing mid-wait: stamp written, lock released.
            (tmp_path / "last_install_fingerprint.txt").write_text(fingerprint)
            lock.unlink(missing_ok=True)

        with (
            patch("ai_cli.main._find_aicli_project_path", return_value=tmp_path),
            patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path),
            patch("subprocess.run", side_effect=fake_run),
            patch("shutil.which", return_value="/usr/bin/ai"),
            patch("time.sleep", side_effect=peer_finishes),
        ):
            updated = _auto_update_if_stale({"deploy": {"project_path": str(tmp_path)}})

        assert updated is True, "loser saw its installation replaced and must re-exec, not continue"

    def test_given_peer_holds_lock_indefinitely_when_auto_update_runs_then_terminates_without_reexec(
        self, tmp_path, capsys
    ):
        """Bounded wait: a stuck peer must not hang the launch or loop re-execing (AC-3)."""
        head, _fingerprint = self._project(tmp_path)
        (tmp_path / "last_install_fingerprint.lock").write_text("")  # never released

        def fake_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return head
            raise AssertionError("the loser must not run `ai update` itself")

        with (
            patch("ai_cli.main._find_aicli_project_path", return_value=tmp_path),
            patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path),
            patch("subprocess.run", side_effect=fake_run),
            patch("shutil.which", return_value="/usr/bin/ai"),
            # Shrink the real bound rather than stubbing sleep: with sleep stubbed and
            # the deadline on real monotonic time, the loop busy-spins the full 90s and
            # the test takes 101s. Patching the constant proves the bound is finite
            # AND keeps the suite fast.
            patch("ai_cli.main._PEER_UPDATE_WAIT_SECONDS", 0.05),
            patch("ai_cli.main._PEER_UPDATE_POLL_SECONDS", 0.01),
        ):
            updated = _auto_update_if_stale({"deploy": {"project_path": str(tmp_path)}})

        assert updated is False, "an unfinished peer must not trigger a re-exec (no exec loop)"
        assert "Warning" in capsys.readouterr().err, "silently continuing on a torn install is the bug"

    def test_given_stamp_already_current_when_auto_update_runs_then_no_wait_and_no_reexec(self, tmp_path):
        """Positive control + AC-4: the common case must not pay for the fix.

        Without this, both tests above would pass against a function that always
        returned True or always waited, which would assert nothing about contention.
        """
        head, fingerprint = self._project(tmp_path)
        (tmp_path / "last_install_fingerprint.txt").write_text(fingerprint)

        def fake_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return head
            raise AssertionError("no update should be attempted when the stamp is current")

        with (
            patch("ai_cli.main._find_aicli_project_path", return_value=tmp_path),
            patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path),
            patch("subprocess.run", side_effect=fake_run),
            patch("shutil.which", return_value="/usr/bin/ai"),
            patch("time.sleep", side_effect=AssertionError("must not wait when nothing is stale")),
        ):
            assert _auto_update_if_stale({"deploy": {"project_path": str(tmp_path)}}) is False

    def test_given_peer_pulls_new_source_when_loser_waits_then_loser_requests_reexec(self, tmp_path):
        """A peer may pull after taking the lock, changing the fingerprint a loser sees."""
        (tmp_path / "src" / "demo").mkdir(parents=True)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0.1.0"\n')
        source = tmp_path / "src" / "demo" / "value.py"
        source.write_text('VALUE = "before"\n')
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        peer_pulled = threading.Event()
        finish_peer = threading.Event()
        updates: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            if len(cmd) >= 2 and cmd[1] == "update":
                updates.append(list(cmd))
                source.write_text('VALUE = "after"\n')
                peer_pulled.set()
                assert finish_peer.wait(timeout=1)
            return MagicMock(returncode=0, stdout="", stderr="")

        def finish_update(*_args, **_kwargs):
            finish_peer.set()

        config = {"deploy": {"project_path": str(tmp_path)}}
        with (
            patch("ai_cli.main._find_aicli_project_path", return_value=tmp_path),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("subprocess.run", side_effect=fake_run),
            patch("shutil.which", return_value="/usr/bin/ai"),
        ):
            winner = threading.Thread(target=_auto_update_if_stale, args=(config,))
            winner.start()
            assert peer_pulled.wait(timeout=1)
            with patch("time.sleep", side_effect=finish_update):
                loser_reexec = _auto_update_if_stale(config)
            winner.join(timeout=1)

        assert not winner.is_alive()
        assert updates and len(updates) == 1
        assert loser_reexec is True, "the loser must restart after its peer replaced the installed files"


# --- Group 11: _cmd_tunnel_start no remote host ---


class TestCmdTunnelStartNoHost:
    def test_when_remote_host_not_set_then_exits_1(self, tmp_path, capsys):
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("shutil.which", return_value="/usr/bin/autossh"),
        ):
            with pytest.raises(SystemExit) as exc:
                _cmd_tunnel_start(4222, 4222, forward=True, config={})
            assert exc.value.code == 1
        assert "host not set" in capsys.readouterr().err


# --- Group 12: _cmd_tunnel_stop ProcessLookupError ---


class TestCmdTunnelStopProcessDead:
    def test_when_process_already_dead_then_cleans_up_pid_file(self, tmp_path, capsys):
        import psutil as _psutil

        pid_file = tmp_path / "tunnel-9222.pid"
        pid_file.write_text("99999")
        mock_proc = MagicMock()
        mock_proc.terminate.side_effect = _psutil.NoSuchProcess(99999)
        with patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path):
            with patch("ai_cli.tunnel.psutil.Process", return_value=mock_proc):
                _cmd_tunnel_stop(9222)
        assert not pid_file.exists()
        assert "stopped" in capsys.readouterr().out.lower()


# --- Group 13: _cmd_tunnel_status ---


class TestCmdTunnelStatus:
    def test_when_no_tunnel_pid_files_then_prints_no_tunnels(self, tmp_path, capsys):
        with patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path):
            _cmd_tunnel_status()
        assert "No tunnels registered" in capsys.readouterr().out

    def test_when_tunnel_pid_is_dead_then_prints_dead_and_removes_file(self, tmp_path, capsys):
        pid_file = tmp_path / "tunnel-4222.pid"
        pid_file.write_text("99999")
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel._pid_alive", return_value=False),
        ):
            _cmd_tunnel_status()
        assert "dead" in capsys.readouterr().out
        assert not pid_file.exists()


# --- Group 15: CLI dispatch tests ---


class TestCliDispatchExtended:
    def test_when_quota_status_subcommand_then_dispatches(self):
        with (
            patch("sys.argv", ["ai", "quota", "status"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.quota.quota_status", return_value=0),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

    def test_when_quota_history_subcommand_then_dispatches(self):
        with (
            patch("sys.argv", ["ai", "quota", "history"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.quota.quota_history", return_value=0),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

    def test_when_quota_scrape_subcommand_then_dispatches(self):
        with (
            patch("sys.argv", ["ai", "quota", "scrape"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.quota.quota_scrape", return_value=0),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

    def test_when_quota_record_subcommand_then_dispatches(self):
        with (
            patch("sys.argv", ["ai", "quota", "record", "sid1", "mac1", "claude-sonnet", "1000"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.quota.quota_record", return_value=0),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

    def test_when_quota_record_missing_args_then_exits_1(self):
        with (
            patch("sys.argv", ["ai", "quota", "record"]),
            patch("ai_cli.config.load_config", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 1

    def test_when_copier_update_dispatches_on_any_host(self):
        with (
            patch("sys.argv", ["ai", "copier-update"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch.dict(os.environ, {"AI_HOST": "hetzner"}),
            patch("ai_cli.copier_update.run_copier_update", return_value=0),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

    def test_when_color_subcommand_no_arg_then_exits_1(self):
        exit_code, _, _ = run_cli(["ai", "color"])
        assert exit_code == 1

    def test_when_color_subcommand_no_session_env_then_exits_1(self, monkeypatch):
        monkeypatch.delenv("AI_TMUX_SESSION", raising=False)
        exit_code, _, stderr = run_cli(["ai", "color", "#e74c3c"])
        assert exit_code == 1
        assert "AI_TMUX_SESSION" in stderr

    def test_when_color_subcommand_with_hex_and_session_then_succeeds(self):
        cfg = make_iterm2_config()
        mock_ig = MagicMock()
        mock_ig.cleanup_session_files = MagicMock()
        mock_ig.generate_session_icon = MagicMock(return_value="/tmp/icon.png")
        mock_ig.generate_dynamic_profile = MagicMock()
        with (
            patch("sys.argv", ["ai", "color", "#e74c3c"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch.dict(os.environ, {"AI_TMUX_SESSION": "session-1"}),
            patch("ai_cli.iterm2._load_iterm2_config", return_value=cfg),
            patch.dict("sys.modules", {"ai_cli.icon_generator": mock_ig}),
            patch("sys.stdout"),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

    def test_when_tunnel_no_args_then_exits_1(self):
        exit_code, _, _ = run_cli(["ai", "tunnel"])
        assert exit_code == 1

    def test_when_tunnel_stop_dispatches(self, tmp_path):
        with (
            patch("sys.argv", ["ai", "tunnel", "stop", "9222"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.tunnel._cmd_tunnel_stop") as mock_stop,
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0
            mock_stop.assert_called_once_with(9222)

    def test_when_tunnel_stop_no_port_then_exits_1(self):
        exit_code, _, _ = run_cli(["ai", "tunnel", "stop"])
        assert exit_code == 1

    def test_when_tunnel_status_dispatches(self):
        with (
            patch("sys.argv", ["ai", "tunnel", "status"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.tunnel._cmd_tunnel_status") as mock_status,
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0
            mock_status.assert_called_once()

    def test_when_tunnel_unknown_action_exits_1(self):
        exit_code, _, _ = run_cli(["ai", "tunnel", "unknown"])
        assert exit_code == 1

    def test_when_copier_update_with_project_flag_then_dispatches(self, tmp_path):
        with (
            patch("sys.argv", ["ai", "copier-update", "--project", "myproject"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.copier_update.run_copier_update", return_value=0),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

    def test_when_color_known_palette_name_then_resolves_hex(self):
        """lines 2304-2305: palette name resolves to hex."""
        cfg = make_iterm2_config()
        mock_ig = MagicMock()
        mock_ig.generate_session_icon = MagicMock(return_value=None)
        # Get first palette name from the config
        palette = cfg.get("iterm2", {}).get("palette", {})
        first_name = next(iter(palette))
        with (
            patch("sys.argv", ["ai", "color", first_name]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch.dict(os.environ, {"AI_TMUX_SESSION": "session-1"}),
            patch("ai_cli.iterm2._load_iterm2_config", return_value=cfg),
            patch.dict("sys.modules", {"ai_cli.icon_generator": mock_ig}),
            patch("sys.stdout"),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

    def test_when_color_unknown_name_then_exits_1(self):
        """lines 2306-2308: color arg not in palette and not hex → exits 1."""
        cfg = make_iterm2_config()
        with (
            patch("sys.argv", ["ai", "color", "notacolor"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch.dict(os.environ, {"AI_TMUX_SESSION": "session-1"}),
            patch("ai_cli.iterm2._load_iterm2_config", return_value=cfg),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 1

    def test_when_color_icon_generation_raises_then_continues(self, capsys):
        """lines 2323-2324: icon generation exception is caught and printed."""
        cfg = make_iterm2_config()
        with (
            patch("sys.argv", ["ai", "color", "#e74c3c"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch.dict(os.environ, {"AI_TMUX_SESSION": "session-1"}),
            patch("ai_cli.iterm2._load_iterm2_config", return_value=cfg),
            patch("ai_cli.icon_generator.cleanup_session_files", side_effect=RuntimeError("icon err")),
            patch("sys.stdout"),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0  # exception is caught, execution continues
            assert "icon generation failed" in capsys.readouterr().err

    def test_when_project_prefix_flag_then_uses_it(self):
        """line 2665: --project-prefix arg is used directly as project_prefix."""
        with (
            patch("sys.argv", ["ai", "c", "--project-prefix", "myprefix", "-R"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 1  # exits because -R requires remote host

    def test_when_unknown_arg_then_used_as_session_name(self):
        """line 2685: unrecognized arg in unknown → used as name when args.name is empty."""
        # args.name is empty (no positional); --unrecognized-flag lands in unknown
        with (
            patch("sys.argv", ["ai", "c", "--project-prefix", "myprefix", "-R", "--unrecognized"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 1  # exits because -R requires remote host

    def test_when_keyboard_interrupt_raised_then_exits_cleanly_without_traceback(self):
        """A raw Ctrl-C (e.g. from the registry-sync input() prompt) must not
        propagate past cli() as an unhandled KeyboardInterrupt — standalone_mode=False
        means Click won't convert it to click.exceptions.Abort on its own."""
        with patch("ai_cli.main._cli_group", side_effect=KeyboardInterrupt):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 1


class TestResolveIsRemote:
    def test_when_flag_true_then_returns_true_regardless_of_host(self):
        with patch.dict(os.environ, {"AI_HOST": "mac"}):
            assert _resolve_is_remote(True) is True

    def test_when_flag_false_and_mac_host_then_returns_false(self):
        with patch.dict(os.environ, {"AI_HOST": "mac"}):
            assert _resolve_is_remote(False) is False

    def test_when_flag_false_and_named_linux_host_then_returns_false(self):
        """A named host is NOT evidence of a remote session.

        This previously returned True for any AI_HOST != "mac", which made every
        ordinary local launch on a Linux workstation take the remote branch and
        create its worktree inside the configured main project instead of the
        repo the user was in.
        """
        with patch.dict(os.environ, {"AI_HOST": "my-linux-box"}):
            assert _resolve_is_remote(False) is False

    def test_when_flag_false_and_arbitrary_host_then_returns_false(self):
        with patch.dict(os.environ, {"AI_HOST": "devserver"}):
            assert _resolve_is_remote(False) is False

    def test_when_flag_false_and_no_host_env_then_returns_false(self):
        env = {k: v for k, v in os.environ.items() if k != "AI_HOST"}
        with patch.dict(os.environ, env, clear=True):
            assert _resolve_is_remote(False) is False

    def test_when_flag_false_and_empty_host_then_returns_false(self):
        with patch.dict(os.environ, {"AI_HOST": ""}):
            assert _resolve_is_remote(False) is False


class TestEnsureNatsTunnel:
    def test_when_stale_pid_file_and_tunnel_start_raises_then_returns(self, tmp_path):
        """lines 1639-1640, 1643-1644: stale PID → ProcessLookupError, SystemExit caught."""
        from ai_cli.main import _ensure_nats_tunnel

        pid_file = tmp_path / "tunnel-4222.pid"
        pid_file.write_text("99999")  # nonexistent PID
        config = {"messaging": {"tunnel_port": 4222}}
        with (
            patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel._cmd_tunnel_start", side_effect=SystemExit(1)),
        ):
            _ensure_nats_tunnel(config)  # must not raise or propagate SystemExit


class TestEnsureCircusd:
    def test_when_all_connection_attempts_fail_then_raises(self, tmp_path):
        """lines 1727-1730: all 10 retry attempts fail → raises RuntimeError."""
        from ai_cli.main import _ensure_circusd

        # Mock the circus.client module so CircusClient always fails
        mock_client_instance = MagicMock()
        mock_client_instance.send_message.side_effect = Exception("not ready")
        mock_client_class = MagicMock(return_value=mock_client_instance)
        mock_circus_client_module = MagicMock()
        mock_circus_client_module.CircusClient = mock_client_class

        with (
            patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path),
            patch("subprocess.Popen"),
            patch.dict("sys.modules", {"circus.client": mock_circus_client_module}),
            patch("time.sleep"),
        ):
            with pytest.raises(RuntimeError, match="circusd did not start in time"):
                _ensure_circusd()


class TestDoSessionLaunchTmuxGuard:
    """The tmux preflight in _do_session_launch, on every platform.

    Originally Windows-only (T-09). Broadened in 55ace53 because every non-Windows
    machine without tmux was crashing with a raw FileNotFoundError from deep inside
    cleanup_stale_sessions() instead of this message. The guard now fires on all
    platforms, with a platform-appropriate install hint and the use_tmux opt-out.
    """

    def _base_kwargs(self):
        return {
            "engine": "c",
            "name": "1",
            "resume": False,
            "once": False,
            "bare": False,
            "notify": True,
            "sandbox": False,
            "no_worktree": False,
            "remote": False,
            "project": "",
            "is_remote": False,
            "project_prefix_override": "test",
            "extra_args": [],
            "config": {},
        }

    def test_when_win32_and_tmux_not_found_then_falls_back_to_bare_mode(self, capsys):
        """On Windows, missing tmux must NOT abort — fall back to bare mode silently.

        Regression test for the bug where the tmux guard exited with code 1 on Windows
        instead of continuing in bare mode.
        """
        from ai_cli.main import _do_session_launch

        with (
            patch("sys.platform", "win32"),
            patch("shutil.which", return_value=None),
            patch("ai_cli.session._resolve_is_remote", return_value=False),
            patch("ai_cli.config.validate_registry_completeness", return_value=True),
            patch("ai_cli.session.get_project_prefix", return_value="test"),
            patch("subprocess.run", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _do_session_launch(**self._base_kwargs())
        # Must NOT be the tmux-not-found hard exit (code 1 with the error message).
        err = capsys.readouterr().err
        assert "tmux not found" not in err
        assert exc_info.value.code != 1, "Windows + no tmux should fall back to bare mode, not exit 1"

    def test_when_win32_and_tmux_found_then_does_not_exit_early(self, capsys):
        from ai_cli.main import _do_session_launch

        # With tmux present on "Windows", the guard passes and execution continues
        # into the normal launch path (which we stop at session resolution)
        with (
            patch("sys.platform", "win32"),
            patch("shutil.which", return_value="/usr/bin/tmux"),
            patch("ai_cli.session._resolve_is_remote", return_value=False),
            patch("ai_cli.config.validate_registry_completeness", return_value=True),
            patch("ai_cli.session.get_project_prefix", return_value="test"),
            patch("ai_cli.main._emit_iterm2_profile_setup"),
            patch("ai_cli.main._assign_iterm2_color_slot", return_value=None),
            patch("subprocess.run", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _do_session_launch(**self._base_kwargs())
        # Exit must not be the tmux-not-found exit (1) — it's 0 (subprocess mock)
        # confirming the guard was passed
        assert exc_info.value.code != 1 or "tmux not found" not in (capsys.readouterr().err)

    def test_when_non_windows_and_tmux_not_found_then_guard_exits_with_platform_hint(self, capsys):
        """The guard DOES fire on Linux/macOS — that is the point of 55ace53.

        This previously asserted the opposite, encoding the very bug 55ace53 fixed:
        without the guard, a non-Windows machine lacking tmux died on a raw
        FileNotFoundError inside cleanup_stale_sessions() with no actionable message.
        """
        from ai_cli.main import _do_session_launch

        with (
            patch("sys.platform", "linux"),
            patch("shutil.which", return_value=None),
            patch("ai_cli.session._resolve_is_remote", return_value=False),
            patch("ai_cli.config.validate_registry_completeness", return_value=True),
            patch("ai_cli.session.get_project_prefix", return_value="test"),
            patch("subprocess.run", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _do_session_launch(**self._base_kwargs())
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "tmux not found" in err
        # Platform-appropriate install hint, not the MSYS2/pacman one.
        assert "apt install tmux" in err
        # Both escape hatches must be named so the user can choose.
        assert "-b" in err
        assert "use_tmux = false" in err

    def test_when_darwin_and_tmux_not_found_then_hint_is_homebrew(self, capsys):
        from ai_cli.main import _do_session_launch

        with (
            patch("sys.platform", "darwin"),
            patch("shutil.which", return_value=None),
            patch("ai_cli.session._resolve_is_remote", return_value=False),
            patch("ai_cli.config.validate_registry_completeness", return_value=True),
            patch("ai_cli.session.get_project_prefix", return_value="test"),
            patch("subprocess.run", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _do_session_launch(**self._base_kwargs())
        assert exc_info.value.code == 1
        assert "brew install tmux" in capsys.readouterr().err

    def test_when_use_tmux_false_and_tmux_absent_then_guard_is_skipped(self):
        """The opt-out is the escape hatch the error message advertises.

        Nothing covered it before, so `use_tmux = false` could have silently stopped
        working and the only symptom would be a machine that cannot launch a session
        at all.
        """
        from ai_cli.main import _do_session_launch

        kwargs = self._base_kwargs()
        kwargs["config"] = {"session": {"use_tmux": False}}
        with (
            patch("sys.platform", "linux"),
            patch("shutil.which", return_value=None),
            patch("ai_cli.session._resolve_is_remote", return_value=False),
            patch("ai_cli.config.validate_registry_completeness", return_value=True),
            patch("ai_cli.session.get_project_prefix", return_value="test"),
            patch("subprocess.run", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _do_session_launch(**kwargs)
        # Reached subprocess.run (0), not the tmux guard (1).
        assert exc_info.value.code != 1


class TestDoSessionLaunchRegistryDiscovery:
    def test_given_legacy_remote_session_when_local_index_launch_then_reuses_legacy_worktree(self):
        from ai_cli.main import _do_session_launch

        kwargs = {
            "engine": "c",
            "name": "7",
            "resume": False,
            "once": False,
            "bare": False,
            "notify": False,
            "sandbox": False,
            "no_worktree": False,
            "remote": False,
            "project": "",
            "is_remote": False,
            "project_prefix_override": "APP",
            "extra_args": [],
            "config": {"worktree": {"enabled": True}},
        }
        with (
            patch("shutil.which", return_value="/usr/bin/tmux"),
            patch("ai_cli.session._resolve_is_remote", return_value=False),
            patch("ai_cli.session.cleanup_stale_sessions"),
            patch("ai_cli.session.detect_repo_root", return_value=None),
            patch("ai_cli.trust.ensure_workspace_trusted"),
            patch("ai_cli.config.get_current_project_name", return_value="myproject"),
            patch(
                "subprocess.run",
                return_value=MagicMock(returncode=0, stdout="c-r-app-7\n"),
            ),
            patch("ai_cli.session.create_worktree", return_value=None) as create_worktree,
        ):
            with pytest.raises(SystemExit) as exc_info:
                _do_session_launch(**kwargs)

        assert exc_info.value.code == 1
        create_worktree.assert_called_once_with("app-7", with_status=True)

    def test_given_named_launch_and_unregistered_other_project_when_started_then_skips_registry_prompt(self):
        from ai_cli.main import _do_session_launch

        kwargs = {
            "engine": "c",
            "name": "1",
            "resume": False,
            "once": False,
            "bare": False,
            "notify": False,
            "sandbox": False,
            "no_worktree": False,
            "remote": False,
            "project": "",
            "is_remote": False,
            "project_prefix_override": "",
            "extra_args": [],
            "config": {"worktree": {"enabled": True}},
        }
        with (
            patch("ai_cli.session._resolve_is_remote", return_value=False),
            patch("ai_cli.config.validate_registry_completeness", return_value=False) as validate,
            patch("ai_cli.session.is_current_project_resolved", return_value=True),
            patch("ai_cli.session.get_project_prefix", return_value="app"),
            patch("ai_cli.session.cleanup_stale_sessions"),
            patch("ai_cli.session.build_session_name", return_value=("c-app-1", "app-1")),
            patch("ai_cli.session.create_worktree", return_value=Path("/tmp/project-worktree")),
            patch("subprocess.run", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _do_session_launch(**kwargs)

        assert exc_info.value.code == 0
        validate.assert_not_called()

    def test_given_targeted_cli_launch_and_unregistered_directory_when_started_then_skips_registry_discovery(
        self, tmp_path, monkeypatch
    ):
        """The Click command path must preserve targeted-launch discovery bypass."""
        import ai_cli.config as config_module

        projects_dir = tmp_path / "projects"
        project_dir = projects_dir / "myproject"
        project_dir.mkdir(parents=True)
        (projects_dir / "unregistered-worktrees").mkdir()
        registry_path = tmp_path / "projects.toml"
        registry_path.write_text(
            '[[projects]]\nname = "myproject"\ntask_prefix = "MYPROJECT"\ntype = "tool"\nactive = true\n'
        )
        monkeypatch.chdir(project_dir)

        with (
            patch("sys.argv", ["ai", "c", "--once", "2"]),
            patch("ai_cli.config.load_config", return_value={"worktree": {"enabled": False}}),
            patch("ai_cli.config._get_project_registry_path", return_value=registry_path),
            patch("ai_cli.config._get_projects_dir", return_value=projects_dir),
            patch("ai_cli.session._get_projects_dir", return_value=projects_dir),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.main._auto_update_if_stale"),
            patch("ai_cli.tunnel._ensure_nats_tunnel"),
            patch("ai_cli.session._resolve_is_remote", return_value=False),
            patch("ai_cli.session.get_project_prefix", return_value="MYPROJECT"),
            patch("ai_cli.session.cleanup_stale_sessions"),
            patch("ai_cli.session.build_session_name", return_value=("c-myproject-2", "myproject-2")),
            patch("ai_cli.trust.ensure_workspace_trusted"),
            patch("ai_cli.main.os.execvp", side_effect=SystemExit(0)),
            patch.object(
                config_module,
                "validate_registry_completeness",
                wraps=config_module.validate_registry_completeness,
            ) as validate,
        ):
            with pytest.raises(SystemExit) as exc_info:
                cli()

        assert exc_info.value.code == 0
        validate.assert_not_called()


# --- uv link-mode detection tests ---


class TestUvLinkModeDetection:
    """Test _should_use_uv_link_mode_copy behavior across filesystem boundaries."""

    def test_given_same_filesystem_when_checked_then_returns_false(self, tmp_path):
        """When cache and target are on the same filesystem, no --link-mode flag needed."""
        from ai_cli.main import _should_use_uv_link_mode_copy

        # Both directories on the same filesystem (tmp_path).
        cache_dir = tmp_path / "cache"
        tool_dir = tmp_path / "tools"
        cache_dir.mkdir()
        tool_dir.mkdir()

        mock_uv = "uv"
        with (
            patch(
                "subprocess.run",
                side_effect=[
                    # First call: uv cache dir
                    MagicMock(returncode=0, stdout=str(cache_dir) + "\n"),
                    # Second call: uv tool dir
                    MagicMock(returncode=0, stdout=str(tool_dir) + "\n"),
                ],
            ),
        ):
            result = _should_use_uv_link_mode_copy(mock_uv)

        assert result is False

    def test_given_different_filesystems_when_checked_then_returns_true(self, tmp_path, monkeypatch):
        """When cache and target are on different filesystems, return True for --link-mode=copy."""
        from ai_cli.main import _should_use_uv_link_mode_copy

        cache_dir = tmp_path / "cache"
        tool_dir = tmp_path / "tools"
        cache_dir.mkdir()
        tool_dir.mkdir()

        # Create mock stat_result objects with different st_dev values.
        cache_stat = os.stat_result((0o40755, 123, 100, 2, 1000, 1000, 4096, 0, 0, 0))
        tool_stat = os.stat_result((0o40755, 456, 200, 2, 1000, 1000, 4096, 0, 0, 0))

        original_stat = Path.stat

        def mock_stat(self, **kwargs):
            path_str = str(self)
            if path_str == str(cache_dir):
                return cache_stat
            if path_str == str(tool_dir):
                return tool_stat
            # Use original for other paths (walking up to parents)
            return original_stat(self, **kwargs)

        mock_uv = "uv"
        with (
            patch(
                "subprocess.run",
                side_effect=[
                    MagicMock(returncode=0, stdout=str(cache_dir) + "\n"),
                    MagicMock(returncode=0, stdout=str(tool_dir) + "\n"),
                ],
            ),
        ):
            monkeypatch.setattr(Path, "stat", mock_stat)
            result = _should_use_uv_link_mode_copy(mock_uv)

        assert result is True

    def test_given_cache_dir_unresolvable_when_checked_then_returns_false(self):
        """When uv cache dir command fails, preserve default behavior (return False)."""
        from ai_cli.main import _should_use_uv_link_mode_copy

        mock_uv = "uv"
        with patch("subprocess.run", return_value=MagicMock(returncode=1)):
            result = _should_use_uv_link_mode_copy(mock_uv)

        assert result is False

    def test_given_target_dir_does_not_exist_when_checked_then_walks_up_to_ancestor(self, tmp_path):
        """When target dir does not exist yet, walk up to nearest existing ancestor."""
        from ai_cli.main import _should_use_uv_link_mode_copy

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        # Tool dir does not exist yet, but its parent does.
        tool_parent = tmp_path / "tools-parent"
        tool_parent.mkdir()
        nonexistent_tool_dir = tool_parent / "subdir" / "tools"

        mock_uv = "uv"
        with patch(
            "subprocess.run",
            side_effect=[
                MagicMock(returncode=0, stdout=str(cache_dir) + "\n"),
                MagicMock(returncode=0, stdout=str(nonexistent_tool_dir) + "\n"),
            ],
        ):
            result = _should_use_uv_link_mode_copy(mock_uv)

        # Both are under tmp_path, so same filesystem → False.
        assert result is False

    def test_given_explicit_target_dir_when_checked_then_uses_it(self, tmp_path):
        """When target_dir is explicitly provided, use it instead of uv tool dir."""
        from ai_cli.main import _should_use_uv_link_mode_copy

        cache_dir = tmp_path / "cache"
        venv_dir = tmp_path / "venv"
        cache_dir.mkdir()
        venv_dir.mkdir()

        mock_uv = "uv"
        with patch(
            "subprocess.run",
            side_effect=[
                # Only one call: uv cache dir (uv tool dir not called when target_dir provided).
                MagicMock(returncode=0, stdout=str(cache_dir) + "\n"),
            ],
        ):
            result = _should_use_uv_link_mode_copy(mock_uv, venv_dir)

        # Same filesystem → False.
        assert result is False
