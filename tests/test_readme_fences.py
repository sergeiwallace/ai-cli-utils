"""Regression coverage for the README's Markdown code fences."""

from __future__ import annotations

import re
from pathlib import Path

_FENCE = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})(?P<suffix>.*)$")


def _fence_errors(markdown: str) -> list[str]:
    """Return Markdown fenced-code-block errors in ``markdown``."""
    errors: list[str] = []
    opener: tuple[int, str, int] | None = None

    for line_number, line in enumerate(markdown.splitlines(), start=1):
        match = _FENCE.match(line)
        if match is None:
            continue

        marker = match.group("marker")
        suffix = match.group("suffix")
        if opener is None:
            opener = (line_number, marker[0], len(marker))
            continue

        opening_line, opening_character, opening_length = opener
        if marker[0] != opening_character or len(marker) < opening_length:
            continue
        if suffix.strip():
            errors.append(f"line {line_number}: fence opened at line {opening_line} must use a bare closing fence")
            continue
        opener = None

    if opener is not None:
        errors.append(f"line {opener[0]}: unclosed code fence")
    return errors


def test_given_the_readme_when_checked_then_all_code_fences_are_balanced():
    readme = Path(__file__).resolve().parent.parent / "README.md"

    assert _fence_errors(readme.read_text(encoding="utf-8")) == []


def test_given_the_readme_when_checked_then_windows_limitations_are_disclosed():
    readme = Path(__file__).resolve().parent.parent / "README.md"

    text = readme.read_text(encoding="utf-8")

    assert "### Windows (experimental)" in text
    assert "the interactive launch lifecycle (keyboard interrupts, bare-mode display, and" in text
    assert "stale-worktree recovery) are not verified on Windows." in text
    assert "unverified; automated coverage is skipped" in text
