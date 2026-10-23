"""A new session's title when human names are enabled, and the settings that decide it.

A title is ``{repo-alias}-{host-alias}-[{label}-]{name}``: the human name takes the
place of the index. Two settings decide how a launch names its session, each resolved
on its own, first hit wins:

1. a flag on this launch: ``-S/--session-names`` or ``-I/--no-session-names``;
   ``-w/--with-name`` or ``-u/--no-name``
2. ``AI_CLI_SESSION_NAMES`` (``1`` or ``0``); ``AI_CLI_SESSION_NAME_WITH_LABEL``
   (``keep`` or ``drop``)
3. ``[session_names]`` in config.toml
4. the shared policy file's ``[hosts.<host id>]`` table
5. the shared policy file's ``[defaults]`` table
6. built in: names off, ``keep``

The two length limits are never per machine where a shared registry exists: they come
from the policy the registry publishes. Without one, they come from ``[session_names]``
in config.toml, then the built-in 3 and 4 (:func:`resolve_limits`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import config as _config
from . import name_list
from . import name_registry as reg

ENV_ENABLED = "AI_CLI_SESSION_NAMES"
ENV_WITH_LABEL = "AI_CLI_SESSION_NAME_WITH_LABEL"
WITH_LABEL_VALUES = ("keep", "drop")
BUILT_IN = {"enabled": False, "name_with_label": "keep"}
CLIENTS = {"c": "claude-code", "g": "gemini", "p": "pi", "cx": "codex"}


class TitleError(ValueError):
    """A launch request that names no valid title; the message says why. Exits 2."""


@dataclass(frozen=True)
class Setting:
    value: object
    layer: str


def _env_enabled(raw: str) -> bool:
    if raw not in ("1", "0"):
        raise TitleError(f"{ENV_ENABLED}={raw!r} is not valid: use 1 (human names) or 0 (index names)")
    return raw == "1"


def _env_with_label(raw: str) -> str:
    if raw not in WITH_LABEL_VALUES:
        raise TitleError(f"{ENV_WITH_LABEL}={raw!r} is not valid: use keep or drop")
    return raw


def _table_value(table: object, key: str, where: str) -> object | None:
    if not isinstance(table, dict) or key not in table:
        return None
    value = table[key]
    if key == "enabled" and not isinstance(value, bool):
        raise TitleError(f"enabled in {where} must be true or false, got {value!r}")
    if key == "name_with_label" and value not in WITH_LABEL_VALUES:
        raise TitleError(f"name_with_label in {where} must be keep or drop, got {value!r}")
    return value


def resolve_settings(
    *,
    enabled_flag: bool | None,
    with_label_flag: str | None,
    policy_config: dict | None,
    host: str,
    policy_source: str = "config/session-names.toml",
    environ: dict[str, str] | None = None,
) -> dict[str, Setting]:
    """``enabled`` and ``name_with_label``, each with the layer that set it."""
    env = os.environ if environ is None else environ
    local = _config.load_config().get("session_names", {})
    hosts = (policy_config or {}).get("hosts", {})
    host_table = hosts.get(host) if isinstance(hosts, dict) else None
    defaults = (policy_config or {}).get("defaults")
    flags = {"enabled": enabled_flag, "name_with_label": with_label_flag}
    parsers = {"enabled": _env_enabled, "name_with_label": _env_with_label}
    env_names = {"enabled": ENV_ENABLED, "name_with_label": ENV_WITH_LABEL}
    resolved: dict[str, Setting] = {}
    for key in ("enabled", "name_with_label"):
        if flags[key] is not None:
            resolved[key] = Setting(flags[key], "flag")
            continue
        raw = env.get(env_names[key], "")
        if raw:
            resolved[key] = Setting(parsers[key](raw), f"environment {env_names[key]}")
            continue
        for table, layer in (
            (local, f"{_config.config_file_text()} [session_names]"),
            (host_table, f"{policy_source} [hosts.{host}]"),
            (defaults, f"{policy_source} [defaults]"),
        ):
            value = _table_value(table, key, layer)
            if value is not None:
                resolved[key] = Setting(value, layer)
                break
        else:
            resolved[key] = Setting(BUILT_IN[key], "built-in")
    return resolved


@dataclass(frozen=True)
class Limits:
    low: int
    high: int
    layer: str
    warning: str | None = None


def resolve_limits(registry: reg.Registry, snapshot: reg.Snapshot | None = None) -> Limits:
    """The two length limits and where they came from; a local value that has no effect is warned about."""
    local = _config.load_config().get("session_names", {})
    local_keys = [key for key in ("min_name_length", "max_name_length") if isinstance(local, dict) and key in local]
    if isinstance(registry, reg.GitRegistry):
        policy = (snapshot or registry.snapshot(fetch=False)).policy
        low, high = name_list.limits(policy)
        warning = None
        if local_keys:
            warning = (
                f"{' and '.join(local_keys)} in {_config.config_file_text()} [session_names] has no effect: "
                f"the length limits are fleet-wide, set in {registry.policy_file} [limits]"
            )
        return Limits(low, high, f"published policy ({registry.policy_file} [limits])", warning)
    low, high = name_list.limits(reg.local_policy(registry))
    return Limits(low, high, f"{_config.config_file_text()} [session_names]" if local_keys else "built-in")


# --- composing the title ----------------------------------------------------------------


PICKED, CUSTOM, LABEL = "picked", "custom", "label"


def compose_kind(*, label: str | None, custom: str | None, flag: str | None, policy: str) -> str:
    """Which part supplies the title's last segment: a picked name, the ``--name`` value, or the label.

    ``flag`` is ``with`` (``--with-name``), ``no`` (``--no-name``) or None. This is the
    decision table AC-DT-1; each feasible combination matches exactly one rule.
    """
    if flag == "no" and custom:
        raise TitleError("--name conflicts with --no-name")
    if flag == "no" and not label:
        raise TitleError("--no-name needs a label: without one the title would have no last segment")
    if custom:
        return CUSTOM
    if not label:
        return PICKED
    if flag == "with" or (flag is None and policy == "keep"):
        return PICKED
    return LABEL


def label_last_token(label: str) -> str:
    return label.rsplit("-", 1)[-1]


def check_label(label: str, name_set: frozenset[str]) -> None:
    """Refuse a label whose last segment would read as a name or an index."""
    tail = label_last_token(label)
    if tail.isdigit():
        raise TitleError(
            f"the label {label!r} ends in {tail!r}, which reads as a session index; choose a label that does not "
            "end in digits"
        )
    if tail in name_set:
        raise TitleError(
            f"the label {label!r} ends in {tail!r}, which is a session name; choose a label such as "
            f"{label}-work, or pass --name {tail} to use the name"
        )


@dataclass(frozen=True)
class Positional:
    """What the positional argument asks for once human names are enabled."""

    kind: str  # "empty", "slot", "title" (reattach to a named title) or "label"
    value: str  # the cleaned slot, title tail or label
    name: str | None = None


def classify_positional(clean: str, name_set: frozenset[str]) -> Positional:
    """Read the positional argument, already stripped of any engine and stem prefix.

    Digits at the end keep the index meaning they always had. A last segment in the
    name set names an existing human-named title, which may only be reattached, never
    created this way: a name is chosen with ``--name``. Anything else is a label.
    """
    if not clean:
        return Positional("empty", "")
    tail = clean.rsplit("-", 1)[-1]
    if clean.isdigit() or tail.isdigit():
        return Positional("slot", clean)
    if tail in name_set:
        return Positional("title", clean, tail)
    return Positional("label", clean)


@dataclass(frozen=True)
class ParsedTitle:
    """A title split into its parts. Exactly one of ``name`` and ``index`` is set, or neither."""

    stem: str
    label: str | None
    name: str | None
    index: int | None


def parse_title(title: str, stem: str, name_set: frozenset[str]) -> ParsedTitle | None:
    """Split ``title`` under ``stem``, or None when it does not start with the stem.

    A digit last segment is an index. A last segment in the name set is the name, and
    what lies between the stem and it is the label. Anything else is all label: a
    dropped-name title, whose last token is reserved by a label row but is not a name.
    """
    if title != stem and not title.startswith(f"{stem}-"):
        return None
    rest = title[len(stem) + 1 :]
    if not rest:
        return ParsedTitle(stem, None, None, None)
    head, _, tail = rest.rpartition("-")
    if tail.isdigit():
        return ParsedTitle(stem, head or None, None, int(tail))
    if tail in name_set:
        return ParsedTitle(stem, head or None, tail, None)
    return ParsedTitle(stem, rest, None, None)
