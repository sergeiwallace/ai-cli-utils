"""Session names: ``{repo-alias}-{host-alias}-{optional-slug}-{N}``.

Every name ``ai c`` gives a session -- its ai_name, worktree directory, transcript
title and pinned task namespace -- is built by :func:`compose_session_name`, so
``myrepo-box-1`` and ``myrepo-box-review-1`` come from one place and an absent slug
can never leave a double hyphen behind.

The host alias is resolved at call time from the registries :func:`alias_sources`
lists, in this order:

1. ``<projects>/ai-harness/config/machine-host-aliases.toml`` -- the fleet's
   git-tracked registry, when that checkout exists (``$PROJECTS_DIR`` first, then
   ``[project] projects_dir``, default ``~/projects``). When it names the host, no
   other file is read.
2. Every other sibling repository's ``config/machine-host-aliases.toml``, in
   directory-name order. A registry joins by shipping the file, so this package never
   names the repository that holds it. Two of them naming the host differently is
   refused, never resolved by picking one.
3. ``<config home>/machine-host-aliases.toml`` -- a local file for machines with no
   such checkout.

All use one shape::

    max_length = 5

    [aliases]
    my-host = "box"

There is no guessed fallback. On a host none of them names, a launch with a terminal
attached asks for the alias and appends it to the first registry that exists
(:func:`prompt_for_host_alias`); without a terminal it is refused with the paths to
edit, because a name invented here would become a worktree directory, a branch and a
transcript title, and renaming those later is a migration rather than an edit.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
import tomllib
from pathlib import Path

from . import config as _config
from . import output as _out
from .output import Tag

ALIAS_FILENAME = "machine-host-aliases.toml"
ALIAS_REGISTRY_MARKER = Path("config") / ALIAS_FILENAME
HARNESS_REPO = "ai-harness"
DEFAULT_MAX_LENGTH = 5
PREFERRED_LENGTH = 3

# Lowercase letters and digits, starting with a letter (``acn``, ``ec2``). Every name
# parser splits the index off the LAST hyphen, so a digit inside the alias segment is
# never read as an index; a leading letter keeps an alias from being all digits.
_ALIAS_RE = re.compile(r"[a-z][a-z0-9]*")
# A host id that is a TOML bare key is written bare; anything else (a hostname with a
# dot would otherwise be read as a dotted key) is written as a quoted key.
_BARE_KEY_RE = re.compile(r"[A-Za-z0-9_-]+")
_ALIASES_HEADER_RE = re.compile(r"\s*\[aliases\]\s*(#.*)?")


class HostAliasError(RuntimeError):
    """No usable host alias for this machine; the message names the files to edit."""


class HostAliasNotConfiguredError(HostAliasError):
    """No registry names this machine at all: the one case a terminal prompt can answer."""


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


def _projects_roots() -> list[Path]:
    """``$PROJECTS_DIR`` then the configured projects dir, once each even through a symlink."""
    candidates: list[Path] = []
    env_projects = os.environ.get("PROJECTS_DIR", "").strip()
    if env_projects:
        candidates.append(Path(env_projects).expanduser())
    candidates.append(_config._get_projects_dir())
    roots: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            roots.append(candidate)
    return roots


def _harness_registries(roots: list[Path]) -> list[Path]:
    return [root / HARNESS_REPO / ALIAS_REGISTRY_MARKER for root in roots if (root / HARNESS_REPO).is_dir()]


def _sibling_registries(roots: list[Path]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for repo in sorted(root.iterdir(), key=lambda entry: entry.name):
            candidate = repo / ALIAS_REGISTRY_MARKER
            if repo.name != HARNESS_REPO and candidate.is_file() and candidate not in found:
                found.append(candidate)
    return found


def _local_registry() -> Path:
    return _config.get_xdg_config_home() / ALIAS_FILENAME


def alias_sources() -> list[Path]:
    """Alias files in resolution order: ai-harness's, every other sibling's, then the local one."""
    roots = _projects_roots()
    return [*_harness_registries(roots), *_sibling_registries(roots), _local_registry()]


def _read_registry(path: Path) -> dict | None:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise HostAliasError(f"cannot read host alias file {path}: {exc}") from exc


def _max_length(data: dict | None, path: Path) -> int:
    max_length = (data or {}).get("max_length", DEFAULT_MAX_LENGTH)
    if not isinstance(max_length, int) or isinstance(max_length, bool) or max_length < 1:
        raise HostAliasError(f"max_length in {path} must be a positive integer, got {max_length!r}")
    return max_length


def _alias_problem(alias: object, max_length: int) -> str | None:
    """Why ``alias`` is not a usable host alias under ``max_length``, None when it is."""
    if not isinstance(alias, str) or not _ALIAS_RE.fullmatch(alias):
        return "it must be lowercase letters and digits starting with a letter"
    if len(alias) > max_length:
        return f"it is {len(alias)} characters and the limit is {max_length}"
    return None


def _alias_from(path: Path, host: str) -> str | None:
    """The alias ``path`` assigns ``host``, None when it names no alias for it."""
    data = _read_registry(path)
    if data is None:
        return None
    aliases = data.get("aliases", {})
    alias = aliases.get(host) if isinstance(aliases, dict) else None
    if alias is None:
        return None
    max_length = _max_length(data, path)
    if _alias_problem(alias, max_length):
        raise HostAliasError(
            f"host alias {alias!r} for {host!r} in {path} is invalid: it must be lowercase "
            f"letters and digits starting with a letter, at most {max_length} characters"
        )
    return alias


def _not_configured(host: str, sources: list[Path]) -> HostAliasNotConfiguredError:
    checked = "\n".join(f"    {source}" for source in sources)
    return HostAliasNotConfiguredError(
        f"no host alias is configured for this machine (host id {host!r}).\n"
        "  Session names are {repo-alias}-{host-alias}-{optional-slug}-{N}, so a launch needs one.\n"
        f"  Checked, in order:\n{checked}\n"
        f"  Fix: add it under [aliases] in {sources[0]}:\n"
        f'    {host} = "<alias>"   # lowercase letters and digits, starting with a letter, at most {DEFAULT_MAX_LENGTH} characters'
    )


def _resolve_from(host: str, sources: list[Path]) -> str:
    """The alias the first naming source gives ``host``, refusing a sibling conflict.

    A hit in the ai-harness registry or the local file is returned at once. A hit in a
    sibling registry is compared with every later sibling first: two repositories that
    disagree about a machine cannot both be right, and picking one is how a session
    gets named under the wrong alias.
    """
    roots = _projects_roots()
    authoritative = {*_harness_registries(roots), _local_registry()}
    for index, source in enumerate(sources):
        alias = _alias_from(source, host)
        if alias is None:
            continue
        if source in authoritative:
            return alias
        rivals = []
        for other in sources[index + 1 :]:
            if other in authoritative:
                continue
            other_alias = _alias_from(other, host)
            if other_alias is not None and other_alias != alias:
                rivals.append(f'    {other}: {host} = "{other_alias}"')
        if rivals:
            listed = "\n".join([f'    {source}: {host} = "{alias}"', *rivals])
            raise HostAliasError(
                f"host alias registries disagree about this machine (host id {host!r}):\n{listed}\n"
                "  Remove the wrong entry so exactly one alias remains."
            )
        return alias
    raise _not_configured(host, sources)


def resolve_host_alias() -> str:
    """Return this machine's host alias from the first source that names it.

    Raises :class:`HostAliasNotConfiguredError` naming every file checked, in order,
    when none does, and :class:`HostAliasError` for an invalid entry or a conflict.
    """
    return _resolve_from(host_id(), alias_sources())


def _in_git_checkout(path: Path) -> Path | None:
    for candidate in path.resolve().parents:
        if _config._has_valid_git_marker(candidate):
            return candidate
    return None


def _render_entry(host: str, alias: str) -> str:
    key = host if _BARE_KEY_RE.fullmatch(host) else json.dumps(host)
    return f'{key} = "{alias}"\n'


def _with_alias_appended(text: str, entry: str, newline: str = "\n") -> str:
    """``text`` with ``entry`` after the last ``key = value`` line of its ``[aliases]`` table.

    After the table's last entry, not after its header, so a table that follows
    ``[aliases]`` in the file never receives the line. A file with no such table gets
    one appended.
    """
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += newline
    header = next((i for i, line in enumerate(lines) if _ALIASES_HEADER_RE.fullmatch(line.rstrip("\r\n"))), None)
    if header is None:
        separator = [newline] if lines and lines[-1].strip() else []
        return "".join([*lines, *separator, f"[aliases]{newline}", entry])
    end = next((i for i in range(header + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    insert_at = header + 1
    for i in range(header + 1, end):
        stripped = lines[i].strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            insert_at = i + 1
    return "".join([*lines[:insert_at], entry, *lines[insert_at:]])


def write_host_alias(path: Path, host: str, alias: str) -> None:
    """Append ``host = "alias"`` to the registry at ``path``, or refuse leaving it unchanged.

    The new text is written beside the file, parsed with ``tomllib`` and the host
    re-resolved from it; only when that returns ``alias`` does it replace the file. A
    missing registry is created with ``max_length`` and an ``[aliases]`` table.
    """
    if path.is_file():
        original = path.read_bytes().decode("utf-8")
    else:
        original = f"max_length = {DEFAULT_MAX_LENGTH}\n\n[aliases]\n"
    newline = "\r\n" if "\r\n" in original else "\n"
    updated = _with_alias_appended(original, _render_entry(host, alias).replace("\n", newline), newline)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".machine-host-aliases-", suffix=".toml", dir=path.parent)
    staged = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(updated)
        try:
            resolved = _alias_from(staged, host)
        except HostAliasError as exc:
            raise HostAliasError(
                f"refusing to write {path}: the edited file would not parse ({exc}); it is unchanged"
            ) from exc
        if resolved != alias:
            raise HostAliasError(
                f"refusing to write {path}: the edited file resolves {host!r} to {resolved!r}, "
                f"not {alias!r}; it is unchanged"
            )
        staged.replace(path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            staged.unlink()
        raise


def prompt_for_host_alias(*, dry_run: bool = False) -> str:
    """Ask for this machine's alias, append it to the first existing registry, return it.

    The target is the first file :func:`alias_sources` lists that exists, else the
    local file, created. ``dry_run`` validates the answer and reports the write it
    would make without touching any file.
    """
    host = host_id()
    sources = alias_sources()
    target = next((source for source in sources if source.is_file()), sources[-1])
    max_length = _max_length(_read_registry(target), target)
    _out.emit(
        Tag.CONFIG,
        f"no host alias is configured for this machine (host id {host!r}).\n"
        "Session names are {repo-alias}-{host-alias}-{optional-slug}-{N}, so a launch needs one.\n"
        f"Checked: {', '.join(str(source) for source in sources)}",
        err=True,
    )
    answer = _out.ask(
        Tag.CONFIG,
        f"Host alias for {host} (lowercase letters and digits, starting with a letter; prefer "
        f"{PREFERRED_LENGTH} or fewer, at most {max_length}; the limit is max_length in {target}):",
        err=True,
    ).strip()
    problem = "nothing was entered" if not answer else _alias_problem(answer, max_length)
    if problem:
        raise HostAliasError(
            f"host alias {answer!r} refused: {problem}. Nothing was written. Run again, or add "
            f'{host} = "<alias>" under [aliases] in {target}.'
        )
    if dry_run:
        _out.emit(Tag.CONFIG, f'dry run: would write {host} = "{answer}" to {target}; nothing was written.', err=True)
        return answer
    write_host_alias(target, host, answer)
    message = f'Wrote {host} = "{answer}" to {target}.'
    if _in_git_checkout(target) is not None:
        message += " It is git-tracked: commit and push it so every machine sees it."
    _out.emit(Tag.CONFIG, message, err=True)
    return answer


def resolve_or_prompt_host_alias(*, dry_run: bool = False) -> str:
    """:func:`resolve_host_alias`, asking for the alias when a terminal can answer.

    Only the not-configured case prompts; an invalid entry or a registry conflict is
    a fault in a file, which an answer typed here cannot fix. Without a terminal the
    refusal is the same one :func:`resolve_host_alias` raises.
    """
    try:
        return resolve_host_alias()
    except HostAliasNotConfiguredError:
        if not _config._can_prompt():
            raise
    return prompt_for_host_alias(dry_run=dry_run)
