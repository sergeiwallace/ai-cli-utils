"""After a completed ``ai migrate-session``, every remaining reference to the old name is named.

A completed migration runs the configured ``[migrate_session] references_command`` once with
``--old OLD --new NEW``, then scans the migrated repository's tracked files for the old name.
Each reference left is printed as ``file:line`` and the command exits 1, reporting the
migration ``finished with leftovers``; a file matching ``references_allow`` is not a leftover.
Each test drives the CLI on the fixture slot of ``test_session_migrate``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import test_session_migrate
from test_session_migrate import NEW, OLD, _git, run_cli

slot = test_session_migrate.slot

RECORDER = """\
import json, sys
with open({log!r}, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
{body}
"""


def _migrate(repo: Path, *flags: str, config: dict | None = None):
    with (
        patch("ai_cli.session._tmux_session_names", return_value=[]),
        patch("ai_cli.config._get_projects_dir", return_value=repo.parent),
    ):
        return run_cli(["ai", "migrate-session", OLD, NEW, "-r", str(repo), *flags], config=config)


def _commit(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _git("add", path, cwd=repo)
    _git("commit", "-qm", f"add {path}", cwd=repo)


def _command(tmp_path: Path, body: str = "") -> tuple[dict, Path]:
    """A references command that logs each argv it gets, then runs ``body``."""
    log = tmp_path / "references-calls.jsonl"
    script = tmp_path / "sweep_references.py"
    script.write_text(RECORDER.format(log=str(log), body=body), encoding="utf-8")
    return {"migrate_session": {"references_command": [sys.executable, str(script)]}}, log


def _calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_given_the_old_name_in_a_tracked_code_file_when_migrated_then_it_names_file_line_and_exits_1(slot):
    _commit(slot["repo"], "src/escalate.py", f'TARGET = "reach"\nSESSION = "c-{OLD}"\n')

    code, out, err = _migrate(slot["repo"], "-y")

    assert code == 1, out + err
    assert "src/escalate.py:2" in out + err
    assert f"{OLD} -> {NEW}: finished with leftovers" in out + err
    assert (slot["repo"] / ".worktrees" / NEW).is_dir()


def test_given_the_old_name_only_in_an_allowlisted_file_when_migrated_then_it_finishes_and_exits_0(slot):
    _commit(slot["repo"], "docs/renames.md", f"| {OLD} | {NEW} |\n")
    config = {"migrate_session": {"references_allow": ["docs/renames.md"]}}

    code, out, err = _migrate(slot["repo"], "-y", config=config)

    assert code == 0, out + err
    assert "docs/renames.md:1" not in out + err
    assert f"{OLD} -> {NEW}: finished" in out + err
    assert "finished with leftovers" not in out + err


def test_given_a_name_that_only_shares_a_prefix_when_migrated_then_it_is_not_a_reference(slot):
    _commit(slot["repo"], "src/other.py", f'SESSION = "{OLD}0"\n')

    code, out, err = _migrate(slot["repo"], "-y")

    assert code == 0, out + err
    assert "src/other.py" not in out + err


def test_given_a_references_command_when_migrated_then_it_runs_once_with_old_and_new(slot, tmp_path):
    config, log = _command(tmp_path)

    code, out, err = _migrate(slot["repo"], "-y", config=config)

    assert code == 0, out + err
    assert _calls(log) == [["--old", OLD, "--new", NEW]]
    assert f"{OLD} -> {NEW}: finished" in out + err


def test_given_a_references_command_that_exits_non_zero_when_migrated_then_it_reports_leftovers_and_exits_1(
    slot, tmp_path
):
    config, log = _command(tmp_path, 'sys.stderr.write("could not open the rename map\\n")\nsys.exit(3)')

    code, out, err = _migrate(slot["repo"], "-y", config=config)

    assert code == 1, out + err
    assert len(_calls(log)) == 1
    assert f"{OLD} -> {NEW}: finished with leftovers" in out + err
    assert "exited 3" in out + err
    assert "could not open the rename map" in out + err


def test_given_a_references_command_naming_a_reference_it_left_when_migrated_then_it_is_listed_and_exits_1(
    slot, tmp_path
):
    config, _log = _command(tmp_path, 'print("migrate-reference-left: hooks/guard.sh:14")')

    code, out, err = _migrate(slot["repo"], "-y", config=config)

    assert code == 1, out + err
    assert "hooks/guard.sh:14" in out + err
    assert f"{OLD} -> {NEW}: finished with leftovers" in out + err


def test_given_no_references_and_no_command_when_migrated_then_it_finishes_as_before_and_says_the_step_was_skipped(
    slot,
):
    code, out, err = _migrate(slot["repo"], "-y")

    assert code == 0, out + err
    assert f"{OLD} -> {NEW}: finished" in out + err
    assert "finished with leftovers" not in out + err
    assert [line for line in (out + err).splitlines() if "references command: skipped" in line] != []
    rows = (slot["state"] / "session-migrations.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(row).get("verified") for row in rows if json.loads(row).get("finished")] == [True]


def test_given_a_dry_run_when_planned_then_neither_the_command_nor_the_scan_runs(slot, tmp_path):
    _commit(slot["repo"], "src/escalate.py", f'SESSION = "{OLD}"\n')
    config, log = _command(tmp_path)

    code, out, err = _migrate(slot["repo"], "-n", config=config)

    assert code == 0, out + err
    assert _calls(log) == []
    assert "src/escalate.py" not in out + err


def test_given_leftovers_fixed_when_finished_again_then_it_finishes_and_exits_0(slot):
    _commit(slot["repo"], "src/escalate.py", f'SESSION = "{OLD}"\n')
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 1, out + err
    _commit(slot["repo"], "src/escalate.py", f'SESSION = "{NEW}"\n')

    code, out, err = _migrate(slot["repo"], "--finish", "-y")

    assert code == 0, out + err
    assert f"{OLD} -> {NEW}: finished" in out + err
    assert "finished with leftovers" not in out + err
