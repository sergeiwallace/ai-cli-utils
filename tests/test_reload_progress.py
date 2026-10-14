"""One reload row that gets louder when it stalls (AI-CLI-zss5, design T-6).

A child body that finds its session script rewritten exits 78, and the supervisor starts a fresh
one. Each reload used to print the same `[session] session script updated — reloading...` line,
so a loop of them scrolled one identical row per iteration with no count and no elapsed time.
The supervisor now owns one in-place row with the reason, the attempt and the time since the
first consecutive reload, escalating to a warning and then to a persistent error.

These tests run the generated script the way `ai c` hands it to tmux: the stable script under the
state directory is both the supervisor and every child body, on a real pseudo-terminal it controls
as a session leader. Only `ai` (apart from `internal emit` and `internal progress`, which reach
the real CLI), `claude`, `tmux` and `direnv` are stand-ins. A reload is made the way a concurrent
`ai c` makes one: the stand-in `ai ps cron`, which every child runs after taking its baseline
mtime and before its loop-top check, gives the stable script a new mtime. Assertions read the
screen a VT100 model renders, because an in-place row only exists once a terminal has
interpreted the carriage returns and erases.
"""

from __future__ import annotations

import sys

import pytest

# `fcntl` and `termios` do not exist on Windows, so this module cannot be imported there.
if sys.platform == "win32":
    pytest.skip("POSIX-only: the session supervisor is a POSIX shell script on a pty", allow_module_level=True)

import contextlib
import fcntl
import os
import re
import signal
import struct
import subprocess
import termios
import threading
import time
from pathlib import Path

import psutil
import pyte

from ai_cli.session_script import get_engine_script, resolve_session_shell

REPO = Path(__file__).resolve().parents[1]
COLUMNS, ROWS = 240, 48  # wide enough that no record wraps
SESSION = "test-session"
_CEILING_SECONDS = max(float(os.environ.get("AI_CLI_TEST_PROCESS_WAIT_SECONDS", "60")), 240.0)

# Run on the slave as a session leader: take it as the controlling terminal, then become the shell.
_CONTROLLING_EXEC = (
    "import fcntl, os, sys, termios; fcntl.ioctl(0, termios.TIOCSCTTY, 0); os.execvp(sys.argv[1], sys.argv[1:])"
)
# pyte does not parse a `<`-prefixed CSI (the kitty keyboard pop in the agent-terminal reset).
_UNMODELLED_CSI = re.compile(rb"\x1b\[<[0-9;]*u")
_SGR = re.compile(r"\x1b\[[0-9;]*m")
_RELOAD_ROW = "reloading (attempt "
# A rendered record may lead with its datetime element.
_SESSION_LINE = re.compile(r"^(?:\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [A-Z]{2,5} )?\[session\] reload: ")


class Session:
    """The generated script on a pty (or on pipes), with its stand-ins and what reached the screen."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        reload_on: tuple[int, ...],
        agent_runs: tuple[float, ...] = (600.0,),
        config: str = "",
        renderer: bool = True,
        exit_on_cron: int | None = None,
        own_tmux_session: bool = False,
        tostop: bool = False,
        pty_backed: bool = True,
        launch_log: Path | None = None,
        direnv_fails: bool = False,
    ) -> None:
        self.tmp_path = tmp_path
        self.started = tmp_path / "agent-started"
        self.received = bytearray()
        self.stderr_text = ""
        state = tmp_path / "state"
        sessions = state / "ai-cli-utils" / "sessions"
        sessions.mkdir(parents=True)
        script = sessions / f"{SESSION}.sh"
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        home = tmp_path / "home"
        (home / "zdotdir").mkdir(parents=True)
        config_dir = tmp_path / "config" / "ai-cli-utils"
        config_dir.mkdir(parents=True)
        if config:
            (config_dir / "config.toml").write_text(config, encoding="utf-8")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        self._fakes(
            bin_dir, script, state, reload_on, agent_runs, renderer, exit_on_cron, own_tmux_session, direnv_fails
        )
        shell = resolve_session_shell()
        assert shell, "the suite needs zsh or bash"
        script.write_text(
            get_engine_script(
                "c",
                "session-1",
                SESSION,
                "test-",
                "myproject",
                worktree_dir=str(worktree),
                launch_log_path=str(launch_log) if launch_log is not None else None,
            )
        )
        script.chmod(0o700)
        env = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "USER", "LOGNAME"}}
        env.update(
            PATH=os.pathsep.join((str(bin_dir), os.environ.get("PATH", ""))),
            HOME=str(home),
            ZDOTDIR=str(home / "zdotdir"),
            XDG_STATE_HOME=str(state),
            XDG_CONFIG_HOME=str(tmp_path / "config"),
            XDG_DATA_HOME=str(tmp_path / "data"),
            XDG_CACHE_HOME=str(tmp_path / "cache"),
            TERM="xterm-256color",
            PYTHONDONTWRITEBYTECODE="1",
        )
        if pty_backed:
            master, slave = os.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLUMNS, 0, 0))
            if tostop:
                attrs = termios.tcgetattr(slave)
                attrs[3] |= termios.TOSTOP
                termios.tcsetattr(slave, termios.TCSANOW, attrs)
            self.process = subprocess.Popen(
                [sys.executable, "-c", _CONTROLLING_EXEC, shell, str(script)],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=env,
                cwd=worktree,
                start_new_session=True,
            )
            os.close(slave)
            self._master = master
            self._reader = threading.Thread(target=self._drain, daemon=True)
        else:
            self._master = -1
            self.process = subprocess.Popen(
                [shell, str(script)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=worktree,
                start_new_session=True,
                text=True,
            )
            self._reader = threading.Thread(target=self._drain_pipes, daemon=True)
        self._reader.start()
        self._descendants: dict[int, psutil.Process] = {}

    @staticmethod
    def _fakes(
        bin_dir: Path,
        script: Path,
        state: Path,
        reload_on: tuple[int, ...],
        agent_runs: tuple[float, ...],
        renderer: bool,
        exit_on_cron: int | None,
        own_tmux_session: bool,
        direnv_fails: bool,
    ) -> None:
        counter = bin_dir.parent / "cron-calls"
        int_exit = state / "ai-cli-utils" / f"session-int-exit-{SESSION}"
        exit_request = (
            f'  [ "$n" = {exit_on_cron} ] && printf "exit\\n" > {int_exit}\n' if exit_on_cron is not None else ""
        )
        render = (
            f'  PYTHONPATH={REPO / "src"} exec {sys.executable} -m ai_cli.main "$@"\n' if renderer else "  exit 127\n"
        )
        fakes = {
            "ai": (
                "#!/bin/sh\n"
                'if [ "$1" = ps ] && [ "$2" = cron ]; then\n'
                f"  n=$(cat {counter} 2>/dev/null || echo 0); n=$((n + 1)); echo $n > {counter}\n"
                f'  case " {" ".join(map(str, reload_on))} " in *" $n "*)\n'
                f"    python3 -c 'import os, sys; os.utime(sys.argv[1], (int(sys.argv[2]),) * 2)' {script} $((1000000000 + n)) ;;\n"
                "  esac\n"
                f"{exit_request}"
                "  exit 0\n"
                "fi\n"
                'if [ "$1" = internal ] && { [ "$2" = emit ] || [ "$2" = progress ]; }; then\n'
                f"{render}"
                "fi\n"
                'if [ "$1" = internal ] && [ "$2" = get-version ]; then echo unknown; fi\n'
                "exit 0\n"
            ),
            "claude": (
                f"#!{sys.executable}\n"
                "import os, sys, time\n"
                f"runs = {list(agent_runs)!r}\n"
                f"started = {str(bin_dir.parent / 'agent-started')!r}\n"
                "with open(started, 'a') as log:\n"
                "    log.write(f'{os.getpid()}\\n')\n"
                "index = len(open(started).read().split()) - 1\n"
                "sys.stdout.write(f'agent-output {index + 1}\\n')\n"
                "sys.stdout.flush()\n"
                "time.sleep(runs[min(index, len(runs) - 1)])\n"
            ),
            # With an owned tmux session the supervisor's ownership fence passes silently at exit,
            # so nothing but its EXIT trap can close a row it left open.
            "tmux": (
                "#!/bin/sh\n"
                + (
                    "if [ \"$1\" = display-message ] && [ \"$2\" = -p ]; then printf '%s\\n' '$1'; fi\n"
                    if own_tmux_session
                    else ""
                )
                + "exit 0\n"
            ),
            "direnv": f"#!/bin/sh\nexit {1 if direnv_fails else 0}\n",
        }
        for name, body in fakes.items():
            path = bin_dir / name
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)
        (bin_dir / "python3").symlink_to(sys.executable)

    def _drain(self) -> None:
        while True:
            try:
                chunk = os.read(self._master, 65536)
            except OSError:
                return
            if not chunk:
                return
            self.received += chunk

    def _drain_pipes(self) -> None:
        assert self.process.stderr is not None
        self.stderr_text = self.process.stderr.read()

    def _track(self) -> None:
        with contextlib.suppress(psutil.Error):
            for child in psutil.Process(self.process.pid).children(recursive=True):
                self._descendants.setdefault(child.pid, child)

    def agents(self) -> list[str]:
        return self.started.read_text(encoding="utf-8").split() if self.started.exists() else []

    def wait_for_agents(self, count: int) -> None:
        deadline = time.monotonic() + _CEILING_SECONDS
        while time.monotonic() < deadline:
            self._track()
            if len(self.agents()) >= count:
                time.sleep(0.5)  # the agent's first line reaches the screen
                return
            assert self.process.poll() is None, f"the session ended early; screen: {self.rows()}"
            time.sleep(0.1)
        pytest.fail(f"{count} agent start(s) never happened; screen: {self.rows()}")

    def wait_for_exit(self) -> int:
        deadline = time.monotonic() + _CEILING_SECONDS
        while self.process.poll() is None and time.monotonic() < deadline:
            self._track()
            time.sleep(0.1)
        code = self.process.poll()
        assert code is not None, f"the session never ended; screen: {self.rows()}"
        self._reader.join(timeout=10)
        return code

    def stopped(self) -> list[str]:
        self._track()
        found = []
        for process in [psutil.Process(self.process.pid), *self._descendants.values()]:
            with contextlib.suppress(psutil.Error):
                if process.status() == psutil.STATUS_STOPPED:
                    found.append(" ".join(process.cmdline()))
        return found

    def stop(self) -> None:
        """End the session as tmux does when its pane is killed: SIGTERM to the supervisor."""
        with contextlib.suppress(OSError):
            os.kill(self.process.pid, signal.SIGTERM)
        self.wait_for_exit()

    def close(self) -> None:
        self._track()
        for pid in self.agents():
            with contextlib.suppress(OSError, ValueError):
                os.kill(int(pid), signal.SIGKILL)
        for process in [*self._descendants.values()]:
            with contextlib.suppress(psutil.Error):
                process.kill()
        if self.process.poll() is None:
            with contextlib.suppress(OSError):
                os.kill(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=30)
        self._reader.join(timeout=10)
        if self._master >= 0:
            with contextlib.suppress(OSError):
                os.close(self._master)

    def screen(self) -> pyte.Screen:
        screen = pyte.Screen(COLUMNS, ROWS)
        pyte.ByteStream(screen).feed(_UNMODELLED_CSI.sub(b"", bytes(self.received)))
        return screen

    def rows(self) -> list[str]:
        return [row.rstrip() for row in self.screen().display]

    def frames(self) -> list[str]:
        """Each in-place reload frame the supervisor wrote, in order, without styling."""
        text = _SGR.sub("", bytes(self.received).decode("utf-8", errors="replace"))
        return [frame.split("\r")[0] for frame in text.split("\r\x1b[K")[1:] if _RELOAD_ROW in frame]


@pytest.fixture
def session_factory(tmp_path):
    sessions: list[Session] = []

    def start(**kwargs) -> Session:
        session = Session(tmp_path, **kwargs)
        sessions.append(session)
        return session

    yield start
    for session in sessions:
        session.close()


def _reload_rows(rows: list[str]) -> list[int]:
    return [index for index, row in enumerate(rows) if _RELOAD_ROW in row]


def test_given_repeated_reloads_on_a_pty_when_the_supervisor_restarts_then_one_row_updates_in_place(
    session_factory,
):
    session = session_factory(reload_on=(1, 2, 3))
    session.wait_for_agents(1)
    rows = session.rows()

    reload_rows = _reload_rows(rows)
    assert len(reload_rows) == 1, rows
    row = rows[reload_rows[0]]
    assert "session script updated" in row, row
    assert re.search(r"reloading \(attempt 3, \d+s\)", row), row
    frames = session.frames()
    assert [re.search(r"attempt (\d+)", frame)[1] for frame in frames] == ["1", "2", "3"], frames  # type: ignore[index]
    assert "session script updated — reloading..." not in "\n".join(rows)


def test_given_reloads_past_both_thresholds_when_rendered_then_the_warning_and_persistent_error_appear(
    session_factory,
):
    session = session_factory(reload_on=(1, 2, 3), config="[output.progress.reload]\nwarn_after = 2\nerror_after = 3\n")
    session.wait_for_agents(1)
    rows = session.rows()

    levels = [("Error:" in f, "Warning:" in f) for f in session.frames()]
    assert levels == [(False, False), (False, True), (True, False)], session.frames()
    errors = [index for index, row in enumerate(rows) if "reloaded 3 times in a row" in row]
    assert len(errors) == 1, rows
    error_row = rows[errors[0]]
    assert "Error:" in error_row
    assert re.search(r"in \d+s", error_row), error_row
    assert "press Ctrl+C twice within 3 seconds" in error_row, error_row
    reload_rows = _reload_rows(rows)
    assert reload_rows == [errors[0] + 1], rows
    assert "attempt 3" in rows[reload_rows[0]]


@pytest.mark.parametrize("closer", ["agent", "record", "exit"])
def test_given_an_open_reload_row_when_the_agent_starts_then_its_output_begins_on_the_next_row(session_factory, closer):
    if closer == "agent":
        session = session_factory(reload_on=(1,))
        session.wait_for_agents(1)
        next_text = "agent-output 1"
    elif closer == "record":
        # The second child finds a recorded double Ctrl+C and exits before closing the row, so the
        # supervisor's own closing message is the next line written.
        session = session_factory(reload_on=(1,), exit_on_cron=2)
        session.wait_for_exit()
        next_text = "skipping tmux session cleanup"
    else:
        session = session_factory(reload_on=(1,), exit_on_cron=2, own_tmux_session=True)
        session.wait_for_exit()
        next_text = None
    screen = session.screen()
    rows = [row.rstrip() for row in screen.display]

    reload_rows = _reload_rows(rows)
    assert len(reload_rows) == 1, rows
    assert re.search(r"reloading \(attempt 1, \d+s\)$", rows[reload_rows[0]]), rows
    if next_text is None:
        assert (screen.cursor.y, screen.cursor.x) == (reload_rows[0] + 1, 0), rows
        assert rows[reload_rows[0] + 1] == "", rows
    else:
        assert any(row.startswith(next_text) or f" {next_text}" in row for row in rows[reload_rows[0] + 1 :]), rows
        assert next_text not in rows[reload_rows[0]], rows


def test_given_redirected_output_when_reloads_repeat_then_each_is_one_plain_counted_line(session_factory):
    session = session_factory(reload_on=(1, 2, 3), pty_backed=False)
    session.wait_for_agents(1)
    session.stop()
    text = session.stderr_text

    lines = [line for line in text.split("\n") if _RELOAD_ROW in line]
    assert [re.search(r"attempt (\d+)", line)[1] for line in lines] == ["1", "2", "3"], text  # type: ignore[index]
    assert all(_SESSION_LINE.match(line) for line in lines), lines
    assert "\r" not in text and "\x1b" not in text, repr(text)


def test_given_no_ai_entry_point_when_a_reload_happens_then_a_plain_counted_line_is_printed(session_factory):
    session = session_factory(reload_on=(1, 2), renderer=False)
    session.wait_for_agents(1)
    rows = session.rows()

    lines = [row for row in rows if _RELOAD_ROW in row]
    cron_calls = (session.tmp_path / "cron-calls").read_text(encoding="utf-8").strip()
    assert len(lines) == 2, f"children started: {cron_calls}; screen: {rows}"
    for attempt, line in enumerate(lines, start=1):
        assert _SESSION_LINE.match(line), line
        assert re.search(rf"session script updated, reloading \(attempt {attempt}, \d+s\)$", line), line


def test_given_tostop_on_the_pty_when_reloads_repeat_then_the_supervisor_is_never_stopped(session_factory):
    session = session_factory(reload_on=(1, 2, 3), tostop=True)
    session.wait_for_agents(1)

    assert session.stopped() == []
    assert "attempt 3" in session.rows()[_reload_rows(session.rows())[0]]


def test_given_a_reload_then_a_normal_agent_exit_when_reloading_again_then_the_count_restarts_at_1(
    session_factory,
):
    # Child 1 reloads, child 2 runs an agent that exits normally after 4s, child 3 reloads, child 4 runs.
    session = session_factory(reload_on=(1, 3), agent_runs=(4.0, 600.0))
    session.wait_for_agents(2)
    rows = session.rows()

    attempts = [re.search(r"attempt (\d+)", frame)[1] for frame in session.frames()]  # type: ignore[index]
    assert attempts == ["1", "1"], session.frames()
    assert len(_reload_rows(rows)) == 2, rows


def test_given_one_reload_when_the_agent_starts_then_no_escalation_is_shown(session_factory):
    session = session_factory(reload_on=(1,))
    session.wait_for_agents(1)
    rows = session.rows()
    text = "\n".join(rows)

    reload_rows = _reload_rows(rows)
    assert len(reload_rows) == 1, rows
    assert "Warning:" not in text and "Error:" not in text, rows
    assert any("agent-output 1" in row for row in rows[reload_rows[0] + 1 :]), rows


@pytest.mark.parametrize(
    ("table", "key"),
    [
        ("warn_after = 0\n", "output.progress.reload.warn_after"),
        ('error_after = "6"\n', "output.progress.reload.error_after"),
        ("warn_after = 5\nerror_after = 4\n", "output.progress.reload.error_after"),
        ("stall_after = 2\n", "output.progress.reload.stall_after"),
    ],
)
def test_given_an_invalid_reload_threshold_when_ai_internal_progress_runs_then_defaults_apply_and_the_key_is_named(
    table, key, tmp_path
):
    config = tmp_path / "config" / "ai-cli-utils" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("[output.progress.reload]\n" + table, encoding="utf-8")
    env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path / "config"), "NO_COLOR": "1", "PYTHONPATH": str(REPO / "src")}

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_cli.main",
            "internal",
            "progress",
            "session",
            "reload",
            "plain",
            "3",
            "0",
            "script-updated",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert f"{config}: {key}: " in result.stderr, result.stderr
    assert "Warning: session script updated, reloading (attempt 3, " in result.stdout, result.stdout
