"""Passive restart evidence rejects unknown or contradictory state."""

import json
import sys
import time
from types import SimpleNamespace

import pytest

from ai_cli import config, main
from ai_cli import process_probe as probes
from ai_cli import session_restart as restart
from ai_cli.process_probe import ProcfsProbe, PsutilProbe, StartTimeMatch


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
        SimpleNamespace(pid=2, ppid=lambda: 1, cmdline=lambda: ["python3", "server.py"], create_time=lambda: 1001),
        SimpleNamespace(pid=3, ppid=lambda: 2, cmdline=lambda: ["worker"]),
    ]
    monkeypatch.setattr(
        restart.psutil,
        "Process",
        lambda pid: SimpleNamespace(children=lambda **kwargs: children, create_time=lambda: 1000),
    )
    restart.descendants_gate(1, tmp_path)
    children.append(SimpleNamespace(pid=4, ppid=lambda: 1, cmdline=lambda: ["sleep", "300"], create_time=lambda: 1001))
    with pytest.raises(restart.Refusal, match="background-work"):
        restart.descendants_gate(1, tmp_path)


@pytest.mark.parametrize("model", ["claude-sonnet-99", "claude-opus-5-5"])
@pytest.mark.parametrize(("tokens", "allowed"), [(40_000, True), (40_001, False)])
def test_given_unknown_model_when_window_unconfigured_then_smallest_window_applies(model, tokens, allowed):
    rows = [
        {
            "type": "assistant",
            "message": {
                "model": model,
                "usage": {"input_tokens": tokens, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            },
        }
    ]
    assert restart.context_gate(rows, config.read_restart_config({})) is allowed


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


@pytest.mark.parametrize("counter", ["absent", 0, 1, None, False, "0"])
def test_given_turn_boundary_when_counter_read_then_only_absent_or_zero_is_idle(counter):
    boundary = {"type": "system", "subtype": "turn_duration"}
    if counter != "absent":
        boundary["pendingBackgroundAgentCount"] = counter
    record = {"status": "idle", "statusUpdatedAt": (time.time() - 1000) * 1000}
    if counter in ("absent", 0) and counter is not False:
        restart.idle_gate(record, [boundary], config.read_restart_config({}))
    else:
        with pytest.raises(restart.Refusal, match="background-work"):
            restart.idle_gate(record, [boundary], config.read_restart_config({}))


@pytest.mark.parametrize("source", ["message", "queue", "structured"])
def test_given_task_completion_when_in_transcript_then_launch_is_finished(source):
    notification = "<task-notification><task-id>task-1</task-id><status>completed</status></task-notification>"
    row = {
        "message": {"type": "user", "message": {"content": notification}},
        "queue": {"type": "queue-operation", "operation": "enqueue", "content": notification},
        "structured": {"type": "task-notification", "taskId": "task-1", "status": "completed"},
    }[source]
    rows = [
        {"type": "user", "toolUseResult": {"backgroundTaskId": "task-1"}},
        row,
        {"type": "system", "subtype": "turn_duration"},
    ]
    restart.idle_gate(
        {"status": "idle", "statusUpdatedAt": (time.time() - 1000) * 1000}, rows, config.read_restart_config({})
    )


@pytest.fixture
def mcp_tree(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(restart.Path, "home", lambda: home)
    child = SimpleNamespace(pid=2, ppid=lambda: 1, cmdline=lambda: ["python", "notifier.py", "mcp-adapter"])
    child.create_time = lambda: 1001
    children = [child, SimpleNamespace(pid=3, ppid=lambda: 2, cmdline=lambda: ["worker"])]
    monkeypatch.setattr(
        restart.psutil,
        "Process",
        lambda pid: SimpleNamespace(children=lambda **kwargs: children, create_time=lambda: 1000),
    )
    return SimpleNamespace(home=home, cwd=tmp_path, child=child, children=children)


@pytest.mark.parametrize("location", ["user", "user-project", "project"])
@pytest.mark.parametrize(("delay", "allowed"), [(0, True), (60, True), (61, False), (-1, False), (None, False)])
def test_given_wrapped_mcp_when_birth_and_config_checked_then_only_startup_server_allowed(
    mcp_tree, location, delay, allowed
):
    servers = {"mcpServers": {"notifier": {"command": "~/.claude/bin/notifier", "args": ["mcp-adapter"]}}}
    path = mcp_tree.cwd / ".mcp.json" if location == "project" else mcp_tree.home / ".claude.json"
    data = {"projects": {str(mcp_tree.cwd): servers}} if location == "user-project" else servers
    path.write_text(json.dumps(data), encoding="utf-8")
    mcp_tree.child.create_time = lambda: None if delay is None else 1000 + delay
    if allowed:
        restart.descendants_gate(1, mcp_tree.cwd)
    else:
        with pytest.raises(restart.Refusal, match="background-work"):
            restart.descendants_gate(1, mcp_tree.cwd)


@pytest.mark.parametrize("matching", [True, False])
def test_given_empty_mcp_args_when_command_resolved_then_only_same_executable_allowed(mcp_tree, matching, monkeypatch):
    command = mcp_tree.home / "notifier"
    command.touch()
    alias = mcp_tree.home / "notifier-alias"
    try:
        alias.symlink_to(command)
    except OSError:
        pytest.skip("symlink creation unavailable")
    (mcp_tree.cwd / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"notifier": {"command": str(alias), "args": []}}}), encoding="utf-8"
    )
    mcp_tree.child.cmdline = lambda: [str(command) if matching else "unrelated", "extra"]
    monkeypatch.setattr(restart.shutil, "which", lambda name: None)
    if matching:
        restart.descendants_gate(1, mcp_tree.cwd)
    else:
        with pytest.raises(restart.Refusal, match="background-work"):
            restart.descendants_gate(1, mcp_tree.cwd)


@pytest.mark.parametrize("recorded", ["21660029", 21660029, "21660030", "bad", "21660029.0", None, True])
def test_given_linux_registry_marker_when_normalized_then_only_same_ticks_match(tmp_path, recorded):
    root = tmp_path / "101"
    root.mkdir()
    (root / "stat").write_text("101 (claude) S " + "0 " * 18 + "21660029\n", encoding="utf-8")
    actual = ProcfsProbe(tmp_path).start_time_match(101, probes.normalize_proc_start(recorded))
    assert (actual is StartTimeMatch.MATCH) is (type(recorded) in (str, int) and recorded in ("21660029", 21660029))


def test_given_macos_registry_marker_when_normalized_then_lstart_remains_supported(monkeypatch):
    from datetime import UTC, datetime

    created = datetime(2026, 10, 5, 21, 28, 33, tzinfo=UTC).timestamp()
    monkeypatch.setattr(probes.psutil, "Process", lambda pid: SimpleNamespace(create_time=lambda: created))
    recorded = probes.normalize_proc_start("Mon Oct  5 21:28:33 2026")
    assert PsutilProbe().start_time_match(101, recorded) is StartTimeMatch.MATCH


@pytest.mark.parametrize("recorded", ["21660029", "21660030"])
def test_given_string_marker_when_registry_liveness_checked_then_birth_is_compared(tmp_path, recorded):
    root = tmp_path / "101"
    root.mkdir()
    (root / "stat").write_text("101 (claude) S " + "0 " * 18 + "21660029\n", encoding="utf-8")
    assert main._cc_record_liveness({"pid": 101, "procStart": recorded}, tmp_path) == (
        "live" if recorded == "21660029" else "unproven"
    )


def test_given_summary_notifications_when_idle_checked_then_no_launch_is_inferred():
    notification = "<task-notification><summary>Task finished</summary></task-notification>"
    rows = [
        {"type": "queue-operation", "operation": "enqueue", "content": notification},
        {"type": "queue-operation", "operation": "dequeue"},
        {"type": "user", "message": {"content": notification}},
        {"type": "system", "subtype": "turn_duration"},
    ]
    restart.idle_gate(
        {"status": "idle", "statusUpdatedAt": (time.time() - 1000) * 1000}, rows, config.read_restart_config({})
    )


def test_given_nested_matching_process_when_parent_unclassified_then_background_refuses(mcp_tree):
    (mcp_tree.cwd / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"notifier": {"command": "notifier", "args": ["mcp-adapter"]}}}), encoding="utf-8"
    )
    mcp_tree.child.ppid = lambda: 4
    mcp_tree.children.append(
        SimpleNamespace(pid=4, ppid=lambda: 1, cmdline=lambda: ["unrelated"], create_time=lambda: 1001)
    )
    with pytest.raises(restart.Refusal, match="background-work"):
        restart.descendants_gate(1, mcp_tree.cwd)
