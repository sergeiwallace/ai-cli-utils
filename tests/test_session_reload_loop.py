"""A live session must reach its agent, not loop on the hot-reload line (AI-CLI-z86u).

The reported symptom: relaunching with ``ai c`` printed ``[session] session script
updated — reloading...`` over and over and never started the agent. Its cause,
pinned at the helper level in ``test_session_reload_mtime.py``: on GNU coreutils
the old ``stat -f "%m" p || stat -c "%Y" p`` probe captured a filesystem report
whose free-block counters change between any two probes, so every replacement
child saw a "changed" stable script at its loop top and exited 78 again.

These tests run the whole generated session -- the stable script ``ai c`` writes
and hands to ``tmux new-session``, its supervisor, the promoted child body and the
agent -- in a real tmux pane, so they bind the artifact that is actually deployed
rather than a fragment extracted from it. Only ``claude`` and ``ai`` are
stand-ins, plus ``stat`` where the host's is not GNU coreutils: there a stand-in
with GNU's measured behaviour is installed, because the loop only exists under
GNU semantics and a BSD ``stat`` would make the test pass for the wrong reason.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from conftest import tmux_runnable
from process_ownership import watch_tmux_directory
from process_wait import process_wait_seconds

from ai_cli.session_script import get_engine_script

_TMUX_RUNNABLE, _TMUX_SKIP_REASON = tmux_runnable()

pytestmark = pytest.mark.skipif(os.name != "posix", reason="the session supervisor is a POSIX shell script")

_OBSERVE_SECONDS = process_wait_seconds()
# The agent starts only after the whole generated startup has run: ~20 s on a
# lightly loaded 10-core host, and past 60 s with eight copies of this file running
# at once (AI-CLI-h17u, measured 2026-10-14). So startup is waited on by its two real
# outcomes, the agent's own start record or the session ending, under a ceiling that
# only a hung startup reaches.
_STARTUP_CEILING_SECONDS = max(_OBSERVE_SECONDS, 300.0)
# The supervisor's reload row (AI-CLI-zss5). Its plain fallback, which these stand-ins
# produce, prints one counted line per reload.
_RELOAD_LINE = "session script updated, reloading (attempt "

# GNU coreutils `stat`, as measured on Linux 2026-09-06: `-f` is FILESYSTEM mode and
# takes no argument, so in `stat -f "%m" FILE` the `%m` is a second FILENAME. It
# prints a filesystem report for FILE on stdout, whose free counters move whenever
# anything allocates a block, complains about `%m` on stderr, and exits 1. The
# counter file stands in for that movement so every probe differs, deterministically.
_GNU_STAT = f"""#!{sys.executable}
import os, sys
args = sys.argv[1:]
if len(args) == 3 and args[0] == "-c" and args[1] == "%Y":
    try:
        print(int(os.stat(args[2]).st_mtime))
    except OSError as exc:
        sys.exit(f"stat: cannot statx '{{args[2]}}': {{exc.strerror}}")
    sys.exit(0)
if args and args[0] == "-f":
    counter = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".stat-free")
    try:
        free = int(open(counter).read()) + 1
    except (OSError, ValueError):
        free = 1000
    open(counter, "w").write(str(free))
    for name in args[1:]:
        if os.path.exists(name):
            print(f'  File: "{{name}}"')
            print("    ID: 0        Namelen: 255     Type: ext2/ext3")
            print(f"Block size: 4096       Fundamental block size: 4096")
            print(f"Blocks: Total: 100000     Free: {{free}}      Available: {{free}}")
            print(f"Inodes: Total: 100000     Free: {{free}}")
        else:
            print(f"stat: cannot read file system information for '{{name}}'", file=sys.stderr)
    sys.exit(1)
sys.exit("stat: unsupported invocation in this stand-in: " + " ".join(args))
"""


def _host_stat_is_gnu() -> bool:
    try:
        probe = subprocess.run(["stat", "--version"], capture_output=True, text=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0 and "GNU coreutils" in probe.stdout


def _session_alive(sock: str, session: str) -> bool:
    return (
        subprocess.run(["tmux", "-S", sock, "has-session", "-t", session], capture_output=True, check=False).returncode
        == 0
    )


def _wait_for_agent_start(sock: str, session: str, started: Path) -> bool:
    """True once the agent records its start; False as soon as the session is gone."""
    deadline = time.monotonic() + _STARTUP_CEILING_SECONDS
    while time.monotonic() < deadline:
        if _lines(started):
            return True
        if not _session_alive(sock, session):
            return bool(_lines(started))
        time.sleep(0.2)
    return False


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").split() if path.exists() else []


@pytest.fixture
def tmux_socket():
    if not _TMUX_RUNNABLE:
        pytest.skip(_TMUX_SKIP_REASON)
    sock_dir = tempfile.mkdtemp(prefix="ai-cli-reload-")
    sock = f"{sock_dir}/tmux.sock"
    with watch_tmux_directory(Path(sock_dir)):
        yield sock


def _start_session(
    sock: str,
    tmp_path: Path,
    session: str,
    *,
    rewrite_once_during_startup: bool,
    extra_fakes: dict[str, str] | None = None,
) -> Path:
    """Run the generated stable script as a tmux pane, exactly as ``ai c`` does.

    ``rewrite_once_during_startup`` makes the first ``ai ps cron`` -- which the child
    runs after taking its baseline mtime and before its loop-top check -- give the
    stable script a new mtime, the way a concurrent ``ai c`` or ``ai update`` does.
    ``extra_fakes`` adds stand-in executables, by name, ahead of the real ones on PATH.
    Returns the file the stand-in agent appends its pid to when it starts.
    """
    started = tmp_path / "started"
    fake_bin = tmp_path / "fakes"
    fake_bin.mkdir()
    sessions = tmp_path / "state" / "ai-cli-utils" / "sessions"
    sessions.mkdir(parents=True)
    script = sessions / f"{session}.sh"
    rewrite = (
        f'if [ "$1" = ps ] && [ "$2" = cron ] && [ ! -e {tmp_path}/rewritten ]; then\n'
        f"  : > {tmp_path}/rewritten; touch -t 200109090146.40 {script}\n"
        "fi\n"
        if rewrite_once_during_startup
        else ""
    )
    (fake_bin / "ai").write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = sessions ] && [ "$2" = _sequence ]; then exec {shlex.quote(sys.executable)} -m ai_cli.main "$@"; fi\n'
        f"{rewrite}"
        'if [ "$1" = internal ] && [ "$2" = get-version ]; then printf "%s\\n" unknown; fi\n'
        "exit 0\n"
    )
    (fake_bin / "claude").write_text(
        f"#!{sys.executable}\nimport os, time\n"
        f"open({str(started)!r}, 'a').write(f'{{os.getpid()}}\\n')\n"
        "time.sleep(300)\n"
    )
    if not _host_stat_is_gnu():
        (fake_bin / "stat").write_text(_GNU_STAT)
    (fake_bin / "python3").symlink_to(sys.executable)
    for name, body in (extra_fakes or {}).items():
        (fake_bin / name).write_text(body)
    for fake in fake_bin.iterdir():
        if not fake.is_symlink():
            fake.chmod(0o755)
    zdotdir = tmp_path / "zdotdir"
    zdotdir.mkdir()
    script.write_text(get_engine_script("c", "reload", session, "c-reload-", "reload", worktree_dir=str(tmp_path)))
    script.chmod(0o700)
    inherited = {key: value for key, value in os.environ.items() if not key.startswith("DIRENV_")}
    created = subprocess.run(
        ["tmux", "-S", sock, "new-session", "-d", "-s", session, "-x", "120", "-y", "30", "bash", str(script)],
        capture_output=True,
        text=True,
        env={
            **inherited,
            "PATH": os.pathsep.join((str(fake_bin), os.environ.get("PATH", ""))),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "ZDOTDIR": str(zdotdir),
        },
        check=False,
    )
    assert created.returncode == 0, created.stderr
    return started


def _pane_history(sock: str, session: str) -> str:
    return subprocess.run(
        ["tmux", "-S", sock, "capture-pane", "-p", "-S", "-", "-t", session],
        capture_output=True,
        text=True,
        check=False,
    ).stdout


def _release(pids: list[str]) -> None:
    for pid in pids:
        with contextlib.suppress(OSError):
            os.kill(int(pid), signal.SIGKILL)


@pytest.mark.real_tmux
def test_given_gnu_stat_and_an_unchanged_stable_script_when_the_session_starts_then_the_agent_starts_without_reloading(
    tmux_socket, tmp_path
):
    session = "reload-steady"
    started = _start_session(tmux_socket, tmp_path, session, rewrite_once_during_startup=False)
    try:
        assert _wait_for_agent_start(tmux_socket, session, started), (
            f"the agent never started; pane: {_pane_history(tmux_socket, session)!r}"
        )
        assert _RELOAD_LINE not in _pane_history(tmux_socket, session), (
            "nothing rewrote the stable script, so any reload is a false positive from the mtime probe"
        )
    finally:
        _release(_lines(started))


@pytest.mark.real_tmux
def test_given_gnu_stat_and_one_stable_script_rewrite_when_the_session_starts_then_it_reloads_once_and_reaches_the_agent(
    tmux_socket, tmp_path
):
    """The positive control: the fix must not cure the loop by disabling hot reload."""
    session = "reload-once"
    started = _start_session(tmux_socket, tmp_path, session, rewrite_once_during_startup=True)
    try:
        assert _wait_for_agent_start(tmux_socket, session, started), (
            f"the agent never started; pane: {_pane_history(tmux_socket, session)!r}"
        )
        assert (tmp_path / "rewritten").exists(), "the stand-in never rewrote the script, so this proves nothing"
        assert _pane_history(tmux_socket, session).count(_RELOAD_LINE) == 1
        assert len(_lines(started)) == 1
    finally:
        _release(_lines(started))


@pytest.mark.real_tmux
def test_given_no_attached_client_and_a_slow_tmux_when_the_session_starts_then_the_attach_wait_is_bounded_by_time(
    tmux_socket, tmp_path
):
    """The first-run wait for a tmux client is about 1 s of wall time, not 20 polls (AI-CLI-b22m).

    Each poll forks ``tmux list-clients``. On a loaded host a fork costs far more than the
    0.05 s sleep between polls, so a wait bounded by its poll count measured 7 s on every
    launch with no attached client. A stand-in ``tmux`` that answers ``list-clients`` slowly
    makes the difference countable instead of timed: 20 polls under a count bound, a few
    under a time bound.
    """
    real_tmux = shutil.which("tmux")
    assert real_tmux, "tmux_socket already proved tmux runnable"
    polls = tmp_path / "list-clients-polls"
    slow_tmux = (
        f'#!/bin/sh\nif [ "$1" = list-clients ]; then echo poll >> {polls}; sleep 0.5; fi\nexec {real_tmux} "$@"\n'
    )
    session = "attach-wait"
    started = _start_session(
        tmux_socket, tmp_path, session, rewrite_once_during_startup=False, extra_fakes={"tmux": slow_tmux}
    )
    try:
        assert _wait_for_agent_start(tmux_socket, session, started), (
            f"the agent never started; pane: {_pane_history(tmux_socket, session)!r}"
        )
        count = len(_lines(polls))
        assert 0 < count <= 8, f"the attach wait polled {count} times; a 1 s wall bound allows a handful"
    finally:
        _release(_lines(started))
