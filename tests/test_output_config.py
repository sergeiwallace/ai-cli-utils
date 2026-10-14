"""Through the real `ai` entry point, one `[output]` table controls every log-line element (AI-CLI-mu4d).

The ask: config-file control of which elements are enabled, their order, delimiters,
colours and format patterns, validated at load with the config path in any error, and
read at call time rather than frozen at launch. These tests write the user's real
`config.toml` (under the per-test XDG config home) and drive `ai_cli.main.cli()`: a
local `ai c 7 --dry-run`, an `ai c -R` reconnect loop, `ai internal emit`, and the
generated session script, whose every message is one `ai internal emit` process.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import tomllib
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from test_line_discipline import Terminal, _launch, _rows, posix_pty
from test_output_rendering import _reattach_with_stale_launcher

from ai_cli import output as _out
from ai_cli.config import get_xdg_config_home
from ai_cli.main import cli
from ai_cli.output import Level, Record, Tag

REPO = Path(__file__).resolve().parents[1]
# Present so the launch's own config load does not append a [machine] table mid-test.
MACHINE = '[machine]\nhost_id = "test-host"\nos_type = "linux"\n\n'
# The start line is a launch phase: its label renders as the operation element.
STARTING = "Starting Claude Code session"
START = f"{STARTING}: local, tmux"
MODE = "launching inside tmux (tmux is the default session mode)"
PLAN = "dry run -- nothing was created, started, or reaped."
CONFIG_ERROR = re.compile(r"^\[config\] load: Error: (?P<path>.+?): (?P<rest>.*)$", re.MULTILINE)


@pytest.fixture(autouse=True)
def _plain_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("COLORTERM", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")


def _config_file() -> Path:
    return get_xdg_config_home() / "config.toml"


def _write_config(body: str) -> Path:
    path = _config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(MACHINE + body, encoding="utf-8")
    return path


def _dry_run(capsys) -> tuple[int, str, str]:
    with ExitStack() as stack:
        for target, kwargs in [
            ("sys.argv", {"new": ["ai", "c", "7", "--dry-run"]}),
            ("ai_cli.session.detect_repo_root", {"return_value": "/tmp/repo"}),
            ("ai_cli.session._resolve_is_remote", {"return_value": False}),
            ("ai_cli.config.validate_registry_completeness", {"return_value": True}),
            ("ai_cli.session.get_project_prefix", {"return_value": "myproject"}),
            ("ai_cli.session.is_current_project_resolved", {"return_value": True}),
            ("ai_cli.session.build_session_name", {"return_value": ("c-myproject-7", "myproject-7")}),
            ("ai_cli.tmux_setup.tmux_present", {"return_value": True}),
            ("ai_cli.main.os.execvp", {"side_effect": AssertionError("a dry run must not exec")}),
        ]:
            stack.enter_context(patch(target, **kwargs))
        with pytest.raises(SystemExit) as exc_info:
            cli()
    captured = capsys.readouterr()
    return exc_info.value.code, captured.out, captured.err


def _internal_emit(capsys, monkeypatch, *args: str) -> tuple[str, str]:
    monkeypatch.setattr(sys, "argv", ["ai", "internal", "emit", *args])
    with pytest.raises(SystemExit) as exc_info:
        cli()
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    return captured.out, captured.err


# --- enable, order, delimiters ----------------------------------------------------


@pytest.mark.parametrize(
    ("config", "start", "mode", "plan"),
    [
        pytest.param("", f"[launch] {START}", f"[launch] Mode: {MODE}", f"[plan] {PLAN}", id="no-config"),
        pytest.param("[output]\n", f"[launch] {START}", f"[launch] Mode: {MODE}", f"[plan] {PLAN}", id="empty-table"),
        pytest.param(
            '[output.elements.tag]\nprefix = "<"\nsuffix = ">"\n',
            f"<launch> {START}",
            f"<launch> Mode: {MODE}",
            f"<plan> {PLAN}",
            id="tag-delimiters",
        ),
        pytest.param("[output.elements.tag]\nenabled = false\n", START, f"Mode: {MODE}", PLAN, id="tag-disabled"),
        pytest.param(
            "[output.elements.operation]\nenabled = false\n",
            "[launch] local, tmux",
            f"[launch] {MODE}",
            f"[plan] {PLAN}",
            id="operation-disabled",
        ),
        pytest.param(
            '[output.elements.operation]\nsuffix = " >"\n',
            f"[launch] {STARTING} > local, tmux",
            f"[launch] Mode > {MODE}",
            f"[plan] {PLAN}",
            id="operation-suffix",
        ),
        pytest.param(
            '[output]\norder = ["description", "operation", "tag"]\n',
            f"local, tmux {STARTING}: [launch]",
            f"{MODE} Mode: [launch]",
            f"{PLAN} [plan]",
            id="order",
        ),
        pytest.param(
            '[output]\nseparator = " | "\n',
            f"[launch] | {STARTING}: | local, tmux",
            f"[launch] | Mode: | {MODE}",
            f"[plan] | {PLAN}",
            id="separator",
        ),
        pytest.param(
            '[output.elements.description]\nprefix = "\'"\nsuffix = "\'"\n',
            f"[launch] {STARTING}: 'local, tmux'",
            f"[launch] Mode: '{MODE}'",
            f"[plan] '{PLAN}'",
            id="description-delimiters",
        ),
    ],
)
def test_given_output_config_when_ai_c_dry_runs_then_lines_follow_the_configured_elements(
    config, start, mode, plan, capsys
):
    _write_config(config)

    code, out, err = _dry_run(capsys)

    assert code == 0
    assert start in err.splitlines(), err
    assert mode in err.splitlines(), err
    assert plan in out.splitlines(), out
    assert not CONFIG_ERROR.search(err), err


@pytest.mark.parametrize(
    ("config", "args", "expected"),
    [
        pytest.param("", ["warning", "plain", "lost"], "[session] Warning: lost", id="no-config"),
        pytest.param(
            "[output.elements.level]\nshow = []\n", ["warning", "plain", "lost"], "[session] lost", id="level-hidden"
        ),
        pytest.param(
            '[output.elements.level]\nshow = ["info", "warning"]\nprefix = "("\nsuffix = ")"\n',
            ["info", "plain", "ok"],
            "[session] (Info) ok",
            id="level-shown-with-delimiters",
        ),
        pytest.param(
            "[output.elements.level]\nenabled = false\n", ["error", "plain", "boom"], "[session] boom", id="level-off"
        ),
        pytest.param(
            '[output]\norder = ["level", "tag", "description"]\n',
            ["error", "plain", "boom"],
            "Error: [session] boom",
            id="level-first",
        ),
    ],
)
def test_given_output_config_when_ai_internal_emit_runs_then_the_reply_follows_the_configured_elements(
    config, args, expected, capsys, monkeypatch
):
    _write_config(config)

    out, err = _internal_emit(capsys, monkeypatch, "session", *args)

    assert out == f"{expected}\n"
    assert err == ""


# --- colour: configured on a terminal, never on a pipe ----------------------------

_COLOUR_CONFIG = (
    "[output.elements.tag.styles]\n"
    'transport = "bold magenta"\n'
    "[output.elements.level.styles]\n"
    'warning = "underline red"\n'
)


def _cells(screen, row_index: int, text: str) -> list:
    row = "".join(screen.buffer[row_index][column].data for column in range(screen.columns))
    start = row.index(text)
    return [screen.buffer[row_index][column] for column in range(start, start + len(text))]


@posix_pty
def test_given_configured_colours_when_ai_c_r_reattaches_on_a_pty_then_each_element_takes_its_colour(tmp_path):
    _write_config(_COLOUR_CONFIG)
    terminal = Terminal(columns=400)
    try:
        # A stale launcher makes the transport print a warning, so the level element shows.
        _reattach_with_stale_launcher(tmp_path, terminal)
        screen = terminal.screen()
    finally:
        terminal.close()

    rows = _rows(screen)
    warning_row = next(index for index, row in enumerate(rows) if row.startswith("[transport] Warning: "))
    dolt_row = next(index for index, row in enumerate(rows) if row.startswith("[dolt_server] healthy"))
    assert all(cell.fg == "magenta" and cell.bold for cell in _cells(screen, warning_row, "[transport]"))
    assert all(cell.fg == "red" and cell.underscore for cell in _cells(screen, warning_row, "Warning:"))
    # A tag with no override keeps the element's own style.
    assert all(cell.fg == "blue" and cell.bold for cell in _cells(screen, dolt_row, "[dolt_server]"))


def test_given_configured_colours_when_ai_c_r_writes_to_a_pipe_then_no_escape_is_written(tmp_path):
    _write_config(_COLOUR_CONFIG)
    sink = io.StringIO()

    code = _launch(tmp_path, attempts=2, ssh=lambda *_a, **_k: 255, stdout=sink, stderr=sink)

    assert code == 255
    written = sink.getvalue()
    assert any(line.startswith("[transport] Connection lost") for line in written.splitlines()), written
    assert "\x1b" not in written


def test_given_a_configured_style_when_ai_internal_emit_targets_a_terminal_then_the_reply_carries_it(
    capsys, monkeypatch
):
    _write_config('[output.elements.tag]\nstyle = "italic green"\n')

    out, _ = _internal_emit(capsys, monkeypatch, "session", "info", "terminal", "styled")

    assert out == "\x1b[3;32m[session]\x1b[0m styled\n"


# --- call-time reading ------------------------------------------------------------


def test_given_a_config_edit_between_reconnect_attempts_when_ai_c_r_runs_then_the_next_line_uses_it(tmp_path):
    _write_config("")
    sink = io.StringIO()
    attempts = []

    def ssh(*_args, **_kwargs):
        if not attempts:
            _write_config('[output.elements.tag]\nprefix = "<"\nsuffix = ">"\n')
        attempts.append(1)
        return 255

    code = _launch(tmp_path, attempts=2, ssh=ssh, stdout=sink, stderr=sink)

    assert code == 255
    lines = sink.getvalue().splitlines()
    assert any(line.startswith("[dolt_server] healthy") for line in lines), lines
    assert any(line.startswith("<transport> Connection lost") for line in lines), lines
    assert not any(line.startswith("[transport] ") for line in lines), lines


def test_given_a_config_edit_between_two_ai_internal_emits_when_run_in_one_process_then_the_second_uses_it(
    capsys, monkeypatch
):
    _write_config("")
    first, _ = _internal_emit(capsys, monkeypatch, "session", "info", "plain", "one")
    _write_config('[output.elements.tag]\nprefix = "<"\nsuffix = ">"\n')

    second, _ = _internal_emit(capsys, monkeypatch, "session", "info", "plain", "two")

    assert (first, second) == ("[session] one\n", "<session> two\n")


def test_given_an_output_config_when_the_session_script_emits_then_the_rendered_line_follows_it(tmp_path):
    if os.name != "posix":
        pytest.skip("the session supervisor is a POSIX shell script")
    from test_stale_session_reaper import _finish_supervisor, _start_generated_supervisor

    _write_config('[output.elements.tag]\nprefix = "<"\nsuffix = ">"\n')
    # Every other internal action keeps the harness stub's answers; `emit` reaches the real CLI,
    # in its own process, as every message of the generated script does.
    ai_stub = (
        "#!/bin/sh\n"
        'if [ "$1" = internal ] && [ "$2" = emit ]; then\n'
        f'  PYTHONPATH={REPO / "src"} exec {sys.executable} -m ai_cli.main "$@"\n'
        "fi\n"
        "exit 0\n"
    )
    process, _, _ = _start_generated_supervisor(
        tmp_path, "/bin/bash", terminal_pgid="456", supervisor_pgid="123", extra_commands={"ai": ai_stub}
    )

    _, stderr = _finish_supervisor(process)

    reason = "supervisor is not in the terminal foreground process group"
    assert f"<session> stale-session reaper evidence disabled: {reason}\n" in stderr, stderr


def test_given_an_unchanged_config_when_1000_records_render_then_it_is_parsed_once(capsys):
    _write_config('[output.elements.tag]\nprefix = "<"\nsuffix = ">"\n')
    _out.emit(Tag.SESSION, "warm", err=True)

    with patch.object(tomllib, "loads", wraps=tomllib.loads) as loads:
        for index in range(1000):
            _out.emit(Tag.SESSION, f"record {index}", err=True)
        assert loads.call_count == 0
        _write_config('[output.elements.tag]\nprefix = "{{"\nsuffix = "}"\n')
        _out.emit(Tag.SESSION, "after the edit", err=True)
        assert loads.call_count == 1

    lines = capsys.readouterr().err.splitlines()
    assert lines[1000] == "<session> record 999"
    assert lines[-1] == "{{session} after the edit"


# --- refusal: an invalid table is set aside whole, naming path and key ------------

_INVALID = [
    pytest.param("[output.elements.tag]\ncolour = 'red'\n", ["output.elements.tag.colour"], id="unknown-key"),
    pytest.param("[output.elements.host]\nenabled = true\n", ["output.elements.host"], id="unknown-element"),
    pytest.param("[output]\norder = ['tag', 'host', 'description']\n", ["output.order"], id="order-unknown"),
    pytest.param("[output]\norder = ['tag', 'tag', 'description']\n", ["output.order"], id="order-twice"),
    pytest.param("[output]\norder = ['tag', 'operation']\n", ["output.order"], id="order-without-description"),
    pytest.param("[output]\nseparator = 3\n", ["output.separator"], id="wrong-type"),
    pytest.param(
        # Continues the valid [output.elements.description] table the test writes first.
        "enabled = false\n",
        ["output.elements.description.enabled"],
        id="description-disabled",
    ),
    pytest.param("[output.elements.tag]\npattern = '^[a-z'\n", ["output.elements.tag.pattern"], id="bad-regex"),
    pytest.param(
        "[output.elements.tag]\npattern = '^[a-z]+$'\n", ["output.elements.tag.pattern"], id="tag-fails-pattern"
    ),
    pytest.param("[output.elements.tag]\nstyle = 'bold blu'\n", ["output.elements.tag.style"], id="bad-style"),
    pytest.param(
        "[output.elements.tag.styles]\nnosuchtag = 'red'\n", ["output.elements.tag.styles.nosuchtag"], id="style-key"
    ),
    pytest.param(
        "[output.elements.level.styles]\nwarning = 'not-a-colour'\n",
        ["output.elements.level.styles.warning"],
        id="bad-level-style",
    ),
    pytest.param("[output.elements.level]\nshow = ['loud']\n", ["output.elements.level.show"], id="unknown-level"),
    pytest.param(
        "[output.elements.operation]\nvocabulary = ['start']\n",
        ["output.elements.operation.vocabulary"],
        id="vocabulary-missing-shipped",
    ),
    pytest.param(
        "[output.elements.operation]\npattern = '^[a-z]+$'\n",
        ["output.elements.operation.vocabulary"],
        id="vocabulary-entry-fails-pattern",
    ),
    pytest.param(
        "[output.elements.operation]\ndefault = 'Not Valid'\n",
        ["output.elements.operation.default"],
        id="operation-default",
    ),
    pytest.param(
        "[output.elements.datetime]\nformat = '%Y %Q'\n", ["output.elements.datetime.format"], id="datetime-directive"
    ),
    pytest.param("[output.elements.datetime]\nformat = ''\n", ["output.elements.datetime.format"], id="datetime-empty"),
    pytest.param(
        "[output.elements.datetime]\nformat = '%H:%M'\n",
        ["output.elements.datetime.format"],
        id="datetime-fails-pattern",
    ),
    pytest.param(
        "[output.elements.datetime]\ntimezone = 'Mars/Olympus_Mons'\n",
        ["output.elements.datetime.timezone"],
        id="datetime-timezone",
    ),
    pytest.param(
        "[output.elements.datetime]\nenabled = true\n",
        ["output.elements.datetime.enabled"],
        id="datetime-not-supported-yet",
    ),
    pytest.param(
        "[output.elements.operation]\nrequired = true\n",
        ["output.elements.operation.required"],
        id="operation-required-not-supported-yet",
    ),
    pytest.param(
        "[output]\nseparator = 3\n[output.elements.tag]\nstyle = 'bold blu'\n",
        ["output.separator", "output.elements.tag.style"],
        id="two-errors",
    ),
]


@pytest.mark.parametrize(("config", "keys"), _INVALID)
def test_given_invalid_output_config_when_ai_c_dry_runs_then_defaults_render_and_path_and_key_are_named(
    config, keys, capsys
):
    # A valid key beside the invalid one: none of the table may apply.
    path = _write_config('[output.elements.description]\nprefix = ">"\n' + config)

    code, out, err = _dry_run(capsys)

    assert code == 0
    assert f"[launch] {START}" in err.splitlines(), err
    assert f"[plan] {PLAN}" in out.splitlines(), out
    errors = CONFIG_ERROR.findall(err)
    assert [error_path for error_path, _ in errors] == [str(path)] * len(keys), err
    assert sorted(rest.split(": ", 1)[0] for _, rest in errors) == sorted(keys), err


@pytest.mark.parametrize(
    "broken",
    [
        pytest.param(lambda path: path.write_text("[output\nseparator = ' '\n", encoding="utf-8"), id="not-toml"),
        pytest.param(lambda path: path.write_bytes(b"\xff\xfe[output]\n"), id="not-utf8"),
        pytest.param(lambda path: path.write_text("output = 'a string'\n", encoding="utf-8"), id="not-a-table"),
    ],
)
def test_given_unparseable_config_when_ai_c_runs_then_defaults_render_with_one_error_line(broken, capsys):
    path = _config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    broken(path)

    code, out, err = _dry_run(capsys)

    assert code == 0
    assert f"[launch] {START}" in err.splitlines(), err
    assert f"[plan] {PLAN}" in out.splitlines(), out
    assert [error_path for error_path, _ in CONFIG_ERROR.findall(err)] == [str(path)], err
    assert "Traceback" not in out + err


def test_given_a_persisting_invalid_config_when_many_records_render_then_the_error_prints_once(capsys):
    _write_config("[output.elements.tag]\nstyle = 'bold blu'\n")

    code, out, err = _dry_run(capsys)
    for index in range(5):
        _out.emit(Tag.SESSION, f"later {index}", err=True)

    later = capsys.readouterr().err
    assert code == 0
    assert len((out + err).splitlines()) > 5
    assert len(CONFIG_ERROR.findall(err + later)) == 1, err + later
    assert "[session] later 4" in later.splitlines()


# --- the no-config default is T-2's output, byte for byte -------------------------

_AT = datetime(2026, 7, 1, 18, 30, tzinfo=UTC)
_T2_GOLDEN = [
    (
        Record(at=_AT, tag=Tag.TRANSPORT, text="connection lost", level=Level.WARNING),
        "\x1b[1;34m[transport]\x1b[0m \x1b[1;33mWarning:\x1b[0m connection lost",
        "[transport] Warning: connection lost",
    ),
    (
        Record(at=_AT, tag=Tag.DOLT_SERVER, text="healthy (socket accepted)"),
        "\x1b[1;34m[dolt_server]\x1b[0m healthy (socket accepted)",
        "[dolt_server] healthy (socket accepted)",
    ),
    (
        Record(at=_AT, tag=Tag.SESSION, text="boom", level=Level.ERROR, op="reload"),
        "\x1b[1;34m[session]\x1b[0m \x1b[1;36mreload:\x1b[0m \x1b[1;31mError:\x1b[0m boom",
        "[session] reload: Error: boom",
    ),
    (
        Record(at=_AT, tag=Tag.LAUNCH, text="created /x (12.4s)", label="Worktree"),
        "\x1b[1;34m[launch]\x1b[0m \x1b[1;36mWorktree:\x1b[0m created /x (12.4s)",
        "[launch] Worktree: created /x (12.4s)",
    ),
    (
        Record(at=_AT, tag=Tag.LAUNCH, text="handing off", label="Ready", label_style="bold green"),
        "\x1b[1;34m[launch]\x1b[0m \x1b[1;32mReady:\x1b[0m handing off",
        "[launch] Ready: handing off",
    ),
    (
        Record(at=_AT, tag=Tag.LAUNCH, text="failed", label="Sync", text_style="bold red"),
        "\x1b[1;34m[launch]\x1b[0m \x1b[1;36mSync:\x1b[0m \x1b[1;31mfailed\x1b[0m",
        "[launch] Sync: failed",
    ),
    (
        Record(at=_AT, tag=Tag.LAUNCH, text="more", label="Sync", level=Level.WARNING, continued=True, op="plan"),
        "\x1b[1;34m[launch]\x1b[0m \x1b[1;36mplan:\x1b[0m more",
        "[launch] plan: more",
    ),
    (
        Record(at=_AT, tag=Tag.LAUNCH, text="", label="Phase"),
        "\x1b[1;34m[launch]\x1b[0m \x1b[1;36mPhase:\x1b[0m",
        "[launch] Phase:",
    ),
    (
        Record(at=_AT, tag=Tag.LOG, text="detail line", level=Level.DETAIL),
        "\x1b[1;34m[log]\x1b[0m detail line",
        "[log] detail line",
    ),
]


@pytest.mark.parametrize(("record", "styled", "plain"), _T2_GOLDEN, ids=lambda value: None)
def test_given_no_output_config_when_a_record_renders_then_it_matches_the_t2_bytes(record, styled, plain):
    # Captured from the T-2 renderer before the config existed. Every terminal line is
    # this renderer's output (the T-2 enforcement tests compare the wire to it).
    assert not _config_file().exists()

    assert _out.render_record(record, color=True, stream=io.StringIO()) == styled
    assert _out.render_record(record, color=False) == plain


def test_given_no_output_config_when_ai_internal_emit_targets_a_terminal_then_it_matches_the_t2_bytes(
    capsys, monkeypatch
):
    out, err = _internal_emit(capsys, monkeypatch, "session", "warning", "terminal", "lost")

    assert (out, err) == ("\x1b[1;34m[session]\x1b[0m \x1b[1;33mWarning:\x1b[0m lost\n", "")


# --- documentation ----------------------------------------------------------------


def _flatten(table: dict, prefix: str = "") -> set[str]:
    keys = set()
    for key, value in table.items():
        dotted = f"{prefix}{key}"
        keys.add(dotted)
        if isinstance(value, dict):
            keys |= _flatten(value, f"{dotted}.")
    return keys


def test_given_the_output_schema_when_compared_to_the_readme_then_every_key_is_documented():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    section = readme.split("### Output configuration", 1)[1].split("\n## ", 1)[0].split("\n### ", 1)[0]
    block = re.search(r"```toml\n(?P<body>.*?)```", section, re.DOTALL)
    assert block is not None, "the Output configuration section has no TOML block"
    shipped = tomllib.loads((REPO / "src" / "ai_cli" / "data" / "output.toml").read_text(encoding="utf-8"))

    documented = _flatten(tomllib.loads(block["body"]))

    assert documented == _flatten(shipped)
    # Every schema key is also explained in prose; the entries of a `styles` map are examples.
    for key in sorted(key for key in _flatten(shipped["output"]) if ".styles." not in key):
        assert f"`{key.rsplit('.', 1)[-1]}`" in section, key


# --- cost: with no user table, a record never pays for validation -----------------

_IMPORT_PROBE = """
import sys
sys.path.insert(0, sys.argv[1])
sys.argv = ["ai", *sys.argv[2:]]
from ai_cli.main import cli
try:
    cli()
except SystemExit:
    pass
loaded = sorted(name for name in ("pydantic", "ai_cli.output_validation") if name in sys.modules)
sys.__stderr__.write(f"LOADED {','.join(loaded) or '-'}\\n")
"""


@pytest.mark.parametrize(
    ("config", "loaded"),
    [
        pytest.param(None, "-", id="no-config-file"),
        pytest.param("", "-", id="no-output-table"),
        pytest.param("[output]\nseparator = ' '\n", "ai_cli.output_validation,pydantic", id="user-table"),
    ],
)
def test_given_no_user_output_table_when_ai_internal_emit_runs_then_validation_is_not_imported(
    config, loaded, tmp_path
):
    home = tmp_path / "home"
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT"}}
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        XDG_STATE_HOME=str(home / "state"),
        XDG_CONFIG_HOME=str(home / "config"),
        XDG_CACHE_HOME=str(home / "cache"),
        LOCALAPPDATA=str(home / "state"),
        APPDATA=str(home / "appdata"),
    )
    if config is not None:
        with patch.dict(os.environ, env, clear=True):
            path = _config_file()
        path.parent.mkdir(parents=True)
        path.write_text(MACHINE + config, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE, str(REPO / "src"), "internal", "emit", "session", "info", "plain", "hi"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.stdout == "[session] hi\n", result.stderr
    assert f"LOADED {loaded}\n" in result.stderr, result.stderr
