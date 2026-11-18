"""A ``--finish`` converges when NEW already holds its own copy of something OLD still holds.

The session has been running under NEW, so NEW wrote its own resume log and holds its own
identity. NEW is authoritative: OLD's copy is moved into a dated archive under the state
directory, the archive is recorded in the migration ledger, and the part counts as done. A
second ``--finish`` then finds nothing left. Nothing is ever deleted. A recorded pair whose
NEW slot is gone because the ledger records NEW's own later migration counts as done; one
with no such record still reads ``slot (missing: ...)``.

Every test drives the CLI on the fixture slot.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import test_session_migrate
from test_session_migrate import NEW, OLD, _finish, _migrate, run_cli

from ai_cli.config import get_xdg_config_home

slot = test_session_migrate.slot

ARCHIVE = "session-migration-archive"


def _ledger(slot) -> Path:
    return slot["state"] / "session-migrations.jsonl"


def _rows(slot) -> list[dict]:
    return [json.loads(line) for line in _ledger(slot).read_text(encoding="utf-8").splitlines()]


def _append(slot, **row: object) -> None:
    with _ledger(slot).open("a", encoding="utf-8") as ledger:
        ledger.write(json.dumps(row) + "\n")


def _moved(slot) -> None:
    """OLD migrated to NEW and recorded, with the verified row dropped: a pair left to finish."""
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    keep = [json.dumps(row) + "\n" for row in _rows(slot) if not row.get("finished")]
    _ledger(slot).write_text("".join(keep), encoding="utf-8")


def _archived(slot) -> list[dict]:
    return [entry for row in _rows(slot) if row.get("archive_recorded") for entry in row.get("archived", [])]


def _status(slot, *argv: str):
    with (
        patch("ai_cli.session._tmux_session_names", return_value=[]),
        patch("ai_cli.config._get_projects_dir", return_value=slot["repo"].parent),
    ):
        return run_cli(["ai", "migrate-session", "--status", "--all", *argv])


def _status_row(out: str, old: str) -> list[str]:
    lines = [line.partition("migrate: ")[2] for line in out.splitlines()]
    return next(line.split(" | ") for line in lines if line.startswith(f"{old} | "))


def _resume_collision(slot) -> dict[Path, bytes]:
    """NEW wrote its own resume log and outbox hand-off while OLD's copies were still on disk."""
    resume = slot["claude"] / "resume"
    files = {
        resume / "logs" / f"{OLD}.log": b"2026-01-01T00:00:00Z old line\n",
        resume / "logs" / f"{NEW}.log": b"2026-01-02T00:00:00Z new line\n",
        resume / "outbox" / f"{OLD}-1791218750.md": b"old hand-off\n",
        resume / "outbox" / f"{NEW}-1791218750.md": b"new hand-off\n",
    }
    for path, data in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return files


def test_given_new_already_has_its_resume_log_when_finished_then_old_is_archived_and_a_second_run_finds_nothing(slot):
    _moved(slot)
    resume = slot["claude"] / "resume" / "logs"
    files = _resume_collision(slot)

    code, out, err = _finish(slot["repo"], "-y")

    assert code == 0, out + err
    assert "NOT re-keyed" not in out + err
    assert (resume / f"{NEW}.log").read_bytes() == files[resume / f"{NEW}.log"]
    assert not (resume / f"{OLD}.log").exists()
    [entry] = [e for e in _archived(slot) if e["source"] == str(resume / f"{OLD}.log")]
    archive = Path(entry["archive"])
    assert archive.read_bytes() == files[resume / f"{OLD}.log"]
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    assert archive.is_relative_to(slot["state"] / ARCHIVE / day / f"{OLD}-to-{NEW}")
    assert (entry["part"], entry["kept"]) == ("resume", str(resume / f"{NEW}.log"))
    assert "archived OLD, NEW kept" in out

    code, out, err = _finish(slot["repo"], "-y")

    assert code == 0, out + err
    assert "NOT finished" not in out + err
    code, out, err = _status(slot)
    assert code == 0, out + err
    assert _status_row(out, OLD)[3] == "complete"


def test_given_every_displaced_resume_artifact_when_finished_then_the_archive_holds_each_and_nothing_is_deleted(slot):
    _moved(slot)
    files = _resume_collision(slot)
    displaced = {path: data for path, data in files.items() if path.name.startswith(OLD)}

    code, out, err = _finish(slot["repo"], "-y")

    assert code == 0, out + err
    archive = {Path(e["source"]): Path(e["archive"]) for e in _archived(slot)}
    assert set(archive) == set(displaced)
    for source, data in displaced.items():
        assert archive[source].read_bytes() == data
    for path, data in files.items():
        if path not in displaced:
            assert path.read_bytes() == data


def test_given_a_dry_run_with_a_resume_collision_when_finishing_then_it_plans_the_archive_and_writes_nothing(slot):
    _moved(slot)
    files = _resume_collision(slot)
    ledger = _ledger(slot).read_bytes()

    code, out, err = _finish(slot["repo"], "-n")

    assert "archive OLD, keep NEW" in out
    assert "NOT re-keyed" not in out + err
    assert all(path.read_bytes() == data for path, data in files.items())
    assert not (slot["state"] / ARCHIVE).exists()
    assert _ledger(slot).read_bytes() == ledger


IDENTITY_HOOK = """\
import os, sys
args = sys.argv[1:]
old, new = args[args.index("--old") + 1], args[args.index("--new") + 1]
keys, archive = {keys!r}, {archive!r}
source, kept = os.path.join(keys, old + ".key"), os.path.join(keys, new + ".key")
target = os.path.join(archive, old + ".key")
if not os.path.exists(source):
    if "--dry-run" in args:
        print("migrate-status: done")
    sys.exit(0)
if "--dry-run" in args:
    print(source + " -> " + target + " archived (NEW kept: " + kept + ")")
    print("migrate-status: pending")
    sys.exit(0)
os.makedirs(archive, exist_ok=True)
os.rename(source, target)
print(source + " -> " + target + " archived (NEW kept: " + kept + ")")
print("migrate-archived: " + target)
"""


def test_given_an_identity_hook_where_both_keys_exist_when_finished_then_its_archive_is_recorded_and_a_rerun_is_clean(
    slot, tmp_path
):
    _moved(slot)
    keys, archive = tmp_path / "keys", tmp_path / "identity-archive"
    keys.mkdir()
    old_key, new_key = keys / f"{OLD}.key", keys / f"{NEW}.key"
    old_key.write_bytes(b"fake-old-key-bytes")
    new_key.write_bytes(b"fake-new-key-bytes")
    script = tmp_path / "identity_hook.py"
    script.write_text(IDENTITY_HOOK.format(keys=str(keys), archive=str(archive)), encoding="utf-8")
    directory = get_xdg_config_home() / "migrate-session.d"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "10-identity.toml").write_text(f'name = "identity"\nargv = [{json.dumps(str(script))}]\n')

    code, out, err = _finish(slot["repo"], "-y")

    assert code == 0, out + err
    assert new_key.read_bytes() == b"fake-new-key-bytes"
    assert not old_key.exists()
    assert (archive / f"{OLD}.key").read_bytes() == b"fake-old-key-bytes"
    assert [(e["part"], e["archive"]) for e in _archived(slot)] == [("hook identity", str(archive / f"{OLD}.key"))]
    assert f"hook identity: {archive / f'{OLD}.key'} (archived OLD, NEW kept)" in out

    code, out, err = _finish(slot["repo"], "-y")

    assert code == 0, out + err
    assert "NOT finished" not in out + err
    assert len(_archived(slot)) == 1


def test_given_new_migrated_on_and_its_slot_gone_when_status_runs_then_the_earlier_pair_reads_complete(slot):
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    repo = slot["repo"]
    _append(slot, old="myrepo-1", new="myrepo-2", repo=str(repo), worktree=str(repo / ".worktrees" / "myrepo-2"))
    _append(slot, old="myrepo-2", new=NEW, repo=str(repo), worktree=str(repo / ".worktrees" / NEW))

    code, out, err = _status(slot, "myrepo-1")

    assert code == 0, out + err
    row = _status_row(out, "myrepo-1")
    assert row[3] == "complete"
    assert f"slot moved on to {NEW} ({repo / '.worktrees' / NEW})" in row[4]


def test_given_a_gone_slot_with_no_onward_record_when_status_runs_then_it_still_reads_slot_missing(slot):
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    repo = slot["repo"]
    _append(slot, old="myrepo-1", new="myrepo-2", repo=str(repo), worktree=str(repo / ".worktrees" / "myrepo-2"))

    code, out, err = _status(slot, "myrepo-1")

    assert code == 1, out + err
    row = _status_row(out, "myrepo-1")
    assert row[3] == "partial"
    assert row[4] == f"slot (missing: {repo / '.worktrees' / 'myrepo-2'} does not exist)"
