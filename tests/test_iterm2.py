import json
import os
from unittest.mock import patch

import pytest
from conftest import make_iterm2_config

from ai_cli.main import (
    _assign_iterm2_color_slot,
    _current_pane_tty,
    _emit_iterm2_profile_setup,
    _is_iterm2,
    _iterm2_palette,
    _iterm2_state_dir,
    _iterm_pane_tty_for_tmux_session,
    _load_iterm2_config,
    _release_iterm2_color_slot,
    _resolve_iterm2_config,
    _set_iterm2_name_by_tty,
    cli,
    get_engine_script,
)


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

    def test_script_calls_fleet_setup_with_prefixed_session_name(self):
        # Display the full engine-prefixed session id (c-sw-1 for Claude, g-… for
        # Gemini) so panes are distinguishable by engine — NOT the stripped ai_name.
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

    def test_script_set_iterm2_name_targets_tmux_session_not_guid_env(self):
        # set-iterm2-name must resolve the pane by the tmux session's live client
        # tty — never a stored/inherited $ITERM_SESSION_ID GUID (AI-CLI-59 root
        # cause).  The script passes "$tmux_session" so the pane is resolved live.
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        assert 'ai internal set-iterm2-name "$tmux_session"' in script
        # No GUID plumbing left in the script.
        assert "$ITERM_SESSION_ID" not in script
        assert "_live_iterm_id" not in script
        assert "show-environment ITERM_SESSION_ID" not in script

    def test_script_displays_engine_prefixed_session_id(self):
        # The pane label must be the engine-prefixed session id (c-sw-1 / g-…),
        # not the stripped short name — so Claude vs Gemini sessions are
        # visually distinguishable. Callers pass "$tmux_session".
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        assert '_iterm2_fleet_setup "$tmux_session"' in script
        assert '_iterm2_status "running" "$_session_type" "$tmux_session"' in script
        # The stripped short-name form must be gone.
        assert '_iterm2_fleet_setup "$ai_name"' not in script


class TestSetIterm2NameByTty:
    """Tests for _set_iterm2_name_by_tty — renames the iTerm pane on a given tty."""

    def test_given_non_darwin_when_called_then_no_subprocess(self):
        with patch("ai_cli.iterm2.subprocess") as mock_sp:
            with patch("ai_cli.iterm2.sys") as mock_sys:
                mock_sys.platform = "linux"
                _set_iterm2_name_by_tty("/dev/ttys000", "sw-1")
        mock_sp.run.assert_not_called()

    def test_given_empty_tty_when_called_then_no_subprocess(self):
        with patch("ai_cli.iterm2.sys") as mock_sys:
            mock_sys.platform = "darwin"
            with patch("ai_cli.iterm2.subprocess") as mock_sp:
                _set_iterm2_name_by_tty("", "sw-1")
        mock_sp.run.assert_not_called()

    def test_given_darwin_with_tty_then_runs_osascript_matching_tty(self):
        from unittest.mock import MagicMock

        with patch("ai_cli.iterm2.sys") as mock_sys:
            mock_sys.platform = "darwin"
            with patch("ai_cli.iterm2.subprocess") as mock_sp:
                mock_sp.run.return_value = MagicMock(stdout="ok")
                result = _set_iterm2_name_by_tty("/dev/ttys007", "sw-1")
        mock_sp.run.assert_called_once()
        cmd = mock_sp.run.call_args[0][0]
        assert cmd[0] == "osascript"
        assert "/dev/ttys007" in cmd[2]
        assert "tty of s" in cmd[2]  # matches on tty, not unique id
        assert "sw-1" in cmd[2]
        assert result is True

    def test_returns_false_when_no_pane_matches_tty(self):
        from unittest.mock import MagicMock

        with patch("ai_cli.iterm2.sys") as mock_sys:
            mock_sys.platform = "darwin"
            with patch("ai_cli.iterm2.subprocess") as mock_sp:
                mock_sp.run.return_value = MagicMock(stdout="miss")
                result = _set_iterm2_name_by_tty("/dev/ttys099", "sw-1")
        assert result is False


class TestItermPaneTtyForTmuxSession:
    """Tests for _iterm_pane_tty_for_tmux_session — resolves a session's client tty."""

    def test_returns_first_client_tty(self):
        from unittest.mock import MagicMock

        with patch("ai_cli.iterm2.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="/dev/ttys000\n")
            tty = _iterm_pane_tty_for_tmux_session("c-sw-3")
        assert tty == "/dev/ttys000"
        cmd = mock_run.call_args[0][0]
        assert cmd[:4] == ["tmux", "list-clients", "-t", "c-sw-3"]

    def test_returns_empty_when_detached(self):
        from unittest.mock import MagicMock

        with patch("ai_cli.iterm2.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="\n")
            assert _iterm_pane_tty_for_tmux_session("c-sw-3") == ""

    def test_returns_empty_when_tmux_fails(self):
        from unittest.mock import MagicMock

        with patch("ai_cli.iterm2.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            assert _iterm_pane_tty_for_tmux_session("nope") == ""

    def test_returns_empty_for_empty_session(self):
        with patch("ai_cli.iterm2.subprocess.run") as mock_run:
            assert _iterm_pane_tty_for_tmux_session("") == ""
        mock_run.assert_not_called()


class TestCurrentPaneTty:
    """Tests for _current_pane_tty — the process's own controlling tty."""

    def test_returns_ttyname_of_stdout(self):
        with patch("ai_cli.iterm2.os.ttyname", return_value="/dev/ttys005") as mock_tty:
            assert _current_pane_tty() == "/dev/ttys005"
        # Tries stdout (fd 1) first.
        assert mock_tty.call_args_list[0][0][0] == 1

    def test_falls_through_fds_and_returns_empty_when_no_tty(self):
        with patch("ai_cli.iterm2.os.ttyname", side_effect=OSError):
            assert _current_pane_tty() == ""


class TestSetIterm2NameInternalCommand:
    """Tests for ai internal set-iterm2-name — resolves pane by tty."""

    def test_tmux_session_arg_resolves_tty_then_renames(self):
        with patch("sys.argv", ["ai", "internal", "set-iterm2-name", "c-sw-5", "sw-5"]):
            with patch("ai_cli.iterm2._iterm_pane_tty_for_tmux_session", return_value="/dev/ttys009") as mock_tty:
                with patch("ai_cli.iterm2._set_iterm2_name_by_tty") as mock_set:
                    with pytest.raises(SystemExit) as exc:
                        cli()
        assert exc.value.code == 0
        mock_tty.assert_called_once_with("c-sw-5")
        mock_set.assert_called_once_with("/dev/ttys009", "sw-5")

    def test_tty_arg_passed_through_directly(self):
        with patch("sys.argv", ["ai", "internal", "set-iterm2-name", "/dev/ttys003", "sw-5"]):
            with patch("ai_cli.iterm2._iterm_pane_tty_for_tmux_session") as mock_tty:
                with patch("ai_cli.iterm2._set_iterm2_name_by_tty") as mock_set:
                    with pytest.raises(SystemExit) as exc:
                        cli()
        assert exc.value.code == 0
        mock_tty.assert_not_called()  # already a tty — no lookup
        mock_set.assert_called_once_with("/dev/ttys003", "sw-5")

    def test_set_iterm2_name_missing_args_exits_1(self):
        with patch("sys.argv", ["ai", "internal", "set-iterm2-name"]):
            with pytest.raises(SystemExit) as exc:
                cli()
        assert exc.value.code == 1


class TestEmitIterm2ProfileSetupRenamesByTty:
    """_emit_iterm2_profile_setup renames the launching pane by its own tty."""

    def test_emit_calls_set_name_by_current_pane_tty(self):
        env = {"LC_TERMINAL": "iTerm2", "TMUX": ""}
        with patch.dict(os.environ, env, clear=False):
            with patch("ai_cli.iterm2._load_iterm2_config", return_value={}):
                with patch("ai_cli.icon_generator.generate_session_icon", return_value=None):
                    with patch("ai_cli.icon_generator.generate_dynamic_profile"):
                        with patch("ai_cli.iterm2._current_pane_tty", return_value="/dev/ttys002"):
                            with patch("ai_cli.iterm2._set_iterm2_name_by_tty") as mock_fn:
                                _emit_iterm2_profile_setup("sw-1", "c", session="c-sw-1")
        mock_fn.assert_called_once_with("/dev/ttys002", "c-sw-1")

    def test_emit_passes_empty_tty_when_not_a_terminal(self):
        env = {"LC_TERMINAL": "iTerm2", "TMUX": ""}
        with patch.dict(os.environ, env, clear=False):
            with patch("ai_cli.iterm2._load_iterm2_config", return_value={}):
                with patch("ai_cli.icon_generator.generate_session_icon", return_value=None):
                    with patch("ai_cli.icon_generator.generate_dynamic_profile"):
                        with patch("ai_cli.iterm2._current_pane_tty", return_value=""):
                            with patch("ai_cli.iterm2._set_iterm2_name_by_tty") as mock_fn:
                                _emit_iterm2_profile_setup("sw-1", "c", session="c-sw-1")
        mock_fn.assert_called_once_with("", "c-sw-1")


class TestRenameAttachmentGuard:
    """The session script must only rename a pane when the session is attached.

    A detached session has no client tty and must not touch any pane.  The shared
    _iterm2_rename helper checks ``tmux list-clients`` before calling
    set-iterm2-name, and falls back to OSC 1 when detached.
    """

    def _rename_body(self, script: str) -> str:
        start = script.index("_iterm2_rename() ")
        end = script.index("_iterm2_fleet_setup() ", start)
        return script[start:end]

    def test_rename_guards_with_client_count_check_before_set_name(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        body = self._rename_body(script)
        assert 'tmux list-clients -t "$tmux_session"' in body
        assert "ai internal set-iterm2-name" in body
        guard_offset = body.index('tmux list-clients -t "$tmux_session"')
        rename_offset = body.index("ai internal set-iterm2-name")
        assert guard_offset < rename_offset, "attachment guard must precede set-iterm2-name"

    def test_rename_guard_requires_at_least_one_client(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        assert "-gt 0" in self._rename_body(script)

    def test_rename_falls_back_to_osc1_when_detached(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        body = self._rename_body(script)
        assert "\\033]1;" in body  # OSC 1 = title only
        assert "\\033]0;" not in body  # OSC 0 = title+icon, must not appear

    def test_both_fleet_and_status_delegate_to_rename(self):
        script = get_engine_script("c", "sw-1", "c-sw-1", "c-sw-", "sw")
        fleet_pos = script.index("_iterm2_fleet_setup() ")
        status_pos = script.index("_iterm2_status() ", fleet_pos)
        fleet_body = script[fleet_pos:status_pos]
        status_body = script[status_pos : status_pos + 1000]
        assert "_iterm2_rename " in fleet_body
        assert "_iterm2_rename " in status_body
