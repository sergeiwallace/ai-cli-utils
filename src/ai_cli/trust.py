"""Workspace trust registration for Claude Code.

Claude Code 2.1.x (GitHub #72896, a side-effect of the CVE-2026-33068 trust
hardening) splits workspace trust into two checks that use incompatible key
matching:

* ``checkHasTrustDialogAccepted`` walks *ancestor* directories in
  ``~/.claude.json`` ``projects`` and suppresses the interactive trust dialog
  if any ancestor is trusted.
* ``isWorkspacePersistedTrusted`` gates loading of ``permissions.allow`` rules
  on an *exact* match of the workspace key (``gitRoot(cwd)``) — no ancestor
  walk.

With ``~/projects`` trusted as an ancestor, the dialog is suppressed for every
subfolder, so each workspace's exact key never gets registered. A
non-interactive ``claude`` launched there then drops its ``.claude/settings.json``
permissions and prints::

    Ignoring N permissions.allow entries from .claude/settings.json:
    this workspace has not been trusted.

Registering ``projects[<gitRoot>].hasTrustDialogAccepted = true`` is the
official remedy (per Claude Code's error docs). We do it at session/worktree
creation — before ``claude`` launches — so the key is present when Claude Code
reads it, and provide a backfill for pre-existing repos.

All functions are best-effort: they never raise into the launch path. If
``~/.claude.json`` is missing, unparseable, or unwritable, they no-op.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path


def _claude_json_path() -> Path:
    # The warning itself points at ~/.claude.json, and Claude Code reads workspace
    # trust from there (not from $CLAUDE_CONFIG_DIR/settings.json).
    return Path.home() / ".claude.json"


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write ``data`` as 2-space JSON to ``path`` via a same-dir temp + rename.

    Matches Claude Code's own formatting (2-space indent, trailing newline) so
    an external edit doesn't reflow the whole 140 KB file.
    """
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".claude.json.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _git_root(path: Path) -> str | None:
    """Return the workspace key Claude Code uses for ``path``: its git toplevel,
    or the resolved path itself when not in a git repo. ``None`` if unresolvable."""
    try:
        res = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0 and res.stdout.strip():
            return str(Path(res.stdout.strip()).resolve())
    except (OSError, ValueError):
        pass
    try:
        return str(Path(path).resolve())
    except (OSError, RuntimeError):
        return None


def ensure_workspace_trusted(paths: Iterable[os.PathLike | str]) -> list[str]:
    """Idempotently mark each path's workspace key trusted in ``~/.claude.json``.

    Each path is resolved to its ``gitRoot`` (the key Claude Code exact-matches)
    and given ``{"hasTrustDialogAccepted": true}`` if not already set. Returns
    the list of keys newly added/changed. Best-effort: returns ``[]`` and never
    raises on any I/O or parse error.
    """
    keys: list[str] = []
    for p in paths:
        if not p:
            continue
        key = _git_root(Path(p))
        if key and key not in keys:
            keys.append(key)
    if not keys:
        return []

    cfg = _claude_json_path()
    # Only ever update an existing config — Claude Code owns creation of
    # ~/.claude.json; fabricating it here could interfere with CC's own
    # first-run initialization. If it's absent, CC hasn't run yet anyway.
    if not cfg.exists():
        return []
    try:
        data = json.loads(cfg.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []

    projects = data.get("projects")
    if not isinstance(projects, dict):
        projects = {}
        data["projects"] = projects

    changed: list[str] = []
    for key in keys:
        entry = projects.get(key)
        if not isinstance(entry, dict):
            entry = {}
            projects[key] = entry
        if entry.get("hasTrustDialogAccepted") is not True:
            entry["hasTrustDialogAccepted"] = True
            changed.append(key)

    if changed:
        try:
            _atomic_write_json(cfg, data)
        except OSError:
            return []
    return changed


def backfill_projects_trust(root: os.PathLike | str) -> list[str]:
    """Register trust for every git repo (and its linked worktrees) under ``root``.

    One-shot remediation for the existing fleet: walks the top-level children of
    ``root``, and for each git repo registers its main gitRoot plus every linked
    worktree path. Returns the list of keys newly added.
    """
    base = Path(root).expanduser()
    if not base.is_dir():
        return []

    targets: list[str] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        top = subprocess.run(
            ["git", "-C", str(child), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
        if top.returncode != 0 or not top.stdout.strip():
            continue
        targets.append(top.stdout.strip())
        # Include linked worktrees (belt-and-suspenders: some CC versions key a
        # worktree by its own path rather than the main gitRoot).
        wts = subprocess.run(
            ["git", "-C", str(child), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
        )
        for line in wts.stdout.splitlines():
            if line.startswith("worktree "):
                targets.append(line.split(" ", 1)[1])

    return ensure_workspace_trusted(targets)
