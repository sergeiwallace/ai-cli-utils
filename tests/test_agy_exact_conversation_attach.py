"""Local tmux replay for Antigravity (agy) slot binding, exact resume, list, and attach."""

from __future__ import annotations

import json
import os
import shlex
import sqlite3
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from legacy_run_state import legacy_state_functions
from test_session_reload_loop import _pane_history, _start_session, _wait_for_agent_start
from test_session_reload_loop import tmux_socket as tmux_socket

from ai_cli import agy_session, main
from ai_cli.session_script import get_engine_script

pytestmark = [pytest.mark.real_tmux, pytest.mark.skipif(os.name != "posix", reason="POSIX tmux launcher")]

CONV_OLD = "11111111-2222-4333-8444-555555555555"
CONV_NEW = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
SLOT = "g-myproject-1"


def _seed_store(home: Path, worktree: Path, rows: list[tuple[str, str]]) -> None:
    store = home / ".gemini" / "antigravity-cli"
    store.mkdir(parents=True, exist_ok=True)
    (store / "antigravity-oauth-token").write_text("placeholder", encoding="utf-8")
    db = store / "conversation_summaries.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS conversation_summaries "
            "(conversation_id TEXT PRIMARY KEY, workspace_uris TEXT NOT NULL, title TEXT NOT NULL DEFAULT '')"
        )
        for conv_id, title in rows:
            conn.execute(
                "INSERT OR REPLACE INTO conversation_summaries (conversation_id, workspace_uris, title) VALUES (?, ?, ?)",
                (conv_id, json.dumps([worktree.resolve().as_uri()]), title),
            )
        conn.commit()
    finally:
        conn.close()


def test_given_two_conversations_in_worktree_when_agy_slot_resumes_then_attach_and_list_keep_exact_id(
    tmux_socket, tmp_path, monkeypatch
):
    home = tmp_path / "home"
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    _seed_store(home, worktree, [(CONV_OLD, "slot-1"), (CONV_NEW, "newer-other")])

    record = tmp_path / "preconfirmed.agy.json"
    agy_session.prepare(
        record,
        worktree,
        SLOT,
        fresh=False,
        route="local-tmux",
        client_version="1.5.14",
        adopt_conversation=(CONV_OLD, SLOT),
        home=home,
    )
    doc = json.loads(record.read_text(encoding="utf-8"))
    doc["in_flight"]["writer"]["pid"] = 999_999_999
    record.write_text(json.dumps(doc), encoding="utf-8")

    observed = tmp_path / "observed.json"
    state_record = tmp_path / "state" / "ai-cli-utils" / "sessions" / f"{SLOT}.agy.json"
    fake_ai = (
        "#!/bin/sh\n"
        f'if [ "$1" = internal ] && [ "$2" = agy-session-confirm ]; then exec {shlex.quote(sys.executable)} -m ai_cli.main "$@"; fi\n'
        f'if [ "$1" = sessions ] && [ "$2" = _sequence ]; then exec {shlex.quote(sys.executable)} -m ai_cli.main "$@"; fi\n'
        f'if [ "$1" = ps ] && [ "$2" = cron ]; then mkdir -p {shlex.quote(str(state_record.parent))} && cp {shlex.quote(str(record))} {shlex.quote(str(state_record))}; fi\n'
        'if [ "$1" = internal ] && [ "$2" = get-version ]; then echo unknown; fi\n'
        "exit 0\n"
    )
    fake_agy = (
        f"#!{sys.executable}\n"
        "import json,os,sys,time\nfrom pathlib import Path\n"
        "if sys.argv[1:] == ['--version']:\n print('1.5.14'); sys.exit(0)\n"
        "args = sys.argv[1:]\n"
        "conv = args[args.index('--conversation') + 1] if '--conversation' in args else None\n"
        f"Path({str(observed)!r}).write_text(json.dumps({{'argv': args, 'conversation_id': conv, 'session_name': os.environ.get('AI_SESSION_NAME'), 'task_list': os.environ.get('CLAUDE_CODE_TASK_LIST_ID'), 'api_key': os.environ.get('GEMINI_API_KEY')}}))\n"
        f"Path({str(tmp_path / 'started')!r}).write_text(str(os.getpid()))\n"
        "time.sleep(300)\n"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GEMINI_API_KEY", "must-be-stripped")
    monkeypatch.setattr(
        "test_session_reload_loop.get_engine_script",
        lambda *_args, **_kw: get_engine_script(
            "g", "myproject-1", SLOT, "g-myproject-", "myproject", worktree_dir=str(worktree), agy_cmd="agy"
        ),
    )
    with legacy_state_functions():
        started = _start_session(
            tmux_socket,
            tmp_path,
            SLOT,
            rewrite_once_during_startup=False,
            extra_fakes={"ai": fake_ai, "agy": fake_agy},
        )
    assert _wait_for_agent_start(tmux_socket, SLOT, started), _pane_history(tmux_socket, SLOT)
    data = json.loads(observed.read_text(encoding="utf-8"))
    assert data["conversation_id"] == CONV_OLD
    assert CONV_NEW not in data["argv"]
    assert data["session_name"] == "myproject-1"
    assert data["task_list"] == "myproject-1"
    assert data["api_key"] is None

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(main.tmux_binary, "argv", lambda *args, **_kw: ["tmux", "-S", tmux_socket, *args])
    real_which = main.shutil.which
    monkeypatch.setattr(
        main.shutil, "which", lambda name, *a, **kw: None if name == "fzf" else real_which(name, *a, **kw)
    )
    listed = CliRunner().invoke(main._cli_group, ["ls"])
    assert listed.exit_code == 0, listed.output
    assert SLOT in listed.output
    assert "[agy]" in listed.output

    attached = []
    monkeypatch.setattr(main, "_resume_suspended_panes", lambda _name: None)
    monkeypatch.setattr(main, "_exec_tmux", lambda argv: attached.append(argv))
    result = CliRunner().invoke(main._cli_group, ["attach", SLOT])
    assert result.exit_code == 0, result.output
    assert attached == [["tmux", "-S", tmux_socket, "attach-session", "-t", SLOT]]
