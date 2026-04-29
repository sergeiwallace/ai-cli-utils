import json
import os
from unittest.mock import patch

from conftest import make_iterm2_config

from ai_cli.main import (
    _assign_iterm2_color_slot,
    _emit_iterm2_profile_setup,
    _evict_iterm2_guid,
    _get_current_iterm_session_id,
    _is_iterm2,
    _iterm2_palette,
    _iterm2_state_dir,
    _load_iterm2_config,
    _release_iterm2_color_slot,
    _resolve_iterm2_config,
    _set_iterm2_name_applescript,
    cli,
    get_engine_script,
)

import pytest


class TestResolveIterm2Config:
    """Tests for _resolve_iterm2_config."""

    def test_when_no_overrides_then_returns_empty(self):
        cfg = make_iterm2_config()
        result = _resolve_iterm2_config(cfg, "c-sw-1")
        assert result == {}

    def test_defaults_returned_when_no_project_or_session(self):
        cfg = make_iterm2_config(defaults={"tab_color": "blue"})
        result = _resolve_iterm2_config(cfg, "c-sw-1")
        assert result["tab_color"] == "blue"

    def test_project_overrides_defaults(self):
        cfg = make_iterm2_config(
            defaults={"tab_color": "blue"},
            projects={"myproject": {"tab_color": "green"}},
        )
        result = _resolve_iterm2_config(cfg, "c-sw-1", project_name="myproject")
        assert result["tab_color"] == "green"

    def test_session_overrides_project(self):
        cfg = make_iterm2_config(
            projects={"myproject": {"tab_color": "green"}},
            sessions={"c-sw-1": {"tab_color": "red"}},
        )
        result = _resolve_iterm2_config(cfg, "c-sw-1", project_name="myproject")
        assert result["tab_color"] == "red"

    def test_session_icon_color_returned(self):
        cfg = make_iterm2_config(sessions={"c-sw-1": {"tab_color": "orange", "icon_color": "#4a7535"}})
        result = _resolve_iterm2_config(cfg, "c-sw-1")
        assert result["icon_color"] == "#4a7535"

    def test_project_name_empty_skips_project_lookup(self):
        cfg = make_iterm2_config(projects={"myproject": {"tab_color": "teal"}})
        result = _resolve_iterm2_config(cfg, "c-sw-1", project_name="")
        assert "tab_color" not in result

    def test_unknown_session_and_project_returns_defaults_only(self):
        cfg = make_iterm2_config(
            defaults={"tab_color": "purple"},
            projects={"other": {"tab_color": "teal"}},
            sessions={"other-session": {"tab_color": "red"}},
        )
        result = _resolve_iterm2_config(cfg, "c-sw-1", project_name="myproject")
        assert result["tab_color"] == "purple"


class TestEmitIterm2ProfileSetup:
    """Tests for _emit_iterm2_profile_setup."""

    def test_when_not_iterm2_then_writes_nothing(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "", "TERM_PROGRAM": ""}, clear=False):
            _emit_iterm2_profile_setup("sw-1", "c")
        assert capsys.readouterr().out == ""

    def test_when_lc_terminal_is_iterm2_and_claude_engine_then_emits_dynamic_profile_and_color(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.iterm2._load_iterm2_config", return_value={}):
                with patch("ai_cli.icon_generator.generate_session_icon", return_value=None):
                    with patch("ai_cli.icon_generator.generate_dynamic_profile"):
                        _emit_iterm2_profile_setup("sw-3", "c")
        out = capsys.readouterr().out
        assert "SetProfile=ai-cli:sw-3" in out
        assert "SetColors=tab=" in out

    def test_when_lc_terminal_is_iterm2_and_gemini_engine_then_emits_dynamic_profile(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.iterm2._load_iterm2_config", return_value={}):
                with patch("ai_cli.icon_generator.generate_session_icon", return_value=None):
                    with patch("ai_cli.icon_generator.generate_dynamic_profile"):
                        _emit_iterm2_profile_setup("ai-dojo-1", "g")
        out = capsys.readouterr().out
        assert "SetProfile=ai-cli:ai-dojo-1" in out
        assert "SetColors=tab=" in out

    def test_when_term_program_is_iterm_app_then_also_activates(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "", "TERM_PROGRAM": "iTerm.app"}, clear=False):
            with patch("ai_cli.iterm2._load_iterm2_config", return_value={}):
                with patch("ai_cli.icon_generator.generate_session_icon", return_value=None):
                    with patch("ai_cli.icon_generator.generate_dynamic_profile"):
                        _emit_iterm2_profile_setup("sw-1", "c")
        assert "SetProfile=ai-cli:sw-1" in capsys.readouterr().out

    def test_when_session_arg_provided_then_profile_uses_ai_name_not_session(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.iterm2._load_iterm2_config", return_value={}):
                with patch("ai_cli.icon_generator.generate_session_icon", return_value=None):
                    with patch("ai_cli.icon_generator.generate_dynamic_profile"):
                        _emit_iterm2_profile_setup("sw-1", "c", session="c-sw-1")
        out = capsys.readouterr().out
        assert "SetProfile=ai-cli:sw-1" in out
        assert "SetColors=tab=" in out

    def test_uses_osc1_not_osc0_for_title(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.iterm2._load_iterm2_config", return_value={}):
                with patch("ai_cli.icon_generator.generate_session_icon", return_value=None):
                    with patch("ai_cli.icon_generator.generate_dynamic_profile"):
                        _emit_iterm2_profile_setup("sw-1", "c", session="c-sw-1")
        out = capsys.readouterr().out
        assert "\033]1;" in out
        assert "\033]0;" not in out

    def test_when_slot_provided_then_uses_slot_color(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.iterm2._load_iterm2_config", return_value={}):
                with patch("ai_cli.icon_generator.generate_session_icon", return_value=None):
                    with patch("ai_cli.icon_generator.generate_dynamic_profile"):
                        _emit_iterm2_profile_setup("sw-5", "c", session="c-sw-5", slot="#1abc9c")
        out = capsys.readouterr().out
        assert "SetProfile=ai-cli:sw-5" in out
        assert "SetColors=tab=1abc9c" in out

    def test_icon_generation_failure_does_not_block_launch(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.iterm2._load_iterm2_config", return_value={}):
                with patch("ai_cli.icon_generator.generate_session_icon", side_effect=RuntimeError("fail")):
                    _emit_iterm2_profile_setup("sw-1", "c")
        out = capsys.readouterr().out
        assert "SetProfile=ai-cli:sw-1" in out


class TestEmitIterm2ProfileSetupGeminiWithIterm2Env:
    """Tests for gemini engine + ITERM_SESSION_ID in _emit_iterm2_profile_setup."""

    def test_when_gemini_engine_and_iterm_session_id_set_then_emits_dynamic_profile(self, capsys):
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2", "ITERM_SESSION_ID": "w0t0p0:abc"}, clear=False):
            with patch("ai_cli.iterm2._load_iterm2_config", return_value={}):
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
        with patch("ai_cli.iterm2.get_xdg_config_home", return_value=tmp_path):
            result = _load_iterm2_config()
        assert isinstance(result, dict)
        assert "iterm2" in result
        assert (tmp_path / "iterm2.toml").exists()

    def test_when_config_exists_then_reads_it(self, tmp_path):
        config_path = tmp_path / "iterm2.toml"
        config_path.write_text("[iterm2]\nenabled = false\n")
        with patch("ai_cli.iterm2.get_xdg_config_home", return_value=tmp_path):
            result = _load_iterm2_config()
        assert result["iterm2"]["enabled"] is False

    def test_when_config_corrupted_then_falls_back_to_defaults(self, tmp_path):
        config_path = tmp_path / "iterm2.toml"
        config_path.write_bytes(b"\xff\xfe invalid toml")
        with patch("ai_cli.iterm2.get_xdg_config_home", return_value=tmp_path):
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
            with patch("ai_cli.iterm2._iterm2_state_dir", return_value=tmp_path):
                with patch("ai_cli.iterm2._load_iterm2_config", return_value=cfg):
                    result = _assign_iterm2_color_slot("sw-1", "c")
        assert result is not None
        assert isinstance(result, str)
        assert len(result.lstrip("#")) == 6

    def test_when_collision_avoidance_assigns_unique_slots(self, tmp_path):
        cfg = make_iterm2_config()
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.iterm2._iterm2_state_dir", return_value=tmp_path):
                with patch("ai_cli.iterm2._load_iterm2_config", return_value=cfg):
                    slot1 = _assign_iterm2_color_slot("sw-1", "c")
                    slot2 = _assign_iterm2_color_slot("sw-2", "c")
                    slot3 = _assign_iterm2_color_slot("sw-3", "c")
        assert len({slot1, slot2, slot3}) == 3

    def test_when_all_slots_occupied_uses_hash_based_fallback(self, tmp_path):
        # When all palette slots are occupied, fallback is MD5(ai_name) % len(palette)
        # md5("sw-3") % 2 == 1, so slot 1 = blue (#1e88e5)
        cfg = make_iterm2_config(palette={"red": "#e74c3c", "blue": "#1e88e5"})
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.iterm2._iterm2_state_dir", return_value=tmp_path):
                with patch("ai_cli.iterm2._load_iterm2_config", return_value=cfg):
                    _assign_iterm2_color_slot("sw-1", "c")
                    _assign_iterm2_color_slot("sw-2", "c")
                    slot3 = _assign_iterm2_color_slot("sw-3", "c")
        assert slot3 == "1e88e5"

    def test_stale_lease_pruned_on_assignment(self, tmp_path):
        cfg = make_iterm2_config()
        lease_file = tmp_path / "color-leases.json"
        lease_file.write_text(json.dumps({"leases": {"sw-dead": {"slot": 0, "pid": 999999999, "ts": "0"}}}))
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.iterm2._iterm2_state_dir", return_value=tmp_path):
                with patch("ai_cli.iterm2._load_iterm2_config", return_value=cfg):
                    slot = _assign_iterm2_color_slot("sw-1", "c")
        leases = json.loads(lease_file.read_text())["leases"]
        assert "sw-dead" not in leases
        assert "sw-1" in leases
        assert slot is not None

    def test_when_collision_avoidance_disabled_uses_modulo(self, tmp_path):
        cfg = make_iterm2_config(collision_avoidance=False)
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.iterm2._iterm2_state_dir", return_value=tmp_path):
                with patch("ai_cli.iterm2._load_iterm2_config", return_value=cfg):
                    slot = _assign_iterm2_color_slot("sw-2", "c")
        assert slot is not None
        assert slot.lstrip("#") == "1e88e5"

    def test_project_tab_color_pins_preferred_slot(self, tmp_path):
        cfg = make_iterm2_config(projects={"myproject": {"tab_color": "green"}})
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.iterm2._iterm2_state_dir", return_value=tmp_path):
                with patch("ai_cli.iterm2._load_iterm2_config", return_value=cfg):
                    slot = _assign_iterm2_color_slot("sw-1", "c", project_name="myproject")
        assert slot is not None
        assert slot.lstrip("#") == "2ecc71"

    def test_project_tab_color_falls_back_when_preferred_occupied(self, tmp_path):
        cfg = make_iterm2_config(projects={"myproject": {"tab_color": "red"}})
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.iterm2._iterm2_state_dir", return_value=tmp_path):
                with patch("ai_cli.iterm2._load_iterm2_config", return_value=cfg):
                    _assign_iterm2_color_slot("other-session", "c")
                    slot = _assign_iterm2_color_slot("sw-1", "c", project_name="myproject")
        assert slot is not None
        assert slot.lstrip("#") != "e74c3c"

    def test_session_tab_color_pins_preferred_slot(self, tmp_path):
        cfg = make_iterm2_config(sessions={"c-sw-1": {"tab_color": "blue"}})
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.iterm2._iterm2_state_dir", return_value=tmp_path):
                with patch("ai_cli.iterm2._load_iterm2_config", return_value=cfg):
                    slot = _assign_iterm2_color_slot("c-sw-1", "c")
        assert slot is not None
        assert slot.lstrip("#") == "1e88e5"

    def test_session_overrides_project_tab_color(self, tmp_path):
        cfg = make_iterm2_config(
            projects={"myproject": {"tab_color": "red"}},
            sessions={"c-sw-1": {"tab_color": "blue"}},
        )
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.iterm2._iterm2_state_dir", return_value=tmp_path):
                with patch("ai_cli.iterm2._load_iterm2_config", return_value=cfg):
                    slot = _assign_iterm2_color_slot("c-sw-1", "c", project_name="myproject")
        assert slot is not None
        assert slot.lstrip("#") == "1e88e5"  # session wins over project

    def test_defaults_tab_color_used_when_no_project_or_session(self, tmp_path):
        cfg = make_iterm2_config(defaults={"tab_color": "green"})
        with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False):
            with patch("ai_cli.iterm2._iterm2_state_dir", return_value=tmp_path):
                with patch("ai_cli.iterm2._load_iterm2_config", return_value=cfg):
                    slot = _assign_iterm2_color_slot("c-sw-1", "c")
        assert slot is not None
        assert slot.lstrip("#") == "2ecc71"


class TestReleaseIterm2ColorSlot:
    def test_when_lease_exists_then_removes_it(self, tmp_path):
        lease_file = tmp_path / "color-leases.json"
        lease_file.write_text(json.dumps({"leases": {"sw-5": {"slot": 2, "pid": 123, "ts": "0"}}}))
        with patch("ai_cli.iterm2._iterm2_state_dir", return_value=tmp_path):
            _release_iterm2_color_slot("sw-5")
        leases = json.loads(lease_file.read_text())["leases"]
        assert "sw-5" not in leases

    def test_when_lease_missing_then_no_error(self, tmp_path):
        lease_file = tmp_path / "color-leases.json"
        lease_file.write_text(json.dumps({"leases": {}}))
        with patch("ai_cli.iterm2._iterm2_state_dir", return_value=tmp_path):
            _release_iterm2_color_slot("sw-99")

    def test_when_file_missing_then_no_error(self, tmp_path):
        with patch("ai_cli.iterm2._iterm2_state_dir", return_value=tmp_path):
            _release_iterm2_color_slot("sw-1")


class TestReleaseColorSlotCommand:
    def test_release_color_slot_internal_command(self, tmp_path):
        lease_file = tmp_path / "color-leases.json"
        lease_file.write_text(json.dumps({"leases": {"sw-3": {"slot": 0, "pid": 1, "ts": "0"}}}))
        with patch("sys.argv", ["ai", "internal", "release-color-slot", "sw-3"]):
            with patch("ai_cli.iterm2._iterm2_state_dir", return_value=tmp_path):
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

    def test_iterm2_status_function_does_not_use_local_status_variable(self):
        # zsh treats `status` as a read-only special variable; using `local status=`
        # causes an immediate error in zsh sessions, breaking `ai g` launch.
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        assert "local status=" not in script

    def test_script_calls_fleet_setup_with_session_name(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        assert '_iterm2_fleet_setup "$tmux_session"' in script

    def test_script_release_color_slot_in_exit_trap(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        assert 'ai internal release-color-slot "$ai_name"' in script

    def test_gemini_engine_when_default_cmd_then_uses_gemini(self):
        script = get_engine_script("g", "g-proj-1", "g-proj-1", "g-proj-", "proj")
        assert "gemini -y" in script

    def test_gemini_engine_when_custom_cmd_then_uses_custom_cmd(self):
        script = get_engine_script("g", "g-proj-1", "g-proj-1", "g-proj-", "proj", gemini_cmd="npx @google/gemini-cli")
        assert "npx @google/gemini-cli -y" in script
        assert "gemini -y" not in script

    def test_script_cleanup_session_files_in_exit_trap(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        assert 'ai internal cleanup-session-files "$ai_name"' in script

    def test_fleet_setup_uses_osc1_not_osc0(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        assert "\\033]1;" in script
        assert "\\033]0;" not in script

    def test_script_waits_for_tmux_client_on_first_run_before_fleet_setup(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        assert "tmux list-clients -t" in script
        assert "$first_run" in script

    def test_script_uses_stable_path_not_self_delete(self):
        # Script no longer self-deletes (stable path persists for mtime-based hot-reload).
        # The stable path lives under the XDG state dir, not /tmp.
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        assert "/tmp/ai-session-" not in script
        assert "_script_stable_path" in script

    def test_script_set_iterm2_name_uses_live_iterm_id_not_static_env(self):
        # set-iterm2-name must always use _live_iterm_id() (reads from tmux session env,
        # picks up GUID updates on re-attach) — never the static $ITERM_SESSION_ID shell
        # variable (inherited at session creation, never updated in the running process).
        # Using the static var caused cross-session pane-title clobbering after template
        # refresh (ai update → exec bash new-template → first_run=true → stale GUID fired).
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        assert "ai internal set-iterm2-name" in script
        assert "$ITERM_SESSION_ID" not in script


class TestSetIterm2NameApplescript:
    """Tests for _set_iterm2_name_applescript."""

    def test_given_non_darwin_when_called_then_no_subprocess(self):
        with patch("ai_cli.iterm2.subprocess") as mock_sp:
            with patch("ai_cli.iterm2.sys") as mock_sys:
                mock_sys.platform = "linux"
                _set_iterm2_name_applescript("w0t0p0:abc123", "c-sw-1")
        mock_sp.run.assert_not_called()

    def test_given_empty_iterm_session_id_when_called_then_no_subprocess(self):
        with patch("ai_cli.iterm2.sys") as mock_sys:
            mock_sys.platform = "darwin"
            with patch("ai_cli.iterm2.subprocess") as mock_sp:
                _set_iterm2_name_applescript("", "c-sw-1")
        mock_sp.run.assert_not_called()

    def test_given_iterm_session_id_without_colon_then_no_subprocess(self):
        with patch("ai_cli.iterm2.sys") as mock_sys:
            mock_sys.platform = "darwin"
            with patch("ai_cli.iterm2.subprocess") as mock_sp:
                _set_iterm2_name_applescript("no-colon-here", "c-sw-1")
        mock_sp.run.assert_not_called()

    def test_given_darwin_with_valid_session_id_when_called_then_runs_osascript(self):
        with patch("ai_cli.iterm2.sys") as mock_sys:
            mock_sys.platform = "darwin"
            with patch("ai_cli.iterm2.subprocess") as mock_sp:
                _set_iterm2_name_applescript("w0t0p0:abc-guid-123", "c-sw-1")
        mock_sp.run.assert_called_once()
        call_args = mock_sp.run.call_args[0][0]
        assert call_args[0] == "osascript"
        assert "abc-guid-123" in mock_sp.run.call_args[0][0][2]
        assert "c-sw-1" in mock_sp.run.call_args[0][0][2]

    def test_guid_extracted_correctly_from_iterm_session_id(self):
        captured_script = []
        with patch("ai_cli.iterm2.sys") as mock_sys:
            mock_sys.platform = "darwin"
            with patch("ai_cli.iterm2.subprocess") as mock_sp:
                mock_sp.run = lambda cmd, **kw: captured_script.append(cmd[2])
                _set_iterm2_name_applescript("w0t0p0:my-actual-guid", "test-session")
        assert len(captured_script) == 1
        assert "my-actual-guid" in captured_script[0]
        assert "test-session" in captured_script[0]


class TestSetIterm2NameInternalCommand:
    """Tests for ai internal set-iterm2-name."""

    def test_set_iterm2_name_command_calls_applescript(self):
        with patch("sys.argv", ["ai", "internal", "set-iterm2-name", "w0t0p0:myguid", "c-sw-5"]):
            with patch("ai_cli.iterm2._set_iterm2_name_applescript") as mock_fn:
                with pytest.raises(SystemExit) as exc:
                    cli()
        assert exc.value.code == 0
        mock_fn.assert_called_once_with("w0t0p0:myguid", "c-sw-5")

    def test_set_iterm2_name_missing_args_exits_1(self):
        with patch("sys.argv", ["ai", "internal", "set-iterm2-name"]):
            with pytest.raises(SystemExit) as exc:
                cli()
        assert exc.value.code == 1


class TestEmitIterm2ProfileSetupCallsApplescript:
    """Verify _emit_iterm2_profile_setup calls _set_iterm2_name_applescript.

    Always uses the shell-env ITERM_SESSION_ID (set by iTerm2 at pane creation).
    This is the physical pane's GUID, correct in all contexts including when
    ai c is launched from inside an existing tmux session.
    """

    def test_given_iterm2_env_outside_tmux_when_emit_called_then_applescript_uses_env_guid(self):
        env = {"LC_TERMINAL": "iTerm2", "ITERM_SESSION_ID": "w0t0p0:myguid", "TMUX": ""}
        with patch.dict(os.environ, env, clear=False):
            with patch("ai_cli.iterm2._load_iterm2_config", return_value={}):
                with patch("ai_cli.icon_generator.generate_session_icon", return_value=None):
                    with patch("ai_cli.icon_generator.generate_dynamic_profile"):
                        with patch("ai_cli.iterm2._set_iterm2_name_applescript") as mock_fn:
                            _emit_iterm2_profile_setup("sw-1", "c", session="c-sw-1")
        mock_fn.assert_called_once_with("w0t0p0:myguid", "c-sw-1")

    def test_given_iterm2_env_inside_tmux_when_emit_called_then_applescript_uses_shell_env_guid(self):
        # Inside tmux: shell env GUID is the correct current-pane GUID (AI-CLI-59 fix).
        # The previous tmux show-environment approach returned the parent session's stale GUID.
        env = {
            "LC_TERMINAL": "iTerm2",
            "ITERM_SESSION_ID": "w0t0p0:current-pane-guid",
            "TMUX": "/private/tmp/tmux-502/default,3142,0",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch("ai_cli.iterm2._load_iterm2_config", return_value={}):
                with patch("ai_cli.icon_generator.generate_session_icon", return_value=None):
                    with patch("ai_cli.icon_generator.generate_dynamic_profile"):
                        with patch("ai_cli.iterm2._set_iterm2_name_applescript") as mock_fn:
                            _emit_iterm2_profile_setup("sw-1", "c", session="c-sw-1")
        mock_fn.assert_called_once_with("w0t0p0:current-pane-guid", "c-sw-1")

    def test_given_no_iterm_session_id_when_emit_called_then_applescript_with_empty_id(self):
        env = {"LC_TERMINAL": "iTerm2", "TMUX": ""}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("ITERM_SESSION_ID", None)
            with patch("ai_cli.iterm2._load_iterm2_config", return_value={}):
                with patch("ai_cli.icon_generator.generate_session_icon", return_value=None):
                    with patch("ai_cli.icon_generator.generate_dynamic_profile"):
                        with patch("ai_cli.iterm2._set_iterm2_name_applescript") as mock_fn:
                            _emit_iterm2_profile_setup("sw-1", "c", session="c-sw-1")
        mock_fn.assert_called_once_with("", "c-sw-1")


class TestFleetSetupAttachmentGuard:
    """The session script must not rename a pane when the session is detached.

    Two sessions share the same ITERM_SESSION_ID when one is spawned from inside
    the other's shell (the child inherits the env var).  Without an attachment
    check, a detached session's CC restart calls ``set-iterm2-name`` with the
    shared GUID, clobbering the visible session's pane title.  The guard ensures
    ``set-iterm2-name`` only fires when ``tmux list-clients`` shows at least one
    active client for *this* session.
    """

    def test_fleet_setup_guards_rename_with_client_count_check(self):
        """_iterm2_fleet_setup must check attachment before calling set-iterm2-name."""
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        fn_def_pos = script.index("_iterm2_fleet_setup() ")
        status_def_pos = script.index("_iterm2_status() ", fn_def_pos)
        fleet_body = script[fn_def_pos:status_def_pos]
        # Guard and rename must both be present in the function body
        assert 'tmux list-clients -t "$tmux_session"' in fleet_body
        assert "ai internal set-iterm2-name" in fleet_body

    def test_fleet_setup_attachment_guard_comes_before_rename(self):
        """The attachment guard must appear before set-iterm2-name in fleet_setup."""
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        fn_def_pos = script.index("_iterm2_fleet_setup() ")
        status_def_pos = script.index("_iterm2_status() ", fn_def_pos)
        fleet_body = script[fn_def_pos:status_def_pos]
        guard_offset = fleet_body.index('tmux list-clients -t "$tmux_session"')
        rename_offset = fleet_body.index("ai internal set-iterm2-name")
        assert guard_offset < rename_offset, "attachment guard must precede set-iterm2-name in _iterm2_fleet_setup"

    def test_fleet_setup_guard_requires_at_least_one_client(self):
        """Guard condition must be -gt 0 (not -ge 0 or absent)."""
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        fn_def_pos = script.index("_iterm2_fleet_setup() ")
        status_def_pos = script.index("_iterm2_status() ", fn_def_pos)
        fleet_body = script[fn_def_pos:status_def_pos]
        assert "-gt 0" in fleet_body

    def test_iterm2_status_guards_rename_with_client_count_check(self):
        """_iterm2_status must check attachment before calling set-iterm2-name."""
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        status_def_pos = script.index("_iterm2_status() ")
        # Grab a generous slice of _iterm2_status body
        status_body = script[status_def_pos : status_def_pos + 1000]
        assert 'tmux list-clients -t "$tmux_session"' in status_body
        assert "ai internal set-iterm2-name" in status_body

    def test_iterm2_status_attachment_guard_comes_before_rename(self):
        """The attachment guard must appear before set-iterm2-name in _iterm2_status."""
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        status_def_pos = script.index("_iterm2_status() ")
        status_body = script[status_def_pos : status_def_pos + 1000]
        guard_offset = status_body.index('tmux list-clients -t "$tmux_session"')
        rename_offset = status_body.index("ai internal set-iterm2-name")
        assert guard_offset < rename_offset, "attachment guard must precede set-iterm2-name in _iterm2_status"

    def test_iterm2_status_guard_requires_at_least_one_client(self):
        """_iterm2_status guard condition must be -gt 0."""
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        status_def_pos = script.index("_iterm2_status() ")
        status_body = script[status_def_pos : status_def_pos + 1000]
        assert "-gt 0" in status_body

    def test_fleet_setup_falls_back_to_osc1_when_no_live_guid(self):
        """When _live_iterm_id returns empty, fleet_setup falls back to OSC 1 (not OSC 0)."""
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        fn_def_pos = script.index("_iterm2_fleet_setup() ")
        status_def_pos = script.index("_iterm2_status() ", fn_def_pos)
        fleet_body = script[fn_def_pos:status_def_pos]
        assert "\\033]1;" in fleet_body  # OSC 1 = title only
        assert "\\033]0;" not in fleet_body  # OSC 0 = title+icon, must not appear

    def test_iterm2_status_falls_back_to_osc1_when_no_live_guid(self):
        """When _live_iterm_id returns empty, _iterm2_status falls back to OSC 1."""
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        status_def_pos = script.index("_iterm2_status() ")
        status_body = script[status_def_pos : status_def_pos + 1000]
        assert "\\033]1;" in status_body
        assert "\\033]0;" not in status_body

    def test_live_iterm_id_reads_from_tmux_not_shell_env(self):
        """_live_iterm_id must use tmux show-environment, not the static shell $ITERM_SESSION_ID.

        Reading from the tmux session environment (not the initial shell env)
        ensures the function picks up GUID updates made by ``ai c`` when
        re-attaching the session to a different iTerm pane.
        """
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        fn_pos = script.index("_live_iterm_id()")
        fn_body = script[fn_pos : fn_pos + 200]
        assert "tmux show-environment" in fn_body
        # Must not rely on the static inherited shell variable
        assert "$ITERM_SESSION_ID" not in fn_body


class TestGetCurrentItermSessionId:
    """Tests for _get_current_iterm_session_id — the shared GUID resolver.

    Always returns the shell-env ITERM_SESSION_ID (set by iTerm2 integration at
    pane creation).  This is the physical pane's GUID regardless of which tmux
    session the shell is attached to.

    The previous implementation read from tmux show-environment when TMUX was set,
    which returned the parent session's stored GUID — causing the wrong pane to be
    renamed when ai c was launched from inside an existing tmux session (AI-CLI-59).
    """

    def test_when_outside_tmux_then_returns_shell_env_guid(self):
        """Outside tmux: returns os.environ ITERM_SESSION_ID."""
        env = {"ITERM_SESSION_ID": "w0t0p0:shell-guid", "TMUX": ""}
        with patch.dict(os.environ, env, clear=False):
            result = _get_current_iterm_session_id()
        assert result == "w0t0p0:shell-guid"

    def test_when_inside_tmux_then_still_returns_shell_env_guid(self):
        """Inside tmux: shell env GUID is still the correct current-pane GUID.

        The tmux session env may hold a stale GUID from a different (parent) session
        when ai c is launched from inside an existing tmux context.  The shell env
        ITERM_SESSION_ID is always set by iTerm2 for the physical pane, so it is
        authoritative regardless of tmux nesting.
        """
        env = {
            "ITERM_SESSION_ID": "w0t0p0:current-pane-guid",
            "TMUX": "/private/tmp/tmux-1234/default,100,0",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch("ai_cli.iterm2.subprocess.run") as mock_run:
                result = _get_current_iterm_session_id()
        assert result == "w0t0p0:current-pane-guid"
        mock_run.assert_not_called()

    def test_when_inside_tmux_shell_env_wins_over_tmux_env(self):
        """Shell env must win even if tmux session env has a different value.

        This is the core AI-CLI-59 regression test: launching ai c from inside
        tmux session A (GUID aaa) while physically sitting in pane B (GUID bbb)
        must return bbb (shell env), not aaa (tmux session env).
        """
        env = {
            "ITERM_SESSION_ID": "w0t0p0:pane-b-guid",  # correct: physical pane
            "TMUX": "/private/tmp/tmux-1234/default,100,0",
        }
        # Even if tmux would return a different value, it must not be called
        with patch.dict(os.environ, env, clear=False):
            with patch("ai_cli.iterm2.subprocess.run") as mock_run:
                result = _get_current_iterm_session_id()
        assert result == "w0t0p0:pane-b-guid"
        mock_run.assert_not_called()

    def test_when_no_iterm_session_id_in_env_then_returns_empty_string(self):
        """When ITERM_SESSION_ID is absent, returns empty string."""
        env = {"TMUX": ""}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("ITERM_SESSION_ID", None)
            result = _get_current_iterm_session_id()
        assert result == ""

    def test_when_inside_tmux_and_no_shell_guid_then_returns_empty_string(self):
        """When ITERM_SESSION_ID absent, returns empty regardless of tmux state."""
        env = {"TMUX": "/private/tmp/tmux-1234/default,100,0"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("ITERM_SESSION_ID", None)
            with patch("ai_cli.iterm2.subprocess.run") as mock_run:
                result = _get_current_iterm_session_id()
        mock_run.assert_not_called()
        assert result == ""


class TestDoSessionLaunchItermGuid:
    """_do_session_launch must use the shell-env GUID via _get_current_iterm_session_id.

    The shell-env ITERM_SESSION_ID is always the correct current-pane GUID set by
    iTerm2 shell integration.  _do_session_launch must go through the helper rather
    than reading os.environ directly, so the logic stays centralized.
    """

    def test_iterm_env_flags_use_guid_helper(self):
        """_do_session_launch must use _get_current_iterm_session_id, not os.environ directly.

        Structural guard: inspects source to ensure the function uses the helper.
        Fails if the fix is reverted to a direct os.environ read.
        """
        import inspect
        from ai_cli import main as _main_mod

        src = inspect.getsource(_main_mod._do_session_launch)
        assert "_get_current_iterm_session_id" in src
        assert 'os.environ.get("ITERM_SESSION_ID"' not in src


class TestEvictIterm2Guid:
    """_evict_iterm2_guid removes the GUID from all sessions except the owner.

    Root cause of AI-CLI-59: multiple sessions accumulate the same ITERM_SESSION_ID
    in their tmux environments and all rename the same pane on CC restart.
    """

    def _make_fake_run(
        self,
        session_guids: dict[str, str],
        sessions_with_clients: set[str] | None = None,
    ):
        """Return a fake subprocess.run that models tmux list-sessions + show-environment + list-clients."""
        calls: list[list[str]] = []
        _sessions_with_clients = sessions_with_clients or set()

        def fake_run(cmd, *args, **kwargs):
            from unittest.mock import MagicMock

            calls.append(list(cmd))
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            if cmd[1] == "list-sessions":
                result.stdout = "\n".join(session_guids.keys()) + "\n"
            elif cmd[1] == "show-environment":
                session = cmd[cmd.index("-t") + 1]
                guid = session_guids.get(session, "")
                if guid:
                    result.stdout = f"ITERM_SESSION_ID={guid}\n"
                else:
                    result.returncode = 1
            elif cmd[1] == "list-clients":
                session = cmd[cmd.index("-t") + 1]
                result.stdout = "client0\n" if session in _sessions_with_clients else ""
            return result

        return fake_run, calls

    def test_when_other_sessions_have_same_guid_then_they_are_cleared(self):
        guid = "w0t0p0:AAAA-1111"
        sessions = {"c-sw-1": guid, "c-ai-cli-1": guid, "c-aido-1": "w0t0p1:BBBB-2222"}
        fake_run, calls = self._make_fake_run(sessions)

        with patch("ai_cli.iterm2.subprocess.run", side_effect=fake_run):
            _evict_iterm2_guid(guid, "c-ai-cli-1")

        unset_calls = [c for c in calls if "set-environment" in c and "-u" in c]
        cleared_sessions = [c[c.index("-t") + 1] for c in unset_calls]
        assert "c-sw-1" in cleared_sessions
        assert "c-ai-cli-1" not in cleared_sessions  # owner must not be cleared
        assert "c-aido-1" not in cleared_sessions  # different GUID — untouched

    def test_when_owner_has_guid_then_owner_is_not_cleared(self):
        guid = "w0t0p0:AAAA-1111"
        sessions = {"c-ai-cli-1": guid}
        fake_run, calls = self._make_fake_run(sessions)

        with patch("ai_cli.iterm2.subprocess.run", side_effect=fake_run):
            _evict_iterm2_guid(guid, "c-ai-cli-1")

        unset_calls = [c for c in calls if "set-environment" in c and "-u" in c]
        assert unset_calls == []

    def test_when_no_sessions_share_guid_then_no_unset_calls(self):
        guid = "w0t0p0:AAAA-1111"
        sessions = {"c-sw-1": "w0t0p5:CCCC-3333", "c-aido-1": "w0t0p1:BBBB-2222"}
        fake_run, calls = self._make_fake_run(sessions)

        with patch("ai_cli.iterm2.subprocess.run", side_effect=fake_run):
            _evict_iterm2_guid(guid, "c-ai-cli-1")

        unset_calls = [c for c in calls if "set-environment" in c and "-u" in c]
        assert unset_calls == []

    def test_when_guid_empty_then_no_subprocess_calls(self):
        fake_run, calls = self._make_fake_run({})

        with patch("ai_cli.iterm2.subprocess.run", side_effect=fake_run):
            _evict_iterm2_guid("", "c-ai-cli-1")

        assert calls == []

    def test_when_multiple_stale_sessions_then_all_cleared(self):
        guid = "w0t0p15:C37C7927"
        sessions = {
            "c-ai-cli-1": guid,  # owner
            "c-hm-1": guid,  # stale
            "g-myproject-1": guid,  # stale
            "g-sw-1": guid,  # stale
        }
        fake_run, calls = self._make_fake_run(sessions)

        with patch("ai_cli.iterm2.subprocess.run", side_effect=fake_run):
            _evict_iterm2_guid(guid, "c-ai-cli-1")

        unset_calls = [c for c in calls if "set-environment" in c and "-u" in c]
        cleared = {c[c.index("-t") + 1] for c in unset_calls}
        assert cleared == {"c-hm-1", "g-myproject-1", "g-sw-1"}
        assert "c-ai-cli-1" not in cleared

    def test_when_stale_session_has_active_clients_then_not_evicted(self):
        """AI-CLI-59 regression: a session with active clients still owns its GUID — skip it."""
        guid = "w0t0p15:C37C7927"
        sessions = {
            "c-ai-cli-1": guid,  # new owner
            "c-hm-1": guid,  # has active clients → must NOT be evicted
            "c-sw-1": guid,  # detached → must be evicted
        }
        # c-hm-1 is currently attached to a physical pane
        fake_run, calls = self._make_fake_run(sessions, sessions_with_clients={"c-hm-1"})

        with patch("ai_cli.iterm2.subprocess.run", side_effect=fake_run):
            _evict_iterm2_guid(guid, "c-ai-cli-1")

        unset_calls = [c for c in calls if "set-environment" in c and "-u" in c]
        cleared = {c[c.index("-t") + 1] for c in unset_calls}
        assert "c-hm-1" not in cleared  # active client — preserved
        assert "c-sw-1" in cleared  # detached — evicted
        assert "c-ai-cli-1" not in cleared  # owner — never touched
