"""Through the real `ai` entry point, every log line carries a configurable datetime (AI-CLI-fx4b).

The ask: a datetime element on every line, visually distinct from the type tag, by
default 24-hour `YYYY-MM-DD HH:MM:SS <TZ>` in US Eastern via datetime/zoneinfo. Its
format, timezone, delimiters and colour come from the `[output]` table, and an
invalid setting is refused at load with the file path. The clock is frozen by
replacing the output module's `datetime` with a subclass whose `now()` is fixed.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import time
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest
from test_output_config import _T2_GOLDEN, CONFIG_ERROR, PLAN, START, _dry_run, _internal_emit, _write_config

from ai_cli import output as _out
from ai_cli.output import Tag
from ai_cli.session_script import get_engine_script

pytestmark = pytest.mark.shipped_output_datetime

SUMMER = "2026-07-01 14:30:00 EDT"
WINTER = "2026-01-15 13:30:00 EST"


class _Clock(datetime):
    """A `datetime` whose `now()` returns the queued instants in turn, the last one repeating."""

    instants: ClassVar[list[datetime]] = []

    @classmethod
    def now(cls, tz=None):
        instant = cls.instants.pop(0) if len(cls.instants) > 1 else cls.instants[0]
        return instant if tz is None else instant.astimezone(tz)


def _at(year: int, month: int, day: int, hour: int, minute: int, second: int = 0) -> datetime:
    return _Clock(year, month, day, hour, minute, second, tzinfo=UTC)


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(_out, "datetime", _Clock)

    def freeze(*instants: datetime) -> None:
        _Clock.instants = list(instants)

    freeze(_at(2026, 7, 1, 18, 30))
    return freeze


@pytest.fixture(autouse=True)
def _plain_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")


def _lines(out: str, err: str) -> list[str]:
    return [line for line in (out + err).splitlines() if line.strip()]


@pytest.mark.parametrize(
    ("instant", "shown"),
    [
        pytest.param(_at(2026, 7, 1, 18, 30), SUMMER, id="summer-EDT"),
        pytest.param(_at(2026, 1, 15, 18, 30), WINTER, id="winter-EST"),
    ],
)
def test_given_default_config_when_ai_c_dry_runs_then_each_line_starts_with_eastern_time(instant, shown, clock, capsys):
    clock(instant)

    code, out, err = _dry_run(capsys)

    assert code == 0
    lines = _lines(out, err)
    assert f"{shown} [launch] start: {START}" in lines, err
    assert f"{shown} [plan] plan: {PLAN}" in lines, out
    assert all(line.startswith(f"{shown} [") for line in lines), lines


_TOKYO = pytest.mark.skipif(not hasattr(time, "tzset"), reason="the host zone can be set per test only on POSIX")


@pytest.fixture
def host_zone_tokyo():
    # Its own MonkeyPatch, so restoring TZ (and re-reading it) reverts nothing else.
    with pytest.MonkeyPatch.context() as zone:
        zone.setenv("TZ", "Asia/Tokyo")
        time.tzset()
        yield
    time.tzset()


@pytest.mark.parametrize(
    ("config", "prefix"),
    [
        pytest.param("[output.elements.datetime]\nenabled = true\n", f"{SUMMER} [launch]", id="enabled-explicitly"),
        pytest.param("[output.elements.datetime]\nenabled = false\n", "[launch]", id="disabled"),
        pytest.param(
            "[output.elements.datetime]\ntimezone = 'UTC'\n", "2026-07-01 18:30:00 UTC [launch]", id="timezone-utc"
        ),
        pytest.param(
            "[output.elements.datetime]\ntimezone = 'Europe/Berlin'\n",
            "2026-07-01 20:30:00 CEST [launch]",
            id="timezone-iana",
        ),
        pytest.param(
            "[output.elements.datetime]\ntimezone = 'local'\n",
            "2026-07-02 03:30:00 JST [launch]",
            id="timezone-local",
            marks=_TOKYO,
        ),
        pytest.param(
            "[output.elements.datetime]\nformat = '%H:%M:%S'\npattern = '^\\d{2}:\\d{2}:\\d{2}$'\n",
            "14:30:00 [launch]",
            id="format",
        ),
        pytest.param(
            "[output.elements.datetime]\nprefix = '('\nsuffix = ')'\n", f"({SUMMER}) [launch]", id="delimiters"
        ),
        pytest.param('[output]\norder = ["tag", "datetime", "description"]\n', f"[launch] {SUMMER}", id="order"),
    ],
)
def test_given_datetime_overrides_when_ai_c_dry_runs_then_the_datetime_follows_them(
    config, prefix, clock, capsys, request
):
    if "'local'" in config:
        request.getfixturevalue("host_zone_tokyo")
    _write_config(config)

    code, out, err = _dry_run(capsys)

    assert code == 0
    assert not CONFIG_ERROR.search(err), err
    launch = [line for line in err.splitlines() if "[launch]" in line]
    assert launch, err
    assert all(line.startswith(f"{prefix} ") for line in launch), launch


@pytest.mark.parametrize(
    ("config", "reply"),
    [
        pytest.param(
            "", f"\x1b[2m{SUMMER}\x1b[0m \x1b[1;34m[session]\x1b[0m \x1b[1;36mreload:\x1b[0m hi\n", id="default-dim"
        ),
        pytest.param(
            "[output.elements.datetime]\nstyle = 'italic magenta'\n",
            f"\x1b[3;35m{SUMMER}\x1b[0m \x1b[1;34m[session]\x1b[0m \x1b[1;36mreload:\x1b[0m hi\n",
            id="configured",
        ),
    ],
)
def test_given_a_datetime_style_when_ai_internal_emit_targets_a_terminal_then_the_datetime_takes_it(
    config, reply, clock, capsys, monkeypatch
):
    _write_config(config)

    out, err = _internal_emit(capsys, monkeypatch, "session", "info", "reload", "terminal", "hi")

    assert (out, err) == (reply, "")


@pytest.mark.parametrize(
    ("setting", "key"),
    [
        pytest.param("format = '%Y-%m-%d %Q'", "output.elements.datetime.format", id="unsupported-directive"),
        pytest.param("format = ''", "output.elements.datetime.format", id="renders-empty"),
        pytest.param("format = '%d/%m/%Y'", "output.elements.datetime.format", id="fails-pattern"),
        pytest.param("timezone = 'Atlantis/Lost_City'", "output.elements.datetime.timezone", id="unknown-zone"),
        pytest.param("timezone = 'Eastern'", "output.elements.datetime.timezone", id="not-an-iana-name"),
    ],
)
def test_given_an_invalid_datetime_setting_when_loaded_then_it_is_refused_with_path_and_key(
    setting, key, clock, capsys
):
    # A valid datetime key beside the invalid one: none of the table may apply.
    path = _write_config(f"[output.elements.datetime]\nprefix = '<'\n{setting}\n")

    code, out, err = _dry_run(capsys)

    assert code == 0
    assert [(error_path, rest.split(": ", 1)[0]) for error_path, rest in CONFIG_ERROR.findall(_strip(err))] == [
        (str(path), key)
    ], err
    assert f"{SUMMER} [launch] start: {START}" in err.splitlines(), err
    assert f"{SUMMER} [plan] plan: {PLAN}" in out.splitlines(), out


def _strip(err: str) -> str:
    """``err`` with each line's leading default datetime removed, so CONFIG_ERROR anchors on the tag."""
    return "\n".join(line.removeprefix(f"{SUMMER} ") for line in err.splitlines())


def test_given_a_multi_line_message_when_emitted_then_every_line_has_the_same_time(clock, capsys):
    # Each clock read advances a second: a line that read the clock itself would show a later time.
    clock(*(_at(2026, 7, 1, 18, 30) + timedelta(seconds=step) for step in range(5)))

    _out.emit(Tag.LAUNCH, "start", "first line\nsecond line\nthird line", err=True)

    lines = capsys.readouterr().err.splitlines()
    assert lines == [f"{SUMMER} [launch] start: {text}" for text in ("first line", "second line", "third line")]


_RELAY = re.compile(r"^ *exec 2> >\(while IFS= read -r _ai_log_line; do$.*?^ *done\)$", re.MULTILINE | re.DOTALL)


@pytest.mark.skipif(os.name != "posix", reason="the session supervisor is a POSIX shell script")
def test_given_a_datetime_led_record_on_supervisor_stderr_when_relayed_then_it_is_not_tagged_again(tmp_path):
    script = get_engine_script("c", "session-1", "test-session", "test-", "myproject", launch_log_path="unused")
    relay = _RELAY.search(script)
    assert relay is not None, "the generated script has no supervisor stderr relay"
    harness = (
        f"_ai_launch_log={tmp_path / 'launch.log'}\nexec 3>&1\n{relay.group(0)}\n"
        f"printf '%s\\n' '{SUMMER} [launch] ready' '[update] ok' 'a bare child line' >&2\n"
    )

    result = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, timeout=30, check=False)

    assert result.stdout.splitlines() == [
        f"{SUMMER} [launch] ready",
        "[update] ok",
        "[session] session: a bare child line",
    ]


@pytest.mark.skipif(os.name != "posix", reason="the session supervisor is a POSIX shell script")
def test_given_no_ai_entry_point_when_the_session_script_emits_then_the_plain_line_leads_with_eastern_time(tmp_path):
    from test_stale_session_reaper import _finish_supervisor, _start_generated_supervisor

    # The entry point exists but cannot render, as while `uv tool install` rewrites it.
    ai_stub = '#!/bin/sh\nif [ "$1" = internal ] && [ "$2" = emit ]; then exit 127; fi\nexit 0\n'
    process, _, _ = _start_generated_supervisor(
        tmp_path, "/bin/bash", terminal_pgid="456", supervisor_pgid="123", extra_commands={"ai": ai_stub}
    )

    _, stderr = _finish_supervisor(process)

    reason = "supervisor is not in the terminal foreground process group"
    stamped = re.compile(
        rf"^\d{{4}}-\d{{2}}-\d{{2}} \d{{2}}:\d{{2}}:\d{{2}} E[DS]T \[session\] session: "
        rf"stale-session reaper evidence disabled: {reason}$",
        re.MULTILINE,
    )
    assert stamped.search(stderr), stderr


@pytest.mark.parametrize(("record", "styled", "plain"), _T2_GOLDEN, ids=lambda value: None)
def test_given_no_output_config_when_a_record_renders_then_the_datetime_leads_the_t2_bytes(record, styled, plain):
    # Every golden record is at 2026-07-01T18:30Z, so the default renders it in EDT.
    assert _out.render_record(record, color=True, stream=io.StringIO()) == f"\x1b[2m{SUMMER}\x1b[0m {styled}"
    assert _out.render_record(record, color=False) == f"{SUMMER} {plain}"
