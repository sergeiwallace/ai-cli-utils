"""The one terminal-output interface for ai-cli-utils.

Every line this package prints goes through :func:`emit` (or its ``warning`` /
``error`` shorthands) and is rendered as::

    [<tag>] <text>
    [<tag>] Warning: <text>
    [<tag>] Error: <text>

``tag`` must be a :class:`Tag` member: a plain string is refused with
``TypeError``, so an untagged line cannot be produced by passing the wrong
thing. Output that a program parses (JSON, ``ai internal`` replies, the
statusline segment, ``--version``) or that is not a line at all (terminal
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
import json
import logging
import os
import re
import subprocess
import sys
import threading
from collections.abc import Iterator, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, TextIO

import click

if TYPE_CHECKING:
    from rich.console import Console
    from rich.text import Text

TAG_PATTERN = re.compile(r"^[a-z0-9_-]+$")
OPERATION_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")

# Rich style strings for each element. T-3 (AI-CLI-mu4d) moves them into config.
TAG_STYLE = "bold blue"
OPERATION_STYLE = "bold cyan"
LABEL_STYLE = "bold cyan"
WARNING_STYLE = "bold yellow"
ERROR_STYLE = "bold red"

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


LEVEL_LABELS = {Level.WARNING: ("Warning:", WARNING_STYLE), Level.ERROR: ("Error:", ERROR_STYLE)}


for _tag in Tag:
    if not TAG_PATTERN.match(_tag.value):
        raise ValueError(f"log type tag {_tag.value!r} does not match {TAG_PATTERN.pattern}")


@dataclass(frozen=True)
class Record:
    """One logical line, built once by the structlog pipeline and rendered per sink.

    ``label`` (with ``label_style``) is the launch reporter's phase name, which
    becomes the operation element in T-5 (AI-CLI-rbuf). ``continued`` marks the
    second and later lines of a multi-line message, which repeat the tag but not
    the label or level.
    """

    at: datetime
    tag: Tag
    text: str
    level: Level = Level.INFO
    op: str | None = None
    label: str | None = None
    label_style: str = ""
    text_style: str = ""
    continued: bool = False


_lock = threading.RLock()
_open_lines: dict[int, Tag] = {}
_open_streams: dict[int, TextIO] = {}
_foreign_depth = 0

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


def _write(stream: TextIO, lines: list[str], *, nl: bool = True) -> None:
    """Write rendered ``lines`` as one record: a single write, caller holds ``_lock``."""
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
            with contextlib.suppress(ValueError, OSError):
                stream.write(_eol(stream))
                stream.flush()


def flush_sinks() -> None:
    """Close any open line and flush both streams: run before an exec or a handoff."""
    with _lock:
        _close_open_lines()
        for stream in (sys.stdout, sys.stderr):
            with contextlib.suppress(AttributeError, ValueError, OSError):
                stream.flush()


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


def _elements(record: Record) -> list[tuple[str, str]]:
    parts = [(f"[{record.tag.value}]", TAG_STYLE)]
    if record.op is not None:
        parts.append((f"{record.op}:", OPERATION_STYLE))
    if not record.continued:
        if record.label is not None:
            parts.append((f"{record.label}:", record.label_style or LABEL_STYLE))
        if record.level in LEVEL_LABELS:
            parts.append(LEVEL_LABELS[record.level])
    if record.text:
        parts.append((record.text, record.text_style))
    return parts


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


def _styled_text(record: Record) -> Text:
    from rich.text import Text

    line = Text()
    for index, (value, style) in enumerate(_elements(record)):
        if index:
            line.append(" ")
        line.append(value, style=style)
    return line


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


def render_record(record: Record, *, color: bool, stream: TextIO | None = None) -> str:
    """``record`` as one line with no terminator; styled when ``color``, plain otherwise."""
    if not color:
        return " ".join(value for value, _style in _elements(record))
    target = stream if stream is not None else sys.stderr
    console = _console(target)
    if _legacy_windows_console(target, console):
        return " ".join(value for value, _style in _elements(record))
    with _lock, console.capture() as capture:
        console.print(_styled_text(record), end="")
    return capture.get()


def _relay_json(record: Record) -> str:
    payload = {
        _RECORD_MARK: 1,
        "at": record.at.isoformat(),
        "tag": record.tag.value,
        "level": record.level.value,
        "op": record.op,
        "label": record.label,
        "text": record.text,
        "continued": record.continued,
    }
    return json.dumps(payload, ensure_ascii=False)


def _write_records(stream: TextIO, records: list[Record], *, nl: bool = True) -> None:
    with _lock:
        _close_open_lines()
        if _relay_records:
            _write(stream, [_relay_json(record) for record in records], nl=nl)
            return
        color = wants_color(stream)
        _write(stream, [render_record(record, color=color, stream=stream) for record in records], nl=nl)


def render(tag: Tag, text: str, *, stream: TextIO | None = None) -> str:
    """One tagged line for a prompt, styled when ``stream`` (stderr when omitted) wants colour."""
    target = stream if stream is not None else sys.stderr
    return render_record(_build(text, tag=tag), color=wants_color(target), stream=target)


def emit(
    tag: Tag,
    message: object = "",
    *,
    err: bool = False,
    file: TextIO | None = None,
    nl: bool = True,
    op: str | None = None,
    level: Level = Level.INFO,
    label: str | None = None,
    label_style: str = "",
    body_style: str = "",
) -> None:
    """Write ``message`` with ``[tag]`` on every line; blank lines are dropped.

    ``err`` sends it to stderr, ``file`` to an explicit stream, and ``nl=False``
    leaves the cursor after the last line (an inline prompt). ``label`` and the
    two styles are the launch reporter's phase grammar.
    """
    _require_tag(tag)
    lines = [line for line in (inert(part) for part in str(message).split("\n")) if line.strip()]
    if not lines and label is None:
        return
    if not lines:
        lines = [""]
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
            label=label,
            label_style=label_style,
            text_style=body_style,
            continued=index > 0,
        )
        for index, line in enumerate(lines)
    ]
    with _lock:
        _write_records(stream, records, nl=nl)
        if not nl:
            _open_lines[id(stream)] = tag
            _open_streams[id(stream)] = stream


def cont(tag: Tag, message: object, *, err: bool = False, file: TextIO | None = None) -> None:
    """Finish the line an ``emit(tag, ..., nl=False)`` left open on the same stream.

    If that line was already closed (another line was emitted in between), the
    text starts a fresh ``[tag]`` line instead, so a continuation can never
    produce an untagged line.
    """
    _require_tag(tag)
    stream = _stream(err, file)
    first, _, rest = str(message).partition("\n")
    with _lock:
        if _open_lines.pop(id(stream), None) is None:
            emit(tag, message, err=err, file=file)
            return
        _open_streams.pop(id(stream), None)
        _write(stream, [inert(first)])
    if rest:
        emit(tag, rest, err=err, file=file)


def warning(tag: Tag, message: object, *, file: TextIO | None = None) -> None:
    """``[tag] Warning: message`` on stderr."""
    emit(tag, message, err=True, file=file, level=Level.WARNING)


def error(tag: Tag, message: object, *, file: TextIO | None = None) -> None:
    """``[tag] Error: message`` on stderr."""
    emit(tag, message, err=True, file=file, level=Level.ERROR)


def confirm(tag: Tag, question: str, *, default: bool = False) -> bool:
    """A yes/no prompt whose prompt line carries ``[tag]``."""
    return click.confirm(render(tag, question.strip(), stream=sys.stdout), default=default)


def ask(tag: Tag, prompt: str, *, err: bool = False) -> str:
    """Read one line from stdin after a ``[tag]`` prompt (every prompt line tagged).

    On stdout the prompt goes through ``input()``; with ``err`` it is written to
    stderr and the answer read from ``sys.stdin``, for a prompt that must not
    pollute a stdout a caller captures.
    """
    stream = _stream(err, None)
    lines = [render(tag, line.strip(), stream=stream) for line in prompt.split("\n") if line.strip()]
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
        emit(Tag.CLI, exc.ctx.get_usage(), err=True)
        if exc.ctx.command.get_help_option(exc.ctx) is not None:
            emit(Tag.CLI, f"Try '{exc.ctx.command_path} {exc.ctx.help_option_names[0]}' for help.", err=True)
    error(Tag.CLI, exc.format_message())


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
        label = data.get("label")
        return _build(
            data["text"],
            tag=Tag(data["tag"]),
            at=datetime.fromisoformat(data["at"]),
            level=Level(data["level"]),
            op=data.get("op"),
            label=label if isinstance(label, str) else None,
            continued=data.get("continued") is True,
        )
    except (KeyError, TypeError, ValueError):
        return None


_TAGGED_LINE = re.compile(r"^\[(?P<tag>[a-z0-9_-]+)\] (?:(?P<level>Warning|Error): )?(?P<text>.*)$")


def _parse_tagged_line(line: str) -> Record | None:
    """A line a child already rendered as ``[tag] ...`` (an ``ai`` not asked for records), kept once."""
    match = _TAGGED_LINE.match(line)
    if match is None or match["tag"] not in Tag._value2member_map_:
        return None
    level = {"Warning": Level.WARNING, "Error": Level.ERROR}.get(match["level"] or "", Level.INFO)
    return _build(match["text"], tag=Tag(match["tag"]), level=level)


def relay(tag: Tag, text: object, *, err: bool = True, op: str | None = None) -> None:
    """Emit a child process's output, one record per line.

    A line a child ``ai`` wrote as a record (see :data:`RECORD_RELAY_ENV`) is
    re-emitted once with the child's own tag, operation, level and time. A line
    already rendered as ``[<tag>] ...`` keeps its tag (and level label) rather
    than being tagged twice. Any other line becomes the description of a
    ``tag``/``op`` record; a leading
    ``<tag>: `` the child prints is dropped, so ``dolt_server: healthy`` becomes
    ``[dolt_server] healthy``.
    """
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
        record = _parse_record(line.strip()) or _parse_tagged_line(shown)
        if record is None:
            record = _build(shown[len(own) :] if shown.startswith(own) else shown, tag=tag, op=op)
        _write_records(stream, [record])


def record_relay_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """``env`` (the current environment when omitted) with a child ``ai`` asked to write records."""
    return {**(os.environ if env is None else env), RECORD_RELAY_ENV: "1"}


def run_relayed(
    tag: Tag, argv: Sequence[str], *, op: str | None = None, **kwargs: Any
) -> subprocess.CompletedProcess[str]:
    """Run ``argv`` with its output captured, then relay every line under ``tag``.

    stderr is merged into stdout so the child's own ordering survives, and the
    result goes to stderr with the rest of the diagnostics. A child ``ai``
    process answers in records, so its lines keep their own tags.
    """
    _require_tag(tag)
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
    relay(tag, completed.stdout, op=op)
    return completed


class TaggedLogHandler(logging.Handler):
    """Render ``ai_cli`` log records as ``[log]`` lines on stderr."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return
        if record.levelno >= logging.ERROR:
            error(Tag.LOG, message)
        elif record.levelno >= logging.WARNING:
            warning(Tag.LOG, message)
        else:
            emit(Tag.LOG, message, err=True)


def _install_log_handler() -> None:
    package_logger = logging.getLogger("ai_cli")
    if not any(isinstance(handler, TaggedLogHandler) for handler in package_logger.handlers):
        handler = TaggedLogHandler(level=logging.WARNING)
        package_logger.addHandler(handler)


_install_log_handler()
