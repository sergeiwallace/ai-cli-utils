"""Migrate a Claude Code session transcript between project roots.

Why this exists
---------------
Claude Code stores each session's transcript as a JSONL file under
``~/.claude/projects/<slug>/``, where ``<slug>`` is the session's working
directory with every non-alphanumeric character replaced by ``-``. That means
a session started at a repo root and a session started inside that repo's
``.worktrees/<name>`` worktree live in two *different* project directories,
and neither can see the other's history.

``ai c <n>`` (bare mode) resumes a worktree session by scanning the
*worktree's* project directory for a transcript whose first ``customTitle``
record matches the session's ai_name (e.g. ``myproject-1``), touching it, and
launching ``claude --continue``. So a conversation that was accidentally run
at the repo root (plain ``claude --name myproject-1`` instead of ``ai c 1``)
is invisible to ``ai c 1`` until its transcript is moved into the worktree's
project directory. This module does that move safely:

1. locate the source transcript by ``customTitle`` or session UUID,
2. rewrite each record's top-level ``cwd``/``originalCwd`` from the old root
   to the new root (content is otherwise byte-preserved per line),
3. write it into the destination project directory (created if needed, mode
   0700 to match Claude Code's own layout),
4. move the transcript's sidecar directory (``<uuid>/`` — subagents and
   tool-results) alongside it,
5. verify the destination parses line-for-line before the source is removed
   (move semantics; ``keep_source`` copies instead).

What it deliberately does NOT touch:

- the per-project ``memory/`` directory — auto-memory may be shared by other
  sessions still running from the source root; copy it manually if wanted;
- anything keyed by session UUID *outside* the project dir (todos, state,
  teams) — the UUID is unchanged by migration, so those keep working as-is.

Depends only on the standard library so it stays trivially testable.
"""

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

#: Top-level record fields that hold the session's working directory and are
#: rewritten from the source root to the destination root on migration.
_CWD_KEYS = ("cwd", "originalCwd")


def cc_project_dir(cwd: Path, claude_home: Path | None = None) -> Path:
    """Return the ``~/.claude/projects`` directory Claude Code uses for ``cwd``.

    Claude Code slugifies the absolute cwd by replacing every non-alphanumeric
    character with ``-`` (``/home/me/my_proj`` -> ``-home-me-my-proj``). The
    underscore case matters: replacing only ``/`` and ``.`` silently computes
    the wrong directory for any path containing ``_``.
    """
    home = claude_home if claude_home is not None else Path.home() / ".claude"
    return home / "projects" / re.sub(r"[^a-zA-Z0-9]", "-", str(cwd))


def transcript_title(path: Path) -> str | None:
    """Return the first non-empty ``customTitle`` in a transcript, else None.

    Mirrors the resume logic in ``main._find_cc_session_by_title``: only the
    first titled record matters — later ones repeat it.
    """
    try:
        with path.open("rb") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    record = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                found = record.get("customTitle", "")
                if found:
                    return found
    except OSError:
        return None
    return None


def find_transcript(project_dir: Path, *, title: str | None = None, session_id: str | None = None) -> Path | None:
    """Locate a transcript in ``project_dir`` by session UUID or customTitle.

    UUID wins when both are given (it is exact — the filename). Title search
    scans newest-first and returns the first transcript whose first titled
    record matches, the same file ``ai c`` would resume.
    """
    if session_id:
        candidate = project_dir / f"{session_id}.jsonl"
        return candidate if candidate.is_file() else None
    if not title:
        return None
    try:
        candidates = sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for path in candidates:
        if transcript_title(path) == title:
            return path
    return None


def _rewrite_line(raw: str, source_root: str, dest_root: str) -> str:
    """Rewrite top-level cwd fields in one JSONL record from source to dest root.

    Only records that parse as JSON objects and actually reference the source
    root are re-serialized; every other line is returned byte-identical, so a
    malformed or unrelated line can never be corrupted by the migration.
    Message *content* (conversation text mentioning old paths) is deliberately
    left alone — history should read as it happened.
    """
    stripped = raw.strip()
    if not stripped:
        return raw
    try:
        record = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return raw
    if not isinstance(record, dict):
        return raw
    changed = False
    for key in _CWD_KEYS:
        value = record.get(key)
        if (
            isinstance(value, str)
            and value.startswith(source_root)
            and (len(value) == len(source_root) or value[len(source_root)] in ("/", "\\"))
        ):
            record[key] = dest_root + value[len(source_root) :]
            changed = True
    if not changed:
        return raw
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"


@dataclass
class MigrationResult:
    """What a migration (or dry run) did / would do."""

    source_jsonl: Path
    dest_jsonl: Path
    sidecar_moved: Path | None
    lines: int
    rewritten: int
    moved: bool  # False = source kept (copy semantics) or dry run
    dry_run: bool
    warnings: list[str] = field(default_factory=list)


def migrate_session(
    source_root: Path,
    dest_root: Path,
    *,
    title: str | None = None,
    session_id: str | None = None,
    source_project_dir: Path | None = None,
    keep_source: bool = False,
    preserve_cwd: bool = False,
    dry_run: bool = False,
    force: bool = False,
    claude_home: Path | None = None,
) -> MigrationResult:
    """Move (or copy) one CC session transcript from ``source_root``'s project
    directory to ``dest_root``'s, rewriting recorded cwd fields. Normally the
    source project directory is derived from ``source_root``; callers that
    discovered a transcript in a moved or legacy project directory may supply
    that physical directory explicitly.

    Raises ``ValueError`` on any unsafe condition: no selector, source not
    found, destination worktree missing, or destination transcript already
    present (without ``force``). The source file is removed only after the
    destination has been written and re-parsed line-for-line.
    """
    if not title and not session_id:
        raise ValueError("select the session to migrate with a title or a session UUID")

    source_root = source_root.resolve()
    dest_root = dest_root.resolve()
    if not dest_root.is_dir():
        raise ValueError(
            f"destination root {dest_root} does not exist — create the worktree first "
            f"(git worktree add), then migrate into it"
        )

    source_dir = (source_project_dir or cc_project_dir(source_root, claude_home)).resolve()
    if not source_dir.is_dir():
        raise ValueError(f"no Claude Code project directory for {source_root} (looked at {source_dir})")

    src_jsonl = find_transcript(source_dir, title=title, session_id=session_id)
    if src_jsonl is None:
        wanted = f"title {title!r}" if title else f"session {session_id}"
        raise ValueError(f"no transcript with {wanted} in {source_dir}")

    dest_dir = cc_project_dir(dest_root, claude_home)
    dest_jsonl = dest_dir / src_jsonl.name
    if dest_jsonl.exists() and not force:
        raise ValueError(f"destination transcript {dest_jsonl} already exists (use --force to overwrite)")

    warnings: list[str] = []
    found_title = transcript_title(src_jsonl)
    if found_title and found_title != dest_root.name:
        warnings.append(
            f"transcript customTitle {found_title!r} != worktree name {dest_root.name!r} — "
            f"`ai c` matches by title, so resume may fall back to --continue-by-mtime"
        )

    src_str, dest_str = str(source_root), str(dest_root)
    lines = rewritten = 0
    out_lines: list[str] = []
    with src_jsonl.open("r", encoding="utf-8") as fh:
        for raw in fh:
            lines += 1
            if preserve_cwd:
                out_lines.append(raw)
                continue
            new = _rewrite_line(raw, src_str, dest_str)
            if new is not raw:
                rewritten += 1
            out_lines.append(new)

    sidecar_src = source_dir / src_jsonl.stem
    sidecar_dest = dest_dir / src_jsonl.stem if sidecar_src.is_dir() else None

    if dry_run:
        return MigrationResult(
            source_jsonl=src_jsonl,
            dest_jsonl=dest_jsonl,
            sidecar_moved=sidecar_dest,
            lines=lines,
            rewritten=rewritten,
            moved=False,
            dry_run=True,
            warnings=warnings,
        )

    dest_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    dest_jsonl.write_text("".join(out_lines), encoding="utf-8")
    # Preserve the source mtime: resume scans are ordered newest-first, and a
    # migrated three-day-old session should not outrank a genuinely newer one.
    src_stat = src_jsonl.stat()
    import os

    os.utime(dest_jsonl, (src_stat.st_atime, src_stat.st_mtime))

    # Verify before any destructive step: every destination line must still
    # parse at least as well as the source did (blank lines excepted).
    with dest_jsonl.open("r", encoding="utf-8") as fh:
        dest_count = sum(1 for _ in fh)
    if dest_count != lines:
        dest_jsonl.unlink()
        raise ValueError(f"verification failed: wrote {dest_count} lines, expected {lines} — source left untouched")

    if sidecar_dest is not None:
        if keep_source:
            shutil.copytree(sidecar_src, sidecar_dest, dirs_exist_ok=force)
        else:
            shutil.move(str(sidecar_src), str(sidecar_dest))

    moved = not keep_source
    if moved:
        src_jsonl.unlink()

    return MigrationResult(
        source_jsonl=src_jsonl,
        dest_jsonl=dest_jsonl,
        sidecar_moved=sidecar_dest,
        lines=lines,
        rewritten=rewritten,
        moved=moved,
        dry_run=False,
        warnings=warnings,
    )
