"""Through the real `ai` entry point, every line goes through the one renderer (AI-CLI-yxt0).

The founding observation: `[transport]` and `[dolt_server]` lines printed plain while
`[launch]` lines were coloured, because the old interface styled only what a caller
asked for. These tests drive `ai_cli.main.cli()` on a real pseudo-terminal and read
the per-cell attributes of the screen pyte renders, so "styled" means what a terminal
shows, not which bytes a test expected. The structured-relay tests run a real child
Python process that emits through `ai_cli.output`, so the parent parses what a child
`ai` really writes. The import-cost test runs `cli()` in a fresh interpreter, because
this test process has long since imported everything.

The pty tests are POSIX-only; the relay, import-cost and session-script tests that do
not need a terminal run everywhere a POSIX shell exists.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from test_line_discipline import (
    DOLT_LINE,
    Terminal,
    _launch,
    _launch_patches,
    _preflight_answering,
    _rows,
    posix_pty,
)

from ai_cli import output as _out
from ai_cli.main import _auto_update_if_stale as _REAL_AUTO_UPDATE
from ai_cli.main import cli
from ai_cli.output import Level, Record, Tag
from ai_cli.transport import _TERMINAL_RESTORE

REPO = Path(__file__).resolve().parents[1]
# The launch patches `subprocess.run` itself (module-wide), so a stub that must start a
# real child keeps its own reference.
_REAL_RUN = subprocess.run


@pytest.fixture(autouse=True)
def _colour_terminal(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("COLORTERM", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")


@pytest.fixture
def terminal():
    term = Terminal()
    yield term
    term.close()


def _cells(screen, row_index: int, text: str) -> list:
    """The screen cells under the first occurrence of ``text`` on ``row_index``."""
    row = "".join(screen.buffer[row_index][column].data for column in range(screen.columns))
    start = row.index(text)
    return [screen.buffer[row_index][column] for column in range(start, start + len(text))]


def _styled(cells, *, fg: str, bold: bool) -> bool:
    return all(cell.fg == fg and cell.bold is bold for cell in cells)


def _record_from_row(row: str) -> Record:
    tag, _, rest = row.partition(" ")
    level = Level.INFO
    for name, candidate in (("Warning: ", Level.WARNING), ("Error: ", Level.ERROR)):
        if rest.startswith(name):
            level, rest = candidate, rest[len(name) :]
    return Record(at=datetime.now(UTC), tag=Tag(tag.strip("[]")), text=rest, level=level)


def _stale_launcher_commits():
    # The first read is the commit this launcher loaded; every later read is a newer install,
    # which makes the transport print its stale-launcher warning after the first drop.
    reads = iter(["a" * 40])
    return lambda: next(reads, "b" * 40)


@posix_pty
def test_given_a_tty_reattach_when_run_through_cli_then_transport_and_dolt_server_lines_are_styled_per_element(
    tmp_path,
):
    # Wide enough that no record wraps, so each screen row is one whole record.
    terminal = Terminal(columns=400)
    try:
        _assert_styled_reattach(tmp_path, terminal)
    finally:
        terminal.close()


def _assert_styled_reattach(tmp_path: Path, terminal: Terminal) -> None:
    _reattach_with_stale_launcher(tmp_path, terminal)
    screen = terminal.screen()
    rows = _rows(screen)
    received = bytes(terminal.received)
    produced = [(index, row) for index, row in enumerate(rows) if row.startswith(("[transport] ", "[dolt_server] "))]
    assert any(row.startswith("[dolt_server] healthy") for _, row in produced), rows
    assert any(row.startswith("[transport] Warning: ") for _, row in produced), rows
    for index, row in produced:
        tag = row.split(" ", 1)[0]
        assert _styled(_cells(screen, index, tag), fg="blue", bold=True), (row, _cells(screen, index, tag)[0])
        record = _record_from_row(row)
        if record.level is Level.WARNING:
            assert _styled(_cells(screen, index, "Warning:"), fg="brown", bold=True), row
        assert _styled(_cells(screen, index, record.text[:20]), fg="default", bold=False), row
        # The bytes on the wire are exactly the renderer's output for that record. The pty's
        # ONLCR turns the record's own CR LF into CR CR LF on the master side.
        assert _out.render_record(record, color=True, stream=terminal.stream).encode() + b"\r\r\n" in received, row


def _reattach_with_stale_launcher(tmp_path: Path, terminal: Terminal) -> None:
    with patch("ai_cli.transport._current_update_commit", side_effect=_stale_launcher_commits()):
        code = _launch(tmp_path, attempts=2, ssh=lambda *_a, **_k: 255, stdout=terminal.stream, stderr=terminal.stream)
    assert code == 255


@posix_pty
def test_given_pre_styled_text_when_emitted_then_only_element_styles_reach_the_terminal(terminal, tmp_path):
    child = "dolt_server: \x1b[1;31mALERT\x1b[0m healthy \x1b[38;5;208mamber\x1b[0m\n"

    _launch(
        tmp_path, attempts=2, ssh=lambda *_a, **_k: 0, stdout=terminal.stream, stderr=terminal.stream, dolt_stdout=child
    )

    screen = terminal.screen()
    rows = _rows(screen)
    received = bytes(terminal.received)
    index = rows.index("[dolt_server] ALERT healthy amber")
    assert b"\x1b[1;31m" not in received
    assert b"38;5;208" not in received
    assert _styled(_cells(screen, index, "[dolt_server]"), fg="blue", bold=True)
    assert _styled(_cells(screen, index, "ALERT healthy amber"), fg="default", bold=False)


@posix_pty
@pytest.mark.parametrize(("variable", "value"), [("NO_COLOR", "1"), ("TERM", "dumb")])
def test_given_no_color_or_dumb_term_when_ai_c_runs_on_a_pty_then_no_sgr_is_written(
    terminal, tmp_path, monkeypatch, variable, value
):
    monkeypatch.setenv(variable, value)

    _reattach_with_stale_launcher(tmp_path, terminal)

    rows = _rows(terminal.screen())
    # The transport's terminal restore ends in an attribute reset: terminal control, not a styled line.
    received = bytes(terminal.received).replace(_TERMINAL_RESTORE.encode(), b"")
    assert any(row.startswith("[transport] Warning: ") for row in rows), rows
    assert any(row.startswith("[dolt_server] healthy") for row in rows), rows
    # An SGR sequence is CSI <digits and semicolons> m.
    assert not re.findall(rb"\x1b\[[0-9;]*m", received), received


@posix_pty
def test_given_a_narrow_pty_when_a_long_record_is_emitted_then_one_line_is_written(tmp_path):
    narrow = Terminal(columns=40, rows=60)
    long_line = "dolt_server: " + "healthy and accepting connections on the socket " * 4
    try:
        _launch(
            tmp_path,
            attempts=2,
            ssh=lambda *_a, **_k: 0,
            stdout=narrow.stream,
            stderr=narrow.stream,
            dolt_stdout=long_line + "\n",
        )
        narrow.screen()
    finally:
        narrow.close()

    received = bytes(narrow.received).decode()
    start = received.index("[dolt_server]")
    # Everything up to the record's terminator; the pty's ONLCR doubles the CR before LF.
    record = received[start : received.index("\n", start)].rstrip("\r")
    assert _out.inert(record) == f"[dolt_server] {long_line.removeprefix('dolt_server: ')}", repr(record)


# --- structured relay between ai processes --------------------------------------

_CHILD_UPDATE = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, sys.argv[1])
    from ai_cli import output
    from ai_cli.output import Tag
    output.emit(Tag.UPDATE, "pulled 3 commits", op="install")
    output.emit(Tag.GIT, "Already up to date.")
    output.warning(Tag.UPDATE, "the uv cache was cold\\nrun ai update -f to retry")
    """
)


def _child_update(script: str):
    """Answer `ai update` by running ``script`` as a real child Python, as the parent's env says."""
    children: list[subprocess.CompletedProcess[str]] = []
    preflight = _preflight_answering(f"{DOLT_LINE}\n")

    def run(command, **kwargs):
        if isinstance(command, list) and command[1:3] == ["update", "--force"]:
            env = kwargs.get("env") or {}
            assert env.get(_out.RECORD_RELAY_ENV) == "1", "the parent must ask its ai child for records"
            child_env = {key: value for key, value in env.items() if key != "PYTHONPATH"}
            completed = _REAL_RUN(
                [sys.executable, "-c", script, str(REPO / "src")],
                capture_output=True,
                text=True,
                env=child_env,
                check=False,
                timeout=120,
            )
            children.append(completed)
            return completed
        return preflight(command, **kwargs)

    return run, children


def _launch_with_update(tmp_path: Path, run):
    project = tmp_path / "ai-cli-utils"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname = 'ai-cli-utils'\n", encoding="utf-8")
    with ExitStack() as stack:
        _launch_patches(stack, tmp_path, 1, lambda *_a, **_k: 0, f"{DOLT_LINE}\n")
        for target, kwargs in [
            ("ai_cli.main._auto_update_if_stale", {"new": _REAL_AUTO_UPDATE}),
            ("ai_cli.main._find_aicli_project_path", {"return_value": project}),
            ("ai_cli.main._installed_source_fingerprint", {"return_value": "fingerprint-1"}),
            ("ai_cli.main.subprocess.run", {"side_effect": run}),
            ("ai_cli.main.os.execvp", {"side_effect": SystemExit(0)}),
        ]:
            stack.enter_context(patch(target, **kwargs))
        rendered = stack.enter_context(patch.object(_out, "render_record", wraps=_out.render_record))
        with pytest.raises(SystemExit):
            cli()
    return [call.args[0] for call in rendered.call_args_list]


def test_given_a_child_ai_update_when_relayed_through_cli_then_each_line_is_tagged_once(tmp_path, capsys):
    run, children = _child_update(_CHILD_UPDATE)

    records = _launch_with_update(tmp_path, run)

    captured = capsys.readouterr()
    assert len(children) == 1
    child_lines = [json.loads(line) for line in (children[0].stdout + children[0].stderr).splitlines()]
    assert "[update] install: pulled 3 commits\n" in captured.out
    assert "[git] Already up to date.\n" in captured.out
    assert "[update] Warning: the uv cache was cold\n[update] run ai update -f to retry\n" in captured.err
    assert "[update] [" not in captured.out + captured.err
    assert "{" not in captured.out + captured.err
    relayed = {record.text: record for record in records if record.text in {line["text"] for line in child_lines}}
    for line in child_lines:
        record = relayed[line["text"]]
        assert (record.tag.value, record.op, record.level.value) == (line["tag"], line["op"], line["level"])
        assert record.at == datetime.fromisoformat(line["at"]), "the child's own timestamp is kept"


def test_given_a_malformed_child_record_when_relayed_then_it_becomes_a_description(tmp_path, capsys):
    malformed = [
        '{"ai_cli_record": 1, "tag": "no-such-tag", "level": "info", "op": null, "text": "x"}',
        '{"ai_cli_record": 1, "tag": "update", "level": "loud", "op": null, "text": "y"}',
        '{"truncated": [1, 2',
    ]
    script = f"import sys\nprint({chr(10).join(malformed)!r})\n"
    run, _children = _child_update(script)

    _launch_with_update(tmp_path, run)

    out = capsys.readouterr().out
    for line in malformed:
        assert f"[update] {line}\n" in out, out


# --- session script ---------------------------------------------------------------


def test_given_no_ai_entry_point_when_the_session_script_emits_then_a_plain_tagged_line_is_printed(tmp_path):
    if os.name != "posix":
        pytest.skip("the session supervisor is a POSIX shell script")
    from test_stale_session_reaper import _finish_supervisor, _start_generated_supervisor

    calls = tmp_path / "emit-calls"
    # The entry point exists but cannot render, as while `uv tool install` rewrites it.
    ai_stub = (
        "#!/bin/sh\n"
        f'if [ "$1" = internal ] && [ "$2" = emit ]; then printf "%s\\n" "$*" >> {calls}; exit 127; fi\n'
        "exit 0\n"
    )
    process, _, _ = _start_generated_supervisor(
        tmp_path, "/bin/bash", terminal_pgid="456", supervisor_pgid="123", extra_commands={"ai": ai_stub}
    )

    _, stderr = _finish_supervisor(process)

    reason = "supervisor is not in the terminal foreground process group"
    assert f"[session] stale-session reaper evidence disabled: {reason}\n" in stderr, stderr
    assert f"internal emit session info plain stale-session reaper evidence disabled: {reason}" in calls.read_text()


def test_given_a_working_ai_entry_point_when_the_session_script_emits_then_the_renderer_prints_the_line(tmp_path):
    if os.name != "posix":
        pytest.skip("the session supervisor is a POSIX shell script")
    from test_stale_session_reaper import _finish_supervisor, _start_generated_supervisor

    # Every other internal action keeps the harness stub's answers; `emit` reaches the real CLI.
    ai_stub = (
        "#!/bin/sh\n"
        'if [ "$1" = internal ] && [ "$2" = emit ]; then\n'
        f"  PYTHONPATH={REPO / 'src'} exec {sys.executable} -m ai_cli.main \"$@\" | sed 's/^/rendered:/'\n"
        "fi\n"
        "exit 0\n"
    )
    process, _, _ = _start_generated_supervisor(
        tmp_path, "/bin/bash", terminal_pgid="456", supervisor_pgid="123", extra_commands={"ai": ai_stub}
    )

    _, stderr = _finish_supervisor(process)

    reason = "supervisor is not in the terminal foreground process group"
    assert f"rendered:[session] stale-session reaper evidence disabled: {reason}\n" in stderr, stderr


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["session", "warning", "plain", "two", "words"], "[session] Warning: two words\n"),
        (["session", "info", "terminal", "styled"], "[session] styled\n"),
    ],
)
def test_given_ai_internal_emit_when_run_through_cli_then_it_replies_with_one_rendered_record(
    args, expected, monkeypatch, capsys
):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(sys, "argv", ["ai", "internal", "emit", *args])

    with pytest.raises(SystemExit) as exc_info:
        cli()

    assert exc_info.value.code == 0
    assert capsys.readouterr() == (expected, "")


def test_given_a_terminal_target_when_ai_internal_emit_runs_then_its_reply_is_styled(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ai", "internal", "emit", "session", "error", "terminal", "boom"])

    with pytest.raises(SystemExit):
        cli()

    out = capsys.readouterr().out
    assert out.startswith("\x1b[")
    assert _out.inert(out) == "[session] Error: boom"


@pytest.mark.parametrize(
    "args",
    [["session"], ["no-such-tag", "info", "plain", "x"], ["session", "loud", "plain", "x"], ["session", "info", "x"]],
)
def test_given_a_malformed_ai_internal_emit_when_run_then_it_fails_so_the_script_falls_back(args, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ai", "internal", "emit", *args])

    with pytest.raises(SystemExit) as exc_info:
        cli()

    assert exc_info.value.code != 0
    assert capsys.readouterr().out == ""


# --- import cost ------------------------------------------------------------------

_IMPORT_PROBE = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, sys.argv[1])
    sys.argv = ["ai", *sys.argv[2:]]
    from ai_cli.main import cli
    try:
        cli()
    except SystemExit:
        pass
    sys.__stderr__.write(f"LOADED rich={'rich' in sys.modules} structlog={'structlog' in sys.modules}\\n")
    """
)


@pytest.mark.parametrize(
    "argv", [["-V"], ["--version"], ["quota", "statusline-part"], ["internal", "get-version"]], ids=" ".join
)
def test_given_version_and_statusline_paths_when_run_through_cli_then_rich_is_not_imported(argv, tmp_path):
    home = tmp_path / "home"
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT"}}
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        XDG_STATE_HOME=str(home / "state"),
        XDG_CONFIG_HOME=str(home / "config"),
        XDG_DATA_HOME=str(home / "data"),
        XDG_CACHE_HOME=str(home / "cache"),
        LOCALAPPDATA=str(home / "state"),
        # The statusline's own env-supplied segment: the statusline path with no store to read.
        AI_CLI_QUOTA_STATUSLINE_SEGMENT="claude-five-hour",
        AI_CLI_QUOTA_FIVE_HOUR_PCT="12",
        AI_CLI_QUOTA_FIVE_HOUR_RESET=str(int(datetime.now(UTC).timestamp()) + 3600),
    )

    # A first `ai internal` call in a fresh home registers the machine profile and says so in
    # a record; the reply paths are measured in the steady state after it.
    subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE, str(REPO / "src"), "internal", "get-version"],
        capture_output=True,
        env=env,
        cwd=tmp_path,
        check=False,
        timeout=120,
    )
    probe = subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE, str(REPO / "src"), *argv],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
        timeout=120,
    )

    assert "LOADED rich=False structlog=False" in probe.stderr, probe.stderr
