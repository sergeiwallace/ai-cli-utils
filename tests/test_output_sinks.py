"""Through the real `ai c` entry point, the terminal, the launch log and telemetry agree (AI-CLI-2gq3).

The ask: apply the same log-line model and config (type tag, datetime, operation,
enforcement, colours where the sink supports them) to the per-launch log file and to
telemetry, so the three share one structured record. Every test drives `ai c -R`
through `cli()`. The Dolt supervisor's relayed lines are the producer: the relay
parses each `[tag] op: Level: text` line into a record, the way a real supervisor's
reply reaches the launcher. The clock is frozen, and HOME and the state directory are
redirected, so the launch log and the telemetry store are this test's own.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import remote_allocation_reply
from test_output_config import _dry_run, _write_config
from test_output_datetime import _at, _Clock

from ai_cli import config as _config
from ai_cli import output as _out
from ai_cli.main import _REMOTE_SHELL_PROBE_CMD, cli

pytestmark = pytest.mark.shipped_output_datetime

SUMMER = "2026-07-01 14:30:00 EDT"
WARNING_TEXT = "socket slow to accept (4.2s)"
WARNING_LINE = f"[dolt_server] ensure: Warning: {WARNING_TEXT}"

_REMOTE_CONFIG = {
    "remote": {
        "default": "example-box",
        "machines": {
            "example-box": {
                "host": "192.0.2.10",
                "user": "exampleuser",
                "transport": "ssh",
                "reconnect_attempts": 1,
                "reconnect_backoff": 0.0,
            }
        },
    },
}

# One rendered line: the shipped default element order.
_LINE = re.compile(
    r"^(?P<at>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [A-Z]{2,5}) \[(?P<tag>[a-z0-9_-]+)\] "
    r"(?P<op>[a-z][a-z0-9-]*): (?:(?P<level>Warning|Error): )?(?P<text>.*)$"
)


@pytest.fixture(autouse=True)
def _launch_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("AI_TMUX_SESSION", raising=False)
    monkeypatch.setattr(_out, "datetime", _Clock)
    _Clock.instants = [_at(2026, 7, 1, 18, 30)]


@pytest.fixture
def dolt_script(tmp_path):
    script = tmp_path / "dolt_server.py"
    script.write_text("", encoding="utf-8")
    return script


def _launch(capsys, dolt_script, dolt_reply: str, *, argv=("ai", "c", "-R", "2"), on_track=None):
    """Run `ai c -R` to its handoff; the supervisor replies ``dolt_reply``. Returns (code, stderr)."""

    def preflight(command, **_kwargs):
        if isinstance(command, (list, tuple)) and any("dolt_server.py" in str(part) for part in command):
            return subprocess.CompletedProcess(command, 0, stdout=dolt_reply, stderr=None)
        if isinstance(command, (list, tuple)) and command and command[-1] == _REMOTE_SHELL_PROBE_CMD:
            return subprocess.CompletedProcess(command, 0, stdout="zsh\n", stderr="")
        if (allocation := remote_allocation_reply(command)) is not None:
            return allocation
        return subprocess.CompletedProcess(command, 0, stdout="current", stderr="")

    def track_launch(**_kwargs):
        if on_track is not None:
            on_track()
        raise OSError("the session registry is not writable here")

    config = {**_REMOTE_CONFIG, "dolt_server": {"script_path": str(dolt_script)}}
    with (
        patch("sys.argv", list(argv)),
        patch("ai_cli.main.sys.platform", "linux"),
        patch("ai_cli.main.shutil.which", return_value="/usr/bin/tmux"),
        patch("ai_cli.config.load_config", return_value=config),
        patch("ai_cli.main.load_config", return_value=config),
        patch("ai_cli.session.get_project_prefix", return_value="myproject"),
        patch("ai_cli.session.is_current_project_resolved", return_value=True),
        patch("ai_cli.config.get_project_aliases", return_value={}),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.main.trigger_background_update"),
        patch("ai_cli.main._session_registry.track_launch", side_effect=track_launch),
        patch("ai_cli.iterm2._assign_iterm2_color_slot", return_value=None),
        patch("ai_cli.iterm2._emit_iterm2_profile_setup"),
        patch("ai_cli.iterm2._current_pane_tty", return_value=None),
        patch("ai_cli.main.subprocess.run", side_effect=preflight),
        patch("ai_cli.transport.time.sleep"),
        patch("ai_cli.transport.subprocess.call", side_effect=[0]),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli()
    return exc_info.value.code, capsys.readouterr().err


def _write_launch_config(body: str) -> Path:
    """Write ``config.toml`` where the launch reads it: ``_launch`` runs it as Linux on every host.

    On Windows the resolver otherwise picks APPDATA, so a file written outside the
    platform patch is one the launch never reads.
    """
    with patch("sys.platform", "linux"):
        return _write_config(body)


def _log_lines() -> list[str]:
    logs = sorted((_config.get_xdg_state_home() / "launch-logs").glob("*.log"))
    assert len(logs) == 1, logs
    return logs[0].read_text(encoding="utf-8").splitlines()


def _telemetry() -> list[tuple[str, float, dict]]:
    database = Path.home() / ".ai-cli" / "telemetry.db"
    if not database.exists():
        return []
    connection = sqlite3.connect(str(database))
    try:
        rows = connection.execute(
            "SELECT subject, ts, data FROM events WHERE subject LIKE 'telemetry.log.%' ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    return [(subject, ts, json.loads(data)) for subject, ts, data in rows]


def _fields(line: str) -> dict[str, str]:
    match = _LINE.match(line)
    assert match is not None, line
    return {
        "at": match["at"],
        "tag": match["tag"],
        "op": match["op"],
        "level": (match["level"] or "info").lower(),
        "text": match["text"],
    }


def _event_fields(data: dict) -> dict[str, str]:
    shown = _out.format_datetime(datetime.fromisoformat(data["at"]), "%Y-%m-%d %H:%M:%S %Z", "America/New_York")
    return {"at": shown, "tag": data["tag"], "op": data["op"], "level": data["level"], "text": data["text"]}


def _containing(lines: list[str], text: str) -> list[str]:
    return [line for line in lines if text in line]


# --- AC 1: one record, three sinks ------------------------------------------------


def test_given_one_warning_event_when_ai_c_launches_then_terminal_file_and_telemetry_agree(capsys, dolt_script):
    code, err = _launch(capsys, dolt_script, f"{WARNING_LINE}\n")

    assert code == 0
    [terminal] = _containing(err.splitlines(), WARNING_TEXT)
    [logged] = _containing(_log_lines(), WARNING_TEXT)
    [event] = [row for row in _telemetry() if row[2]["text"] == WARNING_TEXT]
    expected = {"at": SUMMER, "tag": "dolt_server", "op": "ensure", "level": "warning", "text": WARNING_TEXT}
    assert _fields(terminal) == expected
    assert _fields(logged) == expected
    assert _event_fields(event[2]) == expected
    subject, ts, _data = event
    assert subject == "telemetry.log.dolt_server"
    assert ts == _at(2026, 7, 1, 18, 30).timestamp()


# --- AC 2: the level gate, stored before handoff ----------------------------------


def test_given_records_of_each_level_when_the_launch_hands_off_then_only_warning_and_above_reach_telemetry(
    capsys, dolt_script
):
    reply = (
        "[dolt_server] ensure: Error: socket refused on the first try\n"
        f"{WARNING_LINE}\n"
        "[dolt_server] ensure: healthy (socket accepted)\n"
    )
    stored_at_handoff: list[list[str]] = []
    real_flush = _out.flush_sinks

    def flush_and_look():
        real_flush()
        stored_at_handoff.append([data["text"] for _subject, _ts, data in _telemetry()])

    with patch.object(_out, "flush_sinks", side_effect=flush_and_look):
        code, err = _launch(capsys, dolt_script, reply)

    assert code == 0
    assert "handoff: ready, handing off" in err
    texts = [data["text"] for _subject, _ts, data in _telemetry()]
    # The launch's own records below warning: the detail and every info line.
    assert "configuration loaded" not in texts
    assert "healthy (socket accepted)" not in texts
    assert not any("handing off" in text for text in texts)
    assert sorted(texts) == sorted(
        [
            "socket refused on the first try",
            WARNING_TEXT,
            "iTerm2 session registry not updated (the session registry is not writable here); launching anyway",
        ]
    )
    assert stored_at_handoff and sorted(stored_at_handoff[0]) == sorted(texts), stored_at_handoff


# --- AC 3: telemetry never delays or breaks the handoff ---------------------------


def _store_is_a_file(_tmp_path):
    (Path.home() / ".ai-cli").write_text("not a directory", encoding="utf-8")


def _store_is_locked(_tmp_path):
    from ai_cli import telemetry

    database = Path.home() / ".ai-cli" / "telemetry.db"
    telemetry.init_db(database).close()
    holder = sqlite3.connect(str(database), isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    return holder


def _telemetry_disabled(_tmp_path):
    _write_launch_config("[telemetry]\nenabled = false\n")


@pytest.mark.parametrize(
    "make_unwritable",
    [
        pytest.param(_store_is_a_file, id="store-directory-is-a-file"),
        pytest.param(_store_is_locked, id="store-locked-by-another-writer"),
        pytest.param(_telemetry_disabled, id="telemetry-enabled-false"),
    ],
)
def test_given_an_unwritable_telemetry_store_when_ai_c_hands_off_then_the_launch_proceeds_promptly(
    make_unwritable, capsys, dolt_script, tmp_path
):
    holder = make_unwritable(tmp_path)
    spent: list[float] = []
    real_flush = _out.flush_sinks

    def timed_flush():
        started = time.perf_counter()
        real_flush()
        spent.append(time.perf_counter() - started)

    try:
        with patch.object(_out, "flush_sinks", side_effect=timed_flush):
            code, err = _launch(capsys, dolt_script, f"{WARNING_LINE}\n")
    finally:
        if holder is not None:
            holder.close()

    assert code == 0
    assert "handoff: ready, handing off" in err
    assert "Traceback" not in err
    assert spent, "the handoff flushed no sinks"
    assert sum(spent) <= 0.1, f"the telemetry sink added {sum(spent) * 1000:.0f} ms before handoff"
    if make_unwritable is _telemetry_disabled:
        assert _telemetry() == []


def test_given_a_locked_store_and_a_slow_machine_lookup_when_ai_c_hands_off_then_the_lookup_is_never_paid(
    capsys, dolt_script, tmp_path
):
    """A locked store loses its batch, so it must not pay for the rows it cannot write (AI-CLI-n0d7).

    The first machine-id lookup in a process costs tens of milliseconds warm and far more
    cold; paid before the lock attempt, it pushed a slow macOS runner past the 100 ms
    budget (111 ms) although the batch was dropped anyway.
    """
    from ai_cli import telemetry

    holder = _store_is_locked(tmp_path)
    looked_up: list[float] = []

    def slow_machine_id():
        looked_up.append(time.perf_counter())
        time.sleep(0.08)
        return "host-1"

    spent: list[float] = []
    real_flush = _out.flush_sinks

    def timed_flush():
        started = time.perf_counter()
        real_flush()
        spent.append(time.perf_counter() - started)

    try:
        with (
            patch.object(telemetry, "_get_machine_id", side_effect=slow_machine_id),
            patch.object(_out, "flush_sinks", side_effect=timed_flush),
        ):
            code, err = _launch(capsys, dolt_script, f"{WARNING_LINE}\n")
    finally:
        holder.close()

    assert code == 0
    assert "handoff: ready, handing off" in err
    assert looked_up == [], "the machine id was looked up for a batch the locked store could not take"
    assert sum(spent) <= 0.1, f"the telemetry sink added {sum(spent) * 1000:.0f} ms before handoff"


# --- AC 4: the launch log failing leaves the terminal alone -----------------------


def test_given_an_unwritable_launch_log_when_ai_c_runs_then_terminal_output_is_unchanged(capsys, dolt_script):
    code, writable = _launch(capsys, dolt_script, f"{WARNING_LINE}\n")
    assert code == 0
    logs = _config.get_xdg_state_home() / "launch-logs"
    for log in logs.glob("*.log"):
        log.unlink()
    logs.rmdir()
    logs.write_text("not a directory", encoding="utf-8")

    code, unwritable = _launch(capsys, dolt_script, f"{WARNING_LINE}\n")

    assert code == 0
    assert "Traceback" not in unwritable
    assert unwritable == writable


# --- AC 5: the level comes from the record, not the text --------------------------


def test_given_an_info_line_mentioning_failed_when_logged_then_its_level_is_info(capsys, dolt_script):
    text = "recovered after one failed probe"
    code, _err = _launch(capsys, dolt_script, f"[dolt_server] ensure: {text}\n")

    assert code == 0
    [logged] = _containing(_log_lines(), text)
    assert _fields(logged)["level"] == "info"
    assert "ERROR" not in logged
    assert _fields(logged)["text"] == text


# --- AC 6: detail records reach the file whatever the terminal shows --------------


def test_given_a_non_verbose_launch_when_details_are_recorded_then_the_file_has_them(capsys, dolt_script):
    code, err = _launch(capsys, dolt_script, "[dolt_server] ensure: healthy\n")

    assert code == 0
    assert "configuration loaded" not in err
    [logged] = _containing(_log_lines(), "configuration loaded")
    assert _fields(logged) == {
        "at": SUMMER,
        "tag": "launch",
        "op": "request",
        "level": "info",
        "text": "configuration loaded",
    }


# --- AC 7: non-record stderr text is still mirrored -------------------------------


def test_given_foreign_stderr_text_when_a_launch_runs_then_the_log_file_mirrors_it(capsys, dolt_script):
    foreign = "some-tool: deprecated flag --legacy ignored"

    def foreign_write():
        sys.stderr.write(f"{foreign}\n")

    code, err = _launch(capsys, dolt_script, "[dolt_server] ensure: healthy\n", on_track=foreign_write)

    assert code == 0
    assert foreign in err
    [logged] = _containing(_log_lines(), foreign)
    assert logged == f"{SUMMER} {foreign}"


# --- configured from the same file ------------------------------------------------


@pytest.mark.parametrize(
    ("config", "on_terminal", "in_file", "in_telemetry"),
    [
        pytest.param("", True, True, True, id="shipped"),
        pytest.param("[output.sinks.terminal]\nenabled = false\n", False, True, True, id="terminal-off"),
        pytest.param("[output.sinks.file]\nenabled = false\n", True, False, True, id="file-off"),
        pytest.param("[output.sinks.telemetry]\nenabled = false\n", True, True, False, id="telemetry-off"),
        pytest.param("[output.sinks.telemetry]\nmin_level = 'error'\n", True, True, False, id="min-level-error"),
    ],
)
def test_given_sink_settings_when_ai_c_hands_off_then_each_sink_follows_them(
    config, on_terminal, in_file, in_telemetry, capsys, dolt_script
):
    _write_launch_config(config)

    code, err = _launch(capsys, dolt_script, f"{WARNING_LINE}\n")

    assert code == 0
    assert bool(_containing(err.splitlines(), WARNING_TEXT)) is on_terminal
    assert bool(_containing(_log_lines(), WARNING_TEXT)) is in_file
    assert any(data["text"] == WARNING_TEXT for _s, _t, data in _telemetry()) is in_telemetry


def test_given_min_level_info_when_ai_c_hands_off_then_info_records_reach_telemetry(capsys, dolt_script):
    _write_launch_config("[output.sinks.telemetry]\nmin_level = 'info'\n")

    code, _err = _launch(capsys, dolt_script, "[dolt_server] ensure: healthy (socket accepted)\n")

    assert code == 0
    texts = [data["text"] for _subject, _ts, data in _telemetry()]
    assert "healthy (socket accepted)" in texts
    assert "configuration loaded" not in texts, "a detail record is below info"


def test_given_the_datetime_element_disabled_when_ai_c_launches_then_the_file_still_leads_with_the_time(
    capsys, dolt_script
):
    _write_launch_config("[output.elements.datetime]\nenabled = false\n")

    code, err = _launch(capsys, dolt_script, f"{WARNING_LINE}\n")

    assert code == 0
    assert _containing(err.splitlines(), WARNING_TEXT) == [WARNING_LINE]
    assert _containing(_log_lines(), WARNING_TEXT) == [f"{SUMMER} {WARNING_LINE}"]


@pytest.mark.parametrize(
    ("body", "key"),
    [
        pytest.param("[output.sinks.telemetry]\nmin_level = 'loud'\n", "output.sinks.telemetry.min_level", id="level"),
        pytest.param("[output.sinks.file]\nenabled = 'yes'\n", "output.sinks.file.enabled", id="not-a-bool"),
        pytest.param("[output.sinks.terminal]\ncolour = true\n", "output.sinks.terminal.colour", id="unknown-key"),
    ],
)
def test_given_an_invalid_sink_setting_when_ai_c_dry_runs_then_defaults_render_and_the_key_is_named(body, key, capsys):
    path = _write_config(body)

    code, _out_text, err = _dry_run(capsys)

    assert code == 0
    assert f"[config] load: Error: {path}: {key}: " in err
