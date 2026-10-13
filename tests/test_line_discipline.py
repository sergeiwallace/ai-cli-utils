"""Through the real `ai` entry point, every record lands on its own row (AI-CLI-8h3u).

The founding transcripts showed `ai c -R` reattach lines run together, remote text
overwritten by local text, and the last line joined to the shell prompt. Those
defects only exist after a terminal interprets CR, LF and erase, so the terminal
tests run `ai_cli.main.cli()` with stdout and stderr on a real pseudo-terminal and
assert on the screen a VT100 model (pyte) renders from what reached the master
side. The ssh stub plays the foreign program: it turns output post-processing off
the way ssh's raw mode does, writes to the terminal directly, and writes stderr for
the tee to relay mid-attempt.

The pty tests are POSIX-only; the redirected-stream and concurrency tests run
everywhere, Windows included.
"""

from __future__ import annotations

import io
import os
import re
import select
import struct
import subprocess
import sys
import textwrap
import threading
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pyte
import pytest

from ai_cli import output as _out
from ai_cli.main import _REMOTE_SHELL_PROBE_CMD, cli
from ai_cli.output import Tag

if sys.platform != "win32":
    import fcntl
    import termios

    _REAL_TCGETATTR = termios.tcgetattr
    _REAL_TCSETATTR = termios.tcsetattr

posix_pty = pytest.mark.skipif(
    not hasattr(os, "openpty"), reason="a pseudo-terminal needs os.openpty(), which is POSIX-only"
)

COLUMNS = 160
ROWS = 60
DOLT_LINE = "dolt_server: healthy (socket accepted)"


def _remote_config(attempts: int) -> dict:
    return {
        "remote": {
            "default": "example-box",
            "machines": {
                "example-box": {
                    "host": "192.0.2.10",
                    "user": "exampleuser",
                    "transport": "ssh",
                    "reconnect_attempts": attempts,
                    "reconnect_backoff": 0.0,
                }
            },
        },
    }


@pytest.fixture(autouse=True)
def _state_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")


def _preflight_answering(dolt_stdout: str):
    def _preflight(command, **_kwargs):
        if isinstance(command, (list, tuple)) and any("dolt_server.py" in str(part) for part in command):
            return subprocess.CompletedProcess(command, 0, stdout=dolt_stdout, stderr=None)
        if isinstance(command, (list, tuple)) and command and command[-1] == _REMOTE_SHELL_PROBE_CMD:
            return subprocess.CompletedProcess(command, 0, stdout="zsh\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="current", stderr="")

    return _preflight


def _launch_patches(stack: ExitStack, tmp_path: Path, attempts: int, ssh, dolt_stdout: str, sleep=None) -> None:
    dolt_script = tmp_path / "dolt_server.py"
    dolt_script.write_text("", encoding="utf-8")
    config = {**_remote_config(attempts), "dolt_server": {"script_path": str(dolt_script)}}
    for target, kwargs in [
        ("sys.argv", {"new": ["ai", "c", "-R", "2"]}),
        ("ai_cli.main.sys.platform", {"new": "linux"}),
        ("ai_cli.main.shutil.which", {"return_value": "/usr/bin/tmux"}),
        ("ai_cli.config.load_config", {"return_value": config}),
        ("ai_cli.main.load_config", {"return_value": config}),
        ("ai_cli.session.get_project_prefix", {"return_value": "myproject"}),
        ("ai_cli.session.is_current_project_resolved", {"return_value": True}),
        ("ai_cli.config.get_project_aliases", {"return_value": {}}),
        ("ai_cli.config.get_current_project_name", {"return_value": "myproject"}),
        ("ai_cli.main.trigger_background_update", {}),
        ("ai_cli.iterm2._assign_iterm2_color_slot", {"return_value": None}),
        ("ai_cli.iterm2._emit_iterm2_profile_setup", {}),
        ("ai_cli.iterm2._current_pane_tty", {"return_value": None}),
        ("ai_cli.main.subprocess.run", {"side_effect": _preflight_answering(dolt_stdout)}),
        ("ai_cli.transport.time.sleep", {"side_effect": sleep}),
        ("ai_cli.transport.subprocess.call", {"side_effect": ssh}),
    ]:
        stack.enter_context(patch(target, **kwargs))


def _launch(tmp_path: Path, *, attempts: int, ssh, stdout, stderr, dolt_stdout: str = f"{DOLT_LINE}\n", sleep=None):
    with ExitStack() as stack:
        _launch_patches(stack, tmp_path, attempts, ssh, dolt_stdout, sleep)
        stack.enter_context(patch("sys.stdout", stdout))
        stack.enter_context(patch("sys.stderr", stderr))
        with pytest.raises(SystemExit) as exc_info:
            cli()
    return exc_info.value.code


class Terminal:
    """A pseudo-terminal whose master side is read into a VT100 screen model."""

    def __init__(self, columns: int = COLUMNS, rows: int = ROWS):
        self.columns, self.rows = columns, rows
        self.master, self.slave = os.openpty()
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        self.stream = io.TextIOWrapper(io.FileIO(self.slave, "w", closefd=False), encoding="utf-8", write_through=True)
        self.received = bytearray()
        self._stop = threading.Event()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        while not self._stop.is_set():
            self._pump(0.05)

    def _pump(self, timeout: float) -> bool:
        ready, _, _ = select.select([self.master], [], [], timeout)
        if not ready:
            return False
        try:
            chunk = os.read(self.master, 65536)
        except OSError:
            return False
        self.received += chunk
        return bool(chunk)

    def clear_output_flags(self, flags: int) -> None:
        attrs = _REAL_TCGETATTR(self.slave)
        attrs[1] &= ~flags
        _REAL_TCSETATTR(self.slave, termios.TCSANOW, attrs)

    def output_flags(self) -> int:
        return _REAL_TCGETATTR(self.slave)[1]

    def screen(self) -> pyte.Screen:
        self._stop.set()
        self._reader.join()
        while self._pump(0.2):
            pass
        return _render(bytes(self.received), self.columns, self.rows)[0]

    def close(self) -> None:
        self.stream.close()
        for fd in (self.master, self.slave):
            os.close(fd)


@pytest.fixture
def terminal():
    term = Terminal()
    yield term
    term.close()


# pyte does not parse a `<`-prefixed CSI (the kitty keyboard pop in the terminal-restore
# sequence) and would draw its final byte; a real terminal ignores what it does not know.
_UNMODELLED_CSI = re.compile(rb"\x1b\[<[0-9;]*u")


def _render(received: bytes, columns: int = COLUMNS, rows: int = ROWS) -> tuple[pyte.Screen, pyte.ByteStream]:
    screen = pyte.Screen(columns, rows)
    stream = pyte.ByteStream(screen)
    stream.feed(_UNMODELLED_CSI.sub(b"", received))
    return screen, stream


def _rows(screen: pyte.Screen) -> list[str]:
    return [row.rstrip() for row in screen.display]


def _wait(seconds: float) -> None:
    # time.sleep is patched for the whole launch (the backoff), so wait on an Event.
    threading.Event().wait(seconds)


@posix_pty
def test_given_a_raw_mode_pty_when_ai_c_r_reattaches_then_every_transport_line_starts_at_column_0(terminal, tmp_path):
    def ssh(_args, stderr=None, **_kwargs):
        terminal.clear_output_flags(termios.OPOST)
        os.write(terminal.slave, b"remote output, no newline yet")
        stderr.write(b"client_loop: send disconnect: Broken pipe\n")
        _wait(0.35)  # the tee relays every 0.1s: this line goes out while ssh holds the terminal
        return 255

    code = _launch(tmp_path, attempts=2, ssh=ssh, stdout=terminal.stream, stderr=terminal.stream)

    rows = _rows(terminal.screen())
    transport_rows = [row for row in rows if "[transport]" in row]
    assert code == 255
    assert len(transport_rows) >= 4, rows
    assert all(row.startswith("[transport] ") for row in transport_rows), rows


@posix_pty
def test_given_a_foreign_partial_line_when_a_record_follows_then_both_are_on_separate_rows(terminal, tmp_path):
    def ssh(_args, **_kwargs):
        os.write(terminal.slave, b"remote:~$ partial")
        return 255

    _launch(tmp_path, attempts=2, ssh=ssh, stdout=terminal.stream, stderr=terminal.stream)

    rows = _rows(terminal.screen())
    first = rows.index("remote:~$ partial")
    assert rows[first + 1].startswith("[transport] Connection lost (ssh exit 255)"), rows


@posix_pty
def test_given_a_bare_carriage_return_over_a_long_line_when_dolt_server_reports_then_no_stale_tail_remains(
    terminal, tmp_path
):
    """The `\\r`-without-newline case from the ask: `...this repo)sion mode)<user>@<host> %`."""
    long_line = b"[launch] Starting Claude Code session in this worktree (bypass permission mode)"
    dolt_line = b"[dolt_server] healthy (no beads store is routed to this repo)"

    def ssh(_args, **_kwargs):
        # The remote side's last two lines: a bare CR, then a shorter line and no newline.
        os.write(terminal.slave, long_line + b"\r" + dolt_line)
        return 0

    code = _launch(tmp_path, attempts=2, ssh=ssh, stdout=terminal.stream, stderr=terminal.stream)

    screen = terminal.screen()
    rows = _rows(screen)
    assert code == 0
    assert dolt_line.decode() in rows, rows
    assert not any("sion mode)" in row for row in rows), rows
    assert screen.cursor.x == 0
    assert rows[screen.cursor.y] == ""


@posix_pty
def test_given_a_returned_attempt_when_the_next_record_is_written_then_output_flags_are_restored(terminal, tmp_path):
    flags_at_backoff: list[int] = []

    def ssh(_args, **_kwargs):
        terminal.clear_output_flags(termios.OPOST | termios.ONLCR)
        return 255

    _launch(
        tmp_path,
        attempts=2,
        ssh=ssh,
        stdout=terminal.stream,
        stderr=terminal.stream,
        sleep=lambda _seconds: flags_at_backoff.append(terminal.output_flags()),
    )

    rows = _rows(terminal.screen())
    assert flags_at_backoff, "the reconnect backoff never ran"
    for flags in [*flags_at_backoff, terminal.output_flags()]:
        assert flags & termios.OPOST
        assert flags & termios.ONLCR
    assert all(row.startswith("[transport] ") for row in rows if "[transport]" in row), rows


@posix_pty
def test_given_termios_failing_when_a_record_is_written_then_it_is_still_written_and_nothing_raises(terminal, tmp_path):
    def ssh(_args, **_kwargs):
        terminal.clear_output_flags(termios.OPOST)
        return 255

    with patch("termios.tcgetattr", side_effect=termios.error(25, "Inappropriate ioctl for device")):
        code = _launch(tmp_path, attempts=2, ssh=ssh, stdout=terminal.stream, stderr=terminal.stream)

    rows = _rows(terminal.screen())
    transport_rows = [row for row in rows if "[transport]" in row]
    assert code == 255
    assert any("giving up" in row for row in rows), rows
    assert transport_rows
    assert all(row.startswith("[transport] ") for row in transport_rows), rows


_DRIVER = textwrap.dedent(
    """
    import os, sys, termios
    from contextlib import ExitStack
    from pathlib import Path
    from unittest.mock import patch

    sys.path[:0] = [sys.argv[1], sys.argv[2]]
    from tests import test_line_discipline as harness
    from ai_cli.main import cli

    def ssh(_args, **_kwargs):
        attrs = termios.tcgetattr(2)
        attrs[1] &= ~termios.OPOST
        termios.tcsetattr(2, termios.TCSANOW, attrs)
        os.write(1, b"remote:~$ partial")
        return 255

    with ExitStack() as stack:
        harness._launch_patches(stack, Path(sys.argv[3]), 2, ssh, harness.DOLT_LINE + "\\n")
        # No conftest guards this process: nothing may exec, re-install, or reach the network.
        stack.enter_context(patch("os.execvp", side_effect=AssertionError("the launch must not exec")))
        stack.enter_context(patch("ai_cli.main._auto_update_if_stale", return_value=False))
        stack.enter_context(patch("ai_cli.main._sync_launch_client", return_value=None))
        stack.enter_context(patch("ai_cli.main._track_launch", return_value=None))
        stack.enter_context(patch("ai_cli.main._tunnel._ensure_nats_tunnel"))
        cli()
    """
)


@posix_pty
def test_given_a_reattach_loop_that_gives_up_when_the_process_exits_then_the_prompt_starts_its_own_row(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    driver = tmp_path / "driver.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLUMNS, 0, 0))
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT"}}
    home = tmp_path / "home"
    env.update(
        HOME=str(home),
        XDG_STATE_HOME=str(home / "state"),
        XDG_CONFIG_HOME=str(home / "config"),
        XDG_DATA_HOME=str(home / "data"),
        XDG_CACHE_HOME=str(home / "cache"),
        TERM="xterm-256color",
        PYTHONDONTWRITEBYTECODE="1",
    )
    received = bytearray()
    try:
        child = subprocess.Popen(
            [sys.executable, str(driver), str(repo / "src"), str(repo), str(tmp_path)],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=env,
            cwd=tmp_path,
        )
        os.close(slave)
        slave = -1
        while True:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                received += chunk
            elif child.poll() is not None:
                break
        code = child.wait(timeout=60)
    finally:
        os.close(master)
        if slave >= 0:
            os.close(slave)

    screen, stream = _render(bytes(received))
    rows = _rows(screen)
    assert code == 255, received.decode(errors="replace")
    assert any("giving up" in row for row in rows), rows
    assert screen.cursor.x == 0, rows
    assert rows[screen.cursor.y] == "", rows
    prompt_row = screen.cursor.y
    stream.feed(b"user@host projects %")
    assert screen.display[prompt_row].rstrip() == "user@host projects %"


@posix_pty
def test_given_child_output_with_control_bytes_when_relayed_then_it_renders_on_one_row_without_escapes(
    terminal, tmp_path, capsys
):
    child = "dolt_server: starting 10%\rstarting 100% \x1b[31mred\x1b[0m\x08 done\x07\n"

    _launch(
        tmp_path,
        attempts=2,
        ssh=lambda *_a, **_k: 0,
        stdout=terminal.stream,
        stderr=terminal.stream,
        dolt_stdout=child,
    )
    rows = _rows(terminal.screen())
    received = bytes(terminal.received)
    _launch(tmp_path, attempts=2, ssh=lambda *_a, **_k: 0, stdout=sys.stdout, stderr=sys.stderr, dolt_stdout=child)
    redirected = capsys.readouterr().err

    assert "[dolt_server] starting 100% red done" in rows, rows
    for control in (b"\x1b[31m", b"\x08", b"\x07", b"10%"):
        assert control not in received, control
    assert "[dolt_server] starting 100% red done\n" in redirected
    assert not re.search(r"[\x00-\x09\x0b-\x1f\x7f]", redirected), repr(redirected)


TAGGED_RECORD = re.compile(r"^\[(?P<tag>[a-z0-9_-]+)\] (?:Warning: )?(?P<body>.*)$")


def test_given_concurrent_producers_when_records_are_written_then_no_line_interleaves(tmp_path):
    read_fd, write_fd = os.pipe()
    sink = io.TextIOWrapper(io.FileIO(write_fd, "w"), encoding="utf-8", write_through=True)
    chunks: list[bytes] = []
    reader = threading.Thread(target=lambda: chunks.extend(iter(lambda: os.read(read_fd, 65536), b"")))
    reader.start()
    heartbeat = "still connecting " + "h" * 300
    main_line = "main thread " + "m" * 300
    relayed = "ssh stderr " + "s" * 300

    def ssh(_args, stderr=None, **_kwargs):
        def beat():
            for _ in range(200):
                _out.emit(Tag.LAUNCH, heartbeat, err=True)

        ticker = threading.Thread(target=beat)
        ticker.start()
        for _ in range(200):
            stderr.write(relayed.encode() + b"\n")
            _out.warning(Tag.TRANSPORT, main_line)
        ticker.join()
        _wait(0.3)  # let the tee relay the rest while ssh still "runs"
        return 0

    try:
        _launch(tmp_path, attempts=1, ssh=ssh, stdout=sink, stderr=sink)
    finally:
        sink.close()
        reader.join()
        os.close(read_fd)

    lines = b"".join(chunks).decode().split("\n")
    assert lines[-1] == ""
    bodies = {heartbeat: 0, main_line: 0, relayed: 0}
    for line in lines[:-1]:
        match = TAGGED_RECORD.match(line)
        assert match, line[:120]
        if match["body"] in bodies:
            bodies[match["body"]] += 1
        else:
            assert not any(marker in line for marker in ("hhhh", "mmmm", "ssss")), line[:200]
    assert bodies == {heartbeat: 200, main_line: 200, relayed: 200}


def test_given_redirected_stderr_when_ai_c_runs_then_no_carriage_return_or_escape_is_written(tmp_path, capsys):
    def ssh(_args, stderr=None, **_kwargs):
        stderr.write(b"Warning: \x1b[1mbold\x1b[0m\r\nprogress 1\rprogress 2\n")
        return 255

    code = _launch(tmp_path, attempts=2, ssh=ssh, stdout=sys.stdout, stderr=sys.stderr)

    captured = capsys.readouterr()
    emitted = captured.out + captured.err
    assert code == 255
    assert "[transport] progress 2\n" in captured.err
    assert "[transport] Warning: bold\n" in captured.err
    assert "\r" not in emitted
    assert "\x1b" not in emitted
