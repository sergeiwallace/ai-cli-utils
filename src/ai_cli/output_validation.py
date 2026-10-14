"""Validation of a user's ``[output]`` table (AI-CLI-mu4d).

Imported only when ``config.toml`` has an ``[output]`` table, so a record rendered
with the shipped defaults never pays for pydantic. Checks on one key are pydantic
validators, so they are all reported together; checks that span keys run once
every key is valid by itself. :func:`validate` returns the merged table, which
:mod:`ai_cli.output_settings` turns into the settings the renderer reads.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable, Iterable
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, ValidationError, ValidationInfo
from pydantic_core import ErrorDetails

from .output import Level, Tag, check_style, format_datetime, resolve_zone
from .output_settings import ELEMENTS, merge, shipped_table

# strftime directives that render the same on every platform's C library.
DATETIME_DIRECTIVES = frozenset("aAbBcdfHIjmMpSUwWxXyYzZ%")
# One winter and one summer instant, so a format is checked under both DST abbreviations.
_SAMPLE_TIMES = (dt.datetime(2026, 1, 15, 18, 30, tzinfo=dt.UTC), dt.datetime(2026, 7, 1, 18, 30, tzinfo=dt.UTC))


def _checking(info: ValidationInfo) -> bool:
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


def _timezone(value: str, info: ValidationInfo) -> str:
    if _checking(info):
        try:
            resolve_zone(value)
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
    format: Annotated[str, AfterValidator(_datetime_format)]
    timezone: Annotated[str, AfterValidator(_timezone)]
    pattern: Regex


class TagElement(Element):
    pattern: Regex
    styles: dict[str, Style]


class OperationElement(Element):
    required: bool
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


def _unknown(names: Iterable[str], known: Iterable[str]) -> list[str]:
    allowed = set(known)
    return [name for name in names if name not in allowed]


def _check_datetime(element: DatetimeElement, errors: list[tuple[str, str]]) -> None:
    for sample in _SAMPLE_TIMES:
        rendered = format_datetime(sample, element.format, element.timezone)
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
    shipped = shipped_table()["elements"]["operation"]["vocabulary"]
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


def validate(table: object) -> tuple[dict[str, Any] | None, list[tuple[str, str]]]:
    """``table`` (a user's ``[output]``) merged over the shipped defaults, or None and each ``(key, reason)``."""
    if not isinstance(table, dict):
        return None, [("output", "must be a table")]
    merged = merge(shipped_table(), table)
    try:
        settings = OutputSettings.model_validate(merged, context={"check": True})
    except ValidationError as exc:
        return None, [_key_and_reason(error) for error in exc.errors(include_url=False)]
    errors = _cross_field_errors(settings)
    return (None, errors) if errors else (merged, [])
