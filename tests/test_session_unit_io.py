"""Shared service-unit writes preserve the previous file on failure."""

from pathlib import Path

import pytest

from ai_cli.session_publish_timer import quote_systemd, write_unit


def test_given_unchanged_bytes_when_unit_written_then_no_replace_occurs(tmp_path, monkeypatch):
    path = tmp_path / "example.service"
    path.write_bytes(b"previous unit")
    monkeypatch.setattr(Path, "replace", lambda *args: pytest.fail("unexpected replace"))
    write_unit(path, b"previous unit")


def test_given_replace_failure_when_unit_written_then_old_bytes_survive_without_temporary_file(tmp_path, monkeypatch):
    path = tmp_path / "example.service"
    path.write_bytes(b"previous unit")

    def fail(*args):
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="replace failed"):
        write_unit(path, b"new unit")
    assert path.read_bytes() == b"previous unit"
    assert list(tmp_path.iterdir()) == [path]


def test_given_special_characters_when_systemd_quoted_then_literal_values_are_preserved():
    assert quote_systemd('a\\b"c%d\ne') == '"a\\\\b\\"c%%d\\ne"'
