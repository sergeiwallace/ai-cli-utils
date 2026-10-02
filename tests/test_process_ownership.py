"""The shared process-ownership helpers must really own what they spawn.

These assert against the operating system, not against the helper's own
bookkeeping: a reap that returns cleanly while leaving the process running is
exactly the failure the helpers exist to prevent, and only ``psutil`` can tell
the two apart.  The tree test is the important one — killing a direct child
without its descendants is what turns one unowned spawn into lasting orphans.
"""

from __future__ import annotations

import os
import subprocess
import sys
from time import monotonic, sleep

import psutil
import pytest
from process_ownership import (
    group_spawn_kwargs,
    owned_process,
    owned_sleeper,
    reap,
    spawn_owned,
    spawn_owned_sleeper,
)

# A child that starts a grandchild, reports the grandchild's pid, then waits. The
# grandchild is what proves a group reap: a plain `terminate()` on the child
# leaves it running and reparented.
_PARENT_OF_A_GRANDCHILD = (
    "import subprocess, sys, time\n"
    "kid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
    "print(kid.pid, flush=True)\n"
    "time.sleep(30)\n"
)


def _gone(pid: int, timeout: float = 10.0) -> bool:
    """True once *pid* is neither running nor a zombie holding its own entry.

    Polled rather than read once, because "has exited" and "is observably gone" are
    not the same instant and the gap is platform-dependent. Reading it once made this
    file's tree test intermittently red on Windows CI and nowhere else -- failing on
    main at 843811f and on two unrelated pull requests, always as a bare
    ``assert False`` on a pid whose process the reap had in fact ended.

    Two Windows mechanisms can each produce that, and this bound is correct under
    either, which is why no attempt is made to distinguish them from a host that
    cannot run Windows. A pid stays valid there while any handle to it remains open,
    and ``Popen`` holds one until it is collected, so an exited process can still
    answer ``is_running()``. And Windows recycles pids aggressively, so a freed
    number can be occupied by something new between the reap and the read.

    For a process spawned through ``Popen`` prefer ``proc.poll() is not None``, which
    is authoritative: it reports the exit status of *that* child and cannot be
    confused by another process holding the same number. This helper is for a
    grandchild, where no handle is available and a pid is all there is.
    """
    deadline = monotonic() + timeout
    while True:
        try:
            process = psutil.Process(pid)
            if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        if monotonic() >= deadline:
            return False
        sleep(0.05)


def test_given_an_owned_spawn_when_it_starts_then_it_is_really_running():
    with owned_sleeper() as proc:
        assert psutil.pid_exists(proc.pid), "the helper reported a process that never started"
        assert proc.poll() is None


def test_given_an_owned_spawn_when_reaped_then_the_process_is_gone():
    proc = spawn_owned_sleeper()
    reap(proc)

    assert proc.poll() is not None, "reap returned before the process had actually exited"
    assert _gone(proc.pid), "the reaped process is still observable as running"


def test_given_an_already_reaped_process_when_reaped_again_then_it_is_a_no_op():
    proc = spawn_owned_sleeper()
    reap(proc)
    reap(proc)

    assert _gone(proc.pid)


def test_given_a_child_with_a_grandchild_when_reaped_then_the_whole_tree_is_gone():
    """The point of owning a group: a reap must not leave descendants behind."""
    proc = spawn_owned([sys.executable, "-c", _PARENT_OF_A_GRANDCHILD], stdout=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    grandchild = int(proc.stdout.readline().strip())
    proc.stdout.close()
    assert psutil.pid_exists(grandchild), "the grandchild never started, so this proves nothing"

    reap(proc)

    # poll() for the child we own, _gone() only for the grandchild we do not: poll()
    # reports THIS child's exit status and cannot be answered by another process that
    # inherited its number, which on Windows is a real possibility rather than a
    # theoretical one.
    assert proc.poll() is not None, "reap returned before the child had actually exited"
    assert _gone(grandchild), f"the grandchild {grandchild} outlived the reap of its group"


def _raise_inside_an_owned_process(captured: list[int]) -> None:
    with owned_sleeper() as proc:
        captured.append(proc.pid)
        raise RuntimeError("deliberate")


def test_given_a_failing_body_when_the_context_exits_then_the_process_is_still_reaped():
    """Cleanup must not depend on the assertions above it having passed."""
    captured: list[int] = []
    with pytest.raises(RuntimeError, match="deliberate"):
        _raise_inside_an_owned_process(captured)

    assert _gone(captured[0])


def test_given_extra_argv_when_spawning_a_sleeper_then_the_command_line_carries_it():
    """Tests that classify a process by its argv need to be able to set one."""
    with owned_sleeper(30, "myproject-marker") as proc:
        assert "myproject-marker" in psutil.Process(proc.pid).cmdline()


def test_given_a_group_keyword_when_passed_to_spawn_owned_then_it_is_refused():
    """Silently honouring an override would put the child in the runner's group."""
    key = next(iter(group_spawn_kwargs()))
    with pytest.raises(TypeError, match="owns the process group"):
        spawn_owned([sys.executable, "-c", ""], **{key: False})


@pytest.mark.skipif(not hasattr(os, "getpgrp"), reason="process groups are POSIX-only")
def test_given_an_owned_spawn_on_posix_when_inspected_then_it_leads_its_own_group():
    """Without this the group reap would signal the test runner itself."""
    with owned_sleeper() as proc:
        assert os.getpgid(proc.pid) == proc.pid, "the child is not a process group leader"
        assert os.getpgid(proc.pid) != os.getpgrp(), "the child shares the runner's group"


def test_given_the_platform_when_group_kwargs_are_built_then_they_match_what_it_supports():
    """``start_new_session`` is ignored on Windows, so the group must come from a flag."""
    kwargs = group_spawn_kwargs()
    if sys.platform == "win32":
        assert kwargs == {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        assert kwargs == {"start_new_session": True}


def test_given_an_owned_process_context_when_the_body_returns_then_the_process_is_reaped():
    with owned_process([sys.executable, "-c", f"import time; time.sleep({30})"]) as proc:
        pid = proc.pid
        assert proc.poll() is None

    assert _gone(pid)
