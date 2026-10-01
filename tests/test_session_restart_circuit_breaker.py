"""Restart behaviour of the generated session script's circuit breaker.

The template relaunches the agent after every exit, which is what lets a session
survive an agent that asks to be restarted — ``/login`` is the everyday case: the
agent exits deliberately and expects to come straight back. A circuit breaker
sits in that loop so a supervisor that is merely *cycling*, relaunching an agent
that cannot host a session and returns at once, gives up instead of spinning.

The breaker is documented, in its own message, as counting **consecutive** agent
exits. It did not. The count lived in a per-session state file that was only ever
incremented, and cleared only when a *new* supervisor started, so it accumulated
over one supervisor's entire life: the third agent exit stopped auto-restart for
good, however healthy those runs were and however many hours apart. On a remote
session that handed the pane to a recovery shell instead of relaunching the
agent, and that shell named no way back.

Everything here is asserted behaviourally against the real template, run by a
real shell: how many times the agent is actually launched, what the child body
hands back to its supervisor, and what the human is actually told. Nothing greps
the generated text for a threshold.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ai_cli import session_script
from ai_cli.session_script import get_engine_script

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="drives the POSIX session template under a POSIX shell",
)

#: Statuses the replaceable child body uses to tell its supervisor to stop
#: relaunching it: 77 a clean local final exit, 79 a completed remote recovery
#: shell. Anything else means "launch me again".
_STOP_STATUSES = (77, 79)

#: The status the recovery shell returns to ask for an in-place relaunch.
_RELAUNCH_STATUS = 78

#: Stands in for ``HEALTHY_SESSION_SECONDS`` so a run that must count as a real
#: session costs a few seconds rather than a minute. Patched into the module
#: before the template is generated, exactly as the production value is read.
_TEST_HEALTHY_SECONDS = 4

_HARNESS_TOOLS = (
    "bash",
    "sh",
    "env",
    "sleep",
    "date",
    "stat",
    "cat",
    "grep",
    "sed",
    "tr",
    "wc",
    "head",
    "tail",
    "mkdir",
    "rm",
    "ls",
    "id",
    "true",
    "false",
    "mktemp",
    "touch",
    "tty",
    "python3",
)


def _harness_bin(tmp_path: Path) -> Path:
    """A PATH directory holding only the tools the template legitimately reaches for.

    Deliberately without ``zsh``. The interpreter is baked into the template when it
    is generated, and a zsh child body re-reads the invoking user's ``~/.zshenv``,
    which restores the real PATH and lets the operator's real agent binary leak into
    the run — measured while building this harness, where the stand-in agent was
    bypassed entirely and the real one launched instead.
    """
    bin_dir = tmp_path / "harness-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for tool in _HARNESS_TOOLS:
        resolved = shutil.which(tool)
        if resolved:
            target = bin_dir / tool
            if not target.exists():
                target.symlink_to(resolved)

    # `ai` fronts every side effect the template delegates: events, heartbeats,
    # watchers, template refresh. Reporting an unmeasurable version is what keeps the
    # self-update branch a no-op, so a restart here is a restart and not a reload.
    ai = bin_dir / "ai"
    ai.write_text('#!/bin/sh\n[ "$1 $2" = "internal get-version" ] && echo unknown\nexit 0\n')
    ai.chmod(0o755)

    tmux = bin_dir / "tmux"
    tmux.write_text("#!/bin/sh\nexit 0\n")
    tmux.chmod(0o755)
    return bin_dir


def _install_fake_agent(bin_dir: Path, launch_log: Path, lifetime_seconds: int) -> None:
    """An agent that records each launch, lives for a fixed time, then exits cleanly."""
    agent = bin_dir / "claude"
    agent.write_text(f'#!/bin/sh\necho launch >> "{launch_log}"\nsleep {lifetime_seconds}\nexit 0\n')
    agent.chmod(0o755)


class _Harness:
    """A session's state on disk plus the ability to run one child body against it."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent_lifetime: int) -> None:
        self.bin_dir = _harness_bin(tmp_path)
        self.launch_log = tmp_path / "agent-launches"
        _install_fake_agent(self.bin_dir, self.launch_log, agent_lifetime)
        self.home = tmp_path
        self.state_dir = tmp_path / "state"
        sessions = self.state_dir / "ai-cli-utils" / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        self.script = sessions / "c-myproject-1.sh"
        self.script.write_text(self._template(tmp_path, monkeypatch))
        self.exit_count_file = self.state_dir / "ai-cli-utils" / "session-agent-exits-c-myproject-1"

    def _template(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
        monkeypatch.setenv("PATH", str(self.bin_dir))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
        assert shutil.which("zsh") is None, "the harness PATH must not expose zsh"
        monkeypatch.setattr(session_script, "HEALTHY_SESSION_SECONDS", _TEST_HEALTHY_SECONDS)
        # is_remote: a remote session hands the pane back to a shell when the launch
        # loop stops, so this is the branch whose banner the human actually reads.
        return get_engine_script(
            "c",
            "session-1",
            "c-myproject-1",
            "c-myproject-",
            "myproject",
            worktree_dir=str(tmp_path),
            project_name="myproject",
            is_remote=True,
        )

    def run_child(self, stdin_text: str | None = None) -> subprocess.CompletedProcess:
        """One replaceable child body, exactly as the supervisor starts it."""
        return subprocess.run(
            [str(self.bin_dir / "bash"), str(self.script), "--ai-cli-child-body"],
            env={
                "PATH": str(self.bin_dir),
                "HOME": str(self.home),
                "XDG_STATE_HOME": str(self.state_dir),
                "SHELL": str(self.bin_dir / "bash"),
                "TERM": "dumb",
            },
            input=stdin_text if stdin_text is not None else "",
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )

    def launches(self) -> int:
        return len(self.launch_log.read_text().splitlines()) if self.launch_log.exists() else 0

    def drive_supervisor_loop(self, iterations: int) -> tuple[list[int], str]:
        """Stand in for the supervisor's restart loop, stopping when a child asks it to."""
        statuses: list[int] = []
        output: list[str] = []
        for _ in range(iterations):
            completed = self.run_child()
            statuses.append(completed.returncode)
            output.append(completed.stdout + completed.stderr)
            if completed.returncode in _STOP_STATUSES:
                break
        return statuses, "\n".join(output)


def test_given_repeated_healthy_agent_exits_when_each_hosted_a_session_then_restarts_continue(tmp_path, monkeypatch):
    """A session must keep relaunching its agent however often it exits cleanly.

    This is the ``/login`` path: the agent exits on purpose after a healthy run and
    expects to be relaunched. Before the fix the third such exit tripped the
    "consecutive" breaker and the supervisor stopped relaunching for good.
    """
    harness = _Harness(tmp_path, monkeypatch, agent_lifetime=_TEST_HEALTHY_SECONDS + 2)
    statuses, output = harness.drive_supervisor_loop(iterations=4)

    assert harness.launches() == 4, (
        f"the agent was launched {harness.launches()} times across 4 restarts, so a healthy "
        f"exit stopped the session. statuses={statuses} output={output!r}"
    )
    assert not [status for status in statuses if status in _STOP_STATUSES], (
        f"a child body asked the supervisor to stop relaunching after a healthy agent run. "
        f"statuses={statuses} output={output!r}"
    )


def test_given_an_agent_that_cannot_host_a_session_when_it_keeps_exiting_then_the_session_stops(tmp_path, monkeypatch):
    """Negative constraint: making restarts durable must not create a spin loop.

    An agent that returns immediately is hosting nothing, and the template must still
    give up rather than relaunch it forever.
    """
    harness = _Harness(tmp_path, monkeypatch, agent_lifetime=0)
    statuses, output = harness.drive_supervisor_loop(iterations=6)

    assert statuses[-1] in _STOP_STATUSES, (
        f"a cycling agent never stopped the session, so the restart loop spins forever. "
        f"statuses={statuses} output={output!r}"
    )
    assert harness.launches() <= 3, f"the cycling agent was relaunched {harness.launches()} times before stopping"


def test_given_a_stopped_remote_session_when_the_pane_is_handed_back_then_both_routes_are_named(tmp_path, monkeypatch):
    """The recovery shell's banner is the only instruction the human gets.

    It named only how to close the session, which is why exiting it was reported as
    the way to trigger a restart. Both routes have to be there.
    """
    harness = _Harness(tmp_path, monkeypatch, agent_lifetime=0)
    _statuses, output = harness.drive_supervisor_loop(iterations=6)

    assert f"exit {_RELAUNCH_STATUS}" in output, (
        f"the recovery shell never named a way to get the agent back. output={output!r}"
    )
    assert "close this session" in output, (
        f"the recovery shell no longer says how to close the session. output={output!r}"
    )


def test_given_the_recovery_shell_when_the_human_asks_for_a_relaunch_then_the_supervisor_gets_one(
    tmp_path, monkeypatch
):
    """Asking for a relaunch from the recovery shell must actually produce one.

    The banner's restart route has to be real, not advice. The supervisor relaunches
    any child body that does not return a stop status, so the recovery shell passing
    78 back is an in-place restart — and it must clear the consecutive-exit count, or
    the relaunched agent inherits a tripped breaker and stops again at once.
    """
    harness = _Harness(tmp_path, monkeypatch, agent_lifetime=0)
    statuses, _output = harness.drive_supervisor_loop(iterations=6)
    assert statuses[-1] in _STOP_STATUSES, "precondition: the session must have stopped relaunching"
    assert harness.exit_count_file.exists(), "precondition: the breaker must have recorded exits"

    completed = harness.run_child(stdin_text=f"exit {_RELAUNCH_STATUS}\n")

    assert completed.returncode == _RELAUNCH_STATUS, (
        f"the recovery shell did not ask the supervisor for a relaunch; it returned "
        f"{completed.returncode}, which tears the session down. "
        f"output={(completed.stdout + completed.stderr)!r}"
    )
    assert not harness.exit_count_file.exists(), (
        "the relaunch left the consecutive-exit count in place, so the restarted agent inherits a tripped breaker"
    )


def test_given_the_harness_when_it_builds_a_path_then_the_real_agent_cannot_leak_in(tmp_path):
    """Guard the harness itself: a leaked real agent would make every test above vacuous.

    Resolution is checked in-process rather than by asking a shell, because asking a
    shell to resolve the agent's name is itself the laundering route ``conftest``
    refuses — and rightly, since a leak here is the real binary starting for real.
    """
    bin_dir = _harness_bin(tmp_path)
    launch_log = tmp_path / "agent-launches"
    _install_fake_agent(bin_dir, launch_log, 0)

    resolved = shutil.which("claude", path=str(bin_dir))

    assert resolved == str(bin_dir / "claude"), f"the harness PATH resolves an agent outside it: {resolved!r}"
