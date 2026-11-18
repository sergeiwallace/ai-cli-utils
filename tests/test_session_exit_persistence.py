"""Exercise exit persistence across real shell, process and storage boundaries."""

import errno
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from process_wait import scaled

from ai_cli import config, session_lifecycle, session_lifecycle_script
from ai_cli import session_registry as registry
from ai_cli import session_run_state as state
from ai_cli.session_script import get_engine_script

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Generated supervisors require a POSIX shell")


class ShellEnvironment(dict):
    def __repr__(self):
        return "<shell environment>"


@pytest.fixture(params=["bash", "zsh"])
def exit_shell(request, tmp_path, monkeypatch):
    shell = shutil.which(request.param)
    if shell is None:
        pytest.skip(f"{request.param} is unavailable")
    name = "c-session-1"
    state.register(name, recipe={"argv": ["ai", "c", "1"], "cwd": str(tmp_path)}, host="host-a")
    generation = state.begin(name)
    timing_scale = scaled(1)
    policy = {
        "deadline_seconds": 2 * timing_scale,
        "command_timeout_seconds": 0.4 * timing_scale,
        "backoff_seconds": 0.1 * timing_scale,
        "max_backoff_seconds": 0.2 * timing_scale,
    }
    load = config.load_config
    monkeypatch.setattr(
        config, "load_config", lambda **kwargs: load(**kwargs) | {"session_relaunch": {"exit_persistence": policy}}
    )
    attempts = tmp_path / "attempts"
    endpoint = tmp_path / "endpoint.py"

    def render(program=None, prefix="", suffix=""):
        if program is None:
            # Importing the full CLI costs more than the deliberately tiny fault endpoints.
            policy.update(deadline_seconds=scaled(6), command_timeout_seconds=scaled(3))
        if program is not None:
            endpoint.write_text(program, encoding="utf-8")
            monkeypatch.setattr(
                session_lifecycle_script,
                "lifecycle_command",
                lambda *_args: [
                    sys.executable,
                    "-I",
                    *([] if "from ai_cli.main import cli" in program else ["-S"]),
                    str(endpoint),
                ],
                raising=False,
            )
            cli = tmp_path / "ai"
            cli.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(endpoint))} "$@"\n')
            cli.chmod(0o755)
            prefix = f"export PATH={shlex.quote(str(tmp_path))}:$PATH\n" + prefix
        generated = get_engine_script("c", "session-1", name, "c-session-", "myproject")
        helpers = generated.split("# BEGIN RUN STATE helpers", 1)[1].split("# END RUN STATE", 1)[0]
        script = tmp_path / "exit.sh"
        script.write_text(
            f"tmux_session={name}\nAI_CLI_RUN_GENERATION={shlex.quote(generation)}\n"
            f"_ai_state_dir={shlex.quote(str(registry.registry_path().parent))}\n"
            '_ai_emit() { printf "%s\\n" "$*" >&2; }\n'
            + helpers
            + "\n"
            + prefix
            + "\n_ai_persist_exit manual_exit '{}'\n"
            + suffix,
            encoding="utf-8",
        )
        return script

    env = ShellEnvironment({**os.environ, "ZDOTDIR": str(tmp_path)})
    return shell, env, render, attempts, generation


def run_exit(exit_shell, program=None, prefix="", suffix=""):
    shell, env, render, _, _ = exit_shell
    return subprocess.run(
        [shell, str(render(program, prefix, suffix))],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=scaled(8),
    )


def test_given_shadow_cli_when_exit_persists_then_verified_installation_is_used(exit_shell, tmp_path):
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    endpoint = shadow / "ai"
    endpoint.write_text("#!/bin/sh\nexit 42\n")
    endpoint.chmod(0o755)
    (shadow / "sleep").symlink_to(shutil.which("sleep"))
    package = shadow / "ai_cli"
    package.mkdir()
    (package / "__init__.py").write_text("raise RuntimeError('shadow package')\n")
    result = run_exit(
        exit_shell,
        prefix=f"export PATH={shlex.quote(str(shadow))}\nexport PYTHONPATH={shlex.quote(str(shadow))}",
    )
    assert result.returncode == 0, result.stderr
    assert state.cause(registry.load_registry()["sessions"][0]) == "manual_exit"


@pytest.mark.parametrize("failure", ["exit", "hang", "missing"])
def test_given_permanent_failure_when_deadline_expires_then_generation_is_denied_without_respawn(exit_shell, failure):
    _, _, _, attempts, generation = exit_shell
    program = (
        f"from pathlib import Path\np=Path({str(attempts)!r})\np.write_text(p.read_text()+'x' if p.exists() else 'x')\n"
    )
    program += "import time\ntime.sleep(60)\n" if failure == "hang" else "raise SystemExit(1)\n"
    if failure == "missing":
        program = (
            "from pathlib import Path\np=Path(__file__)\nassert p.name == 'endpoint.py'\n"
            "assert 'p.unlink()' in p.read_text()\np.unlink()\nraise SystemExit(1)\n"
        )
    started = time.monotonic()
    result = run_exit(exit_shell, program, suffix="printf agent-started")
    assert time.monotonic() - started < scaled(6)
    assert result.returncode == 77, result.stderr
    assert "agent-started" not in result.stdout
    safety = json.loads(state.tombstone_path("c-session-1", "host-a").read_text())
    assert safety["deny_boot"] and safety["generation"] == generation
    assert "persistence failed" in result.stderr.lower()
    if failure == "hang":
        assert len(attempts.read_text()) > 1  # The per-command timeout permits another attempt.


def test_given_transient_failure_when_cli_recovers_then_exit_persists_without_safety_file(exit_shell):
    _, _, _, attempts, _ = exit_shell
    config.load_config()["session_relaunch"]["exit_persistence"].update(
        deadline_seconds=scaled(6), command_timeout_seconds=scaled(3)
    )
    # Fail two sequence calls, then use the real pinned installation and storage.
    program = (
        f"from pathlib import Path\nimport sys\nsys.path.insert(0, {str(Path(__file__).resolve().parents[1] / 'src')!r})\n"
        f"p=Path({str(attempts)!r})\nn=int(p.read_text())+1 if p.exists() else 1\np.write_text(str(n))\n"
        "if n < 3: raise SystemExit(1)\nfrom ai_cli.main import cli\ncli()\n"
    )
    result = run_exit(exit_shell, program)
    assert result.returncode == 0, result.stderr
    assert int(attempts.read_text()) >= 4
    assert state.cause(registry.load_registry()["sessions"][0]) == "manual_exit"
    assert not state.tombstone_path("c-session-1", "host-a").exists()


@pytest.mark.parametrize("requested_signal", [signal.SIGINT, signal.SIGTERM])
def test_given_hung_cli_when_signal_arrives_then_exit_is_prompt_and_safety_is_persisted(exit_shell, requested_signal):
    shell, env, render, attempts, generation = exit_shell
    script = render(f"from pathlib import Path\nimport time\nPath({str(attempts)!r}).touch()\ntime.sleep(60)\n")
    proc = subprocess.Popen([shell, str(script)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + scaled(5)
        while not attempts.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert attempts.exists()
        started = time.monotonic()
        proc.send_signal(requested_signal)
        _, stderr = proc.communicate(timeout=scaled(3))
        assert time.monotonic() - started < scaled(1.5)
        assert proc.returncode == 77, stderr
        assert json.loads(state.tombstone_path("c-session-1", "host-a").read_text())["generation"] == generation
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=scaled(3))


def test_given_failed_fallback_storage_when_retries_end_then_failure_is_reported_and_no_agent_starts(exit_shell):
    safety_dir = state.tombstone_path("c-session-1", "host-a").parent
    assert not safety_dir.exists()
    safety_dir.write_text("storage unavailable")
    result = run_exit(exit_shell, "raise SystemExit(1)\n", suffix="printf agent-started")
    assert result.returncode == 77
    assert "agent-started" not in result.stdout
    assert "persistence failed" in result.stderr.lower()
    assert "safety" in result.stderr.lower() and "failed" in result.stderr.lower()


@pytest.mark.parametrize("failure", ["exit", "hang"])
def test_given_failing_mark_when_sequence_succeeds_then_mark_is_bounded_and_exit_is_denied(exit_shell, failure):
    _, _, _, attempts, generation = exit_shell
    program = (
        "import sys, time\nfrom pathlib import Path\n"
        "if sys.argv[2] == '_sequence':\n print(time.monotonic_ns())\n raise SystemExit(0)\n"
        f"p=Path({str(attempts)!r})\np.write_text(p.read_text()+'x' if p.exists() else 'x')\n"
        + ("time.sleep(60)\n" if failure == "hang" else "raise SystemExit(1)\n")
    )
    result = run_exit(exit_shell, program, suffix="printf agent-started")
    assert result.returncode == 77
    assert "agent-started" not in result.stdout
    assert len(attempts.read_text()) > 1
    assert json.loads(state.tombstone_path("c-session-1", "host-a").read_text())["generation"] == generation


@pytest.mark.parametrize("requested_signal", [signal.SIGINT, signal.SIGTERM])
def test_given_long_backoff_when_signal_arrives_then_retry_sleep_is_interrupted(exit_shell, requested_signal):
    shell, env, render, attempts, _ = exit_shell
    config.load_config()["session_relaunch"]["exit_persistence"].update(
        deadline_seconds=15, backoff_seconds=10, max_backoff_seconds=10
    )
    script = render(f"from pathlib import Path\nPath({str(attempts)!r}).touch()\nraise SystemExit(1)\n")
    process = subprocess.Popen([shell, str(script)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + scaled(5)
        while not attempts.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert attempts.exists()
        time.sleep(0.1)
        process.send_signal(requested_signal)
        _, stderr = process.communicate(timeout=scaled(3))
        assert process.returncode == 77, stderr
        assert state.tombstone_path("c-session-1", "host-a").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=scaled(3))


@pytest.mark.parametrize("signal_name", ["INT", "TERM"])
def test_given_existing_signal_trap_when_exit_persists_then_original_trap_is_restored(exit_shell, signal_name):
    result = run_exit(
        exit_shell,
        prefix=f"trap 'printf original-handler' {signal_name}",
        suffix=f"kill -{signal_name} $$",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "original-handler"


def test_given_project_state_override_when_exit_persists_then_original_registry_receives_the_exit(exit_shell, tmp_path):
    redirected = tmp_path / "project-state"
    result = run_exit(exit_shell, prefix=f"export XDG_STATE_HOME={shlex.quote(str(redirected))}")
    assert result.returncode == 0, result.stderr
    assert state.cause(registry.load_registry()["sessions"][0]) == "manual_exit"
    assert not redirected.exists()


def test_given_invalid_cli_output_when_sequence_is_read_then_retries_are_bounded_and_safety_is_persisted(exit_shell):
    result = run_exit(exit_shell, "import os\nos.write(1, bytes([255]))\n")
    assert result.returncode == 77
    assert state.tombstone_path("c-session-1", "host-a").exists()
    assert "persistence failed" in result.stderr.lower()


def test_given_cli_exiting_as_its_timeout_fires_when_group_kill_is_refused_then_safety_is_persisted(monkeypatch):
    # macOS refuses killpg with EPERM once every process in the group is a zombie, which is
    # where a CLI lands when it exits between the last poll and its per-command timeout.
    name = "c-session-1"
    state.register(name, recipe={"argv": ["ai", "c", "1"], "cwd": "/"}, host="host-a")
    generation = state.begin(name)

    def darwin_killpg(pgid, _sig):
        os.waitid(os.P_PID, pgid, os.WEXITED | os.WNOWAIT)
        raise PermissionError(errno.EPERM, os.strerror(errno.EPERM))

    monkeypatch.setattr(os, "killpg", darwin_killpg)
    context = {
        "command": [sys.executable, "-I", "-S", "-c", "raise SystemExit(1)"],
        "registry_path": registry.registry_path().as_posix(),
        "safety_path": state.tombstone_path(name, "host-a").as_posix(),
        "policy": {
            "deadline_seconds": 0.5,
            "command_timeout_seconds": 0.001,
            "backoff_seconds": 0.01,
            "max_backoff_seconds": 0.02,
        },
    }
    persisted = session_lifecycle.transition(context, name, generation, "exited", "manual_exit", "{}")
    assert persisted is False
    safety = json.loads(state.tombstone_path(name, "host-a").read_text())
    assert safety["deny_boot"] and safety["generation"] == generation
