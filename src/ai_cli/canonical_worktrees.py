"""Durable registry for canonical AI session worktrees.

The registry records paths rather than deriving safety from a directory-name
pattern.  That makes it safe for installations which use a different session
naming convention and avoids confusing nested feature worktrees with the
session worktree that owns them.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import portalocker

from .config import get_xdg_data_home

REGISTRY_FILENAME = "canonical-session-worktrees.json"
REGISTRY_VERSION = 1

RegistryAction = Literal["created", "updated", "validated-already-tracked"]


class CanonicalWorktreeRegistryError(RuntimeError):
    """The canonical-worktree registry could not be safely read or written."""


def get_canonical_worktree_registry_path() -> Path:
    """Return the persistent per-machine registry path.

    SageMaker Code Editor stores ``$HOME`` on ephemeral instance storage while
    ``$HOME/user-default-efs`` persists across restarts.  When that EFS root is
    present, keep the state below it; other machines use the normal XDG data
    directory.  The environment override exists for administrators and tests
    that keep state on a separately managed persistent volume.
    """
    override = os.environ.get("AI_CLI_CANONICAL_WORKTREE_REGISTRY")
    if override:
        candidate = Path(override)
        if candidate.is_absolute():
            return candidate

    efs_home = Path.home() / "user-default-efs"
    if efs_home.is_dir():
        return efs_home / ".local" / "share" / "ai-cli-utils" / REGISTRY_FILENAME
    return get_xdg_data_home() / REGISTRY_FILENAME


def _load_registry(path: Path) -> dict:
    if not path.exists():
        return {"version": REGISTRY_VERSION, "worktrees": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CanonicalWorktreeRegistryError(f"cannot read canonical worktree registry {path}: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("version") != REGISTRY_VERSION
        or not isinstance(payload.get("worktrees"), list)
        or not all(isinstance(entry, dict) and isinstance(entry.get("path"), str) for entry in payload["worktrees"])
    ):
        raise CanonicalWorktreeRegistryError(f"canonical worktree registry {path} has an invalid schema")
    return payload


def _write_registry(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
            Path(temporary).replace(path)
        except Exception:
            Path(temporary).unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise CanonicalWorktreeRegistryError(f"cannot write canonical worktree registry {path}: {exc}") from exc


def register_canonical_worktree(path: Path, *, engine: str, session_name: str) -> tuple[RegistryAction, Path]:
    """Create or validate a canonical worktree entry and return its outcome.

    The exclusive lock and atomic replacement prevent a concurrent launcher
    from losing a registration.  A lock failure is surfaced to the launcher:
    silently starting an unregistered canonical session would defeat the guard.
    """
    registry_path = get_canonical_worktree_registry_path()
    canonical_path = str(path.resolve())
    now = datetime.now(UTC).isoformat()
    lock_path = registry_path.with_suffix(f"{registry_path.suffix}.lock")
    try:
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(str(lock_path), mode="a", timeout=10):
            payload = _load_registry(registry_path)
            for entry in payload["worktrees"]:
                if entry["path"] != canonical_path:
                    continue
                changed = entry.get("engine") != engine or entry.get("session_name") != session_name
                entry["engine"] = engine
                entry["session_name"] = session_name
                entry["last_validated_at"] = now
                _write_registry(registry_path, payload)
                return ("updated" if changed else "validated-already-tracked"), registry_path
            payload["worktrees"].append(
                {
                    "path": canonical_path,
                    "engine": engine,
                    "session_name": session_name,
                    "first_registered_at": now,
                    "last_validated_at": now,
                }
            )
            _write_registry(registry_path, payload)
            return "created", registry_path
    except (OSError, portalocker.exceptions.LockException) as exc:
        raise CanonicalWorktreeRegistryError(f"cannot lock canonical worktree registry {registry_path}: {exc}") from exc


def registered_canonical_worktrees() -> tuple[set[Path], Path]:
    """Return registered paths, refusing an absent, malformed, or locked registry.

    Deletion guards use this fail-closed reader.  A missing registry is not
    equivalent to an empty registry: it means the guard cannot prove a target
    is safe to remove.
    """
    registry_path = get_canonical_worktree_registry_path()
    if not registry_path.is_file():
        raise CanonicalWorktreeRegistryError(f"canonical worktree registry is missing: {registry_path}")
    lock_path = registry_path.with_suffix(f"{registry_path.suffix}.lock")
    try:
        with portalocker.Lock(str(lock_path), mode="a", timeout=0):
            payload = _load_registry(registry_path)
    except (OSError, portalocker.exceptions.LockException) as exc:
        raise CanonicalWorktreeRegistryError(f"cannot lock canonical worktree registry {registry_path}: {exc}") from exc
    return {Path(entry["path"]) for entry in payload["worktrees"]}, registry_path


def removal_targets_canonical_worktree(target: Path) -> tuple[bool, Path]:
    """Return whether *target* is exactly a registered worktree or its parent."""
    worktrees, registry_path = registered_canonical_worktrees()
    target = target.resolve(strict=False)
    for worktree in worktrees:
        try:
            worktree.resolve(strict=False).relative_to(target)
        except ValueError:
            continue
        return True, registry_path
    return False, registry_path
