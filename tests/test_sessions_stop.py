"""``ai sessions stop`` ends a managed session so its wrapper does not relaunch it.

A plain agent exit inside a managed session is a restart: the generated
supervisor stops only on its stop marker, exit 77, or the end of its tmux
session. The verb writes the marker for the running supervisor and sends TERM
to the session's one agent process, which is what lets a migration finish.
"""

from __future__ import annotations

import json
import sys
import time

import pytest
from conftest import run_cli
from test_restart_supervisor import _wait_for
from test_restart_supervisor import supervisor_fixture as supervisor_fixture

from ai_cli.process_probe import probe_for

NAME = "myproject-box-1"


def _launches(root):
    path = root / "launches"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _list_slot_while_supervisor_runs(tmux):
    """Make the fixture tmux list the session and its pane while the supervisor runs, as tmux does."""
    tmux.write_text(
        tmux.read_text(encoding="utf-8")
        + """
if args[0] in ('list-sessions', 'list-panes'):
    from ai_cli.process_probe import probe_for
    supervisor = int((root / 'supervisor-pid').read_text(encoding='utf-8'))
    if probe_for().has_ended(supervisor):
        sys.exit(1)
    print('c-myproject-box-1' if args[0] == 'list-sessions' else supervisor)
""",
        encoding="utf-8",
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX supervisor fixture")
@pytest.mark.real_tmux
@pytest.mark.parametrize("supervisor_fixture", ["current"], indirect=True)
def test_given_a_running_managed_session_when_stopped_then_its_agent_gets_term_and_is_not_relaunched(
    supervisor_fixture,
):
    root, _script, supervisor = supervisor_fixture
    _list_slot_while_supervisor_runs(root / "bin" / "tmux")
    agent = _wait_for(lambda: _launches(root), supervisor)[0]
    # An agent that exits within 3 s ends the session on its own ("exited too
    # quickly"), which would pass this test without the stop marker.
    started = time.monotonic()
    _wait_for(lambda: time.monotonic() - started >= 4, supervisor)

    code, out, err = run_cli(["ai", "sessions", "stop", NAME, "-t", "60"])

    assert code == 0, out + err
    assert f"pid {agent['pid']}" in out and "stopped" in out
    assert supervisor.wait(timeout=30) == 0
    assert probe_for().has_ended(agent["pid"])
    assert len(_launches(root)) == 1


def test_given_nothing_running_for_the_name_when_stopped_then_it_says_so_and_exits_cleanly(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr("ai_cli.session._tmux_session_names", list)

    code, out, err = run_cli(["ai", "sessions", "stop", NAME])

    assert code == 0, out + err
    assert f"nothing is running for {NAME}" in out
    assert not (tmp_path / "state").exists()


def test_given_a_name_that_is_not_a_session_name_when_stopped_then_it_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    code, _out, err = run_cli(["ai", "sessions", "stop", "../escape"])

    assert code == 1
    assert "invalid session name" in err
    assert not (tmp_path / "state").exists()
