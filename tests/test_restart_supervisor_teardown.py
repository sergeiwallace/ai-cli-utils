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


@pytest.mark.parametrize("outcome", ["pass", "fail", "kill", "worker-kill"])
def test_given_a_running_supervisor_fixture_when_its_test_ends_then_no_wrapper_process_survives(tmp_path, outcome):
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
        def test_inner(supervisor_fixture):
            root, script, supervisor = supervisor_fixture
            launches = root / "launches"
            while not launches.exists():
                assert supervisor.poll() is None, "the fixture wrapper exited before its agent started"
                time.sleep(0.05)
            members = []
            for process in psutil.process_iter():
                try:
                    if os.getsid(process.pid) == supervisor.pid:
                        members.append([process.pid, process.create_time()])
                except (OSError, psutil.Error):
                    pass
            staged = Path({str(ready)!r} + ".tmp")
            staged.write_text(json.dumps({{"worker": os.getpid(), "root": str(root), "members": members}}))
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
            members = _identified(record["members"])
            assert members, "the inner test saw no process in the wrapper's session"
            proceed.touch()
            if outcome.endswith("kill"):
                target = psutil.Process(record["worker"] if outcome.startswith("worker-") else runner.pid)
                target.send_signal(signal.SIGKILL)
            result = runner.wait(timeout=_WAIT)
            assert result == 0 if outcome == "pass" else result != 0
            assert _wait(lambda: not any(_alive(p) for p in members) and not _carrying(root)), (
                "the supervisor fixture left its wrapper running",
                _describe([*members, *_carrying(root)]),
            )
        finally:
            if runner.poll() is None:
                runner.kill()
                runner.wait(timeout=_WAIT)
            # Bound a red run: only identities the inner test captured from the wrapper's session.
            for process in members:
                with contextlib.suppress(psutil.Error):
                    if _alive(process):
                        process.kill()
