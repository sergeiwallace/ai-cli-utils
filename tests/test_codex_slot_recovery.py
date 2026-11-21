"""Recovery decisions are based on process identity and exact native thread proof."""

import json
import os
from pathlib import Path

import pytest
from test_codex_exact_thread import NEWER, SLOT, THREAD, dispatch
from test_codex_exact_thread import collision as collision

from ai_cli import codex_session as identity
from ai_cli.process_probe import ProcessIdentity, StartTimeMatch


class Probe:
    def __init__(self, state):
        self.status = state

    def is_present(self, pid):
        return self.status != "gone"

    def capture_identity(self, pid):
        if pid == os.getpid():
            return ProcessIdentity("procfs", 100)
        if self.status == "unreadable":
            return None
        return ProcessIdentity("procfs", 200 if self.status == "reused" else 100)

    def start_time_match(self, pid, recorded):
        return StartTimeMatch.MATCH if self.status == "live" else StartTimeMatch.UNPROVEN


@pytest.mark.parametrize("writer", ["live", "gone", "reused", "missing", "unreadable"])
@pytest.mark.parametrize("confirmed", [True, False])
def test_given_inflight_launch_when_prepared_then_writer_decision_table_is_enforced(
    collision, monkeypatch, capsys, writer, confirmed
):
    worktree, _, record = collision
    doc = json.loads(record.read_text())
    doc["in_flight"] = {
        "token": "old-token",
        "writer": {"pid": 12345, "backend": "procfs", "start_time": 100},
        "launched_at": "2026-11-21T00:00:00+00:00",
        "client_version": "0.169.0",
        "route": "local-tmux",
    }
    if not confirmed:
        doc.update(phase="unconfirmed", thread_id=None, rollout_path=None, result="unconfirmed", resumable=None)
    if writer == "missing":
        doc["in_flight"]["writer"] = None
    record.write_text(json.dumps(doc))
    before = record.read_bytes()
    monkeypatch.setattr(identity, "probe_for", lambda: Probe(writer))
    if writer in ("live", "missing", "unreadable"):
        with pytest.raises(identity.IdentityError, match="writer"):
            identity.prepare(record, worktree, SLOT, fresh=False, route="local-tmux", client_version="0.169.0")
        assert record.read_bytes() == before
    else:
        target, _, _, token = identity.prepare(
            record, worktree, SLOT, fresh=False, route="local-tmux", client_version="0.169.0"
        )
        after = json.loads(record.read_text())
        assert target == (THREAD if confirmed else "new")
        assert all(after[key] == doc[key] for key in ("phase", "thread_id", "rollout_path", "result"))
        assert after["in_flight"]["token"] == token != "old-token"
        log = capsys.readouterr().err
        assert "[session]" in log and SLOT in log and "abandoned" in log
        assert ("resume-confirmed" if confirmed else "abandoned-unconfirmed") in log
        assert all(value not in log for value in ("old-token", token, THREAD, str(doc["rollout_path"])))


@pytest.mark.parametrize("problem", ["old-schema", "invalid-json", "phase", "thread", "launch", "writer-start"])
def test_given_malformed_record_when_prepared_then_it_refuses_without_writing(collision, problem):
    worktree, _, record = collision
    doc = json.loads(record.read_text())
    if problem == "old-schema":
        doc["schema_version"] = 1
    elif problem == "phase":
        doc["phase"] = "pending"
    elif problem == "thread":
        doc["thread_id"] = "invalid"
    elif problem == "launch":
        doc["in_flight"] = []
    elif problem == "writer-start":
        doc["in_flight"]["writer"]["start_time"] = None
    record.write_text("{" if problem == "invalid-json" else json.dumps(doc))
    before = record.read_bytes()
    with pytest.raises((identity.IdentityError, ValueError)):
        identity.prepare(record, worktree, SLOT, fresh=False, route="local-tmux", client_version="0.169.0")
    assert record.read_bytes() == before


@pytest.mark.parametrize("token_kind", ["current", "stale", "missing"])
def test_given_different_thread_confirmation_when_callback_arrives_then_only_current_token_can_replace(
    collision, token_kind
):
    worktree, _, record = collision
    _, _, _, token = identity.prepare(record, worktree, SLOT, fresh=False, route="local-tmux", client_version="0.169.0")
    before = record.read_bytes()
    event = {"hook_event_name": "SessionStart", "session_id": NEWER, "cwd": str(worktree), "transcript_path": None}
    if token_kind == "current":
        identity.confirm(record, token, event)
        doc = json.loads(record.read_text())
        assert doc["thread_id"] == NEWER and doc["result"] == "live-only"
        assert doc["resumable"]["thread_id"] == THREAD
    else:
        with pytest.raises(identity.IdentityError):
            identity.confirm(record, "stale" if token_kind == "stale" else "", event)
        assert record.read_bytes() == before


@pytest.mark.parametrize("proof", ["none", "event", "exact-rollout", "other-rollout", "fallback"])
def test_given_live_only_thread_when_relaunched_then_only_exact_proof_upgrades_it(collision, monkeypatch, proof):
    worktree, _, record = collision
    native = worktree / "native"
    monkeypatch.setenv("CODEX_HOME", str(native))
    doc = json.loads(record.read_text())
    saved = doc["resumable"]
    doc.update(thread_id=NEWER, rollout_path=None, result="live-only", resumable=None)
    record.write_text(json.dumps(doc))
    if proof in ("exact-rollout", "other-rollout", "fallback"):
        thread = NEWER if proof == "exact-rollout" else THREAD
        rollout = native / "sessions" / "2026" / "11" / "21" / "rollout-newest.jsonl"
        rollout.parent.mkdir(parents=True)
        rollout.write_text(json.dumps({"type": "session_meta", "payload": {"id": thread, "cwd": str(worktree)}}))
    if proof == "event":
        identity.confirm(
            record,
            doc["in_flight"]["token"],
            {
                "hook_event_name": "SessionStart",
                "session_id": NEWER,
                "cwd": str(worktree),
                "transcript_path": str(worktree / "synthetic-rollout"),
            },
        )
        assert json.loads(record.read_text())["result"] == "confirmed"
        # Every null event leaves the current thread live-only; the last
        # resumable pair remains available only as a separately proved fallback.
        identity.confirm(
            record,
            doc["in_flight"]["token"],
            {"hook_event_name": "SessionStart", "session_id": NEWER, "cwd": str(worktree), "transcript_path": None},
        )
        after = json.loads(record.read_text())
        assert after["rollout_path"] is None and after["result"] == "live-only"
        assert after["resumable"] == {"thread_id": NEWER, "rollout_path": str(worktree / "synthetic-rollout")}
        return
    if proof == "fallback":
        doc["resumable"] = saved
        record.write_text(json.dumps(doc))
    target, _, _, _ = identity.prepare(
        record, worktree, SLOT, fresh=False, route="local-tmux", client_version="0.169.0"
    )
    after = json.loads(record.read_text())
    assert target == (NEWER if proof == "exact-rollout" else THREAD if proof == "fallback" else "new")
    assert after["result"] == ("confirmed" if proof == "exact-rollout" else "live-only")
    assert after["thread_id"] == NEWER


@pytest.mark.skipif(os.name != "posix", reason="POSIX exec launcher")
@pytest.mark.parametrize("resumed", [True, False])
@pytest.mark.parametrize("shell_name", ["bash", "zsh"])
def test_given_quit_before_first_prompt_when_same_shell_relaunches_then_it_recovers(collision, resumed, shell_name):
    worktree, state, record = collision
    if not resumed:
        doc = json.loads(record.read_text())
        record.rename(record.with_suffix(".saved"))
        # No existing directory sessions may be adopted on the initial launch.
        Path(doc["rollout_path"]).parents[3].rename(worktree / "saved-native")
    result, argv = dispatch(collision, first_run=True, prompted=False, shell_name=shell_name, launches=2)
    assert result.returncode == 0, result.stderr
    assert ("resume" in argv) == resumed
    assert (THREAD in argv) == resumed
    assert "abandoned" in result.stderr and SLOT in result.stderr
    doc = json.loads(record.read_text())
    assert doc["phase"] == ("confirmed" if resumed else "unconfirmed")
    # The real process identity survives both launcher exec and the fake client
    # exec; it is not the shell, version-query child, or a finished prepare child.
    assert doc["in_flight"]["writer"] == json.loads((state / "writer.json").read_text())


def test_given_null_transcript_when_new_thread_confirms_then_identity_stays_live_only(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "native"))
    record = tmp_path / "slot.json"
    _, _, _, token = identity.prepare(record, tmp_path, SLOT, fresh=True, route="local-tmux", client_version="0.169.0")
    identity.confirm(
        record,
        token,
        {
            "hook_event_name": "SessionStart",
            "session_id": THREAD,
            "cwd": str(tmp_path),
            "transcript_path": None,
        },
    )
    doc = json.loads(record.read_text())
    assert doc["phase"] == "confirmed" and doc["result"] == "live-only"
    assert doc["rollout_path"] is None and doc["resumable"] is None
    doc["in_flight"]["writer"]["pid"] = 99999999
    record.write_text(json.dumps(doc))
    target, _, _, _ = identity.prepare(
        record, tmp_path, SLOT, fresh=False, route="local-tmux", client_version="0.169.0"
    )
    assert target == "new"
    assert json.loads(record.read_text())["result"] == "live-only"


def test_given_non_posix_exec_when_launch_requested_then_it_refuses_before_any_record_write(
    collision, monkeypatch, capsys
):
    from types import SimpleNamespace

    worktree, _, record = collision
    before = record.read_bytes()
    monkeypatch.setattr(identity, "os", SimpleNamespace(name="nt"))
    assert identity.main(["launch", str(record), str(worktree), SLOT, "false", "local-tmux"]) == 76
    assert record.read_bytes() == before
    assert "writer exec identity unsupported" in capsys.readouterr().err


def test_given_confirmed_identity_when_launch_is_prepared_then_all_committed_fields_are_preserved(collision):
    worktree, _, record = collision
    doc = json.loads(record.read_text())
    doc["rollout_path"] = str(worktree / "original-rollout")
    doc["resumable"]["rollout_path"] = doc["rollout_path"]
    record.write_text(json.dumps(doc))
    identity.prepare(record, worktree, SLOT, fresh=False, route="paired-host-tmux", client_version="0.169.0")
    after = json.loads(record.read_text())
    assert {k: v for k, v in after.items() if k != "in_flight"} == {k: v for k, v in doc.items() if k != "in_flight"}


def test_given_malformed_writer_when_confirmed_then_record_is_unchanged(collision):
    worktree, _, record = collision
    doc = json.loads(record.read_text())
    doc["in_flight"]["writer"] = None
    record.write_text(json.dumps(doc))
    before = record.read_bytes()
    with pytest.raises(identity.IdentityError):
        identity.confirm(
            record,
            doc["in_flight"]["token"],
            {
                "hook_event_name": "SessionStart",
                "session_id": NEWER,
                "cwd": str(worktree),
                "transcript_path": None,
            },
        )
    assert record.read_bytes() == before


@pytest.mark.parametrize("phase", ["confirmed", "unconfirmed"])
def test_given_inconsistent_committed_identity_when_prepared_then_it_refuses_without_writing(collision, phase):
    worktree, _, record = collision
    doc = json.loads(record.read_text())
    if phase == "confirmed":
        doc["resumable"] = None
    else:
        doc.update(phase="unconfirmed", thread_id=None, rollout_path=None, result="unconfirmed")
    record.write_text(json.dumps(doc))
    before = record.read_bytes()
    with pytest.raises(identity.IdentityError, match="inconsistent"):
        identity.prepare(record, worktree, SLOT, fresh=False, route="local-tmux", client_version="0.169.0")
    assert record.read_bytes() == before
