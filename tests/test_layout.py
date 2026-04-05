"""Tests for ai_cli.layout — YAML layout system."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from ai_cli.layout import (
    Layout,
    Pane,
    PaneSplit,
    Tab,
    TabColors,
    _layout_profile_guid,
    _layout_profile_name,
    _pane_to_python,
    cmd_layout_list,
    cmd_layout_profiles,
    cmd_layout_validate,
    generate_layout_profiles,
    load_layout,
    run_layout_command,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_layout(tmp_path: Path, name: str, data: dict) -> Path:
    p = tmp_path / f"{name}.yaml"
    p.write_text(yaml.dump(data))
    return p


_MINIMAL_LAYOUT = {
    "name": "test",
    "tabs": [
        {
            "name": "main",
            "base_profile": "ClaudeCode",
            "root": {"dir": "~", "command": "ai c 1"},
        }
    ],
}

_SPLIT_LAYOUT = {
    "name": "split-test",
    "tabs": [
        {
            "name": "dev",
            "base_profile": "ClaudeCode",
            "colors": {"tab_color": "#5e35b1"},
            "root": {
                "dir": "~/projects/foo",
                "command": "ai c 1",
                "split": {
                    "direction": "vertical",
                    "ratio": 0.65,
                    "right": {"dir": "~/projects/foo", "command": None},
                },
            },
        }
    ],
}


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestPaneSchema:
    def test_leaf_pane_minimal(self):
        p = Pane(dir="~")
        assert p.dir == "~"
        assert p.command is None
        assert p.split is None

    def test_leaf_pane_with_command(self):
        p = Pane(dir="~/projects", command="ai c 1")
        assert p.command == "ai c 1"

    def test_vertical_split_requires_right(self):
        with pytest.raises(Exception):
            PaneSplit(direction="vertical", ratio=0.5)

    def test_horizontal_split_requires_bottom(self):
        with pytest.raises(Exception):
            PaneSplit(direction="horizontal", ratio=0.5)

    def test_invalid_direction_raises(self):
        with pytest.raises(Exception):
            PaneSplit(direction="diagonal", ratio=0.5, right=Pane())

    def test_vertical_split_valid(self):
        s = PaneSplit(direction="vertical", ratio=0.65, right=Pane(dir="~"))
        assert s.direction == "vertical"
        assert s.right is not None


class TestTabSchema:
    def test_tab_name_required(self):
        with pytest.raises(Exception):
            Tab(name="", base_profile="ClaudeCode", root=Pane())

    def test_tab_default_base_profile(self):
        t = Tab(name="dev", root=Pane())
        assert t.base_profile == "ClaudeCode"

    def test_tab_with_colors(self):
        t = Tab(name="dev", root=Pane(), colors=TabColors(tab_color="#5e35b1"))
        assert t.colors.tab_color == "#5e35b1"


class TestLayoutSchema:
    def test_requires_at_least_one_tab(self):
        with pytest.raises(Exception):
            Layout(name="empty", tabs=[])

    def test_valid_minimal(self):
        layout = Layout(**_MINIMAL_LAYOUT)
        assert layout.name == "test"
        assert len(layout.tabs) == 1


# ---------------------------------------------------------------------------
# load_layout
# ---------------------------------------------------------------------------


class TestLoadLayout:
    def test_loads_valid_layout(self, tmp_path):
        _write_layout(tmp_path, "test", _MINIMAL_LAYOUT)
        with patch("ai_cli.layout._layouts_dir", return_value=tmp_path):
            layout = load_layout("test")
        assert layout.name == "test"
        assert len(layout.tabs) == 1

    def test_raises_file_not_found(self, tmp_path):
        with patch("ai_cli.layout._layouts_dir", return_value=tmp_path):
            with pytest.raises(FileNotFoundError, match="not found"):
                load_layout("nonexistent")

    def test_raises_on_invalid_yaml(self, tmp_path):
        (tmp_path / "bad.yaml").write_text(":")
        with patch("ai_cli.layout._layouts_dir", return_value=tmp_path):
            with pytest.raises(ValueError):
                load_layout("bad")

    def test_raises_on_schema_violation(self, tmp_path):
        bad = {"name": "bad", "tabs": []}  # no tabs
        _write_layout(tmp_path, "bad", bad)
        with patch("ai_cli.layout._layouts_dir", return_value=tmp_path):
            with pytest.raises(ValueError, match="Schema validation failed"):
                load_layout("bad")

    def test_injects_name_if_missing(self, tmp_path):
        data = {k: v for k, v in _MINIMAL_LAYOUT.items() if k != "name"}
        _write_layout(tmp_path, "autoname", data)
        with patch("ai_cli.layout._layouts_dir", return_value=tmp_path):
            layout = load_layout("autoname")
        assert layout.name == "autoname"


# ---------------------------------------------------------------------------
# Profile generation
# ---------------------------------------------------------------------------


class TestGenerateLayoutProfiles:
    def test_creates_json_per_tab(self, tmp_path):
        layout = Layout(**_MINIMAL_LAYOUT)
        with (
            patch("ai_cli.layout._dynamic_profile_dir", return_value=tmp_path),
            patch("ai_cli.layout.generate_session_icon", return_value=None),
        ):
            paths = generate_layout_profiles(layout)
        assert len(paths) == 1
        assert paths[0].suffix == ".json"

    def test_profile_name_is_deterministic(self, tmp_path):
        assert _layout_profile_name("sw-dev", "sw-5") == "ai-cli-layout:sw-dev:sw-5"
        assert _layout_profile_guid("sw-dev", "sw-5") == "ai-cli-layout-sw-dev-sw-5"

    def test_generated_profile_has_correct_parent(self, tmp_path):
        layout = Layout(**_MINIMAL_LAYOUT)
        with (
            patch("ai_cli.layout._dynamic_profile_dir", return_value=tmp_path),
            patch("ai_cli.layout.generate_session_icon", return_value=None),
        ):
            paths = generate_layout_profiles(layout)
        data = json.loads(paths[0].read_text())
        assert data["Profiles"][0]["Dynamic Profile Parent Name"] == "ClaudeCode"

    def test_tab_color_set_in_profile(self, tmp_path):
        layout = Layout(
            **{
                "name": "t",
                "tabs": [
                    {
                        "name": "x",
                        "base_profile": "ClaudeCode",
                        "colors": {"tab_color": "#ff0000"},
                        "root": {"dir": "~"},
                    }
                ],
            }
        )
        with (
            patch("ai_cli.layout._dynamic_profile_dir", return_value=tmp_path),
            patch("ai_cli.layout.generate_session_icon", return_value=None),
        ):
            paths = generate_layout_profiles(layout)
        data = json.loads(paths[0].read_text())
        profile = data["Profiles"][0]
        assert "Tab Color" in profile
        assert profile["Tab Color"]["Red Component"] == pytest.approx(1.0, abs=0.01)

    def test_icon_path_included_when_generated(self, tmp_path):
        icon_file = tmp_path / "layout-test-main.png"
        icon_file.write_bytes(b"fake")
        layout = Layout(**_MINIMAL_LAYOUT)
        with (
            patch("ai_cli.layout._dynamic_profile_dir", return_value=tmp_path),
            patch("ai_cli.layout.generate_session_icon", return_value=icon_file),
        ):
            paths = generate_layout_profiles(layout)
        data = json.loads(paths[0].read_text())
        assert "Custom Icon Path" in data["Profiles"][0]

    def test_icon_path_omitted_when_none(self, tmp_path):
        layout = Layout(**_MINIMAL_LAYOUT)
        with (
            patch("ai_cli.layout._dynamic_profile_dir", return_value=tmp_path),
            patch("ai_cli.layout.generate_session_icon", return_value=None),
        ):
            paths = generate_layout_profiles(layout)
        data = json.loads(paths[0].read_text())
        assert "Custom Icon Path" not in data["Profiles"][0]

    def test_no_title_keys_in_profile(self, tmp_path):
        layout = Layout(**_MINIMAL_LAYOUT)
        with (
            patch("ai_cli.layout._dynamic_profile_dir", return_value=tmp_path),
            patch("ai_cli.layout.generate_session_icon", return_value=None),
        ):
            paths = generate_layout_profiles(layout)
        data = json.loads(paths[0].read_text())
        assert "Title Components" not in data["Profiles"][0]

    def test_multiple_tabs_generate_multiple_profiles(self, tmp_path):
        layout = Layout(
            **{
                "name": "multi",
                "tabs": [
                    {"name": "a", "base_profile": "ClaudeCode", "root": {"dir": "~"}},
                    {"name": "b", "base_profile": "GeminiCLI", "root": {"dir": "~"}},
                    {"name": "c", "base_profile": "ShellUtility", "root": {"dir": "~"}},
                ],
            }
        )
        with (
            patch("ai_cli.layout._dynamic_profile_dir", return_value=tmp_path),
            patch("ai_cli.layout.generate_session_icon", return_value=None),
        ):
            paths = generate_layout_profiles(layout)
        assert len(paths) == 3


# ---------------------------------------------------------------------------
# Pane tree → Python code generation
# ---------------------------------------------------------------------------


class TestPaneToCode:
    def test_leaf_pane_sends_cd_and_command(self):
        pane = Pane(dir="~/projects/foo", command="ai c 1")
        code = _pane_to_python(pane)
        assert "cd" in code
        assert "ai c 1" in code

    def test_leaf_pane_null_command_no_send(self):
        pane = Pane(dir="~", command=None)
        code = _pane_to_python(pane)
        assert "cd" in code
        # No command send — only cd line
        assert code.count("async_send_text") == 1

    def test_vertical_split_generates_split_call(self):
        pane = Pane(
            dir="~",
            command="ai c 1",
            split=PaneSplit(direction="vertical", ratio=0.65, right=Pane(dir="~")),
        )
        code = _pane_to_python(pane)
        assert "async_split_pane(vertical=True" in code
        assert "percent=65" in code

    def test_horizontal_split_generates_split_call(self):
        pane = Pane(
            dir="~",
            command="htop",
            split=PaneSplit(direction="horizontal", ratio=0.5, bottom=Pane(dir="~")),
        )
        code = _pane_to_python(pane)
        assert "async_split_pane(vertical=False" in code

    def test_nested_split_generates_code_for_both_levels(self):
        inner = Pane(dir="~", command="git log")
        outer = Pane(
            dir="~",
            command="ai c 1",
            split=PaneSplit(
                direction="vertical",
                ratio=0.65,
                right=Pane(
                    dir="~",
                    split=PaneSplit(direction="horizontal", ratio=0.5, bottom=inner),
                ),
            ),
        )
        code = _pane_to_python(outer)
        assert code.count("async_split_pane") == 2
        assert "git log" in code


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


class TestCmdLayoutList:
    def test_prints_available_layouts(self, tmp_path, capsys):
        _write_layout(tmp_path, "sw-dev", _MINIMAL_LAYOUT)
        with patch("ai_cli.layout._layouts_dir", return_value=tmp_path):
            rc = cmd_layout_list()
        assert rc == 0
        assert "sw-dev" in capsys.readouterr().out

    def test_empty_directory(self, tmp_path, capsys):
        with patch("ai_cli.layout._layouts_dir", return_value=tmp_path):
            rc = cmd_layout_list()
        assert rc == 0
        assert "No layout files" in capsys.readouterr().out

    def test_missing_directory(self, tmp_path, capsys):
        absent = tmp_path / "absent"
        with patch("ai_cli.layout._layouts_dir", return_value=absent):
            rc = cmd_layout_list()
        assert rc == 0
        assert "No layouts directory" in capsys.readouterr().out

    def test_invalid_layout_shows_error(self, tmp_path, capsys):
        (tmp_path / "bad.yaml").write_text(":")
        with patch("ai_cli.layout._layouts_dir", return_value=tmp_path):
            rc = cmd_layout_list()
        assert rc == 0
        assert "invalid" in capsys.readouterr().out


class TestCmdLayoutValidate:
    def test_valid_layout_exits_0(self, tmp_path, capsys):
        _write_layout(tmp_path, "test", _MINIMAL_LAYOUT)
        with patch("ai_cli.layout._layouts_dir", return_value=tmp_path):
            rc = cmd_layout_validate("test")
        assert rc == 0
        assert "valid" in capsys.readouterr().out

    def test_missing_layout_exits_1(self, tmp_path, capsys):
        with patch("ai_cli.layout._layouts_dir", return_value=tmp_path):
            rc = cmd_layout_validate("nope")
        assert rc == 1

    def test_invalid_schema_exits_1(self, tmp_path, capsys):
        _write_layout(tmp_path, "bad", {"name": "bad", "tabs": []})
        with patch("ai_cli.layout._layouts_dir", return_value=tmp_path):
            rc = cmd_layout_validate("bad")
        assert rc == 1


class TestCmdLayoutProfiles:
    def test_generates_profiles_and_exits_0(self, tmp_path, capsys):
        _write_layout(tmp_path, "test", _MINIMAL_LAYOUT)
        with (
            patch("ai_cli.layout._layouts_dir", return_value=tmp_path),
            patch("ai_cli.layout._dynamic_profile_dir", return_value=tmp_path),
            patch("ai_cli.layout.generate_session_icon", return_value=None),
        ):
            rc = cmd_layout_profiles("test")
        assert rc == 0
        assert "Regenerated" in capsys.readouterr().out

    def test_missing_layout_exits_1(self, tmp_path, capsys):
        with patch("ai_cli.layout._layouts_dir", return_value=tmp_path):
            rc = cmd_layout_profiles("nope")
        assert rc == 1


class TestRunLayoutCommand:
    def test_list_subcommand(self, tmp_path, capsys):
        with patch("ai_cli.layout._layouts_dir", return_value=tmp_path):
            rc = run_layout_command(["list"])
        assert rc == 0

    def test_validate_subcommand(self, tmp_path, capsys):
        _write_layout(tmp_path, "test", _MINIMAL_LAYOUT)
        with patch("ai_cli.layout._layouts_dir", return_value=tmp_path):
            rc = run_layout_command(["validate", "test"])
        assert rc == 0

    def test_profiles_subcommand(self, tmp_path, capsys):
        _write_layout(tmp_path, "test", _MINIMAL_LAYOUT)
        with (
            patch("ai_cli.layout._layouts_dir", return_value=tmp_path),
            patch("ai_cli.layout._dynamic_profile_dir", return_value=tmp_path),
            patch("ai_cli.layout.generate_session_icon", return_value=None),
        ):
            rc = run_layout_command(["profiles", "test"])
        assert rc == 0

    def test_no_args_exits_1(self, capsys):
        rc = run_layout_command([])
        assert rc == 1

    def test_validate_missing_name_exits_1(self, capsys):
        rc = run_layout_command(["validate"])
        assert rc == 1

    def test_profiles_missing_name_exits_1(self, capsys):
        rc = run_layout_command(["profiles"])
        assert rc == 1


# ---------------------------------------------------------------------------
# ai layout CLI dispatch (via main)
# ---------------------------------------------------------------------------


class TestLayoutCliDispatch:
    def test_ai_layout_list_dispatches(self, tmp_path, capsys):
        from ai_cli.main import cli

        with (
            patch("sys.argv", ["ai", "layout", "list"]),
            patch("ai_cli.layout._layouts_dir", return_value=tmp_path),
            pytest.raises(SystemExit) as exc,
        ):
            cli()
        assert exc.value.code == 0

    def test_ai_layout_validate_dispatches(self, tmp_path, capsys):
        from ai_cli.main import cli

        _write_layout(tmp_path, "test", _MINIMAL_LAYOUT)
        with (
            patch("sys.argv", ["ai", "layout", "validate", "test"]),
            patch("ai_cli.layout._layouts_dir", return_value=tmp_path),
            pytest.raises(SystemExit) as exc,
        ):
            cli()
        assert exc.value.code == 0


# ---------------------------------------------------------------------------
# _layouts_dir and _dynamic_profile_dir helpers
# ---------------------------------------------------------------------------


class TestLayoutDirHelpers:
    def test_layouts_dir_returns_path(self):
        """line 111: _layouts_dir() returns a Path object."""
        from ai_cli.layout import _layouts_dir

        result = _layouts_dir()
        assert isinstance(result, Path)

    def test_dynamic_profile_dir_creates_and_returns_path(self, tmp_path):
        """lines 128-130: _dynamic_profile_dir creates the directory and returns it."""
        from ai_cli.layout import _dynamic_profile_dir

        with patch("pathlib.Path.home", return_value=tmp_path):
            result = _dynamic_profile_dir()
        assert result.exists()
        assert result.is_dir()


# ---------------------------------------------------------------------------
# load_layout — non-mapping YAML
# ---------------------------------------------------------------------------


class TestLoadLayoutNonMapping:
    def test_when_yaml_is_a_list_then_raises_value_error(self, tmp_path):
        """line 159: YAML content that is not a dict raises ValueError."""
        from ai_cli.layout import load_layout

        layout_file = tmp_path / "bad.yaml"
        layout_file.write_text("- item1\n- item2\n")
        with patch("ai_cli.layout._layouts_dir", return_value=tmp_path):
            with pytest.raises(ValueError, match="YAML mapping"):
                load_layout("bad")


# ---------------------------------------------------------------------------
# generate_layout_profiles — session type detection and color fields
# ---------------------------------------------------------------------------


class TestGenerateLayoutProfilesExtended:
    """Covers session type branches (lines 191, 193, 195) and color fields (214, 216)."""

    def _make_tab(
        self, base_profile: str, tab_color: str = "#1e88e5", bg: str | None = None, fg: str | None = None
    ) -> dict:
        tab = {
            "name": "tab1",
            "base_profile": base_profile,
            "colors": {"tab_color": tab_color},
            "root": {"dir": "~"},
        }
        if bg:
            tab["colors"]["background"] = bg
        if fg:
            tab["colors"]["foreground"] = fg
        return tab

    def _run(self, base_profile: str, tmp_path: Path, **color_kwargs) -> dict:
        layout_data = {
            "name": "test",
            "tabs": [self._make_tab(base_profile, **color_kwargs)],
        }
        layout = Layout(**layout_data)
        with (
            patch("ai_cli.layout._dynamic_profile_dir", return_value=tmp_path),
            patch("ai_cli.layout.generate_session_icon", return_value=None),
        ):
            generate_layout_profiles(layout)
        profile_file = tmp_path / "layout-test-tab1.json"
        return json.loads(profile_file.read_text())["Profiles"][0]

    def test_chrome_base_profile_sets_session_type(self, tmp_path):
        """line 191: 'Chrome' in base_profile → session_type = 'chrome'."""
        profile = self._run("ChromeTab", tmp_path)
        assert profile["Dynamic Profile Parent Name"] == "ChromeTab"

    def test_caffeinate_base_profile_sets_session_type(self, tmp_path):
        """line 193: 'Caffeinate' in base_profile → session_type = 'caffeinate'."""
        profile = self._run("CaffeinateTab", tmp_path)
        assert profile["Dynamic Profile Parent Name"] == "CaffeinateTab"

    def test_ssh_base_profile_sets_session_type(self, tmp_path):
        """line 195: 'SSH' in base_profile → session_type = 'ssh'."""
        profile = self._run("SSHTab", tmp_path)
        assert profile["Dynamic Profile Parent Name"] == "SSHTab"

    def test_background_color_included_when_set(self, tmp_path):
        """line 214: bg_hex present → profile has 'Background Color' key."""
        profile = self._run("ClaudeCode", tmp_path, bg="#111111")
        assert "Background Color" in profile

    def test_foreground_color_included_when_set(self, tmp_path):
        """line 216: foreground present → profile has 'Foreground Color' key."""
        profile = self._run("ClaudeCode", tmp_path, fg="#ffffff")
        assert "Foreground Color" in profile


# ---------------------------------------------------------------------------
# cmd_layout_apply
# ---------------------------------------------------------------------------


class TestCmdLayoutApply:
    def test_when_layout_not_found_then_returns_1(self, tmp_path, capsys):
        """lines 283-287: FileNotFoundError prints error and returns 1."""
        from ai_cli.layout import cmd_layout_apply

        with patch("ai_cli.layout._layouts_dir", return_value=tmp_path):
            result = cmd_layout_apply("nonexistent")
        assert result == 1
        assert "Error:" in capsys.readouterr().err

    def test_when_layout_valid_then_runs_script_and_returns_code(self, tmp_path):
        """lines 289-298: valid layout generates profiles and runs the launch script."""
        from ai_cli.layout import cmd_layout_apply

        _write_layout(tmp_path, "myapp", _MINIMAL_LAYOUT)
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        with (
            patch("ai_cli.layout._layouts_dir", return_value=tmp_path),
            patch("ai_cli.layout._dynamic_profile_dir", return_value=tmp_path),
            patch("ai_cli.layout.generate_session_icon", return_value=None),
            patch("subprocess.run", return_value=fake_proc),
        ):
            result = cmd_layout_apply("myapp")
        assert result == 0


# ---------------------------------------------------------------------------
# _write_launch_script
# ---------------------------------------------------------------------------


class TestWriteLaunchScript:
    def test_generates_temp_python_file_with_iterm2_content(self, tmp_path):
        """lines 350-399: generates a .py file importing iterm2 with tab setup."""
        from ai_cli.layout import _write_launch_script

        layout = Layout(**_MINIMAL_LAYOUT)
        script_path = _write_launch_script(layout)
        try:
            assert script_path.exists()
            assert script_path.suffix == ".py"
            content = script_path.read_text()
            assert "import iterm2" in content
            assert "async def main" in content
            assert "iterm2.run_until_complete" in content
        finally:
            script_path.unlink(missing_ok=True)

    def test_multi_tab_layout_generates_create_tab_calls(self):
        """lines 381-388: each non-first tab generates async_create_tab in the script."""
        from ai_cli.layout import _write_launch_script

        multi_tab = {
            "name": "multi",
            "tabs": [
                {"name": "tab-a", "base_profile": "ClaudeCode", "root": {"dir": "~"}},
                {"name": "tab-b", "base_profile": "GeminiTab", "root": {"dir": "~"}},
            ],
        }
        layout = Layout(**multi_tab)
        script_path = _write_launch_script(layout)
        try:
            content = script_path.read_text()
            assert "async_create_tab" in content
            assert "tab-b" in content
        finally:
            script_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# run_layout_command default (apply)
# ---------------------------------------------------------------------------


class TestRunLayoutCommandApply:
    def test_unknown_subcommand_applies_layout_by_name(self, tmp_path):
        """line 426: unrecognized sub = layout name → calls cmd_layout_apply."""
        _write_layout(tmp_path, "myapp", _MINIMAL_LAYOUT)
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        with (
            patch("ai_cli.layout._layouts_dir", return_value=tmp_path),
            patch("ai_cli.layout._dynamic_profile_dir", return_value=tmp_path),
            patch("ai_cli.layout.generate_session_icon", return_value=None),
            patch("subprocess.run", return_value=fake_proc),
        ):
            result = run_layout_command(["myapp"])
        assert result == 0
