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
    monkeypatch.setattr(main.tmux_binary, "command", lambda *_a, **_kw: ("tmux", "-S", tmux_socket))
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
    assert "[agy]" in result.output
    assert attached == [["tmux", "-S", tmux_socket, "attach-session", "-t", f"={SLOT}"]]


def test_given_generated_agy_supervisor_when_child_restarts_after_signout_then_token_reaches_first_child_only_and_second_reprobes(
    tmux_socket, tmp_path, monkeypatch
):
    import subprocess
    import time

    home = tmp_path / "home"
    home.mkdir()
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    # No antigravity-oauth-token file on disk: any launch without AI_CLI_AGY_SIGN_IN_VERIFIED
    # must run `agy models` to verify Google sign-in.
    token = agy_session.sign_in_cache_token("agy", home)
    events_log = tmp_path / "events.log"
    release_file = tmp_path / "release-child-1"
    script_path = tmp_path / "state" / "ai-cli-utils" / "sessions" / f"{SLOT}.sh"

    fake_ai = (
        "#!/bin/sh\n"
        f'if [ "$1" = internal ] && [ "$2" = agy-session-confirm ]; then exec {shlex.quote(sys.executable)} -m ai_cli.main "$@"; fi\n'
        f'if [ "$1" = sessions ] && [ "$2" = _sequence ]; then exec {shlex.quote(sys.executable)} -m ai_cli.main "$@"; fi\n'
        'if [ "$1" = internal ] && [ "$2" = get-version ]; then echo unknown; fi\n'
        "exit 0\n"
    )
    fake_agy = (
        f"#!{sys.executable}\n"
        "import json, os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        f"log = Path({str(events_log)!r})\n"
        f"release = Path({str(release_file)!r})\n"
        f"script = Path({str(script_path)!r})\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('1.5.14')\n"
        "    sys.exit(0)\n"
        "if sys.argv[1:] == ['models']:\n"
        "    with log.open('a', encoding='utf-8') as f:\n"
        "        f.write('probe:models\\n')\n"
        "    print('Error: Please sign in to view models.', file=sys.stderr)\n"
        "    sys.exit(1)\n"
        "started_at = time.monotonic()\n"
        "with log.open('a', encoding='utf-8') as f:\n"
        "    f.write(f\"launch:{os.environ.get('AI_CLI_AGY_SIGN_IN_VERIFIED', 'UNSET')}\\n\")\n"
        "# Confirm slot 1 via SessionStart hook, bump script mtime so child 1 triggers reload\n"
        "# on exit, and hold child 1 live until the test releases it after checking tmux env.\n"
        "payload = json.dumps({'hookEventName': 'SessionStart', 'conversationId': '11111111-2222-4333-8444-555555555555', 'cwd': os.getcwd()})\n"
        "subprocess.run(['ai', 'internal', 'agy-session-confirm'], input=payload, text=True, check=False)\n"
        "st = script.stat()\n"
        "os.utime(script, (st.st_atime + 20, st.st_mtime + 20))\n"
        "wait_deadline = time.monotonic() + 30.0\n"
        "while time.monotonic() < wait_deadline and not release.exists():\n"
        "    time.sleep(0.05)\n"
        "# Outlive the supervisor's 3s quick-failure guard so child 1 exits 78 and reloads.\n"
        "remaining = 3.5 - (time.monotonic() - started_at)\n"
        "if remaining > 0:\n"
        "    time.sleep(remaining)\n"
        "sys.exit(1)\n"
    )

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(agy_session.SIGN_IN_VERIFIED_ENV, raising=False)
    monkeypatch.setattr(
        "test_session_reload_loop.get_engine_script",
        lambda *_args, **_kw: get_engine_script(
            "g", "myproject-1", SLOT, "g-myproject-", "myproject", worktree_dir=str(worktree), agy_cmd="agy"
        ),
    )
    with legacy_state_functions():
        _start_session(
            tmux_socket,
            tmp_path,
            SLOT,
            rewrite_once_during_startup=False,
            extra_fakes={"ai": fake_ai, "agy": fake_agy},
            session_env={agy_session.SIGN_IN_VERIFIED_ENV: token},
        )

    # 1. Wait until child 1 has launched fake_agy (held live by release_file) and verify the
    #    session-scoped tmux env cleared AI_CLI_AGY_SIGN_IN_VERIFIED while child 1 is STILL LIVE.
    deadline = time.monotonic() + 25.0
    lines: list[str] = []
    try:
        while time.monotonic() < deadline:
            if events_log.is_file():
                lines = events_log.read_text(encoding="utf-8").splitlines()
                if "launch:UNSET" in lines:
                    break
            time.sleep(0.05)
        assert "launch:UNSET" in lines, f"child 1 never launched: {lines!r}; pane:\n{_pane_history(tmux_socket, SLOT)}"

        live_check = subprocess.run(
            ["tmux", "-S", tmux_socket, "has-session", "-t", f"={SLOT}"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert live_check.returncode == 0, "expected tmux session to be live during child 1"
        global_env = subprocess.run(
            ["tmux", "-S", tmux_socket, "show-environment", "-g", agy_session.SIGN_IN_VERIFIED_ENV],
            capture_output=True,
            text=True,
            check=False,
        )
        assert global_env.returncode != 0 or global_env.stdout.strip().startswith("-"), (
            f"expected {agy_session.SIGN_IN_VERIFIED_ENV} absent from server env, got rc={global_env.returncode} "
            f"stdout={global_env.stdout!r}"
        )
        env_res = subprocess.run(
            ["tmux", "-S", tmux_socket, "show-environment", "-t", f"={SLOT}", agy_session.SIGN_IN_VERIFIED_ENV],
            capture_output=True,
            text=True,
            check=False,
        )
        assert (env_res.returncode != 0 or env_res.stdout.strip().startswith("-")) and token not in env_res.stdout, (
            f"expected session-scoped {agy_session.SIGN_IN_VERIFIED_ENV} cleared while child 1 live, "
            f"got rc={env_res.returncode} stdout={env_res.stdout!r}"
        )
    finally:
        release_file.touch()

    # 2. Wait for replacement child 2 to re-probe `agy models` and refuse after sign-out.
    while time.monotonic() < deadline:
        if events_log.is_file():
            lines = events_log.read_text(encoding="utf-8").splitlines()
            if "probe:models" in lines:
                break
        time.sleep(0.1)

    assert lines == ["launch:UNSET", "probe:models"], (
        f"unexpected events={lines!r}; pane history:\n{_pane_history(tmux_socket, SLOT)}"
    )
    # Give the supervisor a moment to observe exit 76 and stop without launching child 2's agy.
    time.sleep(0.5)
    assert events_log.read_text(encoding="utf-8").splitlines() == ["launch:UNSET", "probe:models"]


def test_given_real_tmux_sessions_when_verifying_live_agy_marker_then_exact_slot_passes_and_prefix_or_unmarked_refuses(
    tmux_socket, tmp_path, monkeypatch
):
    import subprocess
    import time

    monkeypatch.setattr(main.tmux_binary, "command", lambda *_a, **_kw: ("tmux", "-S", tmux_socket))
    monkeypatch.setattr(main.tmux_binary, "argv", lambda *args, **_kw: ["tmux", "-S", tmux_socket, *args])
    ran_flag = tmp_path / "once-ran"

    # 1. Real tmux one-shot creation via _create_marked_agy_once_session: marks @ai_cli_client=agy and respawns pane.
    identity = main._create_marked_agy_once_session(
        "g-myproject-1",
        [],
        "/bin/sh",
        f"touch {shlex.quote(str(ran_flag))} && sleep 60",
    )
    assert identity.session_id.startswith("$")
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not ran_flag.is_file():
        time.sleep(0.05)
    assert ran_flag.is_file(), "expected respawned one-shot pane command to execute"

    # 2. Create prefix-colliding slot 10 marked non-agy and unmarked slot 2 on the same real tmux server.
    subprocess.run(
        ["tmux", "-S", tmux_socket, "new-session", "-d", "-s", "g-myproject-10", "--", "/bin/sh", "-c", "sleep 60"],
        check=True,
    )
    subprocess.run(
        ["tmux", "-S", tmux_socket, "set-option", "-t", "=g-myproject-10:", main._TMUX_CLIENT_OPTION, "legacy"],
        check=True,
    )
    subprocess.run(
        ["tmux", "-S", tmux_socket, "new-session", "-d", "-s", "g-myproject-2", "--", "/bin/sh", "-c", "sleep 60"],
        check=True,
    )

    # 3. _verify_live_agy_tmux_session uses =name: so real tmux 3.7c resolves exact slots cleanly.
    assert main._verify_live_agy_tmux_session("g-myproject-1") is None
    wrong = main._verify_live_agy_tmux_session("g-myproject-10")
    assert wrong is not None and "'legacy'" in wrong
    missing = main._verify_live_agy_tmux_session("g-myproject-2")
    assert missing is not None and "missing the @ai_cli_client=agy marker" in missing
    absent = main._verify_live_agy_tmux_session("g-myproject-999")
    assert absent is not None and "Relaunch with `ai g`" in absent


def test_given_tmux_server_restart_recycling_session_id_when_fenced_marker_or_respawn_runs_then_replacement_session_is_untouched(
    tmux_socket, tmp_path
):
    import subprocess
    import time

    from ai_cli import tmux_ownership

    tmux_cmd = ("tmux", "-S", tmux_socket)
    created = subprocess.run(
        [
            *tmux_cmd,
            "new-session",
            "-d",
            "-P",
            "-F",
            "#{session_id}",
            "-s",
            "g-myproject-1",
            "--",
            "/bin/sh",
            "-c",
            "sleep 60",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    orig_id = created.stdout.strip()
    subprocess.run(
        [*tmux_cmd, "set-option", "-t", orig_id, "@ai_cli_session_generation", "gen-original-1"],
        check=True,
    )
    stale_identity = tmux_ownership.capture_tmux_session_identity(
        orig_id, expected_generation="gen-original-1", tmux_command=tmux_cmd
    )
    assert stale_identity is not None

    # Simulate a tmux server restart that recycles the exact same opaque session id ($0) for an unrelated session.
    subprocess.run([*tmux_cmd, "kill-server"], check=True)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and Path(tmux_socket).exists():
        time.sleep(0.05)
    replacement = subprocess.run(
        [
            *tmux_cmd,
            "new-session",
            "-d",
            "-P",
            "-F",
            "#{session_id}",
            "-s",
            "unrelated-slot",
            "--",
            "/bin/sh",
            "-c",
            "sleep 60",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    replacement_id = replacement.stdout.strip()
    assert replacement_id == stale_identity.session_id

    orig_pane_pid = subprocess.run(
        [*tmux_cmd, "display-message", "-p", "-t", replacement_id, "#{pane_pid}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    # Neither marker write nor respawn-pane may touch the replacement session when its generation is absent or different.
    touched_flag = tmp_path / "replacement-respawned"
    for gen_value in (None, "gen-different-2"):
        if gen_value is not None:
            subprocess.run(
                [*tmux_cmd, "set-option", "-t", replacement_id, "@ai_cli_session_generation", gen_value],
                check=True,
            )
        assert (
            tmux_ownership.set_owned_tmux_option(stale_identity, main._TMUX_CLIENT_OPTION, "agy", tmux_command=tmux_cmd)
            is False
        )
        assert (
            tmux_ownership.respawn_owned_tmux_pane(
                stale_identity,
                ("/bin/sh", "-c", f"touch {shlex.quote(str(touched_flag))} && sleep 60"),
                tmux_command=tmux_cmd,
            )
            is False
        )
        time.sleep(0.1)
        marker = subprocess.run(
            [*tmux_cmd, "show-options", "-t", "=unrelated-slot:", "-qv", main._TMUX_CLIENT_OPTION],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert marker == ""
        assert not touched_flag.exists()
        current_pane_pid = subprocess.run(
            [*tmux_cmd, "display-message", "-p", "-t", replacement_id, "#{pane_pid}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert current_pane_pid == orig_pane_pid

    # Verify matching generation passes special characters (quotes, $, #, ;, backslash, newline) verbatim.
    live_identity = tmux_ownership.capture_tmux_session_identity(
        replacement_id, expected_generation="gen-different-2", tmux_command=tmux_cmd
    )
    assert live_identity is not None
    out_file = tmp_path / "quoted-payload.txt"
    payload = 'it\'s "$HOME" #{session_id}; a\\b\nline2'
    assert (
        tmux_ownership.respawn_owned_tmux_pane(
            live_identity,
            (
                sys.executable,
                "-c",
                "import pathlib, sys, time; pathlib.Path(sys.argv[1]).write_text(sys.argv[2], encoding='utf-8'); time.sleep(60)",
                str(out_file),
                payload,
            ),
            tmux_command=tmux_cmd,
        )
        is True
    )
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not (out_file.is_file() and out_file.stat().st_size > 0):
        time.sleep(0.05)
    assert out_file.read_text(encoding="utf-8") == payload


def test_given_remain_on_exit_server_when_once_agy_setup_fails_then_placeholder_session_is_cleaned_up(
    tmux_socket, monkeypatch
):
    import subprocess

    tmux_cmd = ("tmux", "-S", tmux_socket)
    monkeypatch.setattr(main.tmux_binary, "command", lambda *_a, **_kw: tmux_cmd)
    monkeypatch.setattr(main.tmux_binary, "argv", lambda *args, **_kw: [*tmux_cmd, *args])

    # Keep one unrelated session alive and enable global remain-on-exit on the real tmux server.
    subprocess.run(
        [*tmux_cmd, "new-session", "-d", "-s", "unrelated-slot", "--", "/bin/sh", "-c", "sleep 60"],
        check=True,
    )
    subprocess.run([*tmux_cmd, "set-option", "-g", "remain-on-exit", "on"], check=True)

    def _live_sessions() -> set[str]:
        res = subprocess.run(
            [*tmux_cmd, "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            check=True,
        )
        return {line.strip() for line in res.stdout.splitlines() if line.strip()}

    # 1. capture_tmux_session_identity returns None after generation stamp -> provisional fenced kill removes placeholder.
    real_capture = main._tmux_ownership.capture_tmux_session_identity
    monkeypatch.setattr(main._tmux_ownership, "capture_tmux_session_identity", lambda *_a, **_kw: None)
    with pytest.raises(RuntimeError, match="failed to establish ownership"):
        main._create_marked_agy_once_session("g-myproject-1", [], "/bin/sh", "sleep 60")
    assert _live_sessions() == {"unrelated-slot"}
    monkeypatch.setattr(main._tmux_ownership, "capture_tmux_session_identity", real_capture)

    # 2. set-option @ai_cli_session_generation fails -> unmarked placeholder kill removes placeholder under remain-on-exit.
    real_run = main.subprocess.run

    def fail_gen_set(cmd, *args, **kwargs):
        if isinstance(cmd, list) and "set-option" in cmd and "@ai_cli_session_generation" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", "simulated set-option failure")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(main.subprocess, "run", fail_gen_set)
    with pytest.raises(RuntimeError, match="failed to establish ownership"):
        main._create_marked_agy_once_session("g-myproject-2", [], "/bin/sh", "sleep 60")
    monkeypatch.setattr(main.subprocess, "run", real_run)
    assert _live_sessions() == {"unrelated-slot"}

    # 3. respawn_owned_tmux_pane fails -> owned session kill removes placeholder under remain-on-exit.
    real_respawn = main._tmux_ownership.respawn_owned_tmux_pane
    monkeypatch.setattr(main._tmux_ownership, "respawn_owned_tmux_pane", lambda *_a, **_kw: False)
    with pytest.raises(RuntimeError, match="failed to start Antigravity one-shot session"):
        main._create_marked_agy_once_session("g-myproject-3", [], "/bin/sh", "sleep 60")
    monkeypatch.setattr(main._tmux_ownership, "respawn_owned_tmux_pane", real_respawn)
    assert _live_sessions() == {"unrelated-slot"}


def test_given_real_tmux_manual_agy_pane_without_metadata_when_attaching_then_labels_agy_and_skips_plain_shell(
    tmux_socket, tmp_path, monkeypatch
):
    import shutil
    import subprocess
    import time

    from click.testing import CliRunner

    monkeypatch.setattr(main.tmux_binary, "command", lambda *_a, **_kw: ("tmux", "-S", tmux_socket))
    monkeypatch.setattr(main.tmux_binary, "argv", lambda *args, **_kw: ["tmux", "-S", tmux_socket, *args])
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(main._config, "get_xdg_state_home", lambda: state_dir)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_agy = fake_bin / "agy"
    sleep_bin = shutil.which("sleep")
    assert sleep_bin is not None
    shutil.copy2(sleep_bin, fake_agy)
    fake_agy.chmod(0o755)

    # Manually started g- tmux session running an executable named agy, with NO metadata or @ai_cli_client marker.
    subprocess.run(
        ["tmux", "-S", tmux_socket, "new-session", "-d", "-s", "g-myproject-manual", "--", str(fake_agy), "60"],
        check=True,
    )
    # Manually started g- tmux session running a plain shell command.
    subprocess.run(
        ["tmux", "-S", tmux_socket, "new-session", "-d", "-s", "g-myproject-shell", "--", "/bin/sh", "-c", "sleep 60"],
        check=True,
    )

    # Wait briefly for tmux panes to initialize before probing.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and main._g_session_client("g-myproject-manual") != "agy":
        time.sleep(0.05)

    assert main._g_session_client("g-myproject-manual") == "agy"
    assert main._g_session_client("g-myproject-shell") is None

    attach_calls: list[list[str]] = []
    monkeypatch.setattr(main, "_resume_suspended_panes", lambda _name: None)
    monkeypatch.setattr(main, "_exec_tmux", lambda argv: (attach_calls.append(list(argv)), sys.exit(0)))

    manual_res = CliRunner().invoke(main._cli_group, ["attach", "g-myproject-manual"])
    assert manual_res.exit_code == 0, manual_res.output
    assert "Attaching to g-myproject-manual [agy]" in manual_res.output

    shell_res = CliRunner().invoke(main._cli_group, ["attach", "g-myproject-shell"])
    assert shell_res.exit_code == 0, shell_res.output
    assert "[agy]" not in shell_res.output
    assert "[legacy gemini]" not in shell_res.output
