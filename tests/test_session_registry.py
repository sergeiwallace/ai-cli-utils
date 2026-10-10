"""The iTerm2 session registry: launch records, liveness, refresh (AIH-krf96 T-1.2..T-1.4)."""

import json
import os
import stat
import subprocess
import sys
import textwrap

import pytest

from ai_cli import session_registry
from ai_cli.iterm2 import _PERSISTENCE_DEFAULTS


@pytest.fixture
def state_home(tmp_path, monkeypatch):
    """A per-test XDG state home that does not exist until something creates it."""
    home = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(home))
    return home


def _registry_file(state_home):
    return state_home / "ai-cli-utils" / "iterm2-sessions.json"


def _sessions(state_home):
    return json.loads(_registry_file(state_home).read_text(encoding="utf-8"))["sessions"]


_CHILD_WRITER = textwrap.dedent(
    """
    import sys
    from ai_cli import session_registry
    from ai_cli.iterm2 import _PERSISTENCE_DEFAULTS
    worker = sys.argv[1]
    for i in range(int(sys.argv[2])):
        session_registry.record_launch(
            kind="local", name=f"c-myproject-{worker}-{i}", relaunch_argv=["ai", "c", worker],
            cwd=None, remote=None, tty="", iterm_session_id=None, launcher_pid=0,
            config=_PERSISTENCE_DEFAULTS,
        )
    """
)


def test_given_a_launch_mid_write_when_a_second_launch_records_then_both_records_survive(state_home, monkeypatch):
    """The second launch (a separate process) starts after the first has read the registry
    and before it writes. Without the lock it writes in that gap and the first launch's
    stale copy then overwrites it; with the lock it waits and re-reads."""
    env = {**os.environ, "XDG_STATE_HOME": str(state_home)}
    real_write = session_registry._write
    second: list[subprocess.Popen] = []

    def write_after_second_launch_started(path, doc):
        second.append(subprocess.Popen([sys.executable, "-c", _CHILD_WRITER, "second", "1"], env=env))
        try:
            second[0].wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass  # blocked on the lock, as it should be
        real_write(path, doc)

    monkeypatch.setattr(session_registry, "_write", write_after_second_launch_started)
    session_registry.record_launch(
        kind="local",
        name="c-myproject-first",
        relaunch_argv=["ai", "c", "first"],
        cwd=None,
        remote=None,
        tty="",
        iterm_session_id=None,
        launcher_pid=0,
        config=_PERSISTENCE_DEFAULTS,
    )
    assert second[0].wait(timeout=60) == 0

    assert {record["name"] for record in _sessions(state_home)} == {"c-myproject-first", "c-myproject-second-0"}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_given_a_recorded_launch_when_written_then_file_is_owner_only(state_home):
    session_registry.record_launch(
        kind="local",
        name="c-myproject-1",
        relaunch_argv=["ai", "c", "1"],
        cwd=None,
        remote=None,
        tty="",
        iterm_session_id=None,
        launcher_pid=0,
        config=_PERSISTENCE_DEFAULTS,
    )

    assert stat.S_IMODE(_registry_file(state_home).stat().st_mode) == 0o600
    assert [p.name for p in _registry_file(state_home).parent.iterdir() if p.name.endswith(".tmp")] == []
