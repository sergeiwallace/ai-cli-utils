from unittest.mock import MagicMock, patch

import pytest
from conftest import _run_cli_with_args

from ai_cli.main import cli

# --- --remote flag tests ---


@pytest.fixture(autouse=True)
def supported_platform_for_remote_unit_tests():
    """Exercise remote command construction independently of the host OS.

    Remote transport is deliberately rejected on native Windows. These tests
    mock every transport boundary and verify command construction, so they must
    use a supported platform to reach that code path.
    """
    with (
        patch("ai_cli.main.sys.platform", "linux"),
        patch("ai_cli.main.shutil.which", return_value="/usr/bin/tmux"),
    ):
        yield


def test_given_windows_when_remote_flag_used_then_exits_with_documented_error(capsys):
    # transport must be "ssh" -- the default (mosh) never reaches the Windows
    # check at all, since that check only guards the pure-SSH branch.
    config = {
        "remote": {
            "host": "example.com",
            "user": "user",
            "port": 22,
            "identity_file": "",
            "transport": "ssh",
        }
    }

    with (
        patch("ai_cli.main.sys.platform", "win32"),
        # shutil.which()'s own stdlib implementation branches on the real
        # sys.platform too, and its win32 branch needs the (real-Windows-only)
        # _winapi module -- patching platform alone crashes it on a non-Windows
        # test host before the code under test even reaches the Windows check.
        patch("ai_cli.main.shutil.which", return_value="/usr/bin/tmux"),
        patch("sys.argv", ["ai", "c", "1", "--remote"]),
        patch("ai_cli.config.load_config", return_value=config),
        patch("ai_cli.session.get_project_prefix", return_value="test-project"),
        patch("ai_cli.main.trigger_background_update"),
        patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=0),
        pytest.raises(SystemExit) as exc_info,
    ):
        cli()

    assert exc_info.value.code == 1
    assert "remote SSH transport is not supported on Windows" in capsys.readouterr().err


def test_remote_flag_when_host_configured_then_sshs_to_host():
    config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "port": 22, "identity_file": "", "transport": "ssh"}}
    mock_exec = _run_cli_with_args(["ai", "c", "1", "--remote"], config)
    mock_exec.assert_called_once()
    cmd, args = mock_exec.call_args[0]
    assert cmd == "zsh"
    bash_cmd = args[2]
    assert "ubuntu@1.2.3.4" in bash_cmd
    assert "-t" in bash_cmd
    assert "--is-remote" in bash_cmd and "1" in bash_cmd


def test_given_named_remote_default_when_remote_flag_used_then_ssh_uses_default_machine():
    config = {
        "remote": {
            "default": "fw",
            "machines": {
                "fw": {"host": "framework.example.com", "user": "dev", "port": 2222, "transport": "ssh"},
                "hz": {"host": "server.example.com", "user": "root", "transport": "ssh"},
            },
        }
    }
    mock_exec = _run_cli_with_args(["ai", "c", "1", "-R"], config)
    assert "dev@framework.example.com" in mock_exec.call_args[0][1][2]
    assert "-p 2222" in mock_exec.call_args[0][1][2]


def test_given_named_remote_alias_when_remote_machine_selected_then_ssh_uses_selected_machine():
    config = {
        "remote": {
            "default": "fw",
            "machines": {
                "fw": {"host": "framework.example.com", "user": "dev", "transport": "ssh"},
                "hz": {"host": "server.example.com", "user": "root", "port": 2200, "transport": "ssh"},
            },
        }
    }
    mock_exec = _run_cli_with_args(["ai", "c", "1", "-R", "-m", "hz"], config)
    assert "root@server.example.com" in mock_exec.call_args[0][1][2]
    assert "-p 2200" in mock_exec.call_args[0][1][2]


def test_given_unknown_remote_alias_when_remote_machine_selected_then_prints_configured_aliases(capsys):
    config = {
        "remote": {
            "default": "fw",
            "machines": {"fw": {"host": "framework.example.com"}, "hz": {"host": "server.example.com"}},
        }
    }
    with (
        patch("sys.argv", ["ai", "c", "1", "-R", "-m", "missing"]),
        patch("ai_cli.config.load_config", return_value=config),
        patch("ai_cli.main.trigger_background_update"),
        pytest.raises(SystemExit) as exc_info,
    ):
        cli()
    assert exc_info.value.code == 1
    assert "Remote machine 'missing' is not configured. Configured aliases: fw, hz" in capsys.readouterr().err


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
        patch("ai_cli.config.load_config", return_value=config),
        patch("ai_cli.session.get_project_prefix", return_value="sw"),
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
        patch("ai_cli.config.load_config", return_value=config),
        patch("ai_cli.session.get_project_prefix", return_value="test-project"),
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


class TestRemoteSessionIterm2Emit:
    """Verify iTerm2 profile/color is emitted before mosh/ssh connects for remote sessions."""

    def _run_remote(self, argv, transport="mosh"):
        config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "transport": transport}}
        call_order = []

        if transport == "mosh":
            mock_emit = MagicMock()
            mock_slot = MagicMock(return_value="#ff0000")
            mock_emit.side_effect = lambda *a, **kw: call_order.append("emit")

            async def fake_transport_loop(*args, **kwargs):
                call_order.append("exec")

            with (
                patch("sys.argv", argv),
                patch("ai_cli.config.load_config", return_value=config),
                patch("ai_cli.session.get_project_prefix", return_value="sw"),
                patch("ai_cli.config.get_project_aliases", return_value={}),
                patch("ai_cli.main.trigger_background_update"),
                patch("ai_cli.iterm2._assign_iterm2_color_slot", mock_slot),
                patch("ai_cli.iterm2._emit_iterm2_profile_setup", mock_emit),
                patch("ai_cli.transport._is_vpn_active", return_value=False),
                patch("ai_cli.transport._run_transport_loop", side_effect=fake_transport_loop),
                patch("ai_cli.transport._ensure_vpn_watcher"),
                patch("ai_cli.transport._maybe_stop_vpn_watcher"),
                patch("sys.exit", side_effect=SystemExit(0)),
            ):
                with pytest.raises(SystemExit):
                    cli()
            return mock_slot, mock_emit, None, call_order
        mock_exec = MagicMock()
        mock_emit = MagicMock()
        mock_slot = MagicMock(return_value="#ff0000")
        mock_emit.side_effect = lambda *a, **kw: call_order.append("emit")
        mock_exec.side_effect = lambda *a, **kw: (call_order.append("exec"), (_ for _ in ()).throw(SystemExit(0)))[1]
        with (
            patch("sys.argv", argv),
            patch("ai_cli.config.load_config", return_value=config),
            patch("ai_cli.session.get_project_prefix", return_value="sw"),
            patch("ai_cli.config.get_project_aliases", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.iterm2._assign_iterm2_color_slot", mock_slot),
            patch("ai_cli.iterm2._emit_iterm2_profile_setup", mock_emit),
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
