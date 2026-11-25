"""Explicit recovery of slots without a client-confirmed identity."""

import json
import os
import subprocess

import pytest

from ai_cli import codex_session as identity

SLOT = "cx-myproject-1"
OLD = "11111111-2222-4333-8444-555555555555"
NEW = "66666666-7777-4888-8999-000000000000"


@pytest.fixture
def legacy_slot(tmp_path, monkeypatch):
    cwd = tmp_path / "myproject"
    cwd.mkdir()
    native = tmp_path / "native"
    monkeypatch.setenv("CODEX_HOME", str(native))
    rollouts = native / "sessions" / "2026" / "11" / "21"
    rollouts.mkdir(parents=True)
    old = rollouts / "rollout-old.jsonl"
    old.write_text(json.dumps({"type": "session_meta", "payload": {"id": OLD, "cwd": str(cwd)}}))
    record = tmp_path / "state" / "sessions" / f"{SLOT}.codex.json"
    record.parent.mkdir(parents=True)
    record.with_suffix(".json.lock").touch()

    def version(argv, **kwargs):
        assert argv == ["codex", "--version"]
        return subprocess.CompletedProcess(argv, 0, "codex-cli 0.171.0\n", "")

    monkeypatch.setattr(identity.subprocess, "run", version)
    # Model a completed launch so a later prepare in this test is a relaunch.
    monkeypatch.setattr(identity, "_writer", lambda: {"pid": 99999999, "backend": "procfs", "start_time": 100})
    return cwd, record, old


def invocation(legacy_slot, operation="prepare", fresh="false", options=()):
    cwd, record, _ = legacy_slot
    return [operation, str(record), str(cwd), SLOT, fresh, "local-tmux", *options]


@pytest.mark.parametrize("fresh", ["true", "false"])
def test_given_legacy_slot_when_no_approval_then_directory_adoption_still_refuses(legacy_slot, capsys, fresh):
    _, record, old = legacy_slot
    before = old.read_bytes()
    assert identity.main(invocation(legacy_slot, fresh=fresh)) == 1
    assert "Codex slot has no client-confirmed thread; refusing directory-based adoption" in capsys.readouterr().err
    assert not record.exists()
    assert old.read_bytes() == before
    assert record.with_suffix(".json.lock").read_bytes() == b""


@pytest.mark.parametrize("flag", ["-F", "--start-fresh"])
@pytest.mark.parametrize("fresh", ["true", "false"])
def test_given_legacy_slot_when_exact_session_approved_then_new_record_preserves_native_store(
    legacy_slot, capsys, flag, fresh
):
    cwd, record, old = legacy_slot
    before = {path: path.read_bytes() for path in old.parent.iterdir()}
    assert identity.main(invocation(legacy_slot, fresh=fresh, options=(flag, SLOT))) == 0
    assert capsys.readouterr().out.splitlines()[0] == "new"
    doc = identity._read(record)
    assert doc["schema_version"] == 2 and doc["slot"] == SLOT and doc["cwd"] == str(cwd)
    assert doc["phase"] == "unconfirmed" and doc["resumable"] is None
    assert doc["thread_id"] is None and doc["rollout_path"] is None
    assert doc["in_flight"]["token"]
    assert {path: path.read_bytes() for path in old.parent.iterdir()} == before


def test_given_legacy_slot_when_other_session_approved_then_refuses_without_record(legacy_slot, capsys):
    _, record, old = legacy_slot
    before = old.read_bytes()
    assert identity.main(invocation(legacy_slot, options=("--start-fresh", "cx-other-1"))) == 1
    assert "start-fresh session does not match" in capsys.readouterr().err
    assert not record.exists() and old.read_bytes() == before


def test_given_fresh_recovery_when_confirmed_and_relaunched_then_exact_new_thread_resumes(legacy_slot, capsys):
    cwd, record, old = legacy_slot
    before = old.read_bytes()
    assert identity.main(invocation(legacy_slot, options=("--start-fresh", SLOT))) == 0
    token = json.loads(record.read_text())["in_flight"]["token"]
    new = old.with_name("rollout-new.jsonl")
    new.write_text(json.dumps({"type": "session_meta", "payload": {"id": NEW, "cwd": str(cwd)}}))
    identity.confirm(
        record,
        token,
        {"hook_event_name": "SessionStart", "session_id": NEW, "cwd": str(cwd), "transcript_path": str(new)},
    )
    capsys.readouterr()
    confirmed = record.read_bytes()
    # Even replaying approval cannot erase a now-existing identity.
    assert identity.main(invocation(legacy_slot, options=("--start-fresh", SLOT))) == 1
    assert record.read_bytes() == confirmed
    capsys.readouterr()
    assert identity.main(invocation(legacy_slot)) == 0
    assert capsys.readouterr().out.splitlines()[0] == NEW
    assert identity._read(record)["thread_id"] == NEW
    assert old.read_bytes() == before


@pytest.mark.parametrize("candidate", [OLD, NEW])
def test_given_unbound_metadata_when_prepared_then_even_present_candidate_is_not_adopted(
    legacy_slot, capsys, candidate
):
    cwd, record, _ = legacy_slot
    # Registry UUIDs and arbitrary metadata fields are not native confirmation.
    meta = record.parent.parent / f"session-meta-{SLOT}.json"
    meta.write_text(
        json.dumps({"engine": "cx", "session": SLOT, "worktree_dir": str(cwd), "session_id_uuid": candidate})
    )
    assert identity.main(invocation(legacy_slot)) == 1
    assert "--start-fresh" in capsys.readouterr().err
    assert not record.exists()


@pytest.mark.skipif(os.name != "posix", reason="Existing POSIX exec identity requirement")
def test_given_legacy_slot_when_launch_approved_then_fake_handoff_starts_new_thread(legacy_slot, monkeypatch):
    from ai_cli import codex_control, main

    _, record, _ = legacy_slot
    calls = []
    monkeypatch.setattr(codex_control, "start", lambda path: calls.append(path))
    monkeypatch.setattr(main, "_exec_handoff", lambda binary, argv: calls.append(argv))
    monkeypatch.setenv(identity.SLOT_RECORD_ENV, "fixture-record")
    monkeypatch.setenv(identity.LAUNCH_TOKEN_ENV, "fixture-token")
    assert identity.main(invocation(legacy_slot, operation="launch", options=("--start-fresh", SLOT))) == 0
    assert calls == [record, ["codex", "-c", identity.HOOK_DECLARATION]]
    assert os.environ[identity.SLOT_RECORD_ENV] == str(record)
    assert os.environ[identity.LAUNCH_TOKEN_ENV] == identity._read(record)["in_flight"]["token"]


@pytest.mark.parametrize("content", ["{", '{"schema_version": 1}'])
def test_given_existing_invalid_slot_when_fresh_approved_then_record_is_not_overwritten(legacy_slot, content):
    _, record, _ = legacy_slot
    record.write_text(content)
    before = record.read_bytes()
    assert identity.main(invocation(legacy_slot, options=("--start-fresh", SLOT))) == 1
    assert record.read_bytes() == before
