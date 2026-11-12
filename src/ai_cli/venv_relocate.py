"""Rewrite a moved virtual environment so it names its new location.

A virtual environment hard-codes the absolute path of the directory it was created in: console
scripts carry it in their shebang, ``pyvenv.cfg`` and the activate scripts name it, and editable
installs point at it from ``.pth`` files and ``direct_url.json``. When the directory holding the
environment moves, every one of those references goes stale. Reinstalling is not a reliable fix: it
needs the network and fails whenever the project pins a machine-specific source path. Rewriting the
old path to the new one is offline, fast and idempotent.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Iterable
from pathlib import Path

_SCRIPT_DIRS = ("bin", "Scripts")
_NAMED_FILES = ("pyvenv.cfg",)
# Top-level site-packages records that can hold an absolute path to an installed project: a
# ``.pth`` path file, a legacy setuptools ``.egg-link``, and the module a PEP 660 editable install
# imports from its ``__editable__`` ``.pth`` (``__editable___<name>_finder.py``).
_NAMED_SUFFIXES = (".pth", ".egg-link")
_EDITABLE_FINDER = ("__editable___", "_finder.py")
_NAMED_BASENAMES = ("direct_url.json",)
_MAX_BYTES = 1_000_000


def _forms(root: Path) -> list[str]:
    """Return the native spellings of ``root`` that can appear in a venv file."""
    forms = [str(root)]
    resolved = str(root.resolve(strict=False))
    if resolved not in forms:
        forms.append(resolved)
    return forms


def _uri_path(path: str) -> str | None:
    """``path`` as the path part of the ``file:`` URI pip writes into ``direct_url.json``.

    pip writes ``Path.as_uri()``: forward slashes, a leading ``/`` before a Windows drive and
    percent-encoding, so it differs from the native spelling on Windows and for any path with a
    character the URI encodes.
    """
    try:
        return Path(path).as_uri().removeprefix("file://")
    except ValueError:  # a relative path has no URI form
        return None


def _spellings(old_root: Path, new_root: Path) -> dict[bytes, bytes]:
    """Each spelling of ``old_root`` mapped to the same spelling of ``new_root``."""
    new = str(new_root)
    new_uri = _uri_path(new)
    spellings: dict[bytes, bytes] = {}
    for form in _forms(old_root):
        spellings.setdefault(form.encode(), new.encode())
        old_uri = _uri_path(form)
        if old_uri is not None and new_uri is not None:
            spellings.setdefault(old_uri.encode(), new_uri.encode())
    return spellings


def _candidates(venv: Path):
    for name in _NAMED_FILES:
        if (venv / name).is_file():
            yield venv / name
    for sub in _SCRIPT_DIRS:
        folder = venv / sub
        if folder.is_dir():
            yield from (p for p in sorted(folder.iterdir()) if p.is_file() and not p.is_symlink())
    for site in _site_packages(venv):
        for path in sorted(site.iterdir()):
            if _is_top_level_record(path.name) and path.is_file():
                yield path
            elif path.name.endswith(".dist-info"):
                yield from (path / name for name in _NAMED_BASENAMES if (path / name).is_file())


def _is_top_level_record(name: str) -> bool:
    prefix, suffix = _EDITABLE_FINDER
    return name.endswith(_NAMED_SUFFIXES) or (name.startswith(prefix) and name.endswith(suffix))


def _site_packages(venv: Path) -> list[Path]:
    """The venv's site-packages directories, on either platform layout.

    ``.pth`` and ``.egg-link`` files and editable finder modules live only at the top of a
    site-packages directory, and ``direct_url.json`` only in a ``*.dist-info`` directory beside
    them, so nothing below that is read: a package's own data tree (a bundled browser driver, node
    modules) can hold tens of thousands of files.
    """
    found: dict[Path, Path] = {}
    for pattern in ("lib/*/site-packages", "Lib/site-packages"):
        for site in sorted(venv.glob(pattern)):
            if site.is_dir():
                found.setdefault(site.resolve(), site)
    return list(found.values())


def _pattern(old_forms: Iterable[bytes]) -> re.Pattern[bytes]:
    # The old path must end at a path boundary, so ``/wt/session-1`` never matches inside ``/wt/session-10``.
    # Longest first, so a spelling that extends another wins where both start.
    alternatives = b"|".join(re.escape(form) for form in sorted(old_forms, key=len, reverse=True))
    return re.compile(b"(?:" + alternatives + rb")(?![\w.-])")


def _rewrite(path: Path, pattern: re.Pattern[bytes], spellings: dict[bytes, bytes]) -> bool:
    if path.stat().st_size > _MAX_BYTES:
        return False
    data = path.read_bytes()
    if b"\0" in data or not pattern.search(data):
        return False
    updated = pattern.sub(lambda match: spellings[match.group(0)], data)
    mode = path.stat().st_mode
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(updated)
        Path(temp_name).chmod(mode)
        Path(temp_name).replace(path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return True


def needs_relocation(venv: Path, old_root: Path) -> bool:
    """Whether any supported text artifact still names ``old_root``; read-only."""
    pattern = _pattern(_spellings(old_root, old_root))
    for path in _candidates(venv):
        if path.stat().st_size <= _MAX_BYTES:
            data = path.read_bytes()
            if b"\0" not in data and pattern.search(data):
                return True
    return False


def relocate_venv(venv: Path, old_root: Path, new_root: Path) -> list[Path]:
    """Rewrite ``old_root`` to ``new_root`` inside ``venv``; return the files changed.

    Only text files that carry the path are touched (console scripts, ``pyvenv.cfg``, ``.pth`` and
    ``direct_url.json`` files), each replaced atomically. A second call changes nothing.
    """
    if not venv.is_dir():
        return []
    spellings = _spellings(old_root, new_root)
    pattern = _pattern(spellings)
    return [path for path in _candidates(venv) if _rewrite(path, pattern, spellings)]
