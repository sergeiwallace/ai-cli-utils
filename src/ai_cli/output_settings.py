"""The ``[output]`` table of ``config.toml``: every log-line element's settings (AI-CLI-mu4d).

The shipped defaults (``data/output.toml``) hold every key. A user's ``[output]``
table is layered over them key by key and the result is validated as a whole. An
invalid table is set aside whole: records render with the shipped defaults, and
each error names the config file and the dotted key. Output styling is never a
reason a command cannot run.

:func:`current` is called before every record renders. It costs one ``stat`` of
the config file and re-parses only when the file changed, so an edit takes effect
on the next line of a running process. This module never writes the file and
never prints: :mod:`ai_cli.output` reports the errors it returns, rendered with
the shipped defaults, so a broken table cannot affect its own error lines.
"""

from __future__ import annotations

import datetime as dt
import re
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, ValidationError, ValidationInfo
from pydantic_core import ErrorDetails

from .config import get_xdg_config_home
from .output import Level, Tag, check_style

DEFAULTS_FILE = Path(__file__).parent / "data" / "output.toml"
ELEMENTS = ("datetime", "tag", "operation", "level", "description")
# strftime directives that render the same on every platform's C library.
DATETIME_DIRECTIVES = frozenset("aAbBcdfHIjmMpSUwWxXyYzZ%")
# One winter and one summer instant, so a format is checked under both DST abbreviations.
_SAMPLE_TIMES = (dt.datetime(2026, 1, 15, 18, 30, tzinfo=dt.UTC), dt.datetime(2026, 7, 1, 18, 30, tzinfo=dt.UTC))


def _checking(info: ValidationInfo) -> bool:
    # The shipped defaults are validated without the checks (tests prove they pass), so
    # rendering without a user table never imports Rich's style parser on a pipe.
    return bool(info.context and info.context.get("check"))


def _style(value: str, info: ValidationInfo) -> str:
    if _checking(info):
        check_style(value)
    return value


def _regex(value: str, info: ValidationInfo) -> str:
    if _checking(info):
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"{value!r} is not a valid regular expression: {exc}") from None
    return value


def _zone(name: str) -> dt.tzinfo | None:
    """The zone ``name`` names: ``UTC``, ``local`` (None: the host's zone) or an IANA name."""
    if name == "UTC":
        return dt.UTC
    if name == "local":
        return None
    # Imported here: without a user table no zone is ever resolved, so no record pays for it.
    from zoneinfo import ZoneInfo

    return ZoneInfo(name)


def _timezone(value: str, info: ValidationInfo) -> str:
    if _checking(info):
        try:
            _zone(value)
        except (KeyError, ValueError):  # ZoneInfoNotFoundError is a KeyError
            raise ValueError(f"{value!r} is not UTC, local or a known IANA time zone") from None
    return value


def _datetime_format(value: str, info: ValidationInfo) -> str:
    if not _checking(info):
        return value
    unsupported = sorted({f"%{char}" for char in re.findall(r"%(.?)", value)} - {f"%{c}" for c in DATETIME_DIRECTIVES})
    if unsupported:
        raise ValueError(f"unsupported directive {', '.join(unsupported)} in {value!r}")
    return value


def _not_supported_yet(value: bool, info: ValidationInfo) -> bool:
    # Refused until the element's own task lands: the datetime element (AI-CLI-fx4b) and the
    # operation enforcement (AI-CLI-rbuf). Each removes its refusal when it ships.
    if _checking(info) and value:
        raise ValueError("true is not supported yet; this release renders it as false")
    return value


def _one_of(allowed: tuple[str, ...], what: str) -> Callable[[str], str]:
    def check(value: str) -> str:
        if value not in allowed:
            raise ValueError(f"{value!r} is not a known {what}; {what}s are {', '.join(allowed)}")
        return value

    return check


Style = Annotated[str, AfterValidator(_style)]
Regex = Annotated[str, AfterValidator(_regex)]
LEVELS = tuple(level.value for level in Level)
LevelName = Annotated[str, AfterValidator(_one_of(LEVELS, "level"))]
ElementName = Annotated[str, AfterValidator(_one_of(ELEMENTS, "element"))]


class _Table(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Element(_Table):
    enabled: bool
    prefix: str
    suffix: str
    style: Style

    def wrap(self, value: str) -> str:
        return f"{self.prefix}{value}{self.suffix}"


class DatetimeElement(Element):
    enabled: Annotated[bool, AfterValidator(_not_supported_yet)]
    format: Annotated[str, AfterValidator(_datetime_format)]
    timezone: Annotated[str, AfterValidator(_timezone)]
    pattern: Regex


class TagElement(Element):
    pattern: Regex
    styles: dict[str, Style]


class OperationElement(Element):
    required: Annotated[bool, AfterValidator(_not_supported_yet)]
    default: str
    pattern: Regex
    vocabulary: list[str]
    styles: dict[str, Style]


class LevelElement(Element):
    show: list[LevelName]
    styles: dict[LevelName, Style]


class DescriptionElement(Element):
    pattern: Regex


class Elements(_Table):
    datetime: DatetimeElement
    tag: TagElement
    operation: OperationElement
    level: LevelElement
    description: DescriptionElement


class OutputSettings(_Table):
    order: list[ElementName]
    separator: str
    elements: Elements


def format_datetime(at: dt.datetime, element: DatetimeElement) -> str:
    """``at`` in the element's zone and format."""
    zone = _zone(element.timezone)
    return (at.astimezone(zone) if zone is not None else at.astimezone()).strftime(element.format)


@cache
def _shipped_table() -> dict[str, Any]:
    return tomllib.loads(DEFAULTS_FILE.read_text(encoding="utf-8"))["output"]


@cache
def defaults() -> OutputSettings:
    """The shipped settings, which render whenever there is no valid user table."""
    return OutputSettings.model_validate(_shipped_table())


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _unknown(names: Iterable[str], known: Iterable[str]) -> list[str]:
    allowed = set(known)
    return [name for name in names if name not in allowed]


def _check_datetime(element: DatetimeElement, errors: list[tuple[str, str]]) -> None:
    for sample in _SAMPLE_TIMES:
        rendered = format_datetime(sample, element)
        if not rendered:
            errors.append(("output.elements.datetime.format", f"{element.format!r} renders empty"))
            return
        if not re.search(element.pattern, rendered):
            errors.append(
                (
                    "output.elements.datetime.format",
                    f"{element.format!r} renders {rendered!r}, which fails the pattern {element.pattern!r}",
                )
            )
            return


def _cross_field_errors(settings: OutputSettings) -> list[tuple[str, str]]:
    """What no single key can be checked for alone; every key is already valid by itself."""
    errors: list[tuple[str, str]] = []
    elements = settings.elements
    repeated = sorted({name for name in settings.order if settings.order.count(name) > 1})
    if repeated:
        errors.append(("output.order", f"{', '.join(map(repr, repeated))} named more than once"))
    if "description" not in settings.order:
        errors.append(("output.order", "must include 'description': a line with no text says nothing"))
    if not elements.description.enabled:
        errors.append(("output.elements.description.enabled", "the description cannot be disabled"))

    _check_datetime(elements.datetime, errors)

    tag = elements.tag
    failing = [value.value for value in Tag if not re.search(tag.pattern, value.value)]
    if failing:
        errors.append(("output.elements.tag.pattern", f"the tags {', '.join(failing)} fail {tag.pattern!r}"))
    errors.extend(
        (f"output.elements.tag.styles.{name}", f"{name!r} is not a known tag")
        for name in _unknown(tag.styles, (value.value for value in Tag))
    )

    operation = elements.operation
    shipped = _shipped_table()["elements"]["operation"]["vocabulary"]
    missing = _unknown(shipped, operation.vocabulary)
    if missing:
        errors.append(
            ("output.elements.operation.vocabulary", f"cannot remove the shipped operations {', '.join(missing)}")
        )
    failing = [name for name in operation.vocabulary if not re.search(operation.pattern, name)]
    if failing:
        errors.append(
            ("output.elements.operation.vocabulary", f"the operations {', '.join(failing)} fail {operation.pattern!r}")
        )
    # Checked against the shipped operations too, so a short vocabulary is reported once, not per use.
    vocabulary = {*shipped, *operation.vocabulary}
    if operation.default not in vocabulary:
        errors.append(
            ("output.elements.operation.default", f"{operation.default!r} is not in the operation vocabulary")
        )
    errors.extend(
        (f"output.elements.operation.styles.{name}", f"{name!r} is not in the operation vocabulary")
        for name in _unknown(operation.styles, vocabulary)
    )
    return errors


def _key_and_reason(error: ErrorDetails) -> tuple[str, str]:
    # A list position is not a key the user wrote: `show.0` is reported as `show`.
    key = ".".join(["output", *(str(part) for part in error["loc"] if not isinstance(part, int))])
    return key, error["msg"].removeprefix("Value error, ")


def validate(table: object) -> tuple[OutputSettings | None, list[tuple[str, str]]]:
    """``table`` (a user's ``[output]``) over the shipped defaults, or None and each ``(key, reason)``."""
    if not isinstance(table, dict):
        return None, [("output", "must be a table")]
    try:
        settings = OutputSettings.model_validate(_merge(_shipped_table(), table), context={"check": True})
    except ValidationError as exc:
        return None, [_key_and_reason(error) for error in exc.errors(include_url=False)]
    errors = _cross_field_errors(settings)
    return (None, errors) if errors else (settings, [])


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
    settings, errors = validate(data["output"])
    if settings is None:
        return _Loaded(defaults(), tuple(f"{path}: {key}: {reason}" for key, reason in errors))
    return _Loaded(settings, ())


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
