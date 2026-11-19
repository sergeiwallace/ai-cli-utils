"""Behavior tests for the shared Copier cache lock."""

import subprocess
import sys
import time
from contextlib import ExitStack

import pytest


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    cache = tmp_path / "copier-cache"
    monkeypatch.setenv("COPIER_CACHE_DIR", str(cache))
    monkeypatch.setenv("AI_CLI_COPIER_MIRROR_LOCK_TIMEOUT", "10")
    return cache


@pytest.fixture
def mirror_lock():
    from ai_cli.copier_mirror_lock import copier_mirror_lock

    return copier_mirror_lock


def test_given_held_lock_when_another_process_waits_then_it_enters_after_release(tmp_path, cache_dir, mirror_lock):
    events = tmp_path / "events"
    output = tmp_path / "output"
    command = [
        sys.executable,
        "-u",
        "-c",
        """
import sys
from pathlib import Path
from ai_cli.copier_mirror_lock import copier_mirror_lock

with copier_mirror_lock():
    with Path(sys.argv[1]).open('a') as events:
        events.write('second entered\\n')
""",
        str(events),
    ]
    with output.open("w") as log, ExitStack() as processes:
        with mirror_lock():
            events.write_text("first entered\n")
            waiter = processes.enter_context(subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT))
            deadline = time.monotonic() + 10
            # The wait notice is the barrier: it confirms that acquisition was attempted.
            while "Waiting for Copier mirror lock" not in output.read_text():
                assert waiter.poll() is None, output.read_text()
                assert time.monotonic() < deadline, "contender never reported waiting"
                time.sleep(0.01)
            assert events.read_text().splitlines() == ["first entered"]
            with events.open("a") as log_events:
                log_events.write("first leaving\n")
        assert waiter.wait(timeout=10) == 0, output.read_text()

    assert events.read_text().splitlines() == ["first entered", "first leaving", "second entered"]
    waiting = output.read_text()
    assert waiting.count("Waiting for Copier mirror lock") == 1
    assert str(cache_dir / "copier-mirror.lock") in waiting


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_given_body_exception_when_the_lock_exits_then_it_can_be_acquired_again(
    error, cache_dir, mirror_lock, monkeypatch
):
    monkeypatch.setenv("AI_CLI_COPIER_MIRROR_LOCK_TIMEOUT", "0.01")
    with pytest.raises(error), mirror_lock():
        raise error("body failed")

    with mirror_lock():
        assert (cache_dir / "copier-mirror.lock").is_file()


def test_given_other_holder_when_wait_times_out_then_error_names_lock_and_body_is_skipped(
    cache_dir, mirror_lock, monkeypatch
):
    from ai_cli.copier_mirror_lock import CopierMirrorLockError

    monkeypatch.setenv("AI_CLI_COPIER_MIRROR_LOCK_TIMEOUT", "0.01")
    with mirror_lock(), pytest.raises(CopierMirrorLockError, match="Timed out") as caught, mirror_lock():
        pytest.fail("a timed-out waiter entered the body")

    assert str(cache_dir / "copier-mirror.lock") in str(caught.value)


def test_given_cache_override_when_locked_then_lock_is_next_to_the_mirrors(cache_dir, mirror_lock):
    mirror = cache_dir / "template.git"
    mirror.mkdir(parents=True)

    with mirror_lock():
        assert (cache_dir / "copier-mirror.lock").is_file()
        assert list(mirror.iterdir()) == []


@pytest.mark.skipif(sys.platform == "win32", reason="Windows uses its Local AppData known folder")
def test_given_xdg_cache_home_when_locked_then_lock_uses_copier_default_root(tmp_path, mirror_lock, monkeypatch):
    monkeypatch.delenv("COPIER_CACHE_DIR", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    with mirror_lock():
        assert (tmp_path / "copier" / "git" / "copier-mirror.lock").is_file()


def test_given_holder_exits_abruptly_when_reacquiring_then_the_os_has_released_the_lock(
    cache_dir, mirror_lock, monkeypatch
):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os
from ai_cli.copier_mirror_lock import copier_mirror_lock

with copier_mirror_lock():
    os._exit(7)
""",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 7, result.stderr

    monkeypatch.setenv("AI_CLI_COPIER_MIRROR_LOCK_TIMEOUT", "0.01")
    with mirror_lock():
        assert (cache_dir / "copier-mirror.lock").is_file()
