"""Through the real `ai` entry point, every line carries an `<operation>:` element (AI-CLI-rbuf).

The ask: every log line names its operation, rich-formatted and coloured, enforced in
the output library as the log-type tag is, with the vocabulary, whether it is required,
its default, colour and pattern all in config. The founding lines were
`[transport] Connection lost ...` and `[dolt_server] healthy ...`, which had none. These
tests drive `ai_cli.main.cli()` (an `ai c -R` reattach loop and `ai internal emit`) and the
package's real stdlib-logging adapter, and write the user's real `config.toml`. The
static half of the guarantee, that every producer passes a vocabulary operation, is
`tests/test_output_enforcement.py`.
"""

from __future__ import annotations

import logging
import re
import sys
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest
from test_line_discipline import Terminal, _launch, _rows, posix_pty
from test_output_config import CONFIG_ERROR, _internal_emit, _write_config
from test_output_rendering import _cells, _reattach_with_stale_launcher, _styled

from ai_cli import output as _out
from ai_cli.main import cli

REPO = Path(__file__).resolve().parents[1]
SHIPPED = tomllib.loads((REPO / "src" / "ai_cli" / "data" / "output.toml").read_text(encoding="utf-8"))["output"][
    "elements"
]["operation"]["vocabulary"]
_LINE = re.compile(r"^\[(?P<tag>[a-z0-9_-]+)\] (?P<op>[a-z][a-z0-9-]*): (?P<text>.*)$")


@pytest.fixture(autouse=True)
def _colour_terminal(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("COLORTERM", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")


def _vocabulary_toml(names) -> str:
    return "[output.elements.operation]\nvocabulary = [" + ", ".join(f'"{name}"' for name in names) + "]\n"


def _parsed(text: str) -> list[re.Match[str]]:
    lines = [line for line in text.splitlines() if line]
    matches = [_LINE.match(line) for line in lines]
    assert all(matches), [line for line, match in zip(lines, matches, strict=True) if match is None]
    return [match for match in matches if match is not None]


# --- the founding lines -------------------------------------------------------------


def test_given_a_dropped_link_when_ai_c_r_reattaches_then_transport_and_dolt_lines_carry_operations(tmp_path, capsys):
    # First a credential refusal (it waits for re-authentication), then two plain drops
    # (one reattach, then the reconnect budget is spent).
    results = iter([(b"Permission denied (publickey).\n", 255), (b"", 255), (b"", 255)])

    def ssh(_args, stderr=None, **_kwargs):
        assert stderr is not None
        text, code = next(results)
        stderr.write(text)
        return code

    code = _launch(tmp_path, attempts=2, ssh=ssh, stdout=sys.stdout, stderr=sys.stderr)

    captured = capsys.readouterr()
    assert code == 255
    lines = _parsed(captured.err)
    dolt = [line for line in lines if line["tag"] == "dolt_server"]
    transport = [line for line in lines if line["tag"] == "transport"]
    assert [line["op"] for line in dolt] == ["ensure"], captured.err
    assert dolt[0]["text"] == "healthy (socket accepted)"
    notices = {line["op"]: line["text"] for line in transport if line["op"] != "connect"}
    assert notices["auth-wait"].startswith("Connection refused on expired or rejected credentials"), notices
    assert notices["reconnect"].startswith("Connection lost (ssh exit 255)"), notices
    # The relayed ssh stderr is the connection attempt's own output.
    assert [line["text"] for line in transport if line["op"] == "connect"] == ["Permission denied (publickey)."]
    assert {line["op"] for line in transport} == {"connect", "auth-wait", "reconnect"}


# --- required, optional and the default ---------------------------------------------


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        pytest.param("", "[log] general: Warning: disk is full", id="required-default"),
        pytest.param(
            '[output.elements.operation]\nrequired = true\ndefault = "load"\n',
            "[log] load: Warning: disk is full",
            id="required-configured-default",
        ),
        pytest.param("[output.elements.operation]\nrequired = false\n", "[log] Warning: disk is full", id="optional"),
    ],
)
def test_given_a_logging_record_when_required_or_optional_then_the_operation_element_follows_config(
    capsys, config, expected
):
    _write_config(config)

    # A stdlib logging record names no operation: the package's own handler is the adapter.
    logging.getLogger("ai_cli.example").warning("disk is full")

    captured = capsys.readouterr()
    assert captured.err.splitlines() == [expected]


def test_given_optional_operations_when_a_producer_names_one_then_it_still_renders(capsys, monkeypatch):
    _write_config("[output.elements.operation]\nrequired = false\n")

    out, err = _internal_emit(capsys, monkeypatch, "session", "info", "reload", "plain", "reloading")

    assert (out, err) == ("[session] reload: reloading\n", "")


# --- the vocabulary is config ---------------------------------------------------------


@pytest.mark.parametrize(
    ("vocabulary", "reason"),
    [
        pytest.param(["start", "general"], "cannot remove the shipped operations", id="missing-shipped"),
        pytest.param([*SHIPPED, "Deploy_Now"], "the operations Deploy_Now fail", id="fails-pattern"),
    ],
)
def test_given_a_vocabulary_missing_a_shipped_operation_when_loaded_then_it_is_refused(
    capsys, monkeypatch, vocabulary, reason
):
    path = _write_config(_vocabulary_toml(vocabulary) + 'style = "bold magenta"\n')

    out, err = _internal_emit(capsys, monkeypatch, "session", "info", "reload", "plain", "reloading")

    errors = CONFIG_ERROR.findall(err)
    assert [(found, rest.split(": ", 1)[0]) for found, rest in errors] == [
        (str(path), "output.elements.operation.vocabulary")
    ], err
    assert reason in errors[0][1]
    # The whole table is set aside: the reply renders with the shipped defaults.
    assert out == "[session] reload: reloading\n"


def test_given_a_user_added_operation_when_ai_internal_emit_names_it_then_it_renders(capsys, monkeypatch):
    _write_config(_vocabulary_toml([*SHIPPED, "deploy"]))

    out, err = _internal_emit(capsys, monkeypatch, "session", "info", "deploy", "plain", "rolling out")

    assert (out, err) == ("[session] deploy: rolling out\n", "")


def test_given_an_operation_outside_the_vocabulary_when_ai_internal_emit_names_it_then_it_is_refused(
    capsys, monkeypatch
):
    monkeypatch.setattr(sys, "argv", ["ai", "internal", "emit", "session", "info", "deploy", "plain", "rolling out"])

    with pytest.raises(SystemExit) as exc_info:
        cli()

    captured = capsys.readouterr()
    assert exc_info.value.code == 1
    assert captured.out == ""
    assert "'deploy' is not in the operation vocabulary" in captured.err


def test_given_an_operation_outside_the_vocabulary_when_a_producer_emits_then_it_is_refused(capsys):
    with pytest.raises(ValueError, match="'deploy' is not in the operation vocabulary"):
        _out.emit(_out.Tag.SESSION, "deploy", "rolling out", err=True)

    assert capsys.readouterr().err == ""


# --- colour ---------------------------------------------------------------------------


@posix_pty
def test_given_a_per_operation_style_when_rendered_on_a_pty_then_it_applies(tmp_path):
    _write_config('[output.elements.operation.styles]\nreconnect = "bold magenta"\n')
    terminal = Terminal(columns=400)
    try:
        with patch("ai_cli.transport.time.sleep"):
            _reattach_with_stale_launcher(tmp_path, terminal)
        screen = terminal.screen()
    finally:
        terminal.close()

    rows = _rows(screen)
    reconnect = [index for index, row in enumerate(rows) if row.startswith("[transport] reconnect: ")]
    ensure = [index for index, row in enumerate(rows) if row.startswith("[dolt_server] ensure: ")]
    assert reconnect, rows
    assert ensure, rows
    for index in reconnect:
        assert _styled(_cells(screen, index, "reconnect:"), fg="magenta", bold=True), rows[index]
    for index in ensure:
        # An operation the table does not name keeps the element's own style.
        assert _styled(_cells(screen, index, "ensure:"), fg="cyan", bold=True), rows[index]


@posix_pty
def test_given_launch_output_when_rendered_on_a_pty_then_the_launch_lines_carry_their_operations(tmp_path):
    terminal = Terminal(columns=400)
    try:
        code = _launch(tmp_path, attempts=2, ssh=lambda *_a, **_k: 0, stdout=terminal.stream, stderr=terminal.stream)
        screen = terminal.screen()
    finally:
        terminal.close()

    rows = [row for row in _rows(screen) if row.startswith("[launch] ")]
    assert code == 0
    assert rows[0].startswith("[launch] start: Starting Claude Code session ("), rows
    assert rows[-1].startswith("[launch] handoff: ready, handing off to Claude Code ("), rows
    handoff = len(_rows(screen)) - 1 - _rows(screen)[::-1].index(rows[-1])
    assert _styled(_cells(screen, handoff, "handoff:"), fg="green", bold=True), rows[-1]
    for row in rows:
        match = _LINE.match(row)
        assert match and match["op"] in SHIPPED, row
