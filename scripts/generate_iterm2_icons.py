#!/usr/bin/env python3
"""Generate iTerm2 tab icon PNGs for ai-cli-utils profiles.

Reads color configuration from iterm2.toml and generates tinted PNG variants
for all ClaudeCode-{Color} and GeminiCLI-{Color} Dynamic Profile entries.

Usage:
    python scripts/generate_iterm2_icons.py
    python scripts/generate_iterm2_icons.py --outline
    python scripts/generate_iterm2_icons.py --size 256
    python scripts/generate_iterm2_icons.py --out-dir /path/to/output

Outputs to ~/.config/iterm2/icons/ by default (where iTerm2 profiles point).
Also copies to assets/iterm2-icons/ for repo check-in.
"""

import argparse
import shutil
import sys
from pathlib import Path

try:
    from PIL import Image, ImageFilter
except ImportError:
    print("Error: Pillow is required. Install with: pip install Pillow", file=sys.stderr)
    sys.exit(1)

try:
    import cairosvg

    HAS_CAIROSVG = True
except ImportError:
    HAS_CAIROSVG = False


# Icon color definitions: name → (R, G, B) 0-255
ICON_COLORS = {
    "coral": (232, 116, 97),  # brand coral / Claude orange-red
    "white": (255, 255, 255),
    "navy": (26, 26, 46),  # dark navy
    "purple": (139, 92, 246),  # purple (violet-500)
    "gold": (245, 158, 11),  # amber/gold
    "cyan": (6, 182, 212),  # cyan-500
    "teal": (20, 184, 166),  # teal-500
    "green": (34, 197, 94),  # green-500
}

GEMINI_ICON_COLORS = {
    "white": (255, 255, 255),
    "navy": (26, 26, 46),
    "gold": (245, 158, 11),
}


def load_svg_as_image(svg_path: Path, size: int) -> Image.Image:
    """Render SVG to RGBA PIL Image."""
    if not HAS_CAIROSVG:
        raise RuntimeError("cairosvg required for SVG rendering: pip install cairosvg")
    png_bytes = cairosvg.svg2png(url=str(svg_path), output_width=size, output_height=size)
    import io

    return Image.open(io.BytesIO(png_bytes)).convert("RGBA")


def load_png_as_image(png_path: Path, size: int) -> Image.Image:
    """Load PNG and resize to target size."""
    img = Image.open(png_path).convert("RGBA")
    if img.size != (size, size):
        img = img.resize((size, size), Image.LANCZOS)
    return img


def colorize(img: Image.Image, target_rgb: tuple[int, int, int]) -> Image.Image:
    """Recolor all non-transparent pixels to target_rgb, preserving alpha and luminance."""
    # Use luminance from grayscale to modulate the color
    # Convert both to float arrays for per-pixel blend
    import numpy as np

    arr = np.array(img, dtype=np.float32)
    tr, tg, tb = target_rgb
    # Use luminance (from source alpha-premultiplied grayscale) to scale target color
    lum = arr[:, :, :3].mean(axis=2) / 255.0
    out_arr = np.zeros_like(arr)
    out_arr[:, :, 0] = tr * lum
    out_arr[:, :, 1] = tg * lum
    out_arr[:, :, 2] = tb * lum
    out_arr[:, :, 3] = arr[:, :, 3]
    return Image.fromarray(out_arr.astype(np.uint8), "RGBA")


def add_outline(img: Image.Image, outline_color: tuple[int, int, int], width: int = 2) -> Image.Image:
    """Add a thin outline around opaque pixels for contrast on similar-color backgrounds.

    The outline color is chosen automatically based on luminance:
    - dark icon → white outline
    - light icon → black outline
    """
    r, g, b = outline_color
    # Expand alpha mask outward
    _, _, _, a = img.split()
    # Dilate the alpha
    dilated = a.filter(ImageFilter.MaxFilter(size=width * 2 + 1))
    # Create solid outline color image
    outline_img = Image.new("RGBA", img.size, (r, g, b, 0))

    # Outline pixels = dilated alpha mask minus original alpha
    from PIL import ImageChops

    diff = ImageChops.subtract(dilated.convert("L"), a)
    outline_img.putalpha(diff)

    # Composite: outline below original
    return Image.alpha_composite(outline_img, img)


def auto_outline_color(icon_rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Return white or black for best contrast against the icon color."""
    r, g, b = icon_rgb
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return (0, 0, 0) if luminance > 128 else (255, 255, 255)


def generate_icon(
    source: Image.Image,
    color_name: str,
    color_rgb: tuple[int, int, int],
    out_path: Path,
    with_outline: bool = False,
) -> None:
    tinted = colorize(source, color_rgb)
    if with_outline:
        outline = auto_outline_color(color_rgb)
        tinted = add_outline(tinted, outline)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tinted.save(str(out_path), "PNG")
    print(f"  ✓ {out_path.name}  ({color_name})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate iTerm2 tab icon PNGs for ai-cli-utils")
    parser.add_argument("--outline", action="store_true", help="Add auto-contrast outline around icons")
    parser.add_argument("--size", type=int, default=128, help="Output PNG size in pixels (default: 128)")
    parser.add_argument(
        "--out-dir",
        default=str(Path.home() / ".config" / "iterm2" / "icons"),
        help="Output directory for generated PNGs (default: ~/.config/iterm2/icons/)",
    )
    parser.add_argument(
        "--repo-dir",
        default=None,
        help="If set, also copy generated PNGs to this directory (e.g. assets/iterm2-icons/)",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    repo_dir = Path(args.repo_dir) if args.repo_dir else None

    # Load source images
    icons_dir = Path.home() / ".config" / "iterm2" / "icons"
    claude_svg = icons_dir / "claude-logo.svg"
    gemini_png = icons_dir / "gemini-logo.png"

    print(f"Generating {args.size}x{args.size} icons → {out_dir}")
    print(f"Outline: {'yes' if args.outline else 'no'}")
    print()

    # Load Claude source
    if claude_svg.exists():
        print(f"Loading Claude source: {claude_svg}")
        claude_source = load_svg_as_image(claude_svg, args.size)
    else:
        # Fall back to existing coral PNG as source for tinting
        fallback = icons_dir / "claude-icon-v3.png"
        if not fallback.exists():
            print(f"Error: Claude source not found at {claude_svg} or {fallback}", file=sys.stderr)
            sys.exit(1)
        print(f"Loading Claude source (PNG fallback): {fallback}")
        claude_source = load_png_as_image(fallback, args.size)

    # Load Gemini source
    if not gemini_png.exists():
        print(f"Error: Gemini source not found at {gemini_png}", file=sys.stderr)
        sys.exit(1)
    print(f"Loading Gemini source: {gemini_png}")
    gemini_source = load_png_as_image(gemini_png, args.size)

    print()
    print("Claude icons:")
    for name, rgb in ICON_COLORS.items():
        out_path = out_dir / f"claude-icon-{name}.png"
        generate_icon(claude_source, name, rgb, out_path, with_outline=args.outline)
        if repo_dir:
            repo_path = repo_dir / f"claude-icon-{name}.png"
            repo_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(out_path, repo_path)

    print()
    print("Gemini icons:")
    for name, rgb in GEMINI_ICON_COLORS.items():
        out_path = out_dir / f"gemini-icon-{name}.png"
        generate_icon(gemini_source, name, rgb, out_path, with_outline=args.outline)
        if repo_dir:
            repo_path = repo_dir / f"gemini-icon-{name}.png"
            repo_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(out_path, repo_path)

    print()
    print(f"Done. {len(ICON_COLORS) + len(GEMINI_ICON_COLORS)} icons generated.")
    if repo_dir:
        print(f"Copied to repo: {repo_dir}")


if __name__ == "__main__":
    main()
