"""Installed runtime shapes identify startup servers without learning live children."""

import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from ai_cli import config
from ai_cli import session_restart as restart


@pytest.fixture
def runtime_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_xdg_config_home", lambda: tmp_path)
    monkeypatch.setattr(restart.Path, "home", lambda: tmp_path)
    root = tmp_path / "runtime"
    root.mkdir()
    for name in ("python", "notifier.py", "node", "tool.js"):
        (root / name).touch()
    directory = tmp_path / "restart-session.d"
    directory.mkdir()
    allowlist = directory / "mcp-servers.toml"
    fixture = Path(__file__).parent / "fixtures" / "restart_mcp_servers.toml"
    allowlist.write_text(fixture.read_text().replace("/opt/example", root.as_posix()), encoding="utf-8")
    argv = [(root / "python").as_posix(), "-P", (root / "notifier.py").as_posix(), "mcp-adapter"]
    child = SimpleNamespace(
        pid=2, ppid=lambda: 1, create_time=lambda: 1001, cmdline=lambda: argv, exe=lambda: str(root / "python")
    )
    children = [child, SimpleNamespace(pid=3, ppid=lambda: 2)]
    monkeypatch.setattr(
        restart.psutil,
        "Process",
        lambda pid: SimpleNamespace(children=lambda **kwargs: children, create_time=lambda: 1000),
    )
    return SimpleNamespace(cwd=tmp_path, root=root, child=child, children=children, argv=argv, allowlist=allowlist)


@pytest.mark.parametrize("shape", ["exec-wrapper", "npm-title"])
@pytest.mark.parametrize("delay", [0, 60])
def test_given_ac_g4_verified_runtime_when_startup_root_checked_then_subtree_allowed(runtime_tree, shape, delay):
    tree = runtime_tree
    tree.child.create_time = lambda: 1000 + delay
    if shape == "npm-title":
        tree.child.cmdline = lambda: ["npm exec example-tool", "", ""]
        tree.child.exe = lambda: str(tree.root / "node")
    restart.descendants_gate(1, tree.cwd)
    assert restart.load_commands() == {}


@pytest.mark.parametrize(
    "mismatch", ["worker", "suffix", "late", "unknown", "exe", "extra", "blank", "nested", "probe", "birth"]
)
def test_given_ac_g5_unclassified_runtime_when_checked_then_background_refuses(runtime_tree, mismatch):
    tree = runtime_tree
    if mismatch == "worker":
        tree.child.cmdline = lambda: [str(tree.root / "python"), "worker.py"]
    elif mismatch == "suffix":
        tree.argv.insert(1, "worker.py")
    elif mismatch == "late":
        tree.child.create_time = lambda: 1061
    elif mismatch == "unknown":
        tree.child.cmdline = lambda: ["unknown"]
    elif mismatch == "exe":
        tree.child.exe = lambda: str(tree.root / "node")
    elif mismatch == "extra":
        tree.argv.append("extra")
    elif mismatch == "blank":
        tree.child.cmdline = lambda: ["npm exec example-tool", ""]
        tree.child.exe = lambda: str(tree.root / "node")
    elif mismatch == "nested":
        tree.child.ppid = lambda: 4
        tree.children.append(
            SimpleNamespace(pid=4, ppid=lambda: 1, create_time=lambda: 1001, cmdline=lambda: ["unknown"])
        )
    elif mismatch == "birth":
        tree.child.create_time = lambda: float("nan")
    else:

        def denied():
            raise psutil.AccessDenied(2)

        tree.child.exe = denied
    with pytest.raises(restart.Refusal, match="background-work"):
        restart.descendants_gate(1, tree.cwd)


def test_given_ac_g5_allowed_server_when_transcript_task_unfinished_then_refuses(runtime_tree):
    restart.descendants_gate(1, runtime_tree.cwd)
    path = runtime_tree.cwd / "session.jsonl"
    path.write_text(
        '{"toolUseResult":{"backgroundTaskId":"task-1"}}\n{"type":"system","subtype":"turn_duration"}\n',
        encoding="utf-8",
    )
    with pytest.raises(restart.Refusal, match="background-work"):
        restart.idle_gate(
            {"status": "idle", "statusUpdatedAt": (time.time() - 1000) * 1000},
            restart.transcript_tail(path),
            config.read_restart_config({}),
        )


@pytest.mark.parametrize("change", ["size", "digest", "missing", "unreadable"])
def test_given_ac_g6_changed_identity_when_checked_then_regeneration_required(runtime_tree, monkeypatch, change):
    tree = runtime_tree
    script = tree.root / "notifier.py"
    if change == "size":
        script.write_bytes(b"updated")
    elif change == "digest":
        tree.allowlist.write_text(tree.allowlist.read_text().replace("e3b0c442", "00000000"))
    elif change == "missing":
        # Verify the exact fixture-owned empty target before removing it.
        assert script.is_file() and script.read_bytes() == b""
        script.unlink()
    else:
        original = Path.open

        def denied(path, *args, **kwargs):
            if path == script:
                raise PermissionError("identity unavailable")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(restart.Refusal, match="background-work") as caught:
        restart.descendants_gate(1, tree.cwd)
    assert "notifier" in caught.value.detail
    assert "regenerate the allowlist" in caught.value.detail


def test_given_ac_g5_missing_allowlist_when_wrapper_unclassified_then_refuses(runtime_tree):
    assert "[[servers]]" in runtime_tree.allowlist.read_text()
    runtime_tree.allowlist.unlink()
    with pytest.raises(restart.Refusal, match="background-work"):
        restart.descendants_gate(1, runtime_tree.cwd)


@pytest.mark.parametrize(
    "contents", ["version = 2", 'version = 1\nservers = "bad"', 'version = 1\n[[servers]]\nname="bad"']
)
def test_given_invalid_allowlist_when_loaded_then_refuses(runtime_tree, contents):
    runtime_tree.allowlist.write_text(contents, encoding="utf-8")
    with pytest.raises(restart.Refusal, match="background-work"):
        restart.descendants_gate(1, runtime_tree.cwd)
