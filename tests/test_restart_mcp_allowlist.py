"""Installed runtime shapes identify startup servers without learning live children."""

import hashlib
import json
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from ai_cli import config
from ai_cli import session_restart as restart

REGISTRATION_FIXTURE = Path(__file__).parent / "fixtures" / "claude_registration_digest.json"


@pytest.fixture
def runtime_tree(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()
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
    "contents", ["version = 2", 'version = 2\nservers = "bad"', 'version = 2\n[[servers]]\nname="bad"']
)
def test_given_invalid_allowlist_when_loaded_then_refuses(runtime_tree, contents):
    runtime_tree.allowlist.write_text(contents, encoding="utf-8")
    with pytest.raises(restart.Refusal, match="background-work"):
        restart.descendants_gate(1, runtime_tree.cwd)


def test_given_v1_certificate_when_loaded_then_restart_mcp_remedy_is_required(runtime_tree):
    tree = runtime_tree
    original = tree.allowlist.read_text(encoding="utf-8")
    tree.allowlist.write_text(original.replace("version = 2", "version = 1", 1), encoding="utf-8")
    with pytest.raises(restart.Refusal, match="background-work") as caught:
        restart.descendants_gate(1, tree.cwd)
    assert "ai-harness apply --only restart-mcp" in caught.value.detail


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_registries(directory, registries):
    for name, document in registries.items():
        _write_json(directory / name, document)


def _mutated_document(document, case):
    document = deepcopy(document)
    parent = document
    *keys, last_key = case["keys"]
    for key in keys:
        parent = parent[key]
    if "value" in case:
        parent[last_key] = case["value"]
    else:
        del parent[last_key]
    return document


def test_given_pinned_fixture_when_digest_recomputed_then_producer_cases_match(tmp_path):
    raw = REGISTRATION_FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "6235322cce5f93bfa2471e7589e48fb019a31b6121c67b678dabe84d61c752d0"

    from ai_cli._claude_registration import CLAUDE_DIGEST_SCHEME, ArtifactError, claude_registration_digest

    fixture = json.loads(raw)
    assert fixture["scheme"] == CLAUDE_DIGEST_SCHEME
    directory = tmp_path.resolve() / "claude"
    receipt = tmp_path.resolve() / "registration.json"
    _write_json(receipt, fixture["receipt"])
    _write_registries(directory, fixture["registries"])
    assert claude_registration_digest(directory, receipt) == fixture["sha256"]
    for case in fixture["cases"]:
        path = directory / case["file"]
        original = fixture["registries"][case["file"]]
        _write_json(path, _mutated_document(original, case))
        if case["expected"] == "invalid":
            with pytest.raises(ArtifactError):
                claude_registration_digest(directory, receipt)
        else:
            digest = claude_registration_digest(directory, receipt)
            assert (digest == fixture["sha256"]) == (case["expected"] == "same"), case["name"]
        _write_json(path, original)


def _write_core_certificate(tree, *, include_registration=True):
    lines = [
        "version = 2",
        "[[servers]]",
        'name = "ai-harness-core"',
        f"executable = {json.dumps(str(tree.root / 'python'))}",
        f"argv = {json.dumps(tree.argv)}",
    ]
    for path in tree.stamps:
        raw = path.read_bytes()
        lines.extend(
            [
                "[[servers.identities]]",
                f"path = {json.dumps(str(path))}",
                f"size = {len(raw)}",
                f'sha256 = "{hashlib.sha256(raw).hexdigest()}"',
            ]
        )
    if include_registration:
        lines.append("[servers.claude_registration]")
        lines.extend(f"{key} = {json.dumps(value)}" for key, value in tree.registration.items())
    tree.allowlist.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def core_tree(runtime_tree, monkeypatch):
    tree = runtime_tree
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    tree.argv[:] = [str(tree.root / "python"), "-m", "example.mcp"]
    tree.fixture = json.loads(REGISTRATION_FIXTURE.read_bytes())
    tree.claude = tree.cwd / ".claude"
    tree.receipt = tree.root / "registration.json"
    _write_json(tree.receipt, tree.fixture["receipt"])
    _write_registries(tree.claude, tree.fixture["registries"])
    tree.registration = {
        "receipt": str(tree.receipt),
        "scheme": tree.fixture["scheme"],
        "sha256": tree.fixture["sha256"],
    }
    tree.stamps = [
        tree.root / name for name in ("python", "registration.json", "artifact.json", ".mcp.json", "server.py")
    ]
    _write_json(tree.root / "artifact.json", {"package_version": "0.1.0"})
    _write_json(
        tree.root / ".mcp.json",
        {"mcpServers": {"ai-harness-core": {"command": tree.argv[0], "args": tree.argv[1:]}}},
    )
    (tree.root / "server.py").write_text("def main():\n    pass\n", encoding="utf-8")
    _write_core_certificate(tree)
    return tree


@pytest.mark.parametrize(
    "case",
    json.loads(REGISTRATION_FIXTURE.read_bytes())["cases"],
    ids=lambda case: case["name"],
)
def test_given_core_certificate_when_claude_rewrites_registry_then_gate_checks_owned_fields(core_tree, case):
    tree = core_tree
    restart.descendants_gate(1, tree.cwd)
    path = tree.claude / case["file"]
    before = path.read_bytes()
    _write_json(path, _mutated_document(tree.fixture["registries"][case["file"]], case))
    assert path.read_bytes() != before
    if case["expected"] == "same":
        restart.descendants_gate(1, tree.cwd)
    else:
        with pytest.raises(restart.Refusal, match="background-work"):
            restart.descendants_gate(1, tree.cwd)


@pytest.mark.parametrize("override", [False, True])
def test_given_core_certificate_when_active_registry_keys_are_reordered_then_gate_passes(
    core_tree, monkeypatch, override
):
    tree = core_tree
    if override:
        alternate = tree.cwd / "custom-claude"
        _write_registries(alternate, tree.fixture["registries"])
        (tree.claude / "settings.json").write_text("invalid default config", encoding="utf-8")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(alternate))
        tree.claude = alternate
    restart.descendants_gate(1, tree.cwd)
    changed = False
    for name, document in tree.fixture["registries"].items():
        path = tree.claude / name
        reordered = json.dumps(document, sort_keys=True)
        changed |= reordered.encode("utf-8") != path.read_bytes()
        path.write_text(reordered, encoding="utf-8")
    assert changed
    restart.descendants_gate(1, tree.cwd)


@pytest.mark.parametrize("name", ["python", "registration.json", "artifact.json", ".mcp.json", "server.py"])
def test_given_core_certificate_when_stamped_file_changes_then_gate_refuses(core_tree, name):
    tree = core_tree
    restart.descendants_gate(1, tree.cwd)
    path = tree.root / name
    original = path.read_bytes()
    changed = original.replace(b"example.mcp", b"example.alt") if name == ".mcp.json" else b" " + original
    assert original != changed
    path.write_bytes(changed)
    with pytest.raises(restart.Refusal, match="background-work"):
        restart.descendants_gate(1, tree.cwd)


@pytest.mark.parametrize("part", ["executable", "argv"])
def test_given_core_certificate_when_live_runtime_differs_then_gate_refuses(core_tree, part):
    tree = core_tree
    restart.descendants_gate(1, tree.cwd)
    if part == "executable":
        tree.child.exe = lambda: str(tree.root / "node")
    else:
        tree.child.cmdline = lambda: [*tree.argv, "extra"]
    with pytest.raises(restart.Refusal, match="background-work") as caught:
        restart.descendants_gate(1, tree.cwd)
    assert caught.value.detail == "unclassified live descendant"


@pytest.mark.parametrize(
    "problem",
    [
        "missing-table",
        "missing-field",
        "extra-field",
        "bad-scheme",
        "bad-sha256",
        "relative-receipt",
        "unstamped-receipt",
    ],
)
def test_given_core_certificate_when_registration_stamp_is_invalid_then_gate_refuses(core_tree, problem):
    tree = core_tree
    restart.descendants_gate(1, tree.cwd)
    if problem == "missing-field":
        del tree.registration["receipt"]
    elif problem == "extra-field":
        tree.registration["extra"] = "unexpected"
    elif problem == "bad-scheme":
        tree.registration["scheme"] = "semantic-v2"
    elif problem == "bad-sha256":
        tree.registration["sha256"] = "invalid"
    elif problem == "relative-receipt":
        tree.registration["receipt"] = "registration.json"
    elif problem == "unstamped-receipt":
        tree.stamps.remove(tree.receipt)
    _write_core_certificate(tree, include_registration=problem != "missing-table")
    with pytest.raises(restart.Refusal, match="background-work"):
        restart.descendants_gate(1, tree.cwd)


def test_given_core_certificate_when_registry_is_unreadable_then_gate_refuses(core_tree, monkeypatch):
    tree = core_tree
    restart.descendants_gate(1, tree.cwd)
    original = Path.open

    def denied(path, *args, **kwargs):
        if path == tree.claude / "settings.json":
            raise PermissionError("registration unavailable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(restart.Refusal, match="background-work"):
        restart.descendants_gate(1, tree.cwd)
