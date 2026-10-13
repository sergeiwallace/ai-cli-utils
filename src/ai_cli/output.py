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

Colour is applied per token and reaches a TTY only; ``NO_COLOR`` and
``TERM=dumb`` turn it off there too. Nothing here owns terminal state, so a
launcher can exec into another program right after any call.

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
import logging
import os
import re
import subprocess
import sys
import threading
from collections.abc import Iterator, Sequence
from enum import StrEnum
from typing import Any, TextIO

import click

TAG_PATTERN = re.compile(r"^[a-z0-9_-]+$")
TAGGED_LINE = re.compile(r"^\[[a-z0-9_-]+\] ")

TAG_STYLE: dict[str, Any] = {"dim": True}
WARNING_STYLE: dict[str, Any] = {"bold": True, "fg": "yellow"}
ERROR_STYLE: dict[str, Any] = {"bold": True, "fg": "red"}


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


for _tag in Tag:
    if not TAG_PATTERN.match(_tag.value):
        raise ValueError(f"log type tag {_tag.value!r} does not match {TAG_PATTERN.pattern}")

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


def wants_color(stream: TextIO | None = None) -> bool:
    """True when styling should reach ``stream`` (stderr when omitted)."""
    # Click strips ANSI for a non-TTY on its own; the NO_COLOR convention
    # (no-color.org) and TERM=dumb are the two opt-outs it does not implement.
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    return _is_tty(stream if stream is not None else sys.stderr)


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


def _write(stream: TextIO, lines: list[str], *, nl: bool = True, color: bool | None = None) -> None:
    """Write rendered ``lines`` as one record: a single write, caller holds ``_lock``."""
    if color is None:
        color = wants_color(stream)
    eol = _eol(stream)
    lead = _CLEAR_ROW if _foreign_depth and eol == "\r\n" else ""
    text = eol.join(lead + (line if color else click.unstyle(line)) for line in lines)
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


def render(
    tag: Tag,
    text: str,
    *,
    label: str | None = None,
    label_style: dict[str, Any] | None = None,
    body_style: dict[str, Any] | None = None,
) -> str:
    """One tagged line, styled, its text made :func:`inert`; the style is stripped for a non-TTY."""
    prefix = click.style(f"[{_require_tag(tag).value}]", **TAG_STYLE)
    text = inert(text)
    body = click.style(text, **body_style) if body_style and text else text
    if label is None:
        return f"{prefix} {body}"
    styled_label = click.style(f"{label}:", **label_style) if label_style else f"{label}:"
    return f"{prefix} {styled_label} {body}".rstrip(" ")


def emit(
    tag: Tag,
    message: object = "",
    *,
    err: bool = False,
    file: TextIO | None = None,
    nl: bool = True,
    label: str | None = None,
    label_style: dict[str, Any] | None = None,
    body_style: dict[str, Any] | None = None,
) -> None:
    """Write ``message`` with ``[tag]`` on every line; blank lines are dropped.

    ``err`` sends it to stderr, ``file`` to an explicit stream, and ``nl=False``
    leaves the cursor after the last line (an inline prompt).
    """
    _require_tag(tag)
    lines = [line for line in (inert(part) for part in str(message).split("\n")) if line.strip()]
    if not lines and label is None:
        return
    if not lines:
        lines = [""]
    stream = _stream(err, file)
    color = wants_color(stream)
    rendered = [
        render(
            tag,
            line,
            label=label if index == 0 else None,
            label_style=label_style,
            body_style=body_style,
        )
        for index, line in enumerate(lines)
    ]
    with _lock:
        _close_open_lines()
        _write(stream, rendered, nl=nl, color=color)
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
    emit(tag, message, err=True, file=file, label="Warning", label_style=WARNING_STYLE)


def error(tag: Tag, message: object, *, file: TextIO | None = None) -> None:
    """``[tag] Error: message`` on stderr."""
    emit(tag, message, err=True, file=file, label="Error", label_style=ERROR_STYLE)


def confirm(tag: Tag, question: str, *, default: bool = False) -> bool:
    """A yes/no prompt whose prompt line carries ``[tag]``."""
    return click.confirm(render(tag, question.strip()), default=default)


def ask(tag: Tag, prompt: str, *, err: bool = False) -> str:
    """Read one line from stdin after a ``[tag]`` prompt (every prompt line tagged).

    On stdout the prompt goes through ``input()``; with ``err`` it is written to
    stderr and the answer read from ``sys.stdin``, for a prompt that must not
    pollute a stdout a caller captures.
    """
    stream = _stream(err, None)
    lines = [render(tag, line.strip()) for line in prompt.split("\n") if line.strip()]
    text = _eol(stream).join(lines) + " "
    if not wants_color(stream):
        text = click.unstyle(text)
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


def relay(tag: Tag, text: object, *, err: bool = True) -> None:
    """Emit a child process's output, one tagged line per line.

    A line the child already tagged (another ``ai`` command) is kept as it is
    rather than tagged twice. A leading ``<tag>: `` the child prints is dropped,
    so ``dolt_server: healthy`` becomes ``[dolt_server] healthy``.
    """
    if not isinstance(text, str):
        return
    own = f"{_require_tag(tag).value}: "
    stream = _stream(err, None)
    # Split on LF only: a CR inside a line is the child redrawing it, which
    # inert() reduces to what a terminal would have shown.
    for line in text.split("\n"):
        if not inert(line).strip():
            continue
        if TAGGED_LINE.match(inert(line)):
            with _lock:
                _close_open_lines()
                _write(stream, [inert(line)])
            continue
        emit(tag, line[len(own) :] if line.startswith(own) else line, err=err)


def run_relayed(tag: Tag, argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Run ``argv`` with its output captured, then relay every line under ``tag``.

    stderr is merged into stdout so the child's own ordering survives, and the
    result goes to stderr with the rest of the diagnostics.
    """
    _require_tag(tag)
    check = kwargs.pop("check", False)
    completed = subprocess.run(
        list(argv),
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        **kwargs,
    )
    relay(tag, completed.stdout)
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
