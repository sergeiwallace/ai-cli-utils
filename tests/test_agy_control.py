"""A fake Antigravity client exercises the launch trampoline and its live control bridge."""

from __future__ import annotations

import hashlib
import io
import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

import portalocker
import pytest

from ai_cli import agy_control, agy_session, config
from ai_cli.agy_control import contract, request

CONVERSATION = "11111111-2222-4333-8444-555555555555"


@pytest.fixture
def agy_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    token_path = home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text('{"access_token": "tok"}', encoding="utf-8")
    binary = tmp_path / "bin"
    binary.mkdir()
    client = binary / "agy"
    client.write_text(
        f"#!{sys.executable}\n"
        "import json, os, subprocess, sys\n"
        "if '--version' in sys.argv:\n"
        "    print('antigravity 1.5.14'); sys.exit(0)\n"
        "print('started', flush=True)\n"
        "if sys.stdin.readline().strip() == 'confirm':\n"
        f"    event = {{'hookEventName': 'SessionStart', 'conversationId': {CONVERSATION!r}, 'cwd': os.getcwd(), 'transcriptPath': None}}\n"
        "    code = 'from ai_cli.agy_session import confirm_from_environment; raise SystemExit(confirm_from_environment([]))'\n"
        "    result = subprocess.run([sys.executable, '-c', code], input=json.dumps(event), text=True, capture_output=True)\n"
        "    print('confirmed' if result.returncode == 0 else 'refused', flush=True)\n"
        "    sys.stdin.readline()\n",
        encoding="utf-8",
    )
    client.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "state"))
    monkeypatch.setenv("TMUX_PANE", "%998")
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    return home, tmp_path


@pytest.fixture
def register_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    log = tmp_path / "register.jsonl"
    tool = tmp_path / "register.py"
    tool.write_text(
        "import hashlib, json, os, sys\n"
        "from pathlib import Path\n"
        "from ai_cli.agy_control import request\n"
        "record = Path(os.environ['AI_CLI_AGY_SLOT_RECORD'])\n"
        "generation = hashlib.sha256(os.environ['AI_CLI_AGY_LAUNCH_TOKEN'].encode()).hexdigest()\n"
        "metadata = json.loads(record.with_name(f'{record.stem}.{generation}.control-v1.json').read_text())\n"
        "snapshot = request(metadata['path'], {'method': 'get'})\n"
        "with Path(sys.argv[1]).open('a', encoding='utf-8') as log:\n"
        "    log.write(json.dumps([sys.argv[2], os.environ.get('AI_TMUX_SESSION'), snapshot['phase'],\n"
        "                          snapshot['binding']['conversation_id']]) + '\\n')\n"
        "sys.exit(int(os.environ.get('TEST_REGISTER_EXIT', '0')))\n",
        encoding="utf-8",
    )
    config_path = config._config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(tool), str(log), "{client}"]
    config_path.write_text(f"[session_register]\ncommand = {json.dumps(command)}\n", encoding="utf-8")

    def calls():
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []

    return calls


@pytest.fixture
def slot(agy_env, monkeypatch):
    home, workdir = agy_env
    directory = home / "state/ai-cli-utils/sessions"
    record = directory / "test-slot.agy.json"
    monkeypatch.setenv("AI_TMUX_SESSION", "test-slot")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ai_cli.agy_session",
            "launch",
            str(record),
            str(workdir),
            "test-slot",
            "myproject-wt-1",
            "true",
            "local-tmux",
            "--command",
            "agy",
        ],
        cwd=workdir,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    def reply():
        assert process.stdout is not None
        assert select.select([process.stdout], [], [], 10)[0], "fake client did not respond"
        return process.stdout.readline().strip()

    try:
        assert reply() == "started"
        publications = list(directory.glob(contract()["record_glob"]))
        assert len(publications) == 1
        metadata = json.loads(publications[0].read_text(encoding="utf-8"))
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
        "OS exception: the slot bridge needs Unix-domain sockets and a tmux pane, "
        "so Windows is unsupported (no bridge starts)"
    ),
)
def test_given_launched_agy_slot_when_hook_confirms_then_live_bridge_binds_only_that_launch(register_calls, slot):
    process, reply, record, metadata = slot
    assert metadata["owner"]["pid"] == process.pid
    assert request(metadata["path"], {"method": "get"})["phase"] == "unconfirmed"
    assert register_calls() == []
    process.stdin.write("confirm\n")
    process.stdin.flush()
    assert reply() == "confirmed"
    snapshot = request(metadata["path"], {"method": "get"})
    assert snapshot["binding"]["conversation_id"] == CONVERSATION
    assert snapshot["phase"] == "waiting"
    assert register_calls() == [["agy", "test-slot", "waiting", CONVERSATION]]
    token = json.loads(record.read_text(encoding="utf-8"))["in_flight"]["token"]
    assert metadata["generation"] == hashlib.sha256(token.encode()).hexdigest()
    # Same-user callers outside the client tree cannot confirm, even with its token.
    assert request(metadata["path"], {"method": "confirm", "token": token}) == {"confirmed": False}
    assert request(metadata["path"], {"method": "confirm", "token": "stale"}) == {"confirmed": False}


@pytest.mark.skipif(os.name != "posix", reason="Live bridge requires Unix-domain sockets")
@pytest.mark.parametrize("entry", ["confirm", "confirm_from_environment"])
@pytest.mark.parametrize("restore_write_fails", [False, True])
def test_given_nonancestor_agy_hook_when_bridge_refuses_then_confirmation_is_rolled_back(
    slot, monkeypatch, capsys, entry, restore_write_fails
):
    process, _, record, metadata = slot
    before = record.read_bytes()
    original = json.loads(before)
    token = original["in_flight"]["token"]
    event = {
        "hookEventName": "SessionStart",
        "conversationId": CONVERSATION,
        "cwd": original["cwd"],
        "transcriptPath": None,
    }
    notify = agy_control.notify
    lock_held = False

    def fail_write(*args, **kwargs):
        raise OSError("record writes unavailable")

    def rejecting_notify(path, current_token):
        nonlocal lock_held
        assert json.loads(path.read_text(encoding="utf-8"))["phase"] == "confirmed"
        try:
            with portalocker.Lock(str(path) + ".lock", timeout=0):
                pass
        except portalocker.LockException:
            lock_held = True
        try:
            notify(path, current_token)
        except ValueError:
            if restore_write_fails:
                patch.setattr(agy_session, "_write", fail_write)
                patch.setattr(Path, "write_bytes", fail_write)
                patch.setattr(Path, "write_text", fail_write)
            raise

    with monkeypatch.context() as patch:
        patch.setattr(agy_control, "notify", rejecting_notify)
        if entry == "confirm":
            with pytest.raises(ValueError, match=r"^live control confirmation refused$"):
                agy_session.confirm(record, token, event)
        else:
            patch.setenv(agy_session.SLOT_RECORD_ENV, str(record))
            patch.setenv(agy_session.LAUNCH_TOKEN_ENV, token)
            patch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
            assert agy_session.confirm_from_environment([]) == 1
            assert "launch confirmation refused" in capsys.readouterr().err

    after = json.loads(record.read_text(encoding="utf-8"))
    assert after["phase"] == "unconfirmed"
    assert after["conversation_id"] is None and after["result"] == "unconfirmed"
    assert after["in_flight"] == original["in_flight"]
    assert record.read_bytes() == before
    assert lock_held
    snapshot = request(metadata["path"], {"method": "get"})
    assert snapshot["binding"] is None and snapshot["phase"] == "unconfirmed"


@pytest.mark.skipif(os.name != "posix", reason="Live bridge requires Unix-domain sockets")
def test_given_bound_agy_bridge_when_record_is_rolled_back_then_get_discards_binding(slot):
    process, reply, record, metadata = slot
    before = record.read_bytes()
    process.stdin.write("confirm\n")
    process.stdin.flush()
    assert reply() == "confirmed"
    assert request(metadata["path"], {"method": "get"})["binding"]["conversation_id"] == CONVERSATION
    record.write_bytes(before)
    snapshot = request(metadata["path"], {"method": "get"})
    assert snapshot["binding"] is None and snapshot["phase"] == "unconfirmed"


@pytest.mark.skipif(os.name != "posix", reason="Live bridge requires Unix-domain sockets")
@pytest.mark.parametrize("register_exit", [0, 3])
def test_given_adopted_agy_slot_when_resumed_then_registers_before_client_starts(
    agy_env, register_calls, monkeypatch, register_exit
):
    home, workdir = agy_env
    directory = home / "state/ai-cli-utils/sessions"
    record = directory / "resumed-slot.agy.json"
    # Persist the native store evidence used to adopt and resume the conversation.
    cache = home / ".gemini" / "antigravity-cli" / "cache" / "last_conversations.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({str(workdir.resolve()): CONVERSATION}), encoding="utf-8")
    conv_db = home / ".gemini" / "antigravity-cli" / "conversations" / f"{CONVERSATION}.db"
    conv_db.parent.mkdir(parents=True, exist_ok=True)
    conv_db.write_bytes(b"")
    agy_session.prepare(
        record,
        workdir,
        "resumed-slot",
        fresh=False,
        route="local-tmux",
        client_version="1.5.14",
        adopt_conversation=(CONVERSATION, "resumed-slot"),
    )
    # Mark previous writer dead so the resume launch takes ownership cleanly.
    doc = json.loads(record.read_text(encoding="utf-8"))
    doc["in_flight"]["writer"]["pid"] = 99999999
    record.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.delenv("AI_TMUX_SESSION", raising=False)
    monkeypatch.setenv("TEST_REGISTER_EXIT", str(register_exit))

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ai_cli.agy_session",
            "launch",
            str(record),
            str(workdir),
            "resumed-slot",
            "myproject-wt-1",
            "false",
            "local-tmux",
            "--command",
            "agy",
        ],
        cwd=workdir,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert select.select([process.stdout], [], [], 10)[0]
        assert process.stdout.readline().strip() == "started"
        publications = list(directory.glob(contract()["record_glob"]))
        assert len(publications) == 1
        metadata = json.loads(publications[0].read_text(encoding="utf-8"))
        # Without re-firing SessionStart, the resumed bridge already attests the corroborated ID.
        snapshot = request(metadata["path"], {"method": "get"})
        assert snapshot["phase"] == "waiting"
        assert snapshot["binding"]["conversation_id"] == CONVERSATION
        assert register_calls() == [["agy", "resumed-slot", "waiting", CONVERSATION]]
        _, stderr = process.communicate("exit\n", timeout=10)
        assert process.returncode == 0
        warnings = [line for line in stderr.splitlines() if "Session register skipped" in line]
        assert len(warnings) == bool(register_exit)
    finally:
        if process.poll() is None:
            process.communicate("exit\n", timeout=10)
        deadline = time.monotonic() + 5
        while list(directory.glob(contract()["record_glob"])) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not list(directory.glob(contract()["record_glob"]))


def test_given_shared_slot_control_contract_when_serialized_then_matches_codex_and_agy():
    from ai_cli import codex_control

    fixture = Path(__file__).parent / "fixtures/slot_control_contract_v1.json"
    shared = json.loads(fixture.read_text(encoding="utf-8"))
    common = {k: v for k, v in shared.items() if k != "clients"}
    assert {**common, **shared["clients"]["codex"]} == codex_control.contract()
    assert {**common, **shared["clients"]["agy"]} == contract()
    assert shared["clients"]["codex"]["slot_schema_version"] == 2
    assert shared["clients"]["agy"]["slot_schema_version"] == 1
