"""Runtime iTerm2 icon generation and Dynamic Profile management.

Generates tinted PNG icons from neutral base logos using Pillow.
Icon tint is computed automatically from the tab background color via HSL
color theory (complementary hue + contrast boost). A Dynamic Profile JSON
is written per session so iTerm2 hot-reloads the profile without a restart.

Every profile type (Claude, Gemini, shell, caffeinate, Chrome, SSH) uses
the same pipeline. There are no pre-baked icon variants.
"""

from __future__ import annotations

import colorsys
import json
from pathlib import Path
from typing import Optional

from .config import get_xdg_state_home

try:
    from PIL import Image

    _PILLOW_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PILLOW_AVAILABLE = False


# ── Constants ──────────────────────────────────────────────────────────────────

_ICON_SIZE = 128
_DYNAMIC_PROFILE_PREFIX = "ai-cli-session-"

_BASE_PROFILES: dict[str, str] = {
    "cc": "Default",
    "gemini": "Default",
    "shell": "Default",
    "chrome": "Default",
    "caffeinate": "Default",
    "ssh": "Default",
}

# Default icon tint when no tab color is assigned (session has no color set).
_CLAUDE_BRAND_ORANGE = "#da7756"

# Source logo filenames under src/ai_cli/data/icons/
_SOURCE_LOGOS: dict[str, str] = {
    "cc": "claude-logo.png",
    "gemini": "gemini-logo.png",
    "shell": "shell-logo.png",
    "chrome": "chrome-logo.png",
    "caffeinate": "caffeinate-logo.png",
    "ssh": "ssh-logo.png",
}


# ── Path helpers ───────────────────────────────────────────────────────────────


def _icon_cache_dir() -> Path:
    """~/.local/state/ai-cli-utils/iterm2-icons/"""
    d = get_xdg_state_home() / "iterm2-icons"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _dynamic_profile_dir() -> Path:
    """~/Library/Application Support/iTerm2/DynamicProfiles/"""
    d = Path.home() / "Library" / "Application Support" / "iTerm2" / "DynamicProfiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _source_logo_path(session_type: str) -> Optional[Path]:
    """Return absolute path to the source logo for session_type, or None."""
    filename = _SOURCE_LOGOS.get(session_type)
    if not filename:
        return None
    data_dir = Path(__file__).parent / "data" / "icons"
    p = data_dir / filename
    return p if p.exists() else None


# ── Color math ─────────────────────────────────────────────────────────────────


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = h[0] * 2 + h[1] * 2 + h[2] * 2
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def compute_contrast_tint(tab_hex: str) -> str:
    """Compute a contrasting icon tint from a tab background hex color.

    Strategy:
    - Rotate hue 180° (complementary) for maximum hue contrast.
    - Adjust lightness so the tint is clearly visible against the background:
      dark background → bright tint (L ≈ 0.80–0.90)
      light background → dark tint (L ≈ 0.15–0.25)
    - Boost saturation for visual punch.
    """
    r, g, b = _hex_to_rgb(tab_hex)
    # colorsys uses HLS ordering: h, lightness, saturation
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)

    h_tint = (h + 0.5) % 1.0  # complementary hue

    if l < 0.5:  # dark background → bright tint
        l_tint = min(0.90, 0.70 + s * 0.20)
    else:  # light background → dark tint
        l_tint = max(0.10, 0.25 - s * 0.10)

    s_tint = min(0.95, s * 1.4 + 0.35)

    r_t, g_t, b_t = colorsys.hls_to_rgb(h_tint, l_tint, s_tint)
    return _rgb_to_hex(int(r_t * 255), int(g_t * 255), int(b_t * 255))


def _hex_to_iterm2_color(hex_color: str) -> dict:
    r, g, b = _hex_to_rgb(hex_color)
    return {
        "Red Component": round(r / 255, 6),
        "Green Component": round(g / 255, 6),
        "Blue Component": round(b / 255, 6),
        "Alpha Component": 1.0,
        "Color Space": "sRGB",
    }


# ── Icon generation ────────────────────────────────────────────────────────────


def _tint_image(img: "Image.Image", tint_hex: str) -> "Image.Image":
    """Tint an RGBA image: replaces all pixels with tint color, preserving alpha."""
    r, g, b = _hex_to_rgb(tint_hex)
    img_rgba = img.convert("RGBA")
    tint_layer = Image.new("RGBA", img_rgba.size, (r, g, b, 255))
    result = Image.new("RGBA", img_rgba.size, (0, 0, 0, 0))
    _, _, _, alpha = img_rgba.split()
    result.paste(tint_layer, mask=alpha)
    return result


def generate_session_icon(
    session_name: str,
    tab_hex: Optional[str],
    session_type: str,
    icon_color: Optional[str] = None,
) -> Optional[Path]:
    """Generate a tinted PNG icon for this session and write it to the cache.

    Args:
        session_name: e.g. "c-sw-5"
        tab_hex: tab background color, e.g. "#5e35b1"
        session_type: "cc", "gemini", "shell", "chrome", "caffeinate", "ssh"
        icon_color: explicit tint override; auto-derived from tab_hex if None

    Returns path to the written PNG, or None if Pillow is unavailable.
    """
    if not _PILLOW_AVAILABLE:
        return None

    source = _source_logo_path(session_type)
    if not source:
        return None  # No source logo — parent profile's icon will show instead

    tint = icon_color or (compute_contrast_tint(tab_hex) if tab_hex else _CLAUDE_BRAND_ORANGE)

    base_img = Image.open(source).convert("RGBA")
    if base_img.size != (_ICON_SIZE, _ICON_SIZE):
        base_img = base_img.resize((_ICON_SIZE, _ICON_SIZE), Image.Resampling.LANCZOS)

    tinted = _tint_image(base_img, tint)
    out_path = _icon_cache_dir() / f"{session_name}.png"
    tinted.save(out_path, "PNG")
    return out_path


# ── Dynamic Profile ────────────────────────────────────────────────────────────


def generate_dynamic_profile(
    session_name: str,
    tab_hex: str,
    session_type: str,
    icon_path: Optional[Path] = None,
    background_hex: Optional[str] = None,
    base_profile: Optional[str] = None,
) -> Path:
    """Write a Dynamic Profile JSON for this session. Returns the path.

    The profile GUID is deterministic (ai-cli-{session_name}) so re-running
    is idempotent — iTerm2 updates the existing entry, not accumulates.

    Tab color, icon, and title component are set explicitly. Title Components: 1
    = session name only, corresponding to the "Name" dropdown in iTerm2's
    Edit Session > General > Session Title.
    """
    resolved_base = base_profile or _BASE_PROFILES.get(session_type, "Default")

    profile: dict = {
        "Name": f"ai-cli:{session_name}",
        "Guid": f"ai-cli-{session_name}",
        "Dynamic Profile Parent Name": resolved_base,
        "Tab Color": _hex_to_iterm2_color(tab_hex),
        "Use Tab Color": True,
        "Use Separate Colors for Light and Dark Mode": True,
        "Icon": 2,  # 2 = custom icon; must be explicit — not reliably inherited
        "Title Components": 1,  # 1 = session name only ("Name" dropdown option)
    }
    # Inject Shift+Enter → CSI u escape sequence automatically so CC sessions
    # handle newline-without-submit without any manual iTerm2 configuration.
    profile["Key Mappings"] = {
        "0xd-0x20000-0x24": {
            "Version": 2,
            "Apply Mode": 0,
            "Action": 10,
            "Text": "[13;2u",
            "Escaping": 2,
        }
    }
    if background_hex:
        profile["Background Color"] = _hex_to_iterm2_color(background_hex)
    if icon_path and icon_path.exists():
        profile["Custom Icon Path"] = str(icon_path)

    data = {"Profiles": [profile]}
    out_path = _dynamic_profile_dir() / f"{_DYNAMIC_PROFILE_PREFIX}{session_name}.json"
    out_path.write_text(json.dumps(data, indent=2))
    return out_path


def cleanup_session_files(session_name: str) -> None:
    """Remove cached icon PNG and Dynamic Profile JSON for this session.

    Called from the EXIT trap. Silently ignores missing files.
    """
    try:
        (_icon_cache_dir() / f"{session_name}.png").unlink(missing_ok=True)
    except Exception:
        pass
    try:
        (_dynamic_profile_dir() / f"{_DYNAMIC_PROFILE_PREFIX}{session_name}.json").unlink(missing_ok=True)
    except Exception:
        pass
