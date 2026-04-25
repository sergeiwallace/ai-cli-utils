"""Tests for platform-aware XDG path helpers and _pid_alive() in config.py."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_cli.config import _pid_alive, get_xdg_cache_home, get_xdg_config_home, get_xdg_state_home


# ---------------------------------------------------------------------------
# Windows path helpers (sys.platform == "win32")
# ---------------------------------------------------------------------------


class TestGetXdgConfigHomeWindows:
    def test_when_win32_and_appdata_set_then_returns_appdata_subdir(self, tmp_path):
        appdata = str(tmp_path / "AppData" / "Roaming")
        with patch("sys.platform", "win32"), patch.dict(os.environ, {"APPDATA": appdata}, clear=False):
            result = get_xdg_config_home()
        assert result == Path(appdata) / "ai-cli-utils"

    def test_when_win32_and_appdata_missing_then_falls_back_to_home(self, tmp_path):
        env = {k: v for k, v in os.environ.items() if k != "APPDATA"}
        with (
            patch("sys.platform", "win32"),
            patch.dict(os.environ, env, clear=True),
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            result = get_xdg_config_home()
        assert result == tmp_path / "AppData" / "Roaming" / "ai-cli-utils"

    def test_when_win32_then_no_xdg_env_lookup(self, tmp_path):
        appdata = str(tmp_path / "Roaming")
        custom_xdg = str(tmp_path / "custom_xdg")
        with (
            patch("sys.platform", "win32"),
            patch.dict(os.environ, {"APPDATA": appdata, "XDG_CONFIG_HOME": custom_xdg}, clear=False),
        ):
            result = get_xdg_config_home()
        # On win32, XDG_CONFIG_HOME must be ignored
        assert "custom_xdg" not in str(result)
        assert result == Path(appdata) / "ai-cli-utils"


class TestGetXdgStateHomeWindows:
    def test_when_win32_and_localappdata_set_then_returns_localappdata_subdir(self, tmp_path):
        local = str(tmp_path / "AppData" / "Local")
        with patch("sys.platform", "win32"), patch.dict(os.environ, {"LOCALAPPDATA": local}, clear=False):
            result = get_xdg_state_home()
        assert result == Path(local) / "ai-cli-utils"

    def test_when_win32_and_localappdata_missing_then_falls_back_to_home(self, tmp_path):
        env = {k: v for k, v in os.environ.items() if k != "LOCALAPPDATA"}
        with (
            patch("sys.platform", "win32"),
            patch.dict(os.environ, env, clear=True),
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            result = get_xdg_state_home()
        assert result == tmp_path / "AppData" / "Local" / "ai-cli-utils"

    def test_when_win32_then_no_xdg_env_lookup(self, tmp_path):
        local = str(tmp_path / "Local")
        custom_xdg = str(tmp_path / "custom_state")
        with (
            patch("sys.platform", "win32"),
            patch.dict(os.environ, {"LOCALAPPDATA": local, "XDG_STATE_HOME": custom_xdg}, clear=False),
        ):
            result = get_xdg_state_home()
        assert "custom_state" not in str(result)
        assert result == Path(local) / "ai-cli-utils"


class TestGetXdgCacheHomeWindows:
    def test_when_win32_and_localappdata_set_then_returns_cache_subdir(self, tmp_path):
        local = str(tmp_path / "AppData" / "Local")
        with patch("sys.platform", "win32"), patch.dict(os.environ, {"LOCALAPPDATA": local}, clear=False):
            result = get_xdg_cache_home()
        assert result == Path(local) / "ai-cli-utils" / "cache"

    def test_when_win32_and_localappdata_missing_then_falls_back_to_home(self, tmp_path):
        env = {k: v for k, v in os.environ.items() if k != "LOCALAPPDATA"}
        with (
            patch("sys.platform", "win32"),
            patch.dict(os.environ, env, clear=True),
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            result = get_xdg_cache_home()
        assert result == tmp_path / "AppData" / "Local" / "ai-cli-utils" / "cache"


# ---------------------------------------------------------------------------
# Non-Windows path helpers (POSIX / XDG)
# ---------------------------------------------------------------------------


class TestGetXdgConfigHomePosix:
    def test_when_xdg_config_home_set_then_uses_it(self, tmp_path):
        custom = str(tmp_path / "xdg-config")
        with patch("sys.platform", "linux"), patch.dict(os.environ, {"XDG_CONFIG_HOME": custom}, clear=False):
            result = get_xdg_config_home()
        # migration may create ai-cli-utils subdir
        assert result == Path(custom) / "ai-cli-utils"

    def test_when_xdg_config_home_unset_then_uses_dotconfig(self, tmp_path):
        env = {k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME"}
        with (
            patch("sys.platform", "linux"),
            patch.dict(os.environ, env, clear=True),
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            result = get_xdg_config_home()
        assert result == tmp_path / ".config" / "ai-cli-utils"

    def test_when_legacy_ai_cli_dir_exists_then_migrated(self, tmp_path):
        config_base = tmp_path / ".config"
        old_dir = config_base / "ai-cli"
        old_dir.mkdir(parents=True)
        env = {k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME"}
        with (
            patch("sys.platform", "linux"),
            patch.dict(os.environ, env, clear=True),
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            result = get_xdg_config_home()
        assert result == config_base / "ai-cli-utils"
        assert not old_dir.exists()
        assert (config_base / "ai-cli-utils").exists()


class TestGetXdgStateHomePosix:
    def test_when_xdg_state_home_set_then_uses_it(self, tmp_path):
        custom = str(tmp_path / "xdg-state")
        with patch("sys.platform", "linux"), patch.dict(os.environ, {"XDG_STATE_HOME": custom}, clear=False):
            result = get_xdg_state_home()
        assert result == Path(custom) / "ai-cli-utils"

    def test_when_xdg_state_home_unset_then_uses_local_state(self, tmp_path):
        env = {k: v for k, v in os.environ.items() if k != "XDG_STATE_HOME"}
        with (
            patch("sys.platform", "linux"),
            patch.dict(os.environ, env, clear=True),
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            result = get_xdg_state_home()
        assert result == tmp_path / ".local" / "state" / "ai-cli-utils"


class TestGetXdgCacheHomePosix:
    def test_when_xdg_cache_home_set_then_uses_it(self, tmp_path):
        custom = str(tmp_path / "xdg-cache")
        with patch("sys.platform", "linux"), patch.dict(os.environ, {"XDG_CACHE_HOME": custom}, clear=False):
            result = get_xdg_cache_home()
        assert result == Path(custom) / "ai-cli-utils"

    def test_when_xdg_cache_home_unset_then_uses_dotcache(self, tmp_path):
        env = {k: v for k, v in os.environ.items() if k != "XDG_CACHE_HOME"}
        with (
            patch("sys.platform", "linux"),
            patch.dict(os.environ, env, clear=True),
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            result = get_xdg_cache_home()
        assert result == tmp_path / ".cache" / "ai-cli-utils"


# ---------------------------------------------------------------------------
# Path type contract — all helpers must return Path objects
# ---------------------------------------------------------------------------


class TestXdgHelpersReturnType:
    @pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
    def test_config_home_returns_path(self, tmp_path, platform):
        env = {}
        if platform == "win32":
            env["APPDATA"] = str(tmp_path)
        with (
            patch("sys.platform", platform),
            patch.dict(os.environ, env, clear=False),
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            assert isinstance(get_xdg_config_home(), Path)

    @pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
    def test_state_home_returns_path(self, tmp_path, platform):
        env = {}
        if platform == "win32":
            env["LOCALAPPDATA"] = str(tmp_path)
        with (
            patch("sys.platform", platform),
            patch.dict(os.environ, env, clear=False),
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            assert isinstance(get_xdg_state_home(), Path)

    @pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
    def test_cache_home_returns_path(self, tmp_path, platform):
        env = {}
        if platform == "win32":
            env["LOCALAPPDATA"] = str(tmp_path)
        with (
            patch("sys.platform", platform),
            patch.dict(os.environ, env, clear=False),
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            assert isinstance(get_xdg_cache_home(), Path)


# ---------------------------------------------------------------------------
# _pid_alive() — cross-platform process existence helper
# ---------------------------------------------------------------------------


class TestPidAlive:
    def test_when_pid_exists_then_returns_true(self):
        # os.getpid() is always a live process
        assert _pid_alive(os.getpid()) is True

    def test_when_pid_does_not_exist_then_returns_false(self):
        # PID 2**30 is astronomically unlikely to be a real process
        assert _pid_alive(2**30) is False

    def test_delegates_to_psutil_pid_exists(self):
        with patch("psutil.pid_exists", return_value=True) as mock_fn:
            result = _pid_alive(1234)
        mock_fn.assert_called_once_with(1234)
        assert result is True

    def test_when_psutil_returns_false_then_returns_false(self):
        with patch("psutil.pid_exists", return_value=False):
            assert _pid_alive(1234) is False

    def test_returns_bool(self):
        # psutil.pid_exists returns bool; _pid_alive must propagate it
        with patch("psutil.pid_exists", return_value=True):
            result = _pid_alive(1)
        assert isinstance(result, bool)
