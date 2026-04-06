from unittest.mock import patch, MagicMock

import pytest

from ai_cli.main import cli

from conftest import _run_cli_with_args


# --- --remote flag tests ---


def test_remote_flag_when_host_configured_then_sshs_to_host():
    config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "port": 22, "identity_file": "", "transport": "ssh"}}
    mock_exec = _run_cli_with_args(["ai", "c", "1", "--remote"], config)
    mock_exec.assert_called_once()
    cmd, args = mock_exec.call_args[0]
    assert cmd == "bash"
    bash_cmd = args[2]
    assert "ubuntu@1.2.3.4" in bash_cmd
    assert "-t" in bash_cmd
    assert "--is-remote" in bash_cmd and "1" in bash_cmd


def test_remote_flag_when_host_configured_then_passes_is_remote_flag():
    config = {"remote": {"host": "hetzner-dev", "user": "ubuntu", "port": 22, "identity_file": "", "transport": "ssh"}}
    mock_exec = _run_cli_with_args(["ai", "g", "research", "--remote"], config)
    mock_exec.assert_called_once()
    _, args = mock_exec.call_args[0]
    bash_cmd = args[2]
    assert "ubuntu@hetzner-dev" in bash_cmd
    assert "ai g --is-remote" in bash_cmd and "research" in bash_cmd


def test_remote_flag_when_called_then_passes_project_prefix_to_server():
    """Server receives --project-prefix so session uses local project tag, not remote cwd."""
    config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "port": 22, "identity_file": "", "transport": "ssh"}}
    with (
        patch("sys.argv", ["ai", "c", "1", "--remote"]),
        patch("ai_cli.main.load_config", return_value=config),
        patch("ai_cli.main.get_project_prefix", return_value="sw"),
        patch("os.execvp", side_effect=SystemExit(0)) as mock_exec,
        patch("ai_cli.main.trigger_background_update"),
    ):
        try:
            cli()
        except SystemExit:
            pass
    _, args = mock_exec.call_args[0]
    assert any("--project-prefix sw" in a for a in args)


def test_remote_flag_with_resume_when_called_then_forwards_resume_to_server():
    config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "port": 22, "identity_file": "", "transport": "ssh"}}
    mock_exec = _run_cli_with_args(["ai", "c", "-r", "1", "--remote"], config)
    mock_exec.assert_called_once()
    _, args = mock_exec.call_args[0]
    assert any("--resume" in a for a in args)
    assert any("--is-remote" in a and "--resume" in a and "1" in a for a in args)


def test_remote_flag_without_resume_when_called_then_no_resume_in_cmd():
    config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "port": 22, "identity_file": "", "transport": "ssh"}}
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
    bash_cmd = mock_exec.call_args[0][1][2]
    assert "-i" in bash_cmd


def test_remote_flag_when_host_not_configured_then_exits_with_error():
    config = {"remote": {"host": "", "user": "ubuntu", "port": 22, "identity_file": ""}}
    with (
        patch("sys.argv", ["ai", "c", "--remote"]),
        patch("ai_cli.main.load_config", return_value=config),
        patch("ai_cli.main.trigger_background_update"),
        patch("sys.stderr"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli()
        assert exc_info.value.code == 1


# --- VPN detection ---


class TestIsVpnActive:
    """Unit tests for _is_vpn_active VPN detection."""

    def test_when_mullvad_connected_then_returns_true(self):
        from ai_cli.main import _is_vpn_active

        with (
            patch("shutil.which", return_value="/usr/local/bin/mullvad"),
            patch("subprocess.run", return_value=MagicMock(stdout="Connected\n", returncode=0)),
        ):
            assert _is_vpn_active() is True

    def test_when_mullvad_disconnected_then_returns_false(self):
        from ai_cli.main import _is_vpn_active

        with (
            patch("shutil.which", return_value="/usr/local/bin/mullvad"),
            patch("subprocess.run", return_value=MagicMock(stdout="Disconnected\n", returncode=0)),
        ):
            assert _is_vpn_active() is False

    def test_when_mullvad_not_installed_and_no_tunnel_ifaces_then_returns_false(self):
        from ai_cli.main import _is_vpn_active

        ifconfig_output = "lo0: flags=8049 ...\nen0: flags=8863 ...\n  inet 192.168.1.5\n"
        with (
            patch("shutil.which", return_value=None),
            patch("subprocess.run", return_value=MagicMock(stdout=ifconfig_output, returncode=0)),
        ):
            assert _is_vpn_active() is False

    def test_when_mullvad_not_installed_and_utun_with_inet_then_returns_true(self):
        from ai_cli.main import _is_vpn_active

        ifconfig_output = "en0: flags=8863\n  inet 192.168.1.5\nutun3: flags=8051\n  inet 10.64.0.1\n"
        with (
            patch("shutil.which", return_value=None),
            patch("subprocess.run", return_value=MagicMock(stdout=ifconfig_output, returncode=0)),
        ):
            assert _is_vpn_active() is True

    def test_when_exception_raised_then_returns_false(self):
        from ai_cli.main import _is_vpn_active

        with patch("shutil.which", side_effect=Exception("boom")):
            assert _is_vpn_active() is False


class TestMoshVpnFallback:
    """VPN detection drives mosh->SSH fallback at launch time."""

    def _run_mosh_remote(self, vpn_active: bool):
        config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "transport": "mosh"}}
        mock_run = MagicMock(return_value=MagicMock(returncode=0))
        mock_exec = MagicMock(side_effect=SystemExit(0))
        with (
            patch("sys.argv", ["ai", "c", "-R"]),
            patch("ai_cli.main.load_config", return_value=config),
            patch("ai_cli.main.get_project_prefix", return_value="sw"),
            patch("ai_cli.main.get_project_aliases", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.main._is_vpn_active", return_value=vpn_active),
            patch("subprocess.run", mock_run),
            patch("os.execvp", mock_exec),
            patch("sys.exit", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit):
                cli()
        return mock_run, mock_exec

    def test_when_no_vpn_then_mosh_is_used(self):
        mock_run, mock_exec = self._run_mosh_remote(vpn_active=False)
        mosh_calls = [c for c in mock_run.call_args_list if c[0] and c[0][0] and c[0][0][0] == "mosh"]
        assert mosh_calls, "mosh should be called when VPN is inactive"
        assert not mock_exec.called

    def test_when_vpn_active_then_ssh_is_used_directly(self):
        mock_run, mock_exec = self._run_mosh_remote(vpn_active=True)
        mosh_calls = [c for c in mock_run.call_args_list if c[0] and c[0][0] and c[0][0][0] == "mosh"]
        assert not mosh_calls, "mosh should be skipped when VPN is active"
        ssh_calls = [c for c in mock_run.call_args_list if c[0] and c[0][0] and c[0][0][0] == "ssh"]
        assert ssh_calls, "ssh should be called when VPN is active"


class TestMoshReconnectAfterVpnDrop:
    """After SSH exits non-zero (connection dropped), reconnect via mosh if VPN is gone."""

    def _run_with_vpn_sequence(self, vpn_sequence: list[bool]):
        config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "transport": "mosh"}}
        vpn_iter = iter(vpn_sequence)

        run_calls = []

        def fake_run(args, **kwargs):
            run_calls.append(args[0] if args else None)
            if kwargs.get("capture_output"):
                return MagicMock(returncode=0)
            if args and args[0] == "ssh":
                return MagicMock(returncode=255)
            return MagicMock(returncode=0)

        with (
            patch("sys.argv", ["ai", "c", "-R"]),
            patch("ai_cli.main.load_config", return_value=config),
            patch("ai_cli.main.get_project_prefix", return_value="sw"),
            patch("ai_cli.main.get_project_aliases", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.main._is_vpn_active", side_effect=vpn_iter),
            patch("subprocess.run", side_effect=fake_run),
            patch("sys.exit", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit):
                cli()
        return run_calls

    def test_when_vpn_on_then_drops_then_reconnects_via_mosh(self):
        run_calls = self._run_with_vpn_sequence([True, False])
        assert "ssh" in run_calls, "SSH should run while VPN is on"
        assert "mosh" in run_calls, "mosh should reconnect after VPN drops"
        assert run_calls.index("ssh") < run_calls.index("mosh")

    def test_when_vpn_on_then_still_on_after_ssh_exits_then_no_mosh_reconnect(self):
        run_calls = self._run_with_vpn_sequence([True, True])
        assert "ssh" in run_calls
        assert "mosh" not in run_calls

    def test_when_no_vpn_and_mosh_fails_fast_then_ssh_runs(self):
        config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "transport": "mosh"}}
        run_calls = []

        def fake_run(args, **kwargs):
            run_calls.append(args[0] if args else None)
            if kwargs.get("capture_output"):
                return MagicMock(returncode=0)
            if args and args[0] == "mosh":
                return MagicMock(returncode=1)
            return MagicMock(returncode=0)

        with (
            patch("sys.argv", ["ai", "c", "-R"]),
            patch("ai_cli.main.load_config", return_value=config),
            patch("ai_cli.main.get_project_prefix", return_value="sw"),
            patch("ai_cli.main.get_project_aliases", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.main._is_vpn_active", return_value=False),
            patch("subprocess.run", side_effect=fake_run),
            patch("sys.exit", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit):
                cli()
        assert "mosh" in run_calls
        assert "ssh" in run_calls
        assert run_calls.index("mosh") < run_calls.index("ssh")


class TestRemoteSessionIterm2Emit:
    """Verify iTerm2 profile/color is emitted before mosh/ssh connects for remote sessions."""

    def _run_remote(self, argv, transport="mosh"):
        config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "transport": transport}}
        call_order = []

        if transport == "mosh":
            mock_run = MagicMock(return_value=MagicMock(returncode=0))
            mock_emit = MagicMock()
            mock_slot = MagicMock(return_value="#ff0000")
            mock_emit.side_effect = lambda *a, **kw: call_order.append("emit")

            def run_side_effect(args, **kwargs):
                if args and args[0] == "mosh":
                    call_order.append("exec")
                return MagicMock(returncode=0)

            mock_run.side_effect = run_side_effect
            with (
                patch("sys.argv", argv),
                patch("ai_cli.main.load_config", return_value=config),
                patch("ai_cli.main.get_project_prefix", return_value="sw"),
                patch("ai_cli.main.get_project_aliases", return_value={}),
                patch("ai_cli.main.trigger_background_update"),
                patch("ai_cli.main._assign_iterm2_color_slot", mock_slot),
                patch("ai_cli.main._emit_iterm2_profile_setup", mock_emit),
                patch("ai_cli.main._is_vpn_active", return_value=False),
                patch("subprocess.run", mock_run),
                patch("sys.exit", side_effect=SystemExit(0)),
            ):
                with pytest.raises(SystemExit):
                    cli()
            return mock_slot, mock_emit, mock_run, call_order
        else:
            mock_exec = MagicMock()
            mock_emit = MagicMock()
            mock_slot = MagicMock(return_value="#ff0000")
            mock_emit.side_effect = lambda *a, **kw: call_order.append("emit")
            mock_exec.side_effect = lambda *a, **kw: (call_order.append("exec"), (_ for _ in ()).throw(SystemExit(0)))[
                1
            ]
            with (
                patch("sys.argv", argv),
                patch("ai_cli.main.load_config", return_value=config),
                patch("ai_cli.main.get_project_prefix", return_value="sw"),
                patch("ai_cli.main.get_project_aliases", return_value={}),
                patch("ai_cli.main.trigger_background_update"),
                patch("ai_cli.main._assign_iterm2_color_slot", mock_slot),
                patch("ai_cli.main._emit_iterm2_profile_setup", mock_emit),
                patch("os.execvp", mock_exec),
            ):
                with pytest.raises(SystemExit):
                    cli()
            return mock_slot, mock_emit, mock_exec, call_order

    def test_when_remote_mosh_then_emit_called_before_execvp(self):
        _, mock_emit, _, call_order = self._run_remote(["ai", "c", "4", "--remote"], transport="mosh")
        assert mock_emit.called
        assert call_order.index("emit") < call_order.index("exec")

    def test_when_remote_ssh_then_emit_called_before_execvp(self):
        _, mock_emit, _, call_order = self._run_remote(["ai", "c", "4", "--remote"], transport="ssh")
        assert mock_emit.called
        assert call_order.index("emit") < call_order.index("exec")

    def test_when_remote_then_slot_uses_remote_session_name(self):
        mock_slot, _, _, _ = self._run_remote(["ai", "c", "4", "--remote"])
        slot_ai_name = mock_slot.call_args[0][0]
        assert "r" in slot_ai_name
        assert "4" in slot_ai_name

    def test_when_remote_gemini_then_emit_called_with_gemini_engine(self):
        _, mock_emit, _, _ = self._run_remote(["ai", "g", "2", "--remote"])
        assert mock_emit.called
        emit_engine = mock_emit.call_args[0][1]
        assert emit_engine == "g"
