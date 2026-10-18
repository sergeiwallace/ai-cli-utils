"""A bare ``ai c`` launch hosts the agent on a pty the launcher owns (AI-CLI-0nvi).

The auto-compact prompt is submitted by writing to the terminal that owns the agent.
tmux gives a submitter that terminal; a bare launch used to ``execvp`` the agent, and
nothing could ever type into it afterwards. These tests run ``ai_cli.main.cli()``
in-process with the operator's terminal on a real pseudo-terminal, a stand-in
``claude`` on PATH, and assert on what the stand-in READ from its own terminal and on
the screen a VT100 model (pyte) renders from what reached the operator.

POSIX-only: the pty transport does not exist on Windows, where a bare launch keeps
its ``execvp`` (the no-pty-API test pins that path on every platform).
"""

from __future__ import annotations

import io
import os
import shutil
import signal
import struct
import subprocess
import sys
import textwrap
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pyte
import pytest

from ai_cli import compact_transport
from ai_cli.main import cli

if sys.platform != "win32":
    import fcntl
    import termios

posix_pty = pytest.mark.skipif(not hasattr(os, "openpty"), reason="the pty transport is POSIX-only")

#: Bound at import, before the suite's autouse guard replaces it for the test body.
_REAL_POPEN = subprocess.Popen

COLUMNS, ROWS = 100, 30
_WAIT = 20.0

_AGENT = textwrap.dedent(
    """\
    import fcntl, os, signal, struct, sys, termios, tty

    out = os.environ["FAKE_AGENT_DIR"]

    def size():
        rows, cols, _, _ = struct.unpack("HHHH", fcntl.ioctl(0, termios.TIOCGWINSZ, b"\\0" * 8))
        return f"{rows}x{cols}"

    def record(name, text):
        with open(os.path.join(out, name), "a") as handle:
            handle.write(text)

    signal.signal(signal.SIGWINCH, lambda *_: record("sizes", size() + "\\n"))
    tty.setraw(0)
    record("env", os.environ.get("AI_CLI_SUBMIT_FIFO", ""))
    record("pid", str(os.getpid()))
    record("sizes", size() + "\\n")
    # Hide the cursor and enable mouse reporting, as a full-screen agent does, so a
    # launcher that skips its restore leaves them set on the operator's terminal.
    os.write(1, b"\\x1b[?25l\\x1b[?1000hAGENT READY\\r\\n")
    seen = b""
    while True:
        chunk = os.read(0, 1024)
        if not chunk:
            break
        seen += chunk
        with open(os.path.join(out, "read"), "ab") as handle:
            handle.write(chunk)
        os.write(1, b"agent read " + chunk.replace(b"\\x03", b"^C").replace(b"\\r", b"<CR>") + b"\\r\\n")
        if b"EXIT" in seen:
            sys.exit(7)
    """
)


class OperatorTerminal:
    """The operator's terminal: a pty whose master side is rendered by pyte."""

    def __init__(self) -> None:
        self.master, self.slave = os.openpty()
        self.resize(ROWS, COLUMNS)
        self.screen = pyte.Screen(COLUMNS, ROWS)
        self._stream = pyte.ByteStream(self.screen)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        self.stdin = io.TextIOWrapper(io.FileIO(self.slave, "r", closefd=False), encoding="utf-8")
        self.stdout = io.TextIOWrapper(io.FileIO(self.slave, "w", closefd=False), encoding="utf-8", write_through=True)

    def resize(self, rows: int, columns: int) -> None:
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))

    def _read(self) -> None:
        import select

        while not self._stop.is_set():
            ready, _, _ = select.select([self.master], [], [], 0.05)
            if not ready:
                continue
            try:
                data = os.read(self.master, 65536)
            except OSError:
                return
            with self._lock:
                self._stream.feed(data)

    def type(self, data: bytes) -> None:
        os.write(self.master, data)

    def text(self) -> str:
        with self._lock:
            return "\n".join(self.screen.display)

    def cursor_hidden(self) -> bool:
        with self._lock:
            return self.screen.cursor.hidden

    def close(self) -> None:
        self._stop.set()
        self._reader.join(timeout=2)
        for fd in (self.master, self.slave):
            os.close(fd)


def _wait_for(predicate, what: str) -> None:
    deadline = time.monotonic() + _WAIT
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    repo = tmp_path / "myproject"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    monkeypatch.chdir(repo)
    return repo


@pytest.fixture
def agent(tmp_path, monkeypatch):
    """A stand-in ``claude`` first on PATH, recording what it reads into a directory."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text(f"#!{sys.executable}\n{_AGENT}", encoding="utf-8")
    stub.chmod(0o755)
    record = tmp_path / "agent"
    record.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_AGENT_DIR", str(record))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CODE_TASK_LIST_ID", "unset-by-test")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    return record


def _stub_only_popen(stub_root: Path):
    """Let the launcher spawn the test's stand-in agent and nothing else protected."""

    def popen(args, *rest, **kwargs):
        program = kwargs.get("executable") or args[0]
        resolved = shutil.which(program)
        if Path(program).name == "claude":
            assert resolved and Path(resolved).resolve().is_relative_to(stub_root.resolve()), (
                f"the launcher tried to spawn a real {program!r}"
            )
        return _REAL_POPEN(args, *rest, **kwargs)

    return popen


def _launch_patches(stack: ExitStack, config: dict, argv: list[str], stream) -> None:
    for target, kwargs in [
        ("sys.argv", {"new": argv}),
        ("ai_cli.config.load_config", {"return_value": config}),
        ("ai_cli.main.load_config", {"return_value": config}),
        ("ai_cli.session.get_project_prefix", {"return_value": "myproject"}),
        ("ai_cli.session.is_current_project_resolved", {"return_value": True}),
        ("ai_cli.config.get_current_project_name", {"return_value": "myproject"}),
        ("ai_cli.config.get_session_map", {"return_value": {}}),
        ("ai_cli.config.validate_registry_completeness", {"return_value": True}),
        ("ai_cli.session._resolve_is_remote", {"return_value": False}),
        ("ai_cli.session.cleanup_stale_sessions", {}),
        ("ai_cli.trust.ensure_workspace_trusted", {}),
        ("ai_cli.main.trigger_background_update", {}),
        ("ai_cli.main._auto_update_if_stale", {}),
        ("ai_cli.main._ensure_dolt_server", {}),
        ("ai_cli.iterm2._assign_iterm2_color_slot", {"return_value": None}),
        ("ai_cli.iterm2._emit_iterm2_profile_setup", {}),
        ("ai_cli.iterm2._current_pane_tty", {"return_value": None}),
        ("sys.stdout", {"new": stream}),
        ("sys.stderr", {"new": stream}),
    ]:
        stack.enter_context(patch(target, **kwargs))


def _launch(terminal: OperatorTerminal, tmp_path: Path, config: dict) -> int:
    """Run ``ai c -b`` through ``cli()`` with the operator's terminal on *terminal*; return the exit code."""
    with ExitStack() as stack:
        _launch_patches(stack, config, ["ai", "c", "-b", "-W", "1"], terminal.stdout)
        stack.enter_context(patch("sys.stdin", terminal.stdin))
        stack.enter_context(patch("subprocess.Popen", side_effect=_stub_only_popen(tmp_path / "bin")))
        with pytest.raises(SystemExit) as exc_info:
            cli()
    assert isinstance(exc_info.value.code, int)
    return exc_info.value.code


_BARE = {"session": {"use_tmux": False}, "worktree": {"enabled": False}}


class _Session:
    """A bare launch running on the main thread while a helper thread drives it."""

    def __init__(self, tmp_path: Path, agent: Path, drive) -> None:
        self.terminal = OperatorTerminal()
        self.agent = agent
        self.before = _modes(self.terminal.slave)
        self.error: BaseException | None = None
        self._drive = drive
        self._tmp_path = tmp_path

    def ready(self) -> bool:
        return "AGENT READY" in self.terminal.text()

    def read(self) -> bytes:
        path = self.agent / "read"
        return path.read_bytes() if path.exists() else b""

    def fifo(self) -> Path:
        return Path((self.agent / "env").read_text())

    def run(self) -> int:
        def drive() -> None:
            try:
                _wait_for(self.ready, "the agent to start on the pty")
                self._drive(self)
            except BaseException as exc:  # reported on the main thread below
                self.error = exc
                # A broken relay may not deliver "EXIT", so end the agent directly
                # rather than leave the launch on the main thread waiting forever.
                pid = self.agent / "pid"
                if pid.exists():
                    os.kill(int(pid.read_text()), signal.SIGKILL)

        helper = threading.Thread(target=drive, daemon=True)
        helper.start()
        try:
            code = _launch(self.terminal, self._tmp_path, _BARE)
        finally:
            helper.join(timeout=_WAIT)
        if self.error is not None:
            raise self.error
        return code


@pytest.fixture
def session_factory(tmp_path, agent, repo):
    made: list[_Session] = []

    def make(drive) -> _Session:
        session = _Session(tmp_path, agent, drive)
        made.append(session)
        return session

    yield make
    for session in made:
        session.terminal.close()


# --- AC-1, AC-2: the submission reaches what the agent reads -------------------


@posix_pty
def test_given_a_bare_launch_when_text_is_written_to_the_submission_fifo_then_the_agent_reads_it(session_factory):
    def drive(session: _Session) -> None:
        fifo = session.fifo()
        assert fifo.is_fifo(), f"the agent was told its submission FIFO is {fifo}, which is not a FIFO"
        with fifo.open("wb", buffering=0) as handle:
            handle.write(b"/compact\r")
        _wait_for(lambda: b"/compact\r" in session.read(), "the agent to read the submission")
        session.terminal.type(b"EXIT")

    session = session_factory(drive)
    code = session.run()

    assert b"/compact\r" in session.read(), "what the agent READ must carry the submission, byte for byte"
    assert code == 7, "the launcher must exit with the agent's own status"
    assert not session.fifo().exists(), "the FIFO must be removed when the session ends"


# --- The operator-input sidecar the FIFO compact route proves idleness from -----


def _input_at(session: _Session) -> Path:
    return session.fifo().with_name(session.fifo().name.removesuffix(".in") + ".input-at")


@posix_pty
def test_given_a_bare_pty_session_when_the_operator_types_then_the_input_sidecar_records_that_time(session_factory):
    seen: dict[str, object] = {}

    def drive(session: _Session) -> None:
        sidecar = _input_at(session)
        _wait_for(sidecar.exists, "the input sidecar to exist from launch")
        seen["at_launch"] = float(sidecar.read_text())
        seen["mode"] = sidecar.stat().st_mode & 0o777
        time.sleep(0.05)
        before = time.time()
        session.terminal.type(b"hi")
        _wait_for(lambda: b"hi" in session.read(), "typed keys to reach the agent")
        _wait_for(lambda: float(sidecar.read_text()) >= before, "the sidecar to record the typed input")
        seen["after_typing"] = float(sidecar.read_text())
        seen["before_typing"] = before
        seen["path"] = sidecar
        session.terminal.type(b"EXIT")

    session = session_factory(drive)
    session.run()

    assert isinstance(seen["mode"], int)
    assert isinstance(seen["after_typing"], float)
    assert isinstance(seen["before_typing"], float)
    assert isinstance(seen["at_launch"], float)
    assert isinstance(seen["path"], Path)
    assert seen["mode"] == 0o600, f"the sidecar must be private to the operator, got {oct(seen['mode'])}"
    assert seen["after_typing"] >= seen["before_typing"] > seen["at_launch"], (
        "typing must move the recorded time past the moment the keys were sent"
    )
    assert not seen["path"].exists(), "the sidecar must be removed when the session ends, with the FIFO"


@posix_pty
def test_given_a_bare_pty_session_when_only_the_fifo_submits_then_the_input_sidecar_does_not_move(session_factory):
    """Negative control: a submission is not operator input, so the idle proof must not see one."""
    seen: dict[str, float] = {}

    def drive(session: _Session) -> None:
        sidecar = _input_at(session)
        _wait_for(sidecar.exists, "the input sidecar to exist from launch")
        seen["at_launch"] = float(sidecar.read_text())
        with session.fifo().open("wb", buffering=0) as handle:
            handle.write(b"/compact\r")
        _wait_for(lambda: b"/compact\r" in session.read(), "the agent to read the submission")
        seen["after_submit"] = float(sidecar.read_text())
        session.terminal.type(b"EXIT")

    session = session_factory(drive)
    session.run()

    assert seen["after_submit"] == seen["at_launch"], "a FIFO submission must not count as operator input"


# --- AC-3: the negative control ------------------------------------------------


def test_given_no_pty_api_when_a_bare_launch_runs_then_no_submission_fifo_exists(tmp_path, agent, repo):
    """With no pty transport the launch still execs the agent, and nothing can be injected."""
    no_pty = compact_transport.PtyProbe(False, "this platform has no os.openpty()", platform_supported=False)
    execs: list[tuple[str, list[str]]] = []

    def fake_execvp(file, args):
        execs.append((file, list(args)))
        raise SystemExit(0)

    hosted: list[str] = []
    stream = io.StringIO()
    with ExitStack() as stack:
        _launch_patches(stack, _BARE, ["ai", "c", "-b", "-W", "1"], stream)
        stack.enter_context(patch("ai_cli.compact_transport.probe_pty", return_value=no_pty))
        stack.enter_context(patch("ai_cli.main.os.execvp", side_effect=fake_execvp))
        stack.enter_context(patch("ai_cli.main._pty_handoff", side_effect=hosted.append))
        with pytest.raises(SystemExit):
            cli()

    assert hosted == [], "with no pty transport the session must not be hosted on a pty"
    assert [file for file, _ in execs] == ["claude"], "the bare launch must keep replacing itself with the agent"
    fifos = list((tmp_path / "state").rglob("*.in"))
    assert fifos == [], f"no submission FIFO may exist without a pty to carry it: {fifos}"


# --- AC-4: keystrokes and Ctrl-C reach the agent unchanged ---------------------


@posix_pty
def test_given_a_bare_pty_session_when_the_operator_types_then_keys_and_ctrl_c_reach_the_agent_unchanged(
    session_factory,
):
    def drive(session: _Session) -> None:
        session.terminal.type(b"hi")
        _wait_for(lambda: b"hi" in session.read(), "typed keys to reach the agent without a newline")
        session.terminal.type(b"\x03")
        _wait_for(lambda: b"\x03" in session.read(), "Ctrl-C to reach the agent as a byte")
        _wait_for(lambda: "agent read ^C" in session.terminal.text(), "the agent's output to reach the screen")
        session.terminal.type(b"EXIT")

    session = session_factory(drive)
    code = session.run()

    assert session.read().startswith(b"hi\x03"), "the operator's keys must arrive unchanged and in order"
    assert code == 7, "Ctrl-C must go to the agent, not interrupt the launcher"


# --- AC-5: resize --------------------------------------------------------------


@posix_pty
def test_given_a_bare_pty_session_when_the_terminal_is_resized_then_the_agent_sees_the_new_size(session_factory):
    def drive(session: _Session) -> None:
        session.terminal.resize(41, 132)
        os.kill(os.getpid(), signal.SIGWINCH)
        _wait_for(lambda: "41x132" in (session.agent / "sizes").read_text(), "the agent to see the new size")
        session.terminal.type(b"EXIT")

    session = session_factory(drive)
    session.run()

    sizes = (session.agent / "sizes").read_text().split()
    assert sizes[0] == f"{ROWS}x{COLUMNS}", "the agent must start at the operator's size"
    assert sizes[-1] == "41x132"


# --- AC-6: exit status and terminal state on every exit path --------------------


def _modes(fd: int) -> list:
    attrs = termios.tcgetattr(fd)
    # PENDIN is the kernel's own "reprint pending input" marker, which macOS sets when
    # a terminal returns to canonical mode; it is state, not a mode anyone chose.
    attrs[3] &= ~getattr(termios, "PENDIN", 0)
    return attrs


def _assert_terminal_restored(session: _Session) -> None:
    assert _modes(session.terminal.slave) == session.before, (
        "the operator's terminal must leave raw mode exactly as it entered it"
    )
    _wait_for(lambda: not session.terminal.cursor_hidden(), "the cursor the agent hid to be shown again")
    assert not session.fifo().exists(), "the FIFO must be removed on every exit path"


@posix_pty
def test_given_a_bare_pty_session_when_the_agent_exits_then_status_and_terminal_are_restored(session_factory):
    session = session_factory(lambda s: s.terminal.type(b"EXIT"))
    code = session.run()

    assert code == 7
    _assert_terminal_restored(session)


@posix_pty
@pytest.mark.parametrize("signame", ["SIGTERM", "SIGHUP"])
def test_given_a_bare_pty_session_when_the_launcher_gets_a_termination_signal_then_it_exits_128_plus_signum_restored(
    session_factory, signame
):
    signum = getattr(signal, signame)
    session = session_factory(lambda s: os.kill(os.getpid(), signum))
    code = session.run()

    assert code == 128 + signum, f"{signame} must end the launcher with 128 + signum after the agent exits"
    _assert_terminal_restored(session)


# --- AC-7: a tmux launch is unchanged ------------------------------------------


@posix_pty
def test_given_tmux_hosts_the_session_when_launched_then_no_pty_is_hosted(tmp_path, agent, repo):
    probes: list[int] = []
    hosted: list[str] = []
    execs: list[str] = []

    def fake_execvp(file, args):
        execs.append(file)
        raise SystemExit(0)

    stream = io.StringIO()
    with ExitStack() as stack:
        _launch_patches(stack, {"worktree": {"enabled": False}}, ["ai", "c", "-o", "-W", "1"], stream)
        for target, kwargs in [
            ("ai_cli.main._tmux_setup.tmux_runs", {"return_value": True}),
            ("ai_cli.main._tmux_setup.formats_expand", {"return_value": True}),
            ("ai_cli.session._tmux_session_names", {"return_value": []}),
            ("ai_cli.compact_transport.probe_pty", {"side_effect": lambda: probes.append(1)}),
            ("ai_cli.main._pty_handoff", {"side_effect": hosted.append}),
            ("ai_cli.main.os.execvp", {"side_effect": fake_execvp}),
        ]:
            stack.enter_context(patch(target, **kwargs))
        with pytest.raises(SystemExit):
            cli()

    assert execs == ["tmux"], f"a tmux launch must still exec tmux: {stream.getvalue()}"
    assert probes == [] and hosted == [], "a tmux launch must allocate no pty"
    assert list((tmp_path / "state").rglob("*.in")) == []
