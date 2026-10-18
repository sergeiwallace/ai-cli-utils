"""Passive restart evidence rejects unknown or contradictory state."""

import json
import sys
import time
from types import SimpleNamespace

import pytest

from ai_cli import config
from ai_cli import session_restart as restart


def test_given_non_utf8_locale_when_adapter_outputs_utf8_then_text_is_preserved(monkeypatch):
    monkeypatch.setattr(restart.subprocess, "_text_encoding", lambda: "cp1252")
    output = restart._run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(bytes.fromhex('636166c3a9'))"])
    assert output == "café"


@pytest.mark.parametrize("kind", ["shell", "agent"])
def test_given_completed_task_when_idle_checked_then_no_pending_work(kind):
    launch = {"backgroundTaskId": "task-1"} if kind == "shell" else {"status": "async_launched", "agentId": "task-1"}
    rows = [
        {"type": "user", "toolUseResult": launch},
        {
            "type": "user",
            "message": {
                "content": "<task-notification><task-id>task-1</task-id><status>completed</status></task-notification>"
            },
        },
        {"type": "system", "subtype": "turn_duration", "pendingBackgroundAgentCount": 0},
    ]
    restart.idle_gate(
        {"status": "idle", "statusUpdatedAt": (time.time() - 1000) * 1000}, rows, config.read_restart_config({})
    )


def test_given_mcp_tree_when_classified_then_only_configured_servers_allowed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(restart.Path, "home", lambda: home)
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"example": {"command": "python3", "args": ["server.py"]}}}), encoding="utf-8"
    )
    children = [
        SimpleNamespace(pid=2, ppid=lambda: 1, cmdline=lambda: ["python3", "server.py"]),
        SimpleNamespace(pid=3, ppid=lambda: 2, cmdline=lambda: ["worker"]),
    ]
    monkeypatch.setattr(restart.psutil, "Process", lambda pid: SimpleNamespace(children=lambda **kwargs: children))
    restart.descendants_gate(1, tmp_path)
    children.append(SimpleNamespace(pid=4, ppid=lambda: 1, cmdline=lambda: ["sleep", "300"]))
    with pytest.raises(restart.Refusal, match="background-work"):
        restart.descendants_gate(1, tmp_path)


def test_given_unknown_model_when_window_unconfigured_then_context_refuses():
    rows = [
        {
            "type": "assistant",
            "message": {
                "model": "claude-sonnet-99",
                "usage": {"input_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            },
        }
    ]
    assert restart.context_gate(rows, config.read_restart_config({})) is False


def test_given_oversized_history_when_tail_read_then_no_unbounded_read(tmp_path, monkeypatch):
    transcript = tmp_path / "session.jsonl"
    transcript.write_bytes(b" " * 1024)
    monkeypatch.setattr(restart, "TAIL_LIMIT", 100)
    with pytest.raises(restart.Refusal, match="background-work"):
        restart.transcript_tail(transcript)


def test_given_duplicate_adapter_when_loaded_then_config_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_xdg_config_home", lambda: tmp_path)
    directory = tmp_path / "restart-session.d"
    directory.mkdir()
    for name in ("one", "two"):
        (directory / f"{name}.toml").write_text('[inject]\nargv = ["example_injector"]\n', encoding="utf-8")
    with pytest.raises(restart.Refusal, match="config"):
        restart.load_commands()


def test_given_running_notification_when_shell_pending_then_background_refuses():
    rows = [
        {"type": "user", "toolUseResult": {"backgroundTaskId": "task-1"}},
        {
            "type": "user",
            "message": {
                "content": "<task-notification><task-id>task-1</task-id><status>running</status></task-notification>"
            },
        },
        {"type": "system", "subtype": "turn_duration", "pendingBackgroundAgentCount": 0},
    ]
    with pytest.raises(restart.Refusal, match="background-work"):
        restart.idle_gate(
            {"status": "idle", "statusUpdatedAt": (time.time() - 1000) * 1000}, rows, config.read_restart_config({})
        )
