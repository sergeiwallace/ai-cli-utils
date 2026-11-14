#!/usr/bin/env python3
"""Fold the changelog fragments in ``changelog.d/`` into CHANGELOG.md's ``[Unreleased]`` section.

Each change adds one file, ``changelog.d/<name>.<type>.md``, holding its entry exactly as it reads
in CHANGELOG.md: one or more Markdown list items. ``<type>`` names a Keep a Changelog section:
added, changed, deprecated, removed, fixed or security. New files never conflict, so two pull
requests that each add one merge cleanly, where two that edit the top of the same CHANGELOG.md
section do not.

At release time this script puts each fragment at the top of its ``### <Type>`` section, the first
one under ``## [Unreleased]``, newest first (by the commit that added it), which is where an entry
written by hand goes. A missing section is created in Keep a Changelog order. The folded fragments
are then deleted. Nothing is written when any fragment is malformed or the changelog has no
``## [Unreleased]`` heading.

Usage:
  python scripts/fold_changelog.py            # fold and delete the fragments
  python scripts/fold_changelog.py -n         # print the folded CHANGELOG.md, change nothing
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

FRAGMENT_DIR = "changelog.d"
#: Keep a Changelog's section order.
TYPES = ("added", "changed", "deprecated", "removed", "fixed", "security")
FRAGMENT_RE = re.compile(r"^(?P<name>[^/]+)\.(?P<type>[a-z]+)\.md$")
UNRELEASED = "## [Unreleased]"


class FoldError(ValueError):
    """A fragment or changelog the fold cannot use; nothing is written."""


def fragment_type(path: Path) -> str:
    """The section a fragment belongs to, or raise naming what is wrong with it."""
    match = FRAGMENT_RE.match(path.name)
    if not match or match["type"] not in TYPES:
        raise FoldError(f"{path.name}: not changelog.d/<name>.<type>.md with a type of {', '.join(TYPES)}")
    return match["type"]


def fragment_text(path: Path) -> str:
    text = path.read_bytes().decode("utf-8").replace("\r\n", "\n").strip("\n")
    if not text.strip():
        raise FoldError(f"{path.name}: empty fragment")
    if not text.startswith("- "):
        raise FoldError(f"{path.name}: a fragment is one or more Markdown list items starting with '- '")
    return "\n".join(line.rstrip() for line in text.splitlines()) + "\n"


def added_at(repo: Path, path: Path) -> int:
    """When the commit that added ``path`` was made, 0 when git does not know it."""
    done = subprocess.run(
        ["git", "-C", str(repo), "log", "--diff-filter=A", "--format=%ct", "-1", "--", str(path.relative_to(repo))],
        capture_output=True,
        text=True,
        check=False,
    )
    stamp = done.stdout.strip()
    return int(stamp) if done.returncode == 0 and stamp.isdigit() else 0


def fragment_paths(repo: Path) -> list[Path]:
    directory = repo / FRAGMENT_DIR
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file() and not p.name.startswith("."))


def fragments(repo: Path, paths: list[Path]) -> list[tuple[str, str]]:
    """``(type, text)`` for each of ``paths``, newest first; raises :class:`FoldError` on any bad one."""
    problems: list[str] = []
    found: list[tuple[int, str, str, str]] = []
    for path in paths:
        try:
            found.append((added_at(repo, path), path.name, fragment_type(path), fragment_text(path)))
        except FoldError as error:
            problems.append(str(error))
    if problems:
        raise FoldError("; ".join(problems))
    found.sort(key=lambda item: (-item[0], item[1]))
    return [(kind, text) for _when, _name, kind, text in found]


def fold(changelog: str, entries: list[tuple[str, str]]) -> str:
    """``changelog`` with each entry at the top of its section under ``## [Unreleased]``."""
    lines = changelog.splitlines(keepends=True)
    try:
        start = next(i for i, line in enumerate(lines) if line.rstrip("\n") == UNRELEASED)
    except StopIteration:
        raise FoldError(f"CHANGELOG.md has no '{UNRELEASED}' heading") from None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    for kind in TYPES:
        texts = [text for entry_kind, text in entries if entry_kind == kind]
        if not texts:
            continue
        heading = f"### {kind.capitalize()}"
        block = "".join(texts)
        at = next((i for i in range(start + 1, end) if lines[i].rstrip("\n") == heading), None)
        if at is not None:
            body = at + 1
            while body < end and not lines[body].strip():
                body += 1
            lines[body:body] = [block]
        else:
            later = {f"### {k.capitalize()}" for k in TYPES[TYPES.index(kind) + 1 :]}
            before = next((i for i in range(start + 1, end) if lines[i].rstrip("\n") in later), end)
            lines[before:before] = [f"{heading}\n\n{block}\n"]
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("-r", "--repo", type=Path, default=Path.cwd(), help="repository root (default: cwd)")
    parser.add_argument("-n", "--dry-run", action="store_true", help="print the folded CHANGELOG.md and change nothing")
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    changelog_path = repo / "CHANGELOG.md"
    paths = fragment_paths(repo)
    try:
        entries = fragments(repo, paths)
        if not entries:
            print(f"no changelog fragments in {FRAGMENT_DIR}/; CHANGELOG.md is unchanged")
            return 0
        # Bytes in and out, so the file keeps its own line endings on every OS.
        folded = fold(changelog_path.read_bytes().decode("utf-8"), entries)
    except (FoldError, OSError) as error:
        print(f"fold_changelog: {error}", file=sys.stderr)
        return 1
    if args.dry_run:
        sys.stdout.write(folded)
        return 0
    changelog_path.write_bytes(folded.encode("utf-8"))
    for path in paths:
        path.unlink()
    print(f"folded {len(entries)} changelog fragment(s) into CHANGELOG.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
