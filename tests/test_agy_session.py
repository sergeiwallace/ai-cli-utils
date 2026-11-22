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
    assert manifest == {"name": "ai-cli-slot-confirm"}
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


def test_given_abandoned_unconfirmed_launch_when_prepare_called_again_then_starts_fresh(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    home = tmp_path / "home"
    _seed_agy_home(home)
    record = tmp_path / "state" / f"{SLOT}.agy.json"
    agy_session.prepare(record, worktree, SLOT, fresh=True, route="local-tmux", client_version="1.5.14", home=home)
    _mark_writer_dead(record)

    target, _, _ = agy_session.prepare(
        record, worktree, SLOT, fresh=False, route="local-tmux", client_version="1.5.14", home=home
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
