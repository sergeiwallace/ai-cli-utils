"""A fake client exercises the launch trampoline and its live control bridge."""

import hashlib
import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ai_cli.codex_control import contract, request

THREAD = "11111111-2222-4333-8444-555555555555"


@pytest.fixture
def slot(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    native = home / "native"
    (native / "sessions").mkdir(parents=True)
    binary = tmp_path / "bin"
    binary.mkdir()
    client = binary / "codex"
    client.write_text(
        f"#!{sys.executable}\n"
        "import json,os,subprocess,sys\nfrom pathlib import Path\n"
        "if '--version' in sys.argv:\n print('codex-cli 0.171.0'); sys.exit(0)\n"
        "print('started',flush=True)\n"
        "if sys.stdin.readline().strip() == 'confirm':\n"
        f" event={{'hook_event_name':'SessionStart','session_id':{THREAD!r},'cwd':os.getcwd(),'transcript_path':None}}\n"
        " code='from ai_cli.codex_session import confirm_from_environment; raise SystemExit(confirm_from_environment([]))'\n"
        " result=subprocess.run([sys.executable,'-c',code],input=json.dumps(event),text=True,capture_output=True)\n"
        " print('confirmed' if result.returncode == 0 else 'refused',flush=True)\n"
        " sys.stdin.readline()\n"
    )
    client.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(native))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "state"))
    monkeypatch.setenv("TMUX_PANE", "%999")
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    directory = home / "state/ai-cli-utils/sessions"
    record = directory / "test-slot.codex.json"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ai_cli.codex_session",
            "launch",
            str(record),
            str(tmp_path),
            "test-slot",
            "true",
            "local-tmux",
        ],
        cwd=tmp_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    def reply():
        assert select.select([process.stdout], [], [], 10)[0], "fake client did not respond"
        return process.stdout.readline().strip()

    try:
        assert reply() == "started"
        publications = list(directory.glob(contract()["record_glob"]))
        assert len(publications) == 1
        metadata = json.loads(publications[0].read_text())
        yield process, reply, record, metadata
    finally:
        if process.poll() is None:
            process.communicate("exit\n", timeout=10)
        deadline = time.monotonic() + 5
        while list(directory.glob(contract()["record_glob"])) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not list(directory.glob(contract()["record_glob"]))


@pytest.mark.skipif(
    os.name != "posix",
    reason=(
        "OS exception, recorded on AIH-5v06ta and AIH-t5m4uc: the slot bridge needs "
        "Unix-domain sockets and a tmux pane, so Windows is unsupported (no bridge "
        "starts, and the harness refuses the slot as codex-no-record); macOS is "
        "unverified"
    ),
)
def test_given_launched_slot_when_hook_confirms_then_live_bridge_binds_only_that_launch(slot):
    process, reply, record, metadata = slot
    assert metadata["owner"]["pid"] == process.pid
    assert request(metadata["path"], {"method": "get"})["phase"] == "unconfirmed"
    process.stdin.write("confirm\n")
    process.stdin.flush()
    assert reply() == "confirmed"
    snapshot = request(metadata["path"], {"method": "get"})
    assert snapshot["binding"]["thread_id"] == THREAD
    assert snapshot["phase"] == "waiting"
    token = json.loads(record.read_text())["in_flight"]["token"]
    assert metadata["generation"] == hashlib.sha256(token.encode()).hexdigest()
    # Same-user callers outside the client tree cannot confirm, even with its token.
    assert request(metadata["path"], {"method": "confirm", "token": token}) == {"confirmed": False}
    assert request(metadata["path"], {"method": "confirm", "token": "stale"}) == {"confirmed": False}


def test_given_control_contract_when_serialized_then_matches_reciprocal_fixture():
    fixture = Path(__file__).parent / "fixtures/codex_control_contract_v1.json"
    assert json.loads(fixture.read_text()) == contract()
