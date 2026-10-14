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

import os
import pkgutil
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from typing import Any

from .config import config_file_text

# Paths here are text, never pathlib: a record must render even while a caller has
# patched os.name, under which pathlib.Path cannot be constructed.
DEFAULTS_RESOURCE = "data/output.toml"
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
class ReloadProgress:
    warn_after: int
    error_after: int


@dataclass(frozen=True)
class Progress:
    reload: ReloadProgress


@dataclass(frozen=True)
class Sink:
    enabled: bool


@dataclass(frozen=True)
class LevelledSink(Sink):
    min_level: str


@dataclass(frozen=True)
class Sinks:
    terminal: Sink
    file: LevelledSink
    telemetry: LevelledSink


@dataclass(frozen=True)
class OutputSettings:
    order: Sequence[str]
    separator: str
    elements: Elements
    progress: Progress
    sinks: Sinks


def _settings(table: dict[str, Any]) -> OutputSettings:
    """Settings from a complete table: the shipped one, or a user's after validation."""
    elements, sinks = table["elements"], table["sinks"]
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
        progress=Progress(reload=ReloadProgress(**table["progress"]["reload"])),
        sinks=Sinks(
            terminal=Sink(**sinks["terminal"]),
            file=LevelledSink(**sinks["file"]),
            telemetry=LevelledSink(**sinks["telemetry"]),
        ),
    )


@cache
def shipped_table() -> dict[str, Any]:
    data = pkgutil.get_data("ai_cli", DEFAULTS_RESOURCE)
    if data is None:
        raise FileNotFoundError(f"ai_cli/{DEFAULTS_RESOURCE} is missing from the installed package")
    return tomllib.loads(data.decode("utf-8"))["output"]


@cache
def shipped_vocabulary() -> frozenset[str]:
    """The shipped operations: every one the package emits, which no user table can remove."""
    return frozenset(shipped_table()["elements"]["operation"]["vocabulary"])


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


def config_path() -> str:
    return config_file_text()


@dataclass(frozen=True)
class _Loaded:
    settings: OutputSettings
    errors: tuple[str, ...]
    # `[telemetry] enabled`, read from the same parse: the telemetry sink's off switch.
    telemetry_enabled: bool = True


def _telemetry_enabled(data: dict[str, Any]) -> bool:
    telemetry = data.get("telemetry")
    return not (isinstance(telemetry, dict) and telemetry.get("enabled") is False)


def _load(path: str) -> _Loaded:
    try:
        with open(path, "rb") as config_file:  # noqa: PTH123 -- text path, see DEFAULTS_RESOURCE
            text = config_file.read().decode("utf-8")
    except FileNotFoundError:
        return _Loaded(defaults(), ())
    except (OSError, UnicodeDecodeError) as exc:
        return _Loaded(defaults(), (f"{path}: could not be read: {exc}",))
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return _Loaded(defaults(), (f"{path}: not valid TOML: {exc}",))
    telemetry_enabled = _telemetry_enabled(data)
    if "output" not in data:
        return _Loaded(defaults(), (), telemetry_enabled)
    # Only a user table pays for pydantic: the validator is imported here, never at module load.
    from .output_validation import validate

    table, errors = validate(data["output"])
    if table is None:
        return _Loaded(defaults(), tuple(f"{path}: {key}: {reason}" for key, reason in errors), telemetry_enabled)
    return _Loaded(_settings(table), (), telemetry_enabled)


def _identity(path: str) -> tuple[object, ...] | None:
    try:
        stat = os.stat(path)  # noqa: PTH116 -- text path, see DEFAULTS_RESOURCE
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
    global _reported
    loaded = _refresh()
    if loaded.errors == _reported:
        return loaded.settings, ()
    _reported = loaded.errors
    return loaded.settings, loaded.errors


def telemetry_enabled() -> bool:
    """``[telemetry] enabled`` in the same file, at the same one-``stat`` cost; true unless set false."""
    return _refresh().telemetry_enabled


def _refresh() -> _Loaded:
    global _loaded_key, _loaded
    path = config_path()
    key = (path, _identity(path))
    if _loaded is None or key != _loaded_key:
        _loaded_key, _loaded = key, _load(path)
    return _loaded
