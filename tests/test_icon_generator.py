"""Tests for ai_cli.icon_generator — runtime Pillow icon generation + Dynamic Profile."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_cli.icon_generator import (
    _hex_to_iterm2_color,
    _hex_to_rgb,
    _rgb_to_hex,
    cleanup_session_files,
    compute_contrast_tint,
    generate_dynamic_profile,
    generate_session_icon,
)


# ---------------------------------------------------------------------------
# Color math
# ---------------------------------------------------------------------------


class TestHexToRgb:
    def test_six_digit_hex(self):
        assert _hex_to_rgb("#e74c3c") == (231, 76, 60)

    def test_without_hash(self):
        assert _hex_to_rgb("e74c3c") == (231, 76, 60)

    def test_three_digit_expands(self):
        assert _hex_to_rgb("#fff") == (255, 255, 255)

    def test_black(self):
        assert _hex_to_rgb("#000000") == (0, 0, 0)

    def test_white(self):
        assert _hex_to_rgb("#ffffff") == (255, 255, 255)


class TestRgbToHex:
    def test_produces_lowercase_hash_string(self):
        assert _rgb_to_hex(231, 76, 60) == "#e74c3c"

    def test_black(self):
        assert _rgb_to_hex(0, 0, 0) == "#000000"

    def test_white(self):
        assert _rgb_to_hex(255, 255, 255) == "#ffffff"


class TestComputeContrastTint:
    def test_returns_hex_string(self):
        tint = compute_contrast_tint("#5e35b1")
        assert tint.startswith("#")
        assert len(tint) == 7

    def test_dark_background_gives_bright_tint(self):
        tint = compute_contrast_tint("#0d0d1f")  # very dark
        r, g, b = _hex_to_rgb(tint)
        # Bright tint should have high average luminance
        avg = (r + g + b) / 3
        assert avg > 150

    def test_light_background_gives_dark_tint(self):
        tint = compute_contrast_tint("#f5f5f5")  # very light
        r, g, b = _hex_to_rgb(tint)
        avg = (r + g + b) / 3
        assert avg < 100

    def test_complementary_hue_differs_from_input(self):
        # Purple tab → tint should not be purple (should be greenish)
        tint = compute_contrast_tint("#5e35b1")  # purple
        assert tint != "#5e35b1"

    def test_deterministic(self):
        assert compute_contrast_tint("#e74c3c") == compute_contrast_tint("#e74c3c")

    def test_works_without_hash_prefix(self):
        tint = compute_contrast_tint("5e35b1")
        assert tint.startswith("#") and len(tint) == 7


class TestHexToIterm2Color:
    def test_red_component(self):
        result = _hex_to_iterm2_color("#ff0000")
        assert result["Red Component"] == pytest.approx(1.0, abs=0.01)
        assert result["Green Component"] == pytest.approx(0.0, abs=0.01)
        assert result["Blue Component"] == pytest.approx(0.0, abs=0.01)

    def test_alpha_always_1(self):
        assert _hex_to_iterm2_color("#aabbcc")["Alpha Component"] == 1.0

    def test_color_space_srgb(self):
        assert _hex_to_iterm2_color("#aabbcc")["Color Space"] == "sRGB"

    def test_components_in_0_1_range(self):
        result = _hex_to_iterm2_color("#4080c0")
        for key in ("Red Component", "Green Component", "Blue Component"):
            assert 0.0 <= result[key] <= 1.0


# ---------------------------------------------------------------------------
# Dynamic Profile generation
# ---------------------------------------------------------------------------


class TestGenerateDynamicProfile:
    def test_creates_json_file(self, tmp_path):
        with patch("ai_cli.icon_generator._dynamic_profile_dir", return_value=tmp_path):
            out = generate_dynamic_profile("test-session", "#5e35b1", "cc")
        assert out.exists()
        assert out.suffix == ".json"
        assert out.name == "ai-cli-session-test-session.json"

    def test_profile_name_is_ai_cli_session(self, tmp_path):
        with patch("ai_cli.icon_generator._dynamic_profile_dir", return_value=tmp_path):
            out = generate_dynamic_profile("test-session", "#5e35b1", "cc")
        data = json.loads(out.read_text())
        assert data["Profiles"][0]["Name"] == "ai-cli:test-session"

    def test_guid_is_deterministic(self, tmp_path):
        with patch("ai_cli.icon_generator._dynamic_profile_dir", return_value=tmp_path):
            out1 = generate_dynamic_profile("test-session", "#5e35b1", "cc")
            out2 = generate_dynamic_profile("test-session", "#5e35b1", "cc")
        d1 = json.loads(out1.read_text())
        d2 = json.loads(out2.read_text())
        assert d1["Profiles"][0]["Guid"] == d2["Profiles"][0]["Guid"] == "ai-cli-test-session"

    def test_inherits_from_claudecode_for_cc(self, tmp_path):
        with patch("ai_cli.icon_generator._dynamic_profile_dir", return_value=tmp_path):
            out = generate_dynamic_profile("test-session", "#5e35b1", "cc")
        data = json.loads(out.read_text())
        assert data["Profiles"][0]["Dynamic Profile Parent Name"] == "ClaudeCode"

    def test_inherits_from_geminicli_for_gemini(self, tmp_path):
        with patch("ai_cli.icon_generator._dynamic_profile_dir", return_value=tmp_path):
            out = generate_dynamic_profile("gemini-session", "#2ecc71", "gemini")
        data = json.loads(out.read_text())
        assert data["Profiles"][0]["Dynamic Profile Parent Name"] == "GeminiCLI"

    def test_tab_color_set_in_profile(self, tmp_path):
        with patch("ai_cli.icon_generator._dynamic_profile_dir", return_value=tmp_path):
            out = generate_dynamic_profile("test-session", "#ff0000", "cc")
        data = json.loads(out.read_text())
        profile = data["Profiles"][0]
        assert "Tab Color" in profile
        assert profile["Tab Color"]["Red Component"] == pytest.approx(1.0, abs=0.01)

    def test_use_tab_color_true(self, tmp_path):
        with patch("ai_cli.icon_generator._dynamic_profile_dir", return_value=tmp_path):
            out = generate_dynamic_profile("test-session", "#5e35b1", "cc")
        data = json.loads(out.read_text())
        assert data["Profiles"][0]["Use Tab Color"] is True

    def test_no_title_keys_set(self, tmp_path):
        with patch("ai_cli.icon_generator._dynamic_profile_dir", return_value=tmp_path):
            out = generate_dynamic_profile("test-session", "#5e35b1", "cc")
        data = json.loads(out.read_text())
        profile = data["Profiles"][0]
        # Must not set title-related keys — preserves user's Session Title dropdown
        assert "Title Components" not in profile
        assert "Title Format" not in profile

    def test_icon_path_included_when_provided(self, tmp_path):
        icon = tmp_path / "test-session.png"
        icon.write_bytes(b"fake")
        with patch("ai_cli.icon_generator._dynamic_profile_dir", return_value=tmp_path):
            out = generate_dynamic_profile("test-session", "#5e35b1", "cc", icon_path=icon)
        data = json.loads(out.read_text())
        assert data["Profiles"][0]["Custom Icon Path"] == str(icon)

    def test_icon_path_omitted_when_missing(self, tmp_path):
        missing = tmp_path / "nope.png"
        with patch("ai_cli.icon_generator._dynamic_profile_dir", return_value=tmp_path):
            out = generate_dynamic_profile("test-session", "#5e35b1", "cc", icon_path=missing)
        data = json.loads(out.read_text())
        assert "Custom Icon Path" not in data["Profiles"][0]

    def test_icon_path_omitted_when_none(self, tmp_path):
        with patch("ai_cli.icon_generator._dynamic_profile_dir", return_value=tmp_path):
            out = generate_dynamic_profile("test-session", "#5e35b1", "cc", icon_path=None)
        data = json.loads(out.read_text())
        assert "Custom Icon Path" not in data["Profiles"][0]

    def test_custom_base_profile_override(self, tmp_path):
        with patch("ai_cli.icon_generator._dynamic_profile_dir", return_value=tmp_path):
            out = generate_dynamic_profile("test-session", "#5e35b1", "cc", base_profile="MyCustomBase")
        data = json.loads(out.read_text())
        assert data["Profiles"][0]["Dynamic Profile Parent Name"] == "MyCustomBase"

    def test_background_color_included_when_provided(self, tmp_path):
        with patch("ai_cli.icon_generator._dynamic_profile_dir", return_value=tmp_path):
            out = generate_dynamic_profile("test-session", "#5e35b1", "cc", background_hex="#0d0d1f")
        data = json.loads(out.read_text())
        assert "Background Color" in data["Profiles"][0]

    def test_icon_mode_explicitly_set(self, tmp_path):
        with patch("ai_cli.icon_generator._dynamic_profile_dir", return_value=tmp_path):
            out = generate_dynamic_profile("test-session", "#5e35b1", "cc")
        data = json.loads(out.read_text())
        # Icon: 2 must be explicit — iTerm2 does not reliably inherit it from parent profiles
        assert data["Profiles"][0]["Icon"] == 2

    def test_rerun_is_idempotent(self, tmp_path):
        with patch("ai_cli.icon_generator._dynamic_profile_dir", return_value=tmp_path):
            out1 = generate_dynamic_profile("test-session", "#5e35b1", "cc")
            out2 = generate_dynamic_profile("test-session", "#5e35b1", "cc")
        assert out1 == out2
        assert len(list(tmp_path.glob("*.json"))) == 1


# ---------------------------------------------------------------------------
# Icon generation (Pillow)
# ---------------------------------------------------------------------------


class TestGenerateSessionIcon:
    def test_returns_none_when_pillow_unavailable(self, tmp_path):
        with patch("ai_cli.icon_generator._PILLOW_AVAILABLE", False):
            with patch("ai_cli.icon_generator._icon_cache_dir", return_value=tmp_path):
                result = generate_session_icon("test-session", "#5e35b1", "cc")
        assert result is None

    def test_generates_png_file(self, tmp_path):
        from PIL import Image

        logo_path = tmp_path / "claude-logo.png"
        img = Image.new("RGBA", (64, 64), (255, 255, 255, 200))
        img.save(logo_path)
        with patch("ai_cli.icon_generator._icon_cache_dir", return_value=tmp_path):
            with patch("ai_cli.icon_generator._source_logo_path", return_value=logo_path):
                result = generate_session_icon("test-session", "#5e35b1", "cc")
        assert result is not None
        assert result.exists()
        assert result.suffix == ".png"
        assert result.name == "test-session.png"

    def test_returns_none_when_no_source_logo(self, tmp_path):
        with patch("ai_cli.icon_generator._icon_cache_dir", return_value=tmp_path):
            with patch("ai_cli.icon_generator._source_logo_path", return_value=None):
                result = generate_session_icon("test-session", "#5e35b1", "cc")
        assert result is None

    def test_uses_source_logo_when_available(self, tmp_path):
        from PIL import Image

        # Create a minimal RGBA source logo
        logo_path = tmp_path / "claude-logo.png"
        img = Image.new("RGBA", (64, 64), (255, 255, 255, 200))
        img.save(logo_path)
        with patch("ai_cli.icon_generator._icon_cache_dir", return_value=tmp_path):
            with patch("ai_cli.icon_generator._source_logo_path", return_value=logo_path):
                result = generate_session_icon("test-session", "#5e35b1", "cc")
        assert result is not None and result.exists()

    def test_auto_derives_contrast_tint_from_tab_color(self, tmp_path):
        from PIL import Image

        from ai_cli.icon_generator import compute_contrast_tint

        logo_path = tmp_path / "claude-logo.png"
        Image.new("RGBA", (64, 64), (255, 255, 255, 200)).save(logo_path)
        tint_used = []
        original = __import__("ai_cli.icon_generator", fromlist=["_tint_image"])._tint_image

        def capture(img, tint_hex):
            tint_used.append(tint_hex)
            return original(img, tint_hex)

        with patch("ai_cli.icon_generator._icon_cache_dir", return_value=tmp_path):
            with patch("ai_cli.icon_generator._source_logo_path", return_value=logo_path):
                with patch("ai_cli.icon_generator._tint_image", side_effect=capture):
                    generate_session_icon("test-session", "#5e35b1", "cc")
        assert tint_used and tint_used[0] == compute_contrast_tint("#5e35b1")

    def test_no_tab_color_uses_brand_orange(self, tmp_path):
        from PIL import Image

        logo_path = tmp_path / "claude-logo.png"
        Image.new("RGBA", (64, 64), (255, 255, 255, 200)).save(logo_path)
        tint_used = []
        original = __import__("ai_cli.icon_generator", fromlist=["_tint_image"])._tint_image

        def capture(img, tint_hex):
            tint_used.append(tint_hex)
            return original(img, tint_hex)

        with patch("ai_cli.icon_generator._icon_cache_dir", return_value=tmp_path):
            with patch("ai_cli.icon_generator._source_logo_path", return_value=logo_path):
                with patch("ai_cli.icon_generator._tint_image", side_effect=capture):
                    generate_session_icon("test-session", None, "cc")
        assert tint_used and tint_used[0] == "#da7756"

    def test_explicit_icon_color_overrides_auto_tint(self, tmp_path):
        from PIL import Image

        logo_path = tmp_path / "claude-logo.png"
        Image.new("RGBA", (64, 64), (255, 255, 255, 200)).save(logo_path)
        tint_used = []
        original_tint = __import__("ai_cli.icon_generator", fromlist=["_tint_image"])._tint_image

        def capture_tint(img, tint_hex):
            tint_used.append(tint_hex)
            return original_tint(img, tint_hex)

        with patch("ai_cli.icon_generator._icon_cache_dir", return_value=tmp_path):
            with patch("ai_cli.icon_generator._source_logo_path", return_value=logo_path):
                with patch("ai_cli.icon_generator._tint_image", side_effect=capture_tint):
                    generate_session_icon("test-session", "#5e35b1", "cc", icon_color="#ff0000")
        assert tint_used and tint_used[0] == "#ff0000"

    def test_output_is_valid_png(self, tmp_path):
        from PIL import Image

        logo_path = tmp_path / "claude-logo.png"
        Image.new("RGBA", (64, 64), (255, 255, 255, 200)).save(logo_path)
        with patch("ai_cli.icon_generator._icon_cache_dir", return_value=tmp_path):
            with patch("ai_cli.icon_generator._source_logo_path", return_value=logo_path):
                result = generate_session_icon("test-session", "#5e35b1", "cc")
        img = Image.open(result)
        assert img.format == "PNG"
        assert img.mode == "RGBA"

    def test_output_is_correct_size(self, tmp_path):
        from PIL import Image

        logo_path = tmp_path / "claude-logo.png"
        Image.new("RGBA", (64, 64), (255, 255, 255, 200)).save(logo_path)
        with patch("ai_cli.icon_generator._icon_cache_dir", return_value=tmp_path):
            with patch("ai_cli.icon_generator._source_logo_path", return_value=logo_path):
                result = generate_session_icon("test-session", "#5e35b1", "cc")
        img = Image.open(result)
        assert img.size == (128, 128)


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


class TestCleanupSessionFiles:
    def test_removes_icon_and_profile_json(self, tmp_path):
        icon = tmp_path / "test-session.png"
        profile = tmp_path / "ai-cli-session-test-session.json"
        icon.write_bytes(b"png")
        profile.write_bytes(b"json")
        with (
            patch("ai_cli.icon_generator._icon_cache_dir", return_value=tmp_path),
            patch("ai_cli.icon_generator._dynamic_profile_dir", return_value=tmp_path),
        ):
            cleanup_session_files("test-session")
        assert not icon.exists()
        assert not profile.exists()

    def test_silent_when_files_missing(self, tmp_path):
        with (
            patch("ai_cli.icon_generator._icon_cache_dir", return_value=tmp_path),
            patch("ai_cli.icon_generator._dynamic_profile_dir", return_value=tmp_path),
        ):
            cleanup_session_files("nonexistent")  # must not raise

    def test_does_not_remove_other_sessions(self, tmp_path):
        icon_a = tmp_path / "test-session.png"
        icon_b = tmp_path / "other-session.png"
        icon_a.write_bytes(b"a")
        icon_b.write_bytes(b"b")
        with (
            patch("ai_cli.icon_generator._icon_cache_dir", return_value=tmp_path),
            patch("ai_cli.icon_generator._dynamic_profile_dir", return_value=tmp_path),
        ):
            cleanup_session_files("test-session")
        assert not icon_a.exists()
        assert icon_b.exists()


# ---------------------------------------------------------------------------
# ai internal cleanup-session-files CLI command
# ---------------------------------------------------------------------------


class TestCleanupSessionFilesCommand:
    def test_cleanup_session_files_internal_command(self, tmp_path):
        icon = tmp_path / "test-session.png"
        profile = tmp_path / "ai-cli-session-test-session.json"
        icon.write_bytes(b"x")
        profile.write_bytes(b"x")
        from ai_cli.main import cli

        with (
            patch("sys.argv", ["ai", "internal", "cleanup-session-files", "test-session"]),
            patch("ai_cli.icon_generator._icon_cache_dir", return_value=tmp_path),
            patch("ai_cli.icon_generator._dynamic_profile_dir", return_value=tmp_path),
            pytest.raises(SystemExit) as exc,
        ):
            cli()
        assert exc.value.code == 0
        assert not icon.exists()

    def test_cleanup_session_files_missing_arg_exits_1(self):
        from ai_cli.main import cli

        with patch("sys.argv", ["ai", "internal", "cleanup-session-files"]):
            with pytest.raises(SystemExit) as exc:
                cli()
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# Cleanup — error handling paths
# ---------------------------------------------------------------------------


class TestCleanupSessionFilesErrors:
    def test_when_icon_unlink_raises_then_continues_to_profile_cleanup(self, tmp_path):
        """lines 233-234: exception in icon unlink is silently caught; profile is still cleaned."""
        profile = tmp_path / "ai-cli-session-test-session.json"
        profile.write_bytes(b"json")

        with (
            patch("ai_cli.icon_generator._icon_cache_dir", return_value=tmp_path),
            patch("ai_cli.icon_generator._dynamic_profile_dir", return_value=tmp_path),
            patch.object(Path, "unlink", side_effect=[OSError("disk error"), None]),
        ):
            cleanup_session_files("test-session")  # must not raise

    def test_when_profile_unlink_raises_then_no_crash(self, tmp_path):
        """lines 237-238: exception in profile unlink is silently caught."""
        icon = tmp_path / "test-session.png"
        icon.write_bytes(b"png")

        def fake_unlink(self_path, **kwargs):
            if self_path.name.endswith(".json"):
                raise OSError("profile error")

        with (
            patch("ai_cli.icon_generator._icon_cache_dir", return_value=tmp_path),
            patch("ai_cli.icon_generator._dynamic_profile_dir", return_value=tmp_path),
            patch.object(Path, "unlink", fake_unlink),
        ):
            cleanup_session_files("test-session")  # must not raise


class TestSourceLogoPath:
    def test_when_unknown_session_type_then_returns_none(self):
        """line 78: unknown session_type has no filename in _SOURCE_LOGOS → returns None."""
        from ai_cli.icon_generator import _source_logo_path
        result = _source_logo_path("unknown_type_xyz")
        assert result is None
