"""Which rows ``ai sessions list`` prints, in what order and grouping, and its ``[sessions_list]`` defaults.

Depends on: config.py
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from . import config

#: The built-in ``[sessions_list]`` values; docs/reference/sessions-list-defaults.toml documents them.
DEFAULTS = {
    "group_by": "host",
    "sort_by": ["status", "name"],
    "show_all": False,
    "scope": "pair",
    "pair_host": "",
}
GROUP_KEYS = ("host", "status", "relaunch", "none")
SORT_KEYS = ("status", "name", "changed", "host", "relaunch")
SCOPES = ("host", "pair", "fleet")
#: Active sessions first; a state not listed here sorts after ``unknown``.
STATUS_ORDER = ("running", "relaunched", "unknown", "exited")
ACTIVE = frozenset({"running", "relaunched"})

_COLUMNS = (
    ("host", "HOST"),
    ("name", "SESSION"),
    ("status", "STATUS"),
    ("cause", "CAUSE"),
    ("changed", "CHANGED"),
    ("relaunch", "RELAUNCH"),
)


@dataclass(frozen=True)
class Row:
    host: str
    name: str
    status: str
    cause: str
    changed: str
    relaunch: str


class ViewError(ValueError):
    """A ``[sessions_list]`` value or a flag value the list cannot use."""


def parse_sort(value: str | Sequence[str], source: str) -> list[str]:
    """Sort keys from a comma-separated flag value or a config list, checked against :data:`SORT_KEYS`."""
    keys = [key.strip() for key in value.split(",")] if isinstance(value, str) else list(value)
    unknown = [key for key in keys if not isinstance(key, str) or key not in SORT_KEYS]
    if not keys or unknown:
        raise ViewError(f"{source}: sort keys must be one or more of {', '.join(SORT_KEYS)}; got {value!r}")
    return keys


def settings() -> dict:
    """``[sessions_list]`` over the built-in defaults, read from config.toml on every call."""
    table = config.load_config(read_only=True).get("sessions_list", {})
    where = f"[sessions_list] in {config.config_file_text()}"
    if not isinstance(table, dict):
        raise ViewError(f"{where} must be a table")
    unknown = sorted(set(table) - set(DEFAULTS))
    if unknown:
        raise ViewError(f"{where}: unknown key {', '.join(unknown)}; known keys: {', '.join(DEFAULTS)}")
    result = DEFAULTS | table
    choices = {"group_by": GROUP_KEYS, "scope": SCOPES}
    for key, allowed in choices.items():
        if result[key] not in allowed:
            raise ViewError(f"{where}: {key} must be one of {', '.join(allowed)}; got {result[key]!r}")
    if not isinstance(result["show_all"], bool):
        raise ViewError(f"{where}: show_all must be true or false")
    if not isinstance(result["pair_host"], str):
        raise ViewError(f"{where}: pair_host must be a string")
    result["sort_by"] = parse_sort(result["sort_by"], f"{where}: sort_by")
    return result


def _status_rank(status: str) -> int:
    return STATUS_ORDER.index(status) if status in STATUS_ORDER else STATUS_ORDER.index("unknown") + 1


def _ordered(rows: list[Row], sort_by: Sequence[str], local_host: str) -> list[Row]:
    keys = {
        "status": lambda r: _status_rank(r.status),
        "name": lambda r: r.name,
        "host": lambda r: (r.host != local_host, r.host),
        "relaunch": lambda r: r.relaunch,
        "changed": lambda r: r.changed,
    }
    # One stable pass per key, last key first, so the first key decides and later keys break ties.
    for key in reversed(sort_by):
        rows = sorted(rows, key=keys[key], reverse=key == "changed")
    return rows


def _group_order(group_by: str, value: str, local_host: str) -> tuple:
    if group_by == "host":
        return (value != local_host, value)
    if group_by == "status":
        return (_status_rank(value), value)
    return (value,)


def arrange(
    rows: Iterable[Row], *, local_host: str, group_by: str, sort_by: Sequence[str], show_all: bool
) -> list[list[Row]]:
    """The rows to print, filtered, sorted and split into groups in display order."""
    kept = [row for row in rows if show_all or row.status in ACTIVE]
    ordered = _ordered(kept, sort_by, local_host)
    if group_by == "none":
        return [ordered] if ordered else []
    groups: dict[str, list[Row]] = {}
    for row in ordered:
        groups.setdefault(getattr(row, group_by), []).append(row)
    return [groups[value] for value in sorted(groups, key=lambda v: _group_order(group_by, v, local_host))]


def table(groups: list[list[Row]], group_by: str) -> tuple[list[str], list[list[list[str]]]]:
    """Headers and cell text for ``output.table``: the grouping column first, named once per group."""
    columns = list(_COLUMNS)
    if group_by != "none":
        columns.sort(key=lambda column: column[0] != group_by)
    cells = []
    for group in groups:
        lines = []
        for index, row in enumerate(group):
            line = [getattr(row, field) for field, _ in columns]
            if group_by != "none" and index:
                line[0] = ""
            lines.append(line)
        cells.append(lines)
    return [header for _, header in columns], cells
