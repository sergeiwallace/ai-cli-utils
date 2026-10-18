"""No test may reach the tmux server the run was started under (AI-CLI-9h1c).

A test path that slips past its patches resolves tmux's DEFAULT socket, which is the
operator's live server. The spawn guard lets read-only queries through by design, so it
never stopped that: a chief-of-staff test read the live server's version through the
tmux_setup probe. The root conftest now gives every test its own ``TMUX_TMPDIR`` and
unsets ``TMUX``, and these tests drive a nested pytest run against a private server that
stands in for the operator's, so the operator's own server is never touched.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import TMUX_ISOLATION_PARENT, TMUX_ISOLATION_PREFIX
from process_ownership import watch_tmux_directory

pytestmark = [
    pytest.mark.real_tmux,
    pytest.mark.skipif(os.name != "posix", reason="these tests need AF_UNIX tmux sockets"),
]

_TESTS_DIR = Path(__file__).resolve().parent

_WAIT_SECONDS = float(os.environ.get("AI_CLI_TEST_PROCESS_WAIT_SECONDS", "60"))


def _tmux(socket: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["tmux", "-S", str(socket), *args], capture_output=True, text=True, check=False)


def _default_socket(tmux_tmpdir: Path) -> Path:
    """Where tmux puts the default server for ``tmux_tmpdir``: ``<dir>/tmux-<uid>/default``."""
    return tmux_tmpdir / f"tmux-{os.getuid()}" / "default"


def _start_server(socket: Path) -> None:
    socket.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    created = _tmux(socket, "new-session", "-d", "-s", "stand-in", "sleep", "300")
    if created.returncode != 0:
        pytest.fail(f"could not start a private tmux server: {created.stderr.strip()}")


def _server_alive(socket: Path) -> bool:
    return _tmux(socket, "list-sessions").returncode == 0


def _run_inner_suite(tmp_path: Path, body: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run ``body`` as one test file under this suite's own conftest, with ``env``."""
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "test_inner.py").write_text(textwrap.dedent(body))
    env = {**env, "PYTHONPATH": os.pathsep.join([str(_TESTS_DIR), env.get("PYTHONPATH", "")])}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "conftest",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
            "-o",
            "markers=real_tmux",
            "--rootdir",
            str(inner),
            "-q",
            str(inner / "test_inner.py"),
        ],
        cwd=inner,
        env=env,
        capture_output=True,
        text=True,
        timeout=_WAIT_SECONDS * 3,
        check=False,
    )


@pytest.fixture
def stand_in_server() -> Iterator[Path]:
    """A private tmux server on the default socket of a fresh ``TMUX_TMPDIR``.

    It stands in for the operator's live server: an inner run launched with this
    ``TMUX_TMPDIR`` would find it with a bare ``tmux`` call, exactly as an unisolated run
    finds the real one.
    """
    if shutil.which("tmux") is None:
        pytest.fail("tmux is a hard requirement of this suite")
    tmux_tmpdir = Path(tempfile.mkdtemp(prefix="ai-cli-standin-", dir=TMUX_ISOLATION_PARENT))
    socket = _default_socket(tmux_tmpdir)
    with watch_tmux_directory(tmux_tmpdir):
        _start_server(socket)
        yield tmux_tmpdir


def _env_launched_under(tmux_tmpdir: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in {"TMUX", "TMUX_PANE"}}
    env["TMUX_TMPDIR"] = str(tmux_tmpdir)
    return env


@pytest.mark.parametrize("boundary", ["run", "Popen"])
def test_given_monkeypatched_process_boundary_when_next_test_runs_then_guard_allowance_is_current(tmp_path, boundary):
    body = f"""
        import subprocess
        from unittest.mock import Mock

        import pytest

        def test_patch_boundary(monkeypatch):
            monkeypatch.setattr(subprocess, {boundary!r}, Mock())

        @pytest.mark.real_tmux
        def test_allow_private_tmux(tmp_path):
            result = subprocess.run(
                ["tmux", "-S", str(tmp_path / "absent"), "kill-server"],
                capture_output=True, text=True, timeout=10,
            )
            assert result.returncode != 0

        def test_refuse_unmocked_launch():
            with pytest.raises(RuntimeError, match="attempted to spawn a real `tmux` process"):
                subprocess.run(["tmux", "new-session", "-d", "-s", "session-1"])
        """
    result = _run_inner_suite(tmp_path, body, dict(os.environ))
    assert result.returncode == 0, result.stdout + result.stderr


def test_given_a_run_started_under_a_live_tmux_server_when_a_test_makes_a_bare_tmux_call_then_it_does_not_reach_that_server(
    tmp_path: Path, stand_in_server: Path
):
    """The AI-CLI-9h1c reproduction: the query that read the live server's version."""
    reached = tmp_path / "reached"
    body = f"""
        import subprocess
        from pathlib import Path

        def test_bare_query():
            probe = subprocess.run(
                ["tmux", "display-message", "-p", "#{{socket_path}}"], capture_output=True, text=True
            )
            Path({str(reached)!r}).write_text(f"{{probe.returncode}}\\n{{probe.stdout.strip()}}")
        """

    result = _run_inner_suite(tmp_path, body, _env_launched_under(stand_in_server))

    assert result.returncode == 0, result.stdout + result.stderr
    returncode, _, socket_path = reached.read_text().partition("\n")
    stand_in_socket = os.path.realpath(_default_socket(stand_in_server))
    assert returncode != "0" or os.path.realpath(socket_path) != stand_in_socket, (
        f"a bare tmux call inside a test reached the server the run started under ({socket_path})"
    )


def test_given_a_run_started_inside_a_tmux_pane_when_a_test_makes_a_bare_tmux_call_then_it_does_not_follow_TMUX(
    tmp_path: Path, stand_in_server: Path
):
    """``TMUX`` names a socket outright and overrides ``TMUX_TMPDIR``, so it must go too."""
    socket = _default_socket(stand_in_server)
    pane = _tmux(socket, "display-message", "-p", "-t", "stand-in", "#{socket_path},#{pid},#{session_id}")
    env = _env_launched_under(Path(tempfile.gettempdir()))
    env["TMUX"] = pane.stdout.strip()
    reached = tmp_path / "reached"
    body = f"""
        import subprocess
        from pathlib import Path

        def test_bare_query():
            probe = subprocess.run(
                ["tmux", "display-message", "-p", "#{{socket_path}}"], capture_output=True, text=True
            )
            Path({str(reached)!r}).write_text(f"{{probe.returncode}}\\n{{probe.stdout.strip()}}")
        """

    result = _run_inner_suite(tmp_path, body, env)

    assert result.returncode == 0, result.stdout + result.stderr
    returncode, _, socket_path = reached.read_text().partition("\n")
    assert returncode != "0" or os.path.realpath(socket_path) != os.path.realpath(socket)


def test_given_a_test_starts_a_server_on_its_default_socket_when_the_test_ends_then_the_server_is_gone(
    tmp_path: Path, stand_in_server: Path
):
    """A per-test ``TMUX_TMPDIR`` must not trade a live-server leak for orphan servers."""
    recorded = tmp_path / "tmux_tmpdir"
    body = f"""
        import os
        import subprocess
        from pathlib import Path

        import pytest

        @pytest.mark.real_tmux
        def test_starts_a_default_server():
            Path({str(recorded)!r}).write_text(os.environ["TMUX_TMPDIR"])
            started = subprocess.run(["tmux", "new-session", "-d", "-s", "leaked", "sleep", "300"])
            assert started.returncode == 0
        """

    result = _run_inner_suite(tmp_path, body, _env_launched_under(stand_in_server))

    assert result.returncode == 0, result.stdout + result.stderr
    private = Path(recorded.read_text())
    assert private != stand_in_server
    assert not _server_alive(_default_socket(private))
    assert not private.exists()
    assert _server_alive(_default_socket(stand_in_server))


def _dead_pid() -> int:
    finished = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True, check=True
    )
    return int(finished.stdout)


def test_given_a_killed_run_left_a_server_in_its_isolation_dir_when_the_next_run_starts_then_the_server_is_killed(
    tmp_path: Path, stand_in_server: Path
):
    """A killed run skips every teardown, so the next run reaps what it left behind."""
    orphan_dir = Path(tempfile.mkdtemp(prefix=f"{TMUX_ISOLATION_PREFIX}{_dead_pid()}-", dir=TMUX_ISOLATION_PARENT))
    live_dir = Path(tempfile.mkdtemp(prefix=f"{TMUX_ISOLATION_PREFIX}{os.getpid()}-", dir=TMUX_ISOLATION_PARENT))
    with watch_tmux_directory(orphan_dir), watch_tmux_directory(live_dir):
        _start_server(_default_socket(orphan_dir))
        _start_server(_default_socket(live_dir))
        body = """
            def test_nothing():
                pass
            """

        result = _run_inner_suite(tmp_path, body, _env_launched_under(stand_in_server))

        assert result.returncode == 0, result.stdout + result.stderr
        deadline = time.monotonic() + _WAIT_SECONDS
        while _server_alive(_default_socket(orphan_dir)) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _server_alive(_default_socket(orphan_dir))
        assert not orphan_dir.exists()
        assert _server_alive(_default_socket(live_dir)), "a live run's private server must be left alone"
