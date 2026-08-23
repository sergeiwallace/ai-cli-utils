"""Bulk repath Claude Code transcripts from one filesystem root to another.

Extends the session-adoption rewrite machinery to relocate an entire
``~/.claude/projects`` store when the underlying filesystem changes (e.g. migrating
a SageMaker space). Rewrites both the slugified project DIRECTORY NAMES and the
EMBEDDED path values inside session ``*.jsonl`` files.

Why this exists
---------------
Claude Code keys sessions by the slugified absolute cwd. When a filesystem root
changes (e.g. ``/mnt/custom-file-systems/efs/fs-089.../projects`` →
``/home/sagemaker-user/projects``), every project directory becomes misnamed and
every embedded cwd reference becomes stale. This breaks resume.

The rewrite:
1. Maps old project slugs to new ones (via ``cc_project_dir`` from both roots)
2. For each project dir: rewrites every ``*.jsonl`` file's cwd/originalCwd fields
   plus any embedded literal references to the old root
3. Copies (never moves) rewritten files to new directory names under a destination
4. Leaves originals untouched until resume is proven

Depends only on the standard library and the existing ``cc_migrate`` machinery.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .cc_migrate import cc_project_dir

# Pattern to match embedded path references in any JSON string value
# This catches the old root appearing anywhere in string content, not just top-level cwd fields


@dataclass
class RepathPlan:
    """What a bulk repath would do / did."""

    old_root: Path
    new_root: Path
    project_dirs: list[tuple[Path, Path]]  # (old_dir, new_dir) pairs
    total_jsonl_files: int
    total_bytes: int
    dry_run: bool


@dataclass
class RepathResult:
    """Outcome of repathing one project directory."""

    old_dir: Path
    new_dir: Path
    jsonl_files: int
    lines_rewritten: int
    total_lines: int
    bytes_written: int
    errors: list[str] = field(default_factory=list)


def _slugify_cwd(cwd: str) -> str:
    """Return the slugified form Claude Code uses for a cwd path.

    Matches the transform in ``cc_migrate.cc_project_dir``: every non-alphanumeric
    character becomes ``-``.
    """
    return re.sub(r"[^a-zA-Z0-9]", "-", cwd)


def _rewrite_jsonl_line(line: str, old_root: str, new_root: str) -> tuple[str, bool]:
    """Rewrite one JSONL line, replacing all occurrences of old_root with new_root.

    Returns (rewritten_line, changed). Only JSON objects are rewritten; malformed
    or blank lines return unchanged. Unlike the single-session migrate which only
    touches top-level cwd fields, this rewrites ALL string values containing the
    old root, because transcripts embed absolute paths in message content and tool
    results.
    """
    stripped = line.strip()
    if not stripped:
        return line, False

    try:
        record = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return line, False

    if not isinstance(record, dict):
        return line, False

    # Serialize, do a string replacement, deserialize to verify it's still valid JSON
    # This catches embedded paths in any string value at any depth
    serialized = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    if old_root not in serialized:
        return line, False

    rewritten = serialized.replace(old_root, new_root)
    # Verify the replacement didn't break JSON structure
    try:
        json.loads(rewritten)
    except (json.JSONDecodeError, ValueError):
        # Replacement corrupted JSON - return original
        return line, False

    return rewritten + "\n", True


def plan_repath(
    old_root: Path,
    new_root: Path,
    *,
    claude_home: Path | None = None,
) -> RepathPlan:
    """Analyze what would be rewritten from old_root to new_root.

    Returns a plan describing every project directory that would be renamed and
    every file that would be rewritten. Does not write anything.
    """
    home = claude_home if claude_home is not None else Path.home() / ".claude"
    projects_dir = home / "projects"

    if not projects_dir.is_dir():
        return RepathPlan(
            old_root=old_root,
            new_root=new_root,
            project_dirs=[],
            total_jsonl_files=0,
            total_bytes=0,
            dry_run=True,
        )

    old_root = old_root.resolve()
    new_root = new_root.resolve()
    old_slug_prefix = _slugify_cwd(str(old_root))

    project_dirs: list[tuple[Path, Path]] = []
    total_files = 0
    total_bytes = 0

    # Find all project dirs whose slugs start with the old root's slug
    for old_dir in sorted(projects_dir.iterdir()):
        if not old_dir.is_dir():
            continue
        if not old_dir.name.startswith(old_slug_prefix):
            continue

        # Derive the original cwd from the slug by checking a sample transcript
        # (we can't reverse the slug uniquely, but we can read the cwd field)
        jsonl_files = list(old_dir.glob("*.jsonl"))
        if not jsonl_files:
            continue

        # Read the first line's cwd field to get the real path this slug represents
        original_cwd: str | None = None
        try:
            with jsonl_files[0].open("r", encoding="utf-8") as fh:
                for raw in fh:
                    stripped = raw.strip()
                    if stripped:
                        try:
                            record = json.loads(stripped)
                            if isinstance(record, dict) and record.get("cwd"):
                                original_cwd = str(record["cwd"])
                                break
                        except (json.JSONDecodeError, ValueError):
                            continue
        except OSError:
            pass

        if not original_cwd or not original_cwd.startswith(str(old_root)):
            continue

        # Compute the new cwd by replacing the old root prefix
        suffix = original_cwd[len(str(old_root)) :]
        new_cwd = str(new_root) + suffix
        new_dir = cc_project_dir(Path(new_cwd), claude_home=home)

        project_dirs.append((old_dir, new_dir))

        for jsonl in jsonl_files:
            try:
                total_bytes += jsonl.stat().st_size
                total_files += 1
            except OSError:
                pass

    return RepathPlan(
        old_root=old_root,
        new_root=new_root,
        project_dirs=project_dirs,
        total_jsonl_files=total_files,
        total_bytes=total_bytes,
        dry_run=True,
    )


def repath_project_dir(
    old_dir: Path,
    new_dir: Path,
    old_root: str,
    new_root: str,
    *,
    dry_run: bool = False,
) -> RepathResult:
    """Repath one project directory from old_dir to new_dir.

    Rewrites every ``*.jsonl`` file, replacing all occurrences of old_root with
    new_root. Writes to new_dir (created if needed). Never modifies old_dir.
    Returns statistics about what was rewritten.
    """
    result = RepathResult(
        old_dir=old_dir,
        new_dir=new_dir,
        jsonl_files=0,
        lines_rewritten=0,
        total_lines=0,
        bytes_written=0,
    )

    if not old_dir.is_dir():
        result.errors.append(f"source directory {old_dir} does not exist")
        return result

    jsonl_files = sorted(old_dir.glob("*.jsonl"))
    if not jsonl_files:
        return result

    if dry_run:
        # Dry run: count what would be rewritten without writing
        for jsonl in jsonl_files:
            try:
                with jsonl.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        result.total_lines += 1
                        _, changed = _rewrite_jsonl_line(line, old_root, new_root)
                        if changed:
                            result.lines_rewritten += 1
                result.jsonl_files += 1
            except OSError as exc:
                result.errors.append(f"{jsonl.name}: {exc}")
        return result

    # Real run: write rewritten files to new_dir
    try:
        new_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        result.errors.append(f"could not create {new_dir}: {exc}")
        return result

    for jsonl in jsonl_files:
        try:
            out_lines: list[str] = []
            with jsonl.open("r", encoding="utf-8") as fh:
                for line in fh:
                    result.total_lines += 1
                    rewritten, changed = _rewrite_jsonl_line(line, old_root, new_root)
                    if changed:
                        result.lines_rewritten += 1
                    out_lines.append(rewritten)

            dest_jsonl = new_dir / jsonl.name
            content = "".join(out_lines)
            dest_jsonl.write_text(content, encoding="utf-8")
            result.bytes_written += len(content.encode("utf-8"))

            # Preserve mtime
            src_stat = jsonl.stat()
            import os

            os.utime(dest_jsonl, (src_stat.st_atime, src_stat.st_mtime))

            # Copy sidecar directory if present
            sidecar_src = old_dir / jsonl.stem
            if sidecar_src.is_dir():
                sidecar_dest = new_dir / jsonl.stem
                shutil.copytree(sidecar_src, sidecar_dest, dirs_exist_ok=True)

            result.jsonl_files += 1

        except OSError as exc:
            result.errors.append(f"{jsonl.name}: {exc}")

    return result


def repath_all(
    old_root: Path,
    new_root: Path,
    dest_base: Path | None = None,
    *,
    dry_run: bool = False,
    claude_home: Path | None = None,
) -> list[RepathResult]:
    """Repath all project directories from old_root to new_root.

    By default writes rewritten directories into the live ``~/.claude/projects/``.
    Pass ``dest_base`` to write into a different location for testing (the directory
    structure under dest_base will mirror the live layout).

    Returns one result per project directory processed.
    """
    home = claude_home if claude_home is not None else Path.home() / ".claude"
    plan = plan_repath(old_root, new_root, claude_home=home)

    results: list[RepathResult] = []
    old_root_str = str(old_root.resolve())
    new_root_str = str(new_root.resolve())

    for old_dir, new_dir in plan.project_dirs:
        # If dest_base is given, write to that instead of the live projects dir
        if dest_base:
            new_dir = dest_base / new_dir.name

        result = repath_project_dir(
            old_dir,
            new_dir,
            old_root_str,
            new_root_str,
            dry_run=dry_run,
        )
        results.append(result)

    return results
