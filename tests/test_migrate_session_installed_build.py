"""``ai migrate-session`` updates an installed ``ai`` too old for the migration it writes, or refuses.

The migrated session relaunches under the ``ai`` on ``PATH``. These tests run the real
probe against a stand-in for that binary: every ``subprocess.run`` naming it is answered
here, and everything else (git included) runs as usual.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from test_session_migrate import NEW, OLD, _everything, _migrate, run_cli, slot  # noqa: F401  (slot is a fixture)

from ai_cli import main
from ai_cli.session_migrate import MIGRATION_FORMAT

REAL_PROBE = main._installed_migration_format
FAKE_AI = str(Path("/opt/example/bin/ai"))
UPDATE = [FAKE_AI, "update", "--force", "--quiet"]


class FakeAi:
    """The installed ``ai``: ``format`` is what it reads, ``None`` for a build without the probe."""

    def __init__(self, fmt: int | None, *, update_code: int = 0) -> None:
        self.format = fmt
        self.update_code = update_code
        self.calls: list[list[str]] = []

    def run(self, cmd: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(list(cmd))
        if cmd[1:] == ["internal", "get-migration-format"]:
            if self.format is None:
                stderr = "Usage: ai internal <action> [args...] (unknown action: get-migration-format)\n"
                return subprocess.CompletedProcess(cmd, 1, "", stderr)
            return subprocess.CompletedProcess(cmd, 0, f"{self.format}\n", "")
        if cmd == UPDATE:
            if self.update_code == 0:
                self.format = MIGRATION_FORMAT
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, self.update_code, "", "error: source tree not found\n")
        raise AssertionError(f"unexpected call to the installed ai: {cmd}")


@pytest.fixture
def installed(monkeypatch):
    """Install a :class:`FakeAi` on ``PATH``; the test sets its format."""
    monkeypatch.setattr(main, "_installed_migration_format", REAL_PROBE)
    fake = FakeAi(None)
    real_which = shutil.which
    inner_run = subprocess.run

    def which(name, *args, **kwargs):
        return FAKE_AI if name == "ai" else real_which(name, *args, **kwargs)

    def run(*args, **kwargs):
        cmd = args[0] if args else kwargs["args"]
        if isinstance(cmd, list | tuple) and cmd and cmd[0] == FAKE_AI:
            return fake.run(list(cmd))
        return inner_run(*args, **kwargs)

    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(subprocess, "run", run)
    return fake


def _ledger(slot) -> list[dict]:  # noqa: F811
    path = slot["state"] / "session-migrations.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_given_an_installed_build_older_than_the_format_when_migrated_then_it_is_updated_first(slot, installed):  # noqa: F811
    code, out, err = _migrate(slot["repo"], "-y")

    assert code == 0, out + err
    assert UPDATE in installed.calls
    assert "reads migration format 0, older than format 1" in out + err
    assert (slot["repo"] / ".worktrees" / NEW).is_dir()
    rows = _ledger(slot)
    assert [bool(row.get("finished")) for row in rows] == [False, True], "the migration, then its verified finish"
    assert [row["format"] for row in rows] == [MIGRATION_FORMAT, MIGRATION_FORMAT]


def test_given_the_update_failing_when_migrated_then_it_exits_2_with_the_update_command_and_writes_nothing(
    slot,  # noqa: F811
    installed,
):
    installed.update_code = 1
    before = _everything(slot)

    code, _out, err = _migrate(slot["repo"], "-y")

    assert code == 2
    assert UPDATE in installed.calls
    assert "run `ai update --force` and repeat this command" in err
    assert "source tree not found" in err
    assert _everything(slot) == before


def test_given_an_older_installed_build_when_dry_run_then_the_skew_is_named_and_nothing_is_updated_or_written(
    slot,  # noqa: F811
    installed,
):
    before = _everything(slot)

    code, out, err = _migrate(slot["repo"], "--dry-run")

    assert code == 0, out + err
    assert "dry run: the installed `ai`" in out and "reads migration format 0" in out
    assert UPDATE not in installed.calls
    assert _everything(slot) == before


def test_given_no_ai_on_path_when_migrated_then_it_refuses_without_updating(slot, installed, monkeypatch):  # noqa: F811
    monkeypatch.setattr(shutil, "which", lambda name, *args, **kwargs: None)
    before = _everything(slot)

    code, _out, err = _migrate(slot["repo"], "-y")

    assert code == 2
    assert "could not read the migration format of the installed `ai` (none on PATH)" in err
    assert installed.calls == []
    assert _everything(slot) == before


def test_given_a_current_installed_build_when_migrated_and_finished_then_nothing_updates_and_every_row_has_the_format(
    slot,  # noqa: F811
    installed,
):
    installed.format = MIGRATION_FORMAT

    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    code, out, err = _migrate(slot["repo"], "--finish", "-y")

    assert code == 0, out + err
    assert UPDATE not in installed.calls
    rows = _ledger(slot)
    assert any(row.get("finished") for row in rows)
    assert {row["format"] for row in rows} == {MIGRATION_FORMAT}


def test_given_this_build_when_asked_for_its_migration_format_then_it_prints_the_number():
    code, out, _err = run_cli(["ai", "internal", "get-migration-format"])

    assert (code, out.strip()) == (0, str(MIGRATION_FORMAT))
