"""Safety regressions for the isolated stale-session evaluator.

The tmux boundary is controlled, but the ledger, generation lease, and process
identity values are real filesystem/process primitives.  Every negative control
asserts that no ID-targeted kill is issued.
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
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import patch

import portalocker
import psutil
import pytest

from ai_cli.process_probe import ProcessIdentity, ProcessProbe, ProcfsProbe, PsutilProbe
from ai_cli.session_script import get_engine_script
from ai_cli.stale_session_reaper import (
    Pane,
    SessionCandidate,
    StaleSessionReaper,
    SubprocessTmuxAdapter,
    generation_lease_path,
    heartbeat_path,
    run_stale_session_reaper,
    write_heartbeat,
)
from ai_cli.tmux_ownership import capture_tmux_session_identity, kill_owned_tmux_session


class _ControlledTmux:
    def __init__(self, candidates: list[SessionCandidate]) -> None:
        self.candidates = candidates
        self.kills: list[str] = []

    def sessions(self) -> list[SessionCandidate]:
        return [] if self.kills else list(self.candidates)

    def capture_fingerprint(self, candidate: SessionCandidate) -> str | None:
        return "$1|generation-token|0|@1[%1=9001=1;]"

    def fence_and_kill(self, session_id: str, fingerprint: str) -> bool:
        self.kills.append(session_id)
        return True


class _Probe(ProcessProbe):
    ended_states = frozenset({"Z"})
    abandoned_states = ended_states

    def __init__(self, observations: dict[int, tuple[bool, str | None, ProcessIdentity | None]]) -> None:
        self.observations = observations

    def is_present(self, pid: int) -> bool:
        return self.observations[pid][0]

    def state(self, pid: int) -> str | None:
        return self.observations[pid][1]

    def capture_identity(self, pid: int) -> ProcessIdentity | None:
        return self.observations[pid][2]

    def start_time_match(self, pid: int, recorded: object):  # type: ignore[no-untyped-def]
        raise AssertionError("not used by the reaper")

    def end_process(self, pid: int, timeout: float = 5.0) -> bool:
        raise AssertionError("the reaper has no process-kill authority")

    def manual_end_hint(self, pid: int) -> str:
        return ""


@pytest.fixture
def reaper_state(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture
def candidate() -> SessionCandidate:
    return SessionCandidate("$1", "custom-hyphenated-session", "generation-token", (Pane("%1", 9001),))


def _reaper(
    state_home: Path, tmux: _ControlledTmux, probe: ProcessProbe, *, mode: str = "reap", now: float = 1000.0
) -> StaleSessionReaper:
    return StaleSessionReaper(
        {"stale_session_reaper": {"mode": mode, "stale_after_seconds": 60}},
        state_home=state_home,
        tmux=tmux,
        process_probe=probe,
        boot_generation=lambda: "boot-1",
        monotonic_clock=lambda: now,
    )


def _tmux_run(socket: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["tmux", "-S", socket, *args], capture_output=True, text=True, check=False)


def _tmux_new_session(
    socket: str,
    session_id: str,
    command: list[str],
    environment: dict[str, str],
    pane_environment: tuple[str, ...],
) -> subprocess.CompletedProcess[str]:
    argv = ["tmux", "-S", socket, "new-session", "-d", "-s", session_id]
    for name in pane_environment:
        argv += ["-e", f"{name}={environment[name]}"]
    return subprocess.run([*argv, *command], env=environment, capture_output=True, text=True, check=False)


# tmux sockets are AF_UNIX paths under a ~104-byte limit, and macOS's $TMPDIR is a
# long /var/folders/<hash>/T/ path, so a short parent is what keeps the socket inside
# it -- which is why "/tmp" was hardcoded here. It is not a portable location: it does
# not exist on Windows, where `mkdtemp(dir="/tmp")` raised `FileNotFoundError:
# [WinError 3] ... '/tmp\\ai-cli-tmux-...'`. Falling back to tempfile's own default
# (dir=None) costs nothing there, because no real tmux exists on that platform; the
# path is reached only by tests/test_skip_hygiene.py, which fakes tmux's presence to
# drive this generator's cleanup contract.
_SOCKET_PARENT = "/tmp" if Path("/tmp").is_dir() else None

# The real-tmux tests need POSIX process semantics, not merely a tmux binary: AF_UNIX
# sockets, `remain-on-exit` panes, process groups, signals, and supervisors launched as
# shell scripts. A module constant rather than an inline check so
# tests/test_skip_hygiene.py can declare it satisfied while driving this generator's
# cleanup contract, which is how that file already handles `shutil.which`.
_POSIX_HOST = os.name == "posix"

# How long to let a real process take. These tests spawn real shells, supervisors, tmux
# servers and signal relays, and the suite runs under `-n auto`, so several of them
# compete for CPU with ~2,900 other tests. A timeout here exists to bound a genuine
# hang, NOT to assert promptness -- and the short ones were doing the latter by
# accident. Measured on this tree: the same two files give 1 failure serially and 9-34
# under `-n auto`, and every one of those is a `subprocess.TimeoutExpired` after 5
# seconds, or a poll deadline of 10, on a process that was merely descheduled.
#
# A generous bound loses nothing: every wait returns the moment its process exits or
# its predicate holds, so the fast path is unchanged and a real hang is still caught --
# it simply never exits. The worst case is a genuinely broken build taking a minute
# longer to say so. Overridable so a slow runner can raise it without a code change.
_PROCESS_WAIT_SECONDS = float(os.environ.get("AI_CLI_TEST_PROCESS_WAIT_SECONDS", "60"))


def isolated_tmux_socket() -> Iterator[str]:
    """An isolated tmux server, torn down on EVERY exit path including a skip.

    A plain generator rather than a bare fixture so that its cleanup contract can
    be driven directly by a test (tests/test_skip_hygiene.py). That contract used
    to be broken in a way no test could see: the ``try``/``finally`` began BELOW
    the "isolated tmux server unavailable" skip, so on the skip path the temp
    directory was never removed and the probe server was never killed — and the
    skip path is by definition the one taken on hosts where tmux misbehaves
    (AI-CLI-bug-tests-skip-capability-probe-bfqy).

    The ``which`` check stays above the ``mkdtemp`` on purpose: a PATH lookup
    builds nothing, so there is nothing to clean up if it decides to skip.
    """
    if not _POSIX_HOST:
        pytest.skip("the real-tmux tests need POSIX process semantics, not just a tmux binary")
    if shutil.which("tmux") is None:
        pytest.skip("tmux binary not available on PATH")
    socket_dir = Path(tempfile.mkdtemp(prefix="ai-cli-tmux-", dir=_SOCKET_PARENT))
    socket = str(socket_dir / "socket")
    try:
        probe = _tmux_run(socket, "new-session", "-d", "-s", "probe", "sleep", "30")
        if probe.returncode != 0 or _tmux_run(socket, "has-session", "-t", "probe").returncode != 0:
            pytest.skip(f"isolated tmux server unavailable: {(probe.stderr or probe.stdout).strip()}")
        yield socket
    finally:
        _tmux_run(socket, "kill-server")
        shutil.rmtree(socket_dir, ignore_errors=True)


@pytest.fixture
def real_tmux_socket() -> Iterator[str]:
    yield from isolated_tmux_socket()


def _wait_for_dead_pane(socket: str, session_id: str) -> None:
    deadline = time.monotonic() + _PROCESS_WAIT_SECONDS
    while time.monotonic() < deadline:
        result = _tmux_run(socket, "list-panes", "-t", session_id, "-F", "#{pane_dead}")
        if result.returncode == 0 and result.stdout.strip() == "1":
            return
        time.sleep(0.05)
    pytest.fail("tmux pane did not become dead")


def _create_dead_managed_session(socket: str, session_id: str, generation: str = "generation-token") -> None:
    created = _tmux_run(socket, "new-session", "-d", "-s", session_id, "sh", "-c", "read ignored; exit 0")
    assert created.returncode == 0, created.stderr
    assert _tmux_run(socket, "set-window-option", "-t", session_id, "remain-on-exit", "on").returncode == 0
    assert _tmux_run(socket, "set-option", "-t", session_id, "@ai_cli_session_generation", generation).returncode == 0
    assert _tmux_run(socket, "send-keys", "-t", session_id, "done", "Enter").returncode == 0
    _wait_for_dead_pane(socket, session_id)


def _wait_for_missing_session(socket: str, session_id: str) -> None:
    deadline = time.monotonic() + _PROCESS_WAIT_SECONDS
    while time.monotonic() < deadline:
        result = _tmux_run(socket, "has-session", "-t", session_id)
        if result.returncode != 0:
            return
        time.sleep(0.05)
    pytest.fail("tmux session did not terminate")


def _wait_for_lines(path: Path, count: int) -> list[str]:
    deadline = time.monotonic() + _PROCESS_WAIT_SECONDS
    while time.monotonic() < deadline:
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        if len(lines) >= count:
            return lines
        time.sleep(0.05)
    raise AssertionError(f"{path.name} did not contain {count} lines")


def _wait_for_condition(description: str, predicate: Callable[[], bool], timeout: float | None = None) -> None:
    deadline = time.monotonic() + (_PROCESS_WAIT_SECONDS if timeout is None else timeout)
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    pytest.fail(f"timed out waiting for {description}")


def _start_real_tmux_supervisor(
    socket: str,
    tmp_path: Path,
    shell: str,
    *,
    is_remote: bool,
    child_body: str | None = None,
    agent_body: str | None = None,
    ownership_bootstrap_barrier: tuple[Path, Path] | None = None,
) -> tuple[str, Path, Path]:
    """Run a generated supervisor through real tmux, locks, and heartbeat writes."""
    session_id = "test-session"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_isolated_tmux_wrapper(bin_dir / "tmux")
    _write_executable(
        bin_dir / "ai",
        '#!/bin/sh\nexec "$AI_CLI_TEST_PYTHON" -m ai_cli.main "$@"\n',
    )
    ready = tmp_path / "child-ready"
    launches = tmp_path / "launches"
    _write_executable(
        bin_dir / "claude",
        agent_body
        or (
            "#!/bin/sh\n"
            'printf "%s\\n" "$$" >> "$AI_CLI_TEST_LAUNCHES"\n'
            'printf "%s\\n" "$$" > "$AI_CLI_TEST_CHILD_READY"\n'
            "sleep 4\n"
            "exit 0\n"
        ),
    )
    recovery_shell = bin_dir / "recovery-shell"
    _write_executable(
        recovery_shell,
        '#!/bin/sh\nprintf "recovery\\n" > "$AI_CLI_TEST_RECOVERY_READY"\nIFS= read -r _\n',
    )
    state_root = tmp_path / "state"
    state_home = state_root / "ai-cli-utils"
    if child_body is not None:
        sessions_dir = state_home / "sessions"
        sessions_dir.mkdir(parents=True)
        _write_executable(sessions_dir / f"{session_id}.sh", child_body)
    supervisor = tmp_path / "supervisor.sh"
    script = get_engine_script(
        "c",
        "session-1",
        session_id,
        "test-",
        "myproject",
        is_remote=is_remote,
        worktree_dir=str(tmp_path),
    ).replace("sleep 30 || exit 0", "sleep 0.05 || exit 0")
    if ownership_bootstrap_barrier is not None:
        # Deliberately NOT rebinding `ready`: that name is exported as
        # AI_CLI_TEST_CHILD_READY below, so reusing it here would silently make the
        # barrier signal and the child-ready signal the same file, and a caller whose
        # child body watches the environment variable would see the barrier's touch as
        # its own readiness. Harmless for the callers that exist today, which is exactly
        # why it would go unnoticed.
        barrier_ready, release = ownership_bootstrap_barrier
        bootstrap = 'if [[ -n "$generation_token" ]]; then\n'
        paused_bootstrap = (
            f"touch {shlex.quote(str(barrier_ready))}\n"
            f"while [[ ! -f {shlex.quote(str(release))} ]]; do sleep 0.05; done\n" + bootstrap
        )
        assert bootstrap in script
        script = script.replace(bootstrap, paused_bootstrap, 1)
    supervisor.write_text(script, encoding="utf-8")
    environment = {
        **os.environ,
        "AI_CLI_TEST_CHILD_READY": str(ready),
        "AI_CLI_TEST_LAUNCHES": str(launches),
        "AI_CLI_TEST_PYTHON": sys.executable,
        "AI_CLI_TEST_RECOVERY_READY": str(tmp_path / "recovery-ready"),
        "AI_CLI_TEST_TMUX_SOCKET": socket,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "SHELL": str(recovery_shell),
        "XDG_STATE_HOME": str(state_root),
        # See _zsh_rc_free_home: without this, ~/.zshenv re-prepends ~/.local/bin and
        # the real `ai` shadows the stub. Must also be listed in the pane environment
        # below -- tmux passes only the names given with -e, so setting it here alone
        # would leave the pane inheriting the developer's own ZDOTDIR.
        "ZDOTDIR": str(_zsh_rc_free_home(tmp_path)),
    }
    created = _tmux_new_session(
        socket,
        session_id,
        [shell, str(supervisor)],
        environment,
        (
            "AI_CLI_TEST_CHILD_READY",
            "AI_CLI_TEST_LAUNCHES",
            "AI_CLI_TEST_PYTHON",
            "AI_CLI_TEST_RECOVERY_READY",
            "AI_CLI_TEST_TMUX_SOCKET",
            "PATH",
            "SHELL",
            "XDG_STATE_HOME",
            "ZDOTDIR",
        ),
    )
    assert created.returncode == 0, created.stderr
    return session_id, state_home, launches


def _tmux_generation(socket: str, session_id: str) -> str:
    marker = _tmux_run(socket, "show-options", "-t", session_id, "-v", "@ai_cli_session_generation")
    assert marker.returncode == 0, marker.stderr
    assert marker.stdout.strip()
    return marker.stdout.strip()


def _assert_generation_lease_is_held(state_home: Path, session_id: str, generation: str) -> None:
    lease = generation_lease_path(state_home, session_id, generation)
    with pytest.raises(portalocker.exceptions.LockException):
        with portalocker.Lock(str(lease), mode="a+", timeout=0, flags=portalocker.LOCK_EX | portalocker.LOCK_NB):
            pass


@pytest.mark.real_tmux
@pytest.mark.parametrize("is_remote", [False, True], ids=("local", "remote"))
def test_given_real_normal_child_restart_when_supervisor_recovers_then_pid_lease_and_record_stay_continuous(
    real_tmux_socket: str, tmp_path: Path, real_supervisor_shell: str, is_remote: bool
):
    """Normal local and remote exits must restart only the child body."""
    child_body = f"""#!{real_supervisor_shell}
if [[ "${{1:-}}" == "--ai-cli-child-body" ]]; then
  count_file="$AI_CLI_TEST_LAUNCHES"
  count=$(cat "$count_file" 2>/dev/null || echo 0)
  count=$((count + 1))
  printf '%s\\n' "$count" > "$count_file"
  printf '%s\\n' "$$" > "$AI_CLI_TEST_CHILD_READY"
  if (( count == 1 )); then sleep 4; exit 0; fi
  while true; do sleep 0.05; done
fi
"""
    session_id, state_home, launches = _start_real_tmux_supervisor(
        real_tmux_socket, tmp_path, real_supervisor_shell, is_remote=is_remote, child_body=child_body
    )
    _wait_for_condition("the first child", lambda: launches.exists())
    pane = _tmux_run(real_tmux_socket, "display-message", "-p", "-t", session_id, "#{pane_pid}")
    assert pane.returncode == 0, pane.stderr
    supervisor_pid = pane.stdout.strip()
    generation = _tmux_generation(real_tmux_socket, session_id)
    record = heartbeat_path(state_home, session_id, generation)
    _wait_for_condition("the first heartbeat record", record.exists)
    _assert_generation_lease_is_held(state_home, session_id, generation)
    _wait_for_condition("the replacement child", lambda: launches.read_text(encoding="utf-8").strip() == "2")

    assert (
        _tmux_run(real_tmux_socket, "display-message", "-p", "-t", session_id, "#{pane_pid}").stdout.strip()
        == supervisor_pid
    )
    assert record.exists(), "normal child replacement must retain the exact-generation record"
    _assert_generation_lease_is_held(state_home, session_id, generation)

    assert _tmux_run(real_tmux_socket, "kill-session", "-t", session_id).returncode == 0
    _wait_for_condition("the final heartbeat revocation", lambda: not record.exists())


@pytest.mark.real_tmux
def test_given_real_remote_fast_exit_when_recovery_shell_runs_then_lease_record_and_heartbeat_continue(
    real_tmux_socket: str, tmp_path: Path, real_supervisor_shell: str
):
    """Remote recovery retains the supervisor state until its distinct completion."""
    finish_recovery = tmp_path / "finish-recovery"
    child_body = f"""#!{real_supervisor_shell}
if [[ "${{1:-}}" == "--ai-cli-child-body" ]]; then
  printf '%s\\n' "$$" > "$AI_CLI_TEST_CHILD_READY"
  printf 'recovery\\n' > "$AI_CLI_TEST_RECOVERY_READY"
  while [[ ! -f {str(finish_recovery)!r} ]]; do sleep 0.05; done
  exit 79
fi
"""
    session_id, state_home, _ = _start_real_tmux_supervisor(
        real_tmux_socket, tmp_path, real_supervisor_shell, is_remote=True, child_body=child_body
    )
    recovery = tmp_path / "recovery-ready"
    _wait_for_condition("the remote recovery child", recovery.exists)
    pane = _tmux_run(real_tmux_socket, "display-message", "-p", "-t", session_id, "#{pane_pid}")
    assert pane.returncode == 0, pane.stderr
    supervisor_pid = pane.stdout.strip()
    generation = _tmux_generation(real_tmux_socket, session_id)
    record = heartbeat_path(state_home, session_id, generation)
    _wait_for_condition("the remote recovery heartbeat", record.exists)
    first_mtime = record.stat().st_mtime_ns
    _assert_generation_lease_is_held(state_home, session_id, generation)
    _wait_for_condition(
        "a further recovery heartbeat", lambda: record.exists() and record.stat().st_mtime_ns > first_mtime
    )

    assert (
        _tmux_run(real_tmux_socket, "display-message", "-p", "-t", session_id, "#{pane_pid}").stdout.strip()
        == supervisor_pid
    )
    assert record.exists(), "the recovery shell must not revoke the exact-generation record"
    finish_recovery.touch()
    _wait_for_missing_session(real_tmux_socket, session_id)
    _wait_for_condition("remote recovery heartbeat revocation", lambda: not record.exists())


@pytest.mark.real_tmux
def test_given_real_supervisor_crash_when_lease_and_ticker_exist_then_lease_releases_and_heartbeats_stop(
    real_tmux_socket: str, tmp_path: Path, real_supervisor_shell: str
):
    """A crashed pane leader cannot leave a holder or ticker behind."""
    child_body = f"""#!{real_supervisor_shell}
if [[ "${{1:-}}" == "--ai-cli-child-body" ]]; then
  printf '%s\\n' "$$" > "$AI_CLI_TEST_CHILD_READY"
  while true; do sleep 0.05; done
fi
"""
    session_id, state_home, _ = _start_real_tmux_supervisor(
        real_tmux_socket, tmp_path, real_supervisor_shell, is_remote=False, child_body=child_body
    )
    _wait_for_condition("the live child", lambda: (tmp_path / "child-ready").exists())
    generation = _tmux_generation(real_tmux_socket, session_id)
    record = heartbeat_path(state_home, session_id, generation)
    _wait_for_condition("the initial heartbeat", record.exists)
    _assert_generation_lease_is_held(state_home, session_id, generation)
    pane = _tmux_run(real_tmux_socket, "display-message", "-p", "-t", session_id, "#{pane_pid}")
    assert pane.returncode == 0, pane.stderr
    os.kill(int(pane.stdout.strip()), signal.SIGKILL)
    _wait_for_missing_session(real_tmux_socket, session_id)
    final_mtime = record.stat().st_mtime_ns
    time.sleep(0.2)

    with portalocker.Lock(
        str(generation_lease_path(state_home, session_id, generation)),
        mode="a+",
        timeout=0,
        flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
    ):
        pass
    assert record.stat().st_mtime_ns == final_mtime, "a crashed supervisor must stop its detached ticker"


@pytest.mark.real_tmux
def test_given_renamed_supervisor_during_ownership_bootstrap_when_clean_exit_then_replacement_survives(
    real_tmux_socket: str, tmp_path: Path, real_supervisor_shell: str
):
    """Ownership must bind to the supervisor's pane, never a reused session name."""
    bootstrap_ready = tmp_path / "bootstrap-ready"
    release_bootstrap = tmp_path / "release-bootstrap"
    finish_child = tmp_path / "finish-child"
    child_body = f"""#!{real_supervisor_shell}
if [[ "${{1:-}}" == "--ai-cli-child-body" ]]; then
  while [[ ! -f {shlex.quote(str(finish_child))} ]]; do sleep 0.05; done
  exit 77
fi
"""
    session_id, _, _ = _start_real_tmux_supervisor(
        real_tmux_socket,
        tmp_path,
        real_supervisor_shell,
        is_remote=False,
        child_body=child_body,
        ownership_bootstrap_barrier=(bootstrap_ready, release_bootstrap),
    )
    _wait_for_condition("the ownership-bootstrap barrier", bootstrap_ready.exists)
    original_id = _tmux_run(real_tmux_socket, "display-message", "-p", "-t", session_id, "#{session_id}").stdout.strip()
    assert original_id
    assert _tmux_run(real_tmux_socket, "rename-session", "-t", original_id, "renamed-supervisor").returncode == 0
    replacement = _tmux_new_session(
        real_tmux_socket,
        session_id,
        ["sleep", "30"],
        {**os.environ},
        (),
    )
    assert replacement.returncode == 0, replacement.stderr
    replacement_id = _tmux_run(
        real_tmux_socket, "display-message", "-p", "-t", session_id, "#{session_id}"
    ).stdout.strip()
    assert replacement_id and replacement_id != original_id

    release_bootstrap.touch()
    _wait_for_condition(
        "the original supervisor generation marker",
        lambda: (
            _tmux_run(
                real_tmux_socket, "show-options", "-t", original_id, "-v", "@ai_cli_session_generation"
            ).returncode
            == 0
        ),
    )
    replacement_marker = _tmux_run(
        real_tmux_socket, "show-options", "-t", replacement_id, "-v", "@ai_cli_session_generation"
    )
    assert replacement_marker.returncode != 0, "the replacement must never receive the supervisor marker"

    finish_child.touch()
    _wait_for_missing_session(real_tmux_socket, original_id)
    assert _tmux_run(real_tmux_socket, "has-session", "-t", replacement_id).returncode == 0


@pytest.mark.real_tmux
def test_given_child_exits_before_the_supervisor_waits_then_the_session_still_terminates(
    real_tmux_socket: str, tmp_path: Path, real_supervisor_shell: str
):
    """A child that is already reaped when the wait begins must still report its real exit code.

    This is the tty-backed twin of the clean-child-exit test below, and the difference is the whole
    point. That one launches the supervisor with `</dev/null`, so `[[ -t 0 ]]` is false: the child
    never stops itself and `_supervisor_promote_child` returns immediately, leaving no gap between
    the fork and the wait. A real pane has a tty, so promotion spends tens of milliseconds spawning
    a python subprocess to tcsetpgrp and SIGCONT -- and a child that exits at once is terminated
    AND reaped by bash before the wait is ever entered.

    At that point `kill -0` fails, so a wait guarded by it never runs and the clean 77 is replaced
    by the initialised 0. Since 0 is neither 77 nor 79, the supervisor reads it as "restart the
    child" and respawns forever: the session is never torn down, which is precisely the stale
    session the reaper exists to prevent.

    The launch count is asserted as well as the teardown, so the test pins the mechanism rather
    than only the symptom -- with the defect present it runs into the hundreds.
    """
    launch_log = tmp_path / "child-launches"
    child_body = f"""#!{real_supervisor_shell}
if [[ "${{1:-}}" == "--ai-cli-child-body" ]]; then
  printf 'launched\\n' >> {shlex.quote(str(launch_log))}
  exit 77
fi
"""
    session_id, _, _ = _start_real_tmux_supervisor(
        real_tmux_socket,
        tmp_path,
        real_supervisor_shell,
        is_remote=False,
        child_body=child_body,
    )
    _wait_for_missing_session(real_tmux_socket, session_id)
    launches = launch_log.read_text().count("launched") if launch_log.exists() else 0
    assert launches == 1, f"a clean 77 must not respawn the child; it launched {launches} times"


@pytest.mark.real_tmux
def test_given_clean_child_exit_when_supervisor_finishes_then_tmux_session_is_removed(
    real_tmux_socket: str, tmp_path: Path
):
    """A deliberate child exit must detach clients instead of leaving a dead pane."""
    session_id = "clean-child-exit"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_isolated_tmux_wrapper(bin_dir / "tmux")
    _write_executable(bin_dir / "ai", "#!/bin/sh\nexit 0\n")
    state_home = tmp_path / "state"
    sessions_dir = state_home / "ai-cli-utils" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / f"{session_id}.sh").write_text(
        'if [[ "${1:-}" == "--ai-cli-child-body" ]]; then\n  exit 77\nfi\n', encoding="utf-8"
    )
    supervisor = tmp_path / "supervisor.sh"
    supervisor.write_text(
        get_engine_script("c", "test-session", session_id, "test-", "myproject", is_remote=False), encoding="utf-8"
    )
    environment = {
        **os.environ,
        "AI_CLI_TEST_TMUX_SOCKET": real_tmux_socket,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "XDG_STATE_HOME": str(state_home),
        # See _zsh_rc_free_home. This site launches the supervisor under bash, which
        # sources no rc file, so the stubs are not shadowed today. It is set anyway
        # because the generated script's own child and heartbeat ticker are launched
        # under resolve_session_shell()'s choice -- zsh where present -- regardless of
        # the supervisor's shell, which is exactly how this class of breakage reached
        # the [bash] legs elsewhere in this file.
        "ZDOTDIR": str(_zsh_rc_free_home(tmp_path)),
    }

    created = _tmux_new_session(
        real_tmux_socket,
        session_id,
        ["bash", "-c", 'exec bash "$1" </dev/null', "--", str(supervisor)],
        environment,
        ("PATH", "XDG_STATE_HOME", "ZDOTDIR", "AI_CLI_TEST_TMUX_SOCKET"),
    )

    assert created.returncode == 0, created.stderr
    assert _tmux_run(real_tmux_socket, "set-window-option", "-t", session_id, "remain-on-exit", "on").returncode == 0
    _wait_for_missing_session(real_tmux_socket, session_id)


@pytest.mark.real_tmux
def test_given_real_tmux_child_when_terminal_and_direct_signals_arrive_then_delivery_follows_topology(
    real_tmux_socket: str, tmp_path: Path, supported_session_shell: str
):
    """A terminal Ctrl+C targets the child, while direct supervisor signals do not."""
    shell_name = Path(supported_session_shell).name
    session_id = f"signal-topology-{shell_name}"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    events = tmp_path / "events.log"
    launches = tmp_path / "agent-launches.log"
    interrupt_state = tmp_path / "first-interrupt"
    state_home = tmp_path / "state"
    exit_request = state_home / "ai-cli-utils" / f"session-int-exit-{session_id}"
    _write_isolated_tmux_wrapper(bin_dir / "tmux")
    _write_executable(bin_dir / "ai", "#!/bin/sh\nexit 0\n")
    _write_executable(
        bin_dir / "claude",
        "#!/usr/bin/env python3\n"
        "import os\n"
        "import signal\n"
        "import time\n"
        "\n"
        "def record(path, value):\n"
        "    with open(path, 'a', encoding='utf-8') as stream:\n"
        "        stream.write(value + '\\n')\n"
        "\n"
        "events = os.environ['AI_CLI_TEST_EVENTS']\n"
        "interrupt_state = os.environ['AI_CLI_TEST_INT_STATE']\n"
        "exit_request = os.environ['AI_CLI_TEST_EXIT_REQUEST']\n"
        "def on_int(*_):\n"
        "    record(events, 'INT')\n"
        "    if os.path.exists(interrupt_state):\n"
        "        open(exit_request, 'w', encoding='utf-8').close()\n"
        "        raise SystemExit(77)\n"
        "    open(interrupt_state, 'w', encoding='utf-8').close()\n"
        "    raise SystemExit(130)\n"
        "def on_winch(*_):\n"
        "    record(events, 'WINCH')\n"
        "def on_term(*_):\n"
        "    record(events, 'TERM')\n"
        "    raise SystemExit(143)\n"
        "signal.signal(signal.SIGINT, on_int)\n"
        "signal.signal(signal.SIGWINCH, on_winch)\n"
        "signal.signal(signal.SIGTERM, on_term)\n"
        "record(os.environ['AI_CLI_TEST_LAUNCHES'], 'START')\n"
        "while True:\n"
        "    time.sleep(0.05)\n",
    )
    sessions_dir = state_home / "ai-cli-utils" / "sessions"
    sessions_dir.mkdir(parents=True)
    child_script = """#!/bin/sh
if [ "${1:-}" = "--ai-cli-child-body" ]; then
  exec claude
fi
"""
    with patch("ai_cli.session_script.resolve_session_shell", return_value=supported_session_shell):
        supervisor_script = get_engine_script(
            "c",
            "test-session",
            session_id,
            "test-",
            "myproject",
            is_remote=False,
            worktree_dir=str(tmp_path),
        )
    (sessions_dir / f"{session_id}.sh").write_text(child_script, encoding="utf-8")
    supervisor = tmp_path / "supervisor.sh"
    supervisor.write_text(supervisor_script, encoding="utf-8")
    environment = {
        **os.environ,
        "AI_CLI_TEST_EVENTS": str(events),
        "AI_CLI_TEST_EXIT_REQUEST": str(exit_request),
        "AI_CLI_TEST_INT_STATE": str(interrupt_state),
        "AI_CLI_TEST_LAUNCHES": str(launches),
        "AI_CLI_TEST_TMUX_SOCKET": real_tmux_socket,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "XDG_STATE_HOME": str(state_home),
        # See _zsh_rc_free_home. This test builds its own environment rather than going
        # through _start_generated_supervisor, so it needs the same scrub: without it
        # ~/.zshenv re-prepends ~/.local/bin and the REAL `claude` shadows the stub, so
        # nothing is ever appended to agent-launches.log. That is why the [zsh] leg
        # failed while [bash] passed -- bash sources no rc file when non-interactive.
        "ZDOTDIR": str(_zsh_rc_free_home(tmp_path)),
    }

    created = _tmux_new_session(
        real_tmux_socket,
        session_id,
        [supported_session_shell, str(supervisor)],
        environment,
        (
            "PATH",
            "XDG_STATE_HOME",
            "ZDOTDIR",
            "AI_CLI_TEST_EVENTS",
            "AI_CLI_TEST_EXIT_REQUEST",
            "AI_CLI_TEST_INT_STATE",
            "AI_CLI_TEST_LAUNCHES",
            "AI_CLI_TEST_TMUX_SOCKET",
        ),
    )

    assert created.returncode == 0, created.stderr
    _wait_for_lines(launches, 1)
    pane = _tmux_run(real_tmux_socket, "display-message", "-p", "-t", session_id, "#{pane_pid}")
    assert pane.returncode == 0, pane.stderr
    supervisor_pid = int(pane.stdout.strip())

    os.kill(supervisor_pid, signal.SIGINT)
    os.kill(supervisor_pid, signal.SIGINT)
    os.kill(supervisor_pid, signal.SIGWINCH)
    time.sleep(0.2)

    assert _tmux_run(real_tmux_socket, "has-session", "-t", session_id).returncode == 0
    assert _wait_for_lines(launches, 1) == ["START"]
    assert not events.exists(), "direct supervisor signals must not reach the foreground agent"

    resized = _tmux_run(real_tmux_socket, "resize-window", "-t", session_id, "-x", "100", "-y", "40")
    assert resized.returncode == 0, resized.stderr
    assert _wait_for_lines(events, 1) == ["WINCH"]

    assert _tmux_run(real_tmux_socket, "send-keys", "-t", session_id, "C-c").returncode == 0
    assert _wait_for_lines(launches, 2) == ["START", "START"]
    assert events.read_text(encoding="utf-8").splitlines() == ["WINCH", "INT"]

    assert _tmux_run(real_tmux_socket, "send-keys", "-t", session_id, "C-c").returncode == 0
    _wait_for_missing_session(real_tmux_socket, session_id)
    assert events.read_text(encoding="utf-8").splitlines() == ["WINCH", "INT", "INT"]


@pytest.mark.real_tmux
def test_given_managed_pane_with_remain_on_exit_when_process_exits_then_tmux_marks_it_dead(real_tmux_socket: str):
    _create_dead_managed_session(real_tmux_socket, "managed-pane")

    pane_dead = _tmux_run(real_tmux_socket, "list-panes", "-t", "managed-pane", "-F", "#{pane_dead}")

    assert pane_dead.returncode == 0
    assert pane_dead.stdout.strip() == "1"
    assert _tmux_run(real_tmux_socket, "has-session", "-t", "managed-pane").returncode == 0


def _fingerprint_diagnostic(socket: str, candidate: object) -> str:
    """Why `capture_fingerprint` refused, in the terms it actually judges on.

    A bare `assert fingerprint is not None` says a dead managed session could not be
    fenced and nothing about which of the four gates rejected it -- the candidate's
    shape, the tmux call, the format regex, or the pane-for-pane pid comparison. That
    left a Linux-only failure undiagnosable from CI output alone, so the message now
    carries the two sides that have to agree.
    """
    raw = _tmux_run(socket, "list-panes", "-a", "-F", "#{pane_id}=#{pane_pid}=#{pane_dead}")
    shown = _tmux_run(
        socket,
        "display-message",
        "-p",
        "-t",
        getattr(candidate, "session_id", "?"),
        "#{session_id}|#{@ai_cli_session_generation}|#{session_attached}|"
        "#{W/i:#{window_id}[#{P:#{pane_id}=#{pane_pid}=#{pane_dead};}]}",
    )
    return (
        f"candidate={candidate!r}\n"
        f"list-panes -> {raw.stdout.strip()!r} (rc={raw.returncode}, err={raw.stderr.strip()!r})\n"
        f"fingerprint -> {shown.stdout.strip()!r} (rc={shown.returncode}, err={shown.stderr.strip()!r})"
    )


@pytest.mark.real_tmux
def test_given_matching_dead_managed_session_when_fence_runs_then_it_kills_the_exact_session(real_tmux_socket: str):
    _create_dead_managed_session(real_tmux_socket, "fence-positive")
    adapter = SubprocessTmuxAdapter(("tmux", "-S", real_tmux_socket))
    candidate = adapter.sessions()[0]
    fingerprint = adapter.capture_fingerprint(candidate)

    assert fingerprint is not None, _fingerprint_diagnostic(real_tmux_socket, candidate)
    assert adapter.fence_and_kill(candidate.session_id, fingerprint)
    assert _tmux_run(real_tmux_socket, "has-session", "-t", candidate.session_id).returncode != 0


@pytest.mark.real_tmux
def test_given_foreign_tokenless_session_when_identity_is_captured_then_session_survives(real_tmux_socket: str):
    assert _tmux_run(real_tmux_socket, "new-session", "-d", "-s", "foreign-session", "sleep", "30").returncode == 0

    identity = capture_tmux_session_identity("foreign-session", tmux_command=("tmux", "-S", real_tmux_socket))

    assert identity is None
    assert _tmux_run(real_tmux_socket, "has-session", "-t", "foreign-session").returncode == 0


@pytest.mark.real_tmux
def test_given_generation_changes_before_owned_kill_when_fence_runs_then_session_survives(real_tmux_socket: str):
    assert _tmux_run(real_tmux_socket, "new-session", "-d", "-s", "managed-session", "sleep", "30").returncode == 0
    assert (
        _tmux_run(
            real_tmux_socket,
            "set-option",
            "-t",
            "managed-session",
            "@ai_cli_session_generation",
            "first-generation",
        ).returncode
        == 0
    )
    identity = capture_tmux_session_identity("managed-session", tmux_command=("tmux", "-S", real_tmux_socket))
    assert identity is not None
    assert (
        _tmux_run(
            real_tmux_socket,
            "set-option",
            "-t",
            "managed-session",
            "@ai_cli_session_generation",
            "replacement-generation",
        ).returncode
        == 0
    )

    killed = kill_owned_tmux_session(identity, tmux_command=("tmux", "-S", real_tmux_socket))

    assert killed is False
    assert _tmux_run(real_tmux_socket, "has-session", "-t", "managed-session").returncode == 0


@pytest.mark.real_tmux
def test_given_respawn_before_atomic_fence_when_fence_runs_then_live_session_survives(real_tmux_socket: str):
    _create_dead_managed_session(real_tmux_socket, "fence-mutation")
    adapter = SubprocessTmuxAdapter(("tmux", "-S", real_tmux_socket))
    candidate = adapter.sessions()[0]
    fingerprint = adapter.capture_fingerprint(candidate)
    assert fingerprint is not None, _fingerprint_diagnostic(real_tmux_socket, candidate)
    respawned = _tmux_run(real_tmux_socket, "respawn-pane", "-k", "-t", candidate.panes[0].pane_id, "sleep", "30")
    assert respawned.returncode == 0, respawned.stderr

    assert not adapter.fence_and_kill(candidate.session_id, fingerprint)
    assert _tmux_run(real_tmux_socket, "has-session", "-t", candidate.session_id).returncode == 0


@pytest.mark.real_tmux
def test_given_hostile_generation_token_when_fence_fingerprint_is_captured_then_session_is_preserved(
    real_tmux_socket: str,
):
    _create_dead_managed_session(real_tmux_socket, "fence-hostile")
    hostile = "bad,#{==:1,1}"
    assert (
        _tmux_run(
            real_tmux_socket, "set-option", "-t", "fence-hostile", "@ai_cli_session_generation", hostile
        ).returncode
        == 0
    )
    adapter = SubprocessTmuxAdapter(("tmux", "-S", real_tmux_socket))
    candidate = adapter.sessions()[0]

    assert adapter.capture_fingerprint(candidate) is None
    assert _tmux_run(real_tmux_socket, "has-session", "-t", candidate.session_id).returncode == 0


def test_given_valid_fingerprint_when_atomic_fence_runs_then_it_uses_one_argv_if_shell_command():
    fingerprint = "$1|generation-token|0|@1[%1=9001=1;]"
    adapter = SubprocessTmuxAdapter()
    expected = [
        "tmux",
        "if-shell",
        "-F",
        "-t",
        "$1",
        "#{==:#{session_id}|#{@ai_cli_session_generation}|#{session_attached}|#{W/i:#{window_id}[#{P:#{pane_id}=#{pane_pid}=#{pane_dead};}]},$1|generation-token|0|@1[%1=9001=1;]}",
        "kill-session -t '$1'",
        "display-message -p __ai_cli_fence_mismatch__",
    ]
    with patch(
        "ai_cli.stale_session_reaper.subprocess.run",
        return_value=subprocess.CompletedProcess(expected, 0, stdout="", stderr=""),
    ) as run:
        assert adapter.fence_and_kill("$1", fingerprint)

    run.assert_called_once_with(expected, capture_output=True, text=True, check=False)


def test_given_stale_generation_matched_heartbeat_and_ended_panes_when_revalidated_then_kills_exact_id(
    reaper_state: Path, candidate: SessionCandidate
):
    assert write_heartbeat(
        reaper_state,
        candidate.session_name,
        candidate.generation_token,
        boot_generation="boot-1",
        monotonic_clock=lambda: 1.0,
        wall_clock=lambda: 1.0,
    )
    tmux = _ControlledTmux([candidate])
    probe = _Probe({9001: (False, None, None)})

    assert _reaper(reaper_state, tmux, probe).evaluate_once() == ["$1"]
    assert tmux.kills == ["$1"]
    assert not heartbeat_path(reaper_state, candidate.session_name, candidate.generation_token).exists()


def test_given_live_pane_when_heartbeat_is_stale_then_preserves_session(
    reaper_state: Path, candidate: SessionCandidate
):
    write_heartbeat(
        reaper_state,
        candidate.session_name,
        candidate.generation_token,
        boot_generation="boot-1",
        monotonic_clock=lambda: 1,
    )
    tmux = _ControlledTmux([candidate])
    probe = _Probe({9001: (True, "S", ProcessIdentity("procfs", 1))})

    assert _reaper(reaper_state, tmux, probe).evaluate_once() == []
    assert tmux.kills == []


def test_given_fresh_heartbeat_when_pane_has_ended_then_preserves_session(
    reaper_state: Path, candidate: SessionCandidate
):
    write_heartbeat(
        reaper_state,
        candidate.session_name,
        candidate.generation_token,
        boot_generation="boot-1",
        monotonic_clock=lambda: 999,
    )
    tmux = _ControlledTmux([candidate])
    probe = _Probe({9001: (False, None, None)})

    assert _reaper(reaper_state, tmux, probe).evaluate_once() == []
    assert tmux.kills == []


def test_given_held_generation_lease_when_other_gates_pass_then_preserves_session(
    reaper_state: Path, candidate: SessionCandidate
):
    write_heartbeat(
        reaper_state,
        candidate.session_name,
        candidate.generation_token,
        boot_generation="boot-1",
        monotonic_clock=lambda: 1,
    )
    lease = generation_lease_path(reaper_state, candidate.session_name, candidate.generation_token)
    lease.parent.mkdir(parents=True)
    tmux = _ControlledTmux([candidate])
    probe = _Probe({9001: (False, None, None)})

    with portalocker.Lock(str(lease), mode="a+", timeout=0, flags=portalocker.LOCK_EX | portalocker.LOCK_NB):
        assert _reaper(reaper_state, tmux, probe).evaluate_once() == []
    assert tmux.kills == []


def test_given_unknown_zombie_identity_when_heartbeat_is_stale_then_preserves_session(
    reaper_state: Path, candidate: SessionCandidate
):
    write_heartbeat(
        reaper_state,
        candidate.session_name,
        candidate.generation_token,
        boot_generation="boot-1",
        monotonic_clock=lambda: 1,
    )
    tmux = _ControlledTmux([candidate])
    probe = _Probe({9001: (True, "Z", None)})

    assert _reaper(reaper_state, tmux, probe).evaluate_once() == []
    assert tmux.kills == []


def test_given_real_pid_with_changed_identity_during_revalidation_then_evaluator_preserves_session(
    reaper_state: Path, candidate: SessionCandidate
):
    """A controlled reuse mutation must reject a real live process boundary."""
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])

    class IdentityChangesDuringRevalidation(ProcessProbe):
        ended_states = frozenset({"Z"})
        abandoned_states = ended_states

        def __init__(self) -> None:
            self.calls = 0

        def is_present(self, pid: int) -> bool:
            return pid == process.pid

        def state(self, pid: int) -> str | None:
            return "Z" if pid == process.pid else None

        def capture_identity(self, pid: int) -> ProcessIdentity | None:
            assert pid == process.pid
            identity = PsutilProbe().capture_identity(pid)
            assert identity is not None
            self.calls += 1
            if self.calls == 1:
                return identity
            return ProcessIdentity(identity.backend, identity.marker + 1)

        def start_time_match(self, pid: int, recorded: object):  # type: ignore[no-untyped-def]
            raise AssertionError("not used by the reaper")

        def end_process(self, pid: int, timeout: float = 5.0) -> bool:
            raise AssertionError("the reaper has no process-kill authority")

        def manual_end_hint(self, pid: int) -> str:
            return ""

    reused_candidate = SessionCandidate(
        candidate.session_id,
        candidate.session_name,
        candidate.generation_token,
        (Pane(candidate.panes[0].pane_id, process.pid),),
    )
    try:
        assert write_heartbeat(
            reaper_state,
            reused_candidate.session_name,
            reused_candidate.generation_token,
            boot_generation="boot-1",
            monotonic_clock=lambda: 1,
        )
        tmux = _ControlledTmux([reused_candidate])

        assert _reaper(reaper_state, tmux, IdentityChangesDuringRevalidation()).evaluate_once() == []
        assert tmux.kills == []
    finally:
        process.kill()
        process.wait()


def test_given_token_or_name_mismatch_when_heartbeat_is_stale_then_preserves_session(
    reaper_state: Path, candidate: SessionCandidate
):
    write_heartbeat(
        reaper_state, candidate.session_name, "foreign-generation", boot_generation="boot-1", monotonic_clock=lambda: 1
    )
    tmux = _ControlledTmux([candidate])
    probe = _Probe({9001: (False, None, None)})

    assert _reaper(reaper_state, tmux, probe).evaluate_once() == []
    assert tmux.kills == []


def test_given_observe_mode_when_every_reap_gate_passes_then_does_not_kill(
    reaper_state: Path, candidate: SessionCandidate
):
    write_heartbeat(
        reaper_state,
        candidate.session_name,
        candidate.generation_token,
        boot_generation="boot-1",
        monotonic_clock=lambda: 1,
    )
    tmux = _ControlledTmux([candidate])
    probe = _Probe({9001: (False, None, None)})

    assert _reaper(reaper_state, tmux, probe, mode="observe").evaluate_once() == []
    assert tmux.kills == []


@pytest.mark.parametrize(
    "settings",
    [
        {"mode": "unsafe", "stale_after_seconds": 60},
        {"mode": "observe", "stale_after_seconds": 0},
    ],
)
def test_given_invalid_reaper_configuration_when_evaluated_then_logs_and_preserves_all_sessions(
    reaper_state: Path, candidate: SessionCandidate, caplog: pytest.LogCaptureFixture, settings: dict[str, object]
):
    tmux = _ControlledTmux([candidate])
    probe = _Probe({9001: (False, None, None)})
    reaper = StaleSessionReaper(
        {"stale_session_reaper": settings},
        state_home=reaper_state,
        tmux=tmux,
        process_probe=probe,
    )

    assert reaper.evaluate_once() == []
    assert tmux.kills == []
    assert "configuration_invalid" in caplog.text


def test_given_reaper_configuration_is_absent_when_every_gate_passes_then_observes_without_killing(
    reaper_state: Path, candidate: SessionCandidate, caplog: pytest.LogCaptureFixture
):
    assert write_heartbeat(
        reaper_state,
        candidate.session_name,
        candidate.generation_token,
        boot_generation="boot-1",
        monotonic_clock=lambda: 1,
    )
    tmux = _ControlledTmux([candidate])
    probe = _Probe({9001: (False, None, None)})
    reaper = StaleSessionReaper(
        {},
        state_home=reaper_state,
        tmux=tmux,
        process_probe=probe,
        boot_generation=lambda: "boot-1",
        monotonic_clock=lambda: 1000,
    )

    assert reaper.evaluate_once() == []
    assert tmux.kills == []
    assert "mode_observe" in caplog.text


def test_given_first_candidate_is_killed_when_second_candidate_raises_then_first_kill_remains_and_second_is_preserved(
    reaper_state: Path,
    caplog: pytest.LogCaptureFixture,
):
    first = SessionCandidate("$1", "session-1", "generation-1", (Pane("%1", 9001),))
    second = SessionCandidate("$2", "session-2", "generation-2", (Pane("%2", 9002),))
    for item in (first, second):
        assert write_heartbeat(
            reaper_state,
            item.session_name,
            item.generation_token,
            boot_generation="boot-1",
            monotonic_clock=lambda: 1,
        )

    class CandidateErrorReaper(StaleSessionReaper):
        def _evaluate_candidate(self, item: SessionCandidate) -> bool:
            if item.session_id == second.session_id:
                raise RuntimeError("malformed candidate")
            return super()._evaluate_candidate(item)

    tmux = _ControlledTmux([first, second])
    probe = _Probe({9001: (False, None, None), 9002: (False, None, None)})
    reaper = CandidateErrorReaper(
        {"stale_session_reaper": {"mode": "reap", "stale_after_seconds": 60}},
        state_home=reaper_state,
        tmux=tmux,
        process_probe=probe,
        boot_generation=lambda: "boot-1",
        monotonic_clock=lambda: 1000,
    )

    assert reaper.evaluate_once() == [first.session_id]
    assert tmux.kills == [first.session_id]
    assert heartbeat_path(reaper_state, second.session_name, second.generation_token).exists()
    assert "candidate_error" in caplog.text


def test_given_reaper_run_when_an_interval_completes_then_sleeps_for_sixty_seconds(tmp_path: Path):
    class Reaper(StaleSessionReaper):
        def evaluate_once(self) -> list[str]:
            return []

    def stop_after_first_interval(seconds: float) -> None:
        assert seconds == 60
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_stale_session_reaper(
            {},
            state_home=tmp_path,
            reaper_factory=lambda config, **kwargs: Reaper(config, **kwargs),
            sleep=stop_after_first_interval,
        )


def test_given_procfs_and_psutil_processes_when_identity_is_captured_then_values_are_backend_specific(tmp_path: Path):
    proc_dir = tmp_path / "proc"
    stat = proc_dir / "42" / "stat"
    stat.parent.mkdir(parents=True)
    stat.write_text("42 (worker) S " + " ".join(str(value) for value in range(4, 22)) + " 777\n")

    procfs_identity = ProcfsProbe(proc_dir).capture_identity(42)
    psutil_identity = PsutilProbe().capture_identity(os.getpid())

    assert procfs_identity == ProcessIdentity("procfs", 777)
    assert psutil_identity is not None
    assert psutil_identity.backend == "psutil"
    assert psutil_identity != procfs_identity


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="requires POSIX pass_fds and shared open-file-description lock semantics",
)
def test_given_shell_held_descriptor_when_python_lock_helper_exits_then_lease_remains_held(tmp_path: Path):
    lease_path = tmp_path / "generation.lock"
    descriptor = os.open(lease_path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; from ai_cli.main import _handle_internal; _handle_internal(['acquire-generation-lease', sys.argv[1]])",
                str(descriptor),
            ],
            pass_fds=(descriptor,),
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        with pytest.raises(portalocker.exceptions.LockException):
            with portalocker.Lock(
                str(lease_path), mode="a+", timeout=0, flags=portalocker.LOCK_EX | portalocker.LOCK_NB
            ):
                pass
    finally:
        os.close(descriptor)

    with portalocker.Lock(str(lease_path), mode="a+", timeout=0, flags=portalocker.LOCK_EX | portalocker.LOCK_NB):
        pass


@pytest.fixture(params=("bash", "zsh"))
def supported_session_shell(request: pytest.FixtureRequest) -> str:
    """Run each signal regression under every supported installed shell."""
    shell = shutil.which(request.param)
    if shell is None:
        pytest.skip(f"{request.param} is not installed")
    if os.name == "nt" or not hasattr(signal, "SIGWINCH"):
        pytest.skip("generated tmux session signals require a POSIX shell")
    return shell


@pytest.fixture
def real_supervisor_shell() -> str:
    shell = shutil.which("bash")
    if shell is None:
        pytest.skip("bash is required for real supervisor continuity coverage")
    return shell


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o700)


def _zsh_rc_free_home(tmp_path: Path) -> Path:
    """An empty directory to point ``ZDOTDIR`` at, so zsh loads no rc file.

    Scrubbing this at the process boundary is what makes the stub binaries in
    ``bin_dir`` actually win (AI-CLI-ta1l). **zsh sources ``.zshenv`` on every
    invocation, including a non-interactive script** — unlike bash, which sources
    nothing for a non-interactive shell. On a machine whose ``~/.zshenv`` re-exports
    ``PATH`` with ``~/.local/bin`` prepended, that silently moved the REAL ``ai``
    ahead of the harness stub, so the generated script called the real CLI, the stub
    never recorded anything, and the assertion failed on a file that was never
    written.

    It bit both shell legs, not just the zsh one, because the heartbeat ticker and
    the child are always launched under ``resolve_session_shell()``'s choice (zsh
    where present) regardless of which shell runs the supervisor. It also passed on
    CI while failing on a developer Mac, because a CI runner has no such
    ``~/.zshenv`` to inherit — the exact shape of a test that is green only where
    nobody has configured anything.

    ``ZDOTDIR`` is used rather than ``zsh -f``: the shell invocation belongs to the
    generated script (production code), so the fix has to live in the environment the
    harness controls, not in an argv the harness does not own.
    """
    zdotdir = tmp_path / "zdotdir"
    zdotdir.mkdir(exist_ok=True)
    return zdotdir


def _write_isolated_tmux_wrapper(path: Path) -> None:
    tmux_binary = shutil.which("tmux")
    assert tmux_binary is not None, "tmux binary not available on PATH"
    _write_executable(
        path,
        f'#!/bin/sh\nexec {shlex.quote(tmux_binary)} -S "$AI_CLI_TEST_TMUX_SOCKET" "$@"\n',
    )


@pytest.mark.real_tmux
@pytest.mark.skipif(
    os.name != "posix",
    reason="the wrapper is a #!/bin/sh script, which Windows cannot execute (WinError 193)",
)
def test_given_isolated_tmux_wrapper_when_path_starts_with_its_directory_then_it_executes_system_tmux(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_isolated_tmux_wrapper(bin_dir / "tmux")

    result = subprocess.run(
        [str(bin_dir / "tmux"), "-V"],
        env={
            **os.environ,
            "AI_CLI_TEST_TMUX_SOCKET": str(tmp_path / "socket"),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=_PROCESS_WAIT_SECONDS,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("tmux ")


def _wait_for_path(path: Path, process: subprocess.Popen[str], timeout: float | None = None) -> None:
    deadline = time.monotonic() + (_PROCESS_WAIT_SECONDS if timeout is None else timeout)
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            stdout, stderr = _communicate_supervisor(process)
            pytest.fail(f"supervisor exited before its child was ready: {stdout!r} {stderr!r}")
        time.sleep(0.02)
    pytest.fail("supervisor child did not become ready")


def _communicate_supervisor(process: subprocess.Popen[str]) -> tuple[str, str]:
    if process.stdout is None:
        process.wait(timeout=_PROCESS_WAIT_SECONDS)
        return "", ""
    return process.communicate(timeout=_PROCESS_WAIT_SECONDS)


_SPAWNED_SUPERVISORS: list[subprocess.Popen[str]] = []


@pytest.fixture(autouse=True)
def _reap_leaked_supervisors() -> Iterator[None]:
    """Guarantee every supervisor spawned via ``_start_generated_supervisor`` is
    killed at test end, even on assertion failure or timeout (AI-CLI-1xg0).

    Without this, a failure between spawn and the test's own
    ``_finish_supervisor`` call left the whole process tree running forever —
    63 such orphaned ``supervisor.sh``/heartbeat-ticker processes (some 1+ day
    old) were found accumulating from this exact file across prior runs, and
    are a concrete explanation for this file's own "supervisor child did not
    become ready" flakiness: a fresh run competes with dozens of leaked
    processes for PTY/process-table resources. Walks the real process tree via
    psutil rather than the OS process group, since the generated supervisor's
    heartbeat ticker deliberately ``os.setsid()``s itself out of the original
    group (mirroring the /remote-control bridge-leak shape found elsewhere
    this session) while remaining a psutil-visible descendant.
    """
    yield
    while _SPAWNED_SUPERVISORS:
        process = _SPAWNED_SUPERVISORS.pop()
        if process.poll() is not None:
            continue
        with contextlib.suppress(psutil.NoSuchProcess):
            root = psutil.Process(process.pid)
            for descendant in [*root.children(recursive=True), root]:
                with contextlib.suppress(psutil.NoSuchProcess):
                    descendant.kill()
        with contextlib.suppress(Exception):
            process.wait(timeout=_PROCESS_WAIT_SECONDS)


def _start_generated_supervisor(
    tmp_path: Path,
    shell: str,
    *,
    terminal_pgid: str = "123",
    supervisor_pgid: str = "123",
    lease_acquired: bool = True,
    is_remote: bool = False,
    child_body: str | None = None,
    terminal: bool = False,
    fast_heartbeat: bool = False,
    child_group_delay: float = 0,
    pseudo_terminal: bool = False,
    ready_timeout: float = 15,
    extra_commands: dict[str, str] | None = None,
    stdin_fd: int | None = None,
    fast_promotion_retry: bool = False,
    wait_for_child_ready: bool = True,
) -> tuple[subprocess.Popen[str], Path, Path]:
    """Start the generated supervisor with controlled external session commands."""
    # Decide the host-capability question BEFORE building anything. This used to
    # sit ~60 lines down, after a bin directory, three log paths and several
    # generated executables had been written, so a host without `script` paid for
    # all of it and then skipped (AI-CLI-bug-tests-skip-capability-probe-bfqy).
    # `which` builds nothing, so hoisting it is free and cannot leak.
    script_bin = shutil.which("script") if pseudo_terminal else None
    if pseudo_terminal and script_bin is None:
        pytest.skip("script binary unavailable for terminal process-group test")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    event_log = tmp_path / "events.log"
    heartbeat_log = tmp_path / "heartbeats.log"
    child_ready = tmp_path / "child-ready"
    _write_executable(
        bin_dir / "tmux",
        "#!/bin/sh\n"
        '[ -z "${AI_CLI_TEST_TMUX_EVENTS:-}" ] || printf "%s\\n" "$*" >> "$AI_CLI_TEST_TMUX_EVENTS"\n'
        'if [ "$1" = "display-message" ] && [ "$2" = "-p" ]; then\n'
        "  printf '%s\\n' '$1'\n"
        "fi\n"
        "exit 0\n",
    )
    _write_executable(
        bin_dir / "ai",
        "#!/bin/sh\n"
        'if [ "$1" = "internal" ] && [ "$2" = "acquire-generation-lease" ]; then\n'
        '  [ "${AI_CLI_TEST_LEASE_ACQUIRED:-0}" = "1" ] && exit 0\n'
        "  exit 1\n"
        "fi\n"
        'if [ "$1" = "internal" ] && [ "$2" = "publish-heartbeat" ]; then\n'
        '  printf "heartbeat\\n" >> "$AI_CLI_TEST_HEARTBEATS"\n'
        "fi\n"
        "exit 0\n",
    )
    _write_executable(
        bin_dir / "ps",
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *tpgid=*) printf " %s\\n" "$AI_CLI_TEST_TERMINAL_PGID" ;;\n'
        '  *pgid=*) printf " %s\\n" "$AI_CLI_TEST_SUPERVISOR_PGID" ;;\n'
        '  *) exec /bin/ps "$@" ;;\n'
        "esac\n",
    )
    for command, body in (extra_commands or {}).items():
        _write_executable(bin_dir / command, body)

    state_home = tmp_path / "state"
    sessions_dir = state_home / "ai-cli-utils" / "sessions"
    sessions_dir.mkdir(parents=True)
    child_script = (
        child_body
        or f"""#!{shell}
if [[ "${{1:-}}" == "--ai-cli-child-body" ]]; then
  printf '%s\\n' "$$" > "$AI_CLI_TEST_CHILD_READY"
  trap 'printf "TERM\\n" >> "$AI_CLI_TEST_EVENTS"; sleep 0.4; exit 77' TERM
  trap 'printf "INT\\n" >> "$AI_CLI_TEST_EVENTS"' INT
  trap 'printf "WINCH\\n" >> "$AI_CLI_TEST_EVENTS"' WINCH
  while true; do sleep 0.05; done
fi
"""
    )
    _write_executable(sessions_dir / "test-session.sh", child_script)
    supervisor = tmp_path / "supervisor.sh"
    script = get_engine_script("c", "session-1", "test-session", "test-", "myproject", is_remote=is_remote)
    if child_group_delay:
        script = script.replace(
            "import os, signal, sys;",
            "import os, signal, sys, time;",
        ).replace(
            "os.setpgrp()",
            f"time.sleep({child_group_delay}) or os.setpgrp()",
        )
    if fast_heartbeat:
        script = script.replace("sleep 30 || exit 0", "sleep 0.05 || exit 0")
    if fast_promotion_retry:
        # Bound only in the test harness (docs/bugs/terminal-promotion-failure-hang.md
        # "Required next reproduction") so a deterministically-failing promotion
        # gives up in ~0.2s instead of the production 30s (3000 * 0.01s).
        script = script.replace("_promotion_attempt < 3000", "_promotion_attempt < 20")
    supervisor.write_text(script, encoding="utf-8")
    environment = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "ZDOTDIR": str(_zsh_rc_free_home(tmp_path)),
        "XDG_STATE_HOME": str(state_home),
        "AI_CLI_TEST_CHILD_READY": str(child_ready),
        "AI_CLI_TEST_EVENTS": str(event_log),
        "AI_CLI_TEST_HEARTBEATS": str(heartbeat_log),
        "AI_CLI_TEST_TMUX_EVENTS": str(tmp_path / "tmux-events.log"),
        "AI_CLI_TEST_SUPERVISOR_PGID": supervisor_pgid,
        "AI_CLI_TEST_TERMINAL_PGID": terminal_pgid,
        "AI_CLI_TEST_LEASE_ACQUIRED": str(int(lease_acquired)),
    }
    command = [shell, *(["-o", "NO_BG_NICE"] if Path(shell).name == "zsh" else []), str(supervisor)]
    if pseudo_terminal:
        # Resolved and skip-checked at the top of this function, so by here it is
        # known good; re-probing would just be a second PATH lookup.
        assert script_bin is not None
        if sys.platform == "darwin":
            # BSD script: `script [-q] file [command ...]` — the trailing words
            # are the command and are not re-parsed as script's own options.
            command = [script_bin, "-q", "/dev/null", *command]
        else:
            # util-linux script has NO trailing-command form; the command must go
            # through `-c`. Measured on util-linux 2.39.3: passing it as trailing
            # words let script consume the SHELL's `-o NO_BG_NICE` as its own
            # `--output-limit`, and it died with "failed to parse output limit
            # size: 'NO_BG_NICE'" before the supervisor ever started. The test
            # then reported "supervisor exited before its child was ready",
            # blaming the code under test for an argv-parsing collision in its
            # own harness.
            command = [script_bin, "-q", "-c", shlex.join(command), "/dev/null"]
    if stdin_fd is not None:
        stdin_value: int | None = stdin_fd
    else:
        stdin_value = subprocess.PIPE if terminal else None
    process = subprocess.Popen(
        command,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=stdin_value,
        start_new_session=True,
    )
    _SPAWNED_SUPERVISORS.append(process)
    if wait_for_child_ready:
        _wait_for_path(child_ready, process, timeout=ready_timeout)
    return process, event_log, heartbeat_log


def _finish_supervisor(process: subprocess.Popen[str]) -> tuple[str, str]:
    if process.poll() is None:
        os.kill(process.pid, signal.SIGTERM)
    stdout, stderr = _communicate_supervisor(process)
    return stdout, stderr


def test_given_noncontrolling_terminal_when_promotion_fails_then_supervisor_exits_and_child_dies(
    tmp_path: Path, supported_session_shell: str
):
    """A foreground-promotion failure must not hang the supervisor forever (AI-CLI-jpnd).

    Two independent remote launches hung on a blank pane, ignored repeated
    Ctrl+C, and printed "could not promote child process group to terminal
    foreground" -- see docs/bugs/terminal-promotion-failure-hang.md. The child
    wrapper SIGSTOPs itself and waits for the supervisor to promote its
    process group before it execs into the real session. When promotion never
    succeeds, the former cleanup path sent only SIGTERM to that stopped child:
    a stopped process retains a pending SIGTERM without acting on it until it
    is continued, so ``_supervisor_wait_for_child``'s ``wait`` blocked forever
    on a child that could never die.

    Reproducing this does not need tmux or SSH: a pty that is a real terminal
    but never became this process's *controlling* terminal makes the actual
    ``tcsetpgrp`` syscall fail every time (ENOTTY), exactly like the reported
    hang, without mocking anything. Opening the slave with ``O_NOCTTY`` alone
    is not sufficient -- an interactive-capable shell that is itself a fresh
    session leader (via ``start_new_session=True``) auto-acquires any valid
    tty handed to it via job-control initialization the moment it starts,
    regardless of how an unrelated ancestor opened the underlying fd
    (confirmed against a real Linux host, Fedora, kernel 7.2.5, while writing
    this test: a bare non-shell child left the fd genuinely uncontrolled, but
    wrapping the identical fd in ``bash -c`` let it claim the terminal
    anyway). What is reliable is giving the pty's controlling-terminal slot
    to a *different* session first: a throwaway ``bash`` "owner" process is
    started as its own session leader and left running, which the kernel
    then refuses to let the supervisor's own (also fresh) session reclaim --
    ``os.tcsetpgrp`` on it fails deterministically with ENOTTY every time,
    exactly matching the production failure mode.
    """
    # Imported here, not at module scope (AI-CLI-ta1l). On Windows `import pty` pulls in
    # `tty`, which does `from termios import *`, and termios does not exist there -- so a
    # module-level import made this ENTIRE FILE fail to COLLECT on Windows with
    # `ModuleNotFoundError: No module named 'termios'`, taking all ~69 of its tests with
    # it, and tests/test_skip_hygiene.py with them (it imports this module). A
    # module-level `pytestmark` skip cannot help: collection imports the module before
    # any marker is consulted. This is the only use of pty in the file.
    import pty

    master_fd, initial_slave_fd = pty.openpty()
    slave_name = os.ttyname(initial_slave_fd)
    os.close(initial_slave_fd)

    owner_fd = os.open(slave_name, os.O_RDWR)
    owner = subprocess.Popen(
        ["bash", "-c", "echo owner-up; sleep 60"],
        stdin=owner_fd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        text=True,
    )
    os.close(owner_fd)
    try:
        assert owner.stdout is not None
        owner.stdout.readline()  # blocks until the owner has claimed the terminal

        slave_fd = os.open(slave_name, os.O_RDWR)
        try:
            process, _, _ = _start_generated_supervisor(
                tmp_path,
                supported_session_shell,
                stdin_fd=slave_fd,
                fast_promotion_retry=True,
                wait_for_child_ready=False,
            )
        finally:
            os.close(slave_fd)

        try:
            stdout, stderr = process.communicate(timeout=_PROCESS_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=_PROCESS_WAIT_SECONDS)
            pytest.fail(
                "supervisor never exited after a failed foreground promotion -- it hung "
                "waiting on a stopped child that never received SIGCONT (AI-CLI-jpnd); "
                f"stdout={stdout!r} stderr={stderr!r}"
            )

        assert "could not promote child process group to terminal foreground" in stderr, stderr
        assert process.returncode == 1, f"stdout={stdout!r} stderr={stderr!r}"
    finally:
        owner.kill()
        owner.wait(timeout=_PROCESS_WAIT_SECONDS)
        os.close(master_fd)


def test_given_generated_supervisor_when_pgrp_mismatch_then_it_publishes_no_heartbeat(
    tmp_path: Path, supported_session_shell: str
):
    process, _, heartbeats = _start_generated_supervisor(
        tmp_path, supported_session_shell, terminal_pgid="456", supervisor_pgid="123"
    )

    _, stderr = _finish_supervisor(process)

    assert not heartbeats.exists()
    assert "reaper evidence disabled" in stderr
    assert "foreground process group" in stderr


def test_given_inherited_supervisor_descriptors_when_new_supervisor_starts_then_child_becomes_ready(
    tmp_path: Path, supported_session_shell: str, monkeypatch: pytest.MonkeyPatch
):
    """A nested launch must not reuse descriptors a parent session already owns.

    AI_CLI_SUPERVISOR_LEASE_FD/TERMINAL_FD are only meaningful to the supervisor
    that exported them. A new supervisor started inside an environment that
    still carries stale values from an earlier session used those invalid
    descriptors and crashed with "Bad file descriptor" before ever writing its
    child-ready marker (AI-CLI-1xg0).
    """
    monkeypatch.setenv("AI_CLI_SUPERVISOR_LEASE_FD", "999999")
    monkeypatch.setenv("AI_CLI_SUPERVISOR_TERMINAL_FD", "999999")

    process, _, _ = _start_generated_supervisor(tmp_path, supported_session_shell, lease_acquired=False)

    _finish_supervisor(process)


def test_given_shebangless_stable_script_when_generated_supervisor_starts_child_then_child_runs(
    tmp_path: Path, supported_session_shell: str
):
    """The child must be run through the selected shell, not directly exec'd.

    Session templates are shell source files: they are executable on disk but do
    not carry a shebang.  The supervisor therefore has to pass its selected
    interpreter to the Python exec wrapper, as it already does for the ticker.
    """
    child_body = """if [ \"${1:-}\" = \"--ai-cli-child-body\" ]; then
  printf '%s\\n' \"$$\" > \"$AI_CLI_TEST_CHILD_READY\"
  while :; do sleep 0.05; done
fi
"""

    process, _, _ = _start_generated_supervisor(
        tmp_path, supported_session_shell, lease_acquired=False, child_body=child_body
    )
    _finish_supervisor(process)


def test_given_live_generated_supervisor_when_record_only_signals_arrive_then_child_receives_no_relay(
    tmp_path: Path, supported_session_shell: str
):
    process, events, _ = _start_generated_supervisor(tmp_path, supported_session_shell, lease_acquired=False)
    child_pid = int((tmp_path / "child-ready").read_text(encoding="utf-8"))

    os.kill(process.pid, signal.SIGINT)
    time.sleep(0.1)
    os.kill(child_pid, 0)
    os.kill(process.pid, signal.SIGWINCH)
    time.sleep(0.1)
    os.kill(child_pid, 0)

    assert not events.exists()
    _finish_supervisor(process)


def test_given_supervisor_after_child_restart_when_two_direct_ints_arrive_then_child_remains_undisturbed(
    tmp_path: Path, supported_session_shell: str
):
    child_body = f"""#!{supported_session_shell}
if [[ "${{1:-}}" == "--ai-cli-child-body" ]]; then
  count_file="$AI_CLI_TEST_CHILD_READY.count"
  count=$(cat "$count_file" 2>/dev/null || echo 0)
  count=$((count + 1))
  printf '%s\\n' "$count" > "$count_file"
  if (( count == 1 )); then
    printf '%s\\n' "$$" > "$AI_CLI_TEST_CHILD_READY"
    sleep 0.2
    exit 0
  fi
  printf '%s\\n' "$$" > "$AI_CLI_TEST_CHILD_READY"
  trap 'printf "TERM\\n" >> "$AI_CLI_TEST_EVENTS"; exit 0' TERM
  while true; do sleep 0.05; done
fi
"""
    process, events, _ = _start_generated_supervisor(
        tmp_path, supported_session_shell, lease_acquired=False, child_body=child_body
    )

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if (tmp_path / "child-ready.count").read_text(encoding="utf-8").strip() == "2":
            break
        time.sleep(0.02)
    try:
        assert (tmp_path / "child-ready.count").read_text(encoding="utf-8").strip() == "2"
        os.kill(process.pid, signal.SIGINT)
        time.sleep(0.1)
        os.kill(process.pid, signal.SIGINT)
        time.sleep(0.1)

        assert process.poll() is None, "a direct supervisor SIGINT must not terminate the session"
        os.kill(int((tmp_path / "child-ready").read_text(encoding="utf-8")), 0)
        assert not events.exists(), "a direct supervisor SIGINT must not be relayed to the child"
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            _communicate_supervisor(process)


def test_given_clean_child_exit_when_supervisor_finishes_then_it_kills_its_tmux_session(
    tmp_path: Path, supported_session_shell: str
):
    child_body = """if [[ \"${1:-}\" == \"--ai-cli-child-body\" ]]; then
  printf '%s\\n' \"$$\" > \"$AI_CLI_TEST_CHILD_READY\"
  exit 77
fi
"""
    process, _, _ = _start_generated_supervisor(
        tmp_path, supported_session_shell, lease_acquired=False, child_body=child_body
    )

    stdout, stderr = _communicate_supervisor(process)

    assert process.returncode == 0, f"supervisor did not exit cleanly: {stdout!r} {stderr!r}"
    cleanup_event = next(
        event
        for event in (tmp_path / "tmux-events.log").read_text(encoding="utf-8").splitlines()
        if event.startswith("if-shell -F -t $1 ")
    )
    assert "#{==:#{session_id}|#{@ai_cli_session_generation},$1|" in cleanup_event
    assert cleanup_event.endswith(" kill-session -t '$1' display-message -p __ai_cli_ownership_mismatch__")


def test_given_child_receives_ctrl_c_during_preflight_when_single_press_then_wrapper_survives_and_double_press_exits_cleanly(
    tmp_path: Path, supported_session_shell: str
):
    """Regression for AI-CLI-s5cs.

    Before this fix the child body trapped only TERM. A lone terminal Ctrl+C
    (delivered to the child's whole foreground process group, same as any
    preflight subprocess it runs) killed this wrapper outright via bash's
    default SIGINT disposition. The supervisor then treated that abrupt death
    like any other crash and immediately spawned a fresh child -- re-running
    preflight (direnv init, `ai internal ...`) from scratch. If the user kept
    pressing Ctrl+C, each fresh child died the same way before it could ever
    register a deliberate exit, producing an unbreakable crash-restart loop.

    Exercises the REAL ``get_engine_script`` output (not a hand-rolled
    stand-in), so this fails without the production fix: a single Ctrl+C
    must not kill the wrapper -- only whatever synchronous preflight command
    happens to be running dies, exactly as ``ai internal
    resolve-continue-target`` does today. A second Ctrl+C within the window
    must cleanly abort the whole session (exit 77, supervisor exits 0)
    instead of triggering another restart.
    """
    real_script = get_engine_script("c", "session-1", "test-session", "test-", "myproject", is_remote=False)
    marker = "trap '_child_record_int' INT"
    assert real_script.count(marker) == 1, "expected exactly one child-level INT trap installation"
    # A readiness marker written the instant the new trap is installed --
    # before any preflight `ai internal ...` call -- so the test can send
    # Ctrl+C into that exact preflight window without racing a fixed sleep.
    patched_script = real_script.replace(
        marker,
        marker + '\n    printf \'%s\\n\' "$$" > "$AI_CLI_TEST_CHILD_READY"',
        1,
    )
    process, _, _ = _start_generated_supervisor(
        tmp_path, supported_session_shell, lease_acquired=False, child_body=patched_script, terminal=True
    )
    child_pid = int((tmp_path / "child-ready").read_text(encoding="utf-8"))
    assert os.getpgid(child_pid) == process.pid

    os.killpg(process.pid, signal.SIGINT)
    time.sleep(0.5)
    assert process.poll() is None, "a single Ctrl+C during preflight killed the child wrapper (AI-CLI-s5cs)"

    os.killpg(process.pid, signal.SIGINT)
    stdout, stderr = _communicate_supervisor(process)

    assert process.returncode == 0, f"supervisor did not exit cleanly: {stdout!r} {stderr!r}"
    assert "Ctrl+C again within" in stderr


def _child_launch_loop_anchor(script: str) -> str:
    """Return the generated line that installs the per-child EXIT trap, verbatim.

    That trap sits immediately above the child launch loop, so splicing before it
    is how a test gets a statement to run once before any child starts.

    Located by its two invariant substrings rather than matched as an exact literal
    (AI-CLI-ta1l). The literal form drifted when the trap gained a
    ``[[ -n "$watcher_pid" ]] &&`` guard, and every caller then failed with
    "expected the child launch loop" — a stale-anchor problem in the harness
    reported as if the template were broken. Uniqueness is asserted, so a future
    change that makes the anchor ambiguous still fails loudly instead of splicing
    into the wrong place.
    """
    matches = [line for line in script.splitlines() if "watcher_pid" in line and line.rstrip().endswith("EXIT")]
    assert len(matches) == 1, f"expected exactly one per-child EXIT trap to anchor on, found {matches}"
    return matches[0]


def test_given_persisted_exit_request_when_replacement_child_starts_then_it_skips_direnv_and_exits(
    tmp_path: Path, supported_session_shell: str
):
    """A replacement child must honor an earlier double-Ctrl+C before preflight.

    The escape request survives child replacement in session state.  Previously
    ``run_agent`` evaluated ``direnv export bash`` before checking that state,
    so a replacement could visibly enter (or stall in) direnv despite an
    already-recorded request to exit.
    """
    exit_file = tmp_path / "state" / "ai-cli-utils" / "session-int-exit-test-session"
    direnv_called = tmp_path / "direnv-called"
    real_script = get_engine_script("c", "session-1", "test-session", "test-", "myproject", is_remote=False)
    marker = "trap '_child_record_int' INT"
    assert real_script.count(marker) == 1, "expected exactly one child-level INT trap installation"
    instrumented_script = real_script.replace(
        marker,
        marker + '\n    printf \'%s\\n\' "$$" > "$AI_CLI_TEST_CHILD_READY"',
        1,
    )
    loop_start = _child_launch_loop_anchor(instrumented_script)
    instrumented_script = instrumented_script.replace(
        loop_start,
        f"    printf '%s\\n' exit > {str(exit_file)!r}\n" + loop_start,
        1,
    )
    direnv = f"#!/bin/sh\nprintf '%s\\n' called > {str(direnv_called)!r}\nprintf '%s\\n' 'export TEST_DIRENV=1'\n"
    process, _, _ = _start_generated_supervisor(
        tmp_path,
        supported_session_shell,
        lease_acquired=False,
        child_body=instrumented_script,
        extra_commands={"direnv": direnv},
    )

    stdout, stderr = _communicate_supervisor(process)

    assert process.returncode == 0, f"supervisor did not exit cleanly: {stdout!r} {stderr!r}"
    assert not direnv_called.exists(), "an already-requested exit must not start direnv"
    cleanup_event = next(
        event
        for event in (tmp_path / "tmux-events.log").read_text(encoding="utf-8").splitlines()
        if event.startswith("if-shell -F -t $1 ")
    )
    assert "#{==:#{session_id}|#{@ai_cli_session_generation},$1|" in cleanup_event
    assert cleanup_event.endswith(" kill-session -t '$1' display-message -p __ai_cli_ownership_mismatch__")


def test_given_live_generated_supervisor_when_sigterm_is_repeated_then_child_receives_one_relay(
    tmp_path: Path, supported_session_shell: str
):
    process, events, _ = _start_generated_supervisor(tmp_path, supported_session_shell, lease_acquired=False)

    os.kill(process.pid, signal.SIGTERM)
    time.sleep(0.05)
    if process.poll() is None:
        os.kill(process.pid, signal.SIGTERM)
    stdout, stderr = _communicate_supervisor(process)

    assert process.returncode is not None, f"supervisor did not finish: {stdout!r} {stderr!r}"
    assert events.read_text(encoding="utf-8").splitlines() == ["TERM"]


def test_given_child_exits_during_term_relay_when_supervisor_receives_term_then_it_relays_only_once(
    tmp_path: Path, supported_session_shell: str
):
    child_body = f"""#!{supported_session_shell}
if [[ "${{1:-}}" == "--ai-cli-child-body" ]]; then
  printf '%s\\n' "$$" > "$AI_CLI_TEST_CHILD_READY"
  trap 'printf "TERM\\n" >> "$AI_CLI_TEST_EVENTS"; exit 0' TERM
  while true; do sleep 0.05; done
fi
"""
    process, events, _ = _start_generated_supervisor(
        tmp_path, supported_session_shell, lease_acquired=False, child_body=child_body
    )

    os.kill(process.pid, signal.SIGTERM)
    _wait_for_path(events, process)
    if process.poll() is None:
        os.kill(process.pid, signal.SIGTERM)
    stdout, stderr = _communicate_supervisor(process)

    assert process.returncode is not None, f"supervisor did not finish: {stdout!r} {stderr!r}"
    assert events.read_text(encoding="utf-8").splitlines() == ["TERM"]


def test_given_shared_supervisor_process_group_when_direct_group_signal_arrives_then_child_receives_signal_and_stdin(
    tmp_path: Path, supported_session_shell: str
):
    child_body = f"""#!{supported_session_shell}
if [[ "${{1:-}}" == "--ai-cli-child-body" ]]; then
  printf '%s\\n' "$$" > "$AI_CLI_TEST_CHILD_READY"
  trap 'printf "INT\\n" >> "$AI_CLI_TEST_EVENTS"' INT
  IFS= read -r line
  printf 'READ=%s\\n' "$line" >> "$AI_CLI_TEST_EVENTS"
  while true; do sleep 0.05; done
fi
"""
    process, events, _ = _start_generated_supervisor(
        tmp_path, supported_session_shell, lease_acquired=False, child_body=child_body, terminal=True
    )
    assert process.stdin is not None
    child_pid = int((tmp_path / "child-ready").read_text(encoding="utf-8"))
    assert os.getpgid(child_pid) == process.pid

    process.stdin.write("hello\n")
    process.stdin.flush()
    _wait_for_path(events, process)
    os.killpg(process.pid, signal.SIGINT)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if events.exists() and "INT" in events.read_text(encoding="utf-8"):
            break
        time.sleep(0.02)

    assert events.read_text(encoding="utf-8").splitlines() == ["READ=hello", "INT"]
    _finish_supervisor(process)


def test_given_shared_supervisor_process_group_when_direct_group_signal_arrives_then_detached_ticker_continues_ticking(
    tmp_path: Path, supported_session_shell: str
):
    process, events, heartbeats = _start_generated_supervisor(tmp_path, supported_session_shell, fast_heartbeat=True)
    os.killpg(process.pid, signal.SIGINT)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if heartbeats.exists() and len(heartbeats.read_text(encoding="utf-8").splitlines()) >= 2:
            break
        time.sleep(0.02)

    assert heartbeats.exists()
    assert len(heartbeats.read_text(encoding="utf-8").splitlines()) >= 2
    _finish_supervisor(process)


def test_given_remote_normal_child_exit_when_supervisor_waits_then_next_child_starts(
    tmp_path: Path, supported_session_shell: str
):
    child_body = f"""#!{supported_session_shell}
if [[ "${{1:-}}" == "--ai-cli-child-body" ]]; then
  count_file="$AI_CLI_TEST_CHILD_READY.count"
  count=$(cat "$count_file" 2>/dev/null || echo 0)
  count=$((count + 1))
  printf '%s\\n' "$count" > "$count_file"
  if (( count == 1 )); then exit 0; fi
  printf '%s\\n' "$$" > "$AI_CLI_TEST_CHILD_READY"
  while true; do sleep 0.05; done
fi
"""
    process, _, _ = _start_generated_supervisor(
        tmp_path, supported_session_shell, lease_acquired=False, is_remote=True, child_body=child_body
    )

    assert (tmp_path / "child-ready.count").read_text(encoding="utf-8").strip() == "2"
    _finish_supervisor(process)


def test_given_delayed_child_process_group_when_supervisor_promotes_then_it_waits_for_readiness(
    tmp_path: Path, supported_session_shell: str
):
    """The child must not be killed merely because its process-group setup is slow.

    A stable-script hot reload starts a second child through this path.  Exercise
    the generated supervisor against a real pseudo-terminal and delay the child's
    actual ``setpgrp`` beyond the former 500 ms polling budget.
    """
    process, _, _ = _start_generated_supervisor(
        tmp_path,
        supported_session_shell,
        lease_acquired=False,
        child_group_delay=12.0,
        pseudo_terminal=True,
        ready_timeout=30,
    )

    assert process.poll() is None
    assert (tmp_path / "child-ready").exists()
    _finish_supervisor(process)


def test_given_reload_exit_when_supervisor_starts_second_child_then_it_is_promoted(
    tmp_path: Path, supported_session_shell: str
):
    """A reload must promote the replacement child, not leave its wrapper stopped."""
    child_body = f"""#!{supported_session_shell}
if [[ "${{1:-}}" == "--ai-cli-child-body" ]]; then
  count_file="$AI_CLI_TEST_CHILD_READY.count"
  count=$(cat "$count_file" 2>/dev/null || echo 0)
  count=$((count + 1))
  printf '%s\\n' "$count" > "$count_file"
  if (( count == 1 )); then
    exit 78
  fi
  printf '%s\\n' "$$" > "$AI_CLI_TEST_CHILD_READY"
  while true; do sleep 0.05; done
fi
"""
    process, _, _ = _start_generated_supervisor(
        tmp_path,
        supported_session_shell,
        lease_acquired=False,
        child_body=child_body,
        pseudo_terminal=True,
    )

    second_child = int((tmp_path / "child-ready").read_text(encoding="utf-8"))
    assert (tmp_path / "child-ready.count").read_text(encoding="utf-8").strip() == "2"
    assert psutil.Process(second_child).status() != psutil.STATUS_STOPPED
    _finish_supervisor(process)


def test_given_generated_session_script_when_rendered_then_it_never_signals_any_mosh_server():
    """The generated script used to `kill` any `mosh-server` process anywhere
    on the host whose command line matched this session's own --project-prefix and was older
    than 60s, on every session launch AND every child-body restart (self-update, /memory
    reload, etc). Two sibling sessions sharing a project prefix (e.g. both launched with
    --project-prefix mp) would kill each other's mosh-server the next time either one
    restarted.
    No script this function generates may contain a `kill` targeting a `mosh-server` process,
    for any engine/prefix/project combination.
    """
    for engine in ("c", "g", "p", "cx"):
        for is_remote in (False, True):
            script = get_engine_script(
                engine,
                "test-ai-name",
                "test-session",
                "test-",
                "mp",
                is_remote=is_remote,
                project_name="myproject",
            )
            assert "mosh-server" not in script, (
                f"engine={engine} is_remote={is_remote}: generated script references "
                "mosh-server at all -- if this is intentional (e.g. a safe, session-scoped "
                "check), update this test's assertion; a bare 'kill'-adjacent mosh-server "
                "reference is what caused AI-CLI-sdgi"
            )


# ── The child EXIT trap under each supported shell (AI-CLI-vj64) ────────────────
#
# THE MEASURED DEFECT. In zsh, `kill ""` signals the CURRENT PROCESS GROUP instead
# of erroring, and `2>/dev/null` hides the message without preventing the signal:
#
#   bash -c 'p=""; kill "$p" 2>/dev/null; echo survived'  -> survived, status 1
#   zsh  -c 'p=""; kill "$p" 2>/dev/null; echo survived'  -> Terminated, exit 143
#
# The child's EXIT trap killed `$watcher_pid` unguarded, and that variable is
# legitimately empty both before a watcher starts and after the surrounding branch
# stops one and resets it -- so on zsh the trap SIGTERMed the child's own group
# during teardown. The same function already guarded its other kill of that
# variable 125 lines earlier; the trap was the one place that did not.
#
# These tests execute the shipped trap COMMAND rather than driving a whole
# supervisor. That is deliberate: the full-supervisor route is exactly what
# AI-CLI-d7e5 shows to be fragile, where an unrelated harness stub silently moved
# the run onto a different branch and the test stopped measuring its own subject.
# Extracting the command keeps the shell and the signal real, which is where the
# defect lives.
#
# The guard can fail, measured against the pre-fix trap body rather than assumed:
#   bash  -> returncode 0, stdout 'reached'
#   zsh   -> returncode -15 (killed by SIGTERM), no output
# So this is a zsh-only regression, and a bash-only test could never have caught it.
# The selector needs BOTH tokens. Matching on the lock-file cleanup alone found two
# EXIT traps, because a second one removes the lock without touching a watcher --
# and picking the wrong one would have tested a trap that has no kill in it, which
# passes trivially and proves nothing.
_WATCHER_TOKEN = '"$watcher_pid"'
_LOCK_FILE_TOKEN = 'rm -f "$lock_file"'


def _child_exit_trap_body(script: str) -> str:
    """The child EXIT trap's command string, read out of the generated script."""
    bodies = [
        line.split("'", 2)[1]
        for line in script.splitlines()
        if line.strip().startswith("trap '")
        and line.rstrip().endswith("' EXIT")
        and _LOCK_FILE_TOKEN in line
        and _WATCHER_TOKEN in line
    ]
    assert len(bodies) == 1, (
        f"expected exactly one child EXIT trap containing both {_WATCHER_TOKEN!r} and "
        f"{_LOCK_FILE_TOKEN!r}, found {len(bodies)}; the trap moved or changed shape, so "
        "this test is no longer reading the command it exists to check"
    )
    return bodies[0]


def _run_trap_body(shell: str, program: str) -> subprocess.CompletedProcess[str]:
    """Run a snippet in its own session, so a group-wide kill cannot reach pytest."""
    return subprocess.run(
        [shell, "-c", program],
        capture_output=True,
        text=True,
        check=False,
        # Load-bearing: without a new session, the very defect under test would
        # signal this test runner's own process group.
        start_new_session=True,
    )


def test_given_no_watcher_when_the_child_exit_trap_runs_then_it_signals_nothing(
    tmp_path: Path, supported_session_shell: str
):
    """An empty watcher_pid must not be turned into a process-group SIGTERM."""
    body = _child_exit_trap_body(
        get_engine_script("c", "session-1", "test-session", "test-", "myproject", is_remote=False)
    )
    lock_file = tmp_path / "child.lock"
    lock_file.write_text("", encoding="utf-8")
    program = f'watcher_pid=""\nlock_file={shlex.quote(str(lock_file))}\n{body}\nprintf reached\n'

    result = _run_trap_body(supported_session_shell, program)

    assert result.returncode == 0, (
        f"the trap terminated its own process group: exit {result.returncode} "
        f"(143 is SIGTERM), stderr={result.stderr!r}"
    )
    assert result.stdout == "reached", result.stdout
    # The cleanup must survive the guard: an early exit would leak the lock.
    assert not lock_file.exists(), "the trap skipped its lock-file cleanup"


def test_given_a_live_watcher_when_the_child_exit_trap_runs_then_it_is_still_terminated(
    tmp_path: Path, supported_session_shell: str
):
    """The guard must not be satisfied by never killing anything."""
    body = _child_exit_trap_body(
        get_engine_script("c", "session-1", "test-session", "test-", "myproject", is_remote=False)
    )
    lock_file = tmp_path / "child.lock"
    lock_file.write_text("", encoding="utf-8")
    program = (
        f"lock_file={shlex.quote(str(lock_file))}\n"
        "sleep 30 &\n"
        "watcher_pid=$!\n"
        f"{body}\n"
        'wait "$watcher_pid"\n'
        "printf '%s' \"$?\"\n"
    )

    result = _run_trap_body(supported_session_shell, program)

    # 143 == 128 + SIGTERM: the watcher was reaped after being signalled, so this
    # reads the wait status rather than a zombie that `kill -0` would still find.
    assert result.stdout == "143", (
        f"a live watcher was not terminated: wait status {result.stdout!r}, stderr={result.stderr!r}"
    )
    assert not lock_file.exists(), "the trap skipped its lock-file cleanup"
