"""Pane lookups name one session exactly, never a window that shares its name.

On tmux 3.7 a bare ``=name`` target for ``list-panes`` resolves a window called ``name``
in another session when no session of that name exists, so a lookup for a session that
has gone answers for a stranger. ``=name:`` is the exact-session form. These cases run a
real tmux server on the per-test socket directory that ``conftest`` isolates.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from ai_cli import main as ai_main
from ai_cli import session_commands

pytestmark = [
    pytest.mark.real_tmux,
    pytest.mark.skipif(os.name != "posix" or shutil.which("tmux") is None, reason="needs a real POSIX tmux"),
]

ABSENT_SESSION = "slot-1"
OTHER_SESSION = "slot-1x"


@pytest.fixture
def other_session_with_a_window_named_like_the_absent_session():
    """A live session ``slot-1x`` whose only window is called ``slot-1``; no session ``slot-1`` exists."""
    tmux = shutil.which("tmux")
    subprocess.run(
        [tmux, "new-session", "-d", "-s", OTHER_SESSION, "-n", ABSENT_SESSION, "sleep 300"],
        check=True,
        capture_output=True,
    )
    try:
        yield
    finally:
        subprocess.run([tmux, "kill-server"], check=False, capture_output=True)


@pytest.mark.parametrize(
    ("session", "expected"),
    [(ABSENT_SESSION, False), (OTHER_SESSION, True)],
    ids=["absent-session-named-like-a-window", "the-live-session-itself"],
)
def test_given_a_window_named_like_an_absent_session_when_its_live_pane_is_checked_then_only_the_exact_session_answers(
    other_session_with_a_window_named_like_the_absent_session, session, expected
):
    assert ai_main._tmux_session_has_live_pane(session) is expected


@pytest.mark.parametrize(
    ("session", "expect_pids"),
    [(ABSENT_SESSION, False), (OTHER_SESSION, True)],
    ids=["absent-session-named-like-a-window", "the-live-session-itself"],
)
def test_given_a_window_named_like_an_absent_session_when_its_pane_pids_are_read_then_only_the_exact_session_answers(
    other_session_with_a_window_named_like_the_absent_session, session, expect_pids
):
    assert bool(session_commands._pane_pids(session)) is expect_pids
