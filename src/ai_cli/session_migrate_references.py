"""After a completed ``ai migrate-session``: rewrite, or at least name, every reference to the old name.

A migration moves everything ``ai`` keys by a session's name, but code and config in the
repository can still spell the old name out. Two steps close that gap, run once after each
writing run whose re-plan finds nothing left (:func:`ai_cli.session_migrate_status.verify`):

1. The command ``[migrate_session] references_command`` names in ``config.toml``, read at call
   time, run as ``argv + ["--old", OLD, "--new", NEW]`` in the migrated repository. It may
   rewrite references anywhere it knows about. A non-zero exit is a leftover, and so is each
   stdout line ``migrate-reference-left: <where>``. Unset, the step is skipped and says so.
2. A scan of the migrated repository's tracked files (``git grep -w``) for the old name and the
   tmux session names ``ai`` gives it. Each hit is a leftover, ``file:line: text``, unless its
   path matches a glob in ``[migrate_session] references_allow`` (default ``.beads/**``, where
   the migration's own re-key comments name the old session). The scan never rewrites.

Any leftover makes the run report ``finished with leftovers`` and exit 1; the migration
itself stays recorded finished, and a later ``--finish`` runs both steps again.
"""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

from . import config as _config
from . import session_migrate as m
from .git_repair import _git_env
from .session_migrate_hooks import _program

SECTION = "migrate_session"
COMMAND_KEY = "references_command"
ALLOW_KEY = "references_allow"
#: Paths whose hits are records of the migration itself, not stale references.
DEFAULT_ALLOW = (".beads/**",)
#: The stdout prefix a references command uses to name a reference it could not rewrite.
LEFT_PREFIX = "migrate-reference-left:"
#: Seconds the references command may run.
COMMAND_TIMEOUT_S = 600


def _settings() -> tuple[list[str] | None, list[str], list[str]]:
    """``(command argv or None, allow globs, problems)`` from ``[migrate_session]``, read now."""
    try:
        section = _config.load_config().get(SECTION, {})
    except Exception as exc:  # an unreadable config is a leftover, never a silent skip
        return None, list(DEFAULT_ALLOW), [f"config.toml could not be read: {exc}"]
    if not isinstance(section, dict):
        return None, list(DEFAULT_ALLOW), [f"[{SECTION}] must be a table"]
    problems: list[str] = []
    argv = section.get(COMMAND_KEY)
    if argv is not None and (
        not isinstance(argv, list) or not argv or not all(isinstance(arg, str) and arg for arg in argv)
    ):
        problems.append(f"{SECTION}.{COMMAND_KEY} must be a non-empty list of strings")
        argv = None
    allow = section.get(ALLOW_KEY, list(DEFAULT_ALLOW))
    if not isinstance(allow, list) or not all(isinstance(glob, str) for glob in allow):
        problems.append(f"{SECTION}.{ALLOW_KEY} must be a list of path globs")
        allow = list(DEFAULT_ALLOW)
    return argv, allow, problems


def run_command(argv: list[str], old: str, new: str, repo_root: Path) -> tuple[list[str], list[str]]:
    """``(notes, left)`` from one run of the references command."""
    command = [*_program(tuple(argv)), "--old", old, "--new", new]
    try:
        proc = subprocess.run(
            command,
            cwd=repo_root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=COMMAND_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return [], [f"references command did not finish within {COMMAND_TIMEOUT_S} s: {argv[0]}"]
    except OSError as exc:
        return [], [f"references command could not start: {argv[0]}: {exc}"]
    notes: list[str] = []
    left: list[str] = []
    for line in (proc.stdout or "").splitlines():
        if line.startswith(LEFT_PREFIX):
            left.append(line[len(LEFT_PREFIX) :].strip())
        elif line.strip():
            notes.append(line.strip())
    if proc.returncode != 0:
        stderr = m._one_line(proc.stderr or "")
        left.append(f"references command exited {proc.returncode}{': ' + stderr if stderr else ''}")
    return notes, left


def patterns(old: str) -> list[str]:
    """The old name and every tmux session name ``ai`` gives it, each matched as a whole word."""
    return [old, *m._session_variants(old)]


def scan(repo_root: Path, old: str, allow: list[str]) -> tuple[list[str], int]:
    """``(each "file:line: text" naming old, hits allowlisted)`` in ``repo_root``'s tracked files.

    Raises :class:`OSError` when git cannot run the search.
    """
    args = ["git", "-C", str(repo_root), "grep", "-n", "-I", "-w", "-F", "-z", "--no-color"]
    for pattern in patterns(old):
        args += ["-e", pattern]
    proc = subprocess.run(args, capture_output=True, encoding="utf-8", errors="replace", env=_git_env(), check=False)
    if proc.returncode == 1:
        return [], 0
    if proc.returncode != 0:
        raise OSError(m._one_line(proc.stderr or f"git grep exited {proc.returncode}"))
    hits: list[str] = []
    allowed = 0
    for record in filter(None, proc.stdout.split("\n")):
        path, line, text = [*record.split("\0", 2), "", ""][:3]
        if any(fnmatch.fnmatchcase(path, glob) for glob in allow):
            allowed += 1
            continue
        hits.append(f"{path}:{line}: {text.strip()}")
    return hits, allowed


def sweep(result: m.MigrationResult, repo_root: Path) -> None:
    """Run the references command and the scan for a completed ``result``; record what each left."""
    if result.references_checked:
        return
    argv, allow, problems = _settings()
    notes = result.reference_notes
    left = result.references_left
    left += problems
    if argv is None:
        notes.append(f"references command: skipped: none configured ([{SECTION}] {COMMAND_KEY})")
    else:
        command_notes, command_left = run_command(argv, result.old, result.new, repo_root)
        notes.append(f"references command: ran {argv[0]}")
        notes += [f"references command: {note}" for note in command_notes]
        left += command_left
    try:
        hits, allowed = scan(repo_root, result.old, allow)
    except OSError as exc:
        left.append(f"references scan of {repo_root} FAILED: {exc}")
    else:
        found = f"{len(hits)} reference(s) to {result.old} in tracked files of {repo_root}"
        notes.append(f"references scan: {found}" + (f", {allowed} more allowlisted" if allowed else ""))
        left += hits
    result.references_checked = True
    result.parts["references"] = f"left: {len(left)}" if left else "done now"
