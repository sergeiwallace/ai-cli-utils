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

Rewrite policy
--------------
Only STRING VALUES in decoded JSON are rewritten. Dictionary keys, numbers, booleans,
and null values are left untouched. A string value containing the old root as a
substring (not necessarily at the start) is rewritten. This policy means a longer
token embedding the prefix inside may be partially rewritten, which is accepted as
necessary for embedded path references in message content.

Malformed JSONL lines cause the entire file to be rejected with a loud error.

Missing destinations
--------------------
Mapping cwds by root-slug prefix is not sufficient on its own. A session whose cwd
was a since-reaped worktree maps to a destination path that has no checkout, so a
rewritten transcript points Claude Code at nothing while still being offered as
resumable. ``MissingDestPolicy`` decides what happens to those, and the caller must
say how destination existence is determined (``dest_exists``) rather than have one
guessed for it -- see ``plan_repath``.

Limitations
-----------
Windows paths are out of scope. The tool assumes POSIX paths only.

Depends only on the standard library and the existing ``cc_migrate`` machinery.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .cc_migrate import cc_project_dir

# Pattern to match embedded path references in any JSON string value
# This catches the old root appearing anywhere in string content, not just top-level cwd fields


class MissingDestPolicy(StrEnum):
    """What to do with a project dir whose repathed cwd does not exist.

    ``UNREPATHED`` is the default because it is the only option that loses nothing
    and claims nothing: the transcript stays readable as evidence, and because its
    directory slug still names the OLD cwd, the new machine cannot offer it as a
    resumable session.
    """

    SKIP = "skip"
    UNREPATHED = "unrepathed"
    REPATH = "repath"


@dataclass
class MissingDest:
    """A project dir whose repathed cwd does not exist on the destination."""

    old_dir: Path
    new_cwd: Path
    disposition: MissingDestPolicy


@dataclass
class RepathPlan:
    """What a bulk repath would do / did."""

    old_root: Path
    new_root: Path
    project_dirs: list[tuple[Path, Path]]  # (old_dir, new_dir) pairs
    total_jsonl_files: int
    total_bytes: int
    dry_run: bool
    missing_dest: list[MissingDest] = field(default_factory=list)


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
    disposition: str = "repathed"


def _slugify_cwd(cwd: str) -> str:
    """Return the slugified form Claude Code uses for a cwd path.

    Matches the transform in ``cc_migrate.cc_project_dir``: every non-alphanumeric
    character becomes ``-``.
    """
    return re.sub(r"[^a-zA-Z0-9]", "-", cwd)


def _rewrite_value(obj: object, old_root: str, new_root: str) -> tuple[object, bool]:
    """Recursively rewrite string values in a JSON structure.

    Only string values are rewritten; dict keys, numbers, booleans, and nulls are
    left untouched. Returns (rewritten_obj, changed).
    """
    if isinstance(obj, str):
        if old_root in obj:
            return obj.replace(old_root, new_root), True
        return obj, False
    if isinstance(obj, dict):
        changed = False
        result = {}
        for k, v in obj.items():
            new_v, v_changed = _rewrite_value(v, old_root, new_root)
            result[k] = new_v
            changed = changed or v_changed
        return result, changed
    if isinstance(obj, list):
        changed = False
        result = []
        for item in obj:
            new_item, item_changed = _rewrite_value(item, old_root, new_root)
            result.append(new_item)
            changed = changed or item_changed
        return result, changed
    # Numbers, booleans, None - pass through
    return obj, False


def _rewrite_jsonl_line(line: str, old_root: str, new_root: str) -> tuple[str, bool]:
    """Rewrite one JSONL line, replacing old_root with new_root in string values.

    Returns (rewritten_line, changed). Only JSON objects are rewritten; blank lines
    pass through unchanged. Malformed JSON raises an exception (caller must handle).

    Unlike the single-session migrate which only touches top-level cwd fields, this
    rewrites ALL string values containing the old root, because transcripts embed
    absolute paths in message content and tool results.

    Dict keys are never rewritten, only values.
    """
    stripped = line.strip()
    if not stripped:
        return line, False

    # Parse JSON - let exceptions propagate to caller
    record = json.loads(stripped)

    if not isinstance(record, dict):
        return line, False

    # Recursively rewrite string values
    rewritten_record, changed = _rewrite_value(record, old_root, new_root)
    if not changed:
        return line, False

    return json.dumps(rewritten_record, ensure_ascii=False, separators=(",", ":")) + "\n", True


def plan_repath(
    old_root: Path,
    new_root: Path,
    *,
    claude_home: Path | None = None,
    dest_exists: Callable[[Path], bool] | None = None,
    missing_dest_policy: MissingDestPolicy = MissingDestPolicy.UNREPATHED,
) -> RepathPlan:
    """Analyze what would be rewritten from old_root to new_root.

    Returns a plan splitting every matching project directory into ``project_dirs``
    (the repathed cwd exists, so the session stays resumable) and ``missing_dest``
    (it does not). Does not write anything.

    ``dest_exists`` decides which side a directory lands on, and is REQUIRED for
    every policy except ``REPATH``. There is deliberately no default: the obvious
    one, ``Path.is_dir``, is only correct when this runs ON the destination machine.
    Run from the source machine it reports every destination missing, silently
    degrading a legitimate whole-store repath into copying everything unrepathed --
    confidently wrong rather than merely unhelpful. Pass ``Path.is_dir`` when
    running on the target, or a predicate backed by a path manifest measured there.
    """
    if missing_dest_policy is not MissingDestPolicy.REPATH and dest_exists is None:
        raise ValueError(
            f"missing_dest_policy={missing_dest_policy.value!r} depends on whether each repathed cwd "
            "exists, so dest_exists is required. Pass dest_exists=Path.is_dir when running ON the "
            "destination machine, or a predicate backed by a manifest of paths measured there. To "
            "repath every directory without checking, ask for it explicitly with "
            "missing_dest_policy=MissingDestPolicy.REPATH."
        )

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
    missing_dest: list[MissingDest] = []
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

        # A repathed transcript is only resumable if the cwd it now names has a
        # checkout. Everything else is routed by policy rather than rewritten.
        if dest_exists is not None and not dest_exists(Path(new_cwd)):
            missing_dest.append(MissingDest(old_dir=old_dir, new_cwd=Path(new_cwd), disposition=missing_dest_policy))
            continue

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
        missing_dest=missing_dest,
    )


def _copy_or_rewrite_file(
    src: Path,
    dest: Path,
    old_root: str,
    new_root: str,
    *,
    is_jsonl: bool,
    result: RepathResult,
) -> None:
    """Copy one file to dest, rewriting if it's a .jsonl file.

    Raises OSError or json.JSONDecodeError on failure. Uses atomic write via temp file.
    """
    import os
    import tempfile

    if is_jsonl:
        # Parse and rewrite every line
        out_lines: list[str] = []
        try:
            with src.open("r", encoding="utf-8") as fh:
                for line_num, line in enumerate(fh, start=1):
                    result.total_lines += 1
                    try:
                        rewritten, changed = _rewrite_jsonl_line(line, old_root, new_root)
                        if changed:
                            result.lines_rewritten += 1
                        out_lines.append(rewritten)
                    except (json.JSONDecodeError, ValueError) as exc:
                        raise json.JSONDecodeError(
                            f"malformed JSONL at line {line_num}: {exc.msg}", exc.doc, exc.pos
                        ) from exc
        except json.JSONDecodeError:
            raise  # Re-raise with line number info

        content = "".join(out_lines)
        # Atomic write via temp file
        dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=dest.parent, prefix=".tmp_repath_", suffix=".jsonl")
        try:
            os.write(fd, content.encode("utf-8"))
            os.close(fd)
            Path(tmp_path).replace(dest)
        except Exception:
            os.close(fd)
            Path(tmp_path).unlink(missing_ok=True)
            raise

        result.bytes_written += len(content.encode("utf-8"))

        # Preserve mtime
        src_stat = src.stat()
        os.utime(dest, (src_stat.st_atime, src_stat.st_mtime))

        result.jsonl_files += 1
    else:
        # Copy byte-for-byte
        dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def repath_project_dir(
    old_dir: Path,
    new_dir: Path,
    old_root: str,
    new_root: str,
    *,
    dry_run: bool = False,
    rewrite: bool = True,
) -> RepathResult:
    """Repath one project directory from old_dir to new_dir.

    Recursively rewrites every ``*.jsonl`` file at any depth, replacing all
    occurrences of old_root with new_root in string values. Copies all other files
    byte-for-byte. Writes to new_dir (created if needed). Never modifies old_dir.

    ``rewrite=False`` copies every file byte-for-byte instead, jsonl included. That
    is what ``MissingDestPolicy.UNREPATHED`` needs: the transcript is preserved as
    readable evidence while still naming the old cwd, so nothing claims it can be
    resumed against a path that has no checkout.

    Malformed JSONL produces a loud error and no destination copy for that file.

    Returns statistics about what was rewritten.
    """
    result = RepathResult(
        old_dir=old_dir,
        new_dir=new_dir,
        jsonl_files=0,
        lines_rewritten=0,
        total_lines=0,
        bytes_written=0,
        disposition="repathed" if rewrite else "unrepathed",
    )

    if not old_dir.is_dir():
        result.errors.append(f"source directory {old_dir} does not exist")
        return result

    # Collect all files recursively
    all_files = sorted(old_dir.rglob("*"))
    jsonl_files = [f for f in all_files if f.is_file() and f.suffix == ".jsonl"]

    if not jsonl_files:
        # Still copy non-jsonl files if present
        pass

    if dry_run and not rewrite:
        # A copy-only run rewrites nothing, so there is nothing to count per line.
        result.jsonl_files = len(jsonl_files)
        return result

    if dry_run:
        # Dry run: count what would be rewritten without writing
        for jsonl in jsonl_files:
            try:
                with jsonl.open("r", encoding="utf-8") as fh:
                    for line_num, line in enumerate(fh, start=1):
                        result.total_lines += 1
                        try:
                            _, changed = _rewrite_jsonl_line(line, old_root, new_root)
                            if changed:
                                result.lines_rewritten += 1
                        except (json.JSONDecodeError, ValueError):
                            result.errors.append(f"{jsonl.relative_to(old_dir)}: malformed JSONL at line {line_num}")
                            break
                result.jsonl_files += 1
            except OSError as exc:
                result.errors.append(f"{jsonl.relative_to(old_dir)}: {exc}")
        return result

    # Real run: check for collisions first
    if new_dir.exists():
        result.errors.append(f"destination {new_dir} already exists - refusing to overwrite")
        return result

    # Copy/rewrite all files recursively
    for src_file in all_files:
        if src_file.is_dir():
            continue

        rel_path = src_file.relative_to(old_dir)
        dest_file = new_dir / rel_path
        is_jsonl = rewrite and src_file.suffix == ".jsonl"

        try:
            _copy_or_rewrite_file(src_file, dest_file, old_root, new_root, is_jsonl=is_jsonl, result=result)
        except (OSError, json.JSONDecodeError) as exc:
            result.errors.append(f"{rel_path}: {exc}")
            # Continue processing other files, but this file produced no output

    return result


def repath_all(
    old_root: Path,
    new_root: Path,
    dest_base: Path | None = None,
    *,
    dry_run: bool = False,
    claude_home: Path | None = None,
    dest_exists: Callable[[Path], bool] | None = None,
    missing_dest_policy: MissingDestPolicy = MissingDestPolicy.UNREPATHED,
) -> list[RepathResult]:
    """Repath all project directories from old_root to new_root.

    By default writes rewritten directories into the live ``~/.claude/projects/``.
    Pass ``dest_base`` to write into a different location for testing (the directory
    structure under dest_base will mirror the live layout).

    Detects and refuses collisions: if two distinct source directories map to the
    same destination, or if a destination already exists, the operation fails with
    an error.

    ``dest_exists`` and ``missing_dest_policy`` are forwarded to ``plan_repath`` and
    carry the same contract, including its refusal to guess an existence check.

    Returns one result per project directory processed, in each case carrying the
    ``disposition`` that was applied to it.
    """
    home = claude_home if claude_home is not None else Path.home() / ".claude"
    plan = plan_repath(
        old_root,
        new_root,
        claude_home=home,
        dest_exists=dest_exists,
        missing_dest_policy=missing_dest_policy,
    )

    results: list[RepathResult] = []
    old_root_str = str(old_root.resolve())
    new_root_str = str(new_root.resolve())

    # Check for destination collisions before processing anything
    seen_destinations: dict[Path, Path] = {}
    for old_dir, new_dir in plan.project_dirs:
        if dest_base:
            new_dir = dest_base / new_dir.name

        if new_dir in seen_destinations:
            # Two source directories map to the same destination
            result = RepathResult(
                old_dir=old_dir,
                new_dir=new_dir,
                jsonl_files=0,
                lines_rewritten=0,
                total_lines=0,
                bytes_written=0,
            )
            result.errors.append(
                f"destination collision: {old_dir} and {seen_destinations[new_dir]} both map to {new_dir}"
            )
            results.append(result)
            continue

        seen_destinations[new_dir] = old_dir

        result = repath_project_dir(
            old_dir,
            new_dir,
            old_root_str,
            new_root_str,
            dry_run=dry_run,
        )
        results.append(result)

    # Apply the policy to every directory whose repathed cwd has no checkout.
    for missing in plan.missing_dest:
        if missing.disposition is MissingDestPolicy.SKIP:
            results.append(
                RepathResult(
                    old_dir=missing.old_dir,
                    new_dir=missing.new_cwd,
                    jsonl_files=0,
                    lines_rewritten=0,
                    total_lines=0,
                    bytes_written=0,
                    disposition="skipped",
                )
            )
            continue

        # UNREPATHED: copy byte-for-byte under the ORIGINAL slug. Keeping the old
        # slug is the whole mechanism -- it names a cwd that does not exist on the
        # new machine, so the session is readable evidence and nothing else.
        if dest_base is None:
            # Writing into the live store, where the directory ALREADY sits under
            # its original slug. Leaving it alone is the action; copying a directory
            # onto itself would only trip the collision guard and report an error
            # for work that is already in the state the policy wants.
            results.append(
                RepathResult(
                    old_dir=missing.old_dir,
                    new_dir=missing.old_dir,
                    jsonl_files=0,
                    lines_rewritten=0,
                    total_lines=0,
                    bytes_written=0,
                    disposition="unrepathed",
                )
            )
            continue

        results.append(
            repath_project_dir(
                missing.old_dir,
                dest_base / missing.old_dir.name,
                old_root_str,
                new_root_str,
                dry_run=dry_run,
                rewrite=False,
            )
        )

    return results
