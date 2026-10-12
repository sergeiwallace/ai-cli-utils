"""Session names: ``{repo-alias}-{host-alias}-{optional-slug}-{N}``.

Every name ``ai c`` gives a session -- its ai_name, worktree directory, transcript
title and pinned task namespace -- is built by :func:`compose_session_name`, so
``myrepo-box-1`` and ``myrepo-box-review-1`` come from one place and an absent slug
can never leave a double hyphen behind.

The host alias is resolved at call time from the first source that names this
machine (see :func:`alias_sources`):

1. ``<projects>/ai-harness/config/machine-host-aliases.toml`` -- the fleet's
   git-tracked registry, when that checkout exists (``$PROJECTS_DIR`` first, then
   ``[project] projects_dir``, default ``~/projects``).
2. ``<config home>/machine-host-aliases.toml`` -- a local file for machines with no
   such checkout.

Both use one shape::

    max_length = 5

    [aliases]
    my-host = "box"

There is no guessed fallback. An unconfigured host is refused with the paths to
edit, because a name invented here would become a worktree directory, a branch and
a transcript title, and renaming those later is a migration rather than an edit.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

from . import config as _config

ALIAS_FILENAME = "machine-host-aliases.toml"
DEFAULT_MAX_LENGTH = 5

# Lowercase letters and digits, starting with a letter (``acn``, ``ec2``). Every name
# parser splits the index off the LAST hyphen, so a digit inside the alias segment is
# never read as an index; a leading letter keeps an alias from being all digits.
_ALIAS_RE = re.compile(r"[a-z][a-z0-9]*")


class HostAliasError(RuntimeError):
    """No usable host alias for this machine; the message names the files to edit."""


def compose_session_name(*segments: str | int | None) -> str:
    """Join name segments with single hyphens, skipping empty ones.

    ``compose_session_name("myrepo", "box", None, 1)`` is ``myrepo-box-1``, never
    ``myrepo-box--1``: a missing optional slug drops out instead of leaving its
    separator behind. Segments are lowercased and stripped of edge hyphens.
    """
    parts = [str(segment).strip("-").lower() for segment in segments if segment is not None]
    return "-".join(part for part in parts if part)


def naming_prefix(repo_alias: str, host_alias: str) -> str:
    """The ``{repo-alias}-{host-alias}`` stem every session name of this repo starts with."""
    return compose_session_name(repo_alias, host_alias)


def host_id() -> str:
    """This machine's identity, as the launcher's machine profile detects it."""
    return _config.detect_machine_profile()["host_id"]


def alias_sources() -> list[Path]:
    """Alias files in resolution order: the ai-harness checkout's, then the local one."""
    roots: list[Path] = []
    env_projects = os.environ.get("PROJECTS_DIR", "").strip()
    if env_projects:
        roots.append(Path(env_projects).expanduser())
    roots.append(_config._get_projects_dir())
    sources: list[Path] = []
    for root in roots:
        harness = root / "ai-harness"
        candidate = harness / "config" / ALIAS_FILENAME
        if harness.is_dir() and candidate not in sources:
            sources.append(candidate)
    sources.append(_config.get_xdg_config_home() / ALIAS_FILENAME)
    return sources


def _alias_from(path: Path, host: str) -> str | None:
    """The alias ``path`` assigns ``host``, None when it names no alias for it."""
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise HostAliasError(f"cannot read host alias file {path}: {exc}") from exc
    aliases = data.get("aliases", {})
    alias = aliases.get(host) if isinstance(aliases, dict) else None
    if alias is None:
        return None
    max_length = data.get("max_length", DEFAULT_MAX_LENGTH)
    if (
        not isinstance(alias, str)
        or not _ALIAS_RE.fullmatch(alias)
        or not isinstance(max_length, int)
        or len(alias) > max_length
    ):
        raise HostAliasError(
            f"host alias {alias!r} for {host!r} in {path} is invalid: it must be lowercase "
            f"letters and digits starting with a letter, at most {max_length} characters"
        )
    return alias


def resolve_host_alias() -> str:
    """Return this machine's host alias from the first source that names it.

    Raises :class:`HostAliasError` naming every file checked when none does.
    """
    host = host_id()
    sources = alias_sources()
    for source in sources:
        alias = _alias_from(source, host)
        if alias:
            return alias
    checked = "\n".join(f"    {source}" for source in sources)
    raise HostAliasError(
        f"no host alias is configured for this machine (host id {host!r}).\n"
        "  Session names are {repo-alias}-{host-alias}-{optional-slug}-{N}, so a launch needs one.\n"
        f"  Checked, in order:\n{checked}\n"
        f"  Fix: add it under [aliases] in {sources[0]}:\n"
        f'    {host} = "<alias>"   # lowercase letters and digits, starting with a letter, at most {DEFAULT_MAX_LENGTH} characters'
    )
