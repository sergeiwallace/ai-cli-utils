"""The restart-supervisor fixture's session wrapper must end with the test that started it.

The fixture runs the generated session wrapper in a session of its own. Its ``finally:``
cannot clean that up when the test process dies (a killed run, or an xdist worker the
memory guard kills), and the wrapper loops for good once nothing stops it.
"""

from __future__ import annotations

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
from process_wait import process_wait_seconds

pytestmark = [
    pytest.mark.real_tmux,  # Allow the fixture executable named tmux; no tmux server is used.
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX supervisor fixture"),
]

_WAIT = process_wait_seconds()


def _wait(predicate, seconds=_WAIT):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _alive(process):
    with contextlib.suppress(psutil.Error):
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    return False


def _identified(pairs):
    found = []
    for pid, started in pairs:
        with contextlib.suppress(psutil.NoSuchProcess):
            process = psutil.Process(pid)
            if process.create_time() == started:
                found.append(process)
    return found


def _carrying(text):
    """Every live process whose command line names ``text``: the issue's own acceptance probe."""
    found = []
    for process in psutil.process_iter():
        with contextlib.suppress(psutil.Error):
            if _alive(process) and any(text in arg for arg in process.cmdline()):
                found.append(process)
    return found


def _describe(processes):
    lines = []
    for process in processes:
        with contextlib.suppress(psutil.Error):
            if _alive(process):
                lines.append(
                    f"pid={process.pid} ppid={process.ppid()} pgid={os.getpgid(process.pid)} "
                    f"sid={os.getsid(process.pid)} cmdline={process.cmdline()}"
                )
    return lines


@pytest.mark.parametrize("outcome", ["pass", "fail", "kill", "worker-kill", "watcher-kill", "long-lifetime"])
def test_given_a_running_supervisor_fixture_when_its_test_ends_then_no_wrapper_process_survives(tmp_path, outcome):
    lifetime_test = outcome in {"watcher-kill", "long-lifetime"}
    lifetime = process_wait_seconds(8)
    ready = tmp_path / "ready.json"
    proceed = tmp_path / "proceed"
    inner = tmp_path / "test_inner.py"
    inner.write_text(
        textwrap.dedent(f"""
        import json, os, time
        from pathlib import Path
        import psutil
        import pytest
        from test_restart_supervisor import supervisor_fixture

        @pytest.mark.real_tmux
        @pytest.mark.parametrize("supervisor_fixture", [{("current" if lifetime_test else None)!r}], indirect=True)
        def test_inner(supervisor_fixture):
            root, script, supervisor = supervisor_fixture
            launches = root / "launches"
            while not launches.exists() or not launches.read_text(encoding="utf-8").endswith("\\n"):
                assert supervisor.poll() is None, "the fixture wrapper exited before its agent started"
                time.sleep(0.05)
            agent = json.loads(launches.read_text(encoding="utf-8").splitlines()[0])["pid"]
            members = []
            for process in psutil.process_iter():
                try:
                    if os.getsid(process.pid) == supervisor.pid:
                        assert os.getpgid(process.pid) == supervisor.pid, "a fixture process left the group"
                        members.append([process.pid, process.create_time()])
                except (OSError, psutil.Error):
                    pass
            assert {{supervisor.pid, agent}} <= {{pid for pid, _ in members}}
            watchers = []
            if {lifetime_test!r}:
                for child in psutil.Process().children():
                    if child.cmdline()[2:] == ["--session", str(supervisor.pid)]:
                        watchers.append(child.pid)
                assert len(watchers) == 1
            staged = Path({str(ready)!r} + ".tmp")
            staged.write_text(json.dumps({{"worker": os.getpid(), "root": str(root), "members": members,
                                          "supervisor": supervisor.pid, "watchers": watchers}}))
            os.replace(staged, {str(ready)!r})
            while not Path({str(proceed)!r}).exists():
                time.sleep(0.01)
            if {outcome!r} == "fail":
                assert False, "deliberate failure"
            if {outcome!r} != "pass":
                time.sleep(300)
        """),
        encoding="utf-8",
    )
    tests_dir = Path(__file__).resolve().parent
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(tests_dir), str(tests_dir.parent / "src")))}
    if lifetime_test:
        env["RESTART_FIXTURE_LIFETIME_SECONDS"] = str(lifetime if outcome == "watcher-kill" else 3 * lifetime + 10)
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
        "--basetemp",
        str(tmp_path / "inner-basetemp"),
        "-q",
        str(inner),
    ]
    if outcome.startswith("worker-"):
        command.extend(["-n", "1", "--max-worker-restart=0"])
    members = []
    supervisor_pid = None
    root = None
    with (tmp_path / "output").open("w+") as output:
        runner = subprocess.Popen(command, env=env, stdout=output, stderr=subprocess.STDOUT)
        try:
            assert _wait(lambda: ready.exists() or runner.poll() is not None, 3 * _WAIT), Path(output.name).read_text(
                encoding="utf-8"
            )
            assert ready.exists(), Path(output.name).read_text(encoding="utf-8")
            record = json.loads(ready.read_text(encoding="utf-8"))
            root = record["root"]
            supervisor_pid = record["supervisor"]
            members = _identified(record["members"])
            assert len(members) >= 2, "the inner test did not see the wrapper and its child"
            if lifetime_test:
                watcher = psutil.Process(record["watchers"][0])
                watcher.send_signal(signal.SIGKILL)
                assert _wait(lambda: not _alive(watcher))
                assert any(_alive(p) for p in members), "killing the watcher also ended its payload"
            proceed.touch()
            if outcome.endswith("kill") or lifetime_test:
                target = psutil.Process(record["worker"] if outcome.startswith("worker-") else runner.pid)
                target.send_signal(signal.SIGKILL)
            result = runner.wait(timeout=_WAIT)
            assert result == 0 if outcome == "pass" else result != 0
            if lifetime_test:
                assert result == -signal.SIGKILL

            def assert_gone():
                assert _wait(
                    lambda: not any(_alive(p) for p in members) and not _carrying(root),
                    lifetime + 2 if lifetime_test else _WAIT,
                ), (
                    "the supervisor fixture left its wrapper running",
                    _describe([*members, *_carrying(root)]),
                )

            if outcome == "long-lifetime":
                with pytest.raises(AssertionError, match="the supervisor fixture left its wrapper running"):
                    assert_gone()
            else:
                assert_gone()
        finally:
            if runner.poll() is None:
                runner.kill()
                runner.wait(timeout=_WAIT)
            # The fixture has no terminal or reaper heartbeat, so its entire payload
            # stays in the supervisor's group, including children launched after ready.
            if any(_alive(p) for p in members):
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(supervisor_pid, signal.SIGKILL)
            for process in members:
                with contextlib.suppress(psutil.Error):
                    if _alive(process):
                        process.kill()
