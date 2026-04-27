"""Tests for ai cdp start/stop/status subcommand."""

import sys
from unittest.mock import MagicMock, patch

import pytest

from ai_cli.main import (
    _cmd_cdp_start,
    _cmd_cdp_stop,
    _cmd_cdp_status,
    _find_chrome_binary,
    cli,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(binary_path="", port=9222):
    cfg = {}
    if binary_path or port != 9222:
        cfg["cdp"] = {}
        if binary_path:
            cfg["cdp"]["binary_path"] = binary_path
        if port != 9222:
            cfg["cdp"]["port"] = port
    return cfg


# ---------------------------------------------------------------------------
# _find_chrome_binary
# ---------------------------------------------------------------------------


class TestFindChromeBinary:
    def test_when_binary_path_configured_and_exists_then_returns_it(self, tmp_path):
        chrome = tmp_path / "chrome"
        chrome.write_text("")
        config = {"cdp": {"binary_path": str(chrome)}}
        assert _find_chrome_binary(config) == str(chrome)

    def test_when_binary_path_configured_but_missing_then_returns_none(self, tmp_path):
        config = {"cdp": {"binary_path": str(tmp_path / "nothere")}}
        assert _find_chrome_binary(config) is None

    def test_when_no_config_and_shutil_which_finds_candidate_then_returns_it(self):
        with patch("shutil.which", side_effect=lambda c: "/usr/bin/chromium" if c == "chromium" else None):
            with patch.object(sys, "platform", "linux"):
                result = _find_chrome_binary({})
        assert result == "/usr/bin/chromium"

    def test_when_no_config_and_path_exists_on_macos_then_returns_it(self):
        mac_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        with patch("shutil.which", return_value=None):
            with patch("ai_cli.main.Path.exists", return_value=True):
                with patch.object(sys, "platform", "darwin"):
                    result = _find_chrome_binary({})
        assert result == mac_path

    def test_when_nothing_found_then_returns_none(self):
        with patch("shutil.which", return_value=None):
            with patch("ai_cli.main.Path.exists", return_value=False):
                result = _find_chrome_binary({})
        assert result is None

    def test_when_windows_and_nothing_found_then_returns_none(self):
        with patch("shutil.which", return_value=None):
            with patch("ai_cli.main.Path.exists", return_value=False):
                with patch.object(sys, "platform", "win32"):
                    result = _find_chrome_binary({})
        assert result is None


# ---------------------------------------------------------------------------
# _cmd_cdp_start
# ---------------------------------------------------------------------------


@patch.object(sys, "platform", "linux")
class TestCmdCdpStart:
    def test_when_not_running_then_launches_chrome_and_writes_pid(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel._find_chrome_binary", return_value="/usr/bin/chromium"),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("urllib.request.urlopen"),
        ):
            _cmd_cdp_start(9222, True, {})

        pid_file = tmp_path / "cdp-9222.pid"
        assert pid_file.exists()
        assert pid_file.read_text() == "12345"

    def test_when_not_running_then_prints_ready(self, tmp_path, capsys):
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel._find_chrome_binary", return_value="/usr/bin/chromium"),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("urllib.request.urlopen"),
        ):
            _cmd_cdp_start(9222, True, {})

        assert "CDP ready at localhost:9222" in capsys.readouterr().out

    def test_when_readiness_poll_times_out_then_prints_warning(self, tmp_path, capsys):
        mock_proc = MagicMock()
        mock_proc.pid = 99
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel._find_chrome_binary", return_value="/usr/bin/chromium"),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("urllib.request.urlopen", side_effect=OSError("refused")),
            patch("ai_cli.main.time.monotonic", side_effect=[0.0, 10.0]),
            patch("ai_cli.main.time.sleep"),
        ):
            _cmd_cdp_start(9222, True, {})

        out = capsys.readouterr().out
        assert "not yet responding" in out
        assert "(PID 99)" in out

    def test_when_readiness_poll_retries_then_sleeps_and_succeeds(self, tmp_path, capsys):
        mock_proc = MagicMock()
        mock_proc.pid = 99
        # urlopen raises once, then succeeds; monotonic never exceeds deadline
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel._find_chrome_binary", return_value="/usr/bin/chromium"),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("urllib.request.urlopen", side_effect=[OSError("refused"), None]),
            patch("ai_cli.main.time.monotonic", side_effect=[0.0, 0.1, 0.2]),
            patch("ai_cli.main.time.sleep") as mock_sleep,
        ):
            _cmd_cdp_start(9222, True, {})

        mock_sleep.assert_called_once_with(0.25)
        assert "CDP ready" in capsys.readouterr().out

    def test_when_already_running_then_prints_message_and_returns(self, tmp_path, capsys):
        pid_file = tmp_path / "cdp-9222.pid"
        pid_file.write_text("55555")
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel._pid_alive", return_value=True),
            patch("subprocess.Popen") as mock_popen,
        ):
            _cmd_cdp_start(9222, True, {})

        mock_popen.assert_not_called()
        assert "already running" in capsys.readouterr().out

    def test_when_stale_pid_file_then_cleans_up_and_starts(self, tmp_path):
        pid_file = tmp_path / "cdp-9222.pid"
        pid_file.write_text("55555")
        mock_proc = MagicMock()
        mock_proc.pid = 66666
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel._pid_alive", return_value=False),
            patch("ai_cli.tunnel._find_chrome_binary", return_value="/usr/bin/chromium"),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("urllib.request.urlopen"),
        ):
            _cmd_cdp_start(9222, True, {})

        assert pid_file.read_text() == "66666"

    def test_when_no_chrome_found_then_exits_1(self, tmp_path, capsys):
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel._find_chrome_binary", return_value=None),
        ):
            with pytest.raises(SystemExit) as exc:
                _cmd_cdp_start(9222, True, {})

        assert exc.value.code == 1
        assert "Chrome/Chromium not found" in capsys.readouterr().err

    def test_when_incognito_false_then_flag_not_passed(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.pid = 1
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel._find_chrome_binary", return_value="/usr/bin/chromium"),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch("urllib.request.urlopen"),
        ):
            _cmd_cdp_start(9222, False, {})

        cmd = mock_popen.call_args[0][0]
        assert "--incognito" not in cmd

    def test_when_incognito_true_then_flag_passed(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.pid = 1
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel._find_chrome_binary", return_value="/usr/bin/chromium"),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch("urllib.request.urlopen"),
        ):
            _cmd_cdp_start(9222, True, {})

        cmd = mock_popen.call_args[0][0]
        assert "--incognito" in cmd

    def test_when_custom_port_then_pid_file_uses_that_port(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.pid = 1
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel._find_chrome_binary", return_value="/usr/bin/chromium"),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("urllib.request.urlopen"),
        ):
            _cmd_cdp_start(9333, True, {})

        assert (tmp_path / "cdp-9333.pid").exists()

    def test_when_tunnel_true_then_calls_tunnel_start_with_reverse(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.pid = 1
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel._find_chrome_binary", return_value="/usr/bin/chromium"),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("urllib.request.urlopen"),
            patch("ai_cli.tunnel._cmd_tunnel_start") as mock_tunnel,
        ):
            _cmd_cdp_start(9222, True, {}, tunnel=True, forward=False)

        mock_tunnel.assert_called_once_with(9222, 9222, forward=False, config={})

    def test_when_tunnel_true_and_forward_then_calls_tunnel_start_with_forward(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.pid = 1
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel._find_chrome_binary", return_value="/usr/bin/chromium"),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("urllib.request.urlopen"),
            patch("ai_cli.tunnel._cmd_tunnel_start") as mock_tunnel,
        ):
            _cmd_cdp_start(9222, True, {}, tunnel=True, forward=True)

        mock_tunnel.assert_called_once_with(9222, 9222, forward=True, config={})

    def test_when_tunnel_false_then_does_not_call_tunnel_start(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.pid = 1
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel._find_chrome_binary", return_value="/usr/bin/chromium"),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("urllib.request.urlopen"),
            patch("ai_cli.tunnel._cmd_tunnel_start") as mock_tunnel,
        ):
            _cmd_cdp_start(9222, True, {})

        mock_tunnel.assert_not_called()


# ---------------------------------------------------------------------------
# _cmd_cdp_start — macOS path
# ---------------------------------------------------------------------------


@patch.object(sys, "platform", "darwin")
class TestCmdCdpStartMacOS:
    def test_when_on_macos_then_uses_open_na_not_popen(self, tmp_path):
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch(
                "ai_cli.tunnel._find_chrome_binary",
                return_value="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            ),
            patch("subprocess.run") as mock_run,
            patch("subprocess.Popen") as mock_popen,
            patch("urllib.request.urlopen"),
            patch("ai_cli.tunnel._find_chrome_pid_by_port", return_value=12345),
        ):
            _cmd_cdp_start(9222, True, {})

        mock_popen.assert_not_called()
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[:3] == ["open", "-na", "Google Chrome"]
        assert "--remote-debugging-port=9222" in cmd
        assert "--args" in cmd

    def test_when_on_macos_with_chromium_then_derives_app_name_from_path(self, tmp_path):
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch(
                "ai_cli.tunnel._find_chrome_binary", return_value="/Applications/Chromium.app/Contents/MacOS/Chromium"
            ),
            patch("subprocess.run") as mock_run,
            patch("urllib.request.urlopen"),
            patch("ai_cli.tunnel._find_chrome_pid_by_port", return_value=99),
        ):
            _cmd_cdp_start(9222, True, {})

        cmd = mock_run.call_args[0][0]
        assert cmd[2] == "Chromium"

    def test_when_on_macos_and_ready_then_finds_pid_and_writes_pid_file(self, tmp_path):
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch(
                "ai_cli.tunnel._find_chrome_binary",
                return_value="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            ),
            patch("subprocess.run"),
            patch("urllib.request.urlopen"),
            patch("ai_cli.tunnel._find_chrome_pid_by_port", return_value=55555),
        ):
            _cmd_cdp_start(9222, True, {})

        pid_file = tmp_path / "cdp-9222.pid"
        assert pid_file.exists()
        assert pid_file.read_text() == "55555"

    def test_when_on_macos_and_no_pid_found_then_no_pid_file(self, tmp_path, capsys):
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch(
                "ai_cli.tunnel._find_chrome_binary",
                return_value="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            ),
            patch("subprocess.run"),
            patch("urllib.request.urlopen"),
            patch("ai_cli.tunnel._find_chrome_pid_by_port", return_value=None),
        ):
            _cmd_cdp_start(9222, True, {})

        assert not (tmp_path / "cdp-9222.pid").exists()

    def test_when_on_macos_passes_extra_first_run_flags(self, tmp_path):
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch(
                "ai_cli.tunnel._find_chrome_binary",
                return_value="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            ),
            patch("subprocess.run") as mock_run,
            patch("urllib.request.urlopen"),
            patch("ai_cli.tunnel._find_chrome_pid_by_port", return_value=1),
        ):
            _cmd_cdp_start(9222, True, {})

        cmd = mock_run.call_args[0][0]
        assert "--no-first-run" in cmd
        assert "--no-default-browser-check" in cmd
        assert "--disable-default-apps" in cmd


# ---------------------------------------------------------------------------
# _cmd_cdp_stop
# ---------------------------------------------------------------------------


class TestCmdCdpStop:
    def test_when_running_then_terminates_and_removes_pid(self, tmp_path, capsys):
        pid_file = tmp_path / "cdp-9222.pid"
        pid_file.write_text("12345")
        mock_proc = MagicMock()
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel.psutil.Process", return_value=mock_proc),
        ):
            _cmd_cdp_stop(9222)

        mock_proc.terminate.assert_called_once()
        assert not pid_file.exists()
        assert "stopped" in capsys.readouterr().out

    def test_when_no_pid_file_then_prints_message_and_returns(self, tmp_path, capsys):
        mock_proc = MagicMock()
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel.psutil.Process", return_value=mock_proc),
        ):
            _cmd_cdp_stop(9222)

        mock_proc.terminate.assert_not_called()
        assert "No CDP process registered" in capsys.readouterr().out

    def test_when_process_already_dead_then_still_removes_pid_file(self, tmp_path):
        import psutil as _psutil

        pid_file = tmp_path / "cdp-9222.pid"
        pid_file.write_text("12345")
        mock_proc = MagicMock()
        mock_proc.terminate.side_effect = _psutil.NoSuchProcess(12345)
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel.psutil.Process", return_value=mock_proc),
        ):
            _cmd_cdp_stop(9222)

        assert not pid_file.exists()

    def test_when_custom_port_then_reads_correct_pid_file(self, tmp_path):
        pid_file = tmp_path / "cdp-9333.pid"
        pid_file.write_text("77777")
        mock_proc = MagicMock()
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel.psutil.Process", return_value=mock_proc),
        ):
            _cmd_cdp_stop(9333)

        mock_proc.terminate.assert_called_once()

    def test_when_tunnel_true_then_calls_tunnel_stop(self, tmp_path):
        pid_file = tmp_path / "cdp-9222.pid"
        pid_file.write_text("12345")
        mock_proc = MagicMock()
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel.psutil.Process", return_value=mock_proc),
            patch("ai_cli.tunnel._cmd_tunnel_stop") as mock_tunnel_stop,
        ):
            _cmd_cdp_stop(9222, tunnel=True)

        mock_tunnel_stop.assert_called_once_with(9222)

    def test_when_tunnel_false_then_does_not_call_tunnel_stop(self, tmp_path):
        pid_file = tmp_path / "cdp-9222.pid"
        pid_file.write_text("12345")
        mock_proc = MagicMock()
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel.psutil.Process", return_value=mock_proc),
            patch("ai_cli.tunnel._cmd_tunnel_stop") as mock_tunnel_stop,
        ):
            _cmd_cdp_stop(9222)

        mock_tunnel_stop.assert_not_called()


# ---------------------------------------------------------------------------
# _cmd_cdp_status
# ---------------------------------------------------------------------------


class TestCmdCdpStatus:
    def test_when_no_pid_files_then_prints_none_registered(self, tmp_path, capsys):
        with patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path):
            _cmd_cdp_status()

        assert "No CDP processes registered" in capsys.readouterr().out

    def test_when_alive_process_then_reports_alive(self, tmp_path, capsys):
        (tmp_path / "cdp-9222.pid").write_text("12345")
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel._pid_alive", return_value=True),
        ):
            _cmd_cdp_status()

        assert "alive" in capsys.readouterr().out

    def test_when_dead_process_then_reports_dead_and_removes_pid(self, tmp_path, capsys):
        pid_file = tmp_path / "cdp-9222.pid"
        pid_file.write_text("12345")
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel._pid_alive", return_value=False),
        ):
            _cmd_cdp_status()

        assert "dead" in capsys.readouterr().out
        assert not pid_file.exists()

    def test_when_multiple_ports_then_reports_each(self, tmp_path, capsys):
        (tmp_path / "cdp-9222.pid").write_text("111")
        (tmp_path / "cdp-9333.pid").write_text("222")
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel._pid_alive", return_value=True),
        ):
            _cmd_cdp_status()

        out = capsys.readouterr().out
        assert "9222" in out
        assert "9333" in out


# ---------------------------------------------------------------------------
# Dispatch via cli()
# ---------------------------------------------------------------------------


class TestCdpDispatch:
    def test_when_cdp_start_then_calls_cmd_cdp_start(self, tmp_path):
        with (
            patch("sys.argv", ["ai", "cdp", "start"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.tunnel._cmd_cdp_start") as mock_start,
        ):
            with pytest.raises(SystemExit) as exc:
                cli()

        assert exc.value.code == 0
        mock_start.assert_called_once_with(9222, True, {}, tunnel=False, forward=False)

    def test_when_cdp_start_with_port_flag_then_uses_custom_port(self, tmp_path):
        with (
            patch("sys.argv", ["ai", "cdp", "start", "--port", "9333"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.tunnel._cmd_cdp_start") as mock_start,
        ):
            with pytest.raises(SystemExit) as exc:
                cli()

        assert exc.value.code == 0
        mock_start.assert_called_once_with(9333, True, {}, tunnel=False, forward=False)

    def test_when_cdp_start_with_short_port_flag_then_uses_custom_port(self):
        with (
            patch("sys.argv", ["ai", "cdp", "start", "-p", "8888"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.tunnel._cmd_cdp_start") as mock_start,
        ):
            with pytest.raises(SystemExit) as exc:
                cli()

        assert exc.value.code == 0
        mock_start.assert_called_once_with(8888, True, {}, tunnel=False, forward=False)

    def test_when_cdp_start_with_no_incognito_then_passes_false(self):
        with (
            patch("sys.argv", ["ai", "cdp", "start", "--no-incognito"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.tunnel._cmd_cdp_start") as mock_start,
        ):
            with pytest.raises(SystemExit) as exc:
                cli()

        assert exc.value.code == 0
        mock_start.assert_called_once_with(9222, False, {}, tunnel=False, forward=False)

    def test_when_cdp_start_with_short_no_incognito_flag_then_passes_false(self):
        with (
            patch("sys.argv", ["ai", "cdp", "start", "-I"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.tunnel._cmd_cdp_start") as mock_start,
        ):
            with pytest.raises(SystemExit) as exc:
                cli()

        assert exc.value.code == 0
        mock_start.assert_called_once_with(9222, False, {}, tunnel=False, forward=False)

    def test_when_cdp_start_and_config_has_port_then_uses_config_port(self):
        with (
            patch("sys.argv", ["ai", "cdp", "start"]),
            patch("ai_cli.config.load_config", return_value={"cdp": {"port": 9999}}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.tunnel._cmd_cdp_start") as mock_start,
        ):
            with pytest.raises(SystemExit) as exc:
                cli()

        assert exc.value.code == 0
        mock_start.assert_called_once_with(9999, True, {"cdp": {"port": 9999}}, tunnel=False, forward=False)

    def test_when_cdp_start_with_tunnel_flag_then_passes_tunnel_true(self):
        with (
            patch("sys.argv", ["ai", "cdp", "start", "--tunnel"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.tunnel._cmd_cdp_start") as mock_start,
        ):
            with pytest.raises(SystemExit) as exc:
                cli()

        assert exc.value.code == 0
        mock_start.assert_called_once_with(9222, True, {}, tunnel=True, forward=False)

    def test_when_cdp_start_with_short_t_tunnel_flag_then_passes_tunnel_true(self):
        with (
            patch("sys.argv", ["ai", "cdp", "start", "-t"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.tunnel._cmd_cdp_start") as mock_start,
        ):
            with pytest.raises(SystemExit) as exc:
                cli()

        assert exc.value.code == 0
        mock_start.assert_called_once_with(9222, True, {}, tunnel=True, forward=False)

    def test_when_cdp_start_with_tunnel_and_forward_flags_then_passes_both(self):
        with (
            patch("sys.argv", ["ai", "cdp", "start", "-t", "-L"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.tunnel._cmd_cdp_start") as mock_start,
        ):
            with pytest.raises(SystemExit) as exc:
                cli()

        assert exc.value.code == 0
        mock_start.assert_called_once_with(9222, True, {}, tunnel=True, forward=True)

    def test_when_cdp_stop_then_calls_cmd_cdp_stop(self):
        with (
            patch("sys.argv", ["ai", "cdp", "stop"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.tunnel._cmd_cdp_stop") as mock_stop,
        ):
            with pytest.raises(SystemExit) as exc:
                cli()

        assert exc.value.code == 0
        mock_stop.assert_called_once_with(9222, tunnel=False)

    def test_when_cdp_stop_with_port_flag_then_uses_custom_port(self):
        with (
            patch("sys.argv", ["ai", "cdp", "stop", "--port", "9333"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.tunnel._cmd_cdp_stop") as mock_stop,
        ):
            with pytest.raises(SystemExit) as exc:
                cli()

        assert exc.value.code == 0
        mock_stop.assert_called_once_with(9333, tunnel=False)

    def test_when_cdp_stop_with_short_port_flag_then_uses_custom_port(self):
        with (
            patch("sys.argv", ["ai", "cdp", "stop", "-p", "7777"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.tunnel._cmd_cdp_stop") as mock_stop,
        ):
            with pytest.raises(SystemExit) as exc:
                cli()

        assert exc.value.code == 0
        mock_stop.assert_called_once_with(7777, tunnel=False)

    def test_when_cdp_stop_with_tunnel_flag_then_passes_tunnel_true(self):
        with (
            patch("sys.argv", ["ai", "cdp", "stop", "--tunnel"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.tunnel._cmd_cdp_stop") as mock_stop,
        ):
            with pytest.raises(SystemExit) as exc:
                cli()

        assert exc.value.code == 0
        mock_stop.assert_called_once_with(9222, tunnel=True)

    def test_when_cdp_stop_with_short_t_tunnel_flag_then_passes_tunnel_true(self):
        with (
            patch("sys.argv", ["ai", "cdp", "stop", "-t"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.tunnel._cmd_cdp_stop") as mock_stop,
        ):
            with pytest.raises(SystemExit) as exc:
                cli()

        assert exc.value.code == 0
        mock_stop.assert_called_once_with(9222, tunnel=True)

    def test_when_cdp_status_then_calls_cmd_cdp_status(self):
        with (
            patch("sys.argv", ["ai", "cdp", "status"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
            patch("ai_cli.tunnel._cmd_cdp_status") as mock_status,
        ):
            with pytest.raises(SystemExit) as exc:
                cli()

        assert exc.value.code == 0
        mock_status.assert_called_once()

    def test_when_cdp_unknown_action_then_exits_1(self, capsys):
        with (
            patch("sys.argv", ["ai", "cdp", "restart"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()

        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "No such command 'restart'" in err or "Unknown cdp action" in err

    def test_when_cdp_no_action_then_exits_1(self, capsys):
        with (
            patch("sys.argv", ["ai", "cdp"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.main.trigger_background_update"),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()

        assert exc.value.code == 1
        assert "Usage" in capsys.readouterr().err
