"""Private tmux trees must end even when pytest cannot run its finalizers."""

import contextlib
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import psutil
import pytest
from test_tmux_default_server_isolation import stand_in_server as sibling_server

stand_in_server = sibling_server

pytestmark = [pytest.mark.real_tmux, pytest.mark.skipif(os.name != "posix", reason="tmux requires POSIX")]

_WAIT = float(os.environ.get("AI_CLI_TEST_PROCESS_WAIT_SECONDS", "60"))


def _wait(predicate):
    deadline = time.monotonic() + _WAIT
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _alive(process):
    with contextlib.suppress(psutil.Error):
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    return False


@pytest.mark.parametrize(
    ("fixture_module", "fixture_name", "outcome"),
    [
        ("test_session_recovery_shell", "tmux_socket", outcome)
        for outcome in ["pass", "fail", "term", "kill", "worker-term", "worker-kill"]
    ]
    + [
        (module, fixture, "kill")
        for module, fixture in [
            ("test_ctrl_z_suspend", "tmux_socket"),
            ("test_session_reload_loop", "tmux_socket"),
            ("test_session_title_follows_resolved_name", "tmux_socket"),
            ("test_session_launch_shell_resolution", "real_tmux_socket"),
            ("test_session_launch_integration", "tmux_server"),
            ("test_stale_session_reaper", "real_tmux_socket"),
            ("conftest", "_isolate_tmux_default_server"),
        ]
    ],
)
def test_given_a_remote_session_when_pytest_ends_then_its_entire_tree_and_socket_are_gone(
    tmp_path, stand_in_server, fixture_module, fixture_name, outcome
):
    ready = tmp_path / "ready.json"
    proceed = tmp_path / "proceed"
    inner = tmp_path / "test_inner.py"
    inner.write_text(
        textwrap.dedent(f"""
        import json, os, time
        from pathlib import Path
        import psutil
        import pytest
        from test_session_recovery_shell import _RemoteSession
        from {fixture_module} import {fixture_name} as tmux_socket

        @pytest.mark.real_tmux
        def test_remote(tmux_socket, tmp_path):
            tmux_socket = str(getattr(tmux_socket, "socket_path", tmux_socket))
            if {fixture_module!r} == "conftest":
                tmux_socket = str(Path(tmux_socket) / f"tmux-{{os.getuid()}}" / "default")
                Path(tmux_socket).parent.mkdir()
            session = _RemoteSession(tmux_socket, tmp_path, "test-session", "/bin/bash")
            session.wait_for_recovery_shell()
            import subprocess
            server = subprocess.run(
                ["tmux", "-S", tmux_socket, "display-message", "-p", "#{{pid}}"],
                capture_output=True, text=True, check=True,
            )
            root = psutil.Process(int(server.stdout))
            processes = [root, *root.children(recursive=True)]
            assert any("--ai-cli-heartbeat-ticker" in p.cmdline() for p in processes)
            Path({str(ready)!r}).write_text(json.dumps({{
                "worker": os.getpid(), "socket": tmux_socket,
                "processes": [[p.pid, p.create_time()] for p in processes],
            }}))
            while not Path({str(proceed)!r}).exists():
                time.sleep(0.01)
            if {outcome!r} == "fail":
                assert False, "deliberate failure"
            if {outcome!r} != "pass":
                time.sleep(300)
        """),
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(Path(__file__).parent), str(Path.cwd() / "src")))}
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "conftest",
        "-p",
        "no:cacheprovider",
        "-o",
        "addopts=",
        "--rootdir",
        str(tmp_path),
        "-q",
        str(inner),
    ]
    if outcome.startswith("worker-"):
        command.extend(["-n", "1", "--max-worker-restart=0"])
    processes = []
    sock = None
    with (tmp_path / "output").open("w+") as output:
        runner = subprocess.Popen(command, env=env, stdout=output, stderr=subprocess.STDOUT)
        try:
            assert _wait(lambda: ready.exists() or runner.poll() is not None)
            assert ready.exists(), "inner test did not reach its recovery shell"
            record = json.loads(ready.read_text())
            sock = Path(record["socket"])
            for pid, started in record["processes"]:
                with contextlib.suppress(psutil.NoSuchProcess):
                    process = psutil.Process(pid)
                    if process.create_time() == started:
                        processes.append(process)
            # A startup shell can exit between the snapshot and here (seen on macOS). The tmux
            # server, the first entry, must still be up; what remains is what cleanup must reap.
            processes = [p for p in processes if _alive(p)]
            assert processes and processes[0].pid == record["processes"][0][0]
            proceed.touch()
            if outcome not in {"pass", "fail"}:
                target = psutil.Process(record["worker"] if outcome.startswith("worker-") else runner.pid)
                assert Path(target.cwd()) == Path.cwd()
                target.send_signal(signal.SIGKILL if outcome.endswith("kill") else signal.SIGTERM)
            result = runner.wait(timeout=_WAIT)
            assert result == 0 if outcome == "pass" else result != 0
            assert _wait(lambda: not any(_alive(p) for p in processes)), "pytest left its tmux process tree alive"
            assert _wait(lambda: not sock.parent.exists()), "pytest left its private socket directory behind"
            sibling = stand_in_server / f"tmux-{os.getuid()}" / "default"
            assert (
                subprocess.run(["tmux", "-S", str(sibling), "has-session"], capture_output=True, check=False).returncode
                == 0
            )
        finally:
            if runner.poll() is None:
                runner.kill()
                runner.wait(timeout=_WAIT)
            # Also bound the RED run: only identities captured from our private server.
            for process in reversed(processes):
                with contextlib.suppress(psutil.Error):
                    if _alive(process):
                        process.ppid()
                        process.cwd()
                        process.kill()
            if sock is not None and sock.exists():
                assert sock.is_socket()
                sock.unlink()
            if sock is not None and sock.parent.exists():
                assert not list(sock.parent.iterdir())
                sock.parent.rmdir()
