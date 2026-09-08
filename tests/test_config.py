"""Tests for platform-aware XDG path helpers, _pid_alive(), and machine profile detection."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_cli.config import (
    ProjectPrefixError,
    _pid_alive,
    detect_machine_profile,
    ensure_machine_profile_registered,
    get_xdg_cache_home,
    get_xdg_config_home,
    get_xdg_state_home,
    load_config,
    register_project,
    resolve_project_prefix,
)


def test_given_umask_022_when_config_created_then_owner_only(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    previous = os.umask(0o022)
    try:
        load_config()
    finally:
        os.umask(previous)
    config_dir = tmp_path / "xdg" / "ai-cli-utils"
    config_path = config_dir / "config.toml"
    assert config_dir.stat().st_mode & 0o777 == 0o700
    assert config_path.stat().st_mode & 0o777 == 0o600


def test_given_config_symlink_when_loading_then_refuses_target(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    config_dir = tmp_path / "xdg" / "ai-cli-utils"
    config_dir.mkdir(parents=True)
    target = tmp_path / "target.toml"
    target.write_text("[notifications]\nos_fallback = false\n")
    (config_dir / "config.toml").symlink_to(target)
    assert load_config() == {}
    assert target.read_text() == "[notifications]\nos_fallback = false\n"


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


# ---------------------------------------------------------------------------
# detect_machine_profile()
# ---------------------------------------------------------------------------


class TestDetectMachineProfile:
    def test_when_ai_host_set_then_uses_it_as_host_id(self):
        with patch.dict(os.environ, {"AI_HOST": "acn-windows"}, clear=False):
            result = detect_machine_profile()
        assert result["host_id"] == "acn-windows"

    def test_when_ai_host_unset_then_falls_back_to_hostname(self):
        env = {k: v for k, v in os.environ.items() if k != "AI_HOST"}
        with patch.dict(os.environ, env, clear=True), patch("socket.gethostname", return_value="my-box"):
            result = detect_machine_profile()
        assert result["host_id"] == "my-box"

    @pytest.mark.parametrize(
        ("platform", "expected"),
        [("win32", "windows"), ("darwin", "macos"), ("linux", "linux")],
    )
    def test_os_type_mapped_from_platform(self, platform, expected):
        with patch("sys.platform", platform):
            result = detect_machine_profile()
        assert result["os_type"] == expected

    def test_unknown_platform_uses_platform_string(self):
        with patch("sys.platform", "freebsd"):
            result = detect_machine_profile()
        assert result["os_type"] == "freebsd"

    def test_returns_dict_with_required_keys(self):
        result = detect_machine_profile()
        assert "host_id" in result
        assert "os_type" in result


# ---------------------------------------------------------------------------
# ensure_machine_profile_registered()
# ---------------------------------------------------------------------------

_MINIMAL_CONFIG = """\
[behavior]
notify_on_exit = true

[machine]
## Auto-detected machine identity
# host_id = ""
# os_type = ""
"""

_ALREADY_CONFIGURED = """\
[machine]
host_id = "my-server"
os_type = "linux"
"""


class TestEnsureMachineProfileRegistered:
    def test_when_both_missing_then_writes_both_keys(self, tmp_path):
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text(_MINIMAL_CONFIG)
        with (
            patch.dict(os.environ, {"AI_HOST": "acn-windows"}, clear=False),
            patch("sys.platform", "win32"),
        ):
            result = ensure_machine_profile_registered(cfg_file, {})
        assert result is True
        text = cfg_file.read_text()
        assert 'host_id = "acn-windows"' in text
        assert 'os_type = "windows"' in text

    def test_when_both_already_set_then_returns_false_and_no_write(self, tmp_path):
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text(_ALREADY_CONFIGURED)
        original_mtime = cfg_file.stat().st_mtime
        result = ensure_machine_profile_registered(cfg_file, {"machine": {"host_id": "my-server", "os_type": "linux"}})
        assert result is False
        assert cfg_file.stat().st_mtime == original_mtime

    def test_when_only_host_id_missing_then_writes_only_host_id(self, tmp_path):
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text('[machine]\nos_type = "linux"\n')
        with patch.dict(os.environ, {"AI_HOST": "my-box"}, clear=False):
            result = ensure_machine_profile_registered(cfg_file, {"machine": {"os_type": "linux"}})
        assert result is True
        text = cfg_file.read_text()
        assert 'host_id = "my-box"' in text

    def test_when_machine_section_missing_then_appends_new_section(self, tmp_path):
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text("[behavior]\nnotify_on_exit = true\n")
        with (
            patch.dict(os.environ, {"AI_HOST": "box1"}, clear=False),
            patch("sys.platform", "linux"),
        ):
            result = ensure_machine_profile_registered(cfg_file, {})
        assert result is True
        text = cfg_file.read_text()
        assert "[machine]" in text
        assert 'host_id = "box1"' in text
        assert 'os_type = "linux"' in text

    def test_written_config_is_valid_toml(self, tmp_path):
        import tomllib

        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text(_MINIMAL_CONFIG)
        with (
            patch.dict(os.environ, {"AI_HOST": "testhost"}, clear=False),
            patch("sys.platform", "linux"),
        ):
            ensure_machine_profile_registered(cfg_file, {})
        with cfg_file.open("rb") as f:
            parsed = tomllib.load(f)
        assert parsed["machine"]["host_id"] == "testhost"
        assert parsed["machine"]["os_type"] == "linux"

    def test_prints_message_on_registration(self, tmp_path, capsys):
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text(_MINIMAL_CONFIG)
        with (
            patch.dict(os.environ, {"AI_HOST": "acn-windows"}, clear=False),
            patch("sys.platform", "win32"),
        ):
            ensure_machine_profile_registered(cfg_file, {})
        captured = capsys.readouterr()
        assert "acn-windows" in captured.err
        assert "windows" in captured.err


class TestProjectPrefixRegistry:
    def test_given_registered_repo_when_resolving_then_returns_raw_registered_prefix(self, tmp_path):
        repo = tmp_path / "myproject"
        (repo / ".git").mkdir(parents=True)
        config_dir = tmp_path / "config"
        with patch("ai_cli.config.get_xdg_config_home", return_value=config_dir):
            register_project(repo, "PROJECT", "tool")
            assert resolve_project_prefix(repo / ".worktrees" / "session-1") == "PROJECT"

    def test_given_registered_beads_repo_when_registering_then_projects_prefix_into_beads_config(self, tmp_path):
        repo = tmp_path / "myproject"
        (repo / ".git").mkdir(parents=True)
        beads_config = repo / ".beads" / "config.yaml"
        beads_config.parent.mkdir()
        beads_config.write_text('# issue-prefix: ""\n')
        with patch("ai_cli.config.get_xdg_config_home", return_value=tmp_path / "config"):
            register_project(repo, "PROJECT")
        assert beads_config.read_text().splitlines()[0] == 'issue-prefix: "PROJECT"'

    def test_given_existing_registration_when_registering_again_then_updates_one_config_entry(self, tmp_path):
        import tomllib

        repo = tmp_path / "myproject"
        repo.mkdir()
        config_dir = tmp_path / "config"
        with patch("ai_cli.config.get_xdg_config_home", return_value=config_dir):
            register_project(repo, "OLD")
            register_project(repo, "NEW")
            with (config_dir / "config.toml").open("rb") as config_file:
                registry = tomllib.load(config_file)["project_registry"]
            assert registry[str(repo.resolve())]["prefix"] == "NEW"
            assert len(registry) == 1

    def test_given_self_describing_repo_when_resolving_then_auto_registers_metadata_prefix(self, tmp_path):
        repo = tmp_path / "myproject"
        repo.mkdir()
        (repo / "pyproject.toml").write_text('[tool.ai-cli]\ntask_prefix = "PROJECT"\nproject_type = "app"\n')
        config_dir = tmp_path / "config"
        with patch("ai_cli.config.get_xdg_config_home", return_value=config_dir):
            assert resolve_project_prefix(repo) == "PROJECT"
            assert resolve_project_prefix(repo) == "PROJECT"

    def test_given_unregistered_repo_when_resolving_then_raises_registration_remedy(self, tmp_path):
        repo = tmp_path / "myproject"
        repo.mkdir()
        with patch("ai_cli.config.get_xdg_config_home", return_value=tmp_path / "config"):
            with pytest.raises(ProjectPrefixError, match=r"ai register -p .* -x PREFIX"):
                resolve_project_prefix(repo)
