"""``ai probe-transcripts``: each installed client's probe answers present, absent and unknown live.

Every client store here is a fixture under a temporary home, in the shapes
``test_session_clients`` seeds; which client executables are on ``PATH`` is stood in
for at ``shutil.which``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_session_clients import CLIENTS, TITLE, make_root, seed

from ai_cli import session_clients as sc
from ai_cli.main import _cli_group


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("USERPROFILE", str(path))
    return path


@pytest.fixture
def on_path(monkeypatch) -> set[str]:
    """The executables ``shutil.which`` finds; empty unless a test adds one."""
    found: set[str] = set()
    monkeypatch.setattr("shutil.which", lambda name, *a, **k: f"/usr/bin/{name}" if name in found else None)
    return found


@pytest.fixture
def worktree(tmp_path) -> Path:
    path = tmp_path / "projects" / "myrepo" / ".worktrees" / TITLE
    path.mkdir(parents=True)
    return path.resolve()


def _run(*args: str):
    return CliRunner().invoke(_cli_group, ["probe-transcripts", *args])


def _json(*args: str) -> tuple[int, dict]:
    result = _run("-j", *args)
    return result.exit_code, json.loads(result.output)


def _store_snapshot(home: Path) -> dict[str, bytes]:
    return {str(path): path.read_bytes() for path in sorted(home.rglob("*")) if path.is_file()}


def test_given_every_client_with_a_record_when_probed_then_each_case_passes_and_it_exits_0(home, on_path, worktree):
    for client in CLIENTS:
        seed(client, home, worktree)

    result = _run()

    assert result.exit_code == 0, result.output
    for client in CLIENTS:
        for state in (sc.PRESENT, sc.ABSENT, sc.UNKNOWN):
            assert f"{client} {state}: expected {state}, got {state} (ok)" in result.output


def test_given_every_client_with_a_record_when_probed_as_json_then_the_object_names_each_case(home, on_path, worktree):
    for client in CLIENTS:
        seed(client, home, worktree)

    code, report = _json()

    assert code == 0 and report["ok"] is True
    assert [entry["client"] for entry in report["clients"]] == CLIENTS
    for entry in report["clients"]:
        assert entry["installed"] is True
        assert [case["case"] for case in entry["cases"]] == [sc.PRESENT, sc.ABSENT, sc.UNKNOWN]
        for case in entry["cases"]:
            assert set(case) == {"case", "expected", "actual", "reason", "skipped", "ok"}
            assert case["actual"] == case["expected"] and case["ok"] is True and case["reason"]
            assert case["skipped"] is False
    [unknown] = [case for case in report["clients"][0]["cases"] if case["case"] == sc.UNKNOWN]
    assert "is missing" in unknown["reason"]


def test_given_no_client_installed_when_probed_then_each_is_skipped_and_it_exits_0(home, on_path):
    result = _run()

    assert result.exit_code == 0, result.output
    for client in CLIENTS:
        assert f"{client} skipped: not installed" in result.output
    code, report = _json()
    assert code == 0 and all(entry["installed"] is False and entry["cases"] == [] for entry in report["clients"])


def test_given_a_client_on_path_with_no_store_when_probed_then_it_is_skipped_as_held_and_passes(home, on_path):
    on_path.add("codex")

    result = _run()

    assert result.exit_code == 0, result.output
    assert "codex skipped: no codex store on this host; its rows are held as unknown until one exists" in result.output
    assert "codex present" not in result.output and "codex absent" not in result.output
    assert "claude-code skipped: not installed" in result.output


def test_given_a_client_on_path_with_no_store_when_probed_as_json_then_it_is_installed_skipped_with_no_cases(
    home, on_path
):
    on_path.add("codex")

    code, report = _json()

    [codex] = [entry for entry in report["clients"] if entry["client"] == "codex"]
    assert code == 0 and report["ok"] is True
    assert codex["installed"] is True and codex["cases"] == []
    assert codex["skipped"].startswith("no codex store on this host")


def test_given_a_client_on_path_with_an_empty_store_when_probed_then_present_is_skipped_and_it_exits_0(home, on_path):
    on_path.add("codex")
    make_root("codex", home)

    result = _run()

    assert result.exit_code == 0, result.output
    assert "codex present skipped: no codex record on this host to test present" in result.output
    assert "codex absent: expected absent, got absent (ok)" in result.output
    assert "codex unknown: expected unknown, got unknown (ok)" in result.output


def test_given_an_empty_store_when_probed_as_json_then_present_is_a_passing_skip_and_absent_and_unknown_still_run(
    home, on_path
):
    make_root("pi", home)

    code, report = _json()

    [pi] = [entry for entry in report["clients"] if entry["client"] == "pi"]
    assert code == 0 and report["ok"] is True
    present, absent, unknown = pi["cases"]
    assert present == {
        "case": sc.PRESENT,
        "expected": sc.PRESENT,
        "actual": None,
        "reason": "no pi record on this host to test present",
        "skipped": True,
        "ok": True,
    }
    assert absent["actual"] == sc.ABSENT and absent["ok"] is True and absent["skipped"] is False
    assert unknown["actual"] == sc.UNKNOWN and unknown["ok"] is True and unknown["skipped"] is False


def test_given_a_store_root_that_cannot_be_read_when_probed_then_present_fails_naming_the_problem_and_it_exits_1(
    home, on_path
):
    make_root("gemini", home)
    (home / ".gemini" / "projects.json").write_text("not json")

    code, report = _json()

    [gemini] = [entry for entry in report["clients"] if entry["client"] == "gemini"]
    [present] = [case for case in gemini["cases"] if case["case"] == sc.PRESENT]
    assert code == 1 and report["ok"] is False
    assert present["ok"] is False and present["skipped"] is False
    assert "gemini project registry" in present["reason"] and "is not readable" in present["reason"]
    assert "gemini present: gemini project registry" in _run().output


def test_given_a_store_with_an_unreadable_record_when_probed_then_absent_fails_with_the_probe_reason(
    home, on_path, worktree
):
    seed("codex", home, worktree)
    broken = home / ".codex" / "sessions" / "2026" / "10" / "24" / "rollout-2026-10-24T00-00-00-bad.jsonl"
    broken.parent.mkdir(parents=True)
    broken.write_text("not json\n")

    code, report = _json()

    [codex] = [entry for entry in report["clients"] if entry["client"] == "codex"]
    [absent] = [case for case in codex["cases"] if case["case"] == sc.ABSENT]
    assert code == 1 and report["ok"] is False
    assert absent["actual"] == sc.UNKNOWN and absent["ok"] is False
    assert str(broken) in absent["reason"] and "first record is not session_meta" in absent["reason"]


def test_given_several_records_when_probed_then_the_newest_is_probed_present(home, on_path, tmp_path, worktree):
    older = tmp_path / "projects" / "myrepo" / ".worktrees" / "older"
    older.mkdir(parents=True)
    old_record = seed("pi", home, older.resolve())
    new_record = seed("pi", home, worktree)
    stamp = new_record.stat().st_mtime
    os.utime(old_record, (stamp - 60, stamp - 60))

    code, report = _json()

    [pi] = [entry for entry in report["clients"] if entry["client"] == "pi"]
    assert code == 0 and pi["cases"][0]["reason"] == f"pi record {new_record}"


def test_given_real_stores_when_probed_then_nothing_in_them_is_written(home, on_path, worktree):
    for client in CLIENTS:
        seed(client, home, worktree)
    before = _store_snapshot(home)

    assert _run().exit_code == 0

    assert _store_snapshot(home) == before
