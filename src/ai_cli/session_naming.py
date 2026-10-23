"""Session names: ``{repo-alias}-{host-alias}-{optional-slug}-{N}``, or a human name in place of ``{N}``.

Every name ``ai c`` gives a session -- its ai_name, worktree directory, transcript
title and pinned task namespace -- is built by :func:`compose_session_name`, so
``myrepo-box-1`` and ``myrepo-box-review-1`` come from one place and an absent slug
can never leave a double hyphen behind.

The host alias is resolved at call time from the registries :func:`alias_sources`
lists, in this order:

1. ``<projects>/<environment repo>/config/machine-host-aliases.toml`` -- the
   environment's git-tracked registry, when ``[project] environment_repo`` names a
   checkout that exists (``$PROJECTS_DIR`` first, then ``[project] projects_dir``,
   default ``~/projects``). When it names the host, no other file is read.
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

The last part of this module picks the human first name that replaces ``{N}`` when
human names are enabled (:func:`pick_name`).
"""

from __future__ import annotations

import contextlib
import json
import os
import random
import re
import tempfile
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from . import config as _config
from . import output as _out
from .name_list import one_edit_apart
from .output import Tag

ALIAS_FILENAME = "machine-host-aliases.toml"
ALIAS_REGISTRY_MARKER = Path("config") / ALIAS_FILENAME
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
# A human name segment: lowercase ASCII letters only, so it can never read as an index.
_NAME_SEGMENT_RE = re.compile(r"[a-z]+")


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


def _environment_registries(roots: list[Path]) -> list[Path]:
    repo = _config.get_environment_repo()
    if not repo:
        return []
    return [root / repo / ALIAS_REGISTRY_MARKER for root in roots if (root / repo).is_dir()]


def _sibling_registries(roots: list[Path]) -> list[Path]:
    environment_repo = _config.get_environment_repo()
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for repo in sorted(root.iterdir(), key=lambda entry: entry.name):
            candidate = repo / ALIAS_REGISTRY_MARKER
            if repo.name != environment_repo and candidate.is_file() and candidate not in found:
                found.append(candidate)
    return found


def _local_registry() -> Path:
    return _config.get_xdg_config_home() / ALIAS_FILENAME


def alias_sources() -> list[Path]:
    """Alias files in resolution order: the environment repo's, every other sibling's, then the local one."""
    roots = _projects_roots()
    return [*_environment_registries(roots), *_sibling_registries(roots), _local_registry()]


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

    A hit in the environment repo's registry or the local file is returned at once. A hit in a
    sibling registry is compared with every later sibling first: two repositories that
    disagree about a machine cannot both be right, and picking one is how a session
    gets named under the wrong alias.
    """
    roots = _projects_roots()
    authoritative = {*_environment_registries(roots), _local_registry()}
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
        "config",
        f"no host alias is configured for this machine (host id {host!r}).\n"
        "Session names are {repo-alias}-{host-alias}-{optional-slug}-{N}, so a launch needs one.\n"
        f"Checked: {', '.join(str(source) for source in sources)}",
        err=True,
    )
    answer = _out.ask(
        Tag.CONFIG,
        "config",
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
        _out.emit(
            Tag.CONFIG,
            "config",
            f'dry run: would write {host} = "{answer}" to {target}; nothing was written.',
            err=True,
        )
        return answer
    write_host_alias(target, host, answer)
    message = f'Wrote {host} = "{answer}" to {target}.'
    if _in_git_checkout(target) is not None:
        message += " It is git-tracked: commit and push it so every machine sees it."
    _out.emit(Tag.CONFIG, "config", message, err=True)
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


# --- Picking a human name -------------------------------------------------------------
#
# The last segment of a new session's name is a short human first name. A pick takes
# the shortest length that still has a free candidate, then prefers names sharing the
# repo alias's letters, then chooses at random. Everything that depends on what is held
# right now (the held names, the titles they make, the aliases in use) is passed in, so
# the same function serves a launch, a migration and a dry run.


class NamePoolExhaustedError(RuntimeError):
    """No candidate survives the filters; the message names each filter and what it removed."""


@dataclass(frozen=True)
class NamePick:
    """One picked name, and what the picker saw when it chose it."""

    name: str
    tier: str
    run_size: int
    free_at_length: int
    free_total: int


@dataclass(frozen=True)
class PickRequest:
    """Everything a pick depends on.

    ``title_prefix`` is the new title without its name (``myrepo-box-`` or
    ``myrepo-box-review-``); ``stem`` is ``{repo-alias}-{host-alias}``. ``held`` is every
    token the registry and this machine's spool hold; ``held_titles`` their titles.
    ``manual`` is the names added by hand, a candidate only when ``passes_static`` says so.
    """

    generated: frozenset[str]
    held: frozenset[str]
    held_titles: frozenset[str]
    stem: str
    title_prefix: str
    repo_alias: str
    min_length: int
    max_length: int
    letters: frozenset[str]
    pick_floor: int
    reserved_words: frozenset[str] = frozenset()
    denylist: frozenset[str] = frozenset()
    manual: frozenset[str] = frozenset()
    passes_static: Callable[[str], bool] | None = None
    max_edit_distance: int = 1


def alias_letters(repo_alias: str) -> frozenset[str]:
    """The distinct letters of ``repo_alias`` (``my-app`` gives a, m, p, y)."""
    return frozenset(char for char in repo_alias.lower() if "a" <= char <= "z")


def resolve_letters(repo_alias: str, include: Iterable[str] | None, source: str) -> tuple[frozenset[str], str | None]:
    """The letter set the picker uses, and a warning when the configured one cannot be used.

    A configured include set is used only when every entry is one of the alias's own
    letters; otherwise the alias's letters are used and the warning names ``source``,
    the file the install-time check fails on.
    """
    own = alias_letters(repo_alias)
    if include is None:
        return own, None
    configured = frozenset(include)
    stray = sorted(letter for letter in configured if letter not in own)
    if not configured or stray:
        return own, (
            f"the letters configured for {repo_alias!r} in {source} include {', '.join(stray) or 'nothing'}, "
            f"outside the alias's letters ({', '.join(sorted(own))}); using the alias's letters"
        )
    return configured, None


def _tier_key(name: str, letters: frozenset[str], first: str) -> tuple[int, bool]:
    return len(set(name) & letters), name.startswith(first)


def _tier_label(name: str, letters: frozenset[str], first: str) -> str:
    contained, starts = _tier_key(name, letters, first)
    return f"len{len(name)}/{contained}/{'first' if starts else 'other'}"


def _prefix_pair(title: str, held_titles: Iterable[str], stem: str) -> bool:
    same_stem = f"{stem}-"
    return any(
        held != title and held.startswith(same_stem) and (held.startswith(title) or title.startswith(held))
        for held in held_titles
    )


def name_candidates(request: PickRequest) -> tuple[list[str], dict[str, int]]:
    """The free candidates for ``request``, and how many each filter removed."""
    removed = dict.fromkeys(
        (
            "outside the length limits",
            "denylisted",
            "added by hand and failing the static filters",
            "held",
            f"within {request.max_edit_distance} edit of a held name",
            "equal to a repo or host alias",
            "a strict-prefix title pair with a held title",
        ),
        0,
    )
    keys = list(removed)
    manual = {name for name in request.manual if name not in request.generated}
    candidates: list[str] = []
    for name in sorted(request.generated | manual):
        if not _NAME_SEGMENT_RE.fullmatch(name) or not request.min_length <= len(name) <= request.max_length:
            removed[keys[0]] += 1
        elif name in request.denylist:
            removed[keys[1]] += 1
        elif name in manual and not (request.passes_static and request.passes_static(name)):
            removed[keys[2]] += 1
        elif name in request.held:
            removed[keys[3]] += 1
        elif request.max_edit_distance and any(one_edit_apart(name, held) for held in request.held):
            removed[keys[4]] += 1
        elif name in request.reserved_words:
            removed[keys[5]] += 1
        elif _prefix_pair(f"{request.title_prefix}{name}", request.held_titles, request.stem):
            removed[keys[6]] += 1
        else:
            candidates.append(name)
    return candidates, removed


def pick_name(request: PickRequest, rng: random.Random | None = None) -> NamePick:
    """Pick a free name: the shortest length first, then the letter ladder, then at random.

    Within the shortest length that has a candidate, candidates are ranked by how many
    of ``letters`` they contain and then by starting with the alias's first letter. The
    pick is uniform over the smallest run of top tiers holding at least ``pick_floor``
    candidates, or over every candidate of that length when no run reaches it. The run
    never reaches a longer length.
    """
    candidates, removed = name_candidates(request)
    if not candidates:
        considered = len(request.generated | request.manual)
        reasons = "; ".join(f"{reason}: {count}" for reason, count in removed.items() if count)
        raise NamePoolExhaustedError(
            f"no free session name: all {considered} names in the database were filtered out ({reasons or 'none'})"
        )
    shortest = min(len(name) for name in candidates)
    at_length = [name for name in candidates if len(name) == shortest]
    first = next((char for char in request.repo_alias.lower() if "a" <= char <= "z"), "")
    ranked = sorted({_tier_key(name, request.letters, first) for name in at_length}, reverse=True)
    run: list[str] = []
    for tier in ranked:
        run += [name for name in at_length if _tier_key(name, request.letters, first) == tier]
        if len(run) >= request.pick_floor:
            break
    chosen = (rng or random.SystemRandom()).choice(sorted(run))
    return NamePick(
        name=chosen,
        tier=_tier_label(chosen, request.letters, first),
        run_size=len(run),
        free_at_length=len(at_length),
        free_total=len(candidates),
    )


def low_water_warning(pick: NamePick, low_water: int) -> str | None:
    """The one warning a launch prints when the free pool is running low."""
    if pick.free_total >= low_water:
        return None
    return (
        f"only {pick.free_total} free session names remain (warning below {low_water}); "
        "see `ai list-name-candidates`, and raise max_name_length to widen the pool"
    )
