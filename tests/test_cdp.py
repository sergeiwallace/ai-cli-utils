"""Tests for ai cdp start/stop/status subcommand."""

import socket
import sys
from unittest.mock import MagicMock, patch

import pytest

from ai_cli import tunnel
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
    @pytest.fixture(autouse=True)
    def _requested_port_free(self):
        # Keep these tests deterministic: the requested port is free, so the
        # auto-increment path is not taken (that path has its own test class).
        with patch("ai_cli.tunnel._port_in_use", return_value=False):
            yield

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


class TestClearStaleSingletonLock:
    """Self-heal: a stale SingletonLock from a dead PID blocks Chrome launch."""

    def _make_locks(self, d, pid):
        (d / "SingletonLock").symlink_to(f"myhost-{pid}")
        (d / "SingletonCookie").symlink_to("12345")
        (d / "SingletonSocket").symlink_to("/tmp/sock")

    def test_when_lock_pid_dead_then_removes_all_singleton_artifacts(self, tmp_path):
        self._make_locks(tmp_path, 999111)
        with patch("ai_cli.tunnel._pid_alive", return_value=False):
            cleared = tunnel._clear_stale_singleton_lock(tmp_path)
        assert cleared is True
        assert not (tmp_path / "SingletonLock").is_symlink()
        assert not (tmp_path / "SingletonCookie").is_symlink()
        assert not (tmp_path / "SingletonSocket").is_symlink()

    def test_when_lock_pid_alive_then_keeps_lock(self, tmp_path):
        self._make_locks(tmp_path, 4242)
        with patch("ai_cli.tunnel._pid_alive", return_value=True):
            cleared = tunnel._clear_stale_singleton_lock(tmp_path)
        assert cleared is False
        assert (tmp_path / "SingletonLock").is_symlink()

    def test_when_no_lock_then_noop_returns_false(self, tmp_path):
        assert tunnel._clear_stale_singleton_lock(tmp_path) is False

    def test_when_lock_target_unparseable_then_returns_false(self, tmp_path):
        (tmp_path / "SingletonLock").symlink_to("no-pid-here-xyz")
        with patch("ai_cli.tunnel._pid_alive", return_value=False) as mock_alive:
            cleared = tunnel._clear_stale_singleton_lock(tmp_path)
        assert cleared is False
        assert not mock_alive.called  # never reached the liveness check
        assert (tmp_path / "SingletonLock").is_symlink()

    @patch.object(sys, "platform", "linux")
    def test_cmd_cdp_start_clears_stale_lock_before_launch(self, tmp_path):
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel.get_xdg_data_home", return_value=tmp_path),
            patch("ai_cli.tunnel._find_chrome_binary", return_value="/usr/bin/chromium"),
            patch("ai_cli.tunnel._clear_stale_singleton_lock") as mock_clear,
            patch("subprocess.Popen", return_value=MagicMock(pid=1)),
            patch("urllib.request.urlopen"),
        ):
            _cmd_cdp_start(9222, False, {})
        mock_clear.assert_called_once()


# ---------------------------------------------------------------------------
# Occupied-port robustness (AI-CLI: detect foreign holder + auto-increment)
# ---------------------------------------------------------------------------


class TestPortInUse:
    def test_free_port_returns_false(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()  # nothing listening now
        assert tunnel._port_in_use(port) is False

    def test_listening_port_returns_true(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        try:
            assert tunnel._port_in_use(port) is True
        finally:
            s.close()


class TestNextFreePort:
    def test_returns_start_when_free(self):
        with patch("ai_cli.tunnel._port_in_use", return_value=False):
            assert tunnel._next_free_port(9222) == 9222

    def test_skips_occupied_ports(self):
        def occupied(p, host="127.0.0.1"):
            return p in (9222, 9223)

        with patch("ai_cli.tunnel._port_in_use", side_effect=occupied):
            assert tunnel._next_free_port(9222) == 9224

    def test_returns_none_when_all_taken(self):
        with patch("ai_cli.tunnel._port_in_use", return_value=True):
            assert tunnel._next_free_port(9222, limit=5) is None


@patch.object(sys, "platform", "linux")
class TestCmdCdpStartPortConflict:
    def test_when_port_in_use_then_increments_and_launches_on_next_free(self, tmp_path, capsys):
        mock_proc = MagicMock()
        mock_proc.pid = 777

        def occupied(p, host="127.0.0.1"):
            return p == 9222  # requested port taken; 9223 free

        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel._find_chrome_binary", return_value="/usr/bin/chromium"),
            patch("ai_cli.tunnel._port_in_use", side_effect=occupied),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("urllib.request.urlopen"),
        ):
            _cmd_cdp_start(9222, True, {})

        out = capsys.readouterr().out
        assert "9222 is in use" in out
        assert "starting CDP on 9223" in out
        assert (tmp_path / "cdp-9223.pid").exists()
        assert (tmp_path / "cdp-9223.pid").read_text() == "777"
        assert not (tmp_path / "cdp-9222.pid").exists()

    def test_when_no_free_port_then_exits_nonzero(self, tmp_path):
        with (
            patch("ai_cli.tunnel.get_xdg_state_home", return_value=tmp_path),
            patch("ai_cli.tunnel._port_in_use", return_value=True),
        ):
            with pytest.raises(SystemExit) as exc:
                _cmd_cdp_start(9222, True, {})
        assert exc.value.code == 1
