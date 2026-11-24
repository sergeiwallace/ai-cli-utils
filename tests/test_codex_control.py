"""A fake client exercises the launch trampoline and its live control bridge."""

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

from ai_cli import codex_control, codex_session
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
        "OS exception: the slot bridge needs Unix-domain sockets and a tmux pane, "
        "so Windows is unsupported (no bridge starts, and the harness refuses the "
        "slot as codex-no-record); macOS is unverified"
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


@pytest.mark.skipif(os.name != "posix", reason="Live bridge requires Unix-domain sockets")
@pytest.mark.parametrize("entry", ["confirm", "confirm_from_environment"])
@pytest.mark.parametrize("transcript", [None, "rollout.jsonl"])
@pytest.mark.parametrize("restore_write_fails", [False, True])
def test_given_nonancestor_hook_when_bridge_refuses_then_confirmation_is_rolled_back(
    slot, monkeypatch, capsys, entry, transcript, restore_write_fails
):
    process, _, record, metadata = slot
    before = record.read_bytes()
    original = json.loads(before)
    token = original["in_flight"]["token"]
    event = {
        "hook_event_name": "SessionStart",
        "session_id": THREAD,
        "cwd": original["cwd"],
        "transcript_path": str(record.parent / transcript) if transcript else None,
    }
    notify = codex_control.notify
    lock_held = False

    def fail_write(*args, **kwargs):
        raise OSError("record writes unavailable")

    def rejecting_notify(path, current_token):
        nonlocal lock_held
        assert json.loads(path.read_text())["phase"] == "confirmed"
        try:
            with portalocker.Lock(str(path) + ".lock", timeout=0):
                pass
        except portalocker.LockException:
            lock_held = True
        try:
            notify(path, current_token)
        except ValueError:
            if restore_write_fails:
                # A rejected confirmation must not need another record write.
                patch.setattr(codex_session, "_write", fail_write)
                patch.setattr(Path, "write_bytes", fail_write)
                patch.setattr(Path, "write_text", fail_write)
            raise

    with monkeypatch.context() as patch:
        patch.setattr(codex_control, "notify", rejecting_notify)
        if entry == "confirm":
            with pytest.raises(ValueError, match=r"^live control confirmation refused$"):
                codex_session.confirm(record, token, event)
        else:
            patch.setenv(codex_session.SLOT_RECORD_ENV, str(record))
            patch.setenv(codex_session.LAUNCH_TOKEN_ENV, token)
            patch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
            assert codex_session.confirm_from_environment([]) == 1
            assert "launch confirmation refused" in capsys.readouterr().err

    after = json.loads(record.read_text())
    assert after["phase"] == "unconfirmed"
    assert after["thread_id"] is None and after["result"] == "unconfirmed"
    assert after["in_flight"] == original["in_flight"]
    assert record.read_bytes() == before
    assert lock_held
    snapshot = request(metadata["path"], {"method": "get"})
    assert snapshot["binding"] is None and snapshot["phase"] == "unconfirmed"
    with pytest.raises(codex_session.IdentityError, match="writer is live"):
        codex_session.prepare(
            record,
            Path(original["cwd"]),
            original["slot"],
            fresh=False,
            route="local-tmux",
            client_version="0.171.0",
        )
    process.communicate("exit\n", timeout=10)
    target, _, _, next_token = codex_session.prepare(
        record,
        Path(original["cwd"]),
        original["slot"],
        fresh=False,
        route="local-tmux",
        client_version="0.171.0",
    )
    assert target == "new" and next_token != token
    assert "abandoned-unconfirmed" in capsys.readouterr().err


@pytest.mark.skipif(os.name != "posix", reason="Live bridge requires Unix-domain sockets")
def test_given_bound_bridge_when_record_is_rolled_back_then_get_discards_binding(slot):
    process, reply, record, metadata = slot
    before = record.read_bytes()
    process.stdin.write("confirm\n")
    process.stdin.flush()
    assert reply() == "confirmed"
    assert request(metadata["path"], {"method": "get"})["binding"]["thread_id"] == THREAD
    record.write_bytes(before)
    snapshot = request(metadata["path"], {"method": "get"})
    assert snapshot["binding"] is None and snapshot["phase"] == "unconfirmed"


def test_given_control_contract_when_serialized_then_matches_reciprocal_fixture():
    fixture = Path(__file__).parent / "fixtures/codex_control_contract_v1.json"
    assert json.loads(fixture.read_text()) == contract()
