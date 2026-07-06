"""Advisory roadmap ID allocator — `ai next-id <PREFIX>` (AIH-89 T-01).

Prints the next `PREFIX-N` roadmap id by reading the roadmap at ``origin/main``.
Advisory only: it fetches + reads, never reserves or writes (D-3 print-only). It
dramatically cuts the chance two concurrent sessions grab the same id; the pre-push
``roadmap-guard`` hook is the catch-net for the residual race.

Stopgap: retired at the SW-841 (Beads) cutover, where hash ids make sequential
`PREFIX-N` allocation moot.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROADMAP_PATH = "docs/roadmap/master-roadmap.md"

# Prefixes look like AIH, SW, AIDO, AI-CLI — uppercase segments joined by hyphens.
_PREFIX_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*$")


class NextIdError(Exception):
    """Raised when the next id cannot be determined (bad prefix / unreachable roadmap)."""


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def _max_id(roadmap_text: str, prefix: str) -> int:
    """Highest N among ``\\`[PREFIX-N]\\``` entry tokens.

    Only the backtick-bracket entry form counts, so plain-text ``Related: PREFIX-N``
    mentions do not inflate the max. Returns 0 when the prefix has no entries yet.
    """
    pat = re.compile(r"`\[" + re.escape(prefix) + r"-(\d+)\]`")
    return max((int(m) for m in pat.findall(roadmap_text)), default=0)


def next_id(prefix: str, repo_root: Path | None = None, *, fetch: bool = True) -> str:
    """Return the next ``PREFIX-N`` id from ``origin/main``'s roadmap (print-only).

    Args:
        prefix: task prefix, e.g. ``AIH`` (case-insensitive; normalized to upper).
        repo_root: repo to read (default: current working directory).
        fetch: run ``git fetch origin main`` first so a stale remote-tracking ref
            cannot reintroduce the collision the allocator guards against (F-04).

    Raises:
        NextIdError: invalid prefix, not a git repo, or roadmap unreadable at origin/main.
    """
    prefix = (prefix or "").strip().upper()
    if not _PREFIX_RE.match(prefix):
        raise NextIdError(f"invalid prefix {prefix!r}: expected an uppercase id prefix like AIH, SW, AI-CLI")
    root = Path(repo_root or Path.cwd()).resolve()
    if _run(["git", "rev-parse", "--show-toplevel"], root).returncode != 0:
        raise NextIdError(f"not a git repository: {root}")
    if fetch:
        # Best-effort: a fetch failure (offline) still lets us read the existing
        # origin/main ref below; only a missing ref is fatal.
        _run(["git", "fetch", "origin", "main"], root)
    show = _run(["git", "show", f"origin/main:{ROADMAP_PATH}"], root)
    if show.returncode != 0:
        raise NextIdError(f"cannot read {ROADMAP_PATH} at origin/main in {root}: {show.stderr.strip()}")
    return f"{prefix}-{_max_id(show.stdout, prefix) + 1}"
