"""Run the configured ``[resume_restore]`` command before a Claude Code resume.

A deployment may archive an idle transcript, for example as ``<session-id>.jsonl.zst``
beside where it was, and remove the original. ``claude --resume`` cannot read the
archive, and the launcher cannot even see the session to pick it. The configured
command restores a session's transcripts; the launcher runs it before choosing what to
resume and again for the session it resumes, and refuses the resume when it fails.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from . import config as _config

CAPABILITY = "transcript-restore"
ARCHIVE_SUFFIX = ".jsonl.zst"


class TranscriptRestoreError(RuntimeError):
    """The restore command failed, so the resume must not go ahead."""


def _settings() -> dict | None:
    try:
        return _config.read_resume_restore_config(_config.load_config())
    except ValueError as exc:
        raise TranscriptRestoreError(f"Resume stopped: {exc} (config.toml [resume_restore])") from exc


def configured() -> bool:
    """Whether a valid restore command is configured, read at call time."""
    try:
        return _settings() is not None
    except TranscriptRestoreError:
        return False


def _argv(command: list[str], session_id: str) -> list[str]:
    argv = [word.replace(_config.RESUME_RESTORE_PLACEHOLDER, session_id) for word in command]
    # A bare name is looked up on PATH here, so Windows also finds .cmd and .bat programs.
    resolved = shutil.which(argv[0]) if Path(argv[0]).name == argv[0] else None
    return [resolved or argv[0], *argv[1:]]


def _run(settings: dict, session_id: str) -> None:
    argv = _argv(settings["command"], session_id)
    timeout = settings["timeout_seconds"]
    stopped = f"Transcript restore for session {session_id} failed"
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.output or b"").decode(errors="replace").strip()
        raise TranscriptRestoreError(_message(f"{stopped} (timed out after {timeout:g}s)", argv, output)) from exc
    except OSError as exc:
        raise TranscriptRestoreError(_message(f"{stopped} (could not start: {exc})", argv, "")) from exc
    if result.returncode != 0:
        output = result.stdout.decode(errors="replace").strip()
        raise TranscriptRestoreError(_message(f"{stopped} (exit {result.returncode})", argv, output))


def _message(headline: str, argv: list[str], output: str) -> str:
    shown = subprocess.list2cmdline(argv) if sys.platform == "win32" else shlex.join(argv)
    lines = [f"{headline}; the resume is stopped.", f"  command: {shown}"]
    if output:
        lines.append("  output:")
        lines.extend(f"    {line}" for line in output.splitlines())
    return "\n".join(lines)


def _archived_session_ids(project_dir: Path) -> list[tuple[str, Path]]:
    try:
        archives = sorted(project_dir.glob(f"*{ARCHIVE_SUFFIX}"))
    except OSError:
        return []
    found = []
    for archive in archives:
        stem = archive.name[: -len(ARCHIVE_SUFFIX)]
        try:
            canonical = str(uuid.UUID(stem))
        except ValueError:
            continue
        if canonical == stem and not (project_dir / f"{stem}.jsonl").exists():
            found.append((stem, archive))
    return found


def restore_archived(project_dir: Path) -> set[str]:
    """Restore every archived session transcript in ``project_dir``; return the ids restored.

    Runs before the resume target is chosen, which reads each transcript's title and
    mtime. The restored file keeps the archive's mtime when the command gave it a newer
    one, so restoring an old conversation never makes it look the most recently active.
    """
    settings = _settings()
    if settings is None:
        return set()
    restored = set()
    for session_id, archive in _archived_session_ids(project_dir):
        try:
            archived_at = archive.stat().st_mtime
        except OSError:
            continue
        _run(settings, session_id)
        transcript = project_dir / f"{session_id}.jsonl"
        if not transcript.is_file():
            raise TranscriptRestoreError(
                f"Transcript restore for session {session_id} exited 0 but {transcript} is still missing; "
                "the resume is stopped."
            )
        stat = transcript.stat()
        if stat.st_mtime > archived_at:
            os.utime(transcript, (stat.st_atime, archived_at))
        restored.add(session_id)
    return restored


def restore_session(session_id: str, already: set[str] | frozenset[str] = frozenset()) -> None:
    """Run the restore command for the session about to be resumed, unless it already ran for it."""
    settings = _settings()
    if settings is None or session_id in already:
        return
    _run(settings, session_id)
