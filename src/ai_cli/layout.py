"""iTerm2 YAML layout templating system.

Defines the YAML schema, validates layout files, generates Dynamic Profiles
(including tinted icons via icon_generator), and builds iTerm2 windows via
the iTerm2 Python API.

CLI surface: `ai layout <name|list|validate|profiles>`
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

import yaml

try:
    from pydantic import BaseModel, Field, field_validator, model_validator

    _PYDANTIC_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PYDANTIC_AVAILABLE = False

from .icon_generator import (
    generate_session_icon,
)

# ── Constants ──────────────────────────────────────────────────────────────────

_LAYOUTS_DIR = Path.home() / ".config" / "iterm2" / "layouts"
_DYNAMIC_PROFILE_SUBDIR = "ai-cli-generated"


# ── Pydantic schema ────────────────────────────────────────────────────────────


class PaneSplit(BaseModel):
    direction: str  # "horizontal" | "vertical"
    ratio: float = Field(default=0.5, ge=0.0, le=1.0)
    # Populated by model_validator after init
    right: Optional["Pane"] = None
    bottom: Optional["Pane"] = None

    @field_validator("direction")
    @classmethod
    def direction_valid(cls, v: str) -> str:
        if v not in ("horizontal", "vertical"):
            raise ValueError("direction must be 'horizontal' or 'vertical'")
        return v

    @model_validator(mode="after")
    def child_required(self) -> "PaneSplit":
        if self.direction == "vertical" and self.right is None:
            raise ValueError("vertical split requires 'right' child pane")
        if self.direction == "horizontal" and self.bottom is None:
            raise ValueError("horizontal split requires 'bottom' child pane")
        return self


class Pane(BaseModel):
    dir: str = "~"
    command: Optional[str] = None
    split: Optional[PaneSplit] = None


# Update forward refs
PaneSplit.model_rebuild()


class TabColors(BaseModel):
    background: Optional[str] = None
    foreground: Optional[str] = None
    tab_color: Optional[str] = None
    icon_color: Optional[str] = None  # explicit icon tint override


class Tab(BaseModel):
    name: str
    base_profile: str = "ClaudeCode"
    colors: TabColors = Field(default_factory=TabColors)
    root: Pane

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("tab name must not be empty")
        return v


class Layout(BaseModel):
    name: str
    description: str = ""
    tabs: list[Tab]

    @field_validator("tabs")
    @classmethod
    def at_least_one_tab(cls, v: list) -> list:
        if not v:
            raise ValueError("layout must have at least one tab")
        return v


# ── Helpers ────────────────────────────────────────────────────────────────────


def _layouts_dir() -> Path:
    return _LAYOUTS_DIR


def _layout_path(name: str) -> Path:
    return _layouts_dir() / f"{name}.yaml"


def _layout_profile_name(layout_name: str, tab_name: str) -> str:
    """Deterministic Dynamic Profile name for a layout tab."""
    return f"ai-cli-layout:{layout_name}:{tab_name}"


def _layout_profile_guid(layout_name: str, tab_name: str) -> str:
    return f"ai-cli-layout-{layout_name}-{tab_name}"


def _dynamic_profile_dir() -> Path:
    d = Path.home() / "Library" / "Application Support" / "iTerm2" / "DynamicProfiles" / _DYNAMIC_PROFILE_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _hex_to_iterm2_color(hex_color: str) -> dict:
    from .icon_generator import _hex_to_rgb

    r, g, b = _hex_to_rgb(hex_color)
    return {
        "Red Component": round(r / 255, 6),
        "Green Component": round(g / 255, 6),
        "Blue Component": round(b / 255, 6),
        "Alpha Component": 1.0,
        "Color Space": "sRGB",
    }


# ── Layout file loading ────────────────────────────────────────────────────────


def load_layout(name: str) -> Layout:
    """Load and validate a layout YAML file by name. Raises ValueError on errors."""
    path = _layout_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Layout file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in {path}: {e}") from e
    if not isinstance(raw, dict):
        raise ValueError(f"Layout file must be a YAML mapping, got {type(raw).__name__}")
    if "name" not in raw:
        raw["name"] = name
    try:
        return Layout(**raw)
    except Exception as e:
        raise ValueError(f"Schema validation failed for '{name}': {e}") from e


# ── Dynamic Profile generation ─────────────────────────────────────────────────


def generate_layout_profiles(layout: Layout) -> list[Path]:
    """Generate Dynamic Profile JSON files for all tabs in the layout.

    Returns list of generated profile paths.
    """
    generated = []
    icon_cache_dir = Path.home() / ".local" / "state" / "ai-cli-utils" / "iterm2-icons"
    icon_cache_dir.mkdir(parents=True, exist_ok=True)

    for tab in layout.tabs:
        tab_hex = tab.colors.tab_color or "#1e88e5"
        bg_hex = tab.colors.background

        # Determine session type from base_profile convention
        session_type = "cc"
        if "Gemini" in tab.base_profile:
            session_type = "gemini"
        elif "Shell" in tab.base_profile:
            session_type = "shell"
        elif "Chrome" in tab.base_profile:
            session_type = "chrome"
        elif "Caffeinate" in tab.base_profile:
            session_type = "caffeinate"
        elif "SSH" in tab.base_profile:
            session_type = "ssh"

        # Generate tinted icon
        icon_color = tab.colors.icon_color or None
        session_key = f"layout-{layout.name}-{tab.name}"
        icon_path = generate_session_icon(session_key, tab_hex, session_type, icon_color)

        # Build Dynamic Profile
        profile_name = _layout_profile_name(layout.name, tab.name)
        guid = _layout_profile_guid(layout.name, tab.name)

        profile: dict = {
            "Name": profile_name,
            "Guid": guid,
            "Dynamic Profile Parent Name": tab.base_profile,
            "Tab Color": _hex_to_iterm2_color(tab_hex),
            "Use Tab Color": True,
            "Title Components": 1,  # 1 = session name only ("Name" dropdown option)
        }
        if bg_hex:
            profile["Background Color"] = _hex_to_iterm2_color(bg_hex)
        if tab.colors.foreground:
            profile["Foreground Color"] = _hex_to_iterm2_color(tab.colors.foreground)
        if icon_path and icon_path.exists():
            profile["Custom Icon Path"] = str(icon_path)

        data = {"Profiles": [profile]}
        out = _dynamic_profile_dir() / f"layout-{layout.name}-{tab.name}.json"
        out.write_text(json.dumps(data, indent=2))
        generated.append(out)

    return generated


# ── CLI handlers ───────────────────────────────────────────────────────────────


def cmd_layout_list() -> int:
    """List available layout files."""
    d = _layouts_dir()
    if not d.exists():
        print(f"No layouts directory found at {d}")
        return 0
    files = sorted(d.glob("*.yaml"))
    if not files:
        print(f"No layout files found in {d}")
        return 0
    for f in files:
        name = f.stem
        try:
            layout = load_layout(name)
            tab_names = ", ".join(t.name for t in layout.tabs)
            desc = f" — {layout.description}" if layout.description else ""
            print(f"  {name}{desc} [{tab_names}]")
        except Exception as e:
            print(f"  {name} (invalid: {e})")
    return 0


def cmd_layout_validate(name: str) -> int:
    """Validate a layout YAML file without applying it."""
    try:
        layout = load_layout(name)
        print(f"✓ '{name}' is valid — {len(layout.tabs)} tab(s)")
        return 0
    except FileNotFoundError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1


def cmd_layout_profiles(name: str) -> int:
    """Regenerate Dynamic Profile JSON files for a layout without rebuilding the window."""
    try:
        layout = load_layout(name)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    generated = generate_layout_profiles(layout)
    for p in generated:
        print(f"  Generated: {p}")
    print(f"Regenerated {len(generated)} profile(s) for '{name}'")
    return 0


def cmd_layout_apply(name: str) -> int:
    """Apply a layout: generate profiles then launch via iTerm2 Python API script."""
    try:
        layout = load_layout(name)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Generate profiles first (hot-reloaded by iTerm2 before script runs)
    generate_layout_profiles(layout)

    # Write and run the Python API launch script
    script_path = _write_launch_script(layout)
    result = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=False,
    )
    return result.returncode


def _expand_dir(d: str) -> str:
    """Expand ~ in a directory path."""
    return str(Path(d).expanduser())


def _pane_to_python(pane: Pane, indent: int = 0) -> str:
    """Recursively generate Python API code to build a pane tree."""
    pad = "    " * indent
    lines = []

    if pane.split is None:
        # Leaf pane: send cd + command
        cd = _expand_dir(pane.dir)
        lines.append(f"{pad}await session.async_send_text('cd {cd}\\n')")
        if pane.command:
            cmd = pane.command.replace("'", "\\'")
            lines.append(f"{pad}await session.async_send_text('{cmd}\\n')")
    else:
        s = pane.split
        vertical = s.direction == "vertical"
        percent = int(s.ratio * 100)
        if vertical:
            lines.append(f"{pad}right = await session.async_split_pane(vertical=True, percent={percent})")
            # Left pane (current session) — leaf
            cd = _expand_dir(pane.dir)
            lines.append(f"{pad}await session.async_send_text('cd {cd}\\n')")
            if pane.command:
                cmd = pane.command.replace("'", "\\'")
                lines.append(f"{pad}await session.async_send_text('{cmd}\\n')")
            # Right pane — recurse
            lines.append(f"{pad}session = right")
            if s.right:
                lines.extend(_pane_to_python(s.right, indent).splitlines())
        else:
            lines.append(f"{pad}bottom = await session.async_split_pane(vertical=False, percent={percent})")
            cd = _expand_dir(pane.dir)
            lines.append(f"{pad}await session.async_send_text('cd {cd}\\n')")
            if pane.command:
                cmd = pane.command.replace("'", "\\'")
                lines.append(f"{pad}await session.async_send_text('{cmd}\\n')")
            lines.append(f"{pad}session = bottom")
            if s.bottom:
                lines.extend(_pane_to_python(s.bottom, indent).splitlines())

    return "\n".join(lines)


def _write_launch_script(layout: Layout) -> Path:
    """Write a temporary iTerm2 Python API script and return its path."""
    import tempfile

    lines = [
        "import asyncio",
        "import iterm2",
        "",
        "async def main(connection):",
        "    app = await iterm2.async_get_app(connection)",
        "    window = await iterm2.Window.async_create(connection)",
        "    if window is None:",
        "        return",
        "    first_tab = True",
    ]

    for idx, tab in enumerate(layout.tabs):
        tab_name_esc = tab.name.replace('"', '\\"')

        if idx == 0:
            # First tab already exists when window is created
            lines += [
                f"    # Tab: {tab.name}",
                "    tab = window.current_tab",
                "    session = tab.current_session",
                f'    await session.async_set_profile_property("Name", "{tab_name_esc}")',
            ]
        lines += [
            f'    await iterm2.Tab.async_invoke_function(tab, "iterm2.set_tab_title", title="{tab_name_esc}")',
        ]
        lines.extend(_pane_to_python(tab.root, indent=1).splitlines())
        # Create subsequent tabs
        if idx < len(layout.tabs) - 1:
            next_tab = layout.tabs[idx + 1]
            next_profile = _layout_profile_name(layout.name, next_tab.name)
            next_name_esc = next_tab.name.replace('"', '\\"')
            lines += [
                f'    tab = await window.async_create_tab(profile="{next_profile}")',
                "    session = tab.current_session",
            ]
            del next_tab, next_profile, next_name_esc

    lines += [
        "",
        "iterm2.run_until_complete(main)",
    ]

    script_content = "\n".join(lines) + "\n"
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py", prefix="ai-layout-", delete=False)
    tmp.write(script_content)
    tmp.close()
    return Path(tmp.name)


def run_layout_command(args: list[str]) -> int:
    """Dispatch `ai layout` subcommands. args = sys.argv[2:]"""
    if not args:
        print("Usage: ai layout <name|list|validate <name>|profiles <name>>", file=sys.stderr)
        return 1

    sub = args[0]

    if sub in ("-h", "--help"):
        print("Usage: ai layout <name|list|validate <name>|profiles <name>>", file=sys.stderr)
        print("", file=sys.stderr)
        print("  list              List available layouts", file=sys.stderr)
        print("  validate <name>   Validate layout YAML schema", file=sys.stderr)
        print("  profiles <name>   Regenerate Dynamic Profiles without relaunching", file=sys.stderr)
        print("  <name>            Apply a layout", file=sys.stderr)
        return 0

    if sub == "list":
        return cmd_layout_list()

    if sub == "validate":
        if len(args) < 2:
            print("Usage: ai layout validate <name>", file=sys.stderr)
            return 1
        return cmd_layout_validate(args[1])

    if sub == "profiles":
        if len(args) < 2:
            print("Usage: ai layout profiles <name>", file=sys.stderr)
            return 1
        return cmd_layout_profiles(args[1])

    # Default: apply layout by name
    return cmd_layout_apply(sub)
