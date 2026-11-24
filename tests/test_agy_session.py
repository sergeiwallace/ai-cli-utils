"""Tests for Antigravity (agy) slot identity, billing guards, and session-store adapter."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
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


def test_given_approved_source_workspace_when_slot_is_adopted_then_it_resumes_without_rewriting_native_store(tmp_path):
    home = tmp_path / "home"
    source = tmp_path / "original"
    worktree = tmp_path / "worktree"
    record = tmp_path / "state" / f"{SLOT}.agy.json"
    store = _seed_agy_home(home, db_rows=[(CONV_1, [str(source)], "manual session")], conv_files=[CONV_1])
    db = store / "conversation_summaries.db"
    original = db.read_bytes()

    def prepare(**options):
        return agy_session.prepare(
            record, worktree, SLOT, fresh=False, route="local-tmux", client_version="1.5.14", home=home, **options
        )

    with pytest.raises(agy_session.IdentityError, match="working directory"):
        prepare(adopt_conversation=(CONV_1, SLOT), adopted_from=tmp_path / "unrelated")
    assert not record.exists()
    assert prepare(adopt_conversation=(CONV_1, SLOT), adopted_from=source)[0] == CONV_1
    _mark_writer_dead(record)
    assert prepare()[0] == CONV_1
    assert db.read_bytes() == original


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
        "g", AI_NAME, tmp_path, None, "--no-sandbox", ["--model", "gemini-4-neon-low"], resume=False
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


def test_given_agy_resume_when_script_is_generated_then_passes_false_for_fresh(tmp_path):
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
    # Authoritative @ai_cli_client marker governs live attach verification (even if a stale .agy.json exists).
    (tmp_path / "sessions" / "g-myproject-stale.agy.json").write_text("{}", encoding="utf-8")
    (tmp_path / "sessions" / "g-myproject-new.agy.json").write_text("{}", encoding="utf-8")

    assert main._g_session_client("g-myproject-old") == "gemini"
    assert main._g_session_client("g-myproject-new") == "agy"

    # 1. Marker naming another client (e.g. gemini) MUST refuse even if a stale .agy.json exists.
    with patch.object(
        main.subprocess,
        "run",
        return_value=type("R", (), {"returncode": 0, "stdout": "gemini\n"})(),
    ):
        refusal_gemini = main._verify_live_agy_tmux_session("g-myproject-stale")
    assert refusal_gemini is not None and "gemini" in refusal_gemini and "Relaunch with `ai g`" in refusal_gemini

    # 2. Missing marker / failed tmux show-options MUST refuse (pre-marker sessions must relaunch).
    with patch.object(
        main.subprocess,
        "run",
        return_value=type("R", (), {"returncode": 1, "stdout": ""})(),
    ):
        refusal_missing = main._verify_live_agy_tmux_session("g-myproject-new")
    assert refusal_missing is not None and "Relaunch with `ai g`" in refusal_missing

    # 3. Authoritative @ai_cli_client=agy marker verifies exact literal 'agy' only (never AGY, padded, or CR on POSIX).
    with patch.object(
        main.subprocess,
        "run",
        return_value=type("R", (), {"returncode": 0, "stdout": b"agy\n"})(),
    ):
        assert main._verify_live_agy_tmux_session("g-myproject-new") is None
    bad_markers = [b"AGY\n", b" agy \n", b"agy \n", b" agy\n"]
    if sys.platform != "win32":
        bad_markers.append(b"agy\r\n")
    for bad_marker in bad_markers:
        with patch.object(
            main.subprocess,
            "run",
            return_value=type("R", (), {"returncode": 0, "stdout": bad_marker})(),
        ):
            bad_refusal = main._verify_live_agy_tmux_session("g-myproject-new")
        assert bad_refusal is not None and "Relaunch with `ai g`" in bad_refusal

    reporter = main.LaunchReporter(verbose=False)
    monkeypatch.setattr(main._session, "resolve_session", lambda _prefix, _query: "g-myproject-stale")
    with (
        patch.object(
            main.subprocess,
            "run",
            return_value=type("R", (), {"returncode": 0, "stdout": "gemini\n"})(),
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


def test_given_absent_token_file_and_signed_in_agy_on_darwin_when_subscription_refusal_checked_then_allows(
    tmp_path, monkeypatch
):
    for key in agy_session.API_KEY_VARIABLES:
        monkeypatch.setenv(key, "must-be-stripped")
    seen_env: dict = {}

    def fake_run(argv, **kwargs):
        assert argv == ["agy", "models"]
        assert kwargs.get("stdin") == agy_session.subprocess.DEVNULL
        seen_env.update(kwargs.get("env") or {})
        return type("Res", (), {"returncode": 0, "stdout": "gemini-4-neon-high\n", "stderr": ""})()

    with patch.object(agy_session.subprocess, "run", side_effect=fake_run):
        assert agy_session.subscription_refusal(home=tmp_path, executable="agy", platform="darwin") is None
    for key in agy_session.API_KEY_VARIABLES:
        assert key not in seen_env

    # Custom API modelProvider is STILL refused even when agy models exits 0.
    _seed_agy_home(tmp_path, oauth=False, settings={"modelProvider": "gemini"})
    with patch.object(agy_session.subprocess, "run", side_effect=fake_run):
        refusal = agy_session.subscription_refusal(home=tmp_path, executable="agy", platform="darwin")
    assert refusal is not None and "modelProvider" in refusal


def test_given_absent_token_file_and_signed_out_or_offline_agy_when_checked_then_refuses_signed_out_and_handles_os(
    tmp_path,
):
    signed_out = type("Res", (), {"returncode": 1, "stdout": "", "stderr": "Error: Please sign in to view models."})()
    with patch.object(agy_session.subprocess, "run", return_value=signed_out):
        refusal_mac = agy_session.subscription_refusal(home=tmp_path, executable="agy", platform="darwin")
        refusal_lnx = agy_session.subscription_refusal(home=tmp_path, executable="agy", platform="linux")
    assert refusal_mac is not None and "Google sign-in" in refusal_mac
    assert refusal_lnx is not None and "Google sign-in" in refusal_lnx

    # Offline / non-auth probe error: Linux still refuses missing token file; macOS warns and allows.
    offline = type("Res", (), {"returncode": 1, "stdout": "", "stderr": "connection refused"})()
    with patch.object(agy_session.subprocess, "run", return_value=offline):
        assert agy_session.subscription_refusal(home=tmp_path, executable="agy", platform="darwin") is None
        refusal_lnx_offline = agy_session.subscription_refusal(home=tmp_path, executable="agy", platform="linux")
    assert refusal_lnx_offline is not None and "antigravity-oauth-token" in refusal_lnx_offline


def test_given_two_live_agy_slots_outside_tmux_even_if_display_message_answers_then_refuses_ambiguous(monkeypatch):
    from ai_cli import session as _session

    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    with (
        # Simulate tmux display-message succeeding with an implicit target outside $TMUX.
        patch.object(
            _session.subprocess,
            "run",
            return_value=type("R", (), {"returncode": 0, "stdout": "g-myproject-1\n"})(),
        ),
        patch.object(_session, "_tmux_session_names", return_value=["g-myproject-1", "g-myproject-2"]),
    ):
        with pytest.raises(_session.SessionSlotAmbiguityError, match="ambiguous Antigravity resume"):
            _session.resolve_session("g-myproject-", "")

    # Shared label matching two slots must also refuse rather than fall back to recency.
    with (
        patch.object(_session.subprocess, "run", return_value=type("R", (), {"returncode": 1, "stdout": ""})()),
        patch.object(_session, "_tmux_session_names", return_value=["g-myproject-foo-1", "g-myproject-foo-2"]),
    ):
        with pytest.raises(_session.SessionSlotAmbiguityError, match="ambiguous Antigravity resume"):
            _session.resolve_session("g-myproject-", "foo")


@pytest.mark.parametrize(
    ("extra_flags", "stdout_text", "stderr_text", "expected_rc"),
    [
        (["-p", "hi", "--mode", "plan"], "SUCCESS\n", "", agy_session.EXIT_PRINT_EMPTY),
        (
            ["-p", "hi", "--mode", "plan", "--output-format", "stream-json"],
            '{"type":"tool_result","content":"ok"}\n{"type":"result","status":"SUCCESS"}\n',
            "",
            agy_session.EXIT_PRINT_EMPTY,
        ),
        (
            ["-p", "hi", "--mode", "plan"],
            "The file returned permission denied when inspected.\n",
            "",
            0,
        ),
        (
            ["-p", "hi", "--mode", "plan"],
            "The example shows a tool call denied by policy.\n",
            "",
            0,
        ),
        (
            ["-p", "hi", "--mode", "plan"],
            'Quoted: "tool call auto-denied by policy"\n',
            "",
            0,
        ),
        (
            ["-p", "hi", "--mode", "plan"],
            "Example:\n```\nTool call denied by policy\n```\n",
            "",
            0,
        ),
        (
            ["-p", "hi", "--mode", "plan"],
            "````markdown\n```\nTool call denied by policy\n```\n````\n",
            "",
            0,
        ),
        (
            ["-p", "hi", "--mode", "plan"],
            "> Tool call auto-denied by policy\n",
            "",
            0,
        ),
        (
            ["-p", "hi", "--mode", "plan"],
            "    ```\ntool call auto-denied by policy\n",
            "",
            agy_session.EXIT_PRINT_AUTO_DENIED,
        ),
        (
            ["-p", "hi", "--mode", "plan"],
            "    ```\n    tool call auto-denied by policy\n",
            "",
            0,
        ),
        (
            ["-p", "hi", "--mode", "plan"],
            "Example:\n\n    ```\n    tool call auto-denied by policy\n",
            "",
            0,
        ),
        (
            ["-p", "hi", "--mode", "plan"],
            "Example:\n\n    first line\n\n    tool call auto-denied by policy\n",
            "",
            0,
        ),
        (
            ["-p", "hi", "--mode", "plan"],
            "\t```\n\ttool call auto-denied by policy\n",
            "",
            0,
        ),
        (
            ["-p", "hi", "--mode", "plan"],
            "  \ttool call auto-denied by policy\n",
            "",
            0,
        ),
        (
            ["-p", "hi", "--mode", "plan"],
            "   tool call auto-denied by policy\n",
            "",
            agy_session.EXIT_PRINT_AUTO_DENIED,
        ),
        (
            ["-p", "hi", "--mode", "plan"],
            "Run failed:\n    tool call auto-denied by policy\n",
            "",
            agy_session.EXIT_PRINT_AUTO_DENIED,
        ),
        (
            ["-p", "hi", "--mode", "plan"],
            "    ```\n    code\n    ```\ntool call auto-denied by policy\n",
            "",
            agy_session.EXIT_PRINT_AUTO_DENIED,
        ),
        (
            ["-p", "hi", "--mode", "plan"],
            "    indented header\n```\ntool call auto-denied by policy\n```\n",
            "",
            0,
        ),
        (
            ["-p", "hi", "--mode", "plan"],
            "```\nhello\n```\ntool call auto-denied by policy\n",
            "",
            agy_session.EXIT_PRINT_AUTO_DENIED,
        ),
        (
            ["-p", "hi", "--mode", "plan"],
            "Partial answer\n",
            "tool call auto-denied by policy\n",
            agy_session.EXIT_PRINT_AUTO_DENIED,
        ),
        (
            ["-p", "hi", "--mode", "plan"],
            "tool call auto-denied by policy\n",
            "",
            agy_session.EXIT_PRINT_AUTO_DENIED,
        ),
        (
            ["-p", "hi", "--mode", "plan"],
            "Error: tool call auto-denied by policy\n",
            "",
            agy_session.EXIT_PRINT_AUTO_DENIED,
        ),
    ],
)
def test_given_real_wrapper_print_cases_when_main_runs_then_classifies_by_event_not_prose(
    tmp_path, monkeypatch, extra_flags, stdout_text, stderr_text, expected_rc
):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    home = tmp_path / "home"
    _seed_agy_home(home, oauth=True)
    record = tmp_path / "state" / f"{SLOT}.agy.json"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    def fake_run(argv, **kwargs):
        return type("Res", (), {"returncode": 0, "stdout": stdout_text, "stderr": stderr_text})()

    with (
        patch.object(agy_session, "_detect_version", return_value="1.5.14"),
        patch.object(agy_session.subprocess, "run", side_effect=fake_run),
    ):
        rc = agy_session.main(["launch", str(record), str(worktree), SLOT, AI_NAME, "true", "bare", "--", *extra_flags])
    assert rc == expected_rc


def test_given_remote_agy_launch_when_dispatched_then_validates_locally_and_forwards_extra_args(tmp_path, monkeypatch):
    from click.testing import CliRunner

    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("AI_CLI_SKIP_DIRENV", "1")
    monkeypatch.chdir(worktree)
    monkeypatch.setattr(main._session, "is_current_project_resolved", lambda: True)
    monkeypatch.setattr(main._session, "get_project_prefix", lambda *_a, **_kw: "myproject")
    monkeypatch.setattr(
        main._config,
        "load_config",
        lambda *_a, **_kw: {
            "agy": {"command": "agy"},
            "remote": {"host": "192.0.2.10", "user": "user", "port": 22, "transport": "mosh"},
            "projects": {"myproject": {"path": str(worktree), "prefix": "myproject"}},
        },
    )

    # 1. Invalid headless print flag on remote dispatch is refused locally BEFORE any SSH call.
    bad = CliRunner().invoke(main._cli_group, ["g", "1", "-R", "--", "--print", "inspect"])
    assert bad.exit_code == 1
    assert "--mode plan" in bad.output

    # 2. Valid headless print flags are forwarded after `--` in the remote command.
    captured_ssh: list[list[str]] = []

    async def fake_transport_loop(ssh_args, *_a, **_kw):
        captured_ssh.append(list(ssh_args))
        return 0

    monkeypatch.setattr(main, "_resolve_remote_shell", lambda *_a, **_kw: "/bin/bash")
    monkeypatch.setattr(main, "_update_remote_ai_cli", lambda *_a, **_kw: (True, "up to date"))
    monkeypatch.setattr(
        main, "_request_remote_session_allocation", lambda *_a, **_kw: ("g-r-myproject-1", "myproject-1")
    )
    monkeypatch.setattr(main._iterm2, "_assign_iterm2_color_slot", lambda *_a, **_kw: None)
    monkeypatch.setattr(main._iterm2, "_emit_iterm2_profile_setup", lambda *_a, **_kw: None)
    monkeypatch.setattr(main, "_track_launch", lambda *_a, **_kw: None)
    monkeypatch.setattr(main._transport, "_ensure_vpn_watcher", lambda *_a, **_kw: None)
    monkeypatch.setattr(main._transport, "_maybe_stop_vpn_watcher", lambda *_a, **_kw: None)
    monkeypatch.setattr(main._transport, "_run_transport_loop", fake_transport_loop)
    monkeypatch.setattr(main.shutil, "which", lambda name, *a, **kw: "/usr/bin/" + name)

    good = CliRunner().invoke(main._cli_group, ["g", "1", "-R", "--", "--print", "--mode", "plan", "inspect"])
    assert good.exit_code == 0, good.output
    assert captured_ssh
    remote_payload = captured_ssh[0][-1]
    assert "-- --print --mode plan inspect" in remote_payload


def test_given_headless_print_without_bare_and_use_pty_false_when_dispatched_then_runs_sync_without_pty_or_tmux(
    tmp_path, monkeypatch
):
    from click.testing import CliRunner

    home = tmp_path / "home"
    worktree = tmp_path / "wt"
    worktree.mkdir()
    _seed_agy_home(home, oauth=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("AI_CLI_SKIP_DIRENV", "1")
    monkeypatch.chdir(worktree)

    handed_off: list[tuple[list[str], object]] = []

    def fake_exec_with_direnv(_root, command, *, handoff=None):
        handed_off.append((command, handoff))
        raise SystemExit(0)

    monkeypatch.setattr(
        main._config,
        "load_config",
        lambda *_a, **_kw: {
            "agy": {"command": "agy"},
            "session": {"use_pty": False, "use_tmux": True},
            "projects": {"myproject": {"path": str(worktree), "prefix": "myproject"}},
        },
    )
    monkeypatch.setattr(main.shutil, "which", lambda name, *a, **kw: "/usr/bin/agy" if name == "agy" else None)
    monkeypatch.setattr(main, "_exec_with_direnv", fake_exec_with_direnv)
    monkeypatch.setattr(main, "_resolve_session_identity", lambda *a, **kw: (SLOT, AI_NAME))
    monkeypatch.setattr(main._session, "is_current_project_resolved", lambda: True)
    monkeypatch.setattr(main._session, "get_project_prefix", lambda *_a, **_kw: "myproject")
    monkeypatch.setattr(main._session_launch_state, "handoff", lambda _sid, h: h)

    # Without -b and with use_pty=false, headless print still routes synchronously (never detached tmux / PTY refusal).
    result = CliRunner().invoke(
        main._cli_group,
        ["g", "1", "--no-worktree", "--", "-p", "--mode", "plan", "--input-format", "stream-json"],
    )
    assert result.exit_code == 0, result.output
    assert handed_off
    assert handed_off[0][1] is main._exec_handoff


def test_given_no_worktree_shared_dir_when_slot2_starts_and_stopped_slot1_resumes_then_both_succeed(
    tmp_path, monkeypatch
):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    home = tmp_path / "home"
    state_dir = tmp_path / "state"
    sessions_dir = state_dir / "sessions"
    _seed_agy_home(home, oauth=True)
    monkeypatch.setattr(main._config, "get_xdg_state_home", lambda: state_dir)

    rec1 = sessions_dir / "g-myproject-1.agy.json"
    _, _, tok1 = agy_session.prepare(
        rec1, repo_root, "g-myproject-1", fresh=True, route="local-tmux", client_version="1.5.14", home=home
    )
    agy_session.confirm(rec1, tok1, {"hookEventName": "SessionStart", "conversationId": CONV_1, "cwd": str(repo_root)})
    _seed_agy_home(home, oauth=True, db_rows=[(CONV_1, [str(repo_root)], "slot-1")])

    # Slot 2 in the SAME -W directory is NOT blocked by slot 1's already-owned conversation.
    rec2 = sessions_dir / "g-myproject-2.agy.json"
    target2, _, _ = agy_session.prepare(
        rec2, repo_root, "g-myproject-2", fresh=True, route="local-tmux", client_version="1.5.14", home=home
    )
    assert target2 == "new"

    # Stopped -W slot 1 has no worktree directory, yet valid .agy.json binding proves resumable evidence.
    assert main._session_evidence("g", repo_root, "myproject-1") == "myproject-1"


def test_given_g_session_attach_when_do_attach_runs_then_uses_exact_tmux_target(tmp_path, monkeypatch):
    from click.testing import CliRunner

    monkeypatch.setattr(main._config, "get_xdg_state_home", lambda: tmp_path)
    (tmp_path / "sessions").mkdir(parents=True)
    (tmp_path / "sessions" / "g-myproject-1.agy.json").write_text("{}", encoding="utf-8")

    has_calls: list[list[str]] = []
    attach_calls: list[list[str]] = []

    def fake_has(argv, **kwargs):
        has_calls.append(list(argv))
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(main.subprocess, "run", fake_has)
    monkeypatch.setattr(main, "_resume_suspended_panes", lambda _name: None)
    monkeypatch.setattr(main, "_exec_tmux", lambda argv: (attach_calls.append(list(argv)), sys.exit(0)))

    res = CliRunner().invoke(main._cli_group, ["attach", "g-myproject-1"])
    assert res.exit_code == 0, res.output
    assert has_calls and has_calls[0][-1] == "=g-myproject-1"
    assert attach_calls and attach_calls[0][-1] == "=g-myproject-1"
    assert "[agy]" in res.output


@pytest.mark.parametrize("exit_code", [agy_session.EXIT_PRINT_EMPTY, agy_session.EXIT_PRINT_AUTO_DENIED])
def test_given_remote_print_terminal_exit_77_or_78_when_ssh_reconnect_runs_then_stops_without_retry(
    tmp_path, monkeypatch, exit_code
):
    from ai_cli import transport

    ssh_calls: list[list[str]] = []

    def fake_call(argv, **kwargs):
        ssh_calls.append(list(argv))
        return exit_code

    monkeypatch.setattr(transport.subprocess, "call", fake_call)
    monkeypatch.setattr(
        transport.subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    )
    monkeypatch.setattr(transport.time, "sleep", lambda _s: (_ for _ in ()).throw(AssertionError("must not retry")))

    rc = transport.run_ssh_with_reconnect(
        ["ssh", "user@192.0.2.10", "ai g -- -p --mode plan hi"],
        ["true"],
        max_attempts=5,
        backoff_seconds=1.0,
        outcome_path=tmp_path / "outcome.json",
        terminal_exit_codes=(agy_session.EXIT_PRINT_EMPTY, transport.REMOTE_LAUNCH_REFUSED_EXIT),
    )
    assert rc == exit_code
    assert len(ssh_calls) == 1


def _configure_remote_ssh_dispatch(tmp_path, monkeypatch, engine: str, session_id: str, ai_name: str):
    worktree = tmp_path / f"wt-{engine}"
    worktree.mkdir(exist_ok=True)
    monkeypatch.setattr(main.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("AI_CLI_SKIP_DIRENV", "1")
    monkeypatch.chdir(worktree)
    monkeypatch.setattr(main._session, "is_current_project_resolved", lambda: True)
    monkeypatch.setattr(main._session, "get_project_prefix", lambda *_a, **_kw: "myproject")
    monkeypatch.setattr(
        main._config,
        "load_config",
        lambda *_a, **_kw: {
            "agy": {"command": "agy"},
            "remote": {
                "host": "192.0.2.10",
                "user": "user",
                "port": 22,
                "transport": "ssh",
                "reconnect_attempts": 3,
                "reconnect_backoff": 0.01,
            },
            "projects": {"myproject": {"path": str(worktree), "prefix": "myproject"}},
        },
    )
    monkeypatch.setattr(main, "_resolve_remote_shell", lambda *_a, **_kw: "/bin/bash")
    monkeypatch.setattr(main, "_update_remote_ai_cli", lambda *_a, **_kw: (True, "up to date"))
    monkeypatch.setattr(main, "_request_remote_session_allocation", lambda *_a, **_kw: (session_id, ai_name))
    monkeypatch.setattr(main._iterm2, "_assign_iterm2_color_slot", lambda *_a, **_kw: None)
    monkeypatch.setattr(main._iterm2, "_emit_iterm2_profile_setup", lambda *_a, **_kw: None)
    monkeypatch.setattr(main, "_track_launch", lambda *_a, **_kw: None)
    monkeypatch.setattr(main.shutil, "which", lambda name, *a, **kw: "/usr/bin/" + name)


def test_given_engine_g_remote_ssh_exit_77_when_dispatched_then_stops_without_reconnect(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from ai_cli import transport

    _configure_remote_ssh_dispatch(tmp_path, monkeypatch, "g", "g-r-myproject-1", "myproject-1")
    ssh_calls: list[list[str]] = []

    def fake_call(argv, **kwargs):
        ssh_calls.append(list(argv))
        return agy_session.EXIT_PRINT_EMPTY

    monkeypatch.setattr(transport.subprocess, "call", fake_call)
    monkeypatch.setattr(
        transport.subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    )
    monkeypatch.setattr(transport.time, "sleep", lambda _s: (_ for _ in ()).throw(AssertionError("must not retry")))

    res = CliRunner().invoke(main._cli_group, ["g", "1", "-R", "--", "-p", "--mode", "plan", "hi"])
    assert res.exit_code == agy_session.EXIT_PRINT_EMPTY
    assert len(ssh_calls) == 1


def test_given_non_g_remote_ssh_exit_77_when_dispatched_then_reconnects_as_on_main(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from ai_cli import transport

    _configure_remote_ssh_dispatch(tmp_path, monkeypatch, "c", "c-r-myproject-1", "myproject-1")
    returns = iter([agy_session.EXIT_PRINT_EMPTY, 0])
    ssh_calls: list[list[str]] = []
    sleeps: list[float] = []

    def fake_call(argv, **kwargs):
        ssh_calls.append(list(argv))
        return next(returns)

    monkeypatch.setattr(transport.subprocess, "call", fake_call)
    monkeypatch.setattr(
        transport.subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    )
    monkeypatch.setattr(transport.time, "sleep", lambda s: sleeps.append(s))

    res = CliRunner().invoke(main._cli_group, ["c", "1", "-R"])
    assert res.exit_code == 0, res.output
    assert len(ssh_calls) == 2
    assert len(sleeps) == 1


def test_given_darwin_signin_probe_timeout_when_ai_g_runs_then_warns_allows_and_probes_once(tmp_path, monkeypatch):
    from click.testing import CliRunner

    home = tmp_path / "home"
    home.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(agy_session.sys, "platform", "darwin")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("AI_CLI_SKIP_DIRENV", "1")
    monkeypatch.delenv(agy_session.SIGN_IN_VERIFIED_ENV, raising=False)
    monkeypatch.chdir(worktree)

    probe_calls: list[list[str]] = []
    execed_agy: list[tuple[str, list[str], str | None]] = []

    def fake_probe_run(argv, **kwargs):
        if list(argv) == ["agy", "models"]:
            probe_calls.append(list(argv))
            raise agy_session.subprocess.TimeoutExpired(argv, kwargs.get("timeout", 8))
        return type("R", (), {"returncode": 0, "stdout": "1.5.14\n", "stderr": ""})()

    def fake_agy_handoff(exe, argv):
        execed_agy.append((exe, list(argv), os.environ.get(agy_session.SIGN_IN_VERIFIED_ENV)))
        raise SystemExit(0)

    def fake_exec_with_direnv(_root, command, *, handoff=None):
        # Run the real agy_session.main() child step in-process to verify preflight cache reuse.
        idx = command.index("ai_cli.agy_session")
        with patch.object(main, "_exec_handoff", side_effect=fake_agy_handoff):
            rc = agy_session.main(command[idx + 1 :])
        raise SystemExit(rc)

    monkeypatch.setattr(agy_session.subprocess, "run", fake_probe_run)
    monkeypatch.setattr(
        main._config,
        "load_config",
        lambda *_a, **_kw: {
            "agy": {"command": "agy"},
            "session": {"use_pty": False, "use_tmux": False},
            "projects": {"myproject": {"path": str(worktree), "prefix": "myproject"}},
        },
    )
    monkeypatch.setattr(main.shutil, "which", lambda name, *a, **kw: "/usr/bin/agy" if name == "agy" else None)
    monkeypatch.setattr(main, "_exec_with_direnv", fake_exec_with_direnv)
    monkeypatch.setattr(main, "_resolve_session_identity", lambda *a, **kw: (SLOT, AI_NAME))
    monkeypatch.setattr(main._session, "is_current_project_resolved", lambda: True)
    monkeypatch.setattr(main._session, "get_project_prefix", lambda *_a, **_kw: "myproject")
    monkeypatch.setattr(main._session_launch_state, "handoff", lambda _sid, h: h)
    monkeypatch.setattr(
        main._compact_transport,
        "resolve",
        lambda **_kw: type(
            "CT",
            (),
            {
                "must_refuse": False,
                "platform_limited": False,
                "detail": "ok",
                "kind": "none",
            },
        )(),
    )

    result = CliRunner().invoke(main._cli_group, ["g", "1", "--no-worktree", "--bare"])
    assert result.exit_code == 0, result.output
    assert "Could not verify Antigravity sign-in state" in result.output
    assert len(probe_calls) == 1
    assert execed_agy and execed_agy[0][0] == "agy"
    assert execed_agy[0][2] is None


def _setup_agy_cli_tmux_env(tmp_path, monkeypatch, session_name: str = "g-myproject-1"):
    home = tmp_path / "home"
    worktree = tmp_path / "wt"
    worktree.mkdir(exist_ok=True)
    state_dir = tmp_path / "state"
    (state_dir / "sessions").mkdir(parents=True, exist_ok=True)
    (state_dir / "sessions" / f"{session_name}.agy.json").write_text("{}", encoding="utf-8")
    _seed_agy_home(home, oauth=True)

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_dir))
    monkeypatch.setenv("AI_CLI_SKIP_DIRENV", "1")
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.chdir(worktree)
    monkeypatch.setattr(main._config, "get_xdg_state_home", lambda: state_dir)
    monkeypatch.setattr(
        main._config,
        "load_config",
        lambda *_a, **_kw: {
            "agy": {"command": "agy"},
            "projects": {"myproject": {"path": str(worktree), "prefix": "myproject"}},
        },
    )
    monkeypatch.setattr(main.shutil, "which", lambda name, *a, **kw: "/usr/bin/" + name)
    monkeypatch.setattr(main._session, "is_current_project_resolved", lambda: True)
    monkeypatch.setattr(main._session, "get_project_prefix", lambda *_a, **_kw: "myproject")
    monkeypatch.setattr(main._session, "detect_repo_root", lambda *_a, **_kw: None)
    monkeypatch.setattr(main._session, "_tmux_session_names", lambda *_a, **_kw: [session_name])
    monkeypatch.setattr(main, "trigger_background_update", lambda *_a, **_kw: None)
    monkeypatch.setattr(main._iterm2, "_emit_iterm2_profile_setup", lambda *_a, **_kw: None)
    monkeypatch.setattr(main, "_resolve_session_identity", lambda *a, **kw: (session_name, "myproject-1"))
    monkeypatch.setattr(main.sys, "stdin", type("FakeTTY", (), {"isatty": lambda self: True})())
    return state_dir


def test_given_tmux_client_marker_agy_when_ai_g_1_runs_then_attaches(tmp_path, monkeypatch):
    _setup_agy_cli_tmux_env(tmp_path, monkeypatch)
    attached: list[list[str]] = []

    def fake_attach(argv):
        attached.append(list(argv))
        raise SystemExit(0)

    monkeypatch.setattr(main, "_exec_tmux", fake_attach)

    def fake_run(cmd, **kwargs):
        if "-V" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "tmux 3.4\n", "stderr": ""})()
        if "has-session" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        if "show-options" in cmd and main._TMUX_CLIENT_OPTION in cmd:
            return type("R", (), {"returncode": 0, "stdout": "agy\n", "stderr": ""})()
        if "list-panes" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "0\n", "stderr": ""})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    monkeypatch.setattr(main.sys, "argv", ["ai", "g", "1", "--no-worktree"])

    with pytest.raises(SystemExit) as exc:
        main.cli()
    assert exc.value.code == 0
    assert attached == [["tmux", "attach-session", "-d", "-t", "g-myproject-1"]]


@pytest.mark.parametrize(
    ("cli_argv", "session_name"),
    [
        (["ai", "g", "1", "--no-worktree"], "g-myproject-1"),
        (["ai", "g", "1", "--no-worktree", "--is-remote"], "g-r-myproject-1"),
        (["ai", "g", "-r"], "g-myproject-1"),
    ],
)
def test_given_tmux_client_marker_missing_when_ai_g_attach_paths_run_then_refuses(
    tmp_path, monkeypatch, capsys, cli_argv, session_name
):
    state_dir = _setup_agy_cli_tmux_env(tmp_path, monkeypatch, session_name=session_name)
    attached: list[list[str]] = []
    monkeypatch.setattr(main, "_exec_tmux", lambda argv: attached.append(list(argv)))

    def fake_run(cmd, **kwargs):
        if "-V" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "tmux 3.4\n", "stderr": ""})()
        if "has-session" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        if "display-message" in cmd and any("pane_current_path" in str(arg) for arg in cmd):
            return type("R", (), {"returncode": 0, "stdout": f"{tmp_path / 'wt'}\n", "stderr": ""})()
        if "show-options" in cmd and main._TMUX_CLIENT_OPTION in cmd:
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": "invalid option"})()
        if "list-sessions" in cmd:
            return type("R", (), {"returncode": 0, "stdout": f"{session_name}\n", "stderr": ""})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    monkeypatch.setattr(main.sys, "argv", cli_argv)

    with pytest.raises(SystemExit) as exc:
        main.cli()
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert main._TMUX_CLIENT_OPTION in err and "Relaunch with `ai g`" in err
    assert not attached
    assert not (state_dir / "sessions" / f"{session_name}.sh").exists()


@pytest.mark.parametrize(
    ("cli_argv", "session_name"),
    [
        (["ai", "g", "1", "--no-worktree"], "g-myproject-1"),
        (["ai", "g", "1", "--no-worktree", "--is-remote"], "g-r-myproject-1"),
        (["ai", "g", "-r"], "g-myproject-1"),
    ],
)
def test_given_tmux_client_marker_gemini_when_ai_g_attach_paths_run_then_refuses(
    tmp_path, monkeypatch, capsys, cli_argv, session_name
):
    state_dir = _setup_agy_cli_tmux_env(tmp_path, monkeypatch, session_name=session_name)
    attached: list[list[str]] = []
    monkeypatch.setattr(main, "_exec_tmux", lambda argv: attached.append(list(argv)))

    def fake_run(cmd, **kwargs):
        if "-V" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "tmux 3.4\n", "stderr": ""})()
        if "has-session" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        if "display-message" in cmd and any("pane_current_path" in str(arg) for arg in cmd):
            return type("R", (), {"returncode": 0, "stdout": f"{tmp_path / 'wt'}\n", "stderr": ""})()
        if "show-options" in cmd and main._TMUX_CLIENT_OPTION in cmd:
            return type("R", (), {"returncode": 0, "stdout": "gemini\n", "stderr": ""})()
        if "list-sessions" in cmd:
            return type("R", (), {"returncode": 0, "stdout": f"{session_name}\n", "stderr": ""})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    monkeypatch.setattr(main.sys, "argv", cli_argv)

    with pytest.raises(SystemExit) as exc:
        main.cli()
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "'gemini'" in err and "Relaunch with `ai g`" in err
    assert not attached
    assert not (state_dir / "sessions" / f"{session_name}.sh").exists()


def test_given_slots_1_and_10_live_when_ai_g_1_checks_marker_then_targets_exact_slot_1(tmp_path, monkeypatch, capsys):
    _setup_agy_cli_tmux_env(tmp_path, monkeypatch, session_name="g-myproject-1")
    attached: list[list[str]] = []
    show_opt_cmds: list[list[str]] = []
    monkeypatch.setattr(main, "_exec_tmux", lambda argv: attached.append(list(argv)))

    def fake_run(cmd, **kwargs):
        if "-V" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "tmux 3.4\n", "stderr": ""})()
        if "has-session" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        if "show-options" in cmd and main._TMUX_CLIENT_OPTION in cmd:
            show_opt_cmds.append(list(cmd))
            # Slot 10 is marked agy, but slot 1 is marked gemini; exact `=g-myproject-1:` must not match slot 10.
            if "=g-myproject-10:" in cmd:
                return type("R", (), {"returncode": 0, "stdout": b"agy\n", "stderr": b""})()
            if "=g-myproject-1:" in cmd:
                return type("R", (), {"returncode": 0, "stdout": b"gemini\n", "stderr": b""})()
            return type("R", (), {"returncode": 1, "stdout": b"", "stderr": b"no such session\n"})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    monkeypatch.setattr(main.sys, "argv", ["ai", "g", "1", "--no-worktree"])

    with pytest.raises(SystemExit) as exc:
        main.cli()
    assert exc.value.code == 1
    assert show_opt_cmds == [["tmux", "show-options", "-t", "=g-myproject-1:", "-v", main._TMUX_CLIENT_OPTION]]
    assert "'gemini'" in capsys.readouterr().err
    assert not attached


def test_given_split_window_with_sibling_panes_and_agy_marker_when_ai_g_1_runs_then_attaches(tmp_path, monkeypatch):
    _setup_agy_cli_tmux_env(tmp_path, monkeypatch)
    attached: list[list[str]] = []
    seen_cmds: list[list[str]] = []

    def fake_attach(argv):
        attached.append(list(argv))
        raise SystemExit(0)

    monkeypatch.setattr(main, "_exec_tmux", fake_attach)

    def fake_run(cmd, **kwargs):
        seen_cmds.append(list(cmd))
        if "-V" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "tmux 3.4\n", "stderr": ""})()
        if "has-session" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        if "show-options" in cmd and main._TMUX_CLIENT_OPTION in cmd:
            return type("R", (), {"returncode": 0, "stdout": "agy\n", "stderr": ""})()
        if "list-panes" in cmd:
            # Multi-pane split window (both panes alive); attach trusts @ai_cli_client=agy without guessing commands.
            return type("R", (), {"returncode": 0, "stdout": "0\n0\n", "stderr": ""})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    monkeypatch.setattr(main.sys, "argv", ["ai", "g", "1", "--no-worktree"])

    with pytest.raises(SystemExit) as exc:
        main.cli()
    assert exc.value.code == 0
    assert attached == [["tmux", "attach-session", "-d", "-t", "g-myproject-1"]]
    assert not any("pane_current_command" in " ".join(c) for c in seen_cmds)


def test_given_new_agy_tmux_launch_when_ai_g_creates_session_then_sets_client_marker_option(tmp_path, monkeypatch):
    _setup_agy_cli_tmux_env(tmp_path, monkeypatch)
    attached: list[list[str]] = []
    seen_cmds: list[list[str]] = []

    def fake_attach(argv):
        attached.append(list(argv))
        raise SystemExit(0)

    monkeypatch.setattr(main, "_exec_tmux", fake_attach)
    monkeypatch.setattr(
        main._tmux_ownership,
        "capture_tmux_session_identity",
        lambda sid, expected_generation=None, **_kw: main._tmux_ownership.TmuxSessionIdentity(
            sid, "g-myproject-1", expected_generation or "gen-test"
        ),
    )

    def fake_run(cmd, **kwargs):
        seen_cmds.append(list(cmd))
        if "-V" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "tmux 3.4\n", "stderr": ""})()
        if "has-session" in cmd:
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()
        if "new-session" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "$7\n", "stderr": ""})()
        if "list-panes" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "0\n", "stderr": ""})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    monkeypatch.setattr(main._tmux_ownership.subprocess, "run", fake_run)
    monkeypatch.setattr(main.sys, "argv", ["ai", "g", "1", "--no-worktree"])

    with pytest.raises(SystemExit) as exc:
        main.cli()
    assert exc.value.code == 0
    new_idx = next(i for i, c in enumerate(seen_cmds) if "new-session" in c)
    mark_idx = next(
        i
        for i, c in enumerate(seen_cmds)
        if c[:5] == ["tmux", "if-shell", "-F", "-t", "$7"]
        and c[6] == f"set-option -t '$7' '{main._TMUX_CLIENT_OPTION}' 'agy'"
    )
    respawn_idx = next(
        i
        for i, c in enumerate(seen_cmds)
        if c[:5] == ["tmux", "if-shell", "-F", "-t", "$7"] and c[6].startswith("respawn-pane -k -t '$7'")
    )
    # Agent script must NOT start on new-session; marker is written before respawn-pane starts the script.
    assert "sleep 60" in seen_cmds[new_idx]
    assert not any("g-myproject-1.sh" in arg for arg in seen_cmds[new_idx])
    assert new_idx < mark_idx < respawn_idx
    assert "g-myproject-1.sh" in seen_cmds[respawn_idx][6]
    assert attached == [["tmux", "attach-session", "-d", "-t", "$7"]]


def test_given_regular_agy_launch_when_client_marker_write_fails_then_kills_owned_session_without_starting_agent(
    tmp_path, monkeypatch, capsys
):
    state_dir = _setup_agy_cli_tmux_env(tmp_path, monkeypatch)
    killed: list[object] = []
    seen_cmds: list[list[str]] = []

    monkeypatch.setattr(
        main._tmux_ownership,
        "capture_tmux_session_identity",
        lambda sid, expected_generation=None, **_kw: main._tmux_ownership.TmuxSessionIdentity(
            sid, "g-myproject-1", expected_generation or "gen-test"
        ),
    )
    monkeypatch.setattr(main._tmux_ownership, "kill_owned_tmux_session", lambda ident: (killed.append(ident), True)[1])

    def fake_run(cmd, **kwargs):
        seen_cmds.append(list(cmd))
        if "-V" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "tmux 3.4\n", "stderr": ""})()
        if "has-session" in cmd:
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()
        if "new-session" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "$9\n", "stderr": ""})()
        if "if-shell" in cmd and any(main._TMUX_CLIENT_OPTION in part for part in cmd):
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": "set-option failed"})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    monkeypatch.setattr(main._tmux_ownership.subprocess, "run", fake_run)
    monkeypatch.setattr(main.sys, "argv", ["ai", "g", "1", "--no-worktree"])

    with pytest.raises(SystemExit) as exc:
        main.cli()
    assert exc.value.code == 1
    assert len(killed) == 1
    assert not any("respawn-pane" in part for c in seen_cmds for part in c)
    assert not (state_dir / "sessions" / "g-myproject-1.sh").exists()
    assert "failed to mark tmux session" in capsys.readouterr().err


@pytest.mark.parametrize(
    "legacy_command",
    [
        ["gemini", "--sandbox"],
        ["gemini", "-m", "ai_cli.agy_session", "launch"],
        ["python3-wrapper", "-m", "ai_cli.agy_session", "launch"],
        ["/usr/bin/python3/", "-m", "ai_cli.agy_session", "launch"],
    ],
)
@pytest.mark.parametrize("dry_run", [False, True])
def test_given_legacy_gemini_bare_replay_recipe_when_relaunched_via_cli_then_refuses_before_marking(
    tmp_path, monkeypatch, legacy_command, dry_run
):
    from click.testing import CliRunner

    from ai_cli import session_commands as _sc
    from ai_cli import session_run_state as _srs

    _setup_agy_cli_tmux_env(tmp_path, monkeypatch)
    monkeypatch.setattr(_sc.sys, "platform", "linux")
    wt_dir = tmp_path / "wt"
    wt_dir.mkdir(exist_ok=True)
    seen_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen_cmds.append(list(cmd))
        if "-V" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "tmux 3.4\n", "stderr": ""})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    record = {
        "id": "rec-legacy-g",
        "name": "g-myproject-1",
        "kind": "local",
        "owning_host": "test-host",
        "relaunch": "auto",
        "boot_recipe": {
            "engine": "g",
            "session_name": "g-myproject-1",
            "owning_host": "test-host",
            "project": "myproject",
            "cwd": str(wt_dir),
            "shell": "/bin/bash",
            "argv": ["ai", "g", "1", "--bare"],
            "extra_args": [],
            "options": {"bare": True},
            "command": legacy_command,
        },
    }
    doc = {"machine": "test-host", "sessions": [record]}
    monkeypatch.setattr(_sc.session_reconcile, "reconcile", lambda **kw: None)
    monkeypatch.setattr(_sc.registry, "load_registry", lambda: doc)
    monkeypatch.setattr(_sc.registry, "_mutate", lambda fn, **kw: fn(doc))
    monkeypatch.setattr(_sc.registry, "current_boot_id", lambda: "boot-test")
    monkeypatch.setattr(_sc.registry, "_local_session_alive", lambda _name: False)
    monkeypatch.setattr(_sc.state, "cause", lambda _rec, _boot: "reboot")
    monkeypatch.setattr(_sc.state.config, "load_config", lambda *a, **kw: {"machine": {"host_id": "test-host"}})

    args = ["sessions", "relaunch", "g-myproject-1"]
    if dry_run:
        args.insert(2, "--dry-run")
    res = CliRunner().invoke(main._cli_group, args)
    assert res.exit_code != 0
    assert "relaunch with `ai g`" in res.output
    assert not any("new-session" in c or "set-option" in c for c in seen_cmds)

    # Valid Antigravity bare replay commands (direct, free-threaded, and via direnv exec) remain accepted.
    for valid_cmd in (
        ["/usr/bin/python3", "-m", "ai_cli.agy_session", "launch", "/tmp/slot.json"],
        ["/usr/bin/python3.14t", "-m", "ai_cli.agy_session", "launch", "/tmp/slot.json"],
        ["direnv", "exec", str(wt_dir), "python3.14", "-m", "ai_cli.agy_session", "launch", "/tmp/slot.json"],
    ):
        valid_rec = {**record, "boot_recipe": {**record["boot_recipe"], "command": valid_cmd}}
        assert _srs.boot_recipe_problem(valid_rec) is None


def test_given_once_agy_launch_when_attached_and_marker_succeeds_or_fails_then_uses_fenced_marker_before_handoff(
    tmp_path, monkeypatch, capsys
):
    _setup_agy_cli_tmux_env(tmp_path, monkeypatch)
    monkeypatch.setattr(main.tmux_binary, "command", lambda *_a, **_kw: ("/custom/bin/psmux",))
    monkeypatch.setattr(main.tmux_binary, "argv", lambda *args: ["/custom/bin/psmux", *args])
    monkeypatch.setattr(
        main._tmux_ownership,
        "capture_tmux_session_identity",
        lambda sid, expected_generation=None, **_kw: main._tmux_ownership.TmuxSessionIdentity(
            sid, "g-myproject-1", expected_generation or "gen-test"
        ),
    )

    # 1. Attached --once: ONE ownership-fenced marker write on configured mux binary BEFORE respawn-pane and handoff.
    execed: list[list[str]] = []
    seen_ok: list[list[str]] = []

    def fake_exec(argv):
        execed.append(list(argv))
        raise SystemExit(0)

    def fake_run_ok(cmd, **kw):
        seen_ok.append(list(cmd))
        if "-V" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "tmux 3.4\n", "stderr": ""})()
        if "new-session" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "$11\n", "stderr": ""})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(main, "_exec_tmux", fake_exec)
    monkeypatch.setattr(main.subprocess, "run", fake_run_ok)
    monkeypatch.setattr(main._tmux_ownership.subprocess, "run", fake_run_ok)
    monkeypatch.setattr(main.sys, "argv", ["ai", "g", "-o", "1", "--no-worktree"])
    with pytest.raises(SystemExit) as exc:
        main.cli()
    assert exc.value.code == 0
    new_idx = next(i for i, c in enumerate(seen_ok) if "new-session" in c)
    mark_idx = next(
        i
        for i, c in enumerate(seen_ok)
        if c[:5] == ["/custom/bin/psmux", "if-shell", "-F", "-t", "$11"]
        and c[6] == f"set-option -t '$11' '{main._TMUX_CLIENT_OPTION}' 'agy'"
    )
    respawn_idx = next(
        i
        for i, c in enumerate(seen_ok)
        if c[:5] == ["/custom/bin/psmux", "if-shell", "-F", "-t", "$11"] and c[6].startswith("respawn-pane -k -t '$11'")
    )
    assert "sleep 60" in seen_ok[new_idx]
    assert new_idx < mark_idx < respawn_idx
    assert "ai_cli.agy_session" in seen_ok[respawn_idx][6]
    assert "@ai_cli_client" not in seen_ok[respawn_idx][6]
    assert execed == [["/custom/bin/psmux", "attach-session", "-t", "$11"]]

    # 2. Attached --once marker write failure (or fence mismatch): kills owned session by identity, never respawns, never hands off!
    execed.clear()
    seen_fail: list[list[str]] = []
    killed: list[object] = []
    monkeypatch.setattr(main._tmux_ownership, "kill_owned_tmux_session", lambda ident: (killed.append(ident), True)[1])

    def fake_run_fail(cmd, **kw):
        seen_fail.append(list(cmd))
        if "-V" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "tmux 3.4\n", "stderr": ""})()
        if "new-session" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "$12\n", "stderr": ""})()
        if "if-shell" in cmd and any(main._TMUX_CLIENT_OPTION in part for part in cmd):
            return type("R", (), {"returncode": 0, "stdout": "__ai_cli_ownership_mismatch__\n", "stderr": ""})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(main.subprocess, "run", fake_run_fail)
    monkeypatch.setattr(main._tmux_ownership.subprocess, "run", fake_run_fail)
    monkeypatch.setattr(main.sys, "argv", ["ai", "g", "-o", "1", "--no-worktree"])
    with pytest.raises(SystemExit) as exc2:
        main.cli()
    assert exc2.value.code == 1
    assert len(killed) == 1
    assert not any("respawn-pane" in part for c in seen_fail for part in c)
    assert not any("kill-session" in part for c in seen_fail for part in c)
    assert not execed
    assert "failed to mark tmux session" in capsys.readouterr().err


def test_given_once_agy_launch_when_respawn_fails_then_kills_owned_session_without_attaching(
    tmp_path, monkeypatch, capsys
):
    _setup_agy_cli_tmux_env(tmp_path, monkeypatch)
    monkeypatch.setattr(main.tmux_binary, "command", lambda *_a, **_kw: ("tmux",))
    monkeypatch.setattr(main.tmux_binary, "argv", lambda *args: ["tmux", *args])
    monkeypatch.setattr(
        main._tmux_ownership,
        "capture_tmux_session_identity",
        lambda sid, expected_generation=None, **_kw: main._tmux_ownership.TmuxSessionIdentity(
            sid, "g-myproject-1", expected_generation or "gen-test"
        ),
    )
    killed: list[object] = []
    execed: list[list[str]] = []
    monkeypatch.setattr(main._tmux_ownership, "kill_owned_tmux_session", lambda ident: (killed.append(ident), True)[1])
    monkeypatch.setattr(main, "_exec_tmux", lambda argv: (execed.append(list(argv)), None)[1])

    def fake_run(cmd, **kw):
        if "-V" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "tmux 3.4\n", "stderr": ""})()
        if "new-session" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "$13\n", "stderr": ""})()
        if "if-shell" in cmd and len(cmd) > 6 and cmd[6].startswith("respawn-pane"):
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": "respawn failed"})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    monkeypatch.setattr(main._tmux_ownership.subprocess, "run", fake_run)
    monkeypatch.setattr(main.sys, "argv", ["ai", "g", "-o", "1", "--no-worktree"])
    with pytest.raises(SystemExit) as exc:
        main.cli()
    assert exc.value.code == 1
    assert len(killed) == 1
    assert not execed
    assert "failed to start Antigravity one-shot session" in capsys.readouterr().err


def test_given_once_agy_launch_when_ownership_or_mux_fails_before_identity_then_cleans_up_placeholder(
    tmp_path, monkeypatch, capsys
):
    import subprocess as _subprocess

    _setup_agy_cli_tmux_env(tmp_path, monkeypatch)
    monkeypatch.setattr(main.tmux_binary, "command", lambda *_a, **_kw: ("tmux",))
    monkeypatch.setattr(main.tmux_binary, "argv", lambda *args: ["tmux", *args])
    execed: list[list[str]] = []
    monkeypatch.setattr(main, "_exec_tmux", lambda argv: (execed.append(list(argv)), None)[1])

    # 1. set-option @ai_cli_session_generation fails -> tries provisional fenced kill, then unmarked placeholder kill.
    provisional_kills: list[object] = []
    placeholder_kills: list[tuple[str, str]] = []
    monkeypatch.setattr(
        main._tmux_ownership,
        "kill_owned_tmux_session",
        lambda ident: (provisional_kills.append(ident), False)[1],
    )
    monkeypatch.setattr(
        main._tmux_ownership,
        "kill_unmarked_tmux_placeholder",
        lambda sid, name, **_kw: (placeholder_kills.append((sid, name)), True)[1],
    )

    def fake_run_gen_fail(cmd, **kw):
        if "-V" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "tmux 3.4\n", "stderr": ""})()
        if "new-session" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "$21\n", "stderr": ""})()
        if "set-option" in cmd and "@ai_cli_session_generation" in cmd:
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": "set-option failed"})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(main.subprocess, "run", fake_run_gen_fail)
    monkeypatch.setattr(main.sys, "argv", ["ai", "g", "-o", "1", "--no-worktree"])
    with pytest.raises(SystemExit) as exc1:
        main.cli()
    assert exc1.value.code == 1
    assert len(provisional_kills) == 1
    assert placeholder_kills == [("$21", "g-myproject-1")]
    assert not execed
    assert "failed to establish ownership" in capsys.readouterr().err

    # 2. capture_tmux_session_identity returns None after generation stamp -> provisional fenced kill succeeds.
    provisional_kills.clear()
    placeholder_kills.clear()
    monkeypatch.setattr(main._tmux_ownership, "capture_tmux_session_identity", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        main._tmux_ownership,
        "kill_owned_tmux_session",
        lambda ident: (provisional_kills.append(ident), True)[1],
    )

    def fake_run_ok_gen(cmd, **kw):
        if "-V" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "tmux 3.4\n", "stderr": ""})()
        if "new-session" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "$22\n", "stderr": ""})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(main.subprocess, "run", fake_run_ok_gen)
    with pytest.raises(SystemExit) as exc2:
        main.cli()
    assert exc2.value.code == 1
    assert len(provisional_kills) == 1
    assert placeholder_kills == []
    assert not execed

    # 3. Unexpected mux TimeoutExpired during set-option -> cleans up placeholder and exits 1 cleanly.
    provisional_kills.clear()
    placeholder_kills.clear()
    monkeypatch.setattr(
        main._tmux_ownership,
        "kill_owned_tmux_session",
        lambda ident: (provisional_kills.append(ident), False)[1],
    )

    def fake_run_timeout(cmd, **kw):
        if "-V" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "tmux 3.4\n", "stderr": ""})()
        if "new-session" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "$23\n", "stderr": ""})()
        if "set-option" in cmd and "@ai_cli_session_generation" in cmd:
            raise _subprocess.TimeoutExpired(cmd, 10.0)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(main.subprocess, "run", fake_run_timeout)
    with pytest.raises(SystemExit) as exc3:
        main.cli()
    assert exc3.value.code == 1
    assert placeholder_kills == [("$23", "g-myproject-1")]
    assert not execed
    assert "failed to initialize one-shot tmux session" in capsys.readouterr().err

    # 4. format-not-expanded on new-session -> kills newly created =g-myproject-1 by exact name.
    killed_by_name: list[list[str]] = []

    def fake_run_unexpanded(cmd, **kw):
        if "-V" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "tmux 3.4\n", "stderr": ""})()
        if "new-session" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "#session_id\n", "stderr": ""})()
        if "kill-session" in cmd:
            killed_by_name.append(list(cmd))
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(main.subprocess, "run", fake_run_unexpanded)
    with pytest.raises(SystemExit) as exc4:
        main.cli()
    assert exc4.value.code == 1
    assert killed_by_name == [["tmux", "kill-session", "-t", "=g-myproject-1"]]
    assert not execed


def test_given_once_agy_launch_when_agent_exits_before_attach_then_handles_fast_exit_cleanly(
    tmp_path, monkeypatch, capsys
):
    _setup_agy_cli_tmux_env(tmp_path, monkeypatch)
    monkeypatch.setattr(main.tmux_binary, "command", lambda *_a, **_kw: ("tmux",))
    monkeypatch.setattr(main.tmux_binary, "argv", lambda *args: ["tmux", *args])
    execed: list[list[str]] = []
    monkeypatch.setattr(main, "_exec_tmux", lambda argv: (execed.append(list(argv)), None)[1])

    def fake_run(cmd, **kw):
        if "-V" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "tmux 3.4\n", "stderr": ""})()
        if "new-session" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "$30\n", "stderr": ""})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    monkeypatch.setattr(main._tmux_ownership.subprocess, "run", fake_run)

    # First capture (during setup) succeeds; second capture (right before attach) returns None (session already exited).
    def _install_disappearing_capture():
        calls = {"n": 0}

        def capture(sid, expected_generation=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return main._tmux_ownership.TmuxSessionIdentity(sid, "g-myproject-1", expected_generation or "gen-test")
            return None

        monkeypatch.setattr(main._tmux_ownership, "capture_tmux_session_identity", capture)

    monkeypatch.setattr(main.sys, "argv", ["ai", "g", "-o", "1", "--no-worktree"])

    # 1. Recorded clean exit (status 0) -> returns cleanly without invoking attach-session.
    _install_disappearing_capture()
    monkeypatch.setattr(main, "_recorded_single_run_exit_status", lambda _name: 0)
    with pytest.raises(SystemExit) as exc_clean:
        main.cli()
    assert exc_clean.value.code == 0
    assert not execed
    out_clean = capsys.readouterr()
    assert "finished before attach" in (out_clean.out + out_clean.err)

    # 2. Recorded failure exit (status 76) -> exits 76 without invoking attach-session.
    _install_disappearing_capture()
    monkeypatch.setattr(main, "_recorded_single_run_exit_status", lambda _name: 76)
    with pytest.raises(SystemExit) as exc_fail:
        main.cli()
    assert exc_fail.value.code == 76
    assert not execed
    assert "exited with status 76 before attach" in capsys.readouterr().err

    # 3. Unrecorded exit (None) -> fail-closed exit 1 without invoking attach-session.
    _install_disappearing_capture()
    monkeypatch.setattr(main, "_recorded_single_run_exit_status", lambda _name: None)
    with pytest.raises(SystemExit) as exc_unknown:
        main.cli()
    assert exc_unknown.value.code == 1
    assert not execed
    assert "exited before attach" in capsys.readouterr().err


def test_given_registry_records_when_reading_single_run_exit_status_then_ignores_stale_runs(monkeypatch):
    monkeypatch.setattr(
        main._session_registry,
        "load_registry",
        lambda: {
            "sessions": [
                {
                    "kind": "local",
                    "name": "g-myproject-stale",
                    "run_state": "exited",
                    "launched_at": "2026-11-24T10:00:00Z",
                    "exit": {
                        "cause": "manual_exit",
                        "at": "2026-11-24T09:00:00Z",
                        "evidence": {"source": "single-run", "status": 0},
                    },
                },
                {
                    "kind": "local",
                    "name": "g-myproject-fresh",
                    "run_state": "exited",
                    "launched_at": "2026-11-24T10:00:00Z",
                    "supervisor_exit": {
                        "cause": "manual_exit",
                        "at": "2026-11-24T10:00:01Z",
                        "evidence": {"source": "single-run", "status": 76},
                    },
                },
            ]
        },
    )
    assert main._recorded_single_run_exit_status("g-myproject-stale") is None
    assert main._recorded_single_run_exit_status("g-myproject-fresh") == 76


def test_given_non_g_engine_when_session_script_generated_then_omits_agy_signin_supervisor_logic(tmp_path):
    from ai_cli import session_script

    g_script = session_script.get_engine_script(
        "g", AI_NAME, SLOT, "g-myproject-", "myproject", None, False, str(tmp_path), agy_cmd="agy"
    )
    assert "_supervisor_agy_signin_token" in g_script
    for engine, slot in (("c", "c-myproject-1"), ("cx", "cx-myproject-1"), ("p", "p-myproject-1")):
        other = session_script.get_engine_script(
            engine, AI_NAME, slot, f"{engine}-myproject-", "myproject", None, False, str(tmp_path)
        )
        assert "_supervisor_agy_signin_token" not in other


@pytest.mark.parametrize(
    ("extra_argv", "stdin_tty"),
    [
        (["--print", "--mode", "plan"], False),
        (["-p", "inspect", "--mode", "plan"], True),
    ],
)
def test_given_mismatched_tmux_server_when_agy_print_mode_launches_then_skips_tmux_version_gate(
    tmp_path, monkeypatch, extra_argv, stdin_tty
):
    import io

    _setup_agy_cli_tmux_env(tmp_path, monkeypatch)
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,123,0")
    monkeypatch.setattr(main._tmux_setup, "tmux_present", lambda: True)
    monkeypatch.setattr(main.tmux_binary, "which", lambda: "/usr/bin/tmux")
    # If the tmux preflight or version gate probes tmux at all, fail immediately (simulating a
    # mismatched client/server that would otherwise block headless print runs).
    monkeypatch.setattr(
        main._tmux_setup,
        "_tmux_probe",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError(f"unexpected tmux probe: {args}")),
    )
    monkeypatch.setattr(
        main._tmux_setup,
        "tmux_runs",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("tmux_runs must not run for agy print mode")),
    )
    monkeypatch.setattr(
        main._tmux_setup,
        "formats_expand",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("formats_expand must not run for agy print mode")),
    )

    class _FakeStdin(io.StringIO):
        def isatty(self) -> bool:
            return stdin_tty

    monkeypatch.setattr(main.sys, "stdin", _FakeStdin("hello from pipe\n"))
    handed_off: list[tuple[str, list[str]]] = []

    def fake_handoff(file, args, **_kw):
        handed_off.append((file, list(args)))
        raise SystemExit(0)

    monkeypatch.setattr(main, "_exec_handoff", fake_handoff)
    monkeypatch.setattr(main._session_launch_state, "handoff", lambda _name, delegate: delegate)
    monkeypatch.setattr(main.sys, "argv", ["ai", "g", "1", "--no-worktree", "--", *extra_argv])

    with pytest.raises(SystemExit) as exc:
        main.cli()
    assert exc.value.code == 0
    assert len(handed_off) == 1
    _file, argv = handed_off[0]
    assert "ai_cli.agy_session" in argv
    assert "--mode" in argv and "plan" in argv


def test_given_detached_replay_with_print_args_when_tmux_unusable_then_still_refuses(tmp_path, monkeypatch):
    _setup_agy_cli_tmux_env(tmp_path, monkeypatch)
    monkeypatch.setattr(main._tmux_setup, "tmux_runs", lambda: False)
    with pytest.raises(RuntimeError, match="detached replay requires a working tmux executable"):
        main._do_session_launch(
            engine="g",
            name="1",
            resume=False,
            once=False,
            bare=False,
            notify=False,
            sandbox=False,
            no_worktree=True,
            no_direnv=True,
            remote=False,
            remote_machine=None,
            is_remote=False,
            project=None,
            project_prefix_override=None,
            extra_args=["--print", "--mode", "plan", "hi"],
            config={},
            detached=True,
        )


def test_given_detached_replay_with_print_args_when_tmux_healthy_then_still_runs_bare(tmp_path, monkeypatch):
    _setup_agy_cli_tmux_env(tmp_path, monkeypatch)
    monkeypatch.setattr(main._tmux_setup, "tmux_present", lambda: True)
    monkeypatch.setattr(main._tmux_setup, "tmux_runs", lambda: True)
    monkeypatch.setattr(main._tmux_setup, "formats_expand", lambda: True)
    monkeypatch.setattr(
        main._tmux_setup,
        "probe",
        lambda **_kw: main._tmux_setup.TmuxReport(
            present=True, path="/usr/bin/tmux", client_version="3.7c", server_version="3.7c"
        ),
    )
    seen_tmux: list[list[str]] = []

    def fake_run(cmd, **kw):
        seen_tmux.append(list(cmd))
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    handed_off: list[tuple[str, list[str]]] = []

    def fake_handoff(file, args, **_kw):
        handed_off.append((file, list(args)))
        raise SystemExit(0)

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    monkeypatch.setattr(main, "_exec_handoff", fake_handoff)
    monkeypatch.setattr(main._session_launch_state, "handoff", lambda _name, delegate: delegate)

    with pytest.raises(SystemExit) as exc:
        main._do_session_launch(
            engine="g",
            name="1",
            resume=False,
            once=False,
            bare=False,
            notify=False,
            sandbox=False,
            no_worktree=True,
            no_direnv=True,
            remote=False,
            remote_machine=None,
            is_remote=False,
            project=None,
            project_prefix_override=None,
            extra_args=["--print", "--mode", "plan", "hi"],
            config={},
            detached=True,
        )
    assert exc.value.code == 0
    assert len(handed_off) == 1
    assert "ai_cli.agy_session" in handed_off[0][1]
    assert not any("new-session" in cmd for cmd in seen_tmux)


def _fake_tmux_has_session(live_sessions: list[str]):
    """Build a subprocess.run stub answering ``has-session -t =<name>`` against ``live_sessions``."""
    live_set = set(live_sessions)

    def _run(cmd, **_kw):
        if "has-session" in cmd and "-t" in cmd:
            target = cmd[cmd.index("-t") + 1].lstrip("=")
            return type("R", (), {"returncode": 0 if target in live_set else 1, "stdout": "", "stderr": ""})()
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()

    return _run


@pytest.mark.parametrize(
    ("naming_prefix", "legacy_prefix", "live_sessions", "name", "is_remote"),
    [
        ("myproject-box", "myproject", ["g-myproject-box-1", "g-myproject-1"], "", False),
        ("myproject-box", "oldproj", ["g-myproject-box-1", "g-oldproj-1"], "", False),
        ("myproject-box", "myproject", ["g-myproject-box-worker-1", "g-myproject-worker-1"], "worker", False),
        ("myproject-box", "myproject", ["g-myproject-box-worker", "g-myproject-worker"], "worker", False),
        ("myproject-box", "myproject", ["g-myproject-box-1", "g-myproject-1"], "1", False),
        ("myproject-box", "myproject", ["g-r-myproject-box-worker-1", "g-r-myproject-worker-1"], "worker", True),
    ],
)
def test_given_current_and_legacy_live_agy_slots_when_resuming_ambiguously_then_refuses(
    tmp_path, monkeypatch, capsys, naming_prefix, legacy_prefix, live_sessions, name, is_remote
):
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setattr(main._session, "_tmux_session_names", lambda: list(live_sessions))
    monkeypatch.setattr(main._session.subprocess, "run", _fake_tmux_has_session(live_sessions))
    monkeypatch.setattr(main, "_verify_live_agy_tmux_session", lambda _s: None)
    reporter = main.LaunchReporter(verbose=False)

    with pytest.raises(SystemExit) as exc:
        main._resolve_resume_target(
            "g",
            naming_prefix,
            legacy_prefix,
            name,
            is_remote=is_remote,
            bare=False,
            repo_root=tmp_path,
            reporter=reporter,
        )
    assert exc.value.code == 1
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "ambiguous Antigravity resume" in combined
    for expected in live_sessions:
        assert expected in combined


@pytest.mark.parametrize(
    ("live_sessions", "name", "expected_session"),
    [
        (["g-myproject-box-1"], "", "g-myproject-box-1"),
        (["g-myproject-1"], "", "g-myproject-1"),
        (["g-myproject-box-1", "g-myproject-1"], "g-myproject-box-1", "g-myproject-box-1"),
        (["g-myproject-box-1", "g-myproject-1"], "g-myproject-1", "g-myproject-1"),
        (["g-myproject-box-1", "g-myproject-1"], "myproject-box-1", "g-myproject-box-1"),
        (["g-myproject-box-1", "g-myproject-1"], "myproject-1", "g-myproject-1"),
        (["g-myproject-box-worker-1", "g-myproject-worker-1"], "g-myproject-box-worker-1", "g-myproject-box-worker-1"),
        (["g-myproject-box-worker-1", "g-myproject-worker-1"], "g-myproject-worker-1", "g-myproject-worker-1"),
        (["g-myproject-1", "g-myproject-box-myproject-1"], "myproject-1", "g-myproject-1"),
        (["g-myproject-1", "g-myproject-box-myproject-1"], "myproject-box-myproject-1", "g-myproject-box-myproject-1"),
        (
            ["g-myproject-1", "g-myproject-box-myproject-1-a", "g-myproject-box-myproject-1-b"],
            "myproject-1",
            "g-myproject-1",
        ),
    ],
)
def test_given_current_or_legacy_live_agy_slots_when_resuming_unambiguously_then_resolves_target(
    tmp_path, monkeypatch, live_sessions, name, expected_session
):
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setattr(main._session, "_tmux_session_names", lambda: list(live_sessions))
    monkeypatch.setattr(main._session.subprocess, "run", _fake_tmux_has_session(live_sessions))
    monkeypatch.setattr(main, "_verify_live_agy_tmux_session", lambda _s: None)
    reporter = main.LaunchReporter(verbose=False)

    target = main._resolve_resume_target(
        "g",
        "myproject-box",
        "myproject",
        name,
        is_remote=False,
        bare=False,
        repo_root=tmp_path,
        reporter=reporter,
    )
    assert target.session == expected_session


@pytest.mark.parametrize(
    ("legacy_convention", "expect_refusal"),
    [
        ("off", False),
        ("full", True),
    ],
)
def test_given_live_current_and_legacy_agy_slots_when_launching_resume_with_legacy_convention_then_honors_setting(
    tmp_path, monkeypatch, capsys, legacy_convention, expect_refusal
):
    _setup_agy_cli_tmux_env(tmp_path, monkeypatch)
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    live_sessions = ["g-myproject-box-1", "g-myproject-1"]
    monkeypatch.setattr(main._tmux_setup, "tmux_present", lambda: True)
    monkeypatch.setattr(main._tmux_setup, "tmux_runs", lambda: True)
    monkeypatch.setattr(main._tmux_setup, "formats_expand", lambda: True)
    monkeypatch.setattr(
        main._tmux_setup,
        "probe",
        lambda **_kw: main._tmux_setup.TmuxReport(
            present=True, path="/usr/bin/tmux", client_version="3.7c", server_version="3.7c"
        ),
    )
    monkeypatch.setattr(
        main._session_naming,
        "resolve_launch_naming",
        lambda repo_alias, cfg, **_kw: main._session_naming.LaunchNaming(
            f"{repo_alias}-box",
            repo_alias if cfg.get("naming", {}).get("legacy_convention", "full") in ("reach", "full") else None,
            "box",
            False,
        ),
    )
    monkeypatch.setattr(main._session, "_tmux_session_names", lambda: list(live_sessions))
    monkeypatch.setattr(main._session.subprocess, "run", _fake_tmux_has_session(live_sessions))
    monkeypatch.setattr(main, "_verify_live_agy_tmux_session", lambda _s: None)
    monkeypatch.setattr(main, "_resume_suspended_panes", lambda _s: None)
    monkeypatch.setattr(main._session_launch_state, "reattach", lambda _s: None)
    monkeypatch.setattr(main, "_track_launch", lambda *_a, **_kw: None)
    attached: list[list[str]] = []
    created: list[str] = []
    monkeypatch.setattr(main, "_exec_tmux", lambda argv: (attached.append(list(argv)), sys.exit(0)))
    monkeypatch.setattr(main._session, "create_worktree", lambda *a, **kw: created.append("wt"))

    with pytest.raises(SystemExit) as exc:
        main._do_session_launch(
            engine="g",
            name="",
            resume=True,
            once=False,
            bare=False,
            notify=False,
            sandbox=False,
            no_worktree=False,
            no_direnv=True,
            remote=False,
            remote_machine=None,
            is_remote=False,
            project=None,
            project_prefix_override=None,
            extra_args=[],
            config={"naming": {"legacy_convention": legacy_convention}},
            detached=False,
        )

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert created == []
    if expect_refusal:
        assert exc.value.code == 1
        assert "ambiguous Antigravity resume" in combined
        assert "g-myproject-box-1" in combined and "g-myproject-1" in combined
        assert attached == []
    else:
        assert exc.value.code == 0
        assert len(attached) == 1
        assert attached[0][-1] == "g-myproject-box-1"


def _write_agy_slot_record(state_dir: Path, slot: str, cwd: Path) -> Path:
    sessions_dir = state_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    record_path = sessions_dir / f"{slot}.agy.json"
    record_path.write_text(
        json.dumps(
            {
                "schema_version": agy_session.SCHEMA_VERSION,
                "slot": slot,
                "cwd": str(cwd),
                "phase": "confirmed",
                "conversation_id": "11111111-2222-4333-8444-555555555555",
                "transcript_path": None,
                "result": "confirmed",
                "resumable": {
                    "conversation_id": "11111111-2222-4333-8444-555555555555",
                    "transcript_path": None,
                },
                "client_version": "1.0.0",
                "execution_mode": "interactive",
                "route": "local-tmux",
                "in_flight": {
                    "token": "deadbeef" * 4,
                    "writer": {"pid": 999999, "backend": "procfs", "start_time": 1},
                    "launched_at": "2026-01-01T00:00:00+00:00",
                    "client_version": "1.0.0",
                    "route": "local-tmux",
                    "expected_conversation_id": None,
                },
            }
        ),
        encoding="utf-8",
    )
    return record_path


def test_given_stopped_no_worktree_agy_slot_when_resuming_without_dash_W_then_reuses_saved_repo_root_without_creating_worktree(
    tmp_path, monkeypatch
):
    repo_root = tmp_path / "repo"
    subdir = repo_root / "subdir"
    subdir.mkdir(parents=True)
    state_dir = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_dir))
    _setup_agy_cli_tmux_env(repo_root, monkeypatch)
    # Invoke from a subdirectory to prove saved repo_root is reused even when cwd differs.
    monkeypatch.chdir(subdir)
    monkeypatch.setattr(main._session, "detect_repo_root", lambda: str(repo_root))
    monkeypatch.setattr(main._config, "get_xdg_state_home", lambda: state_dir)
    monkeypatch.setattr(main._config, "_project_root", lambda _p: repo_root.resolve())
    monkeypatch.setattr(main._session, "_tmux_session_names", list)
    monkeypatch.setattr(main._session.subprocess, "run", _fake_tmux_has_session([]))
    monkeypatch.setattr(
        main._session_naming,
        "resolve_launch_naming",
        lambda repo_alias, _cfg, **_kw: main._session_naming.LaunchNaming(
            f"{repo_alias}-box", repo_alias, "box", False
        ),
    )
    monkeypatch.setattr(main._session_naming, "validate_script_metadata", lambda *_a, **_kw: None)
    monkeypatch.setattr(main._session, "cleanup_stale_sessions", lambda _cfg: None)
    monkeypatch.setattr(main, "_finish_recorded_migrations", lambda *_a, **_kw: None)
    monkeypatch.setattr(main._session_launch_state, "register_launch", lambda *_a, **_kw: None)
    monkeypatch.setattr(main._session_launch_state, "handoff", lambda _name, delegate: delegate)

    slot = "g-myproject-box-1"
    _write_agy_slot_record(state_dir, slot, repo_root.resolve())

    created_worktrees: list[str] = []
    monkeypatch.setattr(main._session, "create_worktree", lambda *a, **kw: created_worktrees.append(str(a)))

    handed_off: list[tuple[Path, list[str]]] = []

    def fake_handoff(_file, args, **_kw):
        handed_off.append((Path.cwd().resolve(), list(args)))
        raise SystemExit(0)

    monkeypatch.setattr(main, "_exec_handoff", fake_handoff)
    monkeypatch.setattr(main, "_pty_handoff", lambda *_a, **_kw: fake_handoff)

    with pytest.raises(SystemExit) as exc:
        main._do_session_launch(
            engine="g",
            name="myproject-box-1",
            resume=True,
            once=False,
            bare=True,
            notify=False,
            sandbox=False,
            no_worktree=False,
            no_direnv=True,
            remote=False,
            remote_machine=None,
            is_remote=False,
            project=None,
            project_prefix_override=None,
            extra_args=[],
            config={"worktree": {"enabled": True}},
            detached=False,
        )

    assert exc.value.code == 0
    assert created_worktrees == []
    assert len(handed_off) == 1
    launch_cwd, launch_argv = handed_off[0]
    assert launch_cwd == repo_root.resolve()
    assert str(repo_root.resolve()) in launch_argv


def test_given_stopped_no_worktree_agy_slot_when_dry_run_resume_without_dash_W_then_plan_disables_worktree(
    tmp_path, monkeypatch
):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    state_dir = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_dir))
    _setup_agy_cli_tmux_env(repo_root, monkeypatch)
    monkeypatch.setattr(main._session, "detect_repo_root", lambda: str(repo_root))
    monkeypatch.setattr(main._config, "get_xdg_state_home", lambda: state_dir)
    monkeypatch.setattr(main._session, "_tmux_session_names", list)
    monkeypatch.setattr(main._session.subprocess, "run", _fake_tmux_has_session([]))
    monkeypatch.setattr(
        main._session_naming,
        "resolve_launch_naming",
        lambda repo_alias, _cfg, **_kw: main._session_naming.LaunchNaming(
            f"{repo_alias}-box", repo_alias, "box", False
        ),
    )

    _write_agy_slot_record(state_dir, "g-myproject-box-1", repo_root.resolve())

    seen_plans: list[dict] = []
    monkeypatch.setattr(main, "_print_launch_plan", lambda **kw: seen_plans.append(kw))

    main._do_session_launch(
        engine="g",
        name="myproject-box-1",
        resume=True,
        once=False,
        bare=True,
        notify=False,
        sandbox=False,
        no_worktree=False,
        no_direnv=True,
        remote=False,
        remote_machine=None,
        is_remote=False,
        project=None,
        project_prefix_override=None,
        extra_args=[],
        config={"worktree": {"enabled": True}},
        detached=False,
        dry_run=True,
    )

    assert len(seen_plans) == 1
    assert seen_plans[0]["session_id"] == "g-myproject-box-1"
    assert seen_plans[0]["worktree_enabled"] is False


def test_given_stopped_no_worktree_agy_slot_when_resuming_tmux_without_dash_W_then_script_uses_saved_repo_root(
    tmp_path, monkeypatch
):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    state_dir = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_dir))
    _setup_agy_cli_tmux_env(repo_root, monkeypatch)
    monkeypatch.setattr(main._session, "detect_repo_root", lambda: str(repo_root))
    monkeypatch.setattr(main._config, "get_xdg_state_home", lambda: state_dir)
    monkeypatch.setattr(main._tmux_setup, "tmux_present", lambda: True)
    monkeypatch.setattr(main._tmux_setup, "tmux_runs", lambda: True)
    monkeypatch.setattr(main._tmux_setup, "formats_expand", lambda: True)
    monkeypatch.setattr(
        main._tmux_setup,
        "probe",
        lambda **_kw: main._tmux_setup.TmuxReport(
            present=True, path="/usr/bin/tmux", client_version="3.7c", server_version="3.7c"
        ),
    )
    monkeypatch.setattr(main._session, "_tmux_session_names", list)
    monkeypatch.setattr(main._session.subprocess, "run", _fake_tmux_has_session([]))
    monkeypatch.setattr(
        main._session_naming,
        "resolve_launch_naming",
        lambda repo_alias, _cfg, **_kw: main._session_naming.LaunchNaming(
            f"{repo_alias}-box", repo_alias, "box", False
        ),
    )
    monkeypatch.setattr(main._session_naming, "validate_script_metadata", lambda *_a, **_kw: None)
    monkeypatch.setattr(main._session, "cleanup_stale_sessions", lambda _cfg: None)
    monkeypatch.setattr(main, "_finish_recorded_migrations", lambda *_a, **_kw: None)

    slot = "g-myproject-box-1"
    _write_agy_slot_record(state_dir, slot, repo_root.resolve())

    created_worktrees: list[str] = []
    monkeypatch.setattr(main._session, "create_worktree", lambda *a, **kw: created_worktrees.append(str(a)))

    seen_script_worktree: list[str | None] = []

    def fake_get_engine_script(*args, **kwargs):
        seen_script_worktree.append(args[7] if len(args) > 7 else kwargs.get("worktree_dir"))
        raise SystemExit(0)

    monkeypatch.setattr(main._session_script, "get_engine_script", fake_get_engine_script)

    with pytest.raises(SystemExit) as exc:
        main._do_session_launch(
            engine="g",
            name="myproject-box-1",
            resume=True,
            once=False,
            bare=False,
            notify=False,
            sandbox=False,
            no_worktree=False,
            no_direnv=True,
            remote=False,
            remote_machine=None,
            is_remote=False,
            project=None,
            project_prefix_override=None,
            extra_args=[],
            config={"worktree": {"enabled": True}},
            detached=False,
        )

    assert exc.value.code == 0
    assert created_worktrees == []
    assert seen_script_worktree == [str(repo_root.resolve())]


def test_given_stopped_no_worktree_agy_slot_in_subdir_when_resuming_tmux_from_repo_root_then_refuses_without_creating_worktree(
    tmp_path, monkeypatch, capsys
):
    repo_root = tmp_path / "repo"
    subdir = repo_root / "subdir"
    subdir.mkdir(parents=True)
    state_dir = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_dir))
    _setup_agy_cli_tmux_env(repo_root, monkeypatch)
    monkeypatch.setattr(main._session, "detect_repo_root", lambda: str(repo_root))
    monkeypatch.setattr(main._config, "get_xdg_state_home", lambda: state_dir)
    monkeypatch.setattr(main._tmux_setup, "tmux_present", lambda: True)
    monkeypatch.setattr(main._tmux_setup, "tmux_runs", lambda: True)
    monkeypatch.setattr(main._tmux_setup, "formats_expand", lambda: True)
    monkeypatch.setattr(
        main._tmux_setup,
        "probe",
        lambda **_kw: main._tmux_setup.TmuxReport(
            present=True, path="/usr/bin/tmux", client_version="3.7c", server_version="3.7c"
        ),
    )
    monkeypatch.setattr(main._session, "_tmux_session_names", list)
    monkeypatch.setattr(main._session.subprocess, "run", _fake_tmux_has_session([]))
    monkeypatch.setattr(
        main._session_naming,
        "resolve_launch_naming",
        lambda repo_alias, _cfg, **_kw: main._session_naming.LaunchNaming(
            f"{repo_alias}-box", repo_alias, "box", False
        ),
    )
    monkeypatch.setattr(main._session, "cleanup_stale_sessions", lambda _cfg: None)
    monkeypatch.setattr(main, "_finish_recorded_migrations", lambda *_a, **_kw: None)

    slot = "g-myproject-box-1"
    _write_agy_slot_record(state_dir, slot, subdir.resolve())

    created_worktrees: list[str] = []
    monkeypatch.setattr(main._session, "create_worktree", lambda *a, **kw: created_worktrees.append(str(a)))

    with pytest.raises(SystemExit) as exc:
        main._do_session_launch(
            engine="g",
            name="myproject-box-1",
            resume=True,
            once=False,
            bare=False,
            notify=False,
            sandbox=False,
            no_worktree=False,
            no_direnv=True,
            remote=False,
            remote_machine=None,
            is_remote=False,
            project=None,
            project_prefix_override=None,
            extra_args=[],
            config={"worktree": {"enabled": True}},
            detached=False,
        )

    assert exc.value.code == 1
    assert created_worktrees == []
    captured = capsys.readouterr()
    assert str(subdir.resolve()) in (captured.out + captured.err)


def test_given_stopped_worktree_agy_slot_when_checking_saved_no_worktree_cwd_then_returns_none(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    wt_dir = repo_root / ".worktrees" / "myproject-box-1"
    wt_dir.mkdir(parents=True)
    state_dir = tmp_path / "state"
    monkeypatch.setattr(main._config, "get_xdg_state_home", lambda: state_dir)

    _write_agy_slot_record(state_dir, "g-myproject-box-1", wt_dir.resolve())
    assert main._saved_agy_no_worktree_cwd("g-myproject-box-1", repo_root) is None

    # Foreign directory outside repo_root is also ignored.
    other = tmp_path / "other"
    other.mkdir()
    _write_agy_slot_record(state_dir, "g-myproject-box-2", other.resolve())
    assert main._saved_agy_no_worktree_cwd("g-myproject-box-2", repo_root) is None

    # Repo root itself is recognized.
    _write_agy_slot_record(state_dir, "g-myproject-box-3", repo_root.resolve())
    assert main._saved_agy_no_worktree_cwd("g-myproject-box-3", repo_root) == repo_root.resolve()


@pytest.mark.parametrize(
    ("command_line", "expected"),
    [
        ("agy", "agy"),
        ("/usr/local/bin/agy --mode chat", "agy"),
        (r"C:\Tools\agy.exe --chat", "agy"),
        ("antigravity", "agy"),
        ("\"/bin/sh -c 'exec agy --chat'\"", "agy"),
        ("env FOO=1 nice -n 10 /opt/bin/agy", "agy"),
        ("python3 -m ai_cli.agy_session launch slot.json", "agy"),
        ("uv run python -m ai_cli.agy_session launch slot.json", "agy"),
        ("cmd /c agy.cmd --chat", "agy"),
        ("bash -lc 'cd /work && exec agy'", "agy"),
        ("gemini --yolo", "gemini"),
        ("vim agy.py", None),
        ("grep agy notes.txt", None),
        ("bash -c 'echo \"foo; agy\"'", None),
        ("bash -c 'sleep 60'", None),
        ("bash -c 'agy && gemini'", None),
    ],
)
def test_given_pane_command_line_when_classifying_g_client_then_matches_only_real_clients(command_line, expected):
    assert main._classify_g_command_line(command_line) == expected


def test_given_manually_started_agy_pane_without_metadata_when_do_attach_runs_then_prints_agy_label(
    tmp_path, monkeypatch
):
    from click.testing import CliRunner

    monkeypatch.setattr(main._config, "get_xdg_state_home", lambda: tmp_path)
    seen_cmds: list[list[str]] = []
    attach_calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        cmd = list(argv)
        seen_cmds.append(cmd)
        if "has-session" in cmd:
            return type("R", (), {"returncode": 0, "stdout": ""})()
        if "list-panes" in cmd:
            return type("R", (), {"returncode": 0, "stdout": "\tagy\tagy --mode chat\n"})()
        return type("R", (), {"returncode": 1, "stdout": ""})()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    monkeypatch.setattr(main, "_resume_suspended_panes", lambda _name: None)
    monkeypatch.setattr(main, "_exec_tmux", lambda argv: (attach_calls.append(list(argv)), sys.exit(0)))

    res = CliRunner().invoke(main._cli_group, ["attach", "g-myproject-manual"])
    assert res.exit_code == 0, res.output
    assert "Attaching to g-myproject-manual [agy]" in res.output
    list_pane_calls = [cmd for cmd in seen_cmds if "list-panes" in cmd]
    assert list_pane_calls and "=g-myproject-manual:" in list_pane_calls[0]
    assert attach_calls and attach_calls[0][-1] == "=g-myproject-manual"


def test_given_g_session_without_metadata_when_probing_live_panes_then_handles_marker_ambiguity_and_errors(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(main._config, "get_xdg_state_home", lambda: tmp_path)

    # 1. @ai_cli_client option wins even when pane command is a shell.
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda *_a, **_kw: type("R", (), {"returncode": 0, "stdout": "agy\tbash\t/bin/bash\n"})(),
    )
    assert main._g_session_client("g-myproject-1") == "agy"

    # 2. Multi-pane session with one agy pane and one helper shell still resolves to agy.
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda *_a, **_kw: type(
            "R", (), {"returncode": 0, "stdout": "\tbash\t/bin/bash\n\tagy\t/usr/local/bin/agy\n"}
        )(),
    )
    assert main._g_session_client("g-myproject-2") == "agy"

    # 3. Multi-pane session with both agy and gemini is ambiguous -> None.
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda *_a, **_kw: type("R", (), {"returncode": 0, "stdout": "\tagy\tagy\n\tgemini\tgemini\n"})(),
    )
    assert main._g_session_client("g-myproject-3") is None

    # 4. Plain shell pane with no client command -> None.
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda *_a, **_kw: type("R", (), {"returncode": 0, "stdout": "\tzsh\t/bin/zsh\n"})(),
    )
    assert main._g_session_client("g-myproject-4") is None

    # 5. Subprocess failure or timeout degrades cleanly to None.
    def raise_timeout(*_a, **_kw):
        raise main.subprocess.TimeoutExpired(["tmux"], 5)

    monkeypatch.setattr(main.subprocess, "run", raise_timeout)
    assert main._g_session_client("g-myproject-5") is None
