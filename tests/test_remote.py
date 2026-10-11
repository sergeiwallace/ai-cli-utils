import io
import json
import shlex
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import _run_cli_with_args

from ai_cli.main import _REMOTE_SHELL_PROBE_CMD, _resolve_remote_shell, _update_remote_ai_cli, cli
from ai_cli.session import build_session_name

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
        # The remote-shell probe runs before the Windows check below it, and
        # would otherwise make a real, unmocked SSH connection attempt to
        # "example.com" during this test.
        patch("ai_cli.main.subprocess.run") as mock_probe,
        pytest.raises(SystemExit) as exc_info,
    ):
        cli()

    assert exc_info.value.code == 1
    assert "remote SSH transport is not supported on Windows" in capsys.readouterr().err
    assert mock_probe.called


def test_remote_flag_when_host_configured_then_sshs_to_host():
    config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "port": 22, "identity_file": "", "transport": "ssh"}}
    # Asserted against the ssh argv list rather than a joined shell string: this
    # path runs in-process now so a dropped link can be reattached (AI-CLI-w679).
    runner = _run_cli_with_args(["ai", "c", "1", "--remote"], config, capture_ssh_runner=True)
    runner.assert_called_once()
    ssh_args = runner.call_args[0][0]
    assert ssh_args[0] == "ssh"
    assert "ubuntu@1.2.3.4" in ssh_args
    assert "-t" in ssh_args
    assert "--is-remote" in ssh_args[-1] and "1" in ssh_args[-1]


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
    runner = _run_cli_with_args(["ai", "c", "1", "-R"], config, capture_ssh_runner=True)
    ssh_args = runner.call_args[0][0]
    assert "dev@framework.example.com" in ssh_args
    assert ssh_args[ssh_args.index("-p") + 1] == "2222"


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
    runner = _run_cli_with_args(["ai", "c", "1", "-R", "-m", "hz"], config, capture_ssh_runner=True)
    ssh_args = runner.call_args[0][0]
    assert "root@server.example.com" in ssh_args
    assert ssh_args[ssh_args.index("-p") + 1] == "2200"


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


def test_given_default_remote_alias_when_ssh_called_then_execs_configured_machine():
    config = {
        "remote": {
            "default": "primary",
            "machines": {
                "primary": {
                    "host": "primary.example.com",
                    "user": "user",
                    "port": 2222,
                    "identity_file": "~/.ssh/id_primary",
                }
            },
        }
    }
    mock_exec = _run_cli_with_args(["ai", "ssh"], config)

    mock_exec.assert_called_once_with(
        "ssh", ["ssh", "-p", "2222", "-i", str(Path("~/.ssh/id_primary").expanduser()), "user@primary.example.com"]
    )


def test_given_explicit_remote_alias_when_ssh_called_then_execs_selected_machine():
    config = {
        "remote": {
            "default": "primary",
            "machines": {
                "primary": {"host": "primary.example.com"},
                "backup": {
                    "host": "backup.example.com",
                    "user": "admin",
                    "port": 2200,
                    "identity_file": "~/.ssh/id_backup",
                },
            },
        }
    }
    mock_exec = _run_cli_with_args(["ai", "ssh", "backup"], config)

    mock_exec.assert_called_once_with(
        "ssh", ["ssh", "-p", "2200", "-i", str(Path("~/.ssh/id_backup").expanduser()), "admin@backup.example.com"]
    )


def test_given_unknown_remote_alias_when_ssh_called_then_exits_without_exec(capsys):
    config = {
        "remote": {
            "machines": {
                "primary": {"host": "primary.example.com"},
                "backup": {"host": "backup.example.com"},
            }
        }
    }
    with (
        patch("sys.argv", ["ai", "ssh", "missing"]),
        patch("ai_cli.config.load_config", return_value=config),
        patch("ai_cli.main.os.execvp") as mock_exec,
        pytest.raises(SystemExit) as exc_info,
    ):
        cli()

    assert exc_info.value.code == 1
    assert "Remote machine 'missing' is not configured. Configured aliases: backup, primary" in capsys.readouterr().err
    mock_exec.assert_not_called()


def test_given_windows_when_ssh_called_then_exits_without_exec(capsys):
    with (
        patch("ai_cli.main.sys.platform", "win32"),
        patch("sys.argv", ["ai", "ssh"]),
        patch("ai_cli.main.os.execvp") as mock_exec,
        pytest.raises(SystemExit) as exc_info,
    ):
        cli()

    assert exc_info.value.code == 1
    assert "SSH shells are not supported on Windows" in capsys.readouterr().err
    mock_exec.assert_not_called()


def test_given_alias_with_no_host_when_ssh_called_then_exits_without_exec(capsys):
    config = {"remote": {"default": "primary", "machines": {"primary": {"user": "user"}}}}
    with (
        patch("sys.argv", ["ai", "ssh"]),
        patch("ai_cli.config.load_config", return_value=config),
        patch("ai_cli.main.os.execvp") as mock_exec,
        pytest.raises(SystemExit) as exc_info,
    ):
        cli()

    assert exc_info.value.code == 1
    assert "[remote] host not set" in capsys.readouterr().err
    mock_exec.assert_not_called()


def test_remote_flag_when_host_configured_then_passes_is_remote_flag():
    config = {"remote": {"host": "hetzner-dev", "user": "ubuntu", "port": 22, "identity_file": "", "transport": "ssh"}}
    runner = _run_cli_with_args(["ai", "g", "research", "--remote"], config, capture_ssh_runner=True)
    runner.assert_called_once()
    ssh_args = runner.call_args[0][0]
    assert "ubuntu@hetzner-dev" in ssh_args
    assert "ai g --is-remote" in ssh_args[-1] and "research" in ssh_args[-1]


def test_remote_flag_when_called_then_passes_project_prefix_to_server():
    """Server receives --project-prefix so session uses local project tag, not remote cwd."""
    config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "port": 22, "identity_file": "", "transport": "ssh"}}
    with (
        patch("sys.argv", ["ai", "c", "1", "--remote"]),
        patch("ai_cli.config.load_config", return_value=config),
        patch("ai_cli.session.get_project_prefix", return_value="session"),
        patch("ai_cli.transport.run_ssh_with_reconnect", return_value=0) as runner,
        patch("ai_cli.main.trigger_background_update"),
    ):
        try:
            cli()
        except SystemExit:
            pass
    ssh_args = runner.call_args[0][0]
    assert any("--project-prefix session" in a for a in ssh_args)


def test_remote_flag_with_resume_when_called_then_forwards_resume_to_server():
    config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "port": 22, "identity_file": "", "transport": "ssh"}}
    runner = _run_cli_with_args(["ai", "c", "-r", "1", "--remote"], config, capture_ssh_runner=True)
    runner.assert_called_once()
    ssh_args = runner.call_args[0][0]
    assert any("--resume" in a for a in ssh_args)


# --- remote shell resolution (AI-CLI-gg9s regression) ---
#
# Root cause: mosh/ssh remote commands hardcoded "zsh -l -c <cmd>" as the
# interpreter run on the REMOTE host. A remote host without zsh (a minimal
# Fedora box, confirmed live against the Framework machine) fails with
# mosh-server's own execvp error -- invisible to the user because mosh's
# terminal-restore erases the diagnostic before printing "[mosh is
# exiting.]", so the launch appears to silently do nothing.


class TestResolveRemoteShell:
    def test_given_probe_finds_zsh_when_resolved_then_returns_zsh(self):
        with patch(
            "ai_cli.main.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="/usr/bin/zsh\n", stderr=""),
        ) as mock_run:
            result = _resolve_remote_shell(["ssh", "-T", "user@host"])
        assert result == "/usr/bin/zsh"
        assert mock_run.call_args[0][0][-1] == _REMOTE_SHELL_PROBE_CMD

    def test_given_probe_finds_only_bash_when_resolved_then_returns_bash(self):
        with patch(
            "ai_cli.main.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="/usr/bin/bash\n", stderr=""),
        ):
            result = _resolve_remote_shell(["ssh", "-T", "user@host"])
        assert result == "/usr/bin/bash"

    def test_given_probe_raises_when_resolved_then_falls_back_to_bash(self):
        with patch("ai_cli.main.subprocess.run", side_effect=TimeoutError("no route")):
            result = _resolve_remote_shell(["ssh", "-T", "user@host"])
        assert result == "bash"

    def test_given_probe_returns_empty_stdout_when_resolved_then_falls_back_to_bash(self):
        with patch(
            "ai_cli.main.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="", stderr=""),
        ):
            result = _resolve_remote_shell(["ssh", "-T", "user@host"])
        assert result == "bash"

    def test_given_probe_returns_non_string_stdout_when_resolved_then_falls_back_to_bash(self):
        """An unconfigured MagicMock().stdout must never be treated as a real shell path."""
        with patch("ai_cli.main.subprocess.run", return_value=MagicMock()):
            result = _resolve_remote_shell(["ssh", "-T", "user@host"])
        assert result == "bash"


# --- remote host auto-update (AI-CLI-dw1g deployment-gap follow-up) ---
#
# A remote launch runs the generated supervisor entirely from the REMOTE
# host's own installed ai-cli-utils. A fix merged to this repo's own main does
# nothing for a remote session until that host's checkout is pulled and
# reinstalled, and nothing else keeps a remote host's checkout current --
# measured stale on both configured remote machines by the time this was
# written. `_update_remote_ai_cli` closes that gap by running `ai update
# --quiet` on the remote host before every remote launch.


class TestUpdateRemoteAiCli:
    def test_given_update_succeeds_when_called_then_returns_true_with_its_output(self):
        with patch(
            "ai_cli.main.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="0.8.0 → 0.8.0.post20260927120000\n", stderr=""),
        ) as mock_run:
            ok, detail = _update_remote_ai_cli(["ssh", "-T", "user@host"], "zsh")
        assert ok is True
        assert "0.8.0.post20260927120000" in detail
        remote_command = mock_run.call_args[0][0][-1]
        assert "ai update --quiet" in remote_command

    def test_given_update_fails_when_called_then_returns_false_without_raising(self):
        with patch(
            "ai_cli.main.subprocess.run",
            return_value=MagicMock(returncode=1, stdout="", stderr="git pull failed: no network"),
        ):
            ok, detail = _update_remote_ai_cli(["ssh", "-T", "user@host"], "zsh")
        assert ok is False
        assert "no network" in detail

    def test_given_update_times_out_when_called_then_returns_false_without_raising(self):
        with patch("ai_cli.main.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=30)):
            ok, detail = _update_remote_ai_cli(["ssh", "-T", "user@host"], "zsh")
        assert ok is False
        assert "timed out" in detail

    def test_given_update_raises_unexpectedly_when_called_then_returns_false_without_raising(self):
        """A preflight step must never crash the launch -- any failure degrades, never raises."""
        with patch("ai_cli.main.subprocess.run", side_effect=RuntimeError("unexpected")):
            ok, detail = _update_remote_ai_cli(["ssh", "-T", "user@host"], "zsh")
        assert ok is False
        assert "unexpected" in detail


def test_given_dry_run_when_remote_launch_previewed_then_remote_update_is_not_attempted():
    """A dry run must not mutate the remote host any more than it may mutate this one.

    Full network-call coverage for the remote dry-run path (AI-CLI-shpu) lives
    in ``test_session_dry_run.py`` alongside its local-path sibling; this test
    pins the cli()-level integration through ``_run_cli_with_args``.
    """
    config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "port": 22, "identity_file": "", "transport": "ssh"}}
    with patch("ai_cli.main._update_remote_ai_cli") as mock_update:
        _run_cli_with_args(["ai", "c", "1", "--remote", "--dry-run"], config)
    mock_update.assert_not_called()


def test_given_remote_host_lacks_zsh_when_launched_then_uses_probed_shell_not_hardcoded_zsh():
    config = {"remote": {"host": "fw.example.com", "user": "dev", "port": 22, "identity_file": "", "transport": "ssh"}}

    def fake_probe(command, **_kwargs):
        if command[-1] == _REMOTE_SHELL_PROBE_CMD:
            return MagicMock(returncode=0, stdout="/usr/bin/bash\n", stderr="")
        return MagicMock(returncode=1, stdout="")

    with (
        patch("sys.argv", ["ai", "c", "1", "--remote"]),
        patch("ai_cli.config.load_config", return_value=config),
        patch("ai_cli.session.get_project_prefix", return_value="session"),
        patch("ai_cli.transport.run_ssh_with_reconnect", return_value=0) as runner,
        patch("ai_cli.main.trigger_background_update"),
        patch("ai_cli.main.subprocess.run", side_effect=fake_probe),
    ):
        with pytest.raises(SystemExit):
            cli()

    # The remote command is the last argv element. "zsh" must be absent from the
    # whole argv now, not merely from that element: this path no longer wraps the
    # session in a `zsh -c`, so a stray "zsh" anywhere would be the old hardcoding.
    ssh_args = runner.call_args[0][0]
    assert "bin/bash -l -c" in ssh_args[-1]
    assert not any("zsh" in a for a in ssh_args)


def test_remote_flag_without_resume_when_called_then_no_resume_in_cmd():
    config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "port": 22, "identity_file": "", "transport": "ssh"}}
    runner = _run_cli_with_args(["ai", "c", "1", "--remote"], config, capture_ssh_runner=True)
    ssh_args = runner.call_args[0][0]
    assert not any("--resume" in a for a in ssh_args)


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
    runner = _run_cli_with_args(["ai", "c", "--remote"], config, capture_ssh_runner=True)
    runner.assert_called_once()
    bash_cmd = shlex.join(runner.call_args[0][0])
    assert "-i" in bash_cmd


def test_remote_flag_when_host_not_configured_then_exits_with_error():
    config = {"remote": {"host": "", "user": "ubuntu", "port": 22, "identity_file": ""}}
    with (
        patch("sys.argv", ["ai", "c", "--remote"]),
        patch("ai_cli.config.load_config", return_value=config),
        patch("ai_cli.session.get_project_prefix", return_value="test-project"),
        patch("ai_cli.main.trigger_background_update"),
        patch("sys.stderr", io.StringIO()),
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

    def _run_remote(self, argv, transport="mosh", preflight_run=None, vscode_authority=None):
        config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "transport": transport}}
        if vscode_authority is not None:
            config["remote"]["vscode_authority"] = vscode_authority
        call_order = []
        mock_preflight_run = preflight_run or MagicMock()

        if transport == "mosh":
            mock_emit = MagicMock()
            mock_slot = MagicMock(return_value="#ff0000")
            mock_emit.side_effect = lambda *a, **kw: call_order.append("emit")

            async def fake_transport_loop(*args, **kwargs):
                call_order.append("exec")

            with (
                patch("sys.argv", argv),
                patch("ai_cli.config.load_config", return_value=config),
                patch("ai_cli.session.get_project_prefix", return_value="session"),
                patch("ai_cli.config.get_project_aliases", return_value={}),
                patch("ai_cli.main.trigger_background_update"),
                patch("ai_cli.iterm2._assign_iterm2_color_slot", mock_slot),
                patch("ai_cli.iterm2._emit_iterm2_profile_setup", mock_emit),
                patch("ai_cli.main.subprocess.run", mock_preflight_run),
                patch("ai_cli.transport._is_vpn_active", return_value=False),
                patch("ai_cli.transport._run_transport_loop", side_effect=fake_transport_loop),
                patch("ai_cli.transport._ensure_vpn_watcher"),
                patch("ai_cli.transport._maybe_stop_vpn_watcher"),
                patch("sys.exit", side_effect=SystemExit(0)),
            ):
                with pytest.raises(SystemExit):
                    cli()
            return mock_slot, mock_emit, None, call_order
        # The SSH branch's handoff is `transport.run_ssh_with_reconnect`, not an exec:
        # it runs in-process so a dropped link can be reattached (AI-CLI-w679). The
        # ordering under test is unchanged -- the profile must be emitted before the
        # session is handed off -- only the boundary that marks the handoff moved.
        mock_handoff = MagicMock()
        mock_emit = MagicMock()
        mock_slot = MagicMock(return_value="#ff0000")
        mock_emit.side_effect = lambda *a, **kw: call_order.append("emit")
        mock_handoff.side_effect = lambda *a, **kw: (call_order.append("exec"), 0)[1]
        with (
            patch("sys.argv", argv),
            patch("ai_cli.config.load_config", return_value=config),
            patch("ai_cli.session.get_project_prefix", return_value="session"),
            patch("ai_cli.config.get_project_aliases", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.iterm2._assign_iterm2_color_slot", mock_slot),
            patch("ai_cli.iterm2._emit_iterm2_profile_setup", mock_emit),
            patch("ai_cli.main.subprocess.run", mock_preflight_run),
            patch("ai_cli.transport.run_ssh_with_reconnect", mock_handoff),
        ):
            with pytest.raises(SystemExit):
                cli()
        return mock_slot, mock_emit, mock_handoff, call_order

    def test_given_remote_host_lacks_zsh_when_mosh_launched_then_uses_probed_shell_not_hardcoded_zsh(self):
        """The mosh_args path is the one that actually broke against Framework
        (AI-CLI-gg9s) -- mosh-server execvp'd the hardcoded "zsh" literal and
        crashed with no visible diagnostic. ssh_args shares the same fix but
        is covered separately."""
        config = {"remote": {"host": "fw.example.com", "user": "dev", "transport": "mosh"}}
        captured = {}

        def fake_probe(command, **_kwargs):
            if command[-1] == _REMOTE_SHELL_PROBE_CMD:
                return MagicMock(returncode=0, stdout="/usr/bin/bash\n", stderr="")
            return MagicMock(returncode=1, stdout="")

        async def fake_transport_loop(ssh_args, mosh_args, *_args, **_kwargs):
            captured["mosh_args"] = mosh_args
            captured["ssh_args"] = ssh_args

        with (
            patch("sys.argv", ["ai", "c", "1", "--remote"]),
            patch("ai_cli.config.load_config", return_value=config),
            patch("ai_cli.session.get_project_prefix", return_value="session"),
            patch("ai_cli.config.get_project_aliases", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
            patch("ai_cli.iterm2._emit_iterm2_profile_setup"),
            patch("ai_cli.main.subprocess.run", side_effect=fake_probe),
            patch("ai_cli.transport._is_vpn_active", return_value=False),
            patch("ai_cli.transport._run_transport_loop", side_effect=fake_transport_loop),
            patch("ai_cli.transport._ensure_vpn_watcher"),
            patch("ai_cli.transport._maybe_stop_vpn_watcher"),
        ):
            with pytest.raises(SystemExit):
                cli()

        assert "/usr/bin/bash" in captured["mosh_args"]
        assert "zsh" not in captured["mosh_args"]

    def test_when_remote_mosh_command_fails_then_saves_stderr_for_read_only_diagnostic(self):
        config = {"remote": {"host": "server.example.com", "user": "dev", "transport": "mosh"}}
        captured = {}

        async def fake_transport_loop(_ssh_args, mosh_args, *_args, **kwargs):
            captured["mosh_args"] = mosh_args
            captured["diagnostic_ssh_args"] = kwargs["diagnostic_ssh_args"]
            captured["remote_diagnostic_file"] = kwargs["remote_diagnostic_file"]

        with (
            patch("sys.argv", ["ai", "c", "1", "--remote"]),
            patch("ai_cli.config.load_config", return_value=config),
            patch("ai_cli.session.get_project_prefix", return_value="session"),
            patch("ai_cli.config.get_project_aliases", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
            patch("ai_cli.iterm2._emit_iterm2_profile_setup"),
            patch("ai_cli.main.subprocess.run", return_value=MagicMock(stdout="/bin/bash\n", returncode=0)),
            patch("ai_cli.transport._is_vpn_active", return_value=False),
            patch("ai_cli.transport._run_transport_loop", side_effect=fake_transport_loop),
            patch("ai_cli.transport._ensure_vpn_watcher"),
            patch("ai_cli.transport._maybe_stop_vpn_watcher"),
        ):
            with pytest.raises(SystemExit):
                cli()

        remote_command = captured["mosh_args"][-1]
        assert '2>"$diagnostic_file"' in remote_command
        assert 'rm -f "$diagnostic_file"' in remote_command
        assert captured["remote_diagnostic_file"].endswith(".stderr")
        assert captured["diagnostic_ssh_args"][-1] == "dev@server.example.com"

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

    def test_given_named_remote_launch_when_previewed_then_local_identity_matches_remote_allocation(self):
        """The client preview must use the server's canonical named-session ID."""
        remote_allocations = []

        def remote_preflight(command, **_kwargs):
            if command[0] == "tmux":
                return MagicMock(returncode=1, stdout="")
            if command[-1] == _REMOTE_SHELL_PROBE_CMD:
                return MagicMock(returncode=0, stdout="zsh\n", stderr="")
            if "ai update" in command[-1]:
                return MagicMock(returncode=0, stdout="current", stderr="")
            remote_allocations.append(command)
            session_id, ai_name = build_session_name("c", "session", "Planning", is_remote=True)
            return MagicMock(returncode=0, stdout=json.dumps({"session_id": session_id, "ai_name": ai_name}), stderr="")

        with patch("ai_cli.session._matching_tmux_sessions", return_value=[]):
            mock_slot, mock_emit, mock_exec, _ = self._run_remote(
                ["ai", "c", "Planning", "--remote"], transport="ssh", preflight_run=remote_preflight
            )
            remote_session_id, _ = build_session_name("c", "session", "Planning", is_remote=True)

        assert mock_exec is not None
        assert mock_slot.call_args[0][0] == remote_session_id
        assert mock_emit.call_args[0][0] == remote_session_id
        assert mock_emit.call_args[0][2] == remote_session_id
        # Both argv lists the handoff receives, joined. The old form read a single
        # index of a joined `zsh -c` string that carried the ssh command and the
        # cleanup command together; the handoff now takes them as two arguments, so
        # searching both preserves the original claim -- the locally-allocated
        # identity reaches the handoff -- without betting on which half holds it.
        handoff = shlex.join([*mock_exec.call_args[0][0], *mock_exec.call_args[0][1]])
        assert remote_session_id in handoff
        assert len(remote_allocations) == 1

    def test_given_unnamed_remote_launches_when_dispatched_then_each_uses_its_own_remote_identity(self):
        """Closing one wrapper must not clean up another wrapper's transport state."""
        allocations = iter(["c-r-session-1", "c-r-session-2"])
        transport_calls = []

        def remote_preflight(command, **_kwargs):
            if command[-1] == _REMOTE_SHELL_PROBE_CMD:
                return MagicMock(returncode=0, stdout="zsh\n", stderr="")
            if "ai update" in command[-1]:
                return MagicMock(returncode=0, stdout="current", stderr="")
            session_id = next(allocations)
            return MagicMock(
                returncode=0,
                stdout=json.dumps({"session_id": session_id, "ai_name": session_id.removeprefix("c-r-")}),
                stderr="",
            )

        async def fake_transport_loop(_ssh_args, _mosh_args, cleanup_cmd, session_name, _config, **_kwargs):
            transport_calls.append((session_name, cleanup_cmd))

        config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "transport": "mosh"}}
        with (
            patch("sys.argv", ["ai", "c", "-R"]),
            patch("ai_cli.config.load_config", return_value=config),
            patch("ai_cli.session.get_project_prefix", return_value="session"),
            patch("ai_cli.config.get_project_aliases", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.main.subprocess.run", side_effect=remote_preflight),
            patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
            patch("ai_cli.iterm2._emit_iterm2_profile_setup"),
            patch("ai_cli.transport._is_vpn_active", return_value=False),
            patch("ai_cli.transport._run_transport_loop", side_effect=fake_transport_loop),
            patch("ai_cli.transport._ensure_vpn_watcher"),
            patch("ai_cli.transport._maybe_stop_vpn_watcher"),
        ):
            for _ in range(2):
                with pytest.raises(SystemExit):
                    cli()

        assert transport_calls == [
            ("c-r-session-1", ["ai", "internal", "cleanup-session-files", "c-r-session-1"]),
            ("c-r-session-2", ["ai", "internal", "cleanup-session-files", "c-r-session-2"]),
        ]

    def test_when_remote_gemini_then_emit_called_with_gemini_engine(self):
        _, mock_emit, _, _ = self._run_remote(["ai", "g", "2", "--remote"])
        assert mock_emit.called
        emit_engine = mock_emit.call_args[0][1]
        assert emit_engine == "g"

    def test_given_remote_vscode_authority_when_launching_then_profile_receives_authority(self):
        _, mock_emit, _, _ = self._run_remote(
            ["ai", "c", "4", "--remote"],
            vscode_authority="framework",
        )

        assert mock_emit.call_args.kwargs["vscode_authority"] == "framework"


# --- remote attach to a session the remote host launched itself (AI-CLI-wyku) ---
#
# Root cause: the remote allocator answers with the existing live session for a
# slot, and a session the remote host launched itself is named `c-<ai_name>`, not
# `c-r-<ai_name>`. The client accepted only `c-r-`, so `ai c firstmate-1 -R` from
# another machine failed with "invalid identity" instead of attaching.


def _run_remote_named_launch(argv, live_remote_sessions, allocator_reply=None):
    """Drive `ai c <name> -R` through cli() with SSH stubbed.

    The allocation reply comes from the real remote allocator code
    (`build_session_name(..., is_remote=True)`) over the given live tmux names,
    unless ``allocator_reply`` overrides it with a raw JSON body.
    """
    config = {"remote": {"host": "1.2.3.4", "user": "ubuntu", "transport": "ssh"}}

    def remote_ssh(command, **_kwargs):
        if command[-1] == _REMOTE_SHELL_PROBE_CMD:
            return MagicMock(returncode=0, stdout="zsh\n", stderr="")
        if "ai update" in command[-1]:
            return MagicMock(returncode=0, stdout="current", stderr="")
        if allocator_reply is not None:
            return MagicMock(returncode=0, stdout=allocator_reply, stderr="")
        tokens = shlex.split(shlex.split(command[-1])[-1])
        engine, prefix, name = tokens[tokens.index("allocate-session-name") + 1 :][:3]
        with patch("ai_cli.session._tmux_session_names", return_value=live_remote_sessions):
            session_id, ai_name = build_session_name(engine, prefix, name, is_remote=True)
        return MagicMock(returncode=0, stdout=json.dumps({"session_id": session_id, "ai_name": ai_name}), stderr="")

    handoff = MagicMock(return_value=0)
    stderr = io.StringIO()
    with (
        patch("sys.argv", argv),
        patch("ai_cli.config.load_config", return_value=config),
        patch("ai_cli.session.get_project_prefix", return_value="aih"),
        patch("ai_cli.config.get_project_aliases", return_value={}),
        patch("ai_cli.config.resolve_project_prefix_by_name", return_value="aih"),
        patch("ai_cli.main.trigger_background_update"),
        patch("ai_cli.main._track_launch"),
        patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
        patch("ai_cli.iterm2._emit_iterm2_profile_setup"),
        patch("ai_cli.main.subprocess.run", side_effect=remote_ssh),
        patch("ai_cli.transport.run_ssh_with_reconnect", handoff),
        patch("sys.stderr", stderr),
        pytest.raises(SystemExit) as exc_info,
    ):
        cli()
    return exc_info.value.code, handoff, stderr.getvalue()


def _remote_session_arg(handoff) -> str:
    return shlex.split(shlex.split(handoff.call_args[0][0][-1])[-1])[-1]


@pytest.mark.parametrize("argv_tail", [[], ["-p", "aih"]])
def test_given_live_local_prefixed_remote_session_when_remote_launched_then_attaches_that_session(argv_tail):
    code, handoff, stderr = _run_remote_named_launch(
        ["ai", "c", "firstmate-1", "-R", *argv_tail], live_remote_sessions=["c-aih-firstmate-1"]
    )

    assert code in (0, None), stderr
    assert "invalid identity" not in stderr
    handoff.assert_called_once()
    assert _remote_session_arg(handoff) == "c-aih-firstmate-1"
    assert handoff.call_args[0][1] == ["ai", "internal", "cleanup-session-files", "c-aih-firstmate-1"]


def test_given_no_live_remote_session_when_remote_launched_then_allocates_remote_prefixed_slot():
    code, handoff, stderr = _run_remote_named_launch(["ai", "c", "firstmate-1", "-R"], live_remote_sessions=[])

    assert code in (0, None), stderr
    assert _remote_session_arg(handoff) == "c-r-aih-firstmate-1"


@pytest.mark.parametrize(
    "reply",
    [
        json.dumps({"session_id": "c-other-1", "ai_name": "aih-firstmate-1"}),
        json.dumps({"session_id": "g-aih-firstmate-1", "ai_name": "aih-firstmate-1"}),
        json.dumps({"session_id": "c-", "ai_name": ""}),
        json.dumps({"session_id": 7, "ai_name": "aih-firstmate-1"}),
    ],
)
def test_given_malformed_allocator_identity_when_remote_launched_then_errors_before_handoff(reply):
    code, handoff, stderr = _run_remote_named_launch(
        ["ai", "c", "firstmate-1", "-R"], live_remote_sessions=[], allocator_reply=reply
    )

    assert code == 1
    assert "remote session-name allocation returned" in stderr
    handoff.assert_not_called()
