#!/usr/bin/env python3
"""Clean up contaminated CC (Claude Code) session files.

Handles two types of contamination:
1. Stub files: <10KB OR <30 lines (catches large-record stubs that slip past the size gate)
2. Cross-project sessions (e.g., mylib-N sessions in myproject/ dirs)

Files are archived to ~/.claude-session-archive/YYYY-MM-DD/, never deleted.
Dry-run by default; pass --execute to actually move files.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import date
from pathlib import Path

STUB_SIZE_THRESHOLD = 10 * 1024  # 10KB
STUB_LINE_THRESHOLD = 30  # files with fewer lines than this are stubs

# Maps customTitle prefix patterns to expected staging subdir names.
# Order matters: more specific patterns first (sw-N-suffix before sw-N).
TITLE_PATTERNS: list[tuple[re.Pattern, callable]] = [
    (re.compile(r"^sw-(\d+)", re.IGNORECASE), lambda m: f"myproject--worktrees-sw-{m.group(1)}"),
    (re.compile(r"^aido-(\d+)", re.IGNORECASE), lambda m: f"aido--worktrees-aido-{m.group(1)}"),
    (re.compile(r"^art-(\d+)", re.IGNORECASE), lambda m: f"artelier--worktrees-art-{m.group(1)}"),
    (re.compile(r"^aurion-(\d+)", re.IGNORECASE), lambda m: f"aurion--worktrees-aurion-{m.group(1)}"),
    (re.compile(r"^aur-(\d+)", re.IGNORECASE), lambda m: f"aurion--worktrees-aur-{m.group(1)}"),
    (re.compile(r"^ai-cli-(\d+)", re.IGNORECASE), lambda m: f"ai-cli-utils--worktrees-ai-cli-{m.group(1)}"),
    (re.compile(r"^aq-(\d+)", re.IGNORECASE), lambda m: f"ai-cli-quickstart--worktrees-aq-{m.group(1)}"),
    (re.compile(r"^mobile-(\d+)", re.IGNORECASE), lambda m: f"ai-ide-mobile--worktrees-mobile-{m.group(1)}"),
    (re.compile(r"^hwosx-(\d+)", re.IGNORECASE), lambda m: f"ai-ide-macos--worktrees-hwosx-{m.group(1)}"),
    (re.compile(r"^macos-(\d+)", re.IGNORECASE), lambda m: f"ai-ide-macos--worktrees-macos-{m.group(1)}"),
    (re.compile(r"^dojo-(\d+)", re.IGNORECASE), lambda m: f"ai-dojo--worktrees-dojo-{m.group(1)}"),
    (re.compile(r"^ps-(\d+)", re.IGNORECASE), lambda m: f"personal-site--worktrees-ps-{m.group(1)}"),
    (re.compile(r"^site-(\d+)", re.IGNORECASE), lambda m: f"personal-site--worktrees-site-{m.group(1)}"),
    (re.compile(r"^job-(\d+)", re.IGNORECASE), lambda m: f"job-pilot--worktrees-job-{m.group(1)}"),
    (re.compile(r"^apt-(\d+)", re.IGNORECASE), lambda m: f"apt-switch--worktrees-apt-{m.group(1)}"),
    (re.compile(r"^trip-(\d+)", re.IGNORECASE), lambda m: f"trip-planner--worktrees-trip-{m.group(1)}"),
    (re.compile(r"^fin-(\d+)", re.IGNORECASE), lambda m: f"aurion--worktrees-fin-{m.group(1)}"),
    (re.compile(r"^acn-(\d+)", re.IGNORECASE), lambda m: f"acn-automation--worktrees-acn-{m.group(1)}"),
    (re.compile(r"^agora-(\d+)", re.IGNORECASE), lambda m: f"agora--worktrees-agora-{m.group(1)}"),
]


def get_expected_staging_dir(custom_title: str) -> str | None:
    """Return the expected staging subdir name for a customTitle, or None if unknown."""
    for pattern, resolver in TITLE_PATTERNS:
        m = pattern.match(custom_title)
        if m:
            return resolver(m)
    return None


def get_custom_title_and_linecount(jsonl_path: Path) -> tuple[str | None, int]:
    """Read customTitle and total line count from a JSONL file."""
    title = None
    line_count = 0
    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line_count += 1
                if title is None:
                    stripped = line.strip()
                    if stripped:
                        try:
                            record = json.loads(stripped)
                            if isinstance(record, dict) and "customTitle" in record:
                                title = record["customTitle"]
                        except (json.JSONDecodeError, ValueError):
                            pass
    except OSError:
        pass
    return title, line_count


def extract_project_subdir(dir_name: str, is_staging: bool) -> str:
    """Extract the project subdir name from a full directory name.

    Local CC dirs use encoded paths like '-Users-user-projects-myproject'
    while staging dirs use short names like 'myproject'.
    Returns the short project name suitable for matching against expected staging dirs.
    """
    if is_staging:
        return dir_name

    # Local CC dir: strip the path prefix to get the project-relative part
    # e.g., '-Users-user-projects-myproject--worktrees-session-1' -> 'myproject--worktrees-session-1'
    # The prefix pattern is '-{home_path}-projects-'
    # We find '-projects-' and take everything after it
    marker = "-projects-"
    idx = dir_name.find(marker)
    if idx != -1:
        return dir_name[idx + len(marker) :]
    return dir_name


def scan_dir(
    base_dir: Path,
    is_staging: bool,
    archive_base: Path,
    execute: bool,
) -> dict[str, int]:
    """Scan a base directory for contaminated session files.

    Returns counts: {stubs_archived, cross_project_archived, left_in_place, uuid_dirs_archived}
    """
    counts = {"stubs": 0, "cross_project": 0, "left": 0, "uuid_dirs": 0}
    label = "staging" if is_staging else "local"

    if not base_dir.is_dir():
        print(f"  [{label}] Directory not found: {base_dir}")
        return counts

    for project_dir in sorted(base_dir.iterdir()):
        if not project_dir.is_dir():
            continue

        dir_name = project_dir.name
        project_subdir = extract_project_subdir(dir_name, is_staging)

        jsonl_files = list(project_dir.glob("*.jsonl"))
        if not jsonl_files:
            continue

        for jsonl_path in sorted(jsonl_files):
            file_size = jsonl_path.stat().st_size
            custom_title, line_count = get_custom_title_and_linecount(jsonl_path)
            uuid_stem = jsonl_path.stem
            uuid_dir = jsonl_path.parent / uuid_stem

            # Check stub: small file OR very few lines (large-record stubs evade size gate)
            is_stub = file_size < STUB_SIZE_THRESHOLD or line_count < STUB_LINE_THRESHOLD
            if is_stub:
                reason = f"stub({file_size // 1024}KB,{line_count}lines)"
                title_info = f", customTitle={custom_title}" if custom_title else ""
                archive_dest = archive_base / dir_name / jsonl_path.name
                print(f"  ARCHIVE({reason}): {jsonl_path}{title_info} -> {archive_dest}")
                if execute:
                    _archive_file(jsonl_path, archive_dest)
                    counts["uuid_dirs"] += _archive_uuid_dir(uuid_dir, archive_base / dir_name / uuid_stem)
                counts["stubs"] += 1
                continue

            # Check cross-project contamination (only for named sessions)
            if custom_title:
                expected_dir = get_expected_staging_dir(custom_title)
                if expected_dir is not None and expected_dir != project_subdir:
                    reason = "cross-project"
                    archive_dest = archive_base / dir_name / jsonl_path.name
                    print(
                        f"  ARCHIVE({reason}): {jsonl_path} "
                        f"(customTitle={custom_title}, in={project_subdir}, "
                        f"expected={expected_dir}) -> {archive_dest}"
                    )
                    if execute:
                        _archive_file(jsonl_path, archive_dest)
                        counts["uuid_dirs"] += _archive_uuid_dir(uuid_dir, archive_base / dir_name / uuid_stem)
                    counts["cross_project"] += 1
                    continue

            counts["left"] += 1

    return counts


def _archive_file(src: Path, dest: Path) -> None:
    """Move a file to the archive location, creating parent dirs as needed."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))


def _archive_uuid_dir(uuid_dir: Path, archive_dest: Path) -> int:
    """Move a UUID subdirectory to the archive if it exists. Returns 1 if moved, 0 otherwise."""
    if uuid_dir.is_dir():
        archive_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(uuid_dir), str(archive_dest))
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean up contaminated CC session files.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually move files (default is dry-run)",
    )
    args = parser.parse_args()

    home = Path.home()
    local_base = home / ".claude" / "projects"
    staging_base = home / ".claude-sync-staging"
    archive_base = home / ".claude-session-archive" / date.today().isoformat()

    mode = "EXECUTE" if args.execute else "DRY RUN"
    print(f"CC Session Cleanup [{mode}]")
    print(f"  Archive location: {archive_base}")
    print()

    total = {"stubs": 0, "cross_project": 0, "left": 0, "uuid_dirs": 0}

    print("Scanning local CC dirs...")
    counts = scan_dir(local_base, is_staging=False, archive_base=archive_base / "local", execute=args.execute)
    for k in total:
        total[k] += counts[k]
    print()

    print("Scanning staging dirs...")
    counts = scan_dir(staging_base, is_staging=True, archive_base=archive_base / "staging", execute=args.execute)
    for k in total:
        total[k] += counts[k]
    print()

    print("=" * 60)
    print("Summary:")
    print(f"  Stubs archived (<{STUB_SIZE_THRESHOLD // 1024}KB or <{STUB_LINE_THRESHOLD}lines):  {total['stubs']}")
    print(f"  Cross-project archived:       {total['cross_project']}")
    print(f"  UUID dirs archived:           {total['uuid_dirs']}")
    print(f"  Left in place:                {total['left']}")
    action = "Archived" if args.execute else "Would archive"
    print(f"  {action} {total['stubs'] + total['cross_project']} files total.")


if __name__ == "__main__":
    main()
