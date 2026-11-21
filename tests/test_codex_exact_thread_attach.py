"""Local tmux collision replay; the native client is a credential-free stand-in."""

import json
import os
import shlex
import sys

import pytest
from click.testing import CliRunner
from legacy_run_state import legacy_state_functions
from test_codex_exact_thread import NEWER, SLOT, THREAD
from test_codex_exact_thread import collision as collision
from test_session_reload_loop import _pane_history, _start_session, _wait_for_agent_start
from test_session_reload_loop import tmux_socket as tmux_socket

from ai_cli import main
from ai_cli.session_script import get_engine_script

pytestmark = [pytest.mark.real_tmux, pytest.mark.skipif(os.name != "posix", reason="POSIX tmux launcher")]


def test_given_same_directory_collision_when_local_slot_resumes_then_attach_and_list_keep_its_identity(
    collision, tmux_socket, tmp_path, monkeypatch
):
    worktree, _, record = collision
    observed = tmp_path / "observed.json"
    rollout = json.loads(record.read_text())["rollout_path"]
    state_record = tmp_path / "state" / "ai-cli-utils" / "sessions" / f"{SLOT}.codex.json"
    fake_ai = (
        "#!/bin/sh\n"
        f'if [ "$1" = sessions ] && [ "$2" = _sequence ]; then exec {shlex.quote(sys.executable)} -m ai_cli.main "$@"; fi\n'
        f'if [ "$1" = ps ] && [ "$2" = cron ]; then cp {shlex.quote(str(record))} {shlex.quote(str(state_record))}; fi\n'
        'if [ "$1" = internal ] && [ "$2" = get-version ]; then echo unknown; fi\n'
        "exit 0\n"
    )
    fake_codex = (
        f"#!{sys.executable}\n"
        "import json,os,shlex,subprocess,sys,time,tomllib\nfrom pathlib import Path\n"
        "if sys.argv[1:] == ['--version']:\n print('codex-cli 0.169.0'); sys.exit(0)\n"
        "args=sys.argv[1:]\n"
        "config=tomllib.loads(args[args.index('-c')+1])\n"
        "command=config['hooks']['SessionStart'][0]['hooks'][0]['command']\n"
        "thread=args[args.index('resume')+1]\n"
        f"event={{'hook_event_name':'SessionStart','session_id':thread,'cwd':os.getcwd(),'transcript_path':{rollout!r}}}\n"
        "subprocess.run(shlex.split(command),input=json.dumps(event),text=True,check=True)\n"
        f"Path({str(observed)!r}).write_text(json.dumps({{'argv':args,'thread_id':thread}}))\n"
        f"Path({str(tmp_path / 'started')!r}).write_text(str(os.getpid()))\n"
        "time.sleep(300)\n"
    )
    monkeypatch.setattr(
        "test_session_reload_loop.get_engine_script",
        lambda *_args, **_kw: get_engine_script(
            "cx", "myproject-1", SLOT, "cx-myproject-", "myproject", worktree_dir=str(worktree)
        ),
    )
    with legacy_state_functions():
        started = _start_session(
            tmux_socket,
            tmp_path,
            SLOT,
            rewrite_once_during_startup=False,
            extra_fakes={"ai": fake_ai, "codex": fake_codex},
        )
    assert _wait_for_agent_start(tmux_socket, SLOT, started), _pane_history(tmux_socket, SLOT)
    assert json.loads(observed.read_text())["thread_id"] == THREAD
    assert NEWER not in json.loads(observed.read_text())["argv"]
    confirmed = json.loads(state_record.read_text())
    assert confirmed["phase"] == "confirmed" and confirmed["slot"] == SLOT

    monkeypatch.setattr(main.tmux_binary, "argv", lambda *args, **_kw: ["tmux", "-S", tmux_socket, *args])
    real_which = main.shutil.which
    monkeypatch.setattr(
        main.shutil, "which", lambda name, *a, **kw: None if name == "fzf" else real_which(name, *a, **kw)
    )
    listed = CliRunner().invoke(main._cli_group, ["ls"])
    assert listed.exit_code == 0, listed.output
    assert SLOT in listed.output
    attached = []
    monkeypatch.setattr(main, "_resume_suspended_panes", lambda _name: None)
    monkeypatch.setattr(main, "_exec_tmux", lambda argv: attached.append(argv))
    # Live attach does not require persisted proof; it reaches the same pane even
    # when the native store is unavailable, rather than starting another thread.
    state_record.write_text("unreadable identity metadata\n")
    result = CliRunner().invoke(main._cli_group, ["attach", SLOT])
    assert result.exit_code == 0, result.output
    assert attached == [["tmux", "-S", tmux_socket, "attach-session", "-t", SLOT]]
    assert json.loads(observed.read_text())["thread_id"] == THREAD
