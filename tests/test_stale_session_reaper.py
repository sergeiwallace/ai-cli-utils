"""Safety regressions for the isolated stale-session evaluator.

The tmux boundary is controlled, but the ledger, generation lease, and process
identity values are real filesystem/process primitives.  Every negative control
asserts that no ID-targeted kill is issued.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import portalocker
import pytest

from ai_cli.process_probe import ProcessIdentity, ProcessProbe, ProcfsProbe, PsutilProbe
from ai_cli.session_script import get_engine_script
from ai_cli.stale_session_reaper import (
    Pane,
    SessionCandidate,
    StaleSessionReaper,
    generation_lease_path,
    heartbeat_path,
    write_heartbeat,
)


class _ControlledTmux:
    def __init__(self, candidates: list[SessionCandidate]) -> None:
        self.candidates = candidates
        self.kills: list[str] = []

    def sessions(self) -> list[SessionCandidate]:
        return [] if self.kills else list(self.candidates)

    def kill_session(self, session_id: str) -> bool:
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


@pytest.fixture(params=("bash", "zsh"))
def supported_session_shell(request: pytest.FixtureRequest) -> str:
    """Run each signal regression under every supported installed shell."""
    shell = shutil.which(request.param)
    if shell is None:
        pytest.skip(f"{request.param} is not installed")
    if os.name == "nt" or not hasattr(signal, "SIGWINCH"):
        pytest.skip("generated tmux session signals require a POSIX shell")
    return shell


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o700)


def _wait_for_path(path: Path, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            pytest.fail(f"supervisor exited before its child was ready: {stdout!r} {stderr!r}")
        time.sleep(0.02)
    pytest.fail("supervisor child did not become ready")


def _start_generated_supervisor(
    tmp_path: Path,
    shell: str,
    *,
    terminal_pgid: str = "123",
    supervisor_pgid: str = "123",
    lease_acquired: bool = True,
) -> tuple[subprocess.Popen[str], Path, Path]:
    """Start the generated supervisor with controlled external session commands."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    event_log = tmp_path / "events.log"
    heartbeat_log = tmp_path / "heartbeats.log"
    child_ready = tmp_path / "child-ready"
    _write_executable(bin_dir / "tmux", "#!/bin/sh\nexit 0\n")
    _write_executable(
        bin_dir / "ai",
        "#!/bin/sh\n"
        'if [ "$1" = "internal" ] && [ "$2" = "publish-heartbeat" ]; then\n'
        '  printf "heartbeat\\n" >> "$AI_CLI_TEST_HEARTBEATS"\n'
        "fi\n"
        "exit 0\n",
    )
    _write_executable(bin_dir / "flock", f"#!/bin/sh\nexit {int(not lease_acquired)}\n")
    _write_executable(
        bin_dir / "ps",
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *tpgid=*) printf " %s\\n" "$AI_CLI_TEST_TERMINAL_PGID" ;;\n'
        '  *pgid=*) printf " %s\\n" "$AI_CLI_TEST_SUPERVISOR_PGID" ;;\n'
        "  *) exit 1 ;;\n"
        "esac\n",
    )

    state_home = tmp_path / "state"
    sessions_dir = state_home / "ai-cli-utils" / "sessions"
    sessions_dir.mkdir(parents=True)
    _write_executable(
        sessions_dir / "test-session.sh",
        f"""#!{shell}
if [[ "${{1:-}}" == "--ai-cli-child-body" ]]; then
  printf '%s\\n' "$$" > "$AI_CLI_TEST_CHILD_READY"
  trap 'printf "TERM\\n" >> "$AI_CLI_TEST_EVENTS"; sleep 0.4; exit 77' TERM
  trap 'printf "INT\\n" >> "$AI_CLI_TEST_EVENTS"' INT
  trap 'printf "WINCH\\n" >> "$AI_CLI_TEST_EVENTS"' WINCH
  while true; do sleep 0.05; done
fi
""",
    )
    supervisor = tmp_path / "supervisor.sh"
    supervisor.write_text(get_engine_script("c", "session-1", "test-session", "test-", "myproject"), encoding="utf-8")
    environment = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "XDG_STATE_HOME": str(state_home),
        "AI_CLI_TEST_CHILD_READY": str(child_ready),
        "AI_CLI_TEST_EVENTS": str(event_log),
        "AI_CLI_TEST_HEARTBEATS": str(heartbeat_log),
        "AI_CLI_TEST_SUPERVISOR_PGID": supervisor_pgid,
        "AI_CLI_TEST_TERMINAL_PGID": terminal_pgid,
    }
    process = subprocess.Popen(
        [shell, *(["-o", "NO_BG_NICE"] if Path(shell).name == "zsh" else []), str(supervisor)],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    _wait_for_path(child_ready, process)
    return process, event_log, heartbeat_log


def _finish_supervisor(process: subprocess.Popen[str]) -> tuple[str, str]:
    if process.poll() is None:
        os.kill(process.pid, signal.SIGTERM)
    return process.communicate(timeout=5)


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


def test_given_live_generated_supervisor_when_sigterm_is_repeated_then_child_receives_one_relay(
    tmp_path: Path, supported_session_shell: str
):
    process, events, _ = _start_generated_supervisor(tmp_path, supported_session_shell, lease_acquired=False)

    os.kill(process.pid, signal.SIGTERM)
    time.sleep(0.05)
    if process.poll() is None:
        os.kill(process.pid, signal.SIGTERM)
    stdout, stderr = process.communicate(timeout=5)

    assert process.returncode is not None, f"supervisor did not finish: {stdout!r} {stderr!r}"
    assert events.read_text(encoding="utf-8").splitlines() == ["TERM"]
