"""Exact-thread launcher contracts using synthetic metadata, never transcript bodies."""

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
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
                "schema_version": 2,
                "slot": SLOT,
                "cwd": str(worktree),
                "phase": "confirmed",
                "thread_id": THREAD,
                "rollout_path": str(next(rollouts.glob(f"*{THREAD}.jsonl"))),
                "client_version": "0.169.0",
                "execution_mode": "interactive",
                "route": "local-tmux",
                "result": "confirmed",
                "resumable": {"thread_id": THREAD, "rollout_path": str(next(rollouts.glob(f"*{THREAD}.jsonl")))},
                "in_flight": {
                    "token": "fixture-token",
                    "writer": {
                        "pid": 99999999,
                        "backend": "procfs" if sys.platform == "linux" else "psutil",
                        "start_time": 100,
                    },
                    "launched_at": "2026-11-21T00:00:00+00:00",
                    "client_version": "0.169.0",
                    "route": "local-tmux",
                },
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


def dispatch(collision, *, first_run, prompted, shell_name="bash", launches=1):
    shell = shutil.which(shell_name)
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
        "import json,os,sys\nfrom pathlib import Path\nfrom ai_cli.codex_session import _writer\n"
        "Path(sys.argv[1]).with_name('writer.json').write_text(json.dumps(_writer()))\n"
        "Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n"
        "Path(sys.argv[1]).with_name('environment.json').write_text(json.dumps({k:os.environ.get(k) for k in "
        "('AI_CLI_CODEX_SLOT_RECORD','AI_CLI_CODEX_LAUNCH_TOKEN')}))\n"
    )
    fake = worktree.parent / "bin" / "codex"
    fake.write_text(
        f"#!{sys.executable}\nimport os,sys\nif sys.argv[1:] == ['--version']:\n print('codex-cli 0.169.0'); sys.exit(0)\nos.execv(sys.executable, [sys.executable, {str(recorder)!r}, {str(capture)!r}, 'codex', *sys.argv[1:]])\n"
    )
    harness = (
        f"engine=cx\nfirst_run={str(first_run).lower()}\ntmux_session={SLOT}\n"
        f"_ai_state_dir={shlex.quote(str(state))}\nprompt_file={shlex.quote(str(prompt))}\n"
        'run_agent() { "$@"; agent_exit_code=$?; return 0; }\n'
        "_ai_emit() { printf '%s\\n' \"$*\" >&2; }\n"
        "sleep() { :; }\ntmux() { :; }\n"
        + helpers
        + "\nfirst_run=false\n".join([body] * launches)
        + f'\nprintf \'%s:%s\' "${{AI_CLI_CODEX_SLOT_RECORD-unset}}" "${{AI_CLI_CODEX_LAUNCH_TOKEN-unset}}" > {shlex.quote(str(state / "parent-env"))}\n'
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
        doc.update(rollout_path=None, result="live-only", resumable=None)
    elif problem == "pending":
        doc["phase"] = "pending"
    elif problem == "schema":
        doc["schema_version"] = 1
    elif problem == "unrecorded":
        record.rename(record.with_suffix(".unavailable"))
    if problem != "unrecorded":
        record.write_text(json.dumps(doc))
    result, argv = dispatch(collision, first_run=False, prompted=False)
    if problem == "ephemeral":
        # Null hook paths can now be upgraded only with exact persisted ID proof.
        assert result.returncode == 0, result.stderr
        assert THREAD in argv and "resume" in argv
        assert json.loads(record.read_text())["result"] == "confirmed"
        return
    assert result.returncode == 76
    assert not argv
    assert "[session]" in result.stderr
    assert "unknown" in result.stderr or "refus" in result.stderr


def test_given_confirmed_slot_when_prepared_by_cli_then_reply_is_four_machine_readable_lines(
    collision, monkeypatch, capsys
):
    from ai_cli.codex_session import main

    worktree, _, record = collision

    def version(argv, **kwargs):
        assert argv == ["codex", "--version"]
        return subprocess.CompletedProcess(argv, 0, "codex-cli 0.169.0\n", "")

    # The version probe is synthetic on every OS; prepare and its filesystem proof
    # stay real, as does the output interface whose protocol this test exercises.
    monkeypatch.setattr("ai_cli.codex_session.subprocess.run", version)
    assert main(["prepare", str(record), str(worktree), SLOT, "false", "local-tmux"]) == 0
    reply = capsys.readouterr()
    target, config, record_path, token = reply.out.splitlines()
    assert record_path == str(record.resolve())
    assert token == json.loads(record.read_text())["in_flight"]["token"]
    assert target == THREAD
    assert tomllib.loads(config)["hooks"]["SessionStart"][0]["hooks"][0]["type"] == "command"
    assert "[session]" in reply.err and SLOT in reply.err
    assert token not in reply.err


@pytest.mark.parametrize("operation", ["invalid", "confirm"])
def test_given_invalid_identity_input_when_cli_refuses_then_error_is_tagged_without_echoing_payload(
    tmp_path, operation
):
    args = [] if operation == "invalid" else ["confirm", str(tmp_path / "slot.json"), "fixture-token"]
    payload = "private-fixture-payload"
    result = subprocess.run(
        [sys.executable, "-m", "ai_cli.codex_session", *args],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 1
    assert not result.stdout
    assert "[session]" in result.stderr and "Codex identity unknown:" in result.stderr
    assert payload not in result.stderr
    assert "Traceback" not in result.stderr


def test_given_native_event_fixture_when_replayed_then_versioned_identity_matches(collision):
    from ai_cli.codex_session import confirm, prepare

    worktree, _, record = collision
    fixture = json.loads((Path(__file__).parent / "fixtures" / "codex_session_identity_v1.json").read_text())
    rollout = json.loads(record.read_text())["rollout_path"]
    target, config, record_path, token = prepare(
        record, worktree, SLOT, fresh=False, route=fixture["route"], client_version=fixture["client_version"]
    )
    doc = json.loads(record.read_text())
    assert target == THREAD and doc["phase"] == "confirmed"
    handler = tomllib.loads(config)["hooks"]["SessionStart"][0]["hooks"][0]
    assert handler["type"] == "command"
    event = {**fixture["input"], "cwd": str(worktree), "transcript_path": rollout}
    # Execute the actual callback command rendered for Codex, not a mocked confirmer.
    result = subprocess.run(
        handler["command"] if os.name == "nt" else shlex.split(handler["command"]),
        input=json.dumps(event),
        env={**os.environ, "AI_CLI_CODEX_SLOT_RECORD": record_path, "AI_CLI_CODEX_LAUNCH_TOKEN": token},
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
    confirm(record, doc["in_flight"]["token"], event)  # duplicate native callbacks are idempotent
    assert json.loads(record.read_text()) == actual


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_id", "not-an-id"),
        ("cwd", "/different"),
        ("hook_event_name", "Stop"),
        ("transcript_path", {}),
    ],
)
def test_given_incompatible_native_event_when_confirmed_then_committed_identity_is_preserved(collision, field, value):
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
    before = record.read_bytes()
    with pytest.raises(IdentityError):
        confirm(record, doc["in_flight"]["token"], event)
    assert record.read_bytes() == before
    assert json.loads(record.read_text())["phase"] == "confirmed"


def test_given_stale_callback_when_new_launch_is_pending_then_confirmation_is_refused(collision):
    from ai_cli.codex_session import IdentityError, confirm, prepare

    worktree, _, record = collision
    prepare(record, worktree, SLOT, fresh=False, route="local-tmux", client_version="0.169.0")
    before = record.read_bytes()
    with pytest.raises(IdentityError, match="stale"):
        confirm(record, "fixture-token", {})
    assert record.read_bytes() == before


def test_given_ephemeral_native_confirmation_when_writer_is_live_then_collision_is_refused(collision):
    from ai_cli.codex_session import IdentityError, confirm, prepare

    worktree, _, record = collision
    prepare(record, worktree, SLOT, fresh=False, route="local-tmux", client_version="0.169.0")
    doc = json.loads(record.read_text())
    confirm(
        record,
        doc["in_flight"]["token"],
        {"hook_event_name": "SessionStart", "session_id": THREAD, "cwd": str(worktree), "transcript_path": None},
    )
    assert json.loads(record.read_text())["result"] == "live-only"
    with pytest.raises(IdentityError, match="writer"):
        prepare(record, worktree, SLOT, fresh=False, route="local-tmux", client_version="0.169.0")


def test_given_unreadable_client_when_prepared_then_confirmed_identity_is_preserved(collision):
    from ai_cli.codex_session import IdentityError, prepare

    worktree, _, record = collision
    with pytest.raises(IdentityError, match="unreadable"):
        prepare(record, worktree, SLOT, fresh=False, route="paired-host-tmux", client_version="unknown")
    doc = json.loads(record.read_text())
    assert doc["result"] == "confirmed" and doc["phase"] == "confirmed"
    assert doc["client_version"] == "0.169.0" and doc["route"] == "local-tmux"


def test_given_empty_native_store_when_new_slot_starts_then_it_waits_for_client_confirmation(tmp_path, monkeypatch):
    from ai_cli.codex_session import IdentityError, prepare

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    record = tmp_path / "state" / "sessions" / f"{SLOT}.codex.json"
    target, _, _, _ = prepare(record, tmp_path, SLOT, fresh=True, route="local-tmux", client_version="0.169.0")
    doc = json.loads(record.read_text())
    assert target == "new" and doc["thread_id"] is None and doc["phase"] == "unconfirmed"
    with pytest.raises(IdentityError, match="writer"):
        prepare(record, tmp_path, SLOT, fresh=True, route="local-tmux", client_version="0.169.0")


def test_given_custom_native_home_when_thread_probed_then_only_that_store_is_used(collision, monkeypatch):
    from ai_cli.codex_session import prepare

    worktree, _, record = collision
    doc = json.loads(record.read_text())
    root = Path(doc["rollout_path"]).parents[4]
    custom = worktree / "custom-codex"
    root.rename(custom)
    monkeypatch.setenv("CODEX_HOME", str(custom))
    target, _, _, _ = prepare(record, worktree, SLOT, fresh=False, route="local-tmux", client_version="0.169.0")
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


def test_given_different_slots_and_launches_when_prepared_then_declaration_is_byte_identical(tmp_path, monkeypatch):
    from ai_cli.codex_session import prepare

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "native"))
    expected = 'hooks.SessionStart=[{hooks=[{type="command",command="ai internal codex-session-confirm"}]}]'
    tokens = set()
    for name in ("slot-1", "slot-2"):
        record = tmp_path / name / "record.json"
        for launch in range(2):
            _, declaration, record_path, token = prepare(
                record, tmp_path, name, fresh=True, route="local-tmux", client_version="0.169.0"
            )
            assert declaration == expected
            assert declaration.count("hooks.SessionStart") == 1
            assert tomllib.loads(declaration) == {
                "hooks": {
                    "SessionStart": [{"hooks": [{"type": "command", "command": "ai internal codex-session-confirm"}]}]
                }
            }
            assert record_path == str(record.resolve())
            tokens.add(token)
            # Reset only this synthetic record to model a fresh launch on another host.
            record.rename(record.with_suffix(f".previous-{launch}"))
    assert len(tokens) == 4


@pytest.mark.parametrize(
    "problem",
    ["missing-path", "empty-path", "missing-token", "empty-token", "missing-record", "stale", "event", "json"],
)
def test_given_unbound_confirmation_when_internal_action_runs_then_no_record_write_or_private_output(
    collision, problem
):
    from ai_cli.codex_session import prepare

    worktree, _, record = collision
    prepare(record, worktree, SLOT, fresh=False, route="local-tmux", client_version="0.169.0")
    before = record.read_bytes()
    token = json.loads(before)["in_flight"]["token"]
    env = {**os.environ, "AI_CLI_CODEX_SLOT_RECORD": str(record), "AI_CLI_CODEX_LAUNCH_TOKEN": token}
    event = {
        "hook_event_name": "SessionStart",
        "session_id": THREAD,
        "cwd": str(worktree),
        "transcript_path": None,
        "private": "private-fixture-payload",
    }
    if problem == "event":
        event["hook_event_name"] = "Stop"
    payload = json.dumps(event)
    if problem.startswith(("missing-", "empty-")) and problem != "missing-record":
        key = "AI_CLI_CODEX_SLOT_RECORD" if problem.endswith("path") else "AI_CLI_CODEX_LAUNCH_TOKEN"
        if problem.startswith("missing-"):
            env.pop(key)
        else:
            env[key] = ""
    elif problem == "missing-record":
        env["AI_CLI_CODEX_SLOT_RECORD"] = str(record.with_name("absent.json"))
    elif problem == "stale":
        env["AI_CLI_CODEX_LAUNCH_TOKEN"] = "private-stale-token"
    elif problem == "json":
        payload = "private-invalid-json"
    result = subprocess.run(
        [sys.executable, "-m", "ai_cli.main", "internal", "codex-session-confirm"],
        env=env,
        input=payload,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 1
    assert not result.stdout
    assert "[session]" in result.stderr and "Codex identity unknown:" in result.stderr
    assert all(
        value not in result.stderr
        for value in (token, "private-stale-token", "private-fixture-payload", "private-invalid-json", "Traceback")
    )
    assert record.read_bytes() == before
    assert not record.with_name("absent.json").exists()
    assert not record.with_name("absent.json.lock").exists()


def test_given_invalid_event_when_direct_confirmation_runs_then_record_is_unchanged(collision):
    from ai_cli.codex_session import IdentityError, confirm, prepare

    worktree, _, record = collision
    prepare(record, worktree, SLOT, fresh=False, route="local-tmux", client_version="0.169.0")
    before = record.read_bytes()
    with pytest.raises(IdentityError):
        confirm(record, json.loads(before)["in_flight"]["token"], {"hook_event_name": "Stop"})
    assert record.read_bytes() == before


@pytest.mark.parametrize("version", ["0.169.0", "0.171.0", "0.170.0", "0.172.3", "unknown", "", "0.171.0-beta"])
def test_given_client_version_when_prepared_then_any_readable_version_reserves_slot(tmp_path, monkeypatch, version):
    from ai_cli.codex_session import IdentityError, prepare

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "native"))
    record = tmp_path / "slot.json"
    if version in {"unknown", "", "0.171.0-beta"}:
        with pytest.raises(IdentityError, match="client version is unreadable"):
            prepare(record, tmp_path, "test-session", fresh=True, route="local-tmux", client_version=version)
        assert not record.exists()
        assert not record.with_suffix(".json.lock").exists()
    else:
        target, _, _, _ = prepare(
            record, tmp_path, "test-session", fresh=True, route="local-tmux", client_version=version
        )
        assert target == "new"
        doc = json.loads(record.read_bytes())
        assert doc["client_version"] == doc["in_flight"]["client_version"] == version
        assert doc["phase"] == "unconfirmed"


def test_given_pinned_hook_when_contract_requested_then_fixture_and_independent_hash_match():
    result = subprocess.run(
        [sys.executable, "-m", "ai_cli.main", "internal", "codex-hook-contract"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    contract = json.loads(result.stdout)
    fixture = json.loads((Path(__file__).parent / "fixtures" / "codex_hook_contract_v1.json").read_text())
    assert contract == fixture
    assert contract["schema_version"] == 1
    assert contract["supported_client_versions"] == ["0.169.0", "0.171.0", "0.172.0"]
    assert contract["event"] == "SessionStart"
    assert contract["command"] == "ai internal codex-session-confirm"
    assert contract["position"] == {"group": 0, "handler": 0}
    assert contract["keys"] == dict.fromkeys(("linux", "macos"), "/<session-flags>/config.toml:session_start:0:0")
    # Independent normalized identity from the pinned source, including implicit defaults.
    identity = {
        "hooks": [{"type": "command", "timeout": 600, "command": contract["command"], "async": False}],
        "event_name": "session_start",
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert contract["expected_hash"] == "sha256:" + digest
    assert not result.stderr


@pytest.mark.skipif(os.name != "posix", reason="generated launcher uses a POSIX shell")
@pytest.mark.parametrize("shell_name", ["bash", "zsh"])
@pytest.mark.parametrize("first_run", [True, False])
@pytest.mark.parametrize("prompted", [True, False])
def test_given_slot_launch_when_dispatched_then_only_child_inherits_identity_and_one_hook(
    collision, shell_name, first_run, prompted
):
    _, state, record = collision
    result, argv = dispatch(collision, first_run=first_run, prompted=prompted, shell_name=shell_name)
    assert result.returncode == 0, result.stderr
    overrides = [
        argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "-c" and argv[i + 1].startswith("hooks.SessionStart=")
    ]
    assert len(overrides) == 1
    groups = tomllib.loads(overrides[0])["hooks"]["SessionStart"]
    assert len(groups) == 1 and len(groups[0]["hooks"]) == 1
    env = json.loads((state / "environment.json").read_text())
    assert env == {
        "AI_CLI_CODEX_SLOT_RECORD": str(record.resolve()),
        "AI_CLI_CODEX_LAUNCH_TOKEN": json.loads(record.read_text())["in_flight"]["token"],
    }
    assert (state / "parent-env").read_text() == "unset:unset"


@pytest.mark.skipif(os.name != "posix", reason="generated launcher uses a POSIX shell")
@pytest.mark.parametrize("shell_name", ["bash", "zsh"])
@pytest.mark.parametrize("prompted", [True, False])
def test_given_new_slot_when_dispatched_then_one_stable_hook_and_child_identity_are_passed(
    collision, shell_name, prompted
):
    worktree, state, record = collision
    native = Path(json.loads(record.read_text())["rollout_path"]).parents[3]
    native.rename(worktree / "saved-native")
    record.rename(record.with_suffix(".previous"))
    result, argv = dispatch(collision, first_run=True, prompted=prompted, shell_name=shell_name)
    assert result.returncode == 0, result.stderr
    assert argv == [
        "codex",
        "-c",
        'hooks.SessionStart=[{hooks=[{type="command",command="ai internal codex-session-confirm"}]}]',
    ]
    env = json.loads((state / "environment.json").read_text())
    assert env["AI_CLI_CODEX_SLOT_RECORD"] == str(record.resolve())
    assert env["AI_CLI_CODEX_LAUNCH_TOKEN"] == json.loads(record.read_text())["in_flight"]["token"]
    assert (state / "parent-env").read_text() == "unset:unset"
