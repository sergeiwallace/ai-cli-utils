"""Run the generated lifecycle functions and final-exit supervisor path."""

import os
import shlex
import shutil
import subprocess
import sys

import pytest

from ai_cli import session_registry as registry
from ai_cli import session_run_state as state
from ai_cli.session_lifecycle_script import SESSION_STATE_FUNCTIONS
from ai_cli.session_script import get_engine_script


@pytest.fixture
def shell_environment(tmp_path):
    shell = shutil.which("bash")
    if shell is None:
        pytest.skip("bash is unavailable")
    name = "c-session-1"
    state.register(name, recipe={"argv": ["ai", "c", "1"], "cwd": str(tmp_path)}, host="host-a")
    generation = state.begin(name)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ai = bin_dir / "ai"
    ai.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -m ai_cli.main "$@"\n')
    ai.chmod(0o755)
    # No real terminal, manager, agent or tmux is reached by these tests.
    tmux = bin_dir / "tmux"
    tmux.write_text("#!/bin/sh\nexit 1\n")
    tmux.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "AI_CLI_RUN_GENERATION": generation,
        "ZDOTDIR": str(tmp_path),
    }
    return shell, env, name, tmp_path


def test_status_77_when_supervisor_returns_then_manual_exit_is_persisted(shell_environment):
    shell, env, name, tmp_path = shell_environment
    child = registry.registry_path().parent / "sessions" / f"{name}.sh"
    child.parent.mkdir(parents=True)
    child.write_text("#!/bin/sh\nexit 77\n")
    script = tmp_path / "supervisor.sh"
    script.write_text(get_engine_script("c", "session-1", name, "c-session-", "myproject", worktree_dir=str(tmp_path)))
    completed = subprocess.run([shell, str(script)], env=env, capture_output=True, text=True, check=False, timeout=20)
    assert completed.returncode == 0, completed.stderr
    record = registry.load_registry()["sessions"][0]
    assert record["run_state"] == "exited"
    assert record["supervisor_exit"]["cause"] == "manual_exit"


def test_persistence_fails_when_final_exit_retries_then_no_agent_is_started(shell_environment):
    shell, env, name, tmp_path = shell_environment
    ai = tmp_path / "bin/ai"
    count = tmp_path / "attempts"
    ai.write_text(
        '#!/bin/sh\nif [ "$2" != mark ]; then exec '
        + shlex.quote(sys.executable)
        + ' -m ai_cli.main "$@"; fi\n'
        + f'n=$(cat {shlex.quote(str(count))} 2>/dev/null || echo 0)\nn=$((n + 1))\nprintf "%s" "$n" > {shlex.quote(str(count))}\n'
        + f'if [ "$n" -lt 3 ]; then exit 1; fi\nexec {shlex.quote(sys.executable)} -m ai_cli.main "$@"\n'
    )
    body = (
        SESSION_STATE_FUNCTIONS
        + f'\ntmux_session={name}\n_ai_state_dir={shlex.quote(str(registry.registry_path().parent))}\n_ai_emit() {{ printf "%s\\n" "$*"; }}\n_ai_persist_exit manual_exit \'{{}}\'\nprintf shell-returned\n'
    )
    completed = subprocess.run([shell, "-c", body], env=env, capture_output=True, text=True, check=False, timeout=20)
    assert completed.returncode == 0, completed.stderr
    assert count.read_text() == "3"
    assert completed.stdout.count("retrying") == 2
    assert completed.stdout.endswith("shell-returned")
    assert state.run_state(registry.load_registry()["sessions"][0]) == "exited"


def test_missing_registration_when_running_mark_attempted_then_agent_start_is_refused(shell_environment):
    shell, env, _, _ = shell_environment
    body = SESSION_STATE_FUNCTIONS + "\ntmux_session=missing\n_ai_mark_running || exit 76\nprintf agent-started\n"
    result = subprocess.run([shell, "-c", body], env=env, capture_output=True, text=True, check=False, timeout=20)
    assert result.returncode == 76
    assert "agent-started" not in result.stdout


def test_generated_supervisor_when_failed_start_stops_then_unknown_cause_is_written():
    script = get_engine_script("c", "session-1", "c-session-1", "c-session-", "myproject")
    for reason in ("three consecutive agent exits", "agent exited too quickly"):
        assert f'_ai_persist_exit unknown \'{{"reason":"{reason}"}}\'' in script
    assert script.index("_ai_mark_running || exit 76") < script.index('publish-session-event "$tmux_session" "started"')


@pytest.mark.parametrize("status", ["running", "exited"])
def test_given_no_python_on_path_when_lifecycle_marks_then_state_is_persisted(shell_environment, status):
    shell, env, name, tmp_path = shell_environment
    env["PATH"] = str(tmp_path / "bin")
    body = (
        SESSION_STATE_FUNCTIONS
        + f"\ntmux_session={name}\n_ai_state_dir={shlex.quote(str(registry.registry_path().parent))}\n"
        + ("_ai_mark_running" if status == "running" else "_ai_persist_exit manual_exit '{}' ")
    )
    result = subprocess.run([shell, "-c", body], env=env, capture_output=True, text=True, check=False, timeout=20)
    assert result.returncode == 0, result.stderr
    record = registry.load_registry()["sessions"][0]
    assert record["run_state"] == status
    if status == "exited":
        assert record["supervisor_exit"]["cause"] == "manual_exit"
