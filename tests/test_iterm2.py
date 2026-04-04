import json
import os
from unittest.mock import patch

from conftest import make_iterm2_config

from ai_cli.main import (
    _assign_iterm2_color_slot,
    _emit_iterm2_profile_setup,
    _is_iterm2,
    _iterm2_palette,
    _iterm2_state_dir,
    _load_iterm2_config,
    _release_iterm2_color_slot,
    cli,
    get_engine_script,
)

import pytest


class TestEmitIterm2ProfileSetup:
    """Tests for _emit_iterm2_profile_setup."""

    def test_when_not_iterm2_then_writes_nothing(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "", "TERM_PROGRAM": ""}, clear=False):
            _emit_iterm2_profile_setup("sw-1", "c")
        assert capsys.readouterr().out == ""

    def test_when_lc_terminal_is_iterm2_and_claude_engine_then_emits_dynamic_profile_and_color(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.main._load_iterm2_config", return_value={}):
                with patch("ai_cli.icon_generator.generate_session_icon", return_value=None):
                    with patch("ai_cli.icon_generator.generate_dynamic_profile"):
                        _emit_iterm2_profile_setup("sw-3", "c")
        out = capsys.readouterr().out
        assert "SetProfile=ai-cli:sw-3" in out
        assert "SetColors=tab=" in out

    def test_when_lc_terminal_is_iterm2_and_gemini_engine_then_emits_dynamic_profile(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.main._load_iterm2_config", return_value={}):
                with patch("ai_cli.icon_generator.generate_session_icon", return_value=None):
                    with patch("ai_cli.icon_generator.generate_dynamic_profile"):
                        _emit_iterm2_profile_setup("ai-dojo-1", "g")
        out = capsys.readouterr().out
        assert "SetProfile=ai-cli:ai-dojo-1" in out
        assert "SetColors=tab=" in out

    def test_when_term_program_is_iterm_app_then_also_activates(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "", "TERM_PROGRAM": "iTerm.app"}, clear=False):
            with patch("ai_cli.main._load_iterm2_config", return_value={}):
                with patch("ai_cli.icon_generator.generate_session_icon", return_value=None):
                    with patch("ai_cli.icon_generator.generate_dynamic_profile"):
                        _emit_iterm2_profile_setup("sw-1", "c")
        assert "SetProfile=ai-cli:sw-1" in capsys.readouterr().out

    def test_when_session_arg_provided_then_profile_uses_ai_name_not_session(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.main._load_iterm2_config", return_value={}):
                with patch("ai_cli.icon_generator.generate_session_icon", return_value=None):
                    with patch("ai_cli.icon_generator.generate_dynamic_profile"):
                        _emit_iterm2_profile_setup("sw-1", "c", session="c-sw-1")
        out = capsys.readouterr().out
        assert "SetProfile=ai-cli:sw-1" in out
        assert "SetColors=tab=" in out

    def test_uses_osc1_not_osc0_for_title(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.main._load_iterm2_config", return_value={}):
                with patch("ai_cli.icon_generator.generate_session_icon", return_value=None):
                    with patch("ai_cli.icon_generator.generate_dynamic_profile"):
                        _emit_iterm2_profile_setup("sw-1", "c", session="c-sw-1")
        out = capsys.readouterr().out
        assert "\033]1;" in out
        assert "\033]0;" not in out

    def test_when_slot_provided_then_uses_slot_color(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.main._load_iterm2_config", return_value={}):
                with patch("ai_cli.icon_generator.generate_session_icon", return_value=None):
                    with patch("ai_cli.icon_generator.generate_dynamic_profile"):
                        _emit_iterm2_profile_setup("sw-5", "c", session="c-sw-5", slot="#1abc9c")
        out = capsys.readouterr().out
        assert "SetProfile=ai-cli:sw-5" in out
        assert "SetColors=tab=1abc9c" in out

    def test_icon_generation_failure_does_not_block_launch(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.main._load_iterm2_config", return_value={}):
                with patch("ai_cli.icon_generator.generate_session_icon", side_effect=RuntimeError("fail")):
                    _emit_iterm2_profile_setup("sw-1", "c")
        out = capsys.readouterr().out
        assert "SetProfile=ai-cli:sw-1" in out


class TestEmitIterm2ProfileSetupGeminiWithIterm2Env:
    """Tests for gemini engine + ITERM_SESSION_ID in _emit_iterm2_profile_setup."""

    def test_when_gemini_engine_and_iterm_session_id_set_then_emits_dynamic_profile(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2", "ITERM_SESSION_ID": "w0t0p0:abc"}, clear=False):
            with patch("ai_cli.main._load_iterm2_config", return_value={}):
                with patch("ai_cli.icon_generator.generate_session_icon", return_value=None):
                    with patch("ai_cli.icon_generator.generate_dynamic_profile"):
                        _emit_iterm2_profile_setup("research-1", "g")
        out = capsys.readouterr().out
        assert "SetProfile=ai-cli:research-1" in out
        assert "\033]1;" in out
        assert "research-1" in out


class TestIterm2StateDir:
    """Tests for _iterm2_state_dir."""

    def test_returns_xdg_state_iterm2_subdir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        result = _iterm2_state_dir()
        assert result == tmp_path / "ai-cli-utils" / "iterm2"
        assert result.is_dir()

    def test_creates_directory_if_absent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        target = tmp_path / "ai-cli-utils" / "iterm2"
        assert not target.exists()
        _iterm2_state_dir()
        assert target.is_dir()


class TestLoadIterm2Config:
    def test_when_config_missing_then_writes_defaults_and_returns_dict(self, tmp_path):
        with patch("ai_cli.main.get_xdg_config_home", return_value=tmp_path):
            result = _load_iterm2_config()
        assert isinstance(result, dict)
        assert "iterm2" in result
        assert (tmp_path / "iterm2.toml").exists()

    def test_when_config_exists_then_reads_it(self, tmp_path):
        config_path = tmp_path / "iterm2.toml"
        config_path.write_text("[iterm2]\nenabled = false\n")
        with patch("ai_cli.main.get_xdg_config_home", return_value=tmp_path):
            result = _load_iterm2_config()
        assert result["iterm2"]["enabled"] is False

    def test_when_config_corrupted_then_falls_back_to_defaults(self, tmp_path):
        config_path = tmp_path / "iterm2.toml"
        config_path.write_bytes(b"\xff\xfe invalid toml")
        with patch("ai_cli.main.get_xdg_config_home", return_value=tmp_path):
            result = _load_iterm2_config()
        assert isinstance(result, dict)


class TestIterm2Palette:
    def test_returns_list_of_name_hex_tuples(self):
        cfg = {"iterm2": {"palette": {"red": "#e74c3c", "blue": "#1e88e5"}}}
        result = _iterm2_palette(cfg)
        assert result == [("red", "e74c3c"), ("blue", "1e88e5")]

    def test_strips_hash_prefix_from_hex(self):
        cfg = {"iterm2": {"palette": {"teal": "#1abc9c"}}}
        result = _iterm2_palette(cfg)
        assert result[0] == ("teal", "1abc9c")

    def test_when_palette_missing_then_returns_empty_list(self):
        assert _iterm2_palette({}) == []


class TestAssignIterm2ColorSlot:
    def test_when_not_iterm2_then_returns_none(self, tmp_path):
        with patch.dict(os.environ, {"LC_TERMINAL": "", "TERM_PROGRAM": ""}, clear=False):
            result = _assign_iterm2_color_slot("sw-1", "c")
        assert result is None

    def test_when_iterm2_then_returns_hex_string(self, tmp_path):
        cfg = make_iterm2_config()
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
                with patch("ai_cli.main._load_iterm2_config", return_value=cfg):
                    result = _assign_iterm2_color_slot("sw-1", "c")
        assert result is not None
        assert isinstance(result, str)
        assert len(result.lstrip("#")) == 6

    def test_when_collision_avoidance_assigns_unique_slots(self, tmp_path):
        cfg = make_iterm2_config()
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
                with patch("ai_cli.main._load_iterm2_config", return_value=cfg):
                    slot1 = _assign_iterm2_color_slot("sw-1", "c")
                    slot2 = _assign_iterm2_color_slot("sw-2", "c")
                    slot3 = _assign_iterm2_color_slot("sw-3", "c")
        assert len({slot1, slot2, slot3}) == 3

    def test_when_all_slots_occupied_wraps_to_first(self, tmp_path):
        cfg = make_iterm2_config(palette={"red": "#e74c3c", "blue": "#1e88e5"})
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
                with patch("ai_cli.main._load_iterm2_config", return_value=cfg):
                    _assign_iterm2_color_slot("sw-1", "c")
                    _assign_iterm2_color_slot("sw-2", "c")
                    slot3 = _assign_iterm2_color_slot("sw-3", "c")
        assert slot3 == "e74c3c"

    def test_stale_lease_pruned_on_assignment(self, tmp_path):
        cfg = make_iterm2_config()
        lease_file = tmp_path / "color-leases.json"
        lease_file.write_text(json.dumps({"leases": {"sw-dead": {"slot": 0, "pid": 999999999, "ts": "0"}}}))
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
                with patch("ai_cli.main._load_iterm2_config", return_value=cfg):
                    slot = _assign_iterm2_color_slot("sw-1", "c")
        leases = json.loads(lease_file.read_text())["leases"]
        assert "sw-dead" not in leases
        assert "sw-1" in leases
        assert slot is not None

    def test_when_collision_avoidance_disabled_uses_modulo(self, tmp_path):
        cfg = make_iterm2_config(collision_avoidance=False)
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
                with patch("ai_cli.main._load_iterm2_config", return_value=cfg):
                    slot = _assign_iterm2_color_slot("sw-2", "c")
        assert slot is not None
        assert slot.lstrip("#") == "1e88e5"

    def test_project_colors_pins_preferred_slot(self, tmp_path):
        cfg = make_iterm2_config(project_colors={"myproject": "green"})
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
                with patch("ai_cli.main._load_iterm2_config", return_value=cfg):
                    slot = _assign_iterm2_color_slot("sw-1", "c", project_name="myproject")
        assert slot is not None
        assert slot.lstrip("#") == "2ecc71"

    def test_project_colors_falls_back_when_preferred_occupied(self, tmp_path):
        cfg = make_iterm2_config(project_colors={"myproject": "red"})
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
                with patch("ai_cli.main._load_iterm2_config", return_value=cfg):
                    _assign_iterm2_color_slot("other-session", "c")
                    slot = _assign_iterm2_color_slot("sw-1", "c", project_name="myproject")
        assert slot is not None
        assert slot.lstrip("#") != "e74c3c"


class TestReleaseIterm2ColorSlot:
    def test_when_lease_exists_then_removes_it(self, tmp_path):
        lease_file = tmp_path / "color-leases.json"
        lease_file.write_text(json.dumps({"leases": {"sw-5": {"slot": 2, "pid": 123, "ts": "0"}}}))
        with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
            _release_iterm2_color_slot("sw-5")
        leases = json.loads(lease_file.read_text())["leases"]
        assert "sw-5" not in leases

    def test_when_lease_missing_then_no_error(self, tmp_path):
        lease_file = tmp_path / "color-leases.json"
        lease_file.write_text(json.dumps({"leases": {}}))
        with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
            _release_iterm2_color_slot("sw-99")

    def test_when_file_missing_then_no_error(self, tmp_path):
        with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
            _release_iterm2_color_slot("sw-1")


class TestReleaseColorSlotCommand:
    def test_release_color_slot_internal_command(self, tmp_path):
        lease_file = tmp_path / "color-leases.json"
        lease_file.write_text(json.dumps({"leases": {"sw-3": {"slot": 0, "pid": 1, "ts": "0"}}}))
        with patch("sys.argv", ["ai", "internal", "release-color-slot", "sw-3"]):
            with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
                with pytest.raises(SystemExit) as exc:
                    cli()
        assert exc.value.code == 0
        leases = json.loads(lease_file.read_text())["leases"]
        assert "sw-3" not in leases

    def test_release_color_slot_missing_arg_exits_1(self):
        with patch("sys.argv", ["ai", "internal", "release-color-slot"]):
            with pytest.raises(SystemExit) as exc:
                cli()
        assert exc.value.code == 1


class TestIsIterm2:
    def test_when_lc_terminal_iterm2_then_true(self):
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2", "TERM_PROGRAM": ""}, clear=False):
            assert _is_iterm2() is True

    def test_when_term_program_iterm_app_then_true(self):
        with patch.dict(os.environ, {"LC_TERMINAL": "", "TERM_PROGRAM": "iTerm.app"}, clear=False):
            assert _is_iterm2() is True

    def test_when_neither_set_then_false(self):
        with patch.dict(os.environ, {"LC_TERMINAL": "", "TERM_PROGRAM": ""}, clear=False):
            assert _is_iterm2() is False

    def test_when_other_terminal_then_false(self):
        with patch.dict(os.environ, {"LC_TERMINAL": "xterm-256color", "TERM_PROGRAM": "Terminal"}, clear=False):
            assert _is_iterm2() is False


class TestGetEngineScriptIterm2Slot:
    def test_when_slot_provided_then_color_embedded_in_script(self):
        cfg = {"iterm2": {"tab_title": {"show_type_symbol": True, "show_status_symbol": True}}}
        script = get_engine_script(
            "c",
            "sw-1",
            "c-sw-1",
            "c-sw-",
            "sw",
            iterm2_slot="#ff5722",
            iterm2_cfg=cfg,
        )
        assert '_iterm2_color="ff5722"' in script

    def test_slot_with_hash_prefix_stripped_in_script(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw", iterm2_slot="#1e88e5")
        assert '_iterm2_color="1e88e5"' in script

    def test_when_show_type_symbol_false_then_flag_is_0_in_script(self):
        cfg = {"iterm2": {"tab_title": {"show_type_symbol": False, "show_status_symbol": True}}}
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw", iterm2_cfg=cfg)
        assert '_iterm2_show_type_sym="0"' in script

    def test_when_show_status_symbol_false_then_flag_is_0_in_script(self):
        cfg = {"iterm2": {"tab_title": {"show_type_symbol": True, "show_status_symbol": False}}}
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw", iterm2_cfg=cfg)
        assert '_iterm2_show_status_sym="0"' in script

    def test_when_no_slot_then_fallback_color_embedded(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        assert '_iterm2_color="e74c3c"' in script

    def test_no_static_profile_vars_in_script(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        assert "_iterm2_claude_profile" not in script
        assert "_iterm2_gemini_profile" not in script

    def test_script_calls_fleet_setup_with_session_name(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        assert '_iterm2_fleet_setup "$tmux_session"' in script

    def test_script_release_color_slot_in_exit_trap(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        assert 'ai internal release-color-slot "$ai_name"' in script

    def test_script_cleanup_session_files_in_exit_trap(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        assert 'ai internal cleanup-session-files "$ai_name"' in script

    def test_fleet_setup_uses_osc1_not_osc0(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        assert "\\033]1;" in script
        assert "\\033]0;" not in script
