"""Exact-thread launcher contracts using synthetic metadata, never transcript bodies."""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ai_cli.session_script import get_engine_script

THREAD = "11111111-2222-4333-8444-555555555555"
NEWER = "66666666-7777-4888-8999-000000000000"
SLOT = "cx-myproject-1"


@pytest.fixture
def collision(tmp_path, monkeypatch):
    worktree = tmp_path / "myproject"
    worktree.mkdir()
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    rollouts = home / ".codex" / "sessions" / "2026" / "11" / "20"
    rollouts.mkdir(parents=True)
    for number, thread in enumerate((THREAD, NEWER)):
        path = rollouts / f"rollout-2026-11-20T00-00-0{number}-{thread}.jsonl"
        path.write_text(json.dumps({"type": "session_meta", "payload": {"id": thread, "cwd": str(worktree)}}) + "\n")
    state = tmp_path / "state"
    (state / "sessions").mkdir(parents=True)
    record = state / "sessions" / f"{SLOT}.codex.json"
    record.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "slot": SLOT,
                "cwd": str(worktree),
                "phase": "confirmed",
                "thread_id": THREAD,
                "rollout_path": str(next(rollouts.glob(f"*{THREAD}.jsonl"))),
                "client_version": "0.169.0",
                "execution_mode": "interactive",
                "route": "local-tmux",
                "result": "confirmed",
                "launch_token": "fixture-token",
            }
        )
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    codex = binary / "codex"
    codex.write_text("#!/bin/sh\nprintf 'codex-cli 0.169.0\\n'\n")
    codex.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    return worktree, state, record


def dispatch(collision, *, first_run, prompted):
    shell = shutil.which("bash")
    if shell is None:
        pytest.skip("POSIX launcher requires bash")
    worktree, state, _ = collision
    prompt = state / "prompt"
    if prompted:
        prompt.write_text("fixture prompt")
    script = get_engine_script("cx", "myproject-1", SLOT, "cx-myproject-", "myproject")
    helpers = ""
    if "# BEGIN CODEX THREAD" in script:
        helpers = script.split("# BEGIN CODEX THREAD", 1)[1].split("# END CODEX THREAD", 1)[0]
    body = script.split('      if [[ -f "$prompt_file" ]]; then\n        resume_msg=', 1)[1]
    body = (
        'if [[ -f "$prompt_file" ]]; then\n        resume_msg='
        + body.split("# BEGIN BACKGROUND ATTACH prompted-end", 1)[0]
    )
    capture = state / "argv.json"
    recorder = state / "record.py"
    recorder.write_text(
        "import json,sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n"
    )
    harness = (
        f"engine=cx\nfirst_run={str(first_run).lower()}\ntmux_session={SLOT}\n"
        f"_ai_state_dir={shlex.quote(str(state))}\nprompt_file={shlex.quote(str(prompt))}\n"
        f'run_agent() {{ {shlex.quote(sys.executable)} {shlex.quote(str(recorder))} {shlex.quote(str(capture))} "$@"; }}\n'
        "_ai_emit() { printf '%s\\n' \"$*\" >&2; }\n"
        "sleep() { :; }\ntmux() { :; }\n" + helpers + body
    )
    runner = state / "dispatch.sh"
    runner.write_text(harness)
    result = subprocess.run([shell, str(runner)], cwd=worktree, capture_output=True, text=True, check=False, timeout=30)
    return result, json.loads(capture.read_text()) if capture.exists() else []


@pytest.mark.skipif(os.name != "posix", reason="generated launcher uses a POSIX shell")
@pytest.mark.parametrize("first_run", [True, False])
@pytest.mark.parametrize("prompted", [True, False])
def test_given_newer_thread_in_same_directory_when_slot_resumes_then_recorded_thread_is_used(
    collision, first_run, prompted
):
    result, argv = dispatch(collision, first_run=first_run, prompted=prompted)
    assert result.returncode == 0, result.stderr
    assert argv[0] == "codex"
    assert "resume" in argv and THREAD in argv, argv
    assert "--last" not in argv and NEWER not in argv


@pytest.mark.skipif(os.name != "posix", reason="generated launcher uses a POSIX shell")
@pytest.mark.parametrize("problem", ["missing", "ambiguous", "corrupt", "ephemeral", "pending", "schema", "unrecorded"])
def test_given_unproved_persistence_when_stopped_slot_resumes_then_no_client_is_started(collision, problem):
    _, _, record = collision
    doc = json.loads(record.read_text())
    rollout = Path(doc["rollout_path"])
    if problem == "missing":
        rollout.rename(rollout.with_suffix(".unavailable"))
    elif problem == "ambiguous":
        rollout.with_name("rollout-duplicate.jsonl").write_text(rollout.read_text())
    elif problem == "corrupt":
        rollout.write_text("invalid metadata\n")
    elif problem == "ephemeral":
        doc["rollout_path"] = None
    elif problem == "pending":
        doc["phase"] = "pending"
    elif problem == "schema":
        doc["schema_version"] = 2
    elif problem == "unrecorded":
        record.rename(record.with_suffix(".unavailable"))
    if problem != "unrecorded":
        record.write_text(json.dumps(doc))
    result, argv = dispatch(collision, first_run=False, prompted=False)
    assert result.returncode == 76
    assert not argv
    assert "unknown" in result.stderr or "refus" in result.stderr


def test_given_native_event_fixture_when_replayed_then_versioned_identity_matches(collision):
    from ai_cli.codex_session import confirm, prepare

    worktree, _, record = collision
    fixture = json.loads((Path(__file__).parent / "fixtures" / "codex_session_identity_v1.json").read_text())
    rollout = json.loads(record.read_text())["rollout_path"]
    target, config = prepare(
        record, worktree, SLOT, fresh=False, route=fixture["route"], client_version=fixture["client_version"]
    )
    doc = json.loads(record.read_text())
    assert target == THREAD and doc["phase"] == "pending"
    import tomllib

    handler = tomllib.loads(config)["hooks"]["SessionStart"][0]["hooks"][0]
    assert handler["type"] == "command"
    event = {**fixture["input"], "cwd": str(worktree), "transcript_path": rollout}
    # Execute the actual callback command rendered for Codex, not a mocked confirmer.
    result = subprocess.run(
        handler["command"] if os.name == "nt" else shlex.split(handler["command"]),
        input=json.dumps(event),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    actual = json.loads(record.read_text())
    assert {key: actual[key] for key in fixture["output"]} == fixture["output"]
    assert actual["client_version"] == fixture["client_version"]
    assert actual["execution_mode"] == fixture["execution_mode"]
    assert actual["route"] == fixture["route"]
    assert fixture["canary_result"] == "unknown"
    assert not ({"model", "permission_mode", "source"} & actual.keys())
    confirm(record, doc["launch_token"], event)  # duplicate native callbacks are idempotent
    assert json.loads(record.read_text()) == actual


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_id", NEWER),
        ("session_id", "not-an-id"),
        ("cwd", "/different"),
        ("hook_event_name", "Stop"),
        ("transcript_path", {}),
    ],
)
def test_given_incompatible_native_event_when_confirmed_then_slot_remains_pending(collision, field, value):
    from ai_cli.codex_session import IdentityError, confirm, prepare

    worktree, _, record = collision
    prepare(record, worktree, SLOT, fresh=False, route="local-tmux", client_version="0.169.0")
    doc = json.loads(record.read_text())
    event = {
        "hook_event_name": "SessionStart",
        "session_id": THREAD,
        "cwd": str(worktree),
        "transcript_path": None,
        field: value,
    }
    with pytest.raises(IdentityError):
        confirm(record, doc["launch_token"], event)
    assert json.loads(record.read_text())["phase"] == "pending"


def test_given_stale_callback_when_new_launch_is_pending_then_confirmation_is_refused(collision):
    from ai_cli.codex_session import IdentityError, confirm, prepare

    worktree, _, record = collision
    prepare(record, worktree, SLOT, fresh=False, route="local-tmux", client_version="0.169.0")
    before = record.read_bytes()
    with pytest.raises(IdentityError, match="stale"):
        confirm(record, "fixture-token", {})
    assert record.read_bytes() == before


def test_given_ephemeral_native_confirmation_when_resumed_then_persistence_is_refused(collision):
    from ai_cli.codex_session import IdentityError, confirm, prepare

    worktree, _, record = collision
    prepare(record, worktree, SLOT, fresh=False, route="local-tmux", client_version="0.169.0")
    doc = json.loads(record.read_text())
    confirm(
        record,
        doc["launch_token"],
        {"hook_event_name": "SessionStart", "session_id": THREAD, "cwd": str(worktree), "transcript_path": None},
    )
    assert json.loads(record.read_text())["result"] == "live-only"
    with pytest.raises(IdentityError, match="ephemeral"):
        prepare(record, worktree, SLOT, fresh=False, route="local-tmux", client_version="0.169.0")


def test_given_unsupported_client_when_prepared_then_unknown_stays_visible(collision):
    from ai_cli.codex_session import IdentityError, prepare

    worktree, _, record = collision
    with pytest.raises(IdentityError, match="unsupported"):
        prepare(record, worktree, SLOT, fresh=False, route="paired-host-tmux", client_version="99.0.0")
    doc = json.loads(record.read_text())
    assert doc["result"] == "unknown" and doc["phase"] == "pending"
    assert doc["client_version"] == "99.0.0" and doc["route"] == "paired-host-tmux"


def test_given_empty_native_store_when_new_slot_starts_then_it_waits_for_client_confirmation(tmp_path, monkeypatch):
    from ai_cli.codex_session import IdentityError, prepare

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    record = tmp_path / "state" / "sessions" / f"{SLOT}.codex.json"
    target, _ = prepare(record, tmp_path, SLOT, fresh=True, route="local-tmux", client_version="0.169.0")
    doc = json.loads(record.read_text())
    assert target == "new" and doc["thread_id"] is None and doc["phase"] == "pending"
    with pytest.raises(IdentityError, match="pending"):
        prepare(record, tmp_path, SLOT, fresh=True, route="local-tmux", client_version="0.169.0")


def test_given_custom_native_home_when_thread_probed_then_only_that_store_is_used(collision, monkeypatch):
    from ai_cli.codex_session import prepare

    worktree, _, record = collision
    doc = json.loads(record.read_text())
    root = Path(doc["rollout_path"]).parents[4]
    custom = worktree / "custom-codex"
    root.rename(custom)
    monkeypatch.setenv("CODEX_HOME", str(custom))
    target, _ = prepare(record, worktree, SLOT, fresh=False, route="local-tmux", client_version="0.169.0")
    assert target == THREAD


@pytest.mark.skipif(os.name != "posix", reason="generated launcher uses a POSIX shell")
def test_given_project_native_home_when_slot_resumes_then_identity_uses_the_client_environment(collision):
    worktree, _, record = collision
    doc = json.loads(record.read_text())
    custom = worktree / "custom-codex"
    Path(doc["rollout_path"]).parents[4].rename(custom)
    direnv = worktree.parent / "bin" / "direnv"
    export = "export CODEX_HOME=" + shlex.quote(str(custom))
    direnv.write_text("#!/bin/sh\nprintf '%s\\n' " + shlex.quote(export) + "\n")
    direnv.chmod(0o755)
    result, argv = dispatch(collision, first_run=False, prompted=False)
    assert result.returncode == 0, result.stderr
    assert THREAD in argv and "--last" not in argv
