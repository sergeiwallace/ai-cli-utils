"""``sync._cc_session_state_for_worktree`` finds the session that runs in a worktree.

A session's worktree directory carries its title, and its tmux session is the title
with the engine prefix. The lookup used to rebuild the name as ``c-<project>-<N>``,
the form names had before the host alias joined them, so it never found a session.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from ai_cli import sync


@pytest.mark.parametrize("title", ["myproject-box-3", "myproject-box-review-ada"])
def test_given_a_live_session_in_the_worktree_when_its_state_is_read_then_it_is_found(tmp_path, monkeypatch, title):
    worktree = tmp_path / ".worktrees" / title
    worktree.mkdir(parents=True)
    asked: list[list[str]] = []

    def fake_run(command, *args, **kwargs):
        asked.append(list(command))
        if command[:2] == ["tmux", "has-session"]:
            return SimpleNamespace(returncode=0 if command[-1] == f"=c-{title}" else 1, stdout="", stderr="")
        if command[:2] == ["tmux", "list-panes"]:
            return SimpleNamespace(returncode=0, stdout="4242\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert sync._cc_session_state_for_worktree("myproject", worktree) == "idle"
    assert ["tmux", "has-session", "-t", f"=c-{title}"] in asked
