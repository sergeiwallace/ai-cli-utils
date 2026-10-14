"""The one terminal-output interface for ai-cli-utils.

Every line this package prints goes through :func:`emit` (or its ``warning`` /
``error`` shorthands) and is rendered as::

    <datetime> [<tag>] <operation>: <text>
    <datetime> [<tag>] <operation>: Warning: <text>
    <datetime> [<tag>] <operation>: Error: <text>

``tag`` must be a :class:`Tag` member: a plain string is refused with
``TypeError``, so an untagged line cannot be produced by passing the wrong
thing. ``op`` is the second positional argument of every producer and must be
in the operation vocabulary (AI-CLI-rbuf), which lives in the ``[output]``
config, not in code: an unknown one is refused with ``ValueError``. Output that
a program parses (JSON, ``ai internal`` replies, the statusline segment,
``--version``) or that is not a line at all (terminal
control sequences, Click's help text) goes through :func:`raw`, whose reason
must be an :class:`Untagged` member -- the closed, named allowlist of untagged
output. ``tests/test_output_enforcement.py`` fails the build on any terminal
write anywhere else.

One renderer (AI-CLI-yxt0): structlog builds one :class:`Record` per line and
:func:`render_record` turns it into the line, styling each element with Rich.
Rich renders and this module writes, so Rich never owns the stream, the cursor
or any terminal state, and a launcher can exec into another program right after
any call. Colour reaches a TTY only; ``NO_COLOR`` and ``TERM=dumb`` turn it off
there too. structlog and Rich are imported on the first record, so ``raw()``
paths (``--version``, the statusline, ``ai internal`` replies) never pay for
them.

Which elements a line has, their order, delimiters and styles come from the
``[output]`` table of ``config.toml`` (AI-CLI-mu4d, :mod:`ai_cli.output_settings`),
checked before every record, so an edit applies to the next line.

Three sinks (AI-CLI-2gq3) take every record: the terminal gets the styled line,
the per-launch log file attached with :func:`attach_file_sink` gets the same
elements in plain text, always led by the datetime, and telemetry stores each
record at or above ``[output.sinks.telemetry] min_level``. Telemetry is buffered
in memory and stored in one batch by :func:`flush_sinks`, before an exec or a
handoff and at exit, so no record pays for a database write.

Line discipline (AI-CLI-8h3u): every record is one locked write whose text is
made inert (no control bytes), ending in CR LF on a POSIX terminal so it lands
at column 0 even while a foreign program has output post-processing off. While
:func:`foreign_terminal` is active a record also starts by clearing its row,
and :func:`reclaim_terminal` restores ``OPOST``/``ONLCR`` and starts a fresh
row when the terminal comes back. A line left open is closed before any other
record, before an exec (:func:`flush_sinks`) and at exit.
"""

from __future__ import annotations

import atexit
import contextlib
import functools
import json
import logging
import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, tzinfo
from enum import StrEnum
from typing import TYPE_CHECKING, Any, TextIO

import click

if TYPE_CHECKING:
    from rich.console import Console
    from rich.text import Text

    from .output_settings import OutputSettings

TAG_PATTERN = re.compile(r"^[a-z0-9_-]+$")
OPERATION_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")

# A child `ai` process run by run_relayed() (or `ai update`'s self-update) writes
# one JSON record per line when this is set, and the parent re-emits each record
# with the child's own tag, operation, level and time. Read once and removed, so
# it reaches only the child it was set for.
RECORD_RELAY_ENV = "AI_CLI_RECORD_RELAY"
_RECORD_MARK = "ai_cli_record"
_relay_records = os.environ.pop(RECORD_RELAY_ENV, "") == "1"


class Tag(StrEnum):
    """Every log type tag. Adding one is the review point for a new output source."""

    ADOPT = "adopt"
    ATTACH = "attach"
    AUDIT = "audit"
    CDP = "cdp"
    CLI = "cli"
    COLOR = "color"
    CONFIG = "config"
    COPIER = "copier"
    COS = "cos"
    DAEMON = "daemon"
    DOCTOR = "doctor"
    DOLT_SERVER = "dolt_server"
    GIT = "git"
    INTERNAL = "internal"
    ITERM2 = "iterm2"
    LAUNCH = "launch"
    LAYOUT = "layout"
    LOG = "log"
    MEMORY = "memory"
    MEMORY_WATCH = "memory-watch"
    MESSAGING = "messaging"
    MIGRATE = "migrate"
    NOTIFY = "notify"
    PLAN = "plan"
    PS = "ps"
    QUOTA = "quota"
    QUOTA_WATCH = "quota-watch"
    REGISTER = "register"
    REMOTE = "remote"
    RESTORE = "restore"
    RETIRE = "retire"
    SESSION = "session"  # the generated session script (session_script.py) prints this one
    SESSIONS = "sessions"
    SETUP = "setup"
    SPEND = "spend"
    SYNC = "sync"
    SYNC_WATCH = "sync-watch"
    TELEMETRY = "telemetry"
    TELEMETRY_WRITER = "telemetry-writer"
    TRANSPORT = "transport"
    TRUST = "trust"
    TUNNEL = "tunnel"
    UPDATE = "update"
    USAGE = "usage"
    VPN = "vpn"
    WORKSPACE = "workspace"
    ZSH = "zsh"


class Untagged(StrEnum):
    """The named allowlist of output that is deliberately written without a tag."""

    JSON = "machine-readable JSON a caller parses"
    INTERNAL_REPLY = "an `ai internal` reply the generated session script parses"
    STATUSLINE = "a statusline segment Claude Code renders verbatim"
    VERSION = "the `--version` string scripts compare"
    HELP = "Click-rendered help text, the CLI's reference documentation"
    TERMINAL_CONTROL = "a terminal escape sequence, not a line"


class Level(StrEnum):
    """How much a record matters. ``warning`` and ``error`` render a label."""

    DETAIL = "detail"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


_LEVEL_RANK = {level: rank for rank, level in enumerate(Level)}

for _tag in Tag:
    if not TAG_PATTERN.match(_tag.value):
        raise ValueError(f"log type tag {_tag.value!r} does not match {TAG_PATTERN.pattern}")


@dataclass(frozen=True)
class Record:
    """One logical line, built once by the structlog pipeline and rendered per sink.

    ``op`` is None only from the stdlib-logging adapter, whose records name no
    operation; it renders as the configured default while the operation is
    required. ``continued`` marks the
    second and later lines of a multi-line message, which repeat the tag and
    operation but not the level.
    """

    at: datetime
    tag: Tag
    text: str
    level: Level = Level.INFO
    op: str | None = None
    text_style: str = ""
    continued: bool = False


class MirroredStream(TextIO):  # type: ignore[misc]
    """A stream that also copies what is written to it elsewhere (the launch log mirrors stderr).

    A record is written past it, to :attr:`unmirrored`, because it reaches that
    copy through its own sink; only text that is not a record is mirrored.
    """

    unmirrored: TextIO


_lock = threading.RLock()
_open_lines: dict[int, Tag] = {}
_open_streams: dict[int, TextIO] = {}
_foreign_depth = 0
_file_sink: Callable[[str], None] | None = None
# (unix time, subject, data) per record due for telemetry, stored by flush_sinks().
_telemetry_buffer: list[tuple[float, str, dict[str, Any]]] = []

_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")
_ESC = re.compile(r"\x1b[@-_]?")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_CLEAR_ROW = "\r\x1b[K"


def _stream(err: bool, file: TextIO | None) -> TextIO:
    if file is not None:
        return file
    return sys.stderr if err else sys.stdout


def _is_tty(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def color_allowed() -> bool:
    """False when the user opted out of colour: ``NO_COLOR`` set non-empty (no-color.org), or ``TERM=dumb``."""
    return not os.environ.get("NO_COLOR") and os.environ.get("TERM", "").lower() != "dumb"


def wants_color(stream: TextIO | None = None) -> bool:
    """True when styling should reach ``stream`` (stderr when omitted)."""
    return color_allowed() and _is_tty(stream if stream is not None else sys.stderr)


def inert(text: str) -> str:
    """``text`` with no byte that could move the cursor or change terminal state.

    Escape sequences are dropped whole, a ``\\r``-redrawn line keeps only its
    final segment (what a terminal would have shown), tabs become spaces, and
    every other C0/C1 control character is removed.
    """
    text = _ESC.sub("", _OSC.sub("", _CSI.sub("", text)))
    if "\r" in text:
        segments = [segment for segment in text.split("\r") if segment]
        text = segments[-1] if segments else ""
    return _CONTROL.sub("", text.expandtabs())


def _eol(stream: TextIO) -> str:
    # CR LF lands at column 0 whether or not the terminal's OPOST/ONLCR is on,
    # which ssh's raw mode turns off. POSIX-only: a Windows console has no
    # OPOST, and its text-mode stream already writes LF as CR LF.
    return "\r\n" if os.name != "nt" and _is_tty(stream) else "\n"


def _unmirrored(stream: TextIO) -> TextIO:
    while isinstance(stream, MirroredStream):
        stream = stream.unmirrored
    return stream


def _write(stream: TextIO, lines: list[str], *, nl: bool = True) -> None:
    """Write rendered ``lines`` as one record: a single write, caller holds ``_lock``."""
    stream = _unmirrored(stream)
    eol = _eol(stream)
    lead = _CLEAR_ROW if _foreign_depth and eol == "\r\n" else ""
    text = eol.join(lead + line for line in lines)
    # Every terminal line passes here, so CodeQL's name heuristic traces spend.py's
    # `deep_research_oauth` run counters ("password") and its configured
    # `gcp_project_id` ("private") to this write and reports a clear-text leak. Neither
    # is a credential: a count of runs, and a project identifier the user configured
    # so `ai spend` can name it.
    stream.write(text + eol if nl else text)
    stream.flush()


def _close_open_lines() -> None:
    # A line left open by ``nl=False`` is ended before any other line starts, on
    # every stream, so the next line can never land after it untagged.
    for stream_id in list(_open_lines):
        _open_lines.pop(stream_id)
        stream = _open_streams.pop(stream_id, None)
        if stream is not None:
            stream = _unmirrored(stream)
            with contextlib.suppress(ValueError, OSError):
                stream.write(_eol(stream))
                stream.flush()


def flush_sinks() -> None:
    """Close any open line, flush both streams and store buffered telemetry: run before an exec or a handoff."""
    with _lock:
        _close_open_lines()
        for stream in (sys.stdout, sys.stderr):
            with contextlib.suppress(AttributeError, ValueError, OSError):
                stream.flush()
        events = list(_telemetry_buffer)
        _telemetry_buffer.clear()
    if events:
        _store_telemetry(events)


def _store_telemetry(events: list[tuple[float, str, dict[str, Any]]]) -> None:
    # Telemetry is never the reason a launch fails: a store that cannot take the
    # batch (unwritable, locked past its short timeout) loses it, and says nothing.
    with contextlib.suppress(Exception):
        from . import telemetry

        telemetry.store_log_records(events)


def attach_file_sink(sink: Callable[[str], None] | None) -> Callable[[str], None] | None:
    """Send every record to ``sink`` as one plain log-file line (None detaches); return the previous sink.

    The launch attaches its per-launch log for exactly the launch. ``sink`` must
    not raise: a log file that cannot be written must leave the terminal alone.
    """
    global _file_sink
    with _lock:
        previous, _file_sink = _file_sink, sink
    return previous


def store(records: Sequence[Record]) -> None:
    """Send ``records`` to the file and telemetry sinks, not the terminal: for a reply printed through :func:`raw`.

    ``ai internal emit`` replies with the rendered line so the session script can
    print it, and stores the same record here.
    """
    _write_records(sys.stderr, list(records), terminal=False)


def file_text_line(text: str) -> str:
    """``text`` that is not a record (a traceback, a foreign tool's line) as a log-file line, led by the time."""
    settings = _settings()
    when = settings.elements.datetime
    return settings.separator.join([when.wrap(format_datetime(datetime.now(UTC), when.format, when.timezone)), text])


atexit.register(flush_sinks)


@contextlib.contextmanager
def foreign_terminal() -> Iterator[None]:
    """Mark a span where another program (ssh, mosh) owns the terminal.

    A record written inside it first returns to column 0 and clears the row, so
    it never continues the foreign program's partial line.
    """
    global _foreign_depth
    with _lock:
        _foreign_depth += 1
    try:
        yield
    finally:
        with _lock:
            _foreign_depth -= 1


def _restore_output_flags(stream: TextIO) -> None:
    try:
        import termios
    except ImportError:  # Windows: no termios, and no OPOST to lose
        return
    try:
        fd = stream.fileno()
        attrs = termios.tcgetattr(fd)
        wanted = attrs[1] | termios.OPOST | termios.ONLCR
        if wanted == attrs[1] or not _owns_foreground(fd):
            return
        attrs[1] = wanted
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
    except (termios.error, OSError, ValueError, AttributeError):
        pass


def _owns_foreground(fd: int) -> bool:
    # tcsetattr from a background process group of the controlling terminal
    # raises SIGTTOU; a terminal that is not ours to control (ENOTTY) cannot.
    try:
        return os.tcgetpgrp(fd) == os.getpgrp()
    except OSError:
        return True


def _line_start_fence(stream: TextIO) -> str:
    # zsh's PROMPT_SP: a full row of spaces wraps to a fresh row only when the
    # cursor is past column 0, so a foreign partial line is kept, a stale tail a
    # bare CR left behind is overwritten, and no blank line is added.
    try:
        columns = os.get_terminal_size(stream.fileno()).columns
    except (AttributeError, ValueError, OSError):
        columns = 0
    return " " * columns + "\r" if columns > 0 else "\r\n"


def reclaim_terminal(stream: TextIO | None = None) -> None:
    """The terminal is back from a foreign program: restore output flags and start a fresh row."""
    target = stream or (sys.stderr if _is_tty(sys.stderr) else sys.stdout)
    if target is None or os.name == "nt" or not _is_tty(target):
        return
    with _lock:
        _restore_output_flags(target)
        with contextlib.suppress(ValueError, OSError):
            target.write(_line_start_fence(target))
            target.flush()


def utf8_stdout_on_windows() -> None:
    """Make stdout UTF-8 (replacing what it cannot encode) on Windows, where cp1252 is the default."""
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def _require_tag(tag: object) -> Tag:
    if not isinstance(tag, Tag):
        raise TypeError(f"output needs an ai_cli.output.Tag member, got {tag!r}")
    return tag


def vocabulary() -> frozenset[str]:
    """Every operation a record may name: the shipped list plus any a user's ``[output]`` adds."""
    from . import output_settings

    return output_settings.shipped_vocabulary().union(_settings().elements.operation.vocabulary)


def _known_op(op: object) -> bool:
    from . import output_settings

    # The shipped list answers for every operation the package emits without a stat;
    # only an operation a user added needs the current config.
    return isinstance(op, str) and (op in output_settings.shipped_vocabulary() or op in vocabulary())


def require_op(op: object) -> str:
    """``op`` when the operation vocabulary names it; ``ValueError`` otherwise."""
    if isinstance(op, str) and _known_op(op):
        return op
    raise ValueError(f"operation {op!r} is not in the operation vocabulary ([output.elements.operation])")


# --- the record pipeline: structlog builds each record ----------------------------


def _check_signature(_logger: object, _method: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    # The guarantee is static at the call signature (a Tag member, an operation string);
    # this processor only checks it, so a record cannot be built around a bad value.
    _require_tag(event.get("tag"))
    op = event.get("op")
    if op is not None and not (isinstance(op, str) and OPERATION_PATTERN.match(op)):
        raise ValueError(f"operation {op!r} does not match {OPERATION_PATTERN.pattern}")
    event["level"] = Level(event.get("level", Level.INFO))
    return event


def _timestamp(_logger: object, _method: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    at = event.setdefault("at", datetime.now(UTC))
    if not isinstance(at, datetime) or at.tzinfo is None:
        raise ValueError(f"a record time must be an aware datetime, got {at!r}")
    return event


def _to_record(_logger: object, _method: str, event: MutableMapping[str, Any]) -> tuple[tuple[Record], dict[str, Any]]:
    text = inert(str(event.pop("event")))
    return (Record(text=text, **event),), {}


class _RecordSink:
    """structlog's wrapped logger: it hands the finished record back to the caller."""

    def msg(self, record: Record) -> Record:
        return record


_pipeline: Any = None


def _build(text: str, **fields: Any) -> Record:
    global _pipeline
    if _pipeline is None:
        # Imported on the first record, never at module load: the statusline,
        # `--version` and `ai internal` replies stay free of the import cost.
        import structlog

        _pipeline = structlog.wrap_logger(
            _RecordSink(),
            wrapper_class=structlog.BoundLogger,
            processors=[_check_signature, _timestamp, _to_record],
            cache_logger_on_first_use=True,
        )
    return _pipeline.msg(text, **fields)


def make_record(tag: Tag, text: str, **fields: Any) -> Record:
    """One record for ``text`` under ``tag``, built and checked by the pipeline."""
    return _build(text, tag=tag, **fields)


# --- the renderer: Rich styles each element ---------------------------------------


def _element(name: str, record: Record, settings: OutputSettings) -> list[tuple[str, str]]:
    elements = settings.elements
    if name == "datetime" and elements.datetime.enabled:
        when = elements.datetime
        return [(when.wrap(format_datetime(record.at, when.format, when.timezone)), when.style)]
    if name == "tag" and elements.tag.enabled:
        tag = elements.tag
        return [(tag.wrap(record.tag.value), tag.styles.get(record.tag.value, tag.style))]
    if name == "operation" and elements.operation.enabled:
        operation = elements.operation
        op = _shown_op(record, settings)
        return [] if op is None else [(operation.wrap(op), operation.styles.get(op, operation.style))]
    if name == "level" and elements.level.enabled:
        level = elements.level
        if record.continued or record.level.value not in level.show:
            return []
        return [(level.wrap(record.level.value.capitalize()), level.styles.get(record.level.value, level.style))]
    if name == "description" and record.text:
        description = elements.description
        return [(description.wrap(record.text), record.text_style or description.style)]
    return []


def _shown_op(record: Record, settings: OutputSettings) -> str | None:
    operation = settings.elements.operation
    return record.op if record.op is not None else (operation.default if operation.required else None)


def _elements(record: Record, settings: OutputSettings) -> list[tuple[str, str]]:
    return [part for name in settings.order for part in _element(name, record, settings)]


def _settings() -> OutputSettings:
    """The settings for the next record; a newly broken ``[output]`` table is reported first."""
    from . import output_settings

    with _lock:
        settings, errors = output_settings.current()
        if errors:
            _report_config_errors(errors, output_settings.defaults())
    return settings


def settings() -> OutputSettings:
    """The current ``[output]`` settings, reporting a newly broken table first."""
    return _settings()


def _report_config_errors(errors: Sequence[str], settings: OutputSettings) -> None:
    # Rendered with the shipped defaults: the table being refused cannot style its own errors.
    records = [_build(message, tag=Tag.CONFIG, op="load", level=Level.ERROR) for message in errors]
    _write_records(sys.stderr, records, settings=settings)


_consoles: dict[int, Console] = {}


def _console(stream: TextIO) -> Console:
    from rich.console import Console

    console = _consoles.get(id(stream))
    if console is None or console.file is not stream:
        if len(_consoles) > 16:
            _consoles.clear()
        # markup off: `[transport]` would otherwise be read as a markup tag and vanish.
        # soft_wrap on: a long record stays one line instead of being hard-wrapped.
        # force_terminal: the colour decision is ours (wants_color); Rich picks the depth.
        console = Console(file=stream, force_terminal=True, markup=False, highlight=False, emoji=False, soft_wrap=True)
        _consoles[id(stream)] = console
    return console


def _styled_text(parts: list[tuple[str, str]], separator: str) -> Text:
    from rich.text import Text

    line = Text()
    for index, (value, style) in enumerate(parts):
        if index:
            line.append(separator)
        line.append(value, style=style)
    return line


def check_style(style: str) -> None:
    """Raise ``ValueError`` when ``style`` is not a style string Rich can render."""
    from rich.errors import StyleSyntaxError
    from rich.style import Style

    try:
        Style.parse(style)
    except StyleSyntaxError as exc:
        raise ValueError(f"{style!r} is not a valid style: {exc}") from None


@functools.cache
def resolve_zone(name: str) -> tzinfo | None:
    """The zone ``name`` names: ``UTC``, ``local`` (None: the host's zone, read per record) or an IANA name."""
    if name == "UTC":
        return UTC
    if name == "local":
        return None
    # Imported on the first datetime, not at module load: `--version` and the statusline never pay for it.
    from zoneinfo import ZoneInfo

    return ZoneInfo(name)


def _in_zone(at: datetime, timezone: str) -> datetime:
    zone = resolve_zone(timezone)
    return at.astimezone(zone) if zone is not None else at.astimezone()


def format_datetime(at: datetime, fmt: str, timezone: str) -> str:
    """``at`` in ``timezone``, formatted by the strftime ``fmt``; an unknown zone raises ``KeyError``."""
    return _in_zone(at, timezone).strftime(fmt)


def _legacy_windows_console(stream: TextIO, console: Console) -> bool:
    # A Windows console without virtual-terminal support prints escapes literally. Rich
    # detects one from the process's own console, so the answer applies only to a record
    # bound for that console, not to a pipe or a captured stream.
    if sys.platform != "win32":
        return False
    try:
        return stream.fileno() in (1, 2) and console.legacy_windows
    except (AttributeError, OSError, ValueError):
        return False


def render_record(
    record: Record, *, color: bool, stream: TextIO | None = None, settings: OutputSettings | None = None
) -> str:
    """``record`` as one line with no terminator; styled when ``color``, plain otherwise.

    ``settings`` defaults to the current ``[output]`` configuration.
    """
    settings = settings if settings is not None else _settings()
    parts = _elements(record, settings)
    plain = settings.separator.join(value for value, _style in parts)
    if not color:
        return plain
    target = stream if stream is not None else sys.stderr
    console = _console(target)
    if _legacy_windows_console(target, console):
        return plain
    with _lock, console.capture() as capture:
        console.print(_styled_text(parts, settings.separator), end="")
    return capture.get()


def render_progress(record: Record, *, color: bool, in_place: bool) -> str:
    """``record`` as a progress row (rule L4): ``in_place`` returns to column 0 and clears the row first.

    The row carries no terminator, so the next progress row replaces it; whoever
    writes next is responsible for closing it with a line ending.
    """
    line = render_record(record, color=color)
    return _CLEAR_ROW + line if in_place else line


def _relay_json(record: Record) -> str:
    payload = {
        _RECORD_MARK: 1,
        "at": record.at.isoformat(),
        "tag": record.tag.value,
        "level": record.level.value,
        "op": record.op,
        "text": record.text,
        "continued": record.continued,
    }
    return json.dumps(payload, ensure_ascii=False)


def _file_settings(settings: OutputSettings) -> OutputSettings:
    # The log file always leads with the time: a line without one says nothing about when.
    when = settings.elements.datetime
    if when.enabled and "datetime" in settings.order:
        return settings
    order = settings.order if "datetime" in settings.order else ("datetime", *settings.order)
    elements = replace(settings.elements, datetime=replace(when, enabled=True))
    return replace(settings, order=tuple(order), elements=elements)


def _telemetry_event(record: Record, settings: OutputSettings) -> tuple[float, str, dict[str, Any]]:
    data = {
        "at": _in_zone(record.at, settings.elements.datetime.timezone).isoformat(),
        "tag": record.tag.value,
        "op": _shown_op(record, settings),
        "level": record.level.value,
        "text": record.text,
    }
    return record.at.timestamp(), f"telemetry.log.{record.tag.value}", data


def _write_records(
    stream: TextIO,
    records: list[Record],
    *,
    nl: bool = True,
    terminal: bool = True,
    settings: OutputSettings | None = None,
) -> bool:
    """Send ``records`` to every sink; True when they reached the terminal.

    ``terminal=False`` keeps them off the terminal only (a ``-q`` launch's info
    line, a detail line without ``-v``): the file and telemetry still get them.
    """
    with _lock:
        if _relay_records:
            # A child `ai` answers its parent in records, and the parent owns every sink.
            if terminal:
                _close_open_lines()
                _write(stream, [_relay_json(record) for record in records], nl=nl)
            return terminal
        settings = settings if settings is not None else _settings()
        shown = terminal and settings.sinks.terminal.enabled
        if shown:
            _close_open_lines()
            color = wants_color(stream)
            _write(
                stream,
                [render_record(record, color=color, stream=stream, settings=settings) for record in records],
                nl=nl,
            )
        if _file_sink is not None and settings.sinks.file.enabled:
            file_settings = _file_settings(settings)
            for record in records:
                _file_sink(render_record(record, color=False, settings=file_settings))
        telemetry = settings.sinks.telemetry
        if telemetry.enabled:
            floor = _LEVEL_RANK[Level(telemetry.min_level)]
            due = [record for record in records if _LEVEL_RANK[record.level] >= floor]
            if due and _telemetry_switch():
                _telemetry_buffer.extend(_telemetry_event(record, settings) for record in due)
        return shown


def _telemetry_switch() -> bool:
    from . import output_settings

    return output_settings.telemetry_enabled()


def render(tag: Tag, op: str, text: str, *, stream: TextIO | None = None) -> str:
    """One tagged line for a prompt, styled when ``stream`` (stderr when omitted) wants colour."""
    target = stream if stream is not None else sys.stderr
    return render_record(_build(text, tag=tag, op=require_op(op)), color=wants_color(target), stream=target)


def emit(
    tag: Tag,
    op: str,
    message: object = "",
    *,
    err: bool = False,
    file: TextIO | None = None,
    nl: bool = True,
    level: Level = Level.INFO,
    body_style: str = "",
    terminal: bool = True,
) -> None:
    """Write ``message`` as ``[tag] op:`` on every line; blank lines are dropped.

    ``op`` names the operation the line belongs to, from the configured vocabulary.
    ``err`` sends it to stderr, ``file`` to an explicit stream, and ``nl=False``
    leaves the cursor after the last line (an inline prompt). ``body_style`` is the
    launch reporter's outcome style. ``terminal=False`` records the line in the log
    file and telemetry without printing it.
    """
    _require_tag(tag)
    _emit(
        tag, require_op(op), message, err=err, file=file, nl=nl, level=level, body_style=body_style, terminal=terminal
    )


def _emit(
    tag: Tag,
    op: str | None,
    message: object,
    *,
    err: bool = False,
    file: TextIO | None = None,
    nl: bool = True,
    level: Level = Level.INFO,
    body_style: str = "",
    terminal: bool = True,
) -> None:
    lines = [line for line in (inert(part) for part in str(message).split("\n")) if line.strip()]
    if not lines:
        return
    stream = _stream(err, file)
    if _relay_records:
        nl = True  # a record on the wire is a whole line; a later cont() starts a fresh one
    at = datetime.now(UTC)
    records = [
        _build(
            line,
            tag=tag,
            at=at,
            op=op,
            level=level,
            text_style=body_style,
            continued=index > 0,
        )
        for index, line in enumerate(lines)
    ]
    with _lock:
        shown = _write_records(stream, records, nl=nl, terminal=terminal)
        if shown and not nl:
            _open_lines[id(stream)] = tag
            _open_streams[id(stream)] = stream


def cont(tag: Tag, op: str, message: object, *, err: bool = False, file: TextIO | None = None) -> None:
    """Finish the line an ``emit(tag, op, ..., nl=False)`` left open on the same stream.

    If that line was already closed (another line was emitted in between), the
    text starts a fresh ``[tag] op:`` line instead, so a continuation can never
    produce an untagged line.
    """
    _require_tag(tag)
    require_op(op)
    stream = _stream(err, file)
    first, _, rest = str(message).partition("\n")
    with _lock:
        if _open_lines.pop(id(stream), None) is None:
            emit(tag, op, message, err=err, file=file)
            return
        _open_streams.pop(id(stream), None)
        _write(stream, [inert(first)])
    if rest:
        emit(tag, op, rest, err=err, file=file)


def warning(tag: Tag, op: str, message: object, *, file: TextIO | None = None) -> None:
    """``[tag] op: Warning: message`` on stderr."""
    emit(tag, op, message, err=True, file=file, level=Level.WARNING)


def error(tag: Tag, op: str, message: object, *, file: TextIO | None = None) -> None:
    """``[tag] op: Error: message`` on stderr."""
    emit(tag, op, message, err=True, file=file, level=Level.ERROR)


def confirm(tag: Tag, op: str, question: str, *, default: bool = False) -> bool:
    """A yes/no prompt whose prompt line carries ``[tag] op:``."""
    return click.confirm(render(tag, op, question.strip(), stream=sys.stdout), default=default)


def ask(tag: Tag, op: str, prompt: str, *, err: bool = False) -> str:
    """Read one line from stdin after a ``[tag]`` prompt (every prompt line tagged).

    On stdout the prompt goes through ``input()``; with ``err`` it is written to
    stderr and the answer read from ``sys.stdin``, for a prompt that must not
    pollute a stdout a caller captures.
    """
    stream = _stream(err, None)
    lines = [render(tag, op, line.strip(), stream=stream) for line in prompt.split("\n") if line.strip()]
    text = _eol(stream).join(lines) + " "
    with _lock:
        _close_open_lines()
        if not err:
            return input(text)
        stream.write(text)
        stream.flush()
    return sys.stdin.readline()


def click_failure(exc: click.ClickException) -> None:
    """Render a Click usage or command error as ``[cli]`` lines on stderr."""
    if isinstance(exc, click.UsageError) and exc.ctx is not None:
        emit(Tag.CLI, "usage", exc.ctx.get_usage(), err=True)
        if exc.ctx.command.get_help_option(exc.ctx) is not None:
            emit(Tag.CLI, "usage", f"Try '{exc.ctx.command_path} {exc.ctx.help_option_names[0]}' for help.", err=True)
    error(Tag.CLI, "usage", exc.format_message())


def stream_is_tty(*, err: bool = False, file: TextIO | None = None) -> bool:
    """True when the stream :func:`emit` would write to is a terminal."""
    return _is_tty(_stream(err, file))


def raw(reason: Untagged, text: str, *, err: bool = False, file: TextIO | None = None, nl: bool = True) -> None:
    """Write ``text`` untagged; ``reason`` names why this output may not carry a tag."""
    if not isinstance(reason, Untagged):
        raise TypeError(f"untagged output needs an ai_cli.output.Untagged reason, got {reason!r}")
    stream = _stream(err, file)
    # Written verbatim: a statusline segment carries its own ANSI styling, which
    # click.echo would strip on the pipe Claude Code reads it from.
    with _lock:
        _close_open_lines()
        stream.write(f"{text}\n" if nl else text)
        stream.flush()


def _parse_record(line: str) -> Record | None:
    """The record a child ``ai`` wrote as one JSON line, or None for anything else."""
    if not line.startswith("{"):
        return None
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get(_RECORD_MARK) != 1:
        return None
    try:
        return _build(
            data["text"],
            tag=Tag(data["tag"]),
            at=datetime.fromisoformat(data["at"]),
            level=Level(data["level"]),
            op=data.get("op"),
            continued=data.get("continued") is True,
        )
    except (KeyError, TypeError, ValueError):
        return None


_TAGGED_LINE = re.compile(r"^\[(?P<tag>[a-z0-9_-]+)\] (?P<rest>.*)$")
_LEADING_OP = re.compile(r"^(?P<op>[a-z][a-z0-9-]*): ")
_LEADING_LEVEL = re.compile(r"^(?P<level>Warning|Error): ")


def _parse_tagged_line(line: str, op: str) -> Record | None:
    """A line a child already rendered as ``[tag] op: ...`` (an ``ai`` not asked for records), kept once.

    A line without an operation (an ``ai`` that predates them) takes ``op``. A word
    before a colon is the line's operation only when the vocabulary names it.
    """
    match = _TAGGED_LINE.match(line)
    if match is None or match["tag"] not in Tag._value2member_map_:
        return None
    rest = match["rest"]
    own_op = _LEADING_OP.match(rest)
    if own_op is not None and _known_op(own_op["op"]):
        op, rest = own_op["op"], rest[own_op.end() :]
    level = Level.INFO
    leading = _LEADING_LEVEL.match(rest)
    if leading is not None:
        level, rest = Level(leading["level"].lower()), rest[leading.end() :]
    return _build(rest, tag=Tag(match["tag"]), op=op, level=level)


def relay(tag: Tag, op: str, text: object, *, err: bool = True) -> None:
    """Emit a child process's output, one record per line.

    A line a child ``ai`` wrote as a record (see :data:`RECORD_RELAY_ENV`) is
    re-emitted once with the child's own tag, operation, level and time. A line
    already rendered as ``[<tag>] ...`` keeps its tag (and operation and level
    label) rather than being tagged twice. Any other line becomes the description
    of a ``tag``/``op`` record; a leading ``<tag>: `` the child prints is dropped,
    so ``dolt_server: healthy`` becomes ``[dolt_server] ensure: healthy``.
    """
    require_op(op)
    if not isinstance(text, str):
        return
    own = f"{_require_tag(tag).value}: "
    stream = _stream(err, None)
    # Split on LF only: a CR inside a line is the child redrawing it, which
    # inert() reduces to what a terminal would have shown.
    for line in text.split("\n"):
        shown = inert(line)
        if not shown.strip():
            continue
        record = _parse_record(line.strip()) or _parse_tagged_line(shown, op)
        if record is None:
            record = _build(shown[len(own) :] if shown.startswith(own) else shown, tag=tag, op=op)
        _write_records(stream, [record])


def record_relay_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """``env`` (the current environment when omitted) with a child ``ai`` asked to write records."""
    return {**(os.environ if env is None else env), RECORD_RELAY_ENV: "1"}


def run_relayed(tag: Tag, op: str, argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Run ``argv`` with its output captured, then relay every line under ``tag`` and ``op``.

    stderr is merged into stdout so the child's own ordering survives, and the
    result goes to stderr with the rest of the diagnostics. A child ``ai``
    process answers in records, so its lines keep their own tags.
    """
    _require_tag(tag)
    require_op(op)
    check = kwargs.pop("check", False)
    env = record_relay_env(kwargs.pop("env", None))
    completed = subprocess.run(
        list(argv),
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        env=env,
        **kwargs,
    )
    relay(tag, op, completed.stdout)
    return completed


class TaggedLogHandler(logging.Handler):
    """Render ``ai_cli`` log records as ``[log]`` lines on stderr.

    A stdlib logging record names no operation, so this adapter passes none: the
    line takes the configured default operation, or none when it is optional.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return
        if record.levelno >= logging.ERROR:
            level = Level.ERROR
        elif record.levelno >= logging.WARNING:
            level = Level.WARNING
        else:
            level = Level.INFO
        _emit(Tag.LOG, None, message, err=True, level=level)


def _install_log_handler() -> None:
    package_logger = logging.getLogger("ai_cli")
    if not any(isinstance(handler, TaggedLogHandler) for handler in package_logger.handlers):
        handler = TaggedLogHandler(level=logging.WARNING)
        package_logger.addHandler(handler)


_install_log_handler()
