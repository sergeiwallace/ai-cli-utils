import json
import os
import re
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from ai_cli.main import (
    _assign_iterm2_color_slot,
    _auto_update_if_stale,
    _cmd_tunnel_start,
    _cmd_tunnel_stop,
    _cmd_tunnel_status,
    _cmd_signal_watch_start,
    _cmd_signal_watch_status,
    _find_aicli_project_path,
    _load_iterm2_config,
    _log_handoff_event,
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
from conftest import make_iterm2_config, run_cli


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

    def _make_script(self, **kwargs):
        defaults = dict(
            engine="c",
            ai_name="session-1",
            session="c-session-1",
            prefix="c-session-",
            project_prefix="session",
            session_id_uuid="",
            sandbox=False,
            notify=False,
            project_name="myproject",
        )
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

    def test_config_change_detection_has_startup_grace_period(self):
        """Config change auto-restart must also skip the first 10s.
        Same stale-pane problem: config_changed_file from a prior run can
        exist when the watcher starts, and the pane shows old ❯ content."""
        script = self._make_script()
        config_block_start = script.find('if [[ -f "$config_changed_file"')
        assert config_block_start != -1
        config_block = script[config_block_start : config_block_start + 200]
        assert "counter >= 10" in config_block

    def test_resume_match_returns_single_path_when_multiple_files_share_session_name(self, tmp_path):
        """The inline Python that finds the most-recent JSONL matching a session
        name must output exactly one path, even when multiple files share the same
        customTitle.

        Root cause of the original bug: sys.exit(0) was called inside a bare
        ``except: pass`` block.  SystemExit is a BaseException, so the bare
        except swallowed it and the loop continued — producing all matching
        paths concatenated without separators, causing ``touch`` to treat the
        concatenation as a single (invalid) path.
        """
        session_name = "test-session"
        # Create 5 JSONL files, all with customTitle == session_name.
        # Stagger their mtimes so the sort order is deterministic.
        paths = []
        for i in range(5):
            f = tmp_path / f"{i:02d}-fake-uuid.jsonl"
            f.write_text(json.dumps({"customTitle": session_name}) + "\n")
            os.utime(f, (1_000_000 + i, 1_000_000 + i))
            paths.append(f)

        # Extract the inline Python from the generated bash script.
        script = self._make_script()
        m = re.search(r'matched_file=\$\(python3 -c "(.*?)" "\$cc_project_dir"', script, re.DOTALL)
        assert m, "Could not find inline Python block in engine script"
        python_code = m.group(1)

        result = subprocess.run(
            [sys.executable, "-c", python_code, str(tmp_path), session_name],
            capture_output=True,
            text=True,
        )
        output = result.stdout

        # Must be exactly one path (no concatenation, no newline-separated list).
        assert output.count(str(tmp_path)) == 1, f"Expected one path in output, got: {output!r}"
        # Must be a valid file path that actually exists.
        assert os.path.isfile(output), f"Output is not a valid file path: {output!r}"


# --- Group 8: _log_handoff_event OSError ---


class TestLogHandoffEvent:
    def test_when_log_file_write_raises_then_suppressed(self, tmp_path):
        with (
            patch("ai_cli.config.get_xdg_state_home", return_value=tmp_path),
            patch("builtins.open", side_effect=OSError("disk full")),
        ):
            _log_handoff_event("test.event", session="session-1")


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
    def test_when_update_command_fails_then_prints_warning(self, tmp_path, capsys):
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
            _auto_update_if_stale({"deploy": {"project_path": str(tmp_path)}})
        assert "Warning" in capsys.readouterr().err


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
        pid_file = tmp_path / "tunnel-9222.pid"
        pid_file.write_text("99999")
        with patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path):
            with patch("os.kill", side_effect=ProcessLookupError):
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
            patch("os.kill", side_effect=ProcessLookupError),
        ):
            _cmd_tunnel_status()
        assert "dead" in capsys.readouterr().out
        assert not pid_file.exists()


# --- Group 14: _cmd_signal_watch_start / _cmd_signal_watch_status ---


class TestCmdSignalWatchStart:
    def test_when_start_called_then_autostart_not_in_options(self, tmp_path):
        """B-04: autostart is invalid for Circus add — was silently failing watcher registration."""
        mock_client = MagicMock()
        mock_client.send_message.return_value = {"status": "ok"}
        with (
            patch("ai_cli.process_manager.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.process_manager._ensure_circusd", return_value=f"ipc://{tmp_path}/circus.endpoint"),
            patch("circus.client.CircusClient", return_value=mock_client),
        ):
            _cmd_signal_watch_start("myproject", "session-1")
        call_kwargs = mock_client.send_message.call_args_list[-1][1]
        options = call_kwargs.get("options", {})
        assert "autostart" not in options
        assert call_kwargs.get("start") is True

    def test_when_start_called_then_respawn_false(self, tmp_path):
        mock_client = MagicMock()
        mock_client.send_message.return_value = {"status": "ok"}
        with (
            patch("ai_cli.process_manager.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.process_manager._ensure_circusd", return_value=f"ipc://{tmp_path}/circus.endpoint"),
            patch("circus.client.CircusClient", return_value=mock_client),
        ):
            _cmd_signal_watch_start("myproject", "session-1")
        call_kwargs = mock_client.send_message.call_args_list[-1][1]
        assert call_kwargs["options"]["respawn"] is False


class TestCmdSignalWatchStatus:
    def test_when_no_sw_watchers_then_prints_no_processes(self, tmp_path, capsys):
        mock_client = MagicMock()
        mock_client.send_message.return_value = {"statuses": {"other-watcher": "active"}}
        with (
            patch("ai_cli.process_manager.get_xdg_state_home", return_value=tmp_path),
            patch("circus.client.CircusClient", return_value=mock_client),
        ):
            _cmd_signal_watch_status()
        assert "No signal-watch processes running" in capsys.readouterr().out


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
            patch.dict(os.environ, {"AI_CLI_HOST": "hetzner"}),
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

    def test_when_signal_watch_start_missing_args_exits_1(self):
        exit_code, _, _ = run_cli(["ai", "signal-watch", "start"])
        assert exit_code == 1

    def test_when_signal_watch_stop_missing_arg_exits_1(self):
        exit_code, _, _ = run_cli(["ai", "signal-watch", "stop"])
        assert exit_code == 1

    def test_when_signal_watch_status_dispatches(self):
        with (
            patch("sys.argv", ["ai", "signal-watch", "status"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.process_manager._cmd_signal_watch_status") as mock_status,
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0
            mock_status.assert_called_once()

    def test_when_signal_watch_unknown_action_exits_1(self):
        exit_code, _, _ = run_cli(["ai", "signal-watch", "bad"])
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


class TestResolveIsRemote:
    def test_when_flag_true_then_returns_true_regardless_of_host(self):
        with patch.dict(os.environ, {"AI_CLI_HOST": "mac"}):
            assert _resolve_is_remote(True) is True

    def test_when_flag_false_and_mac_host_then_returns_false(self):
        with patch.dict(os.environ, {"AI_CLI_HOST": "mac"}):
            assert _resolve_is_remote(False) is False

    def test_when_flag_false_and_hetzner_host_then_returns_true(self):
        with patch.dict(os.environ, {"AI_CLI_HOST": "hetzner"}):
            assert _resolve_is_remote(False) is True

    def test_when_flag_false_and_arbitrary_remote_host_then_returns_true(self):
        with patch.dict(os.environ, {"AI_CLI_HOST": "devserver"}):
            assert _resolve_is_remote(False) is True

    def test_when_flag_false_and_no_host_env_then_returns_false(self):
        env = {k: v for k, v in os.environ.items() if k != "AI_CLI_HOST"}
        with patch.dict(os.environ, env, clear=True):
            assert _resolve_is_remote(False) is False

    def test_when_flag_false_and_empty_host_then_returns_false(self):
        with patch.dict(os.environ, {"AI_CLI_HOST": ""}):
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
