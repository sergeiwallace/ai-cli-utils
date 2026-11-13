"""Owning-host exit receipts over a fake SSH process boundary."""

import asyncio
import json
import shlex
import subprocess
import sys
from unittest.mock import AsyncMock, Mock

import psutil
import pytest
from click.testing import CliRunner
from process_ownership import reap, spawn_owned, spawn_owned_sleeper

from ai_cli import session_exit, transport
from ai_cli import session_registry as registry
from ai_cli import session_run_state as state
from ai_cli.main import _cli_group as cli


@pytest.fixture
def remote_run(tmp_path, monkeypatch):
    client_path = registry.registry_path()
    owner_path = tmp_path / "owner" / "registry.json"
    config = {"machine": {"host_id": "host-a"}}
    monkeypatch.setattr(state.config, "load_config", lambda **kw: config)
    monkeypatch.setattr(psutil.Process, "parents", lambda self: [])
    monkeypatch.setattr(registry, "get_remote_machine", lambda *a: {"host": "example.com", "user": "user"})
    monkeypatch.setattr(state.config, "get_remote_machine", registry.get_remote_machine)
    proc = spawn_owned_sleeper()
    try:
        with monkeypatch.context() as owner:
            owner.setattr(registry, "registry_path", lambda: owner_path)
            state.register("c-session-1", recipe={"argv": ["ai", "c"], "cwd": str(tmp_path)}, host="host-a")
            generation = state.begin(
                "c-session-1", supervisor={"pid": proc.pid, "started_at": psutil.Process(proc.pid).create_time()}
            )
            assert state.mark_running("c-session-1")

        def mirror(doc):
            doc["sessions"].append(
                {
                    "id": "mirror",
                    "name": "c-session-1",
                    "kind": "remote",
                    "run_state": "running",
                    "remote": {"alias": "server", "session": "c-session-1"},
                }
            )
            return True

        registry._mutate(mirror)
        calls = []
        real_run = subprocess.run

        def run(argv, **kwargs):
            if argv == ["cleanup"]:
                return subprocess.CompletedProcess(argv, 0, "", "")
            if argv[0] != "ssh":
                return real_run(argv, **kwargs)
            calls.append(argv)
            args = shlex.split(argv[-1].partition("; ")[2] or argv[-1])
            assert args[:3] == ["ai", "sessions", "_exit-receipt"]
            with monkeypatch.context() as owner:
                owner.setattr(registry, "registry_path", lambda: owner_path)
                result = CliRunner().invoke(cli, args[1:])
            return subprocess.CompletedProcess(argv, result.exit_code, result.output, "")

        monkeypatch.setattr(subprocess, "run", run)
        yield proc, generation, client_path, owner_path, calls
    finally:
        reap(proc)


@pytest.mark.parametrize("explicit", [True, False])
def test_remote_mirror_when_exit_requested_then_owner_stops_and_client_receives_generation(remote_run, explicit):
    proc, generation, client, owner, calls = remote_run
    result = CliRunner().invoke(cli, ["exit-session", "c-session-1", *(["-r"] if explicit else []), "-t", "0.1"])
    assert result.exit_code == 0, result.output
    proc.wait(timeout=3)
    row = json.loads(client.read_text())["sessions"][0]
    assert row["run_state"] == "exited"
    assert row["generation"] == generation
    assert json.loads(owner.read_text())["sessions"][0]["run_state"] == "exited"
    assert calls and psutil.Process().is_running()


@pytest.mark.parametrize("failure", ["unreachable", "malformed", "wrong-generation"])
def test_remote_unacknowledged_when_exit_requested_then_mirror_stays_unresolved(remote_run, monkeypatch, failure):
    proc, _, client, _, _ = remote_run
    original = subprocess.run

    def run(argv, **kw):
        if failure == "unreachable":
            raise subprocess.TimeoutExpired(argv, 1)
        if failure == "malformed":
            return subprocess.CompletedProcess(argv, 0, "{}", "")
        answer = original(argv, **kw)
        if len(shlex.split(argv[-1].partition("; ")[2] or argv[-1])) > 4:
            receipt = json.loads(answer.stdout)
            receipt["generation"] = "replacement"
            answer.stdout = json.dumps(receipt)
        return answer

    monkeypatch.setattr(subprocess, "run", run)
    before = client.read_bytes()
    result = CliRunner().invoke(cli, ["exit-session", "c-session-1", "-r", "-t", "0.1"])
    assert result.exit_code != 0
    assert "unresolved" in result.output.lower()
    assert client.read_bytes() == before
    if failure != "wrong-generation":
        assert proc.poll() is None


@pytest.mark.parametrize("kind", ["ssh", "mosh"])
def test_remote_self_exit_when_transport_disconnects_then_receipt_stops_reconnect(remote_run, monkeypatch, kind):
    proc, generation, client, owner, _ = remote_run
    with monkeypatch.context() as host:
        host.setattr(registry, "registry_path", lambda: owner)
        result = CliRunner().invoke(cli, ["exit-session", "c-session-1", "-t", "0.1"])
        assert result.exit_code == 0, result.output
    proc.wait(timeout=3)

    def callback():
        return session_exit.receive_remote_exit("mirror")

    attempts = []
    monkeypatch.setattr(transport, "restore_terminal", lambda: None)
    if kind == "ssh":

        def call(*a, **kw):
            attempts.append(a)
            return 255

        monkeypatch.setattr(subprocess, "call", call)
        assert transport.run_ssh_with_reconnect(["ssh", "user@example.com"], ["cleanup"], on_exit_receipt=callback) == 0
    else:
        nc = Mock(nc=None, connect=AsyncMock(), close=AsyncMock())
        monkeypatch.setattr("ai_cli.messaging.NATSClient", lambda *a: nc)
        monkeypatch.setattr(transport, "_is_vpn_active", lambda *a: False)

        def popen(*a, **kw):
            attempts.append(a)
            return Mock(pid=42, returncode=255, poll=lambda: 255, wait=lambda: 255)

        monkeypatch.setattr(subprocess, "Popen", popen)
        asyncio.run(
            transport._run_transport_loop(["ssh"], ["mosh"], ["cleanup"], "c-session-1", {}, on_exit_receipt=callback)
        )
    assert len(attempts) == 1
    row = json.loads(client.read_text())["sessions"][0]
    assert row["generation"] == generation and row["run_state"] == "exited"


def test_remote_mirror_when_dry_run_then_owner_and_client_are_unchanged(remote_run):
    proc, _, client, owner, _ = remote_run
    before = (client.read_bytes(), owner.read_bytes())
    result = CliRunner().invoke(cli, ["exit-session", "c-session-1", "-r", "-d"])
    assert result.exit_code == 0, result.output
    assert (client.read_bytes(), owner.read_bytes()) == before
    assert proc.poll() is None


def test_remote_replacement_when_fenced_request_arrives_then_new_run_survives(remote_run):
    proc, generation, client, owner, _ = remote_run
    from unittest.mock import patch

    with patch.object(registry, "registry_path", lambda: owner):
        replacement = state.begin("c-session-1")
        before = owner.read_bytes()
        result = CliRunner().invoke(cli, ["sessions", "_exit-receipt", "c-session-1", generation, "0.1"])
        assert result.exit_code != 0
        assert "generation changed" in result.output
        assert owner.read_bytes() == before
        assert state._find(registry.load_registry(), "c-session-1")["generation"] == replacement
    assert proc.poll() is None
    assert json.loads(client.read_text())["sessions"][0]["run_state"] == "running"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX remote-shell fixture")
def test_remote_shell_without_user_path_when_receipt_requested_then_installed_cli_is_resolved(
    remote_run, monkeypatch, tmp_path
):
    _, generation, _, _, _ = remote_run
    home = tmp_path / "remote-home"
    executable = home / ".local/bin/ai"
    executable.parent.mkdir(parents=True)
    receipt = {"name": "c-session-1", "owning_host": "host-a", "generation": generation, "run_state": "running"}
    executable.write_text("#!/bin/sh\nprintf '%s\\n' " + shlex.quote(json.dumps(receipt)) + "\n")
    executable.chmod(0o755)

    def ssh(argv, **kw):
        assert argv[0] == "ssh"
        child = spawn_owned(
            ["/bin/sh", "-c", argv[-1]],
            env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = child.communicate(timeout=5)
            return subprocess.CompletedProcess(argv, child.returncode, stdout, stderr)
        finally:
            reap(child)

    monkeypatch.setattr(subprocess, "run", ssh)
    row = registry.load_registry()["sessions"][0]
    assert session_exit._remote_receipt(row) == receipt


def test_acknowledged_exit_when_host_becomes_unreachable_then_client_receipt_still_vetoes_reconnect(
    remote_run, monkeypatch
):
    _, _, client, _, _ = remote_run
    result = CliRunner().invoke(cli, ["exit-session", "c-session-1", "-r", "-t", "0.1"])
    assert result.exit_code == 0, result.output
    before = client.read_bytes()
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired("ssh", 1)))
    assert session_exit.receive_remote_exit("mirror") is True
    assert client.read_bytes() == before


def test_prior_client_receipt_when_session_is_reattached_then_new_client_requires_fresh_acknowledgement(
    remote_run, monkeypatch
):
    from ai_cli.iterm2 import _PERSISTENCE_DEFAULTS

    result = CliRunner().invoke(cli, ["exit-session", "c-session-1", "-r", "-t", "0.1"])
    assert result.exit_code == 0, result.output
    row = registry.record_launch(
        kind="remote",
        name="c-session-1",
        relaunch_argv=["ai", "c", "1", "-R"],
        cwd=None,
        remote={"alias": "server", "session": "c-session-1"},
        tty="",
        iterm_session_id=None,
        launcher_pid=42,
        config=_PERSISTENCE_DEFAULTS,
    )
    assert row["id"] != "mirror"
    assert "exit_receipt_client_id" not in row
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired("ssh", 1)))
    assert session_exit.receive_remote_exit(row["id"]) is False
