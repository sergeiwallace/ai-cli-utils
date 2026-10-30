"""CLI behaviour with fixture registries, transcripts, process probes and tmux."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import select
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import psutil
import pytest
from conftest import run_cli
from process_ownership import owned_process

from ai_cli import config, main
from ai_cli import session_restart as restart
from ai_cli import session_run_state as run_state
from ai_cli.process_probe import ProcessIdentity, StartTimeMatch

# Captured before any fixture replaces it.
_REAL_PROCESS = psutil.Process

SESSION_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ID = "22222222-2222-4222-8222-222222222222"


class _ModuleView(SimpleNamespace):
    """Override a module only as seen by the restart implementation."""

    def __init__(self, module, **overrides):
        super().__init__(**(vars(module) | overrides))


@pytest.fixture
def npx_tree(tmp_path, monkeypatch):
    home = tmp_path.resolve()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(config, "get_xdg_config_home", lambda: home / "config")
    node = home / "bin" / "node"
    launcher = home / "lib" / "npm" / "bin" / "npx-cli.js"
    script = home / ".npm" / "_npx" / "certified" / "node_modules" / "example-tool" / "tool.js"
    manifest = script.with_name("package.json")
    for path in (node, launcher, script, manifest):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    argv = ["npm exec example-tool@latest", "", ""]
    root = SimpleNamespace(
        pid=11, ppid=lambda: 1, create_time=lambda: 1001, exe=lambda: str(node), cmdline=lambda: argv
    )
    helper = SimpleNamespace(
        pid=12, ppid=lambda: 11, exe=lambda: str(node), cmdline=lambda: [str(node), str(script)], cwd=lambda: str(home)
    )
    children = [root, helper]
    monkeypatch.setattr(
        restart.psutil,
        "Process",
        lambda pid: SimpleNamespace(children=lambda **kwargs: children, create_time=lambda: 1000),
    )
    allowlist = home / "config" / "restart-session.d" / "mcp-servers.toml"
    allowlist.parent.mkdir(parents=True)

    def write_entries(installations):
        lines = ["version = 1"]
        for index, installed in enumerate(installations):
            lines.extend(
                [
                    "[[servers]]",
                    f'name = "example-{index}"',
                    f"executable = {json.dumps(str(node))}",
                    f"argv = {json.dumps(argv)}",
                ]
            )
            for path in (node, launcher, installed, installed.with_name("package.json")):
                lines.extend(
                    [
                        "[[servers.identities]]",
                        f"path = {json.dumps(str(path))}",
                        f"size = {path.stat().st_size}",
                        f"sha256 = {json.dumps(hashlib.sha256(path.read_bytes()).hexdigest())}",
                    ]
                )
        allowlist.write_text("\n".join(lines) + "\n", encoding="utf-8")

    write_entries([script])
    return SimpleNamespace(
        home=home,
        node=node,
        launcher=launcher,
        script=script,
        root=root,
        helper=helper,
        children=children,
        allowlist=allowlist,
        write_entries=write_entries,
    )


@pytest.mark.parametrize("argument", ["absolute", "relative", "basename", "shell-command", "option"])
def test_given_npx_root_when_descendant_uses_unstamped_installation_then_background_refuses(npx_tree, argument):
    tree = npx_tree
    restart.descendants_gate(1, tree.home)
    newer = tree.home / ".npm" / "_npx" / "newer" / "node_modules" / "example-tool" / "tool.js"
    newer.parent.mkdir(parents=True)
    newer.write_text("new version", encoding="utf-8")
    arg = {
        "absolute": str(newer),
        "relative": str(newer.relative_to(tree.home)),
        "basename": newer.name,
        "shell-command": f"exec {shlex.quote(str(newer))}",
        "option": f"--script={newer}",
    }[argument]
    tree.helper.cmdline = lambda: [str(tree.node), "-c", arg] if argument == "shell-command" else [str(tree.node), arg]
    if argument == "basename":
        tree.helper.cwd = lambda: str(newer.parent)
    assert tree.script.is_file() and tree.launcher.is_file()
    with pytest.raises(restart.Refusal, match="background-work") as caught:
        restart.descendants_gate(1, tree.home)
    assert "uncertified npx installation" in caught.value.detail
    assert "regenerate the MCP launcher certificates" in caught.value.detail


def test_given_certified_npx_root_when_node_and_shell_helpers_idle_then_passes(npx_tree, monkeypatch):
    tree = npx_tree
    shell = tree.home / "bin" / "sh"
    shell.touch()
    resolve = restart._command_path
    monkeypatch.setattr(
        restart, "_command_path", lambda command, cwd: shell if command == "/bin/sh" else resolve(command, cwd)
    )
    tree.children.insert(
        0,
        SimpleNamespace(
            pid=13,
            ppid=lambda: 12,
            exe=lambda: str(shell),
            cmdline=lambda: ["sh", "-c", shlex.quote(str(tree.script))],
            cwd=lambda: str(tree.home),
        ),
    )
    restart.descendants_gate(1, tree.home)


@pytest.mark.parametrize("program", ["curl", "git", "other-node"])
def test_given_npx_root_when_unrelated_child_running_then_exe_and_argv_refuse(npx_tree, program):
    tree = npx_tree
    executable = tree.home / "bin" / program
    executable.touch()
    argv = [str(executable), "--version"]
    tree.helper.exe = lambda: str(executable)
    tree.helper.cmdline = lambda: argv
    with pytest.raises(restart.Refusal, match="background-work") as caught:
        restart.descendants_gate(1, tree.home)
    assert repr(str(executable)) in caught.value.detail
    assert repr(argv) in caught.value.detail


@pytest.mark.parametrize("failure", ["vanished", "denied", "proc", "relative", "empty-argv"])
def test_given_npx_root_when_descendant_unresolvable_then_background_refuses(npx_tree, failure):
    tree = npx_tree

    def unavailable():
        if failure == "vanished":
            raise restart.psutil.NoSuchProcess(12)
        if failure == "denied":
            raise restart.psutil.AccessDenied(12)
        if failure == "proc":
            raise OSError("process entry unreadable")
        return "unresolved-node"

    if failure == "empty-argv":
        tree.helper.cmdline = list
    else:
        tree.helper.exe = unavailable
    with pytest.raises(restart.Refusal, match="background-work"):
        restart.descendants_gate(1, tree.home)


def test_given_non_npx_root_when_descendant_unclassified_then_legacy_behavior_passes(npx_tree):
    tree = npx_tree
    contents = tree.allowlist.read_text(encoding="utf-8")
    for original, name in (
        (tree.launcher, "server.js"),
        (tree.script, "tool.js"),
        (tree.script.with_name("package.json"), "package.json"),
    ):
        replacement = tree.home / name
        replacement.write_bytes(original.read_bytes())
        contents = contents.replace(json.dumps(str(original)), json.dumps(str(replacement)))
    tree.allowlist.write_text(contents, encoding="utf-8")
    tree.children[1] = SimpleNamespace(pid=12, ppid=lambda: 11)
    restart.descendants_gate(1, tree.home)


@pytest.mark.parametrize("selected", ["first", "second", "mixed"])
def test_given_multiple_npx_entries_when_descendants_checked_then_one_entry_must_cover_tree(npx_tree, selected):
    tree = npx_tree
    second = tree.home / ".npm" / "_npx" / "second" / "node_modules" / "example-tool" / "tool.js"
    second.parent.mkdir(parents=True)
    second.write_bytes(tree.script.read_bytes())
    second.with_name("package.json").write_bytes(tree.script.with_name("package.json").read_bytes())
    tree.write_entries([tree.script, second])
    if selected == "second":
        tree.helper.cmdline = lambda: [str(tree.node), str(second)]
    elif selected == "mixed":
        tree.children.append(
            SimpleNamespace(
                pid=13,
                ppid=lambda: 11,
                exe=lambda: str(tree.node),
                cmdline=lambda: [str(tree.node), str(second)],
                cwd=lambda: str(tree.home),
            )
        )
    if selected == "mixed":
        with pytest.raises(restart.Refusal, match="background-work") as caught:
            restart.descendants_gate(1, tree.home)
        assert "uncertified npx installation" in caught.value.detail
    else:
        restart.descendants_gate(1, tree.home)


def _is_restart_probe(argv, pane_ai):
    """Match the helper probe; Windows `which` may return the executable in a different case."""
    return (
        len(argv) == 4
        and os.path.normcase(str(argv[0])) == os.path.normcase(str(pane_ai))
        and argv[1:] == ["internal", "restart-pin", "--probe"]
    )


@pytest.fixture
def world(tmp_path, monkeypatch):
    # Process probes and signals below simulate POSIX independently of the host.
    # Platform routing is exercised separately, including Windows injection.
    monkeypatch.setattr(restart, "_signal_supported", lambda: True)
    # These are simulated POSIX capabilities, including on hosts whose Python
    # has no Unix socket family or discovers shells with a .exe suffix.
    monkeypatch.setattr(restart.socket, "AF_UNIX", 1, raising=False)
    monkeypatch.setattr(main._session_script, "resolve_session_shell", lambda: "bash")
    monkeypatch.delenv("TMUX_PANE", raising=False)
    root = tmp_path / "home"
    root.mkdir()
    binaries = root / "pane bin"
    binaries.mkdir()
    pane_ai = binaries / ("ai.exe" if sys.platform == "win32" else "ai")
    pane_ai.touch()
    pane_ai.chmod(0o755)
    monkeypatch.setattr(Path, "home", lambda: root)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state with spaces"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    cwd = tmp_path / "myproject"
    cwd.mkdir()
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text('{"remoteControlAtStartup": true}', encoding="utf-8")
    transcript = root / ".claude" / "projects" / "myproject" / f"{SESSION_ID}.jsonl"
    transcript.parent.mkdir(parents=True)
    rows = [
        {
            "type": "assistant",
            "message": {
                "id": "turn-1",
                "model": "claude-sonnet-4-6",
                "usage": {"input_tokens": 10_000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            },
        },
        {"type": "system", "subtype": "turn_duration", "pendingBackgroundAgentCount": 0},
    ]
    record = {
        "pid": 101,
        "procStart": 101,
        "sessionId": SESSION_ID,
        "name": "myproject-box-1",
        "nameSource": "user",
        "status": "idle",
        "statusUpdatedAt": (time.time() - 1000) * 1000,
        "cwd": str(cwd),
        "tmux": "c-myproject-box-1:@1.%1",
        "bridgeSessionId": "bridge-1",
        "messagingSocketPath": str(tmp_path / "socket"),
        "version": "2.1.1",
    }
    registry = root / ".claude" / "sessions" / "101.json"
    registry.parent.mkdir()
    session = "c-myproject-box-1"
    state = config.get_xdg_state_home()
    config_home = config.get_xdg_config_home()
    monkeypatch.setattr(config, "get_xdg_state_home", lambda: state)
    monkeypatch.setattr(config, "get_xdg_config_home", lambda: config_home)
    run_state.register(
        session,
        recipe={
            "engine": "c",
            "session_name": session,
            "owning_host": "host-a",
            "argv": ["ai", "c", "1"],
            "extra_args": [],
            "options": {},
            "cwd": str(cwd),
            "project": "myproject",
        },
        host="host-a",
    )
    meta = {
        "engine": "c",
        "ai_name": record["name"],
        "session": session,
        "prefix": "c-myproject-box-",
        "project_prefix": "myproject-box",
        "worktree_dir": str(cwd),
    }
    script = state / "sessions" / f"{session}.sh"
    main._write_launch_script_if_changed(script, main._engine_script_from_meta(meta))
    (state / f"session-meta-{session}.json").write_text(json.dumps(meta), encoding="utf-8")
    state_obj = SimpleNamespace(
        root=root,
        cwd=cwd,
        rows=rows,
        record=record,
        registry=registry,
        transcript=transcript,
        script=script,
        meta=meta,
        session=session,
        generation="generation-1",
        ended=False,
        identities={},
        children=[],
        signals=[],
        observer="seen",
        injected=[],
        after_prepare=None,
        processes={},
        pane_ai=pane_ai,
        pane_env={"PATH": str(binaries)},
    )
    settings = {
        "restart_session": {
            "min_uptime_seconds": 0,
            "settle_seconds": 0,
            "term_grace_seconds": 0,
            "relaunch_timeout_seconds": 0,
            "cooldown_seconds": 0,
        }
    }
    state_obj.settings = settings

    def write():
        registry.write_text(json.dumps(record), encoding="utf-8")
        transcript.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    state_obj.write = write
    write()
    probe = SimpleNamespace(
        capture_identity=lambda pid: state_obj.identities.get(pid, ProcessIdentity("fake", pid)),
        has_ended=lambda pid: pid == 101 and state_obj.ended,
        start_time_match=lambda pid, recorded: StartTimeMatch.MATCH if recorded == pid else StartTimeMatch.UNPROVEN,
    )
    state_obj.probe = probe
    monkeypatch.setattr(restart, "probe_for", lambda: probe)

    class Process:
        def __init__(self, pid):
            self.pid = pid

        def create_time(self):
            return time.time() - 4000

        def ppid(self):
            return {101: 102, 102: 103, 104: 103, 201: 104}.get(self.pid, 0)

        def parents(self):
            return [Process(102), Process(103)]

        def cmdline(self):
            return ["bash", str(script), "--ai-cli-child-body"]

        def children(self, recursive=False):
            return state_obj.children

        def environ(self):
            assert self.pid == 103
            return state_obj.pane_env.copy()

        def cwd(self):
            assert self.pid == 103
            return str(cwd)

    monkeypatch.setattr(restart.psutil, "Process", Process)
    monkeypatch.setattr(restart, "_script_source", lambda pid, path: path.read_text(encoding="utf-8"))

    def subprocess_run(argv, **kwargs):
        if _is_restart_probe(argv, pane_ai):
            assert kwargs["env"] == state_obj.pane_env
            assert kwargs["cwd"] == cwd
            return SimpleNamespace(returncode=0, stdout="restart-pin-v1\n")
        if argv[0] == "tmux":
            fields = [session, "%1", "103", state_obj.generation]
            if "pane_start_command" in argv[-1]:
                fields.append(shlex.join(["bash", str(script)]))
            return SimpleNamespace(returncode=0, stdout="\t".join(fields) + "\n")
        if argv[:3] == ["claude", "agents", "--json"]:
            replacement = {**record, "pid": 201, "procStart": 201}
            return SimpleNamespace(returncode=0, stdout=json.dumps([replacement]))
        if argv[0] == "observer":
            data = {
                "status": "seen",
                "complete": True,
                "observed_at": time.time(),
                "rows": [{"session_id": SESSION_ID, "title": record["name"], "bridge_session_id": "bridge-1"}],
            }
            if callable(state_obj.observer):
                data = state_obj.observer(data)
            return SimpleNamespace(returncode=0, stdout=json.dumps(data))
        if argv[0] == "inject":
            state_obj.injected.append(argv)
            replace()
            return SimpleNamespace(returncode=0, stdout='{"status":"submitted"}')
        raise AssertionError(f"unexpected subprocess: {argv}")

    state_obj.subprocess_run = subprocess_run
    connection = MagicMock()
    monkeypatch.setattr(restart, "socket", _ModuleView(restart.socket, socket=lambda *a: connection))
    state_obj.connection = connection

    def replace():
        state_obj.ended = True
        replacement = {**record, "pid": 201, "procStart": 201}
        (registry.parent / "201.json").write_text(json.dumps(replacement), encoding="utf-8")

    state_obj.replace = replace

    def kill(pid, sig):
        state_obj.signals.append((pid, sig))
        replace()

    monkeypatch.setattr(restart, "os", _ModuleView(os, kill=kill))
    monkeypatch.setattr(restart, "load_commands", lambda: {"observer": ["observer"]})
    state_obj.commands = lambda value: monkeypatch.setattr(restart, "load_commands", lambda: value)

    def cli(*args):
        code, out, err = run_cli(["ai", *args, "-j"], config=settings)
        return code, json.loads(out), err

    state_obj.cli = cli
    # Restore the shared subprocess module before the conftest spawn guard exits.
    # monkeypatch is finalized later and would resurrect that expired guard.
    with patch.object(restart.subprocess, "run", subprocess_run):
        yield state_obj


@pytest.mark.parametrize("missing", ["record", "recipe", "invalid"])
@pytest.mark.parametrize("mechanism", ["kill", "exit", "fallback"])
def test_given_unrelaunchable_session_when_restart_then_no_boot_recipe_before_any_action(
    world, monkeypatch, missing, mechanism
):
    from ai_cli import session_registry

    def remove(doc):
        if missing == "record":
            doc["sessions"].clear()
        else:
            doc["sessions"][0]["boot_recipe"] = {} if missing == "recipe" else {"engine": "unknown"}
        return True

    session_registry._mutate(remove)
    world.settings["restart_session"].update(mechanism="exit" if mechanism == "exit" else "kill", pre_step=True)
    monkeypatch.setattr(restart, "_signal_supported", lambda: mechanism != "fallback")
    monkeypatch.setattr(restart, "context_gate", lambda *args: False)
    world.commands({"inject": ["inject"]})
    before = session_registry.registry_path().read_bytes()
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "no-boot-recipe"
    assert ("not registered" if missing == "record" else "boot recipe") in report["detail"]
    assert not world.injected and not world.signals and not world.ended
    assert session_registry.registry_path().read_bytes() == before


def test_given_recipe_removed_after_precheck_when_final_assessment_then_no_signal(world, monkeypatch):
    from ai_cli import session_registry

    prepare = restart.prepare_request

    def remove_after_prepare(*args):
        result = prepare(*args)
        session_registry._mutate(lambda doc: doc["sessions"][0].pop("boot_recipe") is not None)
        return result

    monkeypatch.setattr(restart, "prepare_request", remove_after_prepare)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "no-boot-recipe"
    assert not world.injected and not world.signals and not world.ended


def test_given_missing_recipe_when_checking_then_boot_gate_refuses_without_action(world):
    from ai_cli import session_registry

    session_registry._mutate(lambda doc: doc["sessions"].clear() or True)
    code, report, _ = world.cli("-Y", SESSION_ID)
    assert code == 1 and report["gates"]["boot-recipe"] == "no-boot-recipe"
    assert not world.injected and not world.signals


@pytest.mark.parametrize("mechanism", ["kill", "exit", "fallback"])
@pytest.mark.parametrize("after_prepare", [False, True])
def test_given_supervisor_ai_without_restart_pin_when_restarting_then_refuses_before_action(
    world, monkeypatch, mechanism, after_prepare
):
    world.settings["restart_session"]["mechanism"] = "exit" if mechanism == "exit" else "kill"
    monkeypatch.setattr(restart, "_signal_supported", lambda: mechanism != "fallback")
    world.commands({"inject": ["inject"]})
    original_script = world.script.read_bytes()
    probes = []
    prepared = False
    prepare = restart.prepare_request

    def prepare_and_downgrade(*args):
        nonlocal prepared
        result = prepare(*args)
        prepared = True
        return result

    monkeypatch.setattr(restart, "prepare_request", prepare_and_downgrade)

    def run(argv, **kwargs):
        if _is_restart_probe(argv, world.pane_ai):
            probes.append((argv, kwargs))
            if not after_prepare or prepared:
                return SimpleNamespace(returncode=1, stdout="", stderr="unknown action: restart-pin")
        return world.subprocess_run(argv, **kwargs)

    with patch.object(restart.subprocess, "run", run):
        code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "supervisor-upgrade-required"
    assert not world.signals and not world.injected
    assert probes and probes[-1][1]["env"] == world.pane_env
    assert probes[-1][1]["cwd"] == world.cwd
    if not after_prepare:
        assert world.script.read_bytes() == original_script
        assert not restart.state_paths(world.session)[2].exists()


def test_given_restart_pin_probe_when_dispatched_then_no_config_or_request_is_read(monkeypatch, capsys):
    def unexpected(*args):
        pytest.fail("capability probe must not read configuration or restart state")

    monkeypatch.setattr(config, "load_config", unexpected)
    monkeypatch.setattr(restart, "child_pin", unexpected)
    with pytest.raises(SystemExit) as exited:
        main._handle_internal(["restart-pin", "--probe"])
    assert exited.value.code == 0 and capsys.readouterr().out.strip() == "restart-pin-v1"


@pytest.mark.parametrize("failure", ["missing-path", "missing-executable", "unreadable", "timeout", "wrong-reply"])
def test_given_unproven_restart_pin_when_checked_then_upgrade_is_required(world, monkeypatch, failure):
    if failure == "missing-path":
        world.pane_env.clear()
    elif failure == "missing-executable":
        world.pane_env["PATH"] = str(world.cwd / "missing")
    elif failure == "unreadable":

        def unreadable(process):
            raise restart.psutil.AccessDenied(process.pid)

        monkeypatch.setattr(restart.psutil.Process, "environ", unreadable)

    def run(argv, **kwargs):
        if failure == "timeout":
            raise restart.subprocess.TimeoutExpired(argv, 15)
        return SimpleNamespace(returncode=0, stdout="unrelated response")

    with patch.object(restart.subprocess, "run", run):
        with pytest.raises(restart.Refusal, match="supervisor-upgrade-required"):
            restart._restart_pin_gate(103, world.cwd, world.session)
    assert not world.signals and not world.injected


@pytest.mark.parametrize("entry", ["", ".", "../home/pane bin"])
def test_given_relative_supervisor_path_when_probed_then_child_directory_is_used(world, entry):
    world.pane_env["PATH"] = entry
    executable = world.pane_ai if entry.startswith("..") else world.cwd / world.pane_ai.name
    if executable != world.pane_ai:
        executable.touch()
        executable.chmod(0o755)
    probes = []

    def run(argv, **kwargs):
        probes.append(argv)
        assert Path(argv[0]).resolve() == executable.resolve()
        assert argv[1:] == ["internal", "restart-pin", "--probe"]
        assert kwargs["cwd"] == world.cwd and kwargs["env"] == world.pane_env
        return SimpleNamespace(returncode=0, stdout="restart-pin-v1\n")

    with patch.object(restart.subprocess, "run", run):
        restart._restart_pin_gate(103, world.cwd, world.session)
    assert len(probes) == 1


# macOS returns an EMPTY environment for an Apple platform binary such as /bin/zsh or
# /bin/bash, which is what every real supervisor runs under there; psutil reports it as
# `{}` rather than raising. The pane's environment is then read back from tmux, which
# holds the `new-session -e PATH=...` the launcher set.
@pytest.mark.parametrize("tmux_has_path", [True, False])
def test_given_macos_withholds_supervisor_environment_when_probed_then_tmux_pane_environment_decides(
    world, monkeypatch, tmux_has_path
):
    # Replace only this module's view of sys: patching sys.platform itself would also
    # change shutil.which's executable lookup on a Windows host.
    monkeypatch.setattr(restart, "sys", SimpleNamespace(platform="darwin"))
    pane_path = str(world.pane_ai.parent)
    world.pane_env.clear()
    shown = {
        ("-g",): f"PATH=/usr/bin\nHOME={world.root}\n-STALE\n",
        ("-t", world.session): (f"PATH={pane_path}\n" if tmux_has_path else "") + "AI_SESSION_STARTED=1\n",
    }
    probes = []

    def run(argv, **kwargs):
        if argv[:2] == ["tmux", "show-environment"]:
            return SimpleNamespace(returncode=0, stdout=shown[tuple(argv[2:])])
        probes.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="restart-pin-v1\n")

    with patch.object(restart.subprocess, "run", run):
        if not tmux_has_path:
            # The global /usr/bin holds no restart-pin `ai`, so the gate must still refuse.
            with pytest.raises(restart.Refusal, match="supervisor-upgrade-required"):
                restart._restart_pin_gate(103, world.cwd, world.session)
            assert not probes
            return
        restart._restart_pin_gate(103, world.cwd, world.session)
    assert len(probes) == 1
    argv, kwargs = probes[0]
    assert Path(argv[0]).resolve() == world.pane_ai.resolve()
    assert kwargs["env"] == {"PATH": pane_path, "HOME": str(world.root), "AI_SESSION_STARTED": "1"}


def test_given_linux_supervisor_with_empty_environment_when_probed_then_tmux_is_not_consulted(world, monkeypatch):
    # Replace only this module's view of sys: patching sys.platform itself would also
    # change shutil.which's executable lookup on a Windows host.
    monkeypatch.setattr(restart, "sys", SimpleNamespace(platform="linux"))
    world.pane_env.clear()
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=f"PATH={world.pane_ai.parent}\n")

    with patch.object(restart.subprocess, "run", run):
        with pytest.raises(restart.Refusal, match="supervisor-upgrade-required"):
            restart._restart_pin_gate(103, world.cwd, world.session)
    assert not calls


@pytest.mark.parametrize("worktree_dir", [".", ""])
def test_given_unpinned_working_directory_when_checked_then_supervisor_cwd_is_used(world, worktree_dir):
    world.meta["worktree_dir"] = worktree_dir
    meta_path = config.get_xdg_state_home() / f"session-meta-{world.session}.json"
    assert restart._object(meta_path)["worktree_dir"] == str(world.cwd)
    meta_path.write_text(json.dumps(world.meta), encoding="utf-8")
    main._write_launch_script_if_changed(world.script, main._engine_script_from_meta(world.meta))
    code, report, _ = world.cli("-Y", SESSION_ID)
    assert code == 0 and report["gates"]["live-supervisor-uptime"] == "PASS"
    assert not world.signals and not world.injected


@pytest.mark.parametrize("target", [SESSION_ID, "myproject-box-1", "c-myproject-box-1"])
@pytest.mark.parametrize("flag", ["-X", "--restart-session"])
def test_given_alias_when_restart_then_one_pid(world, target, flag):
    code, report, _ = world.cli(flag, target)
    assert code == 0, report
    assert (code, report["reason"], report["local"], report["peer"], report["mobile"]) == (
        0,
        "restarted",
        "VERIFIED",
        "VERIFIED",
        "UNVERIFIED",
    )
    assert world.signals == [(101, signal.SIGTERM)]
    assert world.connection.__enter__.return_value.connect.call_count == 2
    assert restart._object(restart.state_paths(world.session)[2])["status"] == "complete"


@pytest.fixture
def event_world(world, monkeypatch):
    """Exercise the real restart gates with a one-shot child exec receipt."""
    monkeypatch.setattr(restart.psutil.Process, "create_time", lambda self: 1234.5)
    listener = MagicMock()
    listener.getsockname.return_value = ("127.0.0.1", 12345)
    connection = MagicMock()

    def reply(*args):
        request = restart._object(restart.state_paths(world.session)[2])
        return io.BytesIO(
            json.dumps(
                {"request_id": request["request_id"], "replacement_pid": 201, "replacement_start_time": 1234.5}
            ).encode()
            + b"\n"
        )

    connection.makefile.side_effect = reply
    listener.accept.return_value = (connection, ("127.0.0.1", 12346))
    monkeypatch.setattr(restart, "socket", _ModuleView(socket, socket=lambda *args: listener))
    # The kernel wait is the only asynchronous boundary in this fixture.
    monkeypatch.setattr(restart, "select", _ModuleView(select, select=lambda *args: ([listener], [], [])))
    world.listener = listener
    world.connection = connection
    return world


@pytest.mark.parametrize("stranger", [b"", b'{"request_id":"other"}\n', b"invalid\n"])
def test_given_stranger_receipt_when_term_ignored_then_escalates_and_accepts_genuine_receipt(
    event_world, monkeypatch, stranger
):
    world = event_world
    world.settings["restart_session"].update(term_grace_seconds=10, relaunch_timeout_seconds=10)
    clock = [100.0]
    waits = []
    genuine = world.connection.makefile.side_effect
    replies = iter([io.BytesIO(stranger), None])
    world.connection.makefile.side_effect = lambda *args: next(replies) or genuine()

    def wait(readers, writers, errors, timeout):
        waits.append(timeout)
        if len(waits) == 1:
            clock[0] += 2
            return readers, [], []
        if len(waits) == 2:
            clock[0] += timeout
            return [], [], []
        assert world.signals[-1] == (101, signal.SIGKILL)
        return readers, [], []

    def kill(pid, sig):
        world.signals.append((pid, sig))
        if sig == signal.SIGKILL:
            world.replace()

    monkeypatch.setattr(restart, "select", _ModuleView(select, select=wait))
    monkeypatch.setattr(
        restart, "time", _ModuleView(time, monotonic=lambda: clock[0], sleep=Mock(side_effect=AssertionError))
    )
    monkeypatch.setattr(restart, "os", _ModuleView(os, kill=kill))
    code, report, err = world.cli("-X", SESSION_ID, "-E")
    assert (code, report["reason"], err) == (0, "started", "")
    assert world.signals == [(101, signal.SIGTERM), (101, signal.SIGKILL)]
    assert waits == [10, 8, 10]
    assert world.listener.accept.call_count == 2


@pytest.mark.parametrize("flag", ["-E", "--event-completion"])
@pytest.mark.parametrize("command", [[], ["c"]])
def test_given_event_completion_when_restarting_then_emits_identity_without_polling(
    event_world, monkeypatch, flag, command
):
    world = event_world
    monkeypatch.setattr(restart, "resolve_target", lambda target: (world.registry, world.record))
    monkeypatch.setattr(restart, "records", Mock(side_effect=AssertionError("record scan")))
    monkeypatch.setattr(restart.time, "sleep", Mock(side_effect=AssertionError("sleep")))
    monkeypatch.setattr(restart, "_verify_replacement", Mock(side_effect=AssertionError("verification loop")))
    monkeypatch.setenv(main._LAUNCH_REASON_VAR, "remote-inbox:33333333-3333-4333-8333-333333333333")
    # These values force the old termination wait to sleep if it is reached.
    world.settings["restart_session"]["term_grace_seconds"] = 10
    world.probe.has_ended = lambda pid: False
    code, out, err = run_cli(["ai", *command, "-X", SESSION_ID, flag], config=world.settings)
    report = json.loads(out)
    assert (code, report["reason"], err) == (0, "started", "")
    assert report["session_id"] == report["restart_session_id"] == SESSION_ID
    assert report["replacement_pid"] == 201
    assert report["replacement_start_time"] == 1234.5
    assert report["engine_pid"] == os.getpid()
    assert report["engine_start_time"] == 1234.5
    assert report["launch_id"] == "33333333-3333-4333-8333-333333333333"
    assert world.signals == [(101, signal.SIGTERM)]
    request = restart._object(restart.state_paths(world.session)[2])
    assert request["status"] == "complete" and request["result"] == "started"
    assert request["session_id"] == SESSION_ID
    assert restart.child_pin(world.session, world.record["name"]) == (3, "")
    world.listener.close.assert_called_once()


@pytest.mark.parametrize("failure", ["timeout", "exec", "identity"])
def test_given_event_completion_when_start_fails_then_preserves_failure_report(event_world, failure):
    world = event_world
    if failure == "timeout":
        world.listener.accept.side_effect = TimeoutError
    elif failure == "exec":
        reply = world.connection.makefile.side_effect
        world.connection.makefile.side_effect = lambda *args: io.BytesIO(reply().getvalue() + b"!")
    else:
        world.connection.makefile.side_effect = lambda *args: io.BytesIO(b'{"request_id":"other"}\n')
    code, report, err = world.cli("-X", SESSION_ID, "--event-completion")
    assert (code, err) == (1, "")
    assert report == restart._report(
        "verification-failed", detail="replacement or local bridge verification timed out", local="FAILED"
    )
    request = restart._object(restart.state_paths(world.session)[2])
    assert request["status"] == "failed"
    world.listener.close.assert_called_once()


@pytest.mark.parametrize("json_output", [False, True])
def test_given_default_restart_when_completed_then_output_is_unchanged(world, json_output):
    code, out, err = run_cli(["ai", "-X", SESSION_ID, *(["-j"] if json_output else [])], config=world.settings)
    assert (code, err) == (0, "")
    if json_output:
        assert out == (
            '{"detail": "", "local": "VERIFIED", "message_delivery": "UNVERIFIED", '
            '"mobile": "UNVERIFIED", "peer": "VERIFIED", "reason": "restarted", "replacement_pid": 201}\n'
        )
    else:
        assert out == (
            "[session] session: restart; reason=restarted; detail=; local=VERIFIED; peer=VERIFIED; "
            "mobile=UNVERIFIED; message_delivery=UNVERIFIED; replacement_pid=201\n"
        )


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("platform", ["native", "windows"])
def test_given_exec_handoff_when_launched_then_only_exec_success_closes_without_failure(tmp_path, failure, platform):
    # Keep a real exec boundary even in sandboxes that prohibit local sockets.
    # The socket fixture records sends; the successful exec must bypass finally.
    script = """
import json, os, sys
from pathlib import Path
from types import SimpleNamespace
from ai_cli import session_restart as restart
receipt_path, launched_path, failure, platform = sys.argv[1:]
if platform == "windows": restart.sys = SimpleNamespace(platform="win32")
class Connection:
    def settimeout(self, timeout): pass
    def set_inheritable(self, value): assert value is False
    def connect(self, endpoint): assert endpoint == ("127.0.0.1", 12345)
    def sendall(self, data):
        with Path(receipt_path).open("ab") as stream: stream.write(data)
    def close(self): pass
restart.socket.socket = lambda *args: Connection()
restart._object = lambda path: {"request_id": "test-request", "event_completion": {"port": 12345}}
restart._validate_request = lambda *args, **kwargs: None
def forbidden(*args): raise AssertionError("unexpected polling")
restart.time.sleep = forbidden
restart.records = forbidden
argv = [sys.executable, "-c", "import os, sys; from pathlib import Path; Path(sys.argv[1]).write_text(str(os.getpid()))", launched_path]
if failure == "True": argv = [str(Path(launched_path).with_name("missing-command"))]
sys.exit(restart.exec_replacement("test-session", argv))
"""
    receipt_path, launched_path = tmp_path / "receipt", tmp_path / "launched"
    result = subprocess.run(
        [sys.executable, "-c", script, str(receipt_path), str(launched_path), str(failure), platform],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert (result.returncode, result.stdout, result.stderr) == (1 if failure else 0, "", "")
    if failure and platform == "windows":
        assert receipt_path.read_bytes() == b"!" and not launched_path.exists()
        return
    first, rest = receipt_path.read_bytes().split(b"\n", 1)
    receipt = json.loads(first)
    assert receipt["request_id"] == "test-request"
    assert receipt["replacement_pid"] > 1 and receipt["replacement_start_time"] > 0
    if failure:
        assert rest == b"!" and not launched_path.exists()
    else:
        assert rest == b"" and int(launched_path.read_text()) == receipt["replacement_pid"]


@pytest.mark.parametrize("args", [["--event-completion"], ["-Y", SESSION_ID, "-E"], ["c", "-E"]])
def test_given_event_completion_without_restart_when_parsed_then_usage_refuses(world, args):
    code, out, err = run_cli(["ai", *args], config=world.settings)
    assert code == 1 and "--event-completion requires --restart-session" in err
    assert not out and not world.signals


@pytest.mark.parametrize("gate", ["busy", "context"])
def test_given_event_completion_when_waiting_would_be_required_then_gate_refuses_without_polling(
    event_world, monkeypatch, gate
):
    world = event_world
    monkeypatch.setenv("TMUX_PANE", "%1")
    monkeypatch.setattr(restart.time, "sleep", Mock(side_effect=AssertionError("sleep")))
    monkeypatch.setattr(restart, "context_pre_step", Mock(side_effect=AssertionError("compaction wait")))
    with patch.object(restart.subprocess, "Popen", Mock(side_effect=AssertionError("detached helper"))):
        if gate == "busy":
            world.record["status"] = "busy"
        else:
            world.settings["restart_session"].update(pre_step=True, context_threshold_pct=1)
        world.write()
        code, report, _ = world.cli("-X", SESSION_ID, "-E")
    assert code == 1 and report["reason"] == gate
    assert not world.signals


def test_given_remote_event_completion_when_restarting_then_mode_and_json_are_forwarded(world, monkeypatch):
    monkeypatch.setattr(main._config, "get_remote_machine", lambda *args: {"host": "example.com", "user": "user"})
    monkeypatch.setattr(main, "_resolve_remote_shell", lambda ssh: "bash")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout='{"reason":"started","session_id":"test-session"}')

    with patch.object(main.subprocess, "run", run):
        code, out, err = run_cli(["ai", "c", "1", "-R", "-p", "myproject", "-X", "-E"], config=world.settings)
    assert (code, json.loads(out), err) == (0, {"reason": "started", "session_id": "test-session"}, "")
    assert len(calls) == 1
    assert "--event-completion" in calls[0][-1] and "-j" in calls[0][-1]
    assert not world.signals


def test_given_event_request_when_child_pins_then_exact_transcript_and_exec_mode_are_returned(world, monkeypatch):
    settings = config.read_restart_config(world.settings)
    with restart.session_lock(world.session):
        managed = restart.managed_gate(world.record, settings)
        request = restart.prepare_request(world.record, managed, settings)
        request.update(status="acting", event_completion={"port": 12345})
        restart._atomic_json(restart.state_paths(world.session)[2], request)
        world.probe.has_ended = lambda pid: pid in (101, 102)
        monkeypatch.setattr(restart.os, "getppid", lambda: 104)
        monkeypatch.chdir(world.cwd)
        assert restart.child_pin(world.session, world.record["name"]) == (
            0,
            str(world.transcript) + "\nevent-completion",
        )
        assert restart.child_pin(world.session, world.record["name"])[0] == 1


@pytest.mark.parametrize("check", [False, True])
def test_given_string_birth_markers_when_cli_runs_then_every_identity_check_matches(world, monkeypatch, check):
    world.record["procStart"] = "101"
    del world.rows[-1]["pendingBackgroundAgentCount"]
    world.write()
    kill = restart.os.kill

    def replace_with_string_marker(pid, sig):
        kill(pid, sig)
        path = world.registry.parent / "201.json"
        record = restart._object(path)
        path.write_text(json.dumps({**record, "procStart": "201"}), encoding="utf-8")

    monkeypatch.setattr(restart.os, "kill", replace_with_string_marker)
    code, report, _ = world.cli("-Y" if check else "-X", SESSION_ID)
    assert code == 0, report
    assert report["reason"] == ("ready" if check else "restarted")
    assert world.signals == ([] if check else [(101, signal.SIGTERM)])


def test_given_slot_when_restart_then_no_launch(world, monkeypatch):
    monkeypatch.setattr(main._config, "resolve_project_prefix_by_name", lambda name: "myproject")
    monkeypatch.setattr(main._session_naming, "resolve_host_alias", lambda: "box")
    monkeypatch.setattr(main._session, "_tmux_session_names", lambda: [world.session])
    launch = Mock(side_effect=AssertionError("launch reached"))
    monkeypatch.setattr(main, "_dispatch_session", launch)
    code, report, _ = world.cli("c", "1", "-p", "myproject", "-X")
    assert code == 0 and report["reason"] == "restarted"
    launch.assert_not_called()


def test_given_self_when_restart_then_detached(world, monkeypatch):
    monkeypatch.setenv("TMUX_PANE", "%1")
    spawn = Mock()
    with patch.object(restart.subprocess, "Popen", spawn):
        code, report, _ = world.cli("-X")
    assert code == 0 and report["reason"] == "detached"
    assert spawn.call_args.args[0][-2:] == ["restart-detached", SESSION_ID]
    assert spawn.call_args.kwargs["start_new_session"] is True
    assert world.signals == []


@pytest.mark.parametrize("kind", ["absent", "duplicate", "dead", "birth", "pane", "unknown-program"])
def test_given_bad_target_when_restart_then_no_signal(world, monkeypatch, kind):
    if kind == "absent":
        world.registry.unlink()
    elif kind == "duplicate":
        (world.registry.parent / "duplicate.json").write_text(
            json.dumps({**world.record, "pid": 202}), encoding="utf-8"
        )
    elif kind == "dead":
        world.ended = True
    elif kind == "birth":
        world.record["procStart"] = 999
        world.write()
    elif kind == "pane":
        monkeypatch.setattr(restart, "_run", lambda *a: "wrong\t%1\t103\tgeneration-1\tbash /tmp/other.sh")
    else:
        world.script.write_text("echo unsupported\n", encoding="utf-8")
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] in ("identity", "target", "not-managed", "supervisor-upgrade-required")
    assert world.signals == []


@pytest.mark.skipif(sys.platform == "win32", reason="tmux panes are POSIX-only")
def test_given_the_pane_holds_a_claude_attach_client_when_restart_then_the_refusal_names_it(world):
    """AI-CLI-qe0c: a pane running `claude attach <id>` has no registry record, so the target
    was reported as "0 candidates". The refusal names the attach client and the remedy."""
    world.registry.unlink()
    fake_run = restart.subprocess.run
    with owned_process([sys.executable, "-c", "import time; time.sleep(60)", "claude", "attach", "fe7ea124"]) as client:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not _REAL_PROCESS(client.pid).cmdline():
            time.sleep(0.01)

        def run(argv, **kwargs):
            if argv[:2] == ["tmux", "list-panes"]:
                return SimpleNamespace(returncode=0, stdout=f"{client.pid}\n")
            return fake_run(argv, **kwargs)

        # The world fakes psutil.Process for the session's own processes; the pane's client is real.
        with patch.object(restart.subprocess, "run", run), patch.object(restart.psutil, "Process", _REAL_PROCESS):
            code, report, _ = world.cli("-X", "myproject-box-1")

    assert code == 1 and report["reason"] == "target"
    assert "claude attach fe7ea124" in report["detail"] and "claude stop fe7ea124" in report["detail"]
    assert world.signals == []


@pytest.mark.parametrize("kind", ["busy", "waiting", "settling", "unfinished"])
def test_given_busy_when_restart_then_refusal(world, kind):
    if kind == "busy":
        world.record["status"] = "busy"
    elif kind == "waiting":
        world.record["waitingFor"] = "permission"
    elif kind == "settling":
        world.settings["restart_session"]["settle_seconds"] = 2000
    else:
        world.rows.append({"type": "assistant", "message": {"content": []}})
    world.write()
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "busy" and not world.signals


@pytest.mark.parametrize(
    "kind", ["agent-count", "counter-invalid", "shell", "agent", "descendant", "unreadable", "truncated"]
)
def test_given_work_when_restart_then_refusal(world, monkeypatch, kind):
    if kind == "agent-count":
        world.rows[-1]["pendingBackgroundAgentCount"] = 1
    elif kind == "counter-invalid":
        world.rows[-1]["pendingBackgroundAgentCount"] = None
    elif kind in ("shell", "agent"):
        result = (
            {"backgroundTaskId": "task-1"} if kind == "shell" else {"status": "async_launched", "agentId": "agent-1"}
        )
        world.rows.insert(0, {"type": "user", "toolUseResult": result})
    elif kind == "descendant":
        world.children.append(SimpleNamespace(pid=501, ppid=lambda: 101, cmdline=lambda: ["sleep", "300"]))
    world.write()
    if kind == "unreadable":
        world.transcript.write_text("{", encoding="utf-8")
    if kind == "truncated":
        monkeypatch.setattr(restart, "TAIL_LIMIT", 10)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "background-work" and not world.signals


def test_given_context_when_config_changes_then_fresh_threshold(world):
    world.settings["restart_session"]["context_threshold_pct"] = 1
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "context" and report["detail"] == "pre-step unavailable"
    assert not world.signals
    world.settings["restart_session"]["context_threshold_pct"] = 5
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 0 and report["reason"] == "restarted"


@pytest.mark.parametrize("kind", ["missing", "false", "unreadable", "project-false", "local-true"])
def test_given_activation_when_restart_then_settings_preserved(world, kind):
    path = world.root / ".claude" / "settings.json"
    if kind == "missing":
        path.unlink()
    elif kind == "false":
        path.write_text('{"remoteControlAtStartup":false}', encoding="utf-8")
    elif kind == "unreadable":
        path.write_text("{", encoding="utf-8")
    else:
        project = world.cwd / ".claude"
        project.mkdir()
        (project / "settings.json").write_text('{"remoteControlAtStartup":false}', encoding="utf-8")
        if kind == "local-true":
            (project / "settings.local.json").write_text('{"remoteControlAtStartup":true}', encoding="utf-8")
    before = {p: p.read_bytes() for p in [path, *world.cwd.glob(".claude/*.json")] if p.exists()}
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == (0 if kind == "local-true" else 1)
    assert report["reason"] == ("restarted" if kind == "local-true" else "rc-activation")
    assert before == {p: p.read_bytes() for p in before}
    if kind != "local-true":
        assert not world.signals


def test_given_inaccessible_settings_when_restart_then_no_signal(world, monkeypatch):
    path = world.cwd / ".claude" / "settings.local.json"
    original = Path.stat

    def stat(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError("settings unavailable")
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "rc-activation"
    assert not world.signals and not world.injected


@pytest.mark.parametrize(
    "kind",
    [
        "none",
        "unknown",
        "stale",
        "partial",
        "absent",
        "wrong-id",
        "wrong-title",
        "wrong-bridge",
        "duplicate",
        "verified",
    ],
)
def test_given_peer_when_restart_then_separate_verdict(world, kind):
    if kind == "none":
        world.commands({})
    else:

        def observer(data):
            if kind == "unknown":
                return "invalid"
            if kind == "stale":
                data["observed_at"] = 0
            if kind == "partial":
                data["complete"] = False
            if kind == "absent":
                data.update(status="absent", rows=[])
            if kind == "wrong-id":
                data["rows"][0]["session_id"] = OTHER_ID
            if kind == "wrong-title":
                data["rows"][0]["title"] = "other"
            if kind == "wrong-bridge":
                data["rows"][0]["bridge_session_id"] = "old-bridge"
            if kind == "duplicate":
                data["rows"] *= 2
            return data

        world.observer = observer
    code, report, _ = world.cli("-X", SESSION_ID)
    assert report["local"] == "VERIFIED" and report["mobile"] == "UNVERIFIED", report
    if kind == "verified":
        assert code == 0 and report["reason"] == "restarted"
    elif kind in ("absent", "wrong-id", "wrong-title", "wrong-bridge", "duplicate"):
        assert code == 1 and report["reason"] == "verification-failed"
    else:
        assert code == 1 and report["reason"] == "verification-incomplete"


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_given_platform_when_signal_support_checked_then_windows_unsupported(monkeypatch, platform):
    monkeypatch.setattr(restart.sys, "platform", platform)
    assert restart._signal_supported() is (platform != "win32")


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
@pytest.mark.parametrize("mechanism", ["kill", "exit"])
def test_given_mechanism_when_restart_then_exact_route(world, monkeypatch, platform, mechanism):
    monkeypatch.setattr(restart, "_signal_supported", lambda: platform != "win32")
    world.settings["restart_session"]["mechanism"] = mechanism
    world.commands({"inject": ["inject", "{tmux_pane}", "{command}"], "observer": ["observer"]})
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 0 and report["reason"] == "restarted"
    if platform == "win32" or mechanism == "exit":
        assert world.injected == [["inject", "%1", "/exit"]] and not world.signals
    else:
        assert world.signals == [(101, signal.SIGTERM)] and not world.injected


@pytest.mark.parametrize("signal_supported", [False, True])
def test_given_no_injector_when_exit_then_refusal(world, monkeypatch, signal_supported):
    monkeypatch.setattr(restart, "_signal_supported", lambda: signal_supported)
    world.settings["restart_session"]["mechanism"] = "exit" if signal_supported else "kill"
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "no-mechanism" and not world.signals


@pytest.mark.parametrize("flag", ["-Y", "--restart-check"])
def test_given_check_when_cli_then_no_action(world, flag):
    code, report, _ = world.cli(flag, SESSION_ID)
    assert code == 0 and report["reason"] == "ready"
    assert all(value == "PASS" for value in report["gates"].values())
    assert set(report["triggers"]) == {"update", "unreachable"}
    assert not world.signals and not world.injected
    assert not restart.state_paths(world.session)[2].exists()


def test_given_locked_session_when_checked_then_all_gate_verdicts_reported(world):
    with restart.session_lock(world.session):
        code, report, _ = world.cli("-Y", SESSION_ID)
    assert code == 1 and report["reason"] == "refused"
    assert report["gates"]["lock"] == "locked"
    assert report["gates"]["cooldown"] == "UNKNOWN"
    assert report["gates"]["failure-fence"] == "UNKNOWN"
    assert not world.signals and not world.injected


def test_given_cooldown_when_second_restart_then_refusal(world):
    world.settings["restart_session"]["cooldown_seconds"] = 3600
    outcome = world.cli("-X", SESSION_ID)
    assert outcome[0] == 0, outcome
    # The old record is left present to verify cooldown precedes every act gate.
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "cooldown"  # stale dead records do not create ambiguity
    world.registry.unlink()
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "cooldown"
    assert len(world.signals) == 1


def test_given_failure_when_cooldown_expires_then_auto_suppressed(world, monkeypatch):
    world.commands({})
    assert world.cli("-X", SESSION_ID)[1]["reason"] == "verification-incomplete"
    world.registry.unlink()
    world.connection.__enter__.return_value.connect.side_effect = ConnectionRefusedError()
    monkeypatch.setattr(config, "load_config", lambda **_kwargs: world.settings)
    report = restart.restart(SESSION_ID, automatic=True)
    assert report["reason"] == "previous-verification-failed" and len(world.signals) == 1


@pytest.fixture
def failed_restart(world):
    settings = config.read_restart_config(world.settings)
    with restart.session_lock(world.session):
        request = restart.prepare_request(world.record, restart.managed_gate(world.record, settings), settings)
        request["status"] = "failed"
        _, result_path, request_path = restart.state_paths(world.session)
        restart._atomic_json(request_path, request)
        restart._atomic_json(
            result_path,
            {
                "session_id": SESSION_ID,
                "last_restart": time.time() - 100,
                "failed": True,
                "reason": "verification-failed",
            },
        )
    world.replace()
    world.request_path = request_path
    world.result_path = result_path
    world.replacement_path = world.registry.parent / "201.json"
    world.request = request
    return world


@pytest.mark.parametrize("renamed", [False, True])
def test_given_failed_request_when_verified_replacement_checked_then_reconciled_and_fence_passes(
    failed_restart, renamed
):
    world = failed_restart
    if renamed:
        world.request["title"] = "previous-title"
        restart._atomic_json(world.request_path, world.request)
    before = restart._object(world.result_path)
    started = time.time()
    code, report, _ = world.cli("-Y", SESSION_ID)
    assert code == 0, report
    assert report["gates"]["failure-fence"] == "PASS"
    request = restart._object(world.request_path)
    assert request["status"] == "complete"
    assert request["request_id"] == world.request["request_id"]
    assert request["reconciliation"]["replacement_pid"] == 201
    assert started <= request["reconciliation"]["time"] <= time.time()
    assert set(request["reconciliation"]["evidence"]) == {
        "pane",
        "session-id",
        "replacement-pid",
        "process-start",
        "supervisor-identity",
        "supervisor-ancestry",
        "local-verdict",
    }
    result = restart._object(world.result_path)
    assert result["failed"] is False
    assert "reconciled" in result["reason"]
    assert result["last_restart"] == before["last_restart"]
    saved = world.request_path.read_bytes(), world.result_path.read_bytes()
    code, report, _ = world.cli("-Y", SESSION_ID)
    assert code == 0 and report["gates"]["failure-fence"] == "PASS"
    assert (world.request_path.read_bytes(), world.result_path.read_bytes()) == saved
    assert not world.signals and not world.injected


@pytest.mark.parametrize("flag", ["-Y", "-X"])
def test_given_failed_request_when_reconciled_during_cooldown_then_original_cooldown_remains(failed_restart, flag):
    world = failed_restart
    world.settings["restart_session"]["cooldown_seconds"] = 3600
    before = restart._object(world.result_path)["last_restart"]
    code, report, _ = world.cli(flag, SESSION_ID)
    assert code == 1
    assert (report["gates"]["cooldown"] if flag == "-Y" else report["reason"]) == "cooldown"
    assert restart._object(world.request_path)["status"] == "complete"
    assert restart._object(world.result_path)["last_restart"] == before
    assert restart._object(world.result_path)["failed"] is False
    assert not world.signals and not world.injected


def test_given_failed_request_when_manual_retry_prepares_then_reconciliation_precedes_it(failed_restart, monkeypatch):
    world = failed_restart
    seen = []

    def prepare(*args):
        seen.append(restart._object(world.request_path))
        raise restart.Refusal("busy")

    monkeypatch.setattr(restart, "prepare_request", prepare)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "busy"
    assert seen[0]["status"] == "complete"
    assert seen[0]["reconciliation"]["replacement_pid"] == 201
    assert restart._object(world.result_path)["failed"] is False
    assert not world.signals and not world.injected


@pytest.mark.parametrize("flag", ["-Y", "-X"])
@pytest.mark.parametrize("target", ["request_path", "result_path"])
@pytest.mark.parametrize("after_replace", [False, True])
def test_given_reconciliation_write_error_when_attempted_then_fence_and_files_preserved(
    failed_restart, monkeypatch, flag, target, after_replace
):
    world = failed_restart
    before = world.request_path.read_bytes(), world.result_path.read_bytes()
    atomic_json = restart._atomic_json
    failed = False

    def write(path, value):
        nonlocal failed
        with pytest.raises(restart.Refusal, match="locked"):
            with restart.session_lock(world.session):
                pytest.fail("reconciliation must hold the session lock")
        if path == getattr(world, target) and not failed:
            failed = True
            if after_replace:
                atomic_json(path, value)
            raise OSError("fixture write failed")
        atomic_json(path, value)

    monkeypatch.setattr(restart, "_atomic_json", write)
    code, _, _ = world.cli(flag, SESSION_ID)
    assert code == 1
    assert failed
    assert (world.request_path.read_bytes(), world.result_path.read_bytes()) == before
    assert not world.signals and not world.injected


def test_given_verified_replacement_when_automatic_retry_then_failed_suppression_is_cleared(
    failed_restart, monkeypatch
):
    world = failed_restart
    world.commands({})
    monkeypatch.setattr(config, "load_config", lambda **_kwargs: world.settings)
    prepare = Mock(side_effect=restart.Refusal("busy"))
    monkeypatch.setattr(restart, "prepare_request", prepare)
    report = restart.restart(SESSION_ID, automatic=True)
    assert report["reason"] == "busy"
    prepare.assert_called_once()
    assert restart._object(world.result_path)["failed"] is False
    assert restart._object(world.request_path)["status"] == "complete"
    assert not world.signals and not world.injected


@pytest.mark.parametrize("flag", ["-Y", "-X"])
@pytest.mark.parametrize(
    "fault",
    [
        "no-replacement",
        "supervisor",
        "ancestry",
        "supervisor-birth",
        "session-id",
        "pane",
        "local-failed",
        "local-unknown",
        "old-pid",
        "dead",
        "reused",
        "request-unreadable",
        "request-inaccessible",
        "result-unreadable",
        "exception",
    ],
)
def test_given_failed_request_when_replacement_unproven_then_fence_and_files_preserved(
    failed_restart, monkeypatch, flag, fault
):
    world = failed_restart
    replacement = restart._object(world.replacement_path)
    if fault == "no-replacement":
        # Inspect the fixture record before removing only this simulated replacement.
        assert replacement["pid"] == 201
        world.replacement_path.unlink()
        world.ended = False
    elif fault == "supervisor":
        world.request["supervisor_pid"] = 999
        world.request["supervisor_identity"] = {"backend": "fake", "marker": 999}
    elif fault == "supervisor-birth":
        world.identities[103] = ProcessIdentity("fake", 999)
    elif fault == "ancestry":
        monkeypatch.setattr(restart.psutil.Process, "parents", lambda process: [])
    elif fault == "session-id":
        world.request["session_id"] = OTHER_ID
    elif fault == "pane":
        world.request["tmux"] = f"{world.session}:@1.%2"
    elif fault in ("local-failed", "local-unknown"):
        if fault == "local-failed":
            replacement["bridgeSessionId"] = ""
        else:
            world.connection.__enter__.return_value.connect.side_effect = FileNotFoundError()
    elif fault == "old-pid":
        world.request["old_agent_pid"] = 201
    elif fault in ("dead", "reused"):
        if fault == "dead":
            world.probe.has_ended = lambda pid: pid == 201
            world.ended = False
        else:
            replacement["procStart"] = 999
    elif fault == "exception":
        monkeypatch.setattr(restart, "_same", Mock(side_effect=RuntimeError("fixture probe failed")))
    restart._atomic_json(world.request_path, world.request)
    if fault != "no-replacement":
        restart._atomic_json(world.replacement_path, replacement)
    if fault == "request-unreadable":
        world.request_path.write_text("{", encoding="utf-8")
    elif fault == "result-unreadable":
        world.result_path.write_text("{", encoding="utf-8")
    before = world.request_path.read_bytes(), world.result_path.read_bytes()
    if fault == "request-inaccessible":
        original_stat = Path.stat

        def stat(path, *args, **kwargs):
            if path == world.request_path:
                raise PermissionError("fixture request inaccessible")
            return original_stat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", stat)
    if flag == "-X":
        # A manual retry keeps its own same-conversation rule; it never counts as reconciliation.
        with contextlib.suppress(Exception):
            world.cli(flag, world.session)
        request = {}
        with contextlib.suppress(Exception):
            request = restart._object(world.request_path)
        assert "reconciliation" not in request
        return
    code, report, _ = world.cli(flag, world.session)
    assert code == 1, report
    assert report["gates"]["failure-fence"] != "PASS"
    assert (world.request_path.read_bytes(), world.result_path.read_bytes()) == before
    assert not world.signals and not world.injected


@pytest.mark.parametrize("phase", ["pre-step", "relaunch"])
def test_given_contenders_when_locked_then_one_action(world, monkeypatch, phase):
    entered = threading.Event()
    proceed = threading.Event()
    if phase == "pre-step":
        world.settings["restart_session"]["context_threshold_pct"] = 1

        def pre_step(*args):
            entered.set()
            assert proceed.wait(5)
            return "pre-step unavailable"

        monkeypatch.setattr(restart, "context_pre_step", pre_step)
    else:
        original = restart._verify_replacement

        def verify(*args):
            entered.set()
            assert proceed.wait(5)
            return original(*args)

        monkeypatch.setattr(restart, "_verify_replacement", verify)
    monkeypatch.setattr(config, "load_config", lambda **_kwargs: world.settings)
    reports = []
    actor = threading.Thread(target=lambda: reports.append(restart.restart(SESSION_ID)))
    actor.start()
    try:
        assert entered.wait(5)
        # Force the contender to resolve the same original candidate, not pick a replacement.
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(restart, "resolve_target", lambda target: (world.registry, world.record))
            assert restart.restart(SESSION_ID)["reason"] == "locked"
    finally:
        proceed.set()
        actor.join(5)
    assert not actor.is_alive()
    assert len(world.signals) == (0 if phase == "pre-step" else 1)
    if phase == "relaunch":
        assert restart._object(restart.state_paths(world.session)[1])["last_restart"] > 0


def test_given_revalidation_when_busy_then_signal_unreachable(world, monkeypatch):
    original = restart.prepare_request

    def prepare(*args):
        request = original(*args)
        world.record["status"] = "busy"
        world.write()
        return request

    monkeypatch.setattr(restart, "prepare_request", prepare)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "busy" and not world.signals
    assert restart._object(restart.state_paths(world.session)[2])["status"] == "failed"


@pytest.mark.parametrize("signal_supported", [False, True])
@pytest.mark.parametrize("birth_type", [int, str])
def test_given_ignored_term_when_grace_expires_then_exact_kill(world, monkeypatch, signal_supported, birth_type):
    world.record["procStart"] = birth_type(101)
    world.write()
    monkeypatch.setattr(restart, "_signal_supported", lambda: signal_supported)
    # The signal and process are simulated; Windows must use injection instead.
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)
    world.commands({"inject": ["inject", "{tmux_pane}", "{command}"], "observer": ["observer"]})

    def kill(pid, sig):
        world.signals.append((pid, sig))
        if sig == signal.SIGKILL:
            world.replace()

    monkeypatch.setattr(restart.os, "kill", kill)
    assert world.cli("-X", SESSION_ID)[0] == 0
    if signal_supported:
        assert world.signals == [(101, signal.SIGTERM), (101, signal.SIGKILL)]
        assert not world.injected
    else:
        assert not world.signals
        assert world.injected == [["inject", "%1", "/exit"]]


def test_given_reused_pid_when_escalating_then_no_kill(world, monkeypatch):
    def kill(pid, sig):
        world.signals.append((pid, sig))
        world.identities[101] = ProcessIdentity("fake", 999)

    monkeypatch.setattr(restart.os, "kill", kill)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] in ("identity", "restart-fenced")
    assert world.signals == [(101, signal.SIGTERM)]


@pytest.mark.parametrize("unix_family_available", [False, True])
def test_given_fixture_when_passively_probed_then_local_verified(request, monkeypatch, unix_family_available):
    if not unix_family_available:
        monkeypatch.delattr(restart.socket, "AF_UNIX", raising=False)
    world = request.getfixturevalue("world")
    world.replace()
    row = restart._object(world.registry.parent / "201.json")
    assert restart.local_verdict(row) == "VERIFIED", (row, restart._run(["claude", "agents", "--json"]))


def test_given_unavailable_socket_family_when_passively_probed_then_local_unknown(world, monkeypatch):
    world.replace()
    row = restart._object(world.registry.parent / "201.json")
    monkeypatch.delattr(restart.socket, "AF_UNIX")
    assert restart.local_verdict(row) == "UNKNOWN"
    assert not world.signals and not world.injected


@pytest.fixture
def replacement_listing(world, monkeypatch):
    settings = config.read_restart_config(world.settings)
    restart.prepare_request(world.record, restart.managed_gate(world.record, settings), settings)
    world.replace()
    replacement = restart._object(world.registry.parent / "201.json")
    listing = SimpleNamespace(rows=[replacement.copy()], flags=None, delay=0, elapsed=0)
    monkeypatch.setattr(restart.time, "monotonic", lambda: listing.elapsed)
    monkeypatch.setattr(restart.time, "sleep", lambda seconds: setattr(listing, "elapsed", listing.elapsed + seconds))

    def run(argv, **kwargs):
        if argv == ["claude", "agents", "--json"]:
            listing.elapsed += listing.delay
            data = listing.rows if listing.flags is None else {"sessions": listing.rows, **listing.flags}
            return SimpleNamespace(returncode=0, stdout=json.dumps(data))
        return world.subprocess_run(argv, **kwargs)

    monkeypatch.setattr(restart.subprocess, "run", run)
    return replacement, listing, settings


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("unrelated", [{"pid": None}, {"sessionId": ""}, {"name": ""}])
def test_given_unrelated_malformed_agent_when_local_checked_then_replacement_verified(
    replacement_listing, wrapped, unrelated
):
    replacement, listing, _ = replacement_listing
    listing.rows.insert(0, {"sessionId": OTHER_ID, "name": "other-session", "pid": 301, **unrelated})
    if wrapped:
        listing.flags = {"complete": True}
    assert restart.local_verdict(replacement) == "VERIFIED"
    assert restart.trigger_verdicts(replacement, {"observer": ["observer"]})["unreachable"] == "NO"


@pytest.mark.parametrize("peer", [False, True])
@pytest.mark.parametrize("delay", [0, 15])
def test_given_unrelated_pidless_agent_when_replacement_verified_then_restart_succeeds(
    world, replacement_listing, peer, delay
):
    _, listing, settings = replacement_listing
    listing.rows.append({"sessionId": OTHER_ID, "name": "other-session", "pid": None})
    listing.delay = delay
    settings["relaunch_timeout_seconds"] = 180
    report = restart._verify_replacement(world.record, settings, {"observer": ["observer"]} if peer else {})
    assert report["reason"] == ("restarted" if peer else "verification-incomplete"), report
    assert report["local"] == "VERIFIED"
    assert listing.elapsed == 2 * delay
    assert not world.signals and not world.injected


@pytest.mark.parametrize(
    ("field", "value"),
    [("pid", None), ("pid", "201"), ("pid", True), ("pid", 1), ("sessionId", ""), ("name", "")],
)
@pytest.mark.parametrize("missing", [False, True])
def test_given_malformed_target_agent_when_replacement_verified_then_refusal_names_field(
    world, replacement_listing, field, value, missing
):
    replacement, listing, settings = replacement_listing
    if missing:
        listing.rows[0].pop(field)
    else:
        listing.rows[0][field] = value
    assert restart.local_verdict(replacement) == "UNKNOWN"
    report = restart._verify_replacement(world.record, settings, {})
    assert report["reason"] == "verification-failed" and report["local"] == "FAILED"
    assert "matching local agent row" in report["detail"] and field in report["detail"], report
    assert not world.signals and not world.injected


@pytest.mark.parametrize(
    ("fault", "expected"),
    [
        ("absent", "FAILED"),
        ("duplicate", "FAILED"),
        ("pid", "FAILED"),
        ("sessionId", "FAILED"),
        ("name", "FAILED"),
        ("complete", "UNKNOWN"),
        ("listTruncated", "UNKNOWN"),
        ("localListFailed", "UNKNOWN"),
        ("bridgeWalkFailed", "UNKNOWN"),
        ("truncated", "UNKNOWN"),
    ],
)
def test_given_agent_listing_fault_when_local_checked_then_existing_verdict_preserved(
    replacement_listing, fault, expected
):
    replacement, listing, _ = replacement_listing
    if fault == "absent":
        listing.rows.clear()
    elif fault == "duplicate":
        listing.rows *= 2
    elif fault in ("pid", "sessionId", "name"):
        listing.rows[0][fault] = {"pid": 301, "sessionId": OTHER_ID, "name": "other-session"}[fault]
    else:
        listing.flags = {"complete": fault != "complete", **({fault: True} if fault != "complete" else {})}
    assert restart.local_verdict(replacement) == expected


@pytest.mark.parametrize(
    "fault", ["none", "missing-transcript", "expired", "generation", "old-child", "script", "malformed", "crash"]
)
def test_given_pin_when_validated_then_fence_preserved(world, monkeypatch, fault):
    settings = config.read_restart_config(world.settings)
    with restart.session_lock(world.session):
        managed = restart.managed_gate(world.record, settings)
        request = restart.prepare_request(world.record, managed, settings)
        request["status"] = "acting"
        _, _, path = restart.state_paths(world.session)
        restart._atomic_json(path, request)
        if fault == "old-child":
            world.identities[102] = ProcessIdentity("fake", 999)
            with pytest.raises(restart.Refusal, match="restart-fenced"):
                restart._validate_request(request, world.session, child=False)
            assert not world.signals
            return
        world.probe.has_ended = lambda pid: pid in (101, 102)
        monkeypatch.setattr(restart.os, "getppid", lambda: 104)
        monkeypatch.chdir(world.cwd)
        if fault == "missing-transcript":
            world.transcript.unlink()
        elif fault == "expired":
            request["expires_at"] = 0
            restart._atomic_json(path, request)
        elif fault == "generation":
            world.generation = "changed"
        elif fault == "script":
            world.script.write_text("changed", encoding="utf-8")
        elif fault == "malformed":
            path.write_text("{", encoding="utf-8")
        elif fault == "crash":
            world.identities[request["owner_pid"]] = ProcessIdentity("fake", 999)
        code, stdout, _ = run_cli(["ai", "internal", "restart-pin", world.session, world.record["name"]])
        assert code == (0 if fault == "none" else 1)
        assert stdout.strip() == (str(world.transcript) if fault == "none" else "")
        assert path.exists()  # never consumed, even by a successful resolver query
        if fault == "none":
            assert restart.child_pin(world.session, world.record["name"])[0] == 1


def test_given_no_request_when_resolving_then_ordinary_launch(world):
    assert restart.child_pin(world.session, world.record["name"]) == (3, "")


def test_given_wrong_replacement_when_verified_then_resume_mismatch(world, monkeypatch):
    def replace(pid, sig):
        world.signals.append((pid, sig))
        world.ended = True
        (world.registry.parent / "201.json").write_text(
            json.dumps({**world.record, "pid": 201, "procStart": 201, "sessionId": OTHER_ID}), encoding="utf-8"
        )

    monkeypatch.setattr(restart.os, "kill", replace)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "resume-mismatch"
    assert restart._object(restart.state_paths(world.session)[2])["status"] == "failed"


def test_given_crash_when_verifying_then_durable_failure(world, monkeypatch):
    monkeypatch.setattr(restart, "_verify_replacement", Mock(side_effect=KeyboardInterrupt))
    code, _, _ = run_cli(["ai", "-X", SESSION_ID], config=world.settings)
    assert code == 1
    _, result, request = restart.state_paths(world.session)
    assert restart._object(result)["failed"] is True
    assert restart._object(result)["reason"] == "interrupted"
    assert restart._object(request)["status"] == "acting"


@pytest.mark.parametrize("fault", ["status", "background", "context", "activation", "generation", "old-child"])
def test_given_last_check_when_gate_changes_then_no_action(world, monkeypatch, fault):
    original = restart.prepare_request

    def prepare(*args):
        request = original(*args)
        if fault == "status":
            world.record["status"] = "busy"
        elif fault == "background":
            world.rows[-1]["pendingBackgroundAgentCount"] = 1
        elif fault == "context":
            world.rows[0]["message"]["usage"]["input_tokens"] = 100_000
        elif fault == "activation":
            (world.root / ".claude" / "settings.json").write_text('{"remoteControlAtStartup":false}', encoding="utf-8")
        elif fault == "generation":
            world.generation = "changed"
        else:
            world.identities[102] = ProcessIdentity("fake", 999)
        world.write()
        return request

    monkeypatch.setattr(restart, "prepare_request", prepare)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] in ("busy", "background-work", "context", "rc-activation", "identity")
    assert not world.signals


@pytest.mark.parametrize("phase", ["term", "exit", "fallback", "kill"])
def test_given_ac_f1_busy_during_helper_probe_when_acting_then_no_further_action(world, monkeypatch, phase):
    world.commands({"inject": ["inject"], "observer": ["observer"]})
    if phase == "exit":
        world.settings["restart_session"]["mechanism"] = "exit"
    attempts = []

    def kill(pid, sig):
        attempts.append((pid, sig))
        if phase == "fallback":
            raise PermissionError("signal access denied")

    monkeypatch.setattr(restart.os, "kill", kill)

    def run(argv, **kwargs):
        if _is_restart_probe(argv, world.pane_ai):
            request_path = restart.state_paths(world.session)[2]
            acting = request_path.exists() and restart._object(request_path).get("status") == "acting"
            if acting and (phase in ("term", "exit") or attempts):
                world.record["status"] = "busy"
                world.registry.write_text(json.dumps(world.record), encoding="utf-8")
        return world.subprocess_run(argv, **kwargs)

    with patch.object(restart.subprocess, "run", run):
        code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "busy"
    assert attempts == ([(101, signal.SIGTERM)] if phase in ("fallback", "kill") else [])
    assert not world.injected


@pytest.mark.parametrize("json_output", [True, False])
@pytest.mark.parametrize("flag", ["-X", "-Y"])
@pytest.mark.parametrize("exit_code", [0, 1])
def test_given_remote_slot_when_restart_then_remote_resolution_only(world, monkeypatch, json_output, flag, exit_code):
    calls = []
    monkeypatch.setattr(main._config, "get_remote_machine", lambda *a: {"host": "example.com", "user": "user"})
    monkeypatch.setattr(main, "_resolve_remote_shell", lambda ssh: "bash")

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=exit_code, stdout=json.dumps({"reason": "ready"}))

    args = ["ai", "c", "1", "-R", "-p", "myproject", flag]
    if json_output:
        args.append("-j")
    with patch.object(main.subprocess, "run", run):
        code, out, _ = run_cli(args, config=world.settings)
    assert code == exit_code
    if json_output:
        assert json.loads(out)["reason"] == "ready"
    else:
        assert "reason=ready" in out and not out.lstrip().startswith("{")
    assert calls[0][-1] == f"bash -l -c 'ai c 1 -p myproject --is-remote {flag} -j'"
    assert not world.signals


@pytest.mark.parametrize("changed_birth", [False, True])
def test_given_mac_refresh_when_revalidated_then_original_program_proof_is_fenced(request, monkeypatch, changed_birth):
    # Model native Windows discovery before the fixture switches to macOS.
    monkeypatch.setattr(
        main._session_script, "resolve_session_shell", lambda: "bash" if sys.platform == "darwin" else "bash.exe"
    )
    world = request.getfixturevalue("world")
    # Executable discovery stays native while this test simulates macOS provenance.
    monkeypatch.setattr(restart.shutil, "which", lambda name, *, path: str(world.pane_ai))
    from ai_cli import session_registry

    # The registry resolves its state directory per platform; keep it where the fixture registered.
    monkeypatch.setattr(session_registry, "get_xdg_state_home", config.get_xdg_state_home)
    monkeypatch.setattr(restart.sys, "platform", "darwin")
    world.script.write_text(world.script.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    reads = []

    def running_source(pid, path):
        reads.append(pid)
        if len(reads) > 2:
            raise restart.Refusal("supervisor-upgrade-required", "old inode unavailable after refresh")
        return path.read_text(encoding="utf-8")

    monkeypatch.setattr(restart, "_script_source", running_source)
    original = main._write_launch_script_if_changed

    def refresh(path, text):
        original(path, text)
        if changed_birth:
            world.identities[102] = ProcessIdentity("fake", 999)

    monkeypatch.setattr(main, "_write_launch_script_if_changed", refresh)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert reads == [103, 102]
    assert code == (1 if changed_birth else 0), report
    assert report["reason"] == ("identity" if changed_birth else "restarted"), report
    assert bool(world.signals) is not changed_birth


@pytest.mark.parametrize("kind", ["unknown", "override", "auto", "manual", "old-auto", "null", "cached", "extended"])
def test_given_context_evidence_when_checked_then_conservative_window(world, kind):
    if kind in ("unknown", "override"):
        world.rows[0]["message"]["model"] = "unknown-model"
        if kind == "override":
            world.settings["restart_session"]["context_window_tokens"] = 200_000
    elif kind in ("auto", "manual", "old-auto"):
        world.rows.insert(
            0,
            {
                "type": "system",
                "subtype": "compact_boundary",
                "compactMetadata": {"trigger": "manual" if kind == "manual" else "auto"},
            },
        )
        world.rows[1]["message"]["usage"] = None
        if kind == "old-auto":
            world.settings["restart_session"]["compaction_fresh_turns"] = 0
    elif kind == "null":
        world.rows[0]["message"]["usage"] = None
    elif kind == "cached":
        world.rows[0]["message"]["usage"]["cache_read_input_tokens"] = 100_000
    else:
        world.rows[0]["message"]["model"] += "[1m]"
        world.rows[0]["message"]["usage"]["input_tokens"] = 150_000
    world.write()
    code, report, _ = world.cli("-X", SESSION_ID)
    expected = kind in ("override", "auto", "extended")
    assert code == (0 if expected else 1)
    assert report["reason"] == ("restarted" if expected else "context")
    if not expected:
        assert not world.signals and not world.injected


@pytest.fixture
def context_row():
    return {
        "type": "assistant",
        "message": {
            "model": "claude-opus-5-5",
            "usage": {"input_tokens": 17_600, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 100_000},
        },
    }


@pytest.fixture
def context_settings():
    return config.read_restart_config({})


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-opus-5-5[1m]"])
@pytest.mark.parametrize(("requested", "allowed"), [("claude-opus-5-5[1m]", True), ("claude-opus-5-5", False)])
def test_given_matching_requested_model_when_context_checked_then_requested_window_wins(
    context_row, context_settings, model, requested, allowed
):
    context_row["message"]["model"] = model
    context_row["requestedModel"] = requested
    assert restart.context_gate([context_row], context_settings) is allowed


@pytest.mark.parametrize("extended", [False, True])
@pytest.mark.parametrize(
    "requested",
    [
        None,
        False,
        1,
        [],
        {},
        "",
        " ",
        "claude-opus-5-5[1m]\n",
        " claude-opus-5-5[1m]",
        "claude-opus-5-5[1M]",
        "claude-opus-5-5[2m]",
        "claude-opus-5-5[1m][1m]",
        "claude-opus-5-5[1m]/extra",
        "claude-sonnet-5-5[1m]",
        "claude-sonnet-5-5",
    ],
)
def test_given_untrusted_requested_model_when_context_checked_then_message_window_applies(
    context_row, context_settings, requested, extended
):
    context_row["requestedModel"] = requested
    if extended:
        context_row["message"]["model"] += "[1m]"
    assert restart.context_gate([context_row], context_settings) is extended


@pytest.mark.parametrize(
    ("requested", "window", "allowed"),
    [("claude-opus-5-5[1m]", 200_000, False), ("claude-opus-5-5", 1_000_000, True)],
)
def test_given_window_override_when_requested_model_disagrees_then_override_wins(
    context_row, context_settings, requested, window, allowed
):
    context_row["requestedModel"] = requested
    context_settings["context_window_tokens"] = window
    assert restart.context_gate([context_row], context_settings) is allowed


@pytest.mark.parametrize(
    ("model", "requested", "allowed"),
    [
        ("claude-opus-5-5", "us.anthropic.claude-opus-5-5[1m]", True),
        ("us.anthropic.claude-opus-5-5-v1:0", "claude-opus-5-5[1m]", True),
        ("us.anthropic.claude-opus-5-5", "us.anthropic.claude-opus-5-5-v12:34[1m]", True),
        ("us.anthropic.claude-opus-5-5-v1:0[1m]", "claude-opus-5-5", False),
        ("us.anthropic.eu.anthropic.claude-opus-5-5-v1:0", "claude-opus-5-5[1m]", True),
        ("us.anthropic.claude-opus-5-5-v1:0[1m]", None, True),
        ("us.anthropic.claude-opus-5-5-v1:0", None, False),
    ],
)
def test_given_provider_model_ids_when_context_checked_then_stripped_bases_match(
    context_row, context_settings, model, requested, allowed
):
    context_row["message"]["model"] = model
    context_row["requestedModel"] = requested
    assert restart.context_gate([context_row], context_settings) is allowed


@pytest.mark.parametrize("model", ["claude-haiku-5-5", "us.anthropic.claude-haiku-5-5-v1:0"])
@pytest.mark.parametrize(("tokens", "allowed"), [(117_600, False), (10_000, True)])
def test_given_haiku_requested_extension_when_context_checked_then_base_window_applies(
    context_row, context_settings, model, tokens, allowed
):
    context_row["message"]["model"] = model
    context_row["requestedModel"] = "claude-haiku-5-5[1m]"
    context_row["message"]["usage"] = {
        "input_tokens": tokens,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    assert restart.context_gate([context_row], context_settings) is allowed


def test_given_unknown_requested_model_when_context_checked_then_unknown_window_refuses(context_row, context_settings):
    context_row["message"]["model"] = "unknown-model"
    context_row["requestedModel"] = "unknown-model[1m]"
    context_row["message"]["usage"]["cache_read_input_tokens"] = 0
    assert not restart.context_gate([context_row], context_settings)


def test_given_provider_base_model_when_usage_is_low_then_normalized_table_window_passes(context_row, context_settings):
    context_row["message"]["model"] = "us.anthropic.claude-opus-5-5-v1:0"
    context_row["message"]["usage"]["cache_read_input_tokens"] = 0
    assert restart.context_gate([context_row], context_settings)


def test_given_model_switch_when_latest_main_usage_selected_then_older_and_sidechain_windows_are_ignored(
    context_row, context_settings, tmp_path
):
    older = {**context_row, "message": {**context_row["message"]}, "requestedModel": "claude-opus-5-5[1m]"}
    context_row["requestedModel"] = "claude-sonnet-5-5"
    context_row["message"]["model"] = "claude-sonnet-5-5"
    sidechain = {**context_row, "requestedModel": "claude-sonnet-5-5[1m]", "isSidechain": True}
    rows = [
        older,
        context_row,
        {"type": "assistant", "requestedModel": "claude-sonnet-5-5[1m]", "message": {"usage": None}},
        sidechain,
    ]
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    assert not restart.context_gate(restart.transcript_tail(transcript), context_settings)


def test_given_context_docs_when_window_precedence_described_then_contract_is_explicit():
    root = Path(__file__).resolve().parents[1]
    plan = next((root / "docs" / "plans").glob("*restart-idle-sessions-plan.md"))
    for path in (root / "README.md", plan):
        text = " ".join(path.read_text(encoding="utf-8").split())
        for required in (
            "`context_window_tokens` wins",
            "matching `requestedModel`",
            "even when `message.model` has `[1m]`",
            "known non-haiku models",
            "1,000,000",
            "200,000",
            "malformed",
            "mismatched request",
            "last `anthropic.`",
            "`-v<digits>:<digits>`",
        ):
            assert required in text, (path.name, required)


@pytest.mark.parametrize("key", ["input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"])
@pytest.mark.parametrize("value", [None, True, "0", -1, float("nan"), float("inf")])
def test_given_invalid_token_count_when_restart_then_context_refuses(world, key, value):
    world.rows[0]["message"]["usage"][key] = value
    world.write()
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "context"
    assert not world.signals


@pytest.mark.parametrize("key", ["input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"])
def test_given_missing_token_count_when_restart_then_context_refuses(world, key):
    del world.rows[0]["message"]["usage"][key]
    world.write()
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "context"
    assert not world.signals


def test_given_text_check_when_cli_then_gates_printed(world):
    code, out, err = run_cli(["ai", "-Y", SESSION_ID], config=world.settings)
    assert code == 0
    assert "gates=" in out and "triggers=" in out and "mobile=UNVERIFIED" in out
    assert not world.signals


@pytest.mark.parametrize("fault", ["timeout", "exit", "malformed", "future", "truncated"])
def test_given_bad_observer_when_restart_then_incomplete(world, monkeypatch, fault):
    original = restart._run

    def run(argv, *args):
        if argv[0] != "observer":
            return original(argv, *args)
        if fault in ("timeout", "exit"):
            raise restart.Refusal("probe-unavailable")
        if fault == "malformed":
            return "{"
        return json.dumps(
            {
                "status": "seen",
                "complete": True,
                "observed_at": time.time() + (1000 if fault == "future" else 0),
                "truncated": fault == "truncated",
                "rows": [],
            }
        )

    monkeypatch.setattr(restart, "_run", run)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "verification-incomplete" and report["local"] == "VERIFIED"


def test_given_signal_denied_when_injector_exists_then_fallback(world, monkeypatch):
    world.commands({"inject": ["inject", "{command}"], "observer": ["observer"]})
    monkeypatch.setattr(restart.os, "kill", Mock(side_effect=PermissionError))
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 0 and report["reason"] == "restarted"
    assert world.injected == [["inject", "/exit"]]


def test_given_detached_helper_when_idle_arrives_then_all_gates_applied(world, monkeypatch):
    world.record["status"] = "busy"
    world.settings["restart_session"]["compaction_wait_seconds"] = 5
    world.write()

    def idle_after_return(seconds):
        world.record["status"] = "idle"
        world.write()

    monkeypatch.setattr(restart.time, "sleep", idle_after_return)
    code, _, _ = run_cli(["ai", "internal", "restart-detached", SESSION_ID], config=world.settings)
    assert code == 0 and world.signals == [(101, signal.SIGTERM)]


def test_given_detached_busy_when_wait_expires_then_no_signal(world):
    world.record["status"] = "busy"
    world.settings["restart_session"]["compaction_wait_seconds"] = 0
    world.write()
    code, _, _ = run_cli(["ai", "internal", "restart-detached", SESSION_ID], config=world.settings)
    assert code == 1 and not world.signals


def test_given_completed_but_malformed_pin_when_read_then_no_fallback(world):
    path = restart.state_paths(world.session)[2]
    restart._atomic_json(path, {"status": "complete"})
    assert restart.child_pin(world.session, world.record["name"]) == (1, "")
    assert path.exists()


def test_given_observer_race_when_generation_changes_then_verification_fails(world):
    def observe(data):
        world.generation = "other-generation"
        return data

    world.observer = observe
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "verification-failed"
    assert restart._object(restart.state_paths(world.session)[2])["status"] == "failed"


@pytest.fixture
def pre_step_world(world, monkeypatch):
    clock = [0.0]
    epoch = time.time()
    monkeypatch.setattr(restart.time, "time", lambda: epoch + clock[0])
    monkeypatch.setattr(restart.time, "monotonic", lambda: clock[0])
    world.settings["restart_session"]["compaction_wait_seconds"] = 3
    world.rows[0]["message"]["usage"]["input_tokens"] = 100_000
    world.commands({"inject": ["inject", "{tmux_pane}", "{command}"], "observer": ["observer"]})
    world.outcome = {"state": "STARTED", "submitted": True}
    world.inject_code = 0
    world.ticks = clock

    def compact():
        world.rows.extend(
            [
                {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"trigger": "auto"}},
                {"type": "user", "isCompactSummary": True, "message": {"content": "Session summary"}},
            ]
        )
        # One millisecond back: the gate reads this as time.time() - ms / 1000, and writing "now" exactly
        # lets float rounding return a tiny negative quiet time (about 1 in 90 epochs), which reads as busy.
        world.record["statusUpdatedAt"] = (time.time() - 0.001) * 1000
        world.write()

    world.compact = compact
    world.on_tick = lambda: compact() if len(world.rows) == 2 else None

    def sleep(seconds):
        clock[0] += seconds
        world.on_tick()

    monkeypatch.setattr(restart.time, "sleep", sleep)

    def run(argv, **kwargs):
        if argv == ["inject", "%1", "/save-state"]:
            world.injected.append(argv)
            assert kwargs["timeout"] > 0
            return SimpleNamespace(returncode=world.inject_code, stdout=json.dumps(world.outcome))
        return world.subprocess_run(argv, **kwargs)

    world.write()
    with patch.object(restart.subprocess, "run", run):
        yield world


def test_given_ac_3_1_ac_3_6_high_context_when_pre_step_then_new_compaction_precedes_restart(pre_step_world):
    world = pre_step_world
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 0 and report["reason"] == "restarted"
    assert world.injected == [["inject", "%1", "/save-state"]]
    assert world.ticks[0] > 0 and world.signals == [(101, signal.SIGTERM)]
    request = restart._object(restart.state_paths(world.session)[2])
    assert request["session_id"] == SESSION_ID and request["transcript"] == str(world.transcript)


def _busy_after_save_state(world, monkeypatch, *, compacts: bool):
    """The registry reads busy from the moment /save-state is submitted, as a real session does."""
    original = restart._inject_save_state

    def inject(*args):
        outcome = original(*args)
        world.record["status"] = "busy"
        world.write()
        return outcome

    monkeypatch.setattr(restart, "_inject_save_state", inject)

    def tick():
        if compacts and len(world.rows) == 2:
            world.record["status"] = "idle"
            world.compact()

    world.on_tick = tick


def test_given_ac_3_7_save_state_makes_session_busy_when_waiting_then_busy_is_not_a_refusal(
    pre_step_world, monkeypatch
):
    world = pre_step_world
    _busy_after_save_state(world, monkeypatch, compacts=True)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 0 and report["reason"] == "restarted"
    assert world.injected == [["inject", "%1", "/save-state"]] and world.signals == [(101, signal.SIGTERM)]


def test_given_ac_3_7_session_stays_busy_without_compaction_when_waiting_then_bounded_timeout_not_restart(
    pre_step_world, monkeypatch
):
    world = pre_step_world
    _busy_after_save_state(world, monkeypatch, compacts=False)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "context" and "timed out" in report["detail"]
    assert world.ticks[0] >= 3 and not world.signals


def test_given_ac_3_7_busy_after_save_state_when_identity_changes_then_still_refuses(pre_step_world, monkeypatch):
    world = pre_step_world
    _busy_after_save_state(world, monkeypatch, compacts=False)

    def tick():
        world.record["sessionId"] = OTHER_ID
        world.write()

    world.on_tick = tick
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "identity" and not world.signals


@pytest.mark.parametrize("boundary", ["old", "sidechain", "none"])
def test_given_ac_3_1_old_boundary_when_waiting_then_only_new_main_chain_compaction_counts(pre_step_world, boundary):
    world = pre_step_world
    if boundary == "old":
        world.rows.insert(
            0, {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"trigger": "manual"}}
        )
    world.on_tick = lambda: None
    if boundary == "sidechain":
        world.rows.append({"type": "system", "subtype": "compact_boundary", "isSidechain": True})
    world.write()
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "context"
    assert "timed out" in report["detail"] and "STARTED" in report["detail"]
    assert world.ticks[0] >= 3 and len(world.injected) == 1 and not world.signals


def test_given_ac_3_2_compaction_when_idle_settles_then_all_gates_and_pin_run(pre_step_world, monkeypatch):
    world = pre_step_world
    world.settings["restart_session"]["settle_seconds"] = 1
    original = restart.prepare_request

    def prepare(*args):
        assert world.ticks[0] >= 1.2
        assert restart.context_gate(
            restart.transcript_tail(world.transcript), config.read_restart_config(world.settings)
        )
        return original(*args)

    monkeypatch.setattr(restart, "prepare_request", prepare)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 0 and report["reason"] == "restarted"


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("context", "context"),
        ("activation", "rc-activation"),
        ("identity", "identity"),
        ("pin", "supervisor-upgrade-required"),
    ],
)
def test_given_ac_3_2_changed_gate_when_compacted_then_restart_refuses(pre_step_world, monkeypatch, fault, reason):
    world = pre_step_world

    def tick():
        world.compact()
        if fault == "context":
            world.rows[-2]["compactMetadata"]["trigger"] = "manual"
            world.write()
        elif fault == "activation":
            (world.root / ".claude" / "settings.json").write_text('{"remoteControlAtStartup":false}', encoding="utf-8")
        elif fault == "identity":
            world.record["sessionId"] = OTHER_ID
            world.write()
        else:
            monkeypatch.setattr(restart, "_restart_pin_gate", Mock(side_effect=restart.Refusal(reason)))

    world.on_tick = tick
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == reason
    assert len(world.injected) == 1 and not world.signals
    assert not restart.state_paths(world.session)[2].exists()


@pytest.mark.parametrize(
    ("state", "exit_code"),
    [
        ("HELD", 0),
        ("HELD", 6),
        ("QUEUED", 0),
        ("QUEUED", 1),
        ("QUEUED", 3),
        ("REFUSED", 3),
        ("AMBIGUOUS", 4),
        ("BOUND", 4),
        ("CORRUPTED", 4),
        ("DROPPED", 0),
        ("SENT", 1),
        ("untrusted-value", 0),
    ],
)
def test_given_ac_3_3_adapter_refusal_when_injected_then_own_outcome_and_no_signal(pre_step_world, state, exit_code):
    world = pre_step_world
    world.outcome = {
        "state": state,
        "submitted": True,
        "landing": "queued",
        "reason": "private-value-must-not-appear",
    }
    world.inject_code = exit_code
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "context"
    expected_state = "UNKNOWN" if state == "untrusted-value" else state
    assert report["detail"] == f"injection outcome: {expected_state} (exit {exit_code})"
    assert "untrusted-value" not in json.dumps(report)
    assert "private-value" not in json.dumps(report)
    assert len(world.injected) == 1 and not world.signals and world.ticks[0] == 0


@pytest.mark.parametrize("compaction_at", [0.2, 2.8])
def test_given_ac_q_1_ac_q_2_queued_save_state_when_compacted_and_settled_then_restarts(
    pre_step_world, monkeypatch, compaction_at
):
    world = pre_step_world
    world.outcome = {"state": "QUEUED", "submitted": True, "landing": "queued"}
    world.inject_code = 2
    world.settings["restart_session"]["settle_seconds"] = 1
    _busy_after_save_state(world, monkeypatch, compacts=True)
    tick = world.on_tick

    def waiting():
        assert not world.signals and not restart.state_paths(world.session)[2].exists()
        if world.ticks[0] >= compaction_at:
            tick()

    world.on_tick = waiting
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 0 and report["reason"] == "restarted"
    assert world.ticks[0] >= compaction_at + 1 and world.signals == [(101, signal.SIGTERM)]
    assert world.injected == [["inject", "%1", "/save-state"]]
    assert restart._object(restart.state_paths(world.session)[2])["session_id"] == SESSION_ID


@pytest.mark.parametrize("busy", [False, True])
def test_given_ac_q_3_queued_save_state_when_no_compaction_then_context_timeout_without_restart(
    pre_step_world, monkeypatch, busy
):
    world = pre_step_world
    world.outcome = {"state": "QUEUED", "submitted": True, "landing": "queued"}
    world.inject_code = 2
    world.settings["restart_session"]["settle_seconds"] = 1
    world.on_tick = lambda: None
    if busy:
        _busy_after_save_state(world, monkeypatch, compacts=False)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "context"
    assert report["detail"] == (
        "injection outcome: QUEUED (exit 2); compaction wait timed out; "
        "save-state was queued and never observed to compact"
    )
    assert world.ticks[0] == world.settings["restart_session"]["compaction_wait_seconds"]
    assert world.injected == [["inject", "%1", "/save-state"]] and not world.signals
    assert not restart.state_paths(world.session)[2].exists()


@pytest.fixture
def transcript_race(pre_step_world, monkeypatch):
    world = pre_step_world
    race = SimpleNamespace(reads=0, changed=[], when=lambda: bool(world.injected), failure="changing")
    fstat = restart.os.fstat
    target = world.transcript.stat()
    before = True

    def stat(fd):
        nonlocal before
        result = fstat(fd)
        if (result.st_dev, result.st_ino) != (target.st_dev, target.st_ino):
            return result
        first = before
        before = not before
        if first:
            race.reads += 1
        elif race.when():
            assert not world.signals and not restart.state_paths(world.session)[2].exists()
            race.changed.append(world.ticks[0])
            if race.failure == "unreadable":
                raise OSError("transcript read unavailable")
            # Append real bytes between the reader's size/mtime checks.
            with world.transcript.open("ab") as output:
                output.write(b'{"type":"progress"}\n')
            result = fstat(fd)
        return result

    monkeypatch.setattr(restart.os, "fstat", stat)
    return race


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("failure", ["changing", "unreadable"])
def test_given_ac_k_1_ac_k_2_transcript_races_when_waiting_then_recovers_and_restarts(
    pre_step_world, transcript_race, queued, failure
):
    world = pre_step_world
    world.settings["restart_session"]["settle_seconds"] = 1
    if queued:
        world.outcome = {"state": "QUEUED", "submitted": True, "landing": "queued"}
        world.inject_code = 2
    transcript_race.failure = failure
    transcript_race.when = lambda: bool(world.injected) and world.ticks[0] < 0.6
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 0 and report["reason"] == "restarted"
    assert transcript_race.changed == pytest.approx([0, 0.2, 0.4])
    assert world.ticks[0] >= 1.6 - 1e-6
    assert world.injected == [["inject", "%1", "/save-state"]] and world.signals == [(101, signal.SIGTERM)]


@pytest.mark.parametrize("recovers", [False, True])
def test_given_ac_k_3_compacted_transcript_when_read_skipped_then_settle_interval_restarts(
    pre_step_world, transcript_race, recovers
):
    world = pre_step_world
    world.settings["restart_session"]["settle_seconds"] = 1
    transcript_race.when = lambda: world.ticks[0] >= 0.8 and (not recovers or world.ticks[0] < 1.2)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert transcript_race.changed
    assert world.injected == [["inject", "%1", "/save-state"]]
    if recovers:
        assert code == 0 and report["reason"] == "restarted"
        assert world.ticks[0] >= 2.2 - 1e-6 and world.signals == [(101, signal.SIGTERM)]
    else:
        assert code == 1 and report["reason"] == "context"
        assert world.ticks[0] == 4 and not world.signals
        assert "transcript was unreadable on the last pass" in report["detail"]


@pytest.mark.parametrize("queued", [False, True])
def test_given_ac_k_4_unreadable_transcript_when_deadline_passes_then_timeout_names_last_pass(
    pre_step_world, transcript_race, queued
):
    world = pre_step_world
    world.on_tick = lambda: None
    state, exit_code = ("QUEUED", 2) if queued else ("STARTED", 0)
    world.outcome = {"state": state, "submitted": True, "landing": "queued"}
    world.inject_code = exit_code
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "context"
    detail = f"injection outcome: {state} (exit {exit_code}); compaction wait timed out"
    if queued:
        detail += "; save-state was queued and never observed to compact"
    assert report["detail"] == detail + "; transcript was unreadable on the last pass"
    assert transcript_race.changed and world.ticks[0] == 3
    assert world.injected == [["inject", "%1", "/save-state"]] and not world.signals
    assert not restart.state_paths(world.session)[2].exists()


def test_given_ac_k_4_skipped_read_when_last_pass_readable_then_timeout_omits_unreadable_detail(
    pre_step_world, transcript_race
):
    world = pre_step_world
    world.on_tick = lambda: None
    transcript_race.when = lambda: bool(world.injected) and world.ticks[0] < 0.4
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "context"
    assert report["detail"] == "injection outcome: STARTED (exit 0); compaction wait timed out"
    assert transcript_race.changed and world.ticks[0] == 3 and not world.signals


@pytest.mark.parametrize("read_number", [1, 2, 3])
def test_given_ac_k_5_transcript_races_when_read_before_injection_then_refuses_immediately(
    pre_step_world, transcript_race, read_number
):
    world = pre_step_world
    transcript_race.when = lambda: transcript_race.reads == read_number
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "background-work"
    assert report["detail"] == "transcript evidence unreadable"
    assert transcript_race.reads == read_number and transcript_race.changed == [0]
    assert not world.injected and not world.signals and world.ticks[0] == 0


def test_given_ac_k_5_transcript_races_when_final_assessment_reads_then_refuses_immediately(
    pre_step_world, transcript_race, monkeypatch
):
    world = pre_step_world
    transcript_race.when = lambda: False
    assess = restart.assess

    def assess_with_race(*args, **kwargs):
        if world.injected:
            transcript_race.when = lambda: True
        return assess(*args, **kwargs)

    monkeypatch.setattr(restart, "assess", assess_with_race)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "background-work"
    assert report["detail"] == "transcript evidence unreadable"
    assert transcript_race.changed == [0.2] and world.ticks[0] == 0.2
    assert world.injected == [["inject", "%1", "/save-state"]] and not world.signals


@pytest.fixture
def background_launch(pre_step_world):
    world = pre_step_world

    def start():
        world.rows.insert(0, {"type": "user", "toolUseResult": {"backgroundTaskId": "task-1"}})
        world.write()

    def finish():
        world.rows.append({"type": "task-notification", "taskId": "task-1", "status": "completed"})
        world.write()

    return SimpleNamespace(start=start, finish=finish)


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("compacted", [False, True])
def test_given_ac_l_1_ac_l_4_unfinished_launch_when_deadline_passes_then_timeout_names_last_pass(
    pre_step_world, background_launch, compacted, queued
):
    world = pre_step_world
    world.settings["restart_session"]["settle_seconds"] = 1
    state, exit_code = ("QUEUED", 2) if queued else ("STARTED", 0)
    world.outcome = {"state": state, "submitted": True, "landing": "queued"}
    world.inject_code = exit_code

    def tick():
        assert not world.signals and not restart.state_paths(world.session)[2].exists()
        if compacted and len(world.rows) == 2:
            world.compact()
        if world.ticks[0] >= (0.8 if compacted else 0.2) and len(world.rows) == (4 if compacted else 2):
            background_launch.start()

    world.on_tick = tick
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "context"
    detail = f"injection outcome: {state} (exit {exit_code}); compaction wait timed out"
    if queued and not compacted:
        detail += "; save-state was queued and never observed to compact"
    assert report["detail"] == detail + "; an unfinished background launch was seen on the last pass"
    assert world.ticks[0] == (4 if compacted else 3)
    assert world.injected == [["inject", "%1", "/save-state"]] and not world.signals
    assert not restart.state_paths(world.session)[2].exists()


@pytest.mark.parametrize("queued", [False, True])
def test_given_ac_l_1_ac_l_2_launch_finishes_when_compaction_and_idle_observed_then_restarts(
    pre_step_world, background_launch, queued
):
    world = pre_step_world
    world.settings["restart_session"]["settle_seconds"] = 1
    if queued:
        world.outcome = {"state": "QUEUED", "submitted": True, "landing": "queued"}
        world.inject_code = 2

    def tick():
        if world.ticks[0] < 0.6:
            assert not world.signals and not restart.state_paths(world.session)[2].exists()
            if len(world.rows) == 2:
                background_launch.start()
        elif len(world.rows) == 3:
            background_launch.finish()
            world.compact()

    world.on_tick = tick
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 0 and report["reason"] == "restarted"
    assert world.ticks[0] >= 1.6 - 1e-6
    assert world.injected == [["inject", "%1", "/save-state"]] and world.signals == [(101, signal.SIGTERM)]


def test_given_ac_l_3_compaction_seen_when_launch_pass_skipped_then_settle_interval_restarts(
    pre_step_world, background_launch
):
    world = pre_step_world
    world.settings["restart_session"]["settle_seconds"] = 1

    def tick():
        if len(world.rows) == 2:
            world.compact()
        elif world.ticks[0] >= 0.8 and len(world.rows) == 4:
            background_launch.start()
        elif world.ticks[0] >= 1.2 and len(world.rows) == 5:
            background_launch.finish()

    world.on_tick = tick
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 0 and report["reason"] == "restarted"
    assert world.ticks[0] >= 2.2 - 1e-6
    assert world.injected == [["inject", "%1", "/save-state"]] and world.signals == [(101, signal.SIGTERM)]


@pytest.mark.parametrize("unreadable", [False, True])
def test_given_ac_l_4_finished_launch_when_no_compaction_then_timeout_describes_only_last_pass(
    pre_step_world, background_launch, transcript_race, unreadable
):
    world = pre_step_world
    transcript_race.when = lambda: unreadable and world.ticks[0] >= 0.6

    def tick():
        if len(world.rows) == 2:
            background_launch.start()
        elif world.ticks[0] >= 0.6 and len(world.rows) == 3:
            background_launch.finish()

    world.on_tick = tick
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "context"
    detail = "injection outcome: STARTED (exit 0); compaction wait timed out"
    if unreadable:
        detail += "; transcript was unreadable on the last pass"
    assert report["detail"] == detail
    assert world.ticks[0] == 3 and not world.signals
    assert world.injected == [["inject", "%1", "/save-state"]]
    assert not restart.state_paths(world.session)[2].exists()


@pytest.mark.parametrize("read_number", [1, 2, 3])
def test_given_ac_l_5_unfinished_launch_when_read_before_injection_then_refuses_immediately(
    pre_step_world, background_launch, monkeypatch, read_number
):
    world = pre_step_world
    transcript_tail = restart.transcript_tail
    reads = 0

    def read(path):
        nonlocal reads
        reads += 1
        if reads == read_number:
            background_launch.start()
        return transcript_tail(path)

    monkeypatch.setattr(restart, "transcript_tail", read)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "background-work"
    assert report["detail"] == "unfinished background launch"
    assert reads == read_number and world.ticks[0] == 0
    assert not world.injected and not world.signals
    assert not restart.state_paths(world.session)[2].exists()


def test_given_ac_l_5_unfinished_launch_when_final_assessment_reads_then_refuses_immediately(
    pre_step_world, background_launch, monkeypatch
):
    world = pre_step_world
    assess = restart.assess

    def assess_with_launch(*args, **kwargs):
        if world.injected:
            background_launch.start()
        return assess(*args, **kwargs)

    monkeypatch.setattr(restart, "assess", assess_with_launch)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "background-work"
    assert report["detail"] == "unfinished background launch"
    assert world.ticks[0] == 0.2
    assert world.injected == [["inject", "%1", "/save-state"]] and not world.signals
    assert not restart.state_paths(world.session)[2].exists()


def test_given_ac_k_6_ac_l_6_restart_documents_when_wait_contract_checked_then_skipped_passes_are_bounded():
    root = Path(__file__).resolve().parents[1]
    plan = next((root / "docs" / "plans").glob("*restart-idle-sessions-plan.md")).read_text(encoding="utf-8")
    criterion = plan.split("- [ ] AC-3.5 When", 1)[1].split("\n\n", 1)[0]
    readme = (root / "README.md").read_text(encoding="utf-8")
    for document in (criterion, readme):
        assert "unreadable or changing transcript" in document
        assert "existing compaction deadline" in document
        assert "settle interval" in document
        assert "last pass" in document
        assert "unfinished background launch" in document


@pytest.mark.parametrize(
    "evidence",
    [
        {},
        {"submitted": True},
        {"landing": "queued"},
        {"submitted": False, "landing": "queued"},
        {"submitted": 1, "landing": "queued"},
        {"submitted": "true", "landing": "queued"},
        {"submitted": True, "landing": None},
        {"submitted": True, "landing": "command-record"},
        {"submitted": True, "attempt_id": "attempt-1"},
    ],
)
def test_given_ac_q_4_queued_without_enqueue_evidence_when_injected_then_refuses_immediately(pre_step_world, evidence):
    world = pre_step_world
    world.outcome = {"state": "QUEUED", "reason": "private-value-must-not-appear", **evidence}
    world.inject_code = 2
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "context"
    assert report["detail"] == "injection outcome: QUEUED (exit 2)"
    assert "private-value" not in json.dumps(report)
    assert world.ticks[0] == 0 and not world.signals
    assert world.injected == [["inject", "%1", "/save-state"]]
    assert not restart.state_paths(world.session)[2].exists()


@pytest.mark.parametrize("kind", ["agent", "shell", "anonymous-agent", "descendant"])
def test_given_ac_3_4_background_after_compaction_when_waiting_then_refuses(pre_step_world, kind):
    world = pre_step_world

    def tick():
        world.compact()
        if kind == "agent":
            world.rows[1]["pendingBackgroundAgentCount"] = 1
        elif kind == "shell":
            world.rows.insert(0, {"type": "user", "toolUseResult": {"backgroundTaskId": "task-1"}})
        elif kind == "anonymous-agent":
            world.rows.insert(0, {"type": "user", "toolUseResult": {"status": "async_launched"}})
        else:
            world.children.append(SimpleNamespace(pid=501, ppid=lambda: 101, cmdline=lambda: ["sleep", "300"]))
        world.write()

    world.on_tick = tick
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == ("context" if kind == "shell" else "background-work")
    if kind == "shell":
        assert world.ticks[0] == 3
        assert "an unfinished background launch was seen on the last pass" in report["detail"]
    else:
        assert world.ticks[0] == 0.2
    assert len(world.injected) == 1 and not world.signals


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("generation", "identity"),
        pytest.param(
            "lock",
            "locked",
            marks=pytest.mark.skipif(
                sys.platform == "win32", reason="Windows refuses to replace a lock file that is held open"
            ),
        ),
    ],
)
@pytest.mark.parametrize("compacted", [False, True])
def test_given_ac_3_5_wait_guard_lost_when_polling_then_aborts_without_retry(pre_step_world, fault, reason, compacted):
    world = pre_step_world

    def tick():
        if compacted:
            world.compact()
        if fault == "generation":
            world.generation = "generation-2"
        else:
            # Simulate an actor violating the stable-lock-inode protocol.
            restart._atomic_json(restart.state_paths(world.session)[0], {})

    world.on_tick = tick
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == reason
    assert len(world.injected) == 1 and not world.signals


def test_given_ac_3_5_high_context_when_checking_then_never_injects(pre_step_world):
    code, report, _ = pre_step_world.cli("-Y", SESSION_ID)
    assert code == 1 and report["gates"]["context"] == "context"
    assert not pre_step_world.injected and not pre_step_world.signals


@pytest.mark.parametrize("enabled", [True, False])
def test_given_ac_3_1_pre_step_disabled_or_unneeded_when_restarting_then_no_save_state(pre_step_world, enabled):
    world = pre_step_world
    world.settings["restart_session"]["pre_step"] = enabled
    if enabled:
        world.rows[0]["message"]["usage"]["input_tokens"] = 10_000
        world.write()
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == (0 if enabled else 1)
    assert report["reason"] == ("restarted" if enabled else "context")
    assert not world.injected


@pytest.mark.parametrize("outcome", [None, [], {"state": "STARTED", "submitted": False}, {"state": "untrusted-value"}])
def test_given_ac_3_3_invalid_adapter_outcome_when_injected_then_context_refuses(pre_step_world, outcome):
    world = pre_step_world
    world.outcome = outcome
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "context"
    assert "untrusted-value" not in json.dumps(report)
    assert len(world.injected) == 1 and not world.signals


def test_given_ac_3_5_busy_during_injection_probe_when_revalidated_then_never_injects(pre_step_world, monkeypatch):
    world = pre_step_world
    original = restart._restart_pin_gate
    calls = []

    def probe(*args):
        calls.append(args)
        if len(calls) == 2:
            world.record["status"] = "busy"
            world.write()
        return original(*args)

    monkeypatch.setattr(restart, "_restart_pin_gate", probe)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "busy"
    assert not world.injected and not world.signals


def test_given_ac_3_1_compaction_during_probe_when_injecting_then_snapshot_includes_it(pre_step_world, monkeypatch):
    world = pre_step_world
    world.on_tick = lambda: None
    original = restart._restart_pin_gate
    calls = []

    def probe(*args):
        calls.append(args)
        if len(calls) == 2:
            world.compact()
        return original(*args)

    monkeypatch.setattr(restart, "_restart_pin_gate", probe)
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "context" and "timed out" in report["detail"]
    assert len(world.injected) == 1 and not world.signals


@pytest.mark.parametrize("failure", ["timeout", "encoding", "malformed"])
def test_given_ac_3_3_adapter_error_when_injected_then_context_refuses(pre_step_world, failure):
    world = pre_step_world

    def run(argv, **kwargs):
        if argv == ["inject", "%1", "/save-state"]:
            world.injected.append(argv)
            if failure == "timeout":
                raise restart.subprocess.TimeoutExpired(argv, 15)
            if failure == "encoding":
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")
            return SimpleNamespace(returncode=0, stdout="{")
        return world.subprocess_run(argv, **kwargs)

    with patch.object(restart.subprocess, "run", run):
        code, report, _ = world.cli("-X", SESSION_ID)
    assert code == 1 and report["reason"] == "context"
    assert len(world.injected) == 1 and not world.signals


@pytest.mark.parametrize(("tokens", "allowed"), [(40_000, True), (40_001, False)])
def test_given_ac_3_2_manual_compaction_when_rechecking_then_post_tokens_threshold_applies(
    pre_step_world, tokens, allowed
):
    world = pre_step_world
    world.settings["restart_session"]["context_window_tokens"] = 200_000

    def compact():
        world.compact()
        world.rows[-2]["compactMetadata"].update(trigger="manual", postTokens=tokens)
        world.write()

    world.on_tick = compact
    code, report, _ = world.cli("-X", SESSION_ID)
    assert code == (0 if allowed else 1)
    assert report["reason"] == ("restarted" if allowed else "context")
    assert len(world.injected) == 1 and bool(world.signals) is allowed
