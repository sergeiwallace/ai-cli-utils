"""An `ai cx` session carries the same canonical identity as an `ai c` session.

The agent process gets ``AI_SESSION_NAME`` (and, for Codex, ``CLAUDE_CODE_TASK_LIST_ID``),
and the session's registry row records its family and the agent's pid and start time,
so ``ai exit-session`` can find a Codex session by its canonical name and verify it from
inside the session against the process table. Design: docs/designs/codex-session-naming.md.
"""

import os
import subprocess
import sys
from pathlib import Path

import psutil
import pytest
from click.testing import CliRunner
from process_ownership import reap_tree, spawn_owned_python

from ai_cli import session_launch_state as launch_state
from ai_cli import session_registry as registry
from ai_cli import session_run_state as state
from ai_cli.main import _cli_group as cli
from ai_cli.main import _handle_internal
from ai_cli.session_script import get_engine_script

CANONICAL = "myproject-box-session-1"
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="the session script runs under bash")


def _host() -> str:
    return (
        state.config.load_config(read_only=True).get("machine", {}).get("host_id")
        or registry.load_registry()["machine"]
    )


def _row(name: str) -> dict:
    return next(r for r in registry.load_registry()["sessions"] if r.get("name") == name)


def _register(engine: str, tmux_name: str, cwd: Path) -> str:
    launch_state.register_launch(
        tmux_name,
        new_start=True,
        engine=engine,
        cwd=cwd,
        project="myproject",
        options={"is_remote": "-r-" in tmux_name},
        extra_args=[],
    )
    return state.begin(tmux_name)


_real_parents = psutil.Process.parents


def _ancestry(monkeypatch, ancestors: list) -> None:
    """Give the test process these ancestors; every other process keeps its real ones.

    Some macOS sandboxes deny ancestry reads, and the exit command's descent check still
    needs the agent's real parent, its supervisor.
    """
    me = os.getpid()
    monkeypatch.setattr(psutil.Process, "parents", lambda self: ancestors if self.pid == me else _real_parents(self))


@pytest.fixture
def codex_session(tmp_path, monkeypatch):
    """A registered, running `ai cx -R` session whose supervisor and agent are real processes."""
    _ancestry(monkeypatch, [])
    # The supervisor starts the agent, as the session script does: exit-session refuses an
    # agent that does not descend from the recorded supervisor.
    supervisor = spawn_owned_python(
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(120)\n",
        stdout=subprocess.PIPE,
        text=True,
    )
    agent = psutil.Process(int(supervisor.stdout.readline()))
    try:
        name = f"cx-r-{CANONICAL}"
        generation = _register("cx", name, tmp_path)
        state.mark_running(name, generation=generation)

        def bind(doc):
            row = next(r for r in doc["sessions"] if r["name"] == name)
            row["supervisor_identity"] = {
                "pid": supervisor.pid,
                "started_at": psutil.Process(supervisor.pid).create_time(),
                "generation": generation,
            }
            return True

        registry._mutate(bind)
        with pytest.raises(SystemExit) as exc_info:
            _handle_internal(["record-agent-identity", name, str(agent.pid), generation])
        assert exc_info.value.code == 0
        yield name, generation, supervisor, agent
    finally:
        reap_tree(supervisor)


# --- registry rows ------------------------------------------------------------------


@pytest.mark.parametrize(("engine", "family"), [("c", "claude"), ("cx", "codex"), ("g", "gemini"), ("p", "pi")])
def test_given_any_engine_when_launch_registers_then_row_records_its_family(tmp_path, engine, family):
    _register(engine, f"{engine}-{CANONICAL}", tmp_path)

    assert _row(f"{engine}-{CANONICAL}")["family"] == family


def test_given_running_codex_agent_when_its_identity_is_recorded_then_row_holds_pid_and_start_time(codex_session):
    name, generation, _supervisor, agent = codex_session

    identity = _row(name)["agent_identity"]

    assert identity == {
        "pid": agent.pid,
        "started_at": psutil.Process(agent.pid).create_time(),
        "generation": generation,
    }


def test_given_stale_generation_when_agent_identity_is_recorded_then_row_is_left_alone(codex_session):
    name, _generation, supervisor, _agent = codex_session

    with pytest.raises(SystemExit) as exc_info:
        _handle_internal(["record-agent-identity", name, str(supervisor.pid), "an-older-generation"])

    assert exc_info.value.code == 1
    assert _row(name)["agent_identity"]["pid"] != supervisor.pid


# --- exit-session -------------------------------------------------------------------


def test_given_codex_session_when_exit_session_names_its_canonical_name_then_it_exits(codex_session):
    name, _generation, supervisor, _agent = codex_session

    result = CliRunner().invoke(cli, ["exit-session", CANONICAL, "-t", "0.1"])

    assert result.exit_code == 0, result.output
    assert "not registered" not in result.output
    assert _row(name)["supervisor_exit"]["cause"] == "manual_exit"
    supervisor.wait(timeout=3)


def test_given_exit_session_run_inside_the_codex_agent_when_no_name_is_given_then_it_resolves_the_session(
    codex_session, monkeypatch
):
    name, _generation, _supervisor, agent = codex_session
    _ancestry(monkeypatch, [psutil.Process(agent.pid)])

    result = CliRunner().invoke(cli, ["exit-session", "-d"])

    assert result.exit_code == 0, result.output
    assert f"Would exit {name}" in result.output


def test_given_recorded_agent_pid_now_reused_when_exit_session_runs_without_name_then_it_refuses(
    codex_session, monkeypatch
):
    name, _generation, _supervisor, agent = codex_session

    def reused(doc):
        next(r for r in doc["sessions"] if r["name"] == name)["agent_identity"]["started_at"] -= 1000
        return True

    registry._mutate(reused)
    _ancestry(monkeypatch, [psutil.Process(agent.pid)])

    result = CliRunner().invoke(cli, ["exit-session", "-d"])

    assert result.exit_code != 0
    assert "cannot verify current session" in result.output


# --- identity in the agent's environment --------------------------------------------


def _identity_env(tmp_path: Path, engine: str) -> dict[str, str]:
    script = get_engine_script(
        engine=engine,
        ai_name=CANONICAL,
        session=f"{engine}-r-{CANONICAL}",
        prefix=f"{engine}-r-myproject-",
        project_prefix="myproject",
        is_remote=True,
    )
    start = script.index("    # BEGIN AGENT NAMING identity")
    end = script.index("    # END AGENT NAMING", start)
    harness = tmp_path / "identity.sh"
    harness.write_text(
        f"engine={engine}\nai_name={CANONICAL}\nunset CLAUDE_CODE_TASK_LIST_ID AI_SESSION_NAME\n"
        f"{script[start:end]}\n"
        'printf "AI_SESSION_NAME=%s\\nCLAUDE_CODE_TASK_LIST_ID=%s\\n" "${AI_SESSION_NAME-}" "${CLAUDE_CODE_TASK_LIST_ID-}"\n',
        encoding="utf-8",
    )
    done = subprocess.run(["bash", str(harness)], capture_output=True, text=True, timeout=30, check=False)
    assert done.returncode == 0, done.stderr
    return dict(line.split("=", 1) for line in done.stdout.splitlines())


@posix_only
@pytest.mark.parametrize("engine", ["c", "cx", "g", "p"])
def test_given_any_engine_when_the_agent_starts_then_it_sees_the_canonical_session_name(tmp_path, engine):
    assert _identity_env(tmp_path, engine)["AI_SESSION_NAME"] == CANONICAL


@posix_only
def test_given_codex_session_when_the_agent_starts_then_its_task_list_id_is_the_canonical_name(tmp_path):
    assert _identity_env(tmp_path, "cx")["CLAUDE_CODE_TASK_LIST_ID"] == CANONICAL


@posix_only
def test_given_agent_started_by_the_session_script_when_running_then_its_own_pid_is_recorded(tmp_path):
    script = get_engine_script(
        engine="cx", ai_name=CANONICAL, session=f"cx-{CANONICAL}", prefix="cx-myproject-", project_prefix="myproject"
    )
    start = script.index("    run_agent() {")
    end = script.index("\n    }\n", start) + len("\n    }\n")
    log = tmp_path / "calls.log"
    harness = tmp_path / "run_agent.sh"
    harness.write_text(
        f"""
tmux_session=cx-{CANONICAL}
agent_attempted=false
agent_direnv_initialized=true
_child_int_exit_file={tmp_path}/no-int
_child_int_escape_file={tmp_path}/no-escape
_reset_agent_terminal() {{ :; }}
_ai_emit() {{ :; }}
ai() {{ printf 'AI %s\\n' "$*" >> {log}; }}
{script[start:end]}
run_agent sh -c 'echo $$ > {tmp_path}/agent.pid'
wait
""",
        encoding="utf-8",
    )
    done = subprocess.run(["bash", str(harness)], capture_output=True, text=True, timeout=30, check=False)

    assert done.returncode == 0, done.stderr
    agent_pid = (tmp_path / "agent.pid").read_text().strip()
    assert f"AI internal record-agent-identity cx-{CANONICAL} {agent_pid} " in log.read_text() + " "
