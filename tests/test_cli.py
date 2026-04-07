import json
import os
import subprocess
import time
from unittest.mock import patch, MagicMock

import pytest

from ai_cli.main import (
    _auto_update_if_stale,
    _cmd_tunnel_start,
    _cmd_tunnel_stop,
    _cmd_tunnel_status,
    _ensure_nats_tunnel,
    cli,
    get_engine_script,
    trigger_background_update,
)


# --- CLI dispatch tests ---


class TestCliDispatch:
    def test_cli_when_internal_get_latest_gemini_id_then_calls_function(self):
        with patch("sys.argv", ["ai", "internal", "get-latest-gemini-id"]):
            with patch("ai_cli.main.get_latest_gemini_session_id", return_value="abc123") as mock_fn:
                with patch("ai_cli.main.load_config", return_value={}):
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0
                    mock_fn.assert_called_once_with(None)

    def test_cli_when_internal_get_latest_gemini_id_with_ai_name_then_passes_it(self):
        with patch("sys.argv", ["ai", "internal", "get-latest-gemini-id", "art-1"]):
            with patch("ai_cli.main.get_latest_gemini_session_id", return_value="uuid") as mock_fn:
                with patch("ai_cli.main.load_config", return_value={}):
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0
                    mock_fn.assert_called_once_with("art-1")

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

    def test_cli_when_internal_publish_event_then_publishes_event(self):
        with patch("sys.argv", ["ai", "internal", "publish-event", "sess1", "START"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.messaging.NATSClient") as mock_nats:
                    mock_client = MagicMock()
                    mock_nats.return_value = mock_client
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0
                    mock_nats.assert_called_once()
                    mock_client.publish_event.assert_called_once()
                    call_args = mock_client.publish_event.call_args[0]
                    assert call_args[0] == "sess1"
                    assert call_args[1] == "START"

    def test_cli_when_internal_publish_heartbeat_then_publishes_heartbeat(self):
        with patch("sys.argv", ["ai", "internal", "publish-heartbeat", "sess1", '{"cpu": 50}']):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.messaging.NATSClient") as mock_nats:
                    mock_client = MagicMock()
                    mock_nats.return_value = mock_client
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0
                    mock_nats.assert_called_once()
                    mock_client.publish_heartbeat.assert_called_once()
                    call_args = mock_client.publish_heartbeat.call_args[0]
                    assert call_args[0] == "sess1"
                    assert call_args[1] == {"cpu": 50}

    def test_cli_when_internal_publish_heartbeat_bad_json_then_exits_1(self):
        with patch("sys.argv", ["ai", "internal", "publish-heartbeat", "sess1", "not-json"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_internal_publish_session_event_then_publishes_with_subject(self):
        with patch("sys.argv", ["ai", "internal", "publish-session-event", "sess1", "started"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.messaging.NATSClient") as mock_nats:
                    mock_client = MagicMock()
                    mock_nats.return_value = mock_client
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0
                    mock_nats.assert_called_once()
                    mock_client.publish.assert_called_once()
                    subject, payload = mock_client.publish.call_args[0]
                    assert subject == "session.sess1.started"
                    assert payload["session_id"] == "sess1"
                    assert payload["event"] == "started"

    def test_cli_when_internal_publish_then_publishes_to_given_subject(self):
        with patch("sys.argv", ["ai", "internal", "publish", "test.topic", '{"key": "val"}']):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.messaging.NATSClient") as mock_nats:
                    mock_client = MagicMock()
                    mock_nats.return_value = mock_client
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0
                    mock_nats.assert_called_once()
                    mock_client.publish.assert_called_once()
                    subject, payload = mock_client.publish.call_args[0]
                    assert subject == "test.topic"
                    assert payload == {"key": "val"}

    def test_cli_when_handoff_post_then_calls_post_handoff(self):
        with patch("sys.argv", ["ai", "handoff", "post", "--for-machine", "hetzner", "title", "P1", "proj", "msg"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.post_handoff") as mock_post:
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0
                    mock_post.assert_called_once_with("title", "P1", "proj", "msg", for_machine="hetzner")

    def test_cli_when_handoff_post_without_for_machine_then_exits_1(self):
        with patch("sys.argv", ["ai", "handoff", "post", "title", "P1", "proj", "msg"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

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
                    with patch("ai_cli.main.get_project_aliases", return_value={"mp": "myproject"}):
                        with pytest.raises(SystemExit) as exc:
                            probe_result.stdout = "c-r-mp-1\n"  # prefix mp matches alias key mp
                            cli()
                        assert exc.value.code == 0
        output = capsys.readouterr().out
        assert "-p mp" in output

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


class TestCliDispatchBranches:
    def test_cli_when_memory_no_watch_then_exits_1(self):
        with patch("sys.argv", ["ai", "memory", "bad"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_quota_no_watch_then_exits_1(self):
        with patch("sys.argv", ["ai", "quota", "bad"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_telemetry_no_writer_then_exits_1(self):
        with patch("sys.argv", ["ai", "telemetry", "bad"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_gemini_no_args_then_calls_gemini_cli(self):
        with patch("sys.argv", ["ai", "gemini"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.gemini.gemini_cli", side_effect=SystemExit(0)):
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0


class TestCliSessionSetupBranches:
    def test_cli_when_no_explicit_flag_then_sandbox_false_by_default(self):
        with patch("sys.argv", ["ai", "c", "--bare"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.get_project_prefix", return_value="sw"):
                    with patch("ai_cli.main.trigger_background_update"):
                        with patch("os.execvp", side_effect=SystemExit(0)):
                            with pytest.raises(SystemExit):
                                cli()

    def test_cli_when_sandbox_flag_then_sandbox_true(self):
        with patch("sys.argv", ["ai", "g", "--sandbox", "--bare"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.get_project_prefix", return_value="sw"):
                    with patch("ai_cli.main.trigger_background_update"):
                        with patch("os.execvp", side_effect=SystemExit(0)):
                            with pytest.raises(SystemExit):
                                cli()

    def test_cli_when_sandbox_and_session_exists_then_kills_and_recreates(self):
        killed = []

        def _run(cmd, **kwargs):
            if cmd[0] == "tmux" and cmd[1] == "has-session":
                return MagicMock(returncode=1 if killed else 0)
            if cmd[0] == "tmux" and cmd[1] == "kill-session":
                killed.append(True)
                return MagicMock(returncode=0)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("sys.argv", ["ai", "g", "1", "--sandbox"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.get_project_prefix", return_value="sw"):
                    with patch("ai_cli.main.trigger_background_update"):
                        with patch("ai_cli.main._emit_iterm2_profile_setup"):
                            with patch("subprocess.run", side_effect=_run):
                                with patch("os.execvp", side_effect=SystemExit(0)):
                                    with pytest.raises(SystemExit):
                                        cli()
        assert len(killed) == 1

    def test_cli_when_no_explicit_sandbox_and_session_exists_then_attaches_without_kill(self):
        killed = []

        def _run(cmd, **kwargs):
            if cmd[0] == "tmux" and cmd[1] == "has-session":
                return MagicMock(returncode=0)
            if cmd[0] == "tmux" and cmd[1] == "kill-session":
                killed.append(True)
                return MagicMock(returncode=0)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("sys.argv", ["ai", "g", "1"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.get_project_prefix", return_value="sw"):
                    with patch("ai_cli.main.trigger_background_update"):
                        with patch("ai_cli.main._emit_iterm2_profile_setup"):
                            with patch("subprocess.run", side_effect=_run):
                                with patch("os.execvp", side_effect=SystemExit(0)):
                                    with pytest.raises(SystemExit):
                                        cli()
        assert len(killed) == 0

    def test_cli_when_iterm2_env_set_then_passes_to_tmux_new_session(self):
        execvp_calls = []

        with patch("sys.argv", ["ai", "g", "1"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.main.get_project_prefix", return_value="sw"):
                    with patch("ai_cli.main.trigger_background_update"):
                        with patch("ai_cli.main._emit_iterm2_profile_setup"):
                            with patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="")):
                                with patch.dict(
                                    os.environ,
                                    {
                                        "ITERM_SESSION_ID": "w0t1p0:abc",
                                        "LC_TERMINAL": "iTerm2",
                                        "TERM_PROGRAM": "iTerm.app",
                                    },
                                    clear=False,
                                ):
                                    with patch(
                                        "os.execvp",
                                        side_effect=lambda *a: (
                                            execvp_calls.append(a) or (_ for _ in ()).throw(SystemExit(0))
                                        ),
                                    ):
                                        with pytest.raises(SystemExit):
                                            cli()

        assert execvp_calls, "os.execvp was not called"
        cmd = execvp_calls[-1][1]
        assert "-e" in cmd
        assert any("ITERM_SESSION_ID=w0t1p0:abc" in a for a in cmd)
        assert any("LC_TERMINAL=iTerm2" in a for a in cmd)
        assert any("TERM_PROGRAM=iTerm.app" in a for a in cmd)

    def test_cli_when_remote_with_project_flag_then_uses_project_prefix(self):
        config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "transport": "mosh"}}

        async def fake_transport_loop(*args, **kwargs):
            pass

        with (
            patch("sys.argv", ["ai", "c", "-R", "-p", "myproj"]),
            patch("ai_cli.main.load_config", return_value=config),
            patch("ai_cli.main.get_project_prefix", return_value="sw"),
            patch("ai_cli.main.get_project_aliases", return_value={}),
            patch("ai_cli.main._get_project_prefix_by_name", return_value="mp"),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.main._is_vpn_active", return_value=False),
            patch("ai_cli.main._run_transport_loop", side_effect=fake_transport_loop),
            patch("ai_cli.main._ensure_vpn_watcher"),
            patch("ai_cli.main._maybe_stop_vpn_watcher"),
            patch("sys.exit", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit):
                cli()

    def test_cli_when_remote_mosh_non_standard_port_then_adds_ssh_flag(self):
        config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "port": 2222, "transport": "mosh"}}
        captured_mosh_args = []

        async def fake_transport_loop(ssh_args, mosh_args, cleanup_cmd, session_name, config):
            captured_mosh_args.extend(mosh_args)

        with (
            patch("sys.argv", ["ai", "c", "-R"]),
            patch("ai_cli.main.load_config", return_value=config),
            patch("ai_cli.main.get_project_prefix", return_value="sw"),
            patch("ai_cli.main.get_project_aliases", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.main._is_vpn_active", return_value=False),
            patch("ai_cli.main._run_transport_loop", side_effect=fake_transport_loop),
            patch("ai_cli.main._ensure_vpn_watcher"),
            patch("ai_cli.main._maybe_stop_vpn_watcher"),
            patch("sys.exit", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit):
                cli()
        assert "--ssh" in captured_mosh_args

    def test_cli_when_remote_mosh_with_identity_file_only_then_adds_ssh_i(self):
        config = {
            "remote": {
                "host": "1.2.3.4",
                "user": "ubuntu",
                "port": 22,
                "transport": "mosh",
                "identity_file": "~/.ssh/id_ed25519",
            }
        }
        captured_mosh_args = []

        async def fake_transport_loop(ssh_args, mosh_args, cleanup_cmd, session_name, config):
            captured_mosh_args.extend(mosh_args)

        with (
            patch("sys.argv", ["ai", "c", "-R"]),
            patch("ai_cli.main.load_config", return_value=config),
            patch("ai_cli.main.get_project_prefix", return_value="sw"),
            patch("ai_cli.main.get_project_aliases", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.main._is_vpn_active", return_value=False),
            patch("ai_cli.main._run_transport_loop", side_effect=fake_transport_loop),
            patch("ai_cli.main._ensure_vpn_watcher"),
            patch("ai_cli.main._maybe_stop_vpn_watcher"),
            patch("sys.exit", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit):
                cli()
        assert "--ssh" in captured_mosh_args
        assert any("-i" in str(a) for a in captured_mosh_args)


class TestCliGeminiDispatch:
    def test_cli_when_gemini_returns_normally_then_exits_0(self):
        with patch("sys.argv", ["ai", "gemini", "hello"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.gemini.gemini_cli", return_value=None):
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0


class TestCliReconnectContinueBranch:
    def test_cli_when_reconnect_session_name_too_short_then_continues(self, capsys):
        config = {"remote": {"host": "1.2.3.4", "user": "ubuntu"}}
        probe = MagicMock(returncode=0, stdout="c-r-x\nc-r-sw-1\n")
        with patch("sys.argv", ["ai", "reconnect"]):
            with patch("ai_cli.main.load_config", return_value=config):
                with patch("subprocess.run", return_value=probe):
                    with patch("ai_cli.main.get_project_aliases", return_value={}):
                        with pytest.raises(SystemExit) as exc:
                            cli()
                        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "2 remote session" in out


class TestCliResumePath:
    def test_cli_when_resume_and_session_found_then_attaches(self):
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


class TestCliAttachDispatch:
    """Tests for `ai attach` subcommand."""

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
    """Tests for `ai ls` subcommand."""

    def _fake_tmux_sessions(self, sessions: list[tuple[str, int]]):
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
        with patch("sys.argv", ["ai", "ls"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("subprocess.run", return_value=self._fake_tmux_sessions([("random-session", 1000)])):
                    with patch("shutil.which", return_value=None):
                        with pytest.raises(SystemExit) as exc:
                            cli()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "--all" in out

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
        assert "ai attach" in out

    def test_when_fzf_available_and_selection_made_then_execs_attach(self):
        now = int(time.time())
        sessions = [("c-sw-1", now - 60)]
        fzf_result = MagicMock(returncode=0, stdout="c-sw-1\tsw\t1m\n")

        def fake_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and "tmux" in cmd:
                return self._fake_tmux_sessions(sessions)
            return fzf_result

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
        fzf_cancelled = MagicMock(returncode=130, stdout="")

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
        assert "random-session" in out

    def test_when_tmux_output_has_blank_lines_then_skips_them(self, capsys):
        now = int(time.time())
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
        raw_output = "c-sw-1 not-a-number\n"
        tmux_result = MagicMock(returncode=0, stdout=raw_output)
        with patch("sys.argv", ["ai", "ls"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("subprocess.run", return_value=tmux_result):
                    with patch("shutil.which", return_value=None):
                        with pytest.raises(SystemExit):
                            cli()
        assert "c-sw-1" in capsys.readouterr().out

    def test_when_session_age_is_seconds_then_displays_s_suffix(self, capsys):
        now = int(time.time())
        raw_output = f"c-sw-1 {now - 10}\n"
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
        raw_output = f"c-sw-1 {now - 90000}\n"
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


class TestCliInternalMissingArgs:
    def test_cli_when_internal_publish_event_missing_args_then_exits_1(self):
        with patch("sys.argv", ["ai", "internal", "publish-event"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_internal_publish_heartbeat_missing_args_then_exits_1(self):
        with patch("sys.argv", ["ai", "internal", "publish-heartbeat"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_internal_publish_session_event_missing_args_then_exits_1(self):
        with patch("sys.argv", ["ai", "internal", "publish-session-event"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_internal_publish_missing_args_then_exits_1(self):
        with patch("sys.argv", ["ai", "internal", "publish"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with pytest.raises(SystemExit) as exc:
                    cli()
                assert exc.value.code == 1

    def test_cli_when_internal_publish_bad_json_payload_then_uses_empty(self):
        with patch("sys.argv", ["ai", "internal", "publish", "topic", "not-json"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.messaging.NATSClient") as mock_nats:
                    mock_instance = MagicMock()
                    mock_nats.return_value = mock_instance
                    with pytest.raises(SystemExit) as exc:
                        cli()
                    assert exc.value.code == 0


class TestCliRegistryValidation:
    def test_cli_when_registry_incomplete_noninteractive_then_exits(self, tmp_path):
        registry = tmp_path / "registry.toml"
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


class TestCliDaemonDispatch:
    def test_cli_when_memory_watch_then_calls_memory_watch(self):
        with patch("sys.argv", ["ai", "memory", "watch"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.memory.memory_watch", return_value=0) as mock_watch:
                    with pytest.raises(SystemExit) as exc:
                        cli()
        assert exc.value.code == 0
        mock_watch.assert_called_once()

    def test_cli_when_quota_watch_then_calls_quota_watch(self):
        with patch("sys.argv", ["ai", "quota", "watch"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.quota.quota_watch", return_value=0) as mock_watch:
                    with pytest.raises(SystemExit) as exc:
                        cli()
        assert exc.value.code == 0
        mock_watch.assert_called_once()

    def test_cli_when_telemetry_writer_then_calls_telemetry_writer(self):
        with patch("sys.argv", ["ai", "telemetry", "writer"]):
            with patch("ai_cli.main.load_config", return_value={}):
                with patch("ai_cli.telemetry.telemetry_writer", return_value=0) as mock_writer:
                    with pytest.raises(SystemExit) as exc:
                        cli()
        assert exc.value.code == 0
        mock_writer.assert_called_once()


# --- ai internal get-version ---


class TestVersionFlag:
    def test_given_version_flag_when_called_then_prints_version_and_exits_0(self, capsys):
        with patch("sys.argv", ["ai", "--version"]):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0
        out = capsys.readouterr().out.strip()
        assert out
        assert "." in out  # version string contains a dot

    def test_given_short_version_flag_when_called_then_prints_version_and_exits_0(self, capsys):
        with patch("sys.argv", ["ai", "-V"]):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0
        out = capsys.readouterr().out.strip()
        assert out
        assert "." in out

    def test_given_version_flag_when_called_then_does_not_load_config(self):
        with patch("sys.argv", ["ai", "--version"]):
            with patch("ai_cli.main.load_config") as mock_cfg:
                with pytest.raises(SystemExit):
                    cli()
                mock_cfg.assert_not_called()


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
        assert out
        assert "." in out

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


# --- get_engine_script ---


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

    def test_get_engine_script_when_no_sandbox_then_explicit_no_sandbox_flag(self):
        script = get_engine_script("g", "sw-1", "g-sw-1", "g-sw-", "sw", sandbox=False)
        assert "--no-sandbox" in script
        assert "gemini -y --no-sandbox" in script

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


class TestGetEngineScriptSelfUpdate:
    def test_engine_script_embeds_template_version(self):
        script = get_engine_script("c", "c-sw-1", "c-sw-1", "c", "myapp")
        assert "_template_version=" in script

    def test_engine_script_contains_version_check_no_exec(self):
        script = get_engine_script("c", "c-sw-1", "c-sw-1", "c", "myapp")
        assert "ai internal get-version" in script
        assert "_current_ver" in script
        assert "exec ai" not in script


class TestEngineScriptProjectName:
    def test_get_engine_script_when_project_name_set_then_included_in_template(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw", project_name="ai-cli-utils")
        assert 'project_name="ai-cli-utils"' in script

    def test_get_engine_script_when_project_name_empty_then_signal_watch_not_started(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw", project_name="")
        assert 'project_name=""' in script

    def test_get_engine_script_signal_watch_uses_project_name(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw", project_name="my-project")
        assert 'ai signal-watch start "$project_name"' in script

    def test_get_engine_script_exit_trap_cleans_caught_file(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw", project_name="app")
        assert "handoff-caught-$tmux_session" in script

    def test_get_engine_script_while_loop_logs_handoff_pickup(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw", project_name="app")
        assert "handoff.while_loop_pickup" in script


# --- Deploy / update ---


class TestDeploy:
    def test_deploy_bumps_post_version_and_installs(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "ai-cli-utils"\nversion = "0.1.1"\n')

        with (
            patch("sys.argv", ["ai", "deploy"]),
            patch("ai_cli.main.load_config", return_value={"deploy": {"project_path": str(tmp_path)}}),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="")) as mock_run,
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        assert pyproject.read_text() == '[project]\nname = "ai-cli-utils"\nversion = "0.1.1"\n'
        all_cmds = [call[0][0] for call in mock_run.call_args_list]
        uv_cmd = next((c for c in all_cmds if "uv" in c and "--force" in c and "--reinstall" not in c), None)
        assert uv_cmd is not None

    def test_deploy_strips_existing_post_before_bumping(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "0.1.1.post20260101000000"\n')

        with (
            patch("sys.argv", ["ai", "deploy"]),
            patch("ai_cli.main.load_config", return_value={"deploy": {"project_path": str(tmp_path)}}),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="")),
            patch("builtins.print") as mock_print,
        ):
            with pytest.raises(SystemExit):
                cli()

        output = " ".join(str(c) for c in mock_print.call_args_list)
        assert "0.1.1.post20260101000000.post" not in output

    def test_deploy_when_no_pyproject_then_exits_with_error(self, tmp_path):
        with (
            patch("sys.argv", ["ai", "deploy"]),
            patch("ai_cli.main.load_config", return_value={"deploy": {"project_path": str(tmp_path)}}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 1

    def test_deploy_uses_package_location_when_no_config(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "ai-cli-utils"\nversion = "0.2.0"\n')

        with (
            patch("sys.argv", ["ai", "deploy"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main._find_aicli_project_path", return_value=tmp_path),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="")),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

    def test_deploy_exits_when_source_not_found(self, tmp_path, capsys):
        with (
            patch("sys.argv", ["ai", "deploy"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main._find_aicli_project_path", return_value=None),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 1
        assert "could not locate" in capsys.readouterr().err

    def test_deploy_always_pulls_before_install(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "0.1.0"\n')
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="")

        with (
            patch("sys.argv", ["ai", "update"]),
            patch("ai_cli.main.load_config", return_value={"deploy": {"project_path": str(tmp_path)}}),
            patch("subprocess.run", side_effect=fake_run),
            patch.dict("os.environ", {"AI_CLI_HOST": "hetzner"}),
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

    def test_deploy_pull_uses_autostash_and_restores_pyproject_first(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "0.1.0"\n')
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            return MagicMock(returncode=0, stdout="")

        with (
            patch("sys.argv", ["ai", "update"]),
            patch("ai_cli.main.load_config", return_value={"deploy": {"project_path": str(tmp_path)}}),
            patch("subprocess.run", side_effect=fake_run),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        cmds = [" ".join(c) for c in calls]
        checkout_idx = next((i for i, c in enumerate(cmds) if "checkout" in c and "pyproject.toml" in c), None)
        pull_idx = next((i for i, c in enumerate(cmds) if "git" in c and "pull" in c), None)
        assert checkout_idx is not None, "git checkout -- pyproject.toml not called"
        assert pull_idx is not None, "git pull not called"
        assert checkout_idx < pull_idx, "checkout must run before pull"
        assert "--autostash" in cmds[pull_idx], "pull must use --autostash"

    def test_deploy_when_conflict_markers_in_source_then_aborts(self, tmp_path, capsys):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "0.1.0"\n')
        src_dir = tmp_path / "src" / "ai_cli"
        src_dir.mkdir(parents=True)
        conflicted = src_dir / "main.py"
        conflicted.write_text(
            "def foo():\n    pass\n<<<<<<< Updated upstream\n    x = 1\n=======\n    x = 2\n>>>>>>> Stashed changes\n"
        )
        uv_called = []

        def fake_run(cmd, **kwargs):
            if "uv" in cmd and "tool" in cmd:
                uv_called.append(cmd)
            return MagicMock(returncode=0, stdout="")

        with (
            patch("sys.argv", ["ai", "update"]),
            patch("ai_cli.main.load_config", return_value={"deploy": {"project_path": str(tmp_path)}}),
            patch("subprocess.run", side_effect=fake_run),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
        assert exc.value.code == 1
        assert not uv_called, "uv install must not run when conflict markers are present"
        captured = capsys.readouterr()
        assert "conflict markers" in captured.err
        assert "src/ai_cli/main.py" in captured.err

    def test_deploy_installs_into_extra_venvs_when_configured(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "0.1.0"\n')
        extra_venv = tmp_path / "my-tool" / ".venv"
        extra_venv.mkdir(parents=True)
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="")

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
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "0.1.0"\n')
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="")

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


class TestTriggerBackgroundUpdateBadJson:
    def test_trigger_background_update_when_bad_json_in_state_then_proceeds(self, tmp_path):
        state_file = tmp_path / "update_check.json"
        state_file.write_text("not valid json {{{")

        with patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path):
            with patch("subprocess.Popen") as mock_popen:
                trigger_background_update()
        mock_popen.assert_called_once()


class TestTriggerBackgroundUpdateRecent:
    def test_trigger_background_update_when_recently_checked_then_skips(self, tmp_path):
        state_file = tmp_path / "update_check.json"
        state_file.write_text(json.dumps({"last_checked": time.time()}))

        with patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path):
            with patch("subprocess.Popen") as mock_popen:
                trigger_background_update()
        mock_popen.assert_not_called()


# --- _auto_update_if_stale ---


class TestAutoUpdateIfStale:
    def test_when_hash_matches_then_skips_update(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "0.1.0"\n')
        stamp = tmp_path / "last_update_commit.txt"
        stamp.write_text("abc123")

        def fake_run(cmd, **kwargs):
            return MagicMock(returncode=0, stdout="abc123\n")

        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("subprocess.run", side_effect=fake_run) as mock_run,
        ):
            _auto_update_if_stale({"deploy": {"project_path": str(tmp_path)}})

        ai_calls = [c for c in mock_run.call_args_list if "ai" in str(c) and "update" in str(c)]
        assert len(ai_calls) == 0

    def test_when_hash_differs_then_runs_update(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "0.1.0"\n')
        stamp = tmp_path / "last_update_commit.txt"
        stamp.write_text("old_hash")

        update_called = []

        def fake_run(cmd, **kwargs):
            if "update" in cmd:
                update_called.append(cmd)
            return MagicMock(returncode=0, stdout="new_hash\n")

        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("subprocess.run", side_effect=fake_run),
            patch("shutil.which", return_value="/usr/bin/ai"),
        ):
            _auto_update_if_stale({"deploy": {"project_path": str(tmp_path)}})

        assert len(update_called) == 1
        assert "--force" in update_called[0]

    def test_when_no_stamp_file_then_runs_update(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "0.1.0"\n')

        update_called = []

        def fake_run(cmd, **kwargs):
            if "update" in cmd:
                update_called.append(cmd)
            return MagicMock(returncode=0, stdout="abc123\n")

        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("subprocess.run", side_effect=fake_run),
            patch("shutil.which", return_value="/usr/bin/ai"),
        ):
            _auto_update_if_stale({"deploy": {"project_path": str(tmp_path)}})

        assert len(update_called) == 1

    def test_when_no_pyproject_then_skips(self, tmp_path):
        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("subprocess.run") as mock_run,
        ):
            _auto_update_if_stale({"deploy": {"project_path": str(tmp_path)}})

        mock_run.assert_not_called()

    def test_when_pyproject_has_no_version_field_then_exits_1(self, tmp_path, capsys):
        """lines 2488-2489: pyproject.toml exists but has no version = "..." pattern."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "ai-cli-utils"\n')  # no version field
        with (
            patch("sys.argv", ["ai", "deploy"]),
            patch("ai_cli.main.load_config", return_value={"deploy": {"project_path": str(tmp_path)}}),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="")),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
        assert exc.value.code == 1
        assert "could not find version" in capsys.readouterr().err

    def test_when_force_flag_then_adds_reinstall_to_uv_cmd(self, tmp_path):
        """line 2498: --force flag → --reinstall appended to uv install command."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "0.1.1"\n')
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            return MagicMock(returncode=0, stdout="")

        with (
            patch("sys.argv", ["ai", "deploy", "--force"]),
            patch("ai_cli.main.load_config", return_value={"deploy": {"project_path": str(tmp_path)}}),
            patch("subprocess.run", side_effect=fake_run),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
        assert exc.value.code == 0
        assert any("--reinstall" in c for c in calls if isinstance(c, list))

    def test_when_force_and_extra_venvs_then_adds_force_reinstall_to_pip(self, tmp_path):
        """line 2511: --force + extra_venvs → --force-reinstall in pip install command."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "0.1.1"\n')
        extra_venv = tmp_path / "my-tool" / ".venv"
        extra_venv.mkdir(parents=True)
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            return MagicMock(returncode=0, stdout="")

        config = {
            "deploy": {"project_path": str(tmp_path)},
            "update": {"extra_venvs": [str(extra_venv)]},
        }
        with (
            patch("sys.argv", ["ai", "deploy", "--force"]),
            patch("ai_cli.main.load_config", return_value=config),
            patch("subprocess.run", side_effect=fake_run),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
        assert exc.value.code == 0
        assert any("pip" in c and "--force-reinstall" in c for c in calls if isinstance(c, list))

    def test_deploy_writes_commit_hash_stamp_on_success(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "0.1.0"\n')

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["git", "rev-parse"]:
                return MagicMock(returncode=0, stdout="deadbeef\n")
            return MagicMock(returncode=0, stdout="")

        with (
            patch("sys.argv", ["ai", "update"]),
            patch("ai_cli.main.load_config", return_value={"deploy": {"project_path": str(tmp_path)}}),
            patch("subprocess.run", side_effect=fake_run),
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        stamp = tmp_path / "last_update_commit.txt"
        assert stamp.exists()
        assert stamp.read_text().strip() == "deadbeef"


# --- signal-watch CLI dispatch ---


class TestSignalWatchCliDispatch:
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

_TUNNEL_CONFIG = {"remote": {"host": "192.0.2.1", "user": "user"}}


class TestTunnel:
    def test_cmd_tunnel_start_when_default_then_launches_forward_tunnel(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("shutil.which", return_value="/usr/bin/autossh"),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            _cmd_tunnel_start(9222, 9222, config=_TUNNEL_CONFIG)
        args = mock_popen.call_args[0][0]
        assert "-L" in args
        assert "-R" not in args
        assert "9222:localhost:9222" in args
        assert (tmp_path / "tunnel-9222.pid").read_text() == "12345"

    def test_cmd_tunnel_start_when_reverse_flag_then_uses_dash_R(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.pid = 99
        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("shutil.which", return_value="/usr/bin/autossh"),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            _cmd_tunnel_start(8080, 8080, forward=False, config=_TUNNEL_CONFIG)
        args = mock_popen.call_args[0][0]
        assert "-R" in args
        assert "-L" not in args

    def test_cmd_tunnel_start_when_already_running_then_skips(self, tmp_path):
        (tmp_path / "tunnel-9222.pid").write_text("5555")
        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("os.kill"),
            patch("subprocess.Popen") as mock_popen,
        ):
            _cmd_tunnel_start(9222, 9222, config=_TUNNEL_CONFIG)
        mock_popen.assert_not_called()

    def test_cmd_tunnel_start_when_stale_pid_then_starts_new(self, tmp_path):
        (tmp_path / "tunnel-9222.pid").write_text("5555")
        mock_proc = MagicMock()
        mock_proc.pid = 7777
        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("os.kill", side_effect=ProcessLookupError),
            patch("shutil.which", return_value="/usr/bin/autossh"),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            _cmd_tunnel_start(9222, 9222, config=_TUNNEL_CONFIG)
        mock_popen.assert_called_once()
        assert (tmp_path / "tunnel-9222.pid").read_text() == "7777"

    def test_cmd_tunnel_start_suppresses_autossh_output(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.pid = 1
        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("shutil.which", return_value="/usr/bin/autossh"),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            _cmd_tunnel_start(4222, 4222, config=_TUNNEL_CONFIG)
        kwargs = mock_popen.call_args[1]
        assert kwargs.get("stdout") == subprocess.DEVNULL
        assert kwargs.get("stderr") == subprocess.DEVNULL

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
            _cmd_tunnel_stop(9222)

    def test_cmd_tunnel_status_lists_active_tunnels(self, tmp_path, capsys):
        (tmp_path / "tunnel-9222.pid").write_text("4242")
        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("os.kill"),
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

    def test_ensure_nats_tunnel_when_tunnel_port_configured_then_starts_tunnel(self, tmp_path):
        cfg = {**_TUNNEL_CONFIG, "messaging": {"tunnel_port": 4222}}
        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("shutil.which", return_value="/usr/bin/autossh"),
            patch("subprocess.Popen", return_value=MagicMock(pid=999)) as mock_popen,
        ):
            _ensure_nats_tunnel(cfg)
        mock_popen.assert_called_once()

    def test_ensure_nats_tunnel_when_no_tunnel_port_then_skips(self, tmp_path):
        with patch("subprocess.Popen") as mock_popen:
            _ensure_nats_tunnel({"messaging": {}})
        mock_popen.assert_not_called()

    def test_ensure_nats_tunnel_when_already_running_then_skips(self, tmp_path):
        (tmp_path / "tunnel-4222.pid").write_text("9999")
        cfg = {**_TUNNEL_CONFIG, "messaging": {"tunnel_port": 4222}}
        with (
            patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path),
            patch("os.kill"),
            patch("subprocess.Popen") as mock_popen,
        ):
            _ensure_nats_tunnel(cfg)
        mock_popen.assert_not_called()

    def test_cli_tunnel_missing_args_exits_1(self):
        with (
            patch("sys.argv", ["ai", "tunnel", "start"]),
            patch("ai_cli.main.load_config", return_value=_TUNNEL_CONFIG),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 1


# --- Local -p chdir ---


class TestLocalProjectChdir:
    def test_when_local_project_flag_then_chdirs_to_project_dir(self, tmp_path):
        project_dir = tmp_path / "projects" / "myproject"
        project_dir.mkdir(parents=True)
        with (
            patch("sys.argv", ["ai", "g", "1", "-p", "myproject"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main.get_project_aliases", return_value={}),
            patch("ai_cli.main._find_project_dir", return_value=project_dir),
            patch("ai_cli.main.validate_registry_completeness", return_value=True),
            patch("ai_cli.main.cleanup_stale_sessions"),
            patch("ai_cli.main.get_current_project_name", return_value="myproject"),
            patch("ai_cli.main.build_session_name", return_value=("g-myproject-1", "myproject-1")),
            patch("ai_cli.main.get_session_map", return_value={}),
            patch("ai_cli.main.create_worktree", return_value=None),
            patch("ai_cli.main._load_iterm2_config", return_value={}),
            patch("ai_cli.main._assign_iterm2_color_slot", return_value=None),
            patch("ai_cli.main._emit_iterm2_profile_setup"),
            patch("os.execvp", side_effect=SystemExit(0)),
            patch("os.chdir") as mock_chdir,
        ):
            with pytest.raises(SystemExit):
                cli()
        mock_chdir.assert_called_with(project_dir)

    def test_when_no_project_flag_then_no_chdir(self, tmp_path):
        with (
            patch("sys.argv", ["ai", "g", "1"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main.get_project_aliases", return_value={}),
            patch("ai_cli.main.validate_registry_completeness", return_value=True),
            patch("ai_cli.main.cleanup_stale_sessions"),
            patch("ai_cli.main.get_current_project_name", return_value="sw"),
            patch("ai_cli.main.build_session_name", return_value=("g-sw-1", "sw-1")),
            patch("ai_cli.main.get_session_map", return_value={}),
            patch("ai_cli.main.create_worktree", return_value=None),
            patch("ai_cli.main._load_iterm2_config", return_value={}),
            patch("ai_cli.main._assign_iterm2_color_slot", return_value=None),
            patch("ai_cli.main._emit_iterm2_profile_setup"),
            patch("os.execvp", side_effect=SystemExit(0)),
            patch("os.chdir") as mock_chdir,
        ):
            with pytest.raises(SystemExit):
                cli()
        mock_chdir.assert_not_called()

    def test_when_local_project_flag_then_prefix_derived_from_target_project(self, tmp_path):
        project_dir = tmp_path / "projects" / "myapp-mobile"
        project_dir.mkdir(parents=True)
        captured = {}

        def capture_build(engine, prefix, name, config, **kwargs):
            captured["prefix"] = prefix
            return ("c-hm-1", "hm-1")

        with (
            patch("sys.argv", ["ai", "c", "1", "-p", "myapp-mobile"]),
            patch("ai_cli.main.load_config", return_value={}),
            patch("ai_cli.main.get_project_aliases", return_value={}),
            patch("ai_cli.main._find_project_dir", return_value=project_dir),
            patch("ai_cli.main._get_project_prefix_by_name", return_value="hm"),
            patch("ai_cli.main.validate_registry_completeness", return_value=True),
            patch("ai_cli.main.cleanup_stale_sessions"),
            patch("ai_cli.main.get_current_project_name", return_value="myproject"),
            patch("ai_cli.main.get_project_prefix", return_value="sw"),
            patch("ai_cli.main.build_session_name", side_effect=capture_build),
            patch("ai_cli.main.get_session_map", return_value={}),
            patch("ai_cli.main.create_worktree", return_value=None),
            patch("ai_cli.main._load_iterm2_config", return_value={}),
            patch("ai_cli.main._assign_iterm2_color_slot", return_value=None),
            patch("ai_cli.main._emit_iterm2_profile_setup"),
            patch("os.execvp", side_effect=SystemExit(0)),
            patch("os.chdir"),
        ):
            with pytest.raises(SystemExit):
                cli()
        assert captured.get("prefix") == "hm", f"Expected 'hm' prefix but got '{captured.get('prefix')}'"
