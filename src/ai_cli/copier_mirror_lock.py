"""Serialize Copier subprocesses that share a git mirror cache."""

from __future__ import annotations

import math
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import portalocker

from . import output as _out
from .config import resolve_base_dir
from .output import Tag

_TIMEOUT_ENV = "AI_CLI_COPIER_MIRROR_LOCK_TIMEOUT"


class CopierMirrorLockError(RuntimeError):
    """The Copier mirror lock could not be acquired."""


def _mirror_cache_dir(cwd: Path | None) -> Path:
    if override := os.environ.get("COPIER_CACHE_DIR"):
        # Copier interprets relative overrides in the subprocess's working directory.
        return (cwd or Path.cwd()).absolute() / override

    # Match platformdirs.user_cache_dir("copier", appauthor=False) without a dependency.
    if sys.platform == "win32":
        import ctypes

        # Query the Windows Local AppData known folder without requiring it to exist.
        local_app_data = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x001C | 0x4000, None, 0, local_app_data):
            raise CopierMirrorLockError("Could not resolve the Windows Local AppData folder for the Copier cache")
        return Path(local_app_data.value) / "copier" / "Cache" / "git"

    default = Path.home() / ("Library/Caches" if sys.platform == "darwin" else ".cache")
    return resolve_base_dir("XDG_CACHE_HOME", default) / "copier" / "git"


@contextmanager
def copier_mirror_lock(*, cwd: Path | None = None) -> Iterator[None]:
    """Hold the cache's OS lock or raise CopierMirrorLockError before entering the body."""
    lock_path = _mirror_cache_dir(cwd) / "copier-mirror.lock"
    try:
        timeout = float(os.environ.get(_TIMEOUT_ENV, "900"))
    except ValueError:
        timeout = math.nan
    if not math.isfinite(timeout) or timeout < 0:
        raise CopierMirrorLockError(f"{_TIMEOUT_ENV} must be a finite, non-negative number of seconds")

    # Keep the file beside the individual mirrors. Its OS lock is released when the holder exits.
    lock = portalocker.Lock(str(lock_path), mode="a", timeout=timeout, check_interval=min(0.25, timeout))
    try:
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                lock.acquire(fail_when_locked=True)
            except portalocker.exceptions.AlreadyLocked:
                if timeout == 0:
                    raise
                _out.emit(Tag.COPIER, "update", f"Waiting for Copier mirror lock: {lock_path}")
                lock.acquire(fail_when_locked=False)
        except portalocker.exceptions.AlreadyLocked as exc:
            raise CopierMirrorLockError(
                f"Timed out after {timeout:g} s waiting for Copier mirror lock {lock_path}"
            ) from exc
        except (OSError, portalocker.exceptions.LockException) as exc:
            raise CopierMirrorLockError(f"Could not acquire Copier mirror lock {lock_path}: {exc}") from exc
        yield
    finally:
        lock.release()
