"""ai_cli.output: the tag is required by type, every line carries it, colour is TTY-gated."""

from __future__ import annotations

import io
import logging
import subprocess
import sys

import pytest

from ai_cli import output
from ai_cli.output import Tag, Untagged

LINE = r"^\[[a-z0-9_-]+\] "


class _TtyStream(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_given_every_tag_when_defined_then_its_value_matches_the_tag_grammar():
    assert all(output.TAG_PATTERN.match(tag.value) for tag in Tag)


@pytest.mark.parametrize("bad", ["launch", None, 3, Untagged.JSON])
def test_given_a_tag_that_is_not_a_tag_member_when_emitting_then_it_is_refused(bad, capsys):
    with pytest.raises(TypeError):
        output.emit(bad, "hello")  # type: ignore[arg-type]
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("bad", ["json", None, Tag.LAUNCH])
def test_given_a_reason_that_is_not_an_untagged_member_when_writing_raw_then_it_is_refused(bad, capsys):
    with pytest.raises(TypeError):
        output.raw(bad, "{}")  # type: ignore[arg-type]
    assert capsys.readouterr() == ("", "")


def test_given_a_multi_line_message_when_emitted_then_every_line_carries_the_tag_and_blanks_are_dropped(capsys):
    output.emit(Tag.SYNC, "first\n\n  indented\nlast\n")
    assert capsys.readouterr().out == "[sync] first\n[sync]   indented\n[sync] last\n"


def test_given_a_blank_message_when_emitted_then_nothing_is_written(capsys):
    output.emit(Tag.SYNC, "")
    output.emit(Tag.SYNC, "\n  \n")
    assert capsys.readouterr() == ("", "")


def test_given_warning_and_error_when_emitted_then_they_go_to_stderr_with_their_label(capsys):
    output.warning(Tag.CONFIG, "careful")
    output.error(Tag.CONFIG, "broken\nsecond line")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "[config] Warning: careful\n[config] Error: broken\n[config] second line\n"


def test_given_an_open_line_when_another_line_is_emitted_then_the_open_line_is_ended_first(capsys):
    output.emit(Tag.COPIER, "  myproject... ", nl=False)
    output.emit(Tag.COPIER, "an interleaved line")
    output.cont(Tag.COPIER, "✓")
    assert capsys.readouterr().out == "[copier]   myproject... \n[copier] an interleaved line\n[copier] ✓\n"


def test_given_an_open_line_when_continued_then_the_text_finishes_that_tagged_line(capsys):
    output.emit(Tag.COPIER, "  myproject... ", nl=False)
    output.cont(Tag.COPIER, "✓ updated\n    detail")
    assert capsys.readouterr().out == "[copier]   myproject... ✓ updated\n[copier]     detail\n"


def test_given_a_tty_when_emitting_then_the_tag_and_label_are_styled(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    stream = _TtyStream()
    output.error(Tag.LAUNCH, "boom", file=stream)
    assert "\x1b[" in stream.getvalue()


@pytest.mark.parametrize("env", [{"NO_COLOR": "1"}, {"TERM": "dumb"}])
def test_given_a_colour_opt_out_on_a_tty_when_emitting_then_no_escape_is_written(monkeypatch, env):
    monkeypatch.delenv("NO_COLOR", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    stream = _TtyStream()
    output.error(Tag.LAUNCH, "boom", file=stream)
    assert stream.getvalue() == "[launch] Error: boom\n"


def test_given_a_non_tty_when_emitting_then_no_escape_is_written():
    stream = io.StringIO()
    output.warning(Tag.LAUNCH, "careful", file=stream)
    assert "\x1b" not in stream.getvalue()


def test_given_a_statusline_segment_with_ansi_when_written_raw_then_it_is_verbatim_and_untagged(capsys):
    output.raw(Untagged.STATUSLINE, "\x1b[2m-\x1b[0m")
    assert capsys.readouterr().out == "\x1b[2m-\x1b[0m\n"


def test_given_a_tagged_prompt_when_asked_then_the_prompt_line_carries_the_tag(monkeypatch):
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "y")
    assert output.ask(Tag.PS, "first line\nKill 2 orphan(s)? [y/N]") == "y"
    assert prompts == ["[ps] first line\n[ps] Kill 2 orphan(s)? [y/N] "]


def test_given_a_child_that_prints_its_own_name_when_relayed_then_each_line_is_tagged_once(capsys):
    script = "print('dolt_server: healthy (socket accepted)'); import sys; print('raw stderr', file=sys.stderr)"
    completed = output.run_relayed(Tag.DOLT_SERVER, [sys.executable, "-c", script])
    assert completed.returncode == 0
    lines = capsys.readouterr().err.splitlines()
    assert sorted(lines) == ["[dolt_server] healthy (socket accepted)", "[dolt_server] raw stderr"]


def test_given_a_failing_child_when_relayed_then_its_status_is_returned_and_check_is_honoured(capsys):
    completed = output.run_relayed(Tag.DOLT_SERVER, [sys.executable, "-c", "raise SystemExit(3)"])
    assert completed.returncode == 3
    with pytest.raises(subprocess.CalledProcessError):
        output.run_relayed(Tag.DOLT_SERVER, [sys.executable, "-c", "raise SystemExit(3)"], check=True)


def test_given_a_package_logger_warning_when_logged_then_it_reaches_stderr_as_a_tagged_line(capsys):
    logging.getLogger("ai_cli.stale_session_reaper").warning("stale_session_reaper reason=%s", "probe")
    assert "[log] Warning: stale_session_reaper reason=probe\n" in capsys.readouterr().err
