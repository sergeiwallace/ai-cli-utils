"""Whole-history restart evidence stays bounded and fails closed."""

import io
import json
import time
from pathlib import Path

import pytest

from ai_cli import config
from ai_cli import session_restart as restart


@pytest.fixture
def history(tmp_path):
    path = tmp_path / "session.jsonl"

    def write(completion=True, kind="shell"):
        launch = (
            {"backgroundTaskId": "task-1"} if kind == "shell" else {"status": "async_launched", "agentId": "task-1"}
        )
        with path.open("wb") as output:
            output.write(json.dumps({"type": "user", "toolUseResult": launch}).encode() + b"\n")
            padding = json.dumps({"type": "progress", "content": "x" * 4096}).encode() + b"\n"
            for _ in range(restart.TAIL_LIMIT // len(padding) + 10):
                output.write(padding)
            if completion:
                output.write(b'{"type":"task-notification","taskId":"task-1","status":"completed"}\n')
            output.write(b'{"type":"system","subtype":"turn_duration"}\n')
        return path

    return write


def check_idle(path):
    rows = restart.transcript_tail(path)
    restart.idle_gate(
        {"status": "idle", "statusUpdatedAt": (time.time() - 1000) * 1000},
        rows,
        config.read_restart_config({}),
    )
    return rows


@pytest.mark.parametrize("kind", ["shell", "agent"])
def test_given_ac_g1_large_completed_history_when_scanned_then_idle_and_bounded(history, kind):
    path = history(kind=kind)
    assert path.stat().st_size > restart.TAIL_LIMIT
    rows = check_idle(path)
    assert rows[-1]["subtype"] == "turn_duration"
    assert len(rows) < path.stat().st_size // 4096


@pytest.mark.parametrize("kind", ["shell", "agent"])
def test_given_ac_g2_early_unfinished_launch_when_large_history_scanned_then_refuses(history, kind):
    with pytest.raises(restart.Refusal, match="background-work") as caught:
        check_idle(history(completion=False, kind=kind))
    assert caught.value.detail == "unfinished background launch"


@pytest.mark.parametrize("failure", ["read", "short", "incomplete", "invalid", "shape"])
def test_given_ac_g3_unreadable_chunk_when_scanned_then_refuses(tmp_path, monkeypatch, failure):
    path = tmp_path / "session.jsonl"
    raw = b'{"type":"progress"}\n' * 10
    if failure == "incomplete":
        raw += b'{"type":"system"}'
    elif failure == "invalid":
        raw += b"\xff\n"
    elif failure == "shape":
        raw += b'{"message": []}\n'
    path.write_bytes(raw)
    monkeypatch.setattr(restart, "CHUNK_SIZE", 7)
    original = Path.open

    class BrokenReader(io.BytesIO):
        def fileno(self):
            return opened.fileno()

        def read(self, size=-1):
            assert 0 < size <= 7
            if self.tell() >= 14:
                if failure == "read":
                    raise OSError("unreadable chunk")
                if failure == "short":
                    return b""
            return super().read(size)

    with original(path, "rb") as opened:
        if failure in ("read", "short"):
            monkeypatch.setattr(
                Path, "open", lambda self, *a, **kw: BrokenReader(raw) if self == path else original(self, *a, **kw)
            )
        with pytest.raises(restart.Refusal, match="background-work"):
            restart.transcript_tail(path)


def test_given_ac_g3_split_utf8_and_task_record_when_reassembled_then_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(restart, "CHUNK_SIZE", 1)
    path = tmp_path / "session.jsonl"
    path.write_text(
        '{"toolUseResult":{"backgroundTaskId":"café"}}\n'
        '{"type":"queue-operation","operation":"enqueue","content":'
        '"<task-notification><task-id>café</task-id><status>completed</status></task-notification>"}\n'
        '{"type":"system","subtype":"turn_duration"}\n',
        encoding="utf-8",
    )
    check_idle(path)


@pytest.mark.parametrize("completion", [False, True])
def test_given_ac_g2_sidechain_launch_when_scanned_then_background_evidence_preserved(tmp_path, completion):
    path = tmp_path / "session.jsonl"
    rows = [{"isSidechain": True, "toolUseResult": {"backgroundTaskId": "task-1"}}]
    if completion:
        rows.append({"type": "task-notification", "taskId": "task-1", "status": "killed"})
    rows.append({"type": "system", "subtype": "turn_duration"})
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    if completion:
        check_idle(path)
    else:
        with pytest.raises(restart.Refusal, match="background-work"):
            check_idle(path)


def test_given_ac_g1_old_completion_when_launch_in_tail_then_full_ledger_applies(tmp_path, monkeypatch):
    monkeypatch.setattr(restart, "TAIL_LIMIT", 256)
    path = tmp_path / "session.jsonl"
    path.write_text(
        '{"type":"task-notification","taskId":"task-1","status":"completed"}\n'
        + '{"type":"progress"}\n' * 100
        + '{"toolUseResult":{"backgroundTaskId":"task-1"}}\n'
        + '{"type":"system","subtype":"turn_duration"}\n',
        encoding="utf-8",
    )
    check_idle(path)


def test_given_ac_g1_many_completed_tasks_when_streamed_then_memory_stays_bounded(tmp_path, monkeypatch):
    import tracemalloc

    monkeypatch.setattr(restart, "TAIL_LIMIT", 4096)
    path = tmp_path / "session.jsonl"
    with path.open("w", encoding="utf-8") as output:
        for index in range(30000):
            output.write(json.dumps({"toolUseResult": {"backgroundTaskId": f"task-{index}"}}) + "\n")
            output.write(
                json.dumps({"type": "task-notification", "taskId": f"task-{index}", "status": "completed"}) + "\n"
            )
        output.write('{"type":"system","subtype":"turn_duration"}\n')
    tracemalloc.start()
    try:
        check_idle(path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 2 * 1024 * 1024
