from unittest.mock import patch

from ai_cli.main import get_xdg_cache_home, get_xdg_state_home, load_config


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
        with patch("ai_cli.main.get_xdg_config_home", return_value=config_dir):
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
        with patch("ai_cli.main.get_xdg_config_home", return_value=config_dir):
            result = load_config()
        assert result == {}
