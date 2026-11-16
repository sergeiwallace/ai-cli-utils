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
from ai_cli.process_probe import StartTimeMatch, probe_for
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


def _start_session(tmp_path, monkeypatch, name: str, agent_argv: list[str]):
    """Register and run NAME with a real supervisor whose child is the agent process.

    The agent child carries AGENT_ARGV in its command line, as a launched Codex does
    (``codex --no-daemon`` or a default ``codex``), because the engine binding is read
    from the process's own command line.
    """
    _ancestry(monkeypatch, [])
    # The supervisor starts the agent, as the session script does: exit-session refuses an
    # agent that does not descend from the recorded supervisor.
    supervisor = spawn_owned_python(
        "import subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)', *{agent_argv!r}])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(120)\n",
        stdout=subprocess.PIPE,
        text=True,
    )
    assert supervisor.stdout is not None
    agent = psutil.Process(int(supervisor.stdout.readline()))
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
    return generation, supervisor, agent


@pytest.fixture
def codex_session(tmp_path, monkeypatch):
    """A registered, running `ai cx -R` session whose Codex runs its own engine (--no-daemon)."""
    name = f"cx-r-{CANONICAL}"
    generation, supervisor, agent = _start_session(tmp_path, monkeypatch, name, ["codex", "--no-daemon"])
    try:
        yield name, generation, supervisor, agent
    finally:
        reap_tree(supervisor)


@pytest.fixture
def shared_daemon_session(tmp_path, monkeypatch):
    """The same session on a default Codex, whose engine is the host's shared app-server daemon."""
    name = f"cx-r-{CANONICAL}"
    generation, supervisor, agent = _start_session(tmp_path, monkeypatch, name, ["codex"])
    try:
        yield name, generation, supervisor, agent
    finally:
        reap_tree(supervisor)


# --- registry rows ------------------------------------------------------------------


@pytest.mark.parametrize(("engine", "family"), [("c", "claude"), ("cx", "codex"), ("g", "gemini"), ("p", "pi")])
def test_given_any_engine_when_launch_registers_then_row_records_its_family(tmp_path, engine, family):
    _register(engine, f"{engine}-{CANONICAL}", tmp_path)

    assert _row(f"{engine}-{CANONICAL}")["family"] == family


def test_given_running_codex_agent_when_its_identity_is_recorded_then_row_holds_what_a_consumer_verifies(
    codex_session,
):
    name, generation, _supervisor, agent = codex_session

    row = _row(name)
    identity = row["agent_identity"]

    assert row["family"] == "codex"
    assert row["canonical_name"] == CANONICAL
    assert identity["pid"] == agent.pid
    assert identity["started_at"] == psutil.Process(agent.pid).create_time()
    assert identity["interactive"] is True
    assert identity["engine_binding"] == "exclusive"
    assert identity["generation"] == generation
    assert identity["boot_id"] == registry.current_boot_id()
    probe = probe_for()
    captured = probe.capture_identity(agent.pid)
    assert captured is not None
    assert identity["proc_start_backend"] == captured.backend
    assert probe.start_time_match(agent.pid, identity["proc_start"]) is StartTimeMatch.MATCH


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
def test_given_agent_started_twice_by_the_session_script_when_running_then_each_start_records_its_own_pid(tmp_path):
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
export AI_CLI_RUN_GENERATION=generation-2
run_agent sh -c 'echo $$ > {tmp_path}/agent-1.pid'
run_agent sh -c 'echo $$ > {tmp_path}/agent-2.pid'
wait
""",
        encoding="utf-8",
    )
    done = subprocess.run(["bash", str(harness)], capture_output=True, text=True, timeout=30, check=False)

    assert done.returncode == 0, done.stderr
    # Every start (a fresh codex, or `codex resume <id>` on a relaunch) is a new process,
    # recorded under the generation of the supervisor that started it.
    pids = [(tmp_path / f"agent-{n}.pid").read_text().strip() for n in (1, 2)]
    recorded = sorted(line for line in log.read_text().splitlines() if "record-agent-identity" in line)
    assert pids[0] != pids[1]
    assert recorded == sorted(f"AI internal record-agent-identity cx-{CANONICAL} {pid} generation-2" for pid in pids)


# --- bare launches --------------------------------------------------------------------


@posix_only
def test_given_bare_launch_on_a_launcher_owned_pty_when_the_agent_starts_then_its_pid_is_handed_over(tmp_path):
    pid_file = tmp_path / "agent.pid"
    program = (
        "import sys\n"
        "from ai_cli import pty_host\n"
        "seen = []\n"
        f"status = pty_host.host('sh', ['sh', '-c', 'echo $$ > {pid_file}'], session='test-session', on_start=seen.append)\n"
        "print(seen, status)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", program],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "XDG_STATE_HOME": str(tmp_path / "state")},
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == [f"[{pid_file.read_text().strip()}]", "0"]


@posix_only
def test_given_bare_exec_launch_of_ai_cx_when_it_hands_off_then_the_row_records_the_process_that_becomes_codex(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import patch

    from test_session_launch_locality import _launch_in, _make_repo

    repo = _make_repo(tmp_path / "projects" / "myproject")
    monkeypatch.chdir(repo)
    no_pty = SimpleNamespace(kind="tmux", must_refuse=False, platform_limited=False, detail="no pty in this test")
    with (
        patch("ai_cli.compact_transport.resolve", return_value=no_pty),
        patch("ai_cli.main.shutil.which", return_value="/usr/bin/codex"),
        patch("ai_cli.main._name_new_codex_session"),
    ):
        _launch_in(monkeypatch, tmp_path, engine="cx")

    row = next(r for r in registry.load_registry()["sessions"] if r.get("name", "").startswith("cx-"))
    # execvp keeps the pid, so the launcher's pid and start time become Codex's.
    assert row["agent_identity"]["pid"] == os.getpid()
    assert row["agent_identity"]["started_at"] == psutil.Process().create_time()


# --- one owner per attested agent ------------------------------------------------------


def test_given_agent_attested_by_one_session_when_another_session_claims_the_same_process_then_it_is_refused(
    codex_session, tmp_path, capsys
):
    name, _generation, _supervisor, agent = codex_session
    other = "cx-r-myproject-box-session-2"
    other_generation = _register("cx", other, tmp_path)
    state.mark_running(other, generation=other_generation)
    before = _row(name)["agent_identity"]

    with pytest.raises(SystemExit) as exc_info:
        _handle_internal(["record-agent-identity", other, str(agent.pid), other_generation])

    assert exc_info.value.code == 1
    assert f"agent identity not recorded: pid {agent.pid} is already attested by live session {name}" in (
        capsys.readouterr().err
    )
    assert "agent_identity" not in _row(other)
    assert _row(name)["agent_identity"] == before


def test_given_session_relaunched_when_it_gets_a_new_generation_then_the_old_attestation_no_longer_proves_it(
    codex_session, monkeypatch
):
    name, old_generation, _supervisor, agent = codex_session

    new_generation = state.begin(name)
    _ancestry(monkeypatch, [psutil.Process(agent.pid)])
    result = CliRunner().invoke(cli, ["exit-session", "-d"])

    assert new_generation != old_generation
    assert _row(name)["agent_identity"]["generation"] == old_generation
    assert result.exit_code != 0
    assert "cannot verify current session" in result.output


# --- a Codex on the shared app-server daemon ------------------------------------------


def test_given_default_codex_on_the_shared_daemon_when_recorded_then_its_binding_is_shared_daemon(
    shared_daemon_session,
):
    name, _generation, _supervisor, agent = shared_daemon_session

    identity = _row(name)["agent_identity"]

    assert identity["pid"] == agent.pid
    assert identity["engine_binding"] == "shared-daemon"


def test_given_shared_daemon_session_when_exit_session_runs_inside_without_name_then_it_refuses_and_asks_for_it(
    shared_daemon_session, monkeypatch
):
    _name, _generation, supervisor, agent = shared_daemon_session
    monkeypatch.setenv("AI_SESSION_NAME", CANONICAL)
    _ancestry(monkeypatch, [psutil.Process(agent.pid)])
    before = registry.registry_path().read_bytes()

    result = CliRunner().invoke(cli, ["exit-session"])

    assert result.exit_code != 0
    assert "shared app-server daemon" in result.output
    assert f"ai exit-session {CANONICAL}" in result.output
    assert registry.registry_path().read_bytes() == before
    assert supervisor.poll() is None
