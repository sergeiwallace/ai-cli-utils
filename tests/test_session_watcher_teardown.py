"""The watchers a session starts must not outlive the home they were started for."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil
import pytest
from conftest import TEST_BOOT_ID, tmux_runnable
from process_wait import scaled

from ai_cli import session_lifecycle_script
from ai_cli import session_run_state as state
from ai_cli.session_script import get_engine_script

pytestmark = [
    pytest.mark.real_tmux,
    pytest.mark.skipif(os.name != "posix", reason="session wrappers require POSIX terminals"),
    pytest.mark.timeout(120),
]

WATCHERS = (("sync", "watch"), ("memory", "watch"))

# Stands in for `ai`. Every subcommand is a no-op except the two watchers, which run the
# real ai_cli code. Only the NATS network boundary is faked: a connection that accepts
# every call, as a reachable server would, so the watcher stays up exactly as it did on
# a host with a local server.
_AI_SHIM = """\
import json, os, sys
from pathlib import Path

argv = sys.argv[1:]
if len(argv) > 1 and argv[1] == "_begin":
    print(os.environ.get("TEST_GENERATION", ""))
if tuple(argv[:2]) not in {watchers!r}:
    sys.exit(0)

import psutil
pid = os.getpid()
with open({record!r}, "a") as out:
    out.write(json.dumps({{"argv": argv, "pid": pid, "start": psutil.Process(pid).create_time()}}) + "\\n")

from unittest.mock import AsyncMock, MagicMock

import nats

jetstream = AsyncMock()
connection = MagicMock()
connection.jetstream = lambda: jetstream
connection.subscribe = AsyncMock()
connection.close = AsyncMock()


async def connect(*args, **kwargs):
    return connection


nats.connect = connect

from ai_cli.main import cli

sys.argv = ["ai", *argv]
cli()
"""


def _wait(predicate, seconds):
    deadline = time.monotonic() + scaled(seconds)
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def _still_running(entry):
    try:
        process = psutil.Process(entry["pid"])
        return process.create_time() == entry["start"] and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


@pytest.fixture
def session_home(tmp_path, monkeypatch):
    runnable, reason = tmux_runnable()
    if not runnable:
        pytest.skip(reason)
    shell = shutil.which("bash")
    if shell is None:
        pytest.skip("bash is unavailable")
    # The session records its run state through the real lifecycle CLI, which needs the
    # registered row and generation below and the boot identity pytest pins. Without them
    # the agent never starts and the session ends only when exit persistence gives up,
    # 30 s later, which raced the wait for it.
    lifecycle_command = session_lifecycle_script.lifecycle_command

    def fixed_boot_command(path):
        command = lifecycle_command(path)
        command[-1] = command[-1].replace(
            "from ai_cli.main import cli",
            f"registry.current_boot_id = lambda: {TEST_BOOT_ID!r}; from ai_cli.main import cli",
        )
        return command

    monkeypatch.setattr(session_lifecycle_script, "lifecycle_command", fixed_boot_command)
    record = tmp_path / "watchers.jsonl"
    launches = tmp_path / "launches"
    binaries = tmp_path / "bin"
    binaries.mkdir()
    ai = binaries / "ai"
    ai.write_text(f"#!{sys.executable}\n" + _AI_SHIM.format(watchers=set(WATCHERS), record=str(record)))
    (binaries / "claude").write_text(f"#!/bin/sh\necho launched >> {shlex.quote(str(launches))}\nexit 0\n")
    (binaries / "direnv").write_text("#!/bin/sh\nexit 0\n")
    # A frozen clock makes the agent's exit fall inside the startup guard however slow
    # the host, so the session stops on that first exit instead of restarting it.
    (binaries / "date").write_text(
        f'#!/bin/sh\nif [ "$1" = +%s ]; then echo 1000000000; else exec {shlex.quote(shutil.which("date"))} "$@"; fi\n'
    )
    for binary in binaries.iterdir():
        binary.chmod(0o755)
    home = tmp_path / "home"
    (home / ".claude" / "projects" / "myproject" / "memory").mkdir(parents=True)
    (home / ".claude" / "projects" / "myproject" / "memory" / "MEMORY.md").write_text("# memory\n")
    project = tmp_path / "myproject"
    project.mkdir()
    sock_dir = Path(tempfile.mkdtemp(prefix="watchers-"))
    socket = str(sock_dir / "tmux.sock")
    env = {key: value for key, value in os.environ.items() if not key.startswith(("DIRENV_", "XDG_", "TMUX"))}
    env.update(
        PATH=str(binaries) + os.pathsep + env["PATH"],
        HOME=str(home),
        SHELL=shell,
        AI_HOST="host-a",
        XDG_CONFIG_HOME=str(home / ".config"),
        XDG_STATE_HOME=str(home / ".local" / "state"),
        XDG_DATA_HOME=str(home / ".local" / "share"),
        XDG_CACHE_HOME=str(home / ".cache"),
    )

    def records():
        if not record.exists():
            return []
        return [json.loads(line) for line in record.read_text().splitlines()]

    def start():
        state.register("c-session-1", recipe={"argv": ["ai", "c", "session-1"], "cwd": str(project)}, host="host-a")
        env["TEST_GENERATION"] = state.begin("c-session-1")
        script = tmp_path / "session.sh"
        script.write_text(
            get_engine_script("c", "session-1", "c-session-1", "c-session-", "myproject", worktree_dir=str(project))
        )
        new_session = ["tmux", "-S", socket, "-f", os.devnull, "new-session", "-d", "-s", "c-session-1"]
        result = subprocess.run(
            [*new_session, "-c", str(project), shell, str(script)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr

    def tear_down():
        subprocess.run(["tmux", "-S", socket, "kill-server"], capture_output=True, check=False, timeout=10)

    def session_alive():
        result = subprocess.run(["tmux", "-S", socket, "has-session"], capture_output=True, check=False, timeout=10)
        return result.returncode == 0

    def agent_launches():
        return launches.read_text().count("launched") if launches.exists() else 0

    yield home, project, start, session_alive, tear_down, records, agent_launches
    tear_down()
    for entry in records():
        if _still_running(entry):
            os.kill(entry["pid"], 9)
    shutil.rmtree(sock_dir, ignore_errors=True)


def test_given_session_on_private_server_when_it_ends_and_its_home_is_removed_then_no_watcher_survives(session_home):
    home, project, start, session_alive, tear_down, records, agent_launches = session_home
    start()

    def both_running():
        running = {tuple(entry["argv"][:2]) for entry in records() if _still_running(entry)}
        return running == set(WATCHERS)

    assert _wait(both_running, 30), f"the session did not start both watchers: {records()}"
    pid_files = home / ".local" / "state" / "ai-cli-utils"
    assert _wait(lambda: {path.name for path in pid_files.glob("*.pid")} >= {"sync-watch.pid", "memory-watch.pid"}, 10)

    # The fake agent exits at once, so the session ends on its own, as a finished test
    # session does. Killing the server first would hang up the pane and take the watchers
    # down with it, which is not how the orphans were left behind.
    assert _wait(lambda: not session_alive(), 30), "the session did not end after its agent exited"
    assert agent_launches() == 1, f"the session ended without running its agent once: {agent_launches()} launches"
    tear_down()
    shutil.rmtree(home)
    shutil.rmtree(project)

    assert _wait(lambda: not any(_still_running(entry) for entry in records()), 15), (
        f"watchers outlived their session and home: {[e for e in records() if _still_running(e)]}"
    )
