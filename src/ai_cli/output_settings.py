"""The ``[output]`` table of ``config.toml``: every log-line element's settings (AI-CLI-mu4d).

The shipped defaults (``data/output.toml``) hold every key. A user's ``[output]``
table is layered over them key by key and validated as a whole by
:mod:`ai_cli.output_validation`, which is imported only when such a table exists:
with none, records render from the shipped defaults without importing pydantic.
An invalid table is set aside whole: records render with the shipped defaults,
and each error names the config file and the dotted key. Output styling is never
a reason a command cannot run.

:func:`current` is called before every record renders. It costs one ``stat`` of
the config file and re-parses only when the file changed, so an edit takes effect
on the next line of a running process. This module never writes the file and
never prints: :mod:`ai_cli.output` reports the errors it returns, rendered with
the shipped defaults, so a broken table cannot affect its own error lines.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from .config import get_xdg_config_home

DEFAULTS_FILE = Path(__file__).parent / "data" / "output.toml"
ELEMENTS = ("datetime", "tag", "operation", "level", "description")


@dataclass(frozen=True)
class Element:
    enabled: bool
    prefix: str
    suffix: str
    style: str

    def wrap(self, value: str) -> str:
        return f"{self.prefix}{value}{self.suffix}"


@dataclass(frozen=True)
class DatetimeElement(Element):
    format: str
    timezone: str
    pattern: str


@dataclass(frozen=True)
class TagElement(Element):
    pattern: str
    styles: Mapping[str, str]


@dataclass(frozen=True)
class OperationElement(Element):
    required: bool
    default: str
    pattern: str
    vocabulary: Sequence[str]
    styles: Mapping[str, str]


@dataclass(frozen=True)
class LevelElement(Element):
    show: Sequence[str]
    styles: Mapping[str, str]


@dataclass(frozen=True)
class DescriptionElement(Element):
    pattern: str


@dataclass(frozen=True)
class Elements:
    datetime: DatetimeElement
    tag: TagElement
    operation: OperationElement
    level: LevelElement
    description: DescriptionElement


@dataclass(frozen=True)
class OutputSettings:
    order: Sequence[str]
    separator: str
    elements: Elements


def _settings(table: dict[str, Any]) -> OutputSettings:
    """Settings from a complete table: the shipped one, or a user's after validation."""
    elements = table["elements"]
    return OutputSettings(
        order=tuple(table["order"]),
        separator=table["separator"],
        elements=Elements(
            datetime=DatetimeElement(**elements["datetime"]),
            tag=TagElement(**elements["tag"]),
            operation=OperationElement(**elements["operation"]),
            level=LevelElement(**elements["level"]),
            description=DescriptionElement(**elements["description"]),
        ),
    )


@cache
def shipped_table() -> dict[str, Any]:
    return tomllib.loads(DEFAULTS_FILE.read_text(encoding="utf-8"))["output"]


@cache
def defaults() -> OutputSettings:
    """The shipped settings, which render whenever there is no valid user table."""
    return _settings(shipped_table())


def merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def config_path() -> Path:
    # Not migrated: reading output settings must never move the user's config directory.
    return get_xdg_config_home(migrate=False) / "config.toml"


@dataclass(frozen=True)
class _Loaded:
    settings: OutputSettings
    errors: tuple[str, ...]


def _load(path: Path) -> _Loaded:
    try:
        text = path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return _Loaded(defaults(), ())
    except (OSError, UnicodeDecodeError) as exc:
        return _Loaded(defaults(), (f"{path}: could not be read: {exc}",))
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return _Loaded(defaults(), (f"{path}: not valid TOML: {exc}",))
    if "output" not in data:
        return _Loaded(defaults(), ())
    # Only a user table pays for pydantic: the validator is imported here, never at module load.
    from .output_validation import validate

    table, errors = validate(data["output"])
    if table is None:
        return _Loaded(defaults(), tuple(f"{path}: {key}: {reason}" for key, reason in errors))
    return _Loaded(_settings(table), ())


def _identity(path: Path) -> tuple[object, ...] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return ("unreadable", exc.errno)
    return (stat.st_mtime_ns, stat.st_size, stat.st_ino)


_loaded_key: tuple[object, ...] | None = None
_loaded: _Loaded | None = None
_reported: tuple[str, ...] = ()


def current() -> tuple[OutputSettings, tuple[str, ...]]:
    """The settings for the next record, and any errors not yet reported for this table.

    The caller serialises calls (``ai_cli.output`` holds its lock). An error set is
    returned once per process, so a table that stays broken is reported once.
    """
    global _loaded_key, _loaded, _reported
    path = config_path()
    key = (str(path), _identity(path))
    if _loaded is None or key != _loaded_key:
        _loaded_key, _loaded = key, _load(path)
    if _loaded.errors == _reported:
        return _loaded.settings, ()
    _reported = _loaded.errors
    return _loaded.settings, _loaded.errors
