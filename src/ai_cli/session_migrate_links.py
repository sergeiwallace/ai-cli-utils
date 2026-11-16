"""Links inside a migrated session's worktrees that name an old-name path.

A checkout can hold a symlink naming another path of the same session by its old name: a
``.venv`` linking to a venv kept in a sibling worktree, a link to a file of another agent
worktree, either absolute or relative. ``ai migrate-session`` renames the slot
``.worktrees/<old>`` and the agents containers ``<old>-agents`` and ``<host>--<old>-agents``, so
each such link would dangle afterwards. :func:`scan` finds them and :func:`rewrite` repoints one
at the same path under the new name, keeping a relative link relative.

Only links that live inside the session's worktrees are read for rewriting, and no directory
link is descended into, so a link outside them is never changed. A link whose own target is
outside but resolves through an outside link naming an old-name path cannot be fixed here; it is
reported, and a writing run fails on it. So is a Windows junction naming an old-name path, which
the standard library cannot create.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

#: How many outside links a chain is followed through before it is treated as a loop.
_MAX_HOPS = 40
_WINDOWS_PREFIXES = ("\\\\?\\UNC\\", "\\\\?\\", "\\??\\")


@dataclass(frozen=True)
class StaleLink:
    """One link that names an old-name path, located where it will be after the move."""

    path: Path
    target: str
    #: What the link is rewritten to; None when it cannot be rewritten here.
    new_target: str | None
    reason: str = ""


def _is_junction(path: Path) -> bool:
    check = getattr(os.path, "isjunction", None)  # Python 3.12+
    return bool(check and check(path))


def _readlink(path: Path) -> str:
    raw = str(path.readlink())
    if sys.platform == "win32":  # a junction or absolute link reads back with its NT prefix
        for prefix in _WINDOWS_PREFIXES:
            if raw.startswith(prefix):
                return ("\\\\" + raw[len(prefix) :]) if prefix.endswith("UNC\\") else raw[len(prefix) :]
    return raw


def _normal(path: Path) -> Path:
    """``path`` with ``.`` and ``..`` collapsed lexically, as the link itself spells it."""
    return Path(os.path.normpath(path))


def _spellings(target: Path) -> list[Path]:
    """``target`` as written, and with its existing parent directories resolved."""
    lexical = _normal(target)
    resolved = lexical.parent.resolve(strict=False) / lexical.name
    return [lexical] if resolved == lexical else [lexical, resolved]


def _renamed(target: Path, pairs: Iterable[tuple[Path, Path]]) -> Path | None:
    """``target`` under the new name, when one of its spellings lies in an old root of ``pairs``."""
    for path in _spellings(target):
        for old, new in pairs:
            for form in {old, old.resolve(strict=False)}:
                if path == form or form in path.parents:
                    return form.parent / new.name / path.relative_to(form)
    return None


def _inside(target: Path, trees: Iterable[Path]) -> bool:
    path = _normal(target)
    return any(path == tree or tree in path.parents for tree in trees)


def _outside_hop(start: Path, pairs: list[tuple[Path, Path]]) -> Path | None:
    """The first link on the chain from ``start`` whose target names an old root; read-only."""
    current = _normal(start)
    for _ in range(_MAX_HOPS):
        if not current.is_symlink():
            return None
        try:
            hop = current.parent / _readlink(current)
        except OSError:
            return None
        if _renamed(hop, pairs) is not None:
            return current
        current = _normal(hop)
    return None


def _links(tree: Path) -> Iterable[Path]:
    """Every link under ``tree``, never descending a link, a ``.git`` directory or a venv."""
    for root, dirs, files in os.walk(tree):
        here = Path(root)
        if here != tree and (here / "pyvenv.cfg").exists():
            dirs.clear()
            continue
        keep = []
        for name in dirs:
            child = here / name
            if child.is_symlink() or _is_junction(child):
                yield child
            elif name != ".git":
                keep.append(name)
        dirs[:] = keep
        for name in files:
            child = here / name
            if child.is_symlink():
                yield child


def scan(trees: list[tuple[Path, Path]], pairs: list[tuple[Path, Path]]) -> list[StaleLink]:
    """Each link inside ``trees`` that names an old root of ``pairs``, or resolves through one outside.

    ``trees`` are ``(where the worktree is now, where it will be)``; ``pairs`` are each renamed
    root, ``(old, new)``. Read-only. A tree that does not exist is skipped.
    """
    now = [source for source, _dest in trees]
    found: list[StaleLink] = []
    for source, dest in trees:
        if not source.is_dir() or source.is_symlink():
            continue
        for link in _links(source):
            after = dest / link.relative_to(source)
            try:
                raw = _readlink(link)
            except OSError as exc:
                found.append(StaleLink(after, "", None, f"cannot read it: {exc}"))
                continue
            # A relative link is judged where it will be: one into a sibling moves with it.
            renamed = _renamed(after.parent / raw, pairs)
            if renamed is not None:
                if _is_junction(link):
                    found.append(StaleLink(after, raw, None, "a junction, which this tool cannot recreate"))
                    continue
                new_target = str(renamed)
                if not Path(raw).is_absolute():
                    try:
                        new_target = os.path.relpath(renamed, after.parent)
                    except ValueError:  # on another Windows drive: no relative form exists
                        new_target = str(renamed)
                if new_target != raw:
                    found.append(StaleLink(after, raw, new_target))
                continue
            absolute = link.parent / raw
            if _inside(absolute, now):
                continue  # a link inside the trees is examined on its own
            hop = _outside_hop(absolute, pairs)
            if hop is not None:
                found.append(
                    StaleLink(
                        after,
                        raw,
                        None,
                        f"it resolves through {hop}, outside the session's worktrees, which names an old-name path",
                    )
                )
    return found


def _relink(path: Path, target: str, new_target: str) -> None:
    """Repoint the link at ``path`` from ``target`` to ``new_target``, atomically where the platform allows."""
    if _readlink(path) != target:
        return  # rewritten since it was planned
    is_dir = (path.parent / new_target).is_dir()
    temporary = path.with_name(f".{path.name}.relink")
    if temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(new_target, target_is_directory=is_dir)
    try:
        temporary.replace(path)
    except OSError:
        # Windows refuses to replace a directory link in place: drop it, then move the new one in.
        path.unlink()
        temporary.replace(path)
    finally:
        if temporary.is_symlink():
            temporary.unlink()


def describe(links: list[StaleLink]) -> list[tuple[str, str, str, str]]:
    """``(kind, source, dest, action)`` for each planned link."""
    return [
        ("stale link", f"{link.path} -> {link.target}", link.new_target or "", "rewrite")
        if link.new_target is not None
        else ("stale link", f"{link.path} -> {link.target}", "", f"skipped: cannot rewrite: {link.reason}")
        for link in links
    ]


def apply(links: list[StaleLink]) -> list[str]:
    """Rewrite each fixable link in ``links``; a warning for each one that failed."""
    warnings = []
    for link in links:
        if link.new_target is None:
            continue
        try:
            _relink(link.path, link.target, link.new_target)
        except OSError as exc:
            warnings.append(f"stale link {link.path} FAILED to name {link.new_target}: {exc}")
    return warnings


def pairs_for(repos: Iterable[Path], slot_repo: Path, old: str, new: str) -> list[tuple[Path, Path]]:
    """Each root the migration renames: every repo's agents containers and the slot itself."""
    found: list[tuple[Path, Path]] = [(slot_repo / ".worktrees" / old, slot_repo / ".worktrees" / new)]
    for repo in repos:
        slots = repo / ".worktrees"
        found.append((slots / f"{old}-agents", slots / f"{new}-agents"))
        if slots.is_dir():
            for child in sorted(slots.iterdir()):
                if child.name.endswith(f"--{old}-agents"):
                    found.append((child, child.with_name(child.name[: -len(f"{old}-agents")] + f"{new}-agents")))
                elif child.name.endswith(f"--{new}-agents"):
                    old_name = child.name[: -len(f"{new}-agents")] + f"{old}-agents"
                    found.append((child.with_name(old_name), child))
    return list(dict.fromkeys(found))


def check(trees: Iterable[Path], pairs: list[tuple[Path, Path]]) -> list[str]:
    """A ``NOT fixed`` warning for each link inside ``trees`` still naming an old-name path, after a run."""
    return [
        f"stale link NOT fixed: {link.path} -> {link.target}"
        + (f" ({link.reason})" if link.reason else f" names an old-name path; it should name {link.new_target}")
        for link in scan([(tree, tree) for tree in dict.fromkeys(trees)], pairs)
    ]
