#!/usr/bin/env python3
"""
lint_doc_alignment.py — verify that shared sections in CLAUDE.md and GEMINI.md are identical.

Shared sections that must remain in sync:
  - ## Public Open-Source Package Standards
  - ## CLI Conventions
  - ## Test Requirements

Exit 0 if all shared sections match; exit 1 with diff output otherwise.

Usage:
  python scripts/lint_doc_alignment.py [--claude PATH] [--gemini PATH]
"""

import argparse
import difflib
import re
import sys
from pathlib import Path

SHARED_SECTIONS = [
    "## Public Open-Source Package Standards",
    "## CLI Conventions",
    "## Test Requirements",
]


def extract_section(text: str, heading: str) -> str:
    """Return the content of a markdown section (heading line through the next ## heading).

    Returns an empty string if the heading is not found.
    """
    pattern = re.compile(
        r"(^" + re.escape(heading) + r"\s*\n(?:(?!^## )[\s\S])*)",
        re.MULTILINE,
    )
    match = pattern.search(text)
    if not match:
        return ""
    return match.group(1).rstrip()


def check_alignment(claude_path: Path, gemini_path: Path) -> int:
    """Compare shared sections between CLAUDE.md and GEMINI.md.

    Returns 0 on success, 1 on any mismatch or missing section.
    """
    claude_text = claude_path.read_text(encoding="utf-8")
    gemini_text = gemini_path.read_text(encoding="utf-8")

    exit_code = 0

    for heading in SHARED_SECTIONS:
        claude_section = extract_section(claude_text, heading)
        gemini_section = extract_section(gemini_text, heading)

        if not claude_section:
            print(f"ERROR: section '{heading}' not found in {claude_path}", file=sys.stderr)
            exit_code = 1
            continue

        if not gemini_section:
            print(f"ERROR: section '{heading}' not found in {gemini_path}", file=sys.stderr)
            exit_code = 1
            continue

        if claude_section != gemini_section:
            print(f"\nMISMATCH: '{heading}'")
            diff = difflib.unified_diff(
                claude_section.splitlines(keepends=True),
                gemini_section.splitlines(keepends=True),
                fromfile=str(claude_path),
                tofile=str(gemini_path),
            )
            sys.stdout.writelines(diff)
            exit_code = 1

    if exit_code == 0:
        print("OK: all shared sections match between CLAUDE.md and GEMINI.md")

    return exit_code


def main() -> None:
    repo_root = Path(__file__).parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--claude",
        default=str(repo_root / "CLAUDE.md"),
        help="Path to CLAUDE.md (default: repo root)",
    )
    parser.add_argument(
        "--gemini",
        default=str(repo_root / "GEMINI.md"),
        help="Path to GEMINI.md (default: repo root)",
    )
    args = parser.parse_args()

    sys.exit(check_alignment(Path(args.claude), Path(args.gemini)))


if __name__ == "__main__":
    main()
