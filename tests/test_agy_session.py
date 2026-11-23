"""Tests for Antigravity (agy) slot identity, billing guards, and session-store adapter."""

from __future__ import annotations

import io
import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_cli import agy_session, main, session_clients
from ai_cli.session_script import get_engine_script

CONV_1 = "11111111-2222-4333-8444-555555555555"
CONV_2 = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
SLOT = "g-myproject-1"
AI_NAME = "myproject-1"


def _seed_agy_home(
    home: Path,
    *,
    oauth: bool = True,
    settings: dict | None = None,
    db_rows: list[tuple[str, list[str], str]] | None = None,
    cache: dict[str, str] | None = None,
    conv_files: list[str] | None = None,
) -> Path:
    store = home / ".gemini" / "antigravity-cli"
    store.mkdir(parents=True, exist_ok=True)
    if oauth:
        (store / "antigravity-oauth-token").write_text("placeholder", encoding="utf-8")
    if settings is not None:
        (store / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    if db_rows is not None:
        db = store / "conversation_summaries.db"
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                "CREATE TABLE conversation_summaries "
                "(conversation_id TEXT PRIMARY KEY, workspace_uris TEXT NOT NULL, title TEXT NOT NULL DEFAULT '')"
            )
            for conv_id, paths, title in db_rows:
                uris = [Path(p).resolve().as_uri() for p in paths]
                conn.execute(
                    "INSERT INTO conversation_summaries (conversation_id, workspace_uris, title) VALUES (?, ?, ?)",
                    (conv_id, json.dumps(uris), title),
                )
            conn.commit()
        finally:
            conn.close()
    if cache is not None:
        cache_dir = store / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "last_conversations.json").write_text(json.dumps(cache), encoding="utf-8")
    if conv_files:
        conv_dir = store / "conversations"
        conv_dir.mkdir(parents=True, exist_ok=True)
        for conv_id in conv_files:
            (conv_dir / f"{conv_id}.db").write_bytes(b"")
    return store


def _seed_legacy_gemini(home: Path, worktree: Path) -> None:
    gemini_root = home / ".gemini"
    chats = gemini_root / "tmp" / "proj1" / "chats"
    chats.mkdir(parents=True, exist_ok=True)
    (gemini_root / "projects.json").write_text(
        json.dumps({"projects": {str(worktree.resolve()): "proj1"}}), encoding="utf-8"
    )
    (chats / "session-1.json").write_text(json.dumps({"sessionId": CONV_1}), encoding="utf-8")


def _mark_writer_dead(record_path: Path) -> None:
    doc = json.loads(record_path.read_text(encoding="utf-8"))
    doc["in_flight"]["writer"]["pid"] = 999_999_999
    record_path.write_text(json.dumps(doc), encoding="utf-8")


def test_given_missing_oauth_token_when_subscription_refusal_checked_then_refuses_with_signin_step(tmp_path):
    reason = agy_session.subscription_refusal(home=tmp_path)
    assert reason is not None
    assert "antigravity-oauth-token" in reason
    assert "Google sign-in" in reason


@pytest.mark.parametrize("provider", ["gemini", "vertex", "GEMINI", " Vertex "])
def test_given_api_model_provider_when_subscription_refusal_checked_then_refuses_billing(tmp_path, provider):
    _seed_agy_home(tmp_path, oauth=True, settings={"modelProvider": provider})
    reason = agy_session.subscription_refusal(home=tmp_path)
    assert reason is not None
    assert "modelProvider" in reason


def test_given_oauth_token_and_no_api_provider_when_subscription_refusal_checked_then_allows(tmp_path):
    _seed_agy_home(tmp_path, oauth=True, settings={"toolPermission": "always-proceed"})
    assert agy_session.subscription_refusal(home=tmp_path) is None


def test_given_api_key_env_vars_when_strip_api_keys_called_then_removes_all_billed_vars():
    env = {
        "PATH": "/usr/bin",
        "GEMINI_API_KEY": "k1",
        "GOOGLE_API_KEY": "k2",
        "GOOGLE_GENAI_USE_VERTEXAI": "1",
        "AGY_ADC_AUTH": "1",
    }
    agy_session.strip_api_keys(env)
    assert env == {"PATH": "/usr/bin"}


@pytest.mark.parametrize(
    "args",
    [["-p", "hi"], ["--print", "hi"], ["--prompt", "hi"], ["--print=hi"], ["--prompt=hi"]],
)
def test_given_print_flag_without_mode_when_validate_launch_args_called_then_refuses(args):
    with pytest.raises(agy_session.IdentityError, match="--mode"):
        agy_session.validate_launch_args(args)


@pytest.mark.parametrize(
    "args",
    [["-p", "hi", "--mode", "plan"], ["--print", "hi", "--mode=plan"], ["--model", "gemini-4-neon-low"]],
)
def test_given_valid_args_when_validate_launch_args_called_then_allows(args):
    agy_session.validate_launch_args(args)


def test_given_fresh_home_when_ensure_confirm_plugin_called_then_writes_manifest_and_hooks_idempotently(tmp_path):
    first = agy_session.ensure_confirm_plugin(home=tmp_path)
    manifest = json.loads((first / "plugin.json").read_text(encoding="utf-8"))
    hooks = json.loads((first / "hooks.json").read_text(encoding="utf-8"))
    assert manifest == {"name": "ai-cli-slot-confirm", "managed_by": "ai-cli-utils"}
    entry = hooks["ai-cli-slot-confirm"]["SessionStart"][0]
    assert entry["type"] == "command"
    assert "agy-session-confirm" in entry["command"]
    mtime_before = (first / "hooks.json").stat().st_mtime_ns
    second = agy_session.ensure_confirm_plugin(home=tmp_path)
    assert second == first
    assert (first / "hooks.json").stat().st_mtime_ns == mtime_before


def test_given_fresh_slot_when_prepared_and_confirmed_then_subsequent_prepare_resumes_exact_conversation(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    home = tmp_path / "home"
    record = tmp_path / "state" / f"{SLOT}.agy.json"
    _seed_agy_home(home, db_rows=[(CONV_1, [str(worktree)], "slot-1"), (CONV_2, [str(worktree)], "newer")])

    # Fresh slot with existing unowned records in worktree refuses without approval;
    # start with empty store for a truly fresh worktree, then add the two records after confirmation.
    home_fresh = tmp_path / "home-fresh"
    _seed_agy_home(home_fresh)
    target, rec_str, token = agy_session.prepare(
        record, worktree, SLOT, fresh=True, route="local-tmux", client_version="1.5.14", home=home_fresh
    )
    assert target == "new"
    assert Path(rec_str) == record.resolve()

    transcript = home_fresh / ".gemini" / "antigravity-cli" / "brain" / CONV_1 / "transcript.jsonl"
    agy_session.confirm(
        record,
        token,
        {
            "hookEventName": "SessionStart",
            "conversationId": CONV_1,
            "cwd": str(worktree),
            "transcriptPath": str(transcript),
        },
    )
    _seed_agy_home(home_fresh, db_rows=[(CONV_1, [str(worktree)], "slot-1"), (CONV_2, [str(worktree)], "newer")])
    _mark_writer_dead(record)

    resumed_target, _, _ = agy_session.prepare(
        record, worktree, SLOT, fresh=False, route="local-tmux", client_version="1.5.14", home=home_fresh
    )
    assert resumed_target == CONV_1


def test_given_stale_token_when_confirm_called_then_refuses_without_mutating_record(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    home = tmp_path / "home"
    _seed_agy_home(home)
    record = tmp_path / "state" / f"{SLOT}.agy.json"
    agy_session.prepare(record, worktree, SLOT, fresh=True, route="local-tmux", client_version="1.5.14", home=home)
    before = record.read_text(encoding="utf-8")

    with pytest.raises(agy_session.IdentityError, match="stale"):
        agy_session.confirm(
            record,
            "wrong-token",
            {"hookEventName": "SessionStart", "conversationId": CONV_1, "cwd": str(worktree)},
        )
    assert record.read_text(encoding="utf-8") == before


def test_given_mismatched_cwd_when_confirm_called_then_refuses(tmp_path):
    worktree = tmp_path / "wt"
    other = tmp_path / "other"
    worktree.mkdir()
    other.mkdir()
    home = tmp_path / "home"
    _seed_agy_home(home)
    record = tmp_path / "state" / f"{SLOT}.agy.json"
    _, _, token = agy_session.prepare(
        record, worktree, SLOT, fresh=True, route="local-tmux", client_version="1.5.14", home=home
    )
    with pytest.raises(agy_session.IdentityError, match="incompatible"):
        agy_session.confirm(
            record,
            token,
            {"hookEventName": "SessionStart", "conversationId": CONV_1, "cwd": str(other)},
        )


def test_given_manual_agy_without_env_when_confirm_from_environment_called_then_prints_empty_json_and_exits_zero(
    monkeypatch, capsys
):
    monkeypatch.delenv(agy_session.SLOT_RECORD_ENV, raising=False)
    monkeypatch.delenv(agy_session.LAUNCH_TOKEN_ENV, raising=False)
    assert agy_session.confirm_from_environment([]) == 0
    assert capsys.readouterr().out.strip() == "{}"


def test_given_live_writer_when_prepare_called_again_then_refuses_collision(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    home = tmp_path / "home"
    _seed_agy_home(home)
    record = tmp_path / "state" / f"{SLOT}.agy.json"
    agy_session.prepare(record, worktree, SLOT, fresh=True, route="local-tmux", client_version="1.5.14", home=home)
    with pytest.raises(agy_session.IdentityError, match="live"):
        agy_session.prepare(record, worktree, SLOT, fresh=False, route="local-tmux", client_version="1.5.14", home=home)


def test_given_abandoned_unconfirmed_launch_when_prepare_called_on_resume_then_refuses_until_explicit_start_fresh(
    tmp_path,
):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    home = tmp_path / "home"
    _seed_agy_home(home)
    record = tmp_path / "state" / f"{SLOT}.agy.json"
    agy_session.prepare(record, worktree, SLOT, fresh=True, route="local-tmux", client_version="1.5.14", home=home)
    _mark_writer_dead(record)

    with pytest.raises(agy_session.IdentityError, match="refusing silent fresh start"):
        agy_session.prepare(record, worktree, SLOT, fresh=False, route="local-tmux", client_version="1.5.14", home=home)

    target, _, _ = agy_session.prepare(
        record,
        worktree,
        SLOT,
        fresh=False,
        route="local-tmux",
        client_version="1.5.14",
        start_fresh=SLOT,
        home=home,
    )
    assert target == "new"


def test_given_legacy_gemini_records_in_worktree_when_prepare_called_without_approval_then_refuses_wrong_client(
    tmp_path,
):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    home = tmp_path / "home"
    _seed_agy_home(home)
    _seed_legacy_gemini(home, worktree)
    record = tmp_path / "state" / f"{SLOT}.agy.json"

    with pytest.raises(agy_session.IdentityError, match="Legacy Gemini CLI"):
        agy_session.prepare(record, worktree, SLOT, fresh=True, route="local-tmux", client_version="1.5.14", home=home)

    target, _, _ = agy_session.prepare(
        record,
        worktree,
        SLOT,
        fresh=False,
        route="local-tmux",
        client_version="1.5.14",
        start_fresh=SLOT,
        home=home,
    )
    assert target == "new"


def test_given_unowned_agy_records_when_prepare_called_with_adopt_conversation_then_binds_verified_id(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    home = tmp_path / "home"
    _seed_agy_home(home, db_rows=[(CONV_1, [str(worktree)], "existing")])
    record = tmp_path / "state" / f"{SLOT}.agy.json"

    with pytest.raises(agy_session.IdentityError, match="directory-based adoption"):
        agy_session.prepare(record, worktree, SLOT, fresh=True, route="local-tmux", client_version="1.5.14", home=home)

    target, _, _ = agy_session.prepare(
        record,
        worktree,
        SLOT,
        fresh=False,
        route="local-tmux",
        client_version="1.5.14",
        adopt_conversation=(CONV_1, SLOT),
        home=home,
    )
    assert target == CONV_1
    saved = json.loads(record.read_text(encoding="utf-8"))
    assert saved["phase"] == "confirmed"
    assert saved["conversation_id"] == CONV_1


def test_given_confirmed_slot_with_missing_store_evidence_when_prepare_called_then_refuses_guessing(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    home = tmp_path / "home"
    _seed_agy_home(home)
    record = tmp_path / "state" / f"{SLOT}.agy.json"
    _, _, token = agy_session.prepare(
        record, worktree, SLOT, fresh=True, route="local-tmux", client_version="1.5.14", home=home
    )
    agy_session.confirm(
        record, token, {"hookEventName": "SessionStart", "conversationId": CONV_1, "cwd": str(worktree)}
    )
    _mark_writer_dead(record)

    with pytest.raises(agy_session.IdentityError, match="uncorroborated"):
        agy_session.prepare(record, worktree, SLOT, fresh=False, route="local-tmux", client_version="1.5.14", home=home)


def test_given_valid_launch_when_main_execs_then_strips_api_keys_exports_session_env_and_passes_conversation(
    tmp_path, monkeypatch
):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    home = tmp_path / "home"
    _seed_agy_home(home, oauth=True, db_rows=[(CONV_1, [str(worktree)], "slot-1")])
    record = tmp_path / "state" / f"{SLOT}.agy.json"
    agy_session.prepare(
        record,
        worktree,
        SLOT,
        fresh=False,
        route="local-tmux",
        client_version="1.5.14",
        adopt_conversation=(CONV_1, SLOT),
        home=home,
    )
    _mark_writer_dead(record)

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("GEMINI_API_KEY", "secret-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "secret-key-2")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "1")
    monkeypatch.setenv("AGY_ADC_AUTH", "1")
    monkeypatch.delenv("AI_SESSION_NAME", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_TASK_LIST_ID", raising=False)

    captured: dict = {}

    def fake_exec(file: str, args: list[str], **_kwargs) -> None:
        captured["file"] = file
        captured["args"] = list(args)
        captured["env"] = dict(os.environ)
        raise SystemExit(0)

    with (
        patch.object(agy_session, "_detect_version", return_value="1.5.14"),
        patch.object(main, "_exec_handoff", side_effect=fake_exec),
    ):
        with pytest.raises(SystemExit) as exc_info:
            agy_session.main(
                [
                    "launch",
                    str(record),
                    str(worktree),
                    SLOT,
                    AI_NAME,
                    "false",
                    "local-tmux",
                    "--command",
                    "agy",
                    "--sandbox",
                    "--",
                    "--model",
                    "gemini-4-neon-low",
                ]
            )
        assert exc_info.value.code == 0

    assert captured["file"] == "agy"
    assert captured["args"] == ["agy", "--sandbox", "--conversation", CONV_1, "--model", "gemini-4-neon-low"]
    env = captured["env"]
    assert env["AI_SESSION_NAME"] == AI_NAME
    assert env["AI_CLI_SESSION_NAME"] == AI_NAME
    assert env["CLAUDE_CODE_TASK_LIST_ID"] == AI_NAME
    for key in agy_session.API_KEY_VARIABLES:
        assert key not in env
    assert (home / agy_session.PLUGIN_REL_DIR / "hooks.json").is_file()


def test_given_missing_oauth_when_main_launch_called_then_exits_76(tmp_path, monkeypatch):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    record = tmp_path / "state" / f"{SLOT}.agy.json"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    rc = agy_session.main(["launch", str(record), str(worktree), SLOT, AI_NAME, "true", "local-tmux"])
    assert rc == 76


def test_given_print_without_mode_when_main_launch_called_then_exits_76(tmp_path, monkeypatch):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    home = tmp_path / "home"
    _seed_agy_home(home, oauth=True)
    record = tmp_path / "state" / f"{SLOT}.agy.json"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    rc = agy_session.main(
        ["launch", str(record), str(worktree), SLOT, AI_NAME, "true", "local-tmux", "--", "--print", "hello"]
    )
    assert rc == 76


def test_given_summary_db_and_cache_when_antigravity_adapter_reads_then_corroborates_exact_conversation(tmp_path):
    wt_a = tmp_path / "wt-a"
    wt_b = tmp_path / "wt-b"
    wt_a.mkdir()
    wt_b.mkdir()
    home = tmp_path / "home"
    _seed_agy_home(
        home,
        db_rows=[(CONV_1, [str(wt_a)], "first")],
        cache={str(wt_b.resolve()): CONV_2},
        conv_files=[CONV_2],
    )
    adapter = session_clients.Antigravity(home)
    assert adapter.probe_conversation(wt_a, CONV_1).state == session_clients.PRESENT
    assert adapter.probe_conversation(wt_b, CONV_2).state == session_clients.PRESENT
    assert adapter.probe_conversation(wt_b, CONV_1).state == session_clients.UNKNOWN
    assert adapter.probe_transcript(wt_a, "").state == session_clients.PRESENT
    other = tmp_path / "empty"
    other.mkdir()
    assert adapter.probe_transcript(other, "").state == session_clients.UNKNOWN


def test_given_confirm_from_environment_with_stdin_when_called_then_confirms_and_prints_json(
    tmp_path, monkeypatch, capsys
):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    home = tmp_path / "home"
    _seed_agy_home(home)
    record = tmp_path / "state" / f"{SLOT}.agy.json"
    _, rec_str, token = agy_session.prepare(
        record, worktree, SLOT, fresh=True, route="local-tmux", client_version="1.5.14", home=home
    )
    monkeypatch.setenv(agy_session.SLOT_RECORD_ENV, rec_str)
    monkeypatch.setenv(agy_session.LAUNCH_TOKEN_ENV, token)
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"hookEventName": "SessionStart", "conversationId": CONV_1, "cwd": str(worktree)})),
    )

    assert agy_session.confirm_from_environment([]) == 0
    assert capsys.readouterr().out.strip() == "{}"
    saved = json.loads(record.read_text(encoding="utf-8"))
    assert saved["phase"] == "confirmed"
    assert saved["conversation_id"] == CONV_1


def test_given_g_engine_when_get_engine_script_called_then_invokes_agy_session_with_exit_76_guard(tmp_path):
    script = get_engine_script("g", AI_NAME, SLOT, "g-myproject-", "myproject", worktree_dir=str(tmp_path))
    assert "-m ai_cli.agy_session launch" in script
    assert "_run_agy" in script
    assert 'if [[ "${agent_exit_code:-0}" == 76 ]]; then exit 76; fi' in script
    assert "get-latest-gemini-id" not in script


def test_given_g_engine_bare_launch_when_bare_engine_command_built_then_delegates_to_agy_session(tmp_path):
    cmd = main._bare_engine_command(
        "g", AI_NAME, tmp_path, None, "gemini", "--no-sandbox", ["--model", "gemini-4-neon-low"], resume=False
    )
    assert cmd[1:4] == ["-m", "ai_cli.agy_session", "launch"]
    assert "--command" in cmd
    assert "agy" in cmd
    assert "--model" in cmd


@pytest.mark.parametrize(
    "args",
    [
        ["-p", "hi", "--mode", "auto"],
        ["-p", "hi", "--mode=accept-edits"],
        ["-p", "hi", "--mode", "plan", "--mode", "auto"],
        ["-p", "hi", "--mode", "plan", "--disable-slash-commands"],
        ["--print", "hi", "--mode=plan", "--disable-slash-commands=true"],
    ],
)
def test_given_write_mode_or_disabled_slash_commands_in_print_when_validated_then_refuses(args):
    with pytest.raises(agy_session.IdentityError):
        agy_session.validate_launch_args(args)


@pytest.mark.parametrize(
    "args",
    [
        ["-c"],
        ["--continue"],
        ["--conversation", CONV_2],
        [f"--conversation={CONV_2}"],
        ["-r", CONV_2],
        ["--resume", CONV_2],
    ],
)
def test_given_native_resume_override_flag_when_validated_then_refuses(args):
    with pytest.raises(agy_session.IdentityError, match="native resume flag"):
        agy_session.validate_launch_args(args)


def test_given_print_prompt_framing_when_validated_then_refuses_plain_stdin_and_accepts_argv_or_ndjson():
    assert agy_session.validate_print_prompt_and_stdin(["-p", "inspect", "--mode", "plan"], stdin_is_tty=True) is None
    with pytest.raises(agy_session.IdentityError, match="plain-text stdin"):
        agy_session.validate_print_prompt_and_stdin(
            ["-p", "--mode", "plan"], stdin_text="raw plain text", stdin_is_tty=False
        )
    with pytest.raises(agy_session.IdentityError, match="plain-text stdin"):
        agy_session.validate_print_prompt_and_stdin(
            ["-p", "inspect", "--mode", "plan"], stdin_text="extra raw text", stdin_is_tty=False
        )
    ndjson = '{"type": "user", "message": {"role": "user", "content": "hi"}}\n'
    validated = agy_session.validate_print_prompt_and_stdin(
        ["--print", "--mode", "plan", "--input-format", "stream-json"],
        stdin_text=ndjson,
        stdin_is_tty=False,
    )
    assert validated is not None and '"type": "user"' in validated
    with pytest.raises(agy_session.IdentityError, match="valid NDJSON"):
        agy_session.validate_print_prompt_and_stdin(
            ["--print", "--mode", "plan", "--input-format", "stream-json"],
            stdin_text="not json",
            stdin_is_tty=False,
        )


def test_given_print_output_when_classified_then_distinguishes_ok_empty_and_auto_denied():
    assert agy_session.classify_print_output(0, "ok answer\n", "") == 0
    assert agy_session.classify_print_output(0, "   \n", "") == agy_session.EXIT_PRINT_EMPTY
    assert (
        agy_session.classify_print_output(0, json.dumps({"result": ""}), "", output_format="json")
        == agy_session.EXIT_PRINT_EMPTY
    )
    assert (
        agy_session.classify_print_output(
            0, json.dumps({"result": "", "permission_denials": ["write_file"]}), "", output_format="json"
        )
        == agy_session.EXIT_PRINT_AUTO_DENIED
    )
    assert (
        agy_session.classify_print_output(0, "", "tool call denied by policy", output_format="text")
        == agy_session.EXIT_PRINT_AUTO_DENIED
    )
    assert agy_session.classify_print_output(2, "error", "") == 2


def test_given_headless_print_launch_when_main_runs_then_uses_devnull_and_classifies_output(tmp_path, monkeypatch):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    home = tmp_path / "home"
    _seed_agy_home(home, oauth=True)
    record = tmp_path / "state" / f"{SLOT}.agy.json"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return type("Res", (), {"returncode": 0, "stdout": json.dumps({"result": ""}), "stderr": ""})()

    with (
        patch.object(agy_session, "_detect_version", return_value="1.5.14"),
        patch.object(agy_session.subprocess, "run", side_effect=fake_run),
    ):
        rc = agy_session.main(
            [
                "launch",
                str(record),
                str(worktree),
                SLOT,
                AI_NAME,
                "true",
                "bare",
                "--",
                "-p",
                "check status",
                "--mode",
                "plan",
                "--output-format",
                "json",
            ]
        )
    assert rc == agy_session.EXIT_PRINT_EMPTY
    assert seen["kwargs"].get("stdin") == agy_session.subprocess.DEVNULL


def test_given_pinned_slot_when_confirm_reports_different_conversation_then_refuses(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    home = tmp_path / "home"
    _seed_agy_home(home, db_rows=[(CONV_1, [str(worktree)], "pinned")])
    record = tmp_path / "state" / f"{SLOT}.agy.json"
    _, _, token = agy_session.prepare(
        record,
        worktree,
        SLOT,
        fresh=False,
        route="local-tmux",
        client_version="1.5.14",
        adopt_conversation=(CONV_1, SLOT),
        home=home,
    )
    with pytest.raises(agy_session.IdentityError, match="does not match pinned slot"):
        agy_session.confirm(
            record,
            token,
            {"hookEventName": "SessionStart", "conversationId": CONV_2, "cwd": str(worktree)},
        )
    assert json.loads(record.read_text(encoding="utf-8"))["conversation_id"] == CONV_1


def test_given_conversation_owned_by_slot_a_when_slot_b_adopts_or_confirms_same_uuid_then_refuses(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    home = tmp_path / "home"
    _seed_agy_home(home, db_rows=[(CONV_1, [str(worktree)], "shared")])
    state_dir = tmp_path / "state"
    rec_a = state_dir / "g-myproject-1.agy.json"
    rec_b = state_dir / "g-myproject-2.agy.json"

    agy_session.prepare(
        rec_a,
        worktree,
        "g-myproject-1",
        fresh=False,
        route="local-tmux",
        client_version="1.5.14",
        adopt_conversation=(CONV_1, "g-myproject-1"),
        home=home,
    )
    with pytest.raises(agy_session.IdentityError, match="already owned by slot g-myproject-1"):
        agy_session.prepare(
            rec_b,
            worktree,
            "g-myproject-2",
            fresh=False,
            route="local-tmux",
            client_version="1.5.14",
            adopt_conversation=(CONV_1, "g-myproject-2"),
            home=home,
        )

    _, _, token_b = agy_session.prepare(
        rec_b,
        worktree,
        "g-myproject-2",
        fresh=False,
        route="local-tmux",
        client_version="1.5.14",
        start_fresh="g-myproject-2",
        home=home,
    )
    with pytest.raises(agy_session.IdentityError, match="already owned by slot g-myproject-1"):
        agy_session.confirm(
            rec_b,
            token_b,
            {"hookEventName": "SessionStart", "conversationId": CONV_1, "cwd": str(worktree)},
        )


def test_given_api_keys_in_env_when_detect_version_runs_then_strips_credentials(monkeypatch):
    for key in agy_session.API_KEY_VARIABLES:
        monkeypatch.setenv(key, "secret-value")
    captured_env: dict = {}

    def fake_run(argv, **kwargs):
        captured_env.update(kwargs.get("env") or {})
        return type("Res", (), {"returncode": 0, "stdout": "1.5.14\n", "stderr": ""})()

    with patch.object(agy_session.subprocess, "run", side_effect=fake_run):
        assert agy_session._detect_version("agy") == "1.5.14"
    for key in agy_session.API_KEY_VARIABLES:
        assert key not in captured_env


def test_given_unmanaged_plugin_or_slot_refusal_when_ensuring_plugin_then_preserves_files_and_backs_up_owned(
    tmp_path, monkeypatch
):
    foreign_dir = tmp_path / agy_session.PLUGIN_REL_DIR
    foreign_dir.mkdir(parents=True)
    (foreign_dir / "plugin.json").write_text(json.dumps({"name": "custom-user-plugin"}), encoding="utf-8")
    with pytest.raises(agy_session.IdentityError, match="unmanaged"):
        agy_session.ensure_confirm_plugin(home=tmp_path)

    # Owned upgrade backs up differing hooks.json before atomic replace.
    owned_home = tmp_path / "owned"
    plugin_dir = agy_session.ensure_confirm_plugin(home=owned_home)
    (plugin_dir / "hooks.json").write_text('{"custom": true}\n', encoding="utf-8")
    agy_session.ensure_confirm_plugin(home=owned_home)
    assert (plugin_dir / "hooks.json.bak").read_text(encoding="utf-8") == '{"custom": true}\n'

    # Slot refusal happens BEFORE plugin installation writes anything to HOME.
    refusal_home = tmp_path / "refusal"
    worktree = tmp_path / "wt-legacy"
    worktree.mkdir()
    _seed_agy_home(refusal_home, oauth=True)
    _seed_legacy_gemini(refusal_home, worktree)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: refusal_home))
    with patch.object(agy_session, "_detect_version", return_value="1.5.14"):
        rc = agy_session.main(
            ["launch", str(tmp_path / "state" / f"{SLOT}.agy.json"), str(worktree), SLOT, AI_NAME, "true", "local-tmux"]
        )
    assert rc == 76
    assert not (refusal_home / agy_session.PLUGIN_REL_DIR).exists()


def test_given_legacy_gemini_command_override_when_building_agy_launch_then_ignores_gemini_cmd(tmp_path):
    cmd = main._bare_engine_command(
        "g", AI_NAME, tmp_path, None, "/legacy/custom-gemini", "--no-sandbox", [], resume=False
    )
    assert "--command" in cmd
    assert cmd[cmd.index("--command") + 1] == "agy"
    script = get_engine_script(
        "g", AI_NAME, SLOT, "g-myproject-", "myproject", worktree_dir=str(tmp_path), gemini_cmd="/legacy/gemini"
    )
    assert "--command agy" in script
    assert "--command /legacy/gemini" not in script
    resume_script = get_engine_script(
        "g", AI_NAME, SLOT, "g-myproject-", "myproject", worktree_dir=str(tmp_path), resume=True
    )
    assert (
        'ai_cli.agy_session launch "$_ai_state_dir/sessions/$tmux_session.agy.json" "$PWD" "$tmux_session" "$ai_name" false '
        in resume_script
    )


def test_given_legacy_gemini_live_pane_when_ai_g_resumes_or_ls_runs_then_identifies_and_refuses_wrong_client(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(main._config, "get_xdg_state_home", lambda: tmp_path)
    (tmp_path / "session-meta-g-myproject-old.json").write_text(
        json.dumps({"engine": "g", "gemini_cmd": "gemini"}), encoding="utf-8"
    )
    (tmp_path / "sessions").mkdir(parents=True)
    # Seed a stale .agy.json for the legacy pane too: live pane evidence must win over stale files.
    (tmp_path / "sessions" / "g-myproject-stale.agy.json").write_text("{}", encoding="utf-8")
    (tmp_path / "sessions" / "g-myproject-new.agy.json").write_text("{}", encoding="utf-8")

    assert main._g_session_client("g-myproject-old") == "gemini"
    assert main._g_session_client("g-myproject-new") == "agy"

    # 1. Stale .agy.json cannot mask a live legacy gemini pane.
    with patch.object(
        main.subprocess,
        "run",
        return_value=type("R", (), {"returncode": 0, "stdout": "gemini gemini -y\n"})(),
    ):
        assert main._verify_live_agy_tmux_session("g-myproject-stale") is not None

    # 2. Failed tmux pane probe MUST refuse rather than permit attachment.
    with patch.object(
        main.subprocess,
        "run",
        return_value=type("R", (), {"returncode": 1, "stdout": ""})(),
    ):
        assert main._verify_live_agy_tmux_session("g-myproject-new") is not None

    # 3. Live agy pane (or wrapper shell backed by .agy.json) is verified.
    with patch.object(
        main.subprocess,
        "run",
        return_value=type("R", (), {"returncode": 0, "stdout": "agy agy --conversation ...\n"})(),
    ):
        assert main._verify_live_agy_tmux_session("g-myproject-new") is None
    with patch.object(
        main.subprocess,
        "run",
        return_value=type("R", (), {"returncode": 0, "stdout": "zsh -l\n"})(),
    ):
        assert main._verify_live_agy_tmux_session("g-myproject-new") is None

    reporter = main.LaunchReporter(verbose=False)
    monkeypatch.setattr(main._session, "resolve_session", lambda _prefix, _query: "g-myproject-stale")
    with (
        patch.object(
            main.subprocess,
            "run",
            return_value=type("R", (), {"returncode": 0, "stdout": "gemini gemini -y\n"})(),
        ),
        pytest.raises(SystemExit) as exc,
    ):
        main._resolve_resume_target(
            "g",
            "myproject",
            "myproject",
            "stale",
            is_remote=False,
            bare=False,
            repo_root=tmp_path,
            reporter=reporter,
        )
    assert exc.value.code == 1


def test_given_mode_after_double_dash_when_validate_launch_args_called_then_refuses():
    with pytest.raises(agy_session.IdentityError, match="--mode plan"):
        agy_session.validate_launch_args(["-p", "hi", "--", "--mode", "plan"])
    with pytest.raises(agy_session.IdentityError, match="--mode plan"):
        agy_session.validate_launch_args(["--print", "--", "--mode=plan", "hello"])
    # Prompt after `--` with real `--mode plan` before `--` is valid.
    agy_session.validate_launch_args(["-p", "--mode", "plan", "--", "hello"])
    assert (
        agy_session.validate_print_prompt_and_stdin(["-p", "--mode", "plan", "--", "hello"], stdin_is_tty=True) is None
    )


def test_given_print_edge_cases_when_validated_and_classified_then_fails_closed():
    # Two NDJSON user lines must be refused (accept exactly one user line).
    two_lines = (
        '{"type":"user","message":{"role":"user","content":"one"}}\n'
        '{"type":"user","message":{"role":"user","content":"two"}}\n'
    )
    with pytest.raises(agy_session.IdentityError, match="exactly one NDJSON user line"):
        agy_session.validate_print_prompt_and_stdin(
            ["--print", "--mode", "plan", "--input-format", "stream-json"],
            stdin_text=two_lines,
            stdin_is_tty=False,
        )

    # (a) Bare status SUCCESS with no assistant response returns EXIT_PRINT_EMPTY.
    assert (
        agy_session.classify_print_output(0, json.dumps({"status": "SUCCESS"}), "", output_format="json")
        == agy_session.EXIT_PRINT_EMPTY
    )
    assert (
        agy_session.classify_print_output(
            0, json.dumps({"type": "result", "status": "SUCCESS"}), "", output_format="text"
        )
        == agy_session.EXIT_PRINT_EMPTY
    )

    # (b) Answer text PLUS stderr auto-denial notice returns EXIT_PRINT_AUTO_DENIED.
    assert (
        agy_session.classify_print_output(
            0, "partial answer\n", "tool call auto-denied by policy", output_format="text"
        )
        == agy_session.EXIT_PRINT_AUTO_DENIED
    )
    assert (
        agy_session.classify_print_output(
            0, json.dumps({"result": "partial answer"}), "permission denied for edit_file", output_format="json"
        )
        == agy_session.EXIT_PRINT_AUTO_DENIED
    )

    # (c) Streamed user echo + bare result SUCCESS without assistant text returns EXIT_PRINT_EMPTY.
    user_echo_stream = (
        '{"type":"user","message":{"role":"user","content":"echoed prompt"}}\n{"type":"result","status":"SUCCESS"}\n'
    )
    assert (
        agy_session.classify_print_output(0, user_echo_stream, "", output_format="stream-json")
        == agy_session.EXIT_PRINT_EMPTY
    )


def test_given_live_only_record_without_proof_or_fallback_when_prepare_called_then_refuses(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    home = tmp_path / "home"
    _seed_agy_home(home)
    record = tmp_path / "state" / f"{SLOT}.agy.json"
    _, _, token = agy_session.prepare(
        record, worktree, SLOT, fresh=True, route="local-tmux", client_version="1.5.14", home=home
    )
    agy_session.confirm(
        record, token, {"hookEventName": "SessionStart", "conversationId": CONV_1, "cwd": str(worktree)}
    )
    doc = json.loads(record.read_text(encoding="utf-8"))
    doc["result"] = "live-only"
    doc["resumable"] = None
    doc["in_flight"]["writer"]["pid"] = 999_999_999
    record.write_text(json.dumps(doc), encoding="utf-8")

    with pytest.raises(agy_session.IdentityError, match="uncorroborated"):
        agy_session.prepare(record, worktree, SLOT, fresh=False, route="local-tmux", client_version="1.5.14", home=home)


def test_given_mixed_case_uuid_when_second_slot_adopts_then_case_insensitive_lock_refuses(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    home = tmp_path / "home"
    _seed_agy_home(home, db_rows=[(CONV_1.lower(), [str(worktree)], "owned")])
    state_dir = tmp_path / "state"
    agy_session.prepare(
        state_dir / "g-myproject-1.agy.json",
        worktree,
        "g-myproject-1",
        fresh=False,
        route="local-tmux",
        client_version="1.5.14",
        adopt_conversation=(CONV_1.lower(), "g-myproject-1"),
        home=home,
    )
    with pytest.raises(agy_session.IdentityError, match="already owned by slot g-myproject-1"):
        agy_session.prepare(
            state_dir / "g-myproject-2.agy.json",
            worktree,
            "g-myproject-2",
            fresh=False,
            route="local-tmux",
            client_version="1.5.14",
            adopt_conversation=(CONV_1.upper(), "g-myproject-2"),
            home=home,
        )


def test_given_repeated_plugin_updates_when_ensuring_plugin_then_preserves_first_backup_and_numbers_next(tmp_path):
    plugin_dir = agy_session.ensure_confirm_plugin(home=tmp_path)
    (plugin_dir / "hooks.json").write_text('{"v": 1}\n', encoding="utf-8")
    agy_session.ensure_confirm_plugin(home=tmp_path)
    assert (plugin_dir / "hooks.json.bak").read_text(encoding="utf-8") == '{"v": 1}\n'

    (plugin_dir / "hooks.json").write_text('{"v": 2}\n', encoding="utf-8")
    agy_session.ensure_confirm_plugin(home=tmp_path)
    assert (plugin_dir / "hooks.json.bak").read_text(encoding="utf-8") == '{"v": 1}\n'
    assert (plugin_dir / "hooks.json.bak.1").read_text(encoding="utf-8") == '{"v": 2}\n'


def test_given_two_live_agy_slots_outside_tmux_when_resolve_session_called_without_name_then_refuses_ambiguous(
    monkeypatch,
):
    from ai_cli import session as _session

    with (
        patch.object(
            _session.subprocess,
            "run",
            return_value=type("R", (), {"returncode": 1, "stdout": ""})(),
        ),
        patch.object(_session, "_tmux_session_names", return_value=["g-myproject-1", "g-myproject-2"]),
    ):
        with pytest.raises(_session.SessionSlotAmbiguityError, match="ambiguous Antigravity resume"):
            _session.resolve_session("g-myproject-", "")


def test_given_print_args_when_get_engine_script_called_then_exits_without_entering_restart_loop(tmp_path):
    script = get_engine_script(
        "g",
        AI_NAME,
        SLOT,
        "g-myproject-",
        "myproject",
        worktree_dir=str(tmp_path),
        agy_args=["-p", "inspect repo", "--mode", "plan"],
    )
    start = script.index("# BEGIN AGY THREAD")
    end = script.index("# END AGY THREAD", start)
    agy_block = script[start:end]
    assert 'exit "${agent_exit_code:-0}"' in agy_block


def test_given_bare_headless_print_launch_with_pty_transport_when_dispatched_then_bypasses_pty_for_piped_ndjson(
    tmp_path, monkeypatch
):
    from ai_cli import compact_transport as _ct

    home = tmp_path / "home"
    worktree = tmp_path / "wt"
    worktree.mkdir()
    _seed_agy_home(home, oauth=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("AI_CLI_SKIP_DIRENV", "1")
    monkeypatch.chdir(worktree)

    pty_called = []
    exec_handoff_args = []

    def fake_pty_handoff(*args, **kwargs):
        pty_called.append((args, kwargs))
        raise AssertionError("bare headless print must not wrap in PTY")

    def fake_exec_with_direnv(_root, command, *, handoff=None):
        exec_handoff_args.append((command, handoff))
        raise SystemExit(0)

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr(
        main._config,
        "load_config",
        lambda *_a, **_kw: {
            "agy": {"command": "agy"},
            "projects": {"myproject": {"path": str(worktree), "prefix": "myproject"}},
        },
    )
    monkeypatch.setattr(main.shutil, "which", lambda name, *a, **kw: "/usr/bin/agy" if name == "agy" else None)
    monkeypatch.setattr(main, "_pty_handoff", fake_pty_handoff)
    monkeypatch.setattr(main, "_exec_with_direnv", fake_exec_with_direnv)
    monkeypatch.setattr(
        main._compact_transport,
        "resolve",
        lambda **_kw: _ct.CompactTransport(kind="pty", tmux_detail="bare", pty_detail="pty-test"),
    )
    monkeypatch.setattr(
        main,
        "_resolve_session_identity",
        lambda *args, **kwargs: (SLOT, AI_NAME),
    )
    monkeypatch.setattr(main._session, "is_current_project_resolved", lambda: True)
    monkeypatch.setattr(main._session, "get_project_prefix", lambda *_a, **_kw: "myproject")
    monkeypatch.setattr(main._session_launch_state, "handoff", lambda _sid, h: h)

    from click.testing import CliRunner

    result = CliRunner().invoke(
        main._cli_group,
        ["g", "1", "-b", "--no-worktree", "--", "-p", "--mode", "plan", "--input-format", "stream-json"],
    )
    assert result.exit_code == 0, result.output
    assert not pty_called
    assert exec_handoff_args
    assert exec_handoff_args[0][1] is main._exec_handoff
