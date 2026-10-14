"""Session-script warnings and errors reach the launch log and telemetry (AI-CLI-cuwe).

The generated session script prints every message through `ai internal emit` and its reload row
through `ai internal progress`. Those used to reach the terminal only. Now a warning or an error
is also stored in the launch log the script was generated for and in telemetry, as the same record
the terminal shows, and an info line stays on the terminal. Each test runs the script on a pty with
the `test_reload_progress` harness, so `ai internal emit` and `ai internal progress` are the real
CLI in a fresh interpreter, and HOME (where the telemetry store lives) is the test's own.
"""

from __future__ import annotations

import sys

import pytest

if sys.platform == "win32":
    pytest.skip("POSIX-only: the session supervisor is a POSIX shell script on a pty", allow_module_level=True)

import json
import re
import sqlite3
from pathlib import Path

from test_output_sinks import _event_fields, _fields
from test_reload_progress import _SGR, Session, session_factory  # noqa: F401  (the fixture)

DIRENV_TEXT = "direnv could not load"
CLEANUP_TEXT = "skipping tmux session cleanup because supervisor ownership was not established"
_SEED = "launch log started for session test-session"
# The script's own plain line when the renderer cannot run: a stamp, then `[session] <op>: ...`.
_FALLBACK = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [A-Z]{2,5} \[session\] direnv: Warning: ")


@pytest.fixture
def launch_log(tmp_path) -> Path:
    path = tmp_path / "state" / "ai-cli-utils" / "launch-logs" / "test-session.log"
    path.parent.mkdir(parents=True)
    path.write_text(f"2026-01-15 13:30:00 EST {_SEED}\n", encoding="utf-8")
    return path


def _log(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _telemetry(session: Session) -> list[tuple[str, dict]]:
    database = session.tmp_path / "home" / ".ai-cli" / "telemetry.db"
    if not database.exists():
        return []
    connection = sqlite3.connect(str(database))
    try:
        rows = connection.execute(
            "SELECT subject, data FROM events WHERE subject LIKE 'telemetry.log.%' ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    return [(subject, json.loads(data)) for subject, data in rows]


def _terminal_lines(session: Session) -> list[str]:
    # Read from the bytes, not the modelled screen: a line naming the test's worktree path wraps.
    text = _SGR.sub("", bytes(session.received).decode("utf-8", errors="replace"))
    return [line.rstrip("\r").rsplit("\r", 1)[-1] for line in text.split("\n")]


def _containing(lines: list[str], text: str) -> list[str]:
    return [line for line in lines if text in line]


def test_given_a_session_script_warning_when_emitted_then_terminal_log_and_telemetry_agree(
    session_factory,  # noqa: F811
    launch_log,
):
    session = session_factory(reload_on=(), launch_log=launch_log, direnv_fails=True)
    session.wait_for_agents(1)

    [terminal] = _containing(_terminal_lines(session), DIRENV_TEXT)
    [logged] = _containing(_log(launch_log), DIRENV_TEXT)
    [(subject, data)] = [row for row in _telemetry(session) if DIRENV_TEXT in row[1]["text"]]
    expected = _fields(terminal)
    assert expected["tag"] == "session" and expected["op"] == "direnv" and expected["level"] == "warning", terminal
    assert _fields(logged) == expected
    assert _event_fields(data) == expected
    assert subject == "telemetry.log.session"


def test_given_a_session_script_info_line_when_emitted_then_only_the_terminal_shows_it(
    session_factory,  # noqa: F811
    launch_log,
):
    session = session_factory(
        reload_on=(), agent_runs=(2.0, 600.0), launch_log=launch_log, direnv_fails=True, exit_on_cron=2
    )
    session.wait_for_exit()

    assert _containing(_terminal_lines(session), CLEANUP_TEXT), session.rows()
    lines = _log(launch_log)
    events = _telemetry(session)
    # The same session's warning was stored in both, so the sinks were reachable.
    assert _containing(lines, DIRENV_TEXT), lines
    assert [data["text"] for _subject, data in events if DIRENV_TEXT in data["text"]], events
    assert not _containing(lines, CLEANUP_TEXT), lines
    assert [data for _subject, data in events if data["level"] == "info"] == [], events


def test_given_reloads_past_both_thresholds_when_rendered_then_only_the_escalated_rows_are_stored(
    session_factory,  # noqa: F811
    launch_log,
):
    session = session_factory(
        reload_on=(1, 2, 3),
        launch_log=launch_log,
        config="[output.progress.reload]\nwarn_after = 2\nerror_after = 3\n",
    )
    session.wait_for_agents(1)

    stored = [(data["level"], data["text"]) for _subject, data in _telemetry(session)]
    assert [level for level, _text in stored] == ["warning", "error", "error"], stored
    assert "(attempt 2, " in stored[0][1], stored
    assert stored[1][1].startswith("reloaded 3 times in a row"), stored
    assert "(attempt 3, " in stored[2][1], stored
    logged = _containing(_log(launch_log), "[session] reload: ")
    assert [_fields(line)["text"] for line in logged] == [text for _level, text in stored], logged
    assert not _containing(logged, "(attempt 1, "), logged


def test_given_no_renderer_when_a_warning_is_emitted_then_the_log_keeps_the_plain_line(
    session_factory,  # noqa: F811
    launch_log,
):
    session = session_factory(reload_on=(), launch_log=launch_log, direnv_fails=True, renderer=False)
    session.wait_for_agents(1)

    assert _containing(session.rows(), DIRENV_TEXT), session.rows()
    [logged] = _containing(_log(launch_log), DIRENV_TEXT)
    assert _FALLBACK.match(logged), logged
