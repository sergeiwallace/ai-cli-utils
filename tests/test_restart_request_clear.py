"""Explicit recovery clears only a request whose original target is gone."""

from __future__ import annotations

import getpass
import json
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from conftest import run_cli

from ai_cli import session_restart as restart
from ai_cli.process_probe import ProcessIdentity

REQUEST_ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def fenced_request(tmp_path, monkeypatch):
    monkeypatch.setattr(restart.config, "get_xdg_state_home", lambda: tmp_path)
    session = "c-myproject-1"
    log = tmp_path / "supervisor.log"
    log.write_text("existing supervisor output\n", encoding="utf-8")
    request = {
        "version": 1,
        "request_id": REQUEST_ID,
        "status": "failed",
        "tmux": f"{session}:@1.%1",
        "old_agent_pid": 101,
        "old_agent_identity": asdict(ProcessIdentity("procfs", 123)),
        "launch_log_path": str(log),
    }
    path = restart.state_paths(session)[2]
    restart._atomic_json(path, request)
    other = path.with_name("c-other-1.request.json")
    other.write_text('{"status":"failed"}', encoding="utf-8")
    receipt = path.with_name(f"{session}.{REQUEST_ID}.launched")
    receipt.write_text(REQUEST_ID, encoding="utf-8")
    probe = Mock(
        is_present=Mock(return_value=False), capture_identity=Mock(return_value=ProcessIdentity("procfs", 123))
    )
    monkeypatch.setattr(restart, "probe_for", lambda: probe)
    monkeypatch.setattr(getpass, "getuser", lambda: "user")
    return SimpleNamespace(
        path=path, request=request, session=session, log=log, other=other, receipt=receipt, probe=probe
    )


def _clear(*args):
    return run_cli(["ai", "clear-restart-request", REQUEST_ID, *args])


def test_given_dead_target_when_cleared_then_only_request_removed_and_audit_persisted(fenced_request):
    world = fenced_request
    before = {path: path.read_bytes() for path in (world.other, world.receipt)}
    code, out, err = _clear("--reason", "target exited\nchecked by operator")
    assert code == 0, (out, err)
    assert not world.path.exists()
    assert all(path.read_bytes() == content for path, content in before.items())
    lines = world.log.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "existing supervisor output"
    assert len(lines) == 2
    audit = json.loads(lines[1])
    assert audit["request_id"] == REQUEST_ID
    assert audit["reason"] == "target exited\nchecked by operator"
    assert audit["actor"]["user"] == "user"
    assert audit["actor"]["pid"] > 1
    assert audit["proof"] == {"pid": 101, "state": "gone", "recorded": world.request["old_agent_identity"]}


def test_given_live_target_when_cleared_then_request_and_log_unchanged(fenced_request):
    world = fenced_request
    world.probe.is_present.return_value = True
    before = world.path.read_bytes(), world.log.read_bytes()
    code, _, _ = _clear("-r", "checked")
    assert code == 1
    assert (world.path.read_bytes(), world.log.read_bytes()) == before


@pytest.mark.parametrize("backend", ["procfs", "psutil"])
def test_given_reused_pid_when_cleared_then_birth_difference_logged(fenced_request, backend):
    world = fenced_request
    world.request["old_agent_identity"]["backend"] = backend
    restart._atomic_json(world.path, world.request)
    world.probe.is_present.return_value = True
    world.probe.capture_identity.return_value = ProcessIdentity(backend, 456)
    code, _, err = _clear("-r", "pid reused")
    assert code == 0, err
    assert not world.path.exists()
    proof = json.loads(world.log.read_text(encoding="utf-8").splitlines()[-1])["proof"]
    assert proof == {
        "pid": 101,
        "state": "reused",
        "recorded": world.request["old_agent_identity"],
        "current": {"backend": backend, "marker": 456},
    }


@pytest.mark.parametrize("reason_args", [[], ["--reason", ""], ["-r", "   "]])
def test_given_missing_reason_when_cleared_then_refused_without_changes(fenced_request, reason_args):
    before = fenced_request.path.read_bytes(), fenced_request.log.read_bytes()
    code, _, _ = _clear(*reason_args)
    assert code == 1
    assert (fenced_request.path.read_bytes(), fenced_request.log.read_bytes()) == before


@pytest.mark.parametrize("identity", [None, ProcessIdentity("psutil", 456)])
def test_given_unproven_identity_when_cleared_then_refused(fenced_request, identity):
    fenced_request.probe.is_present.return_value = True
    fenced_request.probe.capture_identity.return_value = identity
    code, _, _ = _clear("-r", "checked")
    assert code == 1
    assert fenced_request.path.exists()
    assert fenced_request.log.read_text(encoding="utf-8") == "existing supervisor output\n"


def test_given_unwritable_log_when_cleared_then_request_preserved(fenced_request):
    fenced_request.request["launch_log_path"] = str(fenced_request.log.parent)
    restart._atomic_json(fenced_request.path, fenced_request.request)
    code, _, _ = _clear("-r", "checked")
    assert code == 1
    assert fenced_request.path.exists()


def test_given_locked_request_when_cleared_then_request_preserved(fenced_request):
    with restart.session_lock(fenced_request.session):
        code, _, _ = _clear("-r", "checked")
    assert code == 1
    assert fenced_request.path.exists()


def test_given_wrong_request_id_when_cleared_then_nothing_removed(fenced_request):
    code, _, _ = run_cli(["ai", "clear-restart-request", "22222222-2222-4222-8222-222222222222", "-r", "checked"])
    assert code == 1
    assert fenced_request.path.exists()


def test_given_legacy_request_when_cleared_then_supervisor_metadata_supplies_log(fenced_request):
    world = fenced_request
    world.request.pop("launch_log_path")
    restart._atomic_json(world.path, world.request)
    restart._atomic_json(
        world.path.parent.parent / f"session-meta-{world.session}.json", {"launch_log_path": str(world.log)}
    )
    code, _, err = _clear("-r", "checked")
    assert code == 0, err
    assert not world.path.exists()
    assert json.loads(world.log.read_text(encoding="utf-8").splitlines()[-1])["request_id"] == REQUEST_ID


@pytest.mark.parametrize("live", [False, True])
def test_given_fenced_target_when_remedy_requested_then_command_matches_target_state(fenced_request, live):
    world = fenced_request
    world.probe.is_present.return_value = live
    code, out, _ = run_cli(["ai", "internal", "restart-remedy", world.session])
    assert code == 0
    if live:
        assert f"ai --restart-session {world.session}" in out
    else:
        assert f"ai clear-restart-request {REQUEST_ID} --reason 'explain recovery'" in out


@pytest.mark.parametrize("pid", [None, True, 0, 1, "101"])
def test_given_invalid_target_pid_when_cleared_then_refused(fenced_request, pid):
    fenced_request.request["old_agent_pid"] = pid
    restart._atomic_json(fenced_request.path, fenced_request.request)
    code, _, _ = _clear("-r", "checked")
    assert code == 1
    assert fenced_request.path.exists()
