"""`ai cx` names its Codex session once and resumes it by name; `ai p` continues its Pi session.

Design: docs/designs/codex-session-naming.md.

The session script's dispatch is run for real under bash with the agents stubbed as
shell functions. The resolver and the namer are driven through ``ai internal`` against
a real Codex index on disk; tmux is a stand-in that plays Codex's part, appending the
index entry when the ``/rename`` is submitted, as Codex 0.169.0 was measured to do.
"""

import json
import os
import select
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_cli.main import _bare_engine_command, _handle_internal
from ai_cli.session_script import get_engine_script

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the session script and tmux are POSIX-only")

NAME = "myproject-1"
THREAD = "01a1e138-42c6-7643-af6a-48768d55c843"
OLDER = "01a1e100-0000-7000-8000-000000000001"
READY_PANE = "› Ask Codex to do anything\n  model default · /work · \n  ← for agents · ? for shortcuts\n"
LOADING_PANE = "  >_ OpenAI Codex\n     loading\n› \n"


# --- Codex's own store ------------------------------------------------------------


def _index(home: Path, *entries: tuple[str, str]) -> None:
    home.mkdir(parents=True, exist_ok=True)
    with (home / "session_index.jsonl").open("a", encoding="utf-8") as handle:
        for thread_id, name in entries:
            handle.write(
                json.dumps({"id": thread_id, "thread_name": name, "updated_at": "2026-11-15T00:00:00Z"}) + "\n"
            )


def _rollout(home: Path, thread_id: str, suffix: str = ".jsonl") -> Path:
    day = home / "sessions" / "2026" / "11" / "15"
    day.mkdir(parents=True, exist_ok=True)
    path = day / f"rollout-2026-11-15T16-58-43-{thread_id}{suffix}"
    path.write_text('{"type":"session_meta"}\n', encoding="utf-8")
    return path


@pytest.fixture
def codex_home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    return home


def _resolve(capsys) -> str:
    with patch("ai_cli.config.load_config", return_value={}), pytest.raises(SystemExit) as exc_info:
        _handle_internal(["resolve-codex-session", NAME])
    assert exc_info.value.code == 0
    return capsys.readouterr().out.strip()


# --- resolver ---------------------------------------------------------------------


def test_given_thread_renamed_before_any_turn_when_resolving_then_prints_its_id(codex_home, capsys):
    # The owner's case: /rename only, then exit. Codex's own `resume <name>` and picker
    # skip this thread; resuming it by id works.
    _index(codex_home, (THREAD, NAME))
    _rollout(codex_home, THREAD)

    assert _resolve(capsys) == THREAD


def test_given_no_thread_carries_the_name_when_resolving_then_prints_nothing(codex_home, capsys):
    _index(codex_home, (THREAD, "myproject-2"))
    _rollout(codex_home, THREAD)

    assert _resolve(capsys) == ""


def test_given_thread_later_renamed_to_another_name_when_resolving_then_prints_nothing(codex_home, capsys):
    _index(codex_home, (THREAD, NAME), (THREAD, "something-else"))
    _rollout(codex_home, THREAD)

    assert _resolve(capsys) == ""


def test_given_named_thread_whose_rollout_is_gone_when_resolving_then_prints_nothing(codex_home, capsys):
    _index(codex_home, (THREAD, NAME))

    assert _resolve(capsys) == ""


def test_given_named_thread_with_a_compressed_rollout_when_resolving_then_prints_its_id(codex_home, capsys):
    _index(codex_home, (THREAD, NAME))
    _rollout(codex_home, THREAD, ".jsonl.zst")

    assert _resolve(capsys) == THREAD


def test_given_two_threads_with_the_name_when_resolving_then_prints_the_newest(codex_home, capsys):
    _index(codex_home, (OLDER, NAME), (THREAD, NAME))
    _rollout(codex_home, OLDER)
    _rollout(codex_home, THREAD)

    assert _resolve(capsys) == THREAD


def test_given_newest_named_thread_never_saved_when_resolving_then_falls_back_to_the_saved_one(codex_home, capsys):
    _index(codex_home, (OLDER, NAME), (THREAD, NAME))
    _rollout(codex_home, OLDER)

    assert _resolve(capsys) == OLDER


# --- namer ------------------------------------------------------------------------


@pytest.fixture
def fake_codex_tmux(monkeypatch, codex_home):
    """tmux as the pane of a Codex TUI: it records keys and records the rename on Enter."""
    state: dict = {"panes": [READY_PANE], "keys": [], "typed": "", "records": True, "pastes": []}
    real_run = subprocess.run

    def run(command, *args, **kwargs):
        if Path(str(command[0])).name != "tmux":
            return real_run(command, *args, **kwargs)
        verb = command[1]
        if verb == "capture-pane":
            pane = state["panes"].pop(0) if len(state["panes"]) > 1 else state["panes"][0]
            return subprocess.CompletedProcess(command, 0, pane, "")
        if verb == "send-keys":
            key = command[-1]
            state["keys"].append(key)
            if "-l" in command:
                state["typed"] += key
            elif key == "Enter":
                if state["typed"].startswith("/rename ") and state["records"]:
                    _index(codex_home, (THREAD, state["typed"].removeprefix("/rename ")))
                state["typed"] = ""
            return subprocess.CompletedProcess(command, 0, "", "")
        if verb == "load-buffer":
            state["pastes"].append(Path(command[-1]).read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: f"/usr/bin/{name}")
    monkeypatch.setattr("ai_cli.codex_session.time.sleep", lambda _s: None)
    monkeypatch.setattr("ai_cli.background_attach.time.sleep", lambda _s: None)
    return state


def _name(capsys, *extra: str) -> tuple[int, str]:
    with patch("ai_cli.config.load_config", return_value={}), pytest.raises(SystemExit) as exc_info:
        _handle_internal(["name-codex-session", "cx-myproject-1", NAME, *extra])
    return exc_info.value.code, capsys.readouterr().err


def test_given_new_codex_session_when_naming_then_types_rename_and_codex_records_the_name(
    fake_codex_tmux, codex_home, capsys
):
    code, err = _name(capsys)

    assert code == 0, err
    assert fake_codex_tmux["keys"] == [f"/rename {NAME}", "Enter"]
    _rollout(codex_home, THREAD)
    assert _resolve(capsys) == THREAD


def test_given_codex_still_loading_when_naming_then_types_only_after_its_idle_footer(fake_codex_tmux, capsys):
    fake_codex_tmux["panes"] = [LOADING_PANE, LOADING_PANE, READY_PANE]

    code, err = _name(capsys)

    assert code == 0, err
    assert fake_codex_tmux["panes"] == [READY_PANE]
    assert fake_codex_tmux["keys"] == [f"/rename {NAME}", "Enter"]


def test_given_codex_never_becomes_ready_when_naming_then_types_nothing_and_says_why(fake_codex_tmux, capsys):
    fake_codex_tmux["panes"] = [LOADING_PANE]

    with patch("ai_cli.codex_session.READY_WAIT_SECONDS", 0.0):
        code, err = _name(capsys)

    assert code == 1
    assert fake_codex_tmux["keys"] == []
    assert "not named" in err and f"/rename {NAME}" in err


def test_given_codex_does_not_record_the_name_when_naming_then_reports_it_unconfirmed(fake_codex_tmux, capsys):
    fake_codex_tmux["records"] = False

    with patch("ai_cli.codex_session.CONFIRM_WAIT_SECONDS", 0.0):
        code, err = _name(capsys)

    assert code == 1
    assert "did not record the name" in err


def test_given_resume_prompt_when_naming_a_new_session_then_pastes_it_after_the_name_is_recorded(
    fake_codex_tmux, tmp_path, capsys
):
    prompt = tmp_path / "prompt.codex"
    prompt.write_text("continue the task\n", encoding="utf-8")

    code, err = _name(capsys, "--prompt-file", str(prompt))

    assert code == 0, err
    assert fake_codex_tmux["keys"][:2] == [f"/rename {NAME}", "Enter"]
    assert fake_codex_tmux["pastes"] == ["continue the task"]
    assert not prompt.exists()


def test_given_bare_pty_submission_fifo_when_naming_then_writes_rename_and_enter_into_it(codex_home, tmp_path, capsys):
    fifo = tmp_path / "submit.fifo"
    os.mkfifo(fifo)
    # The pty host opens its FIFO like this and holds it for the whole session.
    read_fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    received: list[bytes] = []

    def codex_reading_the_pty() -> None:
        data = b""
        while not data.endswith(b"\r"):
            ready, _, _ = select.select([read_fd], [], [], 5)
            if not ready:
                break
            data += os.read(read_fd, 64)
        received.append(data)
        _index(codex_home, (THREAD, data.decode().removeprefix("/rename ").removesuffix("\r")))

    reader = threading.Thread(target=codex_reading_the_pty, daemon=True)
    reader.start()
    try:
        with (
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.codex_session.FIFO_START_SECONDS", 0.0),
            patch("ai_cli.codex_session.SUBMIT_SETTLE_SECONDS", 0.0),
            pytest.raises(SystemExit) as exc_info,
        ):
            _handle_internal(["name-codex-session", "-f", str(fifo), NAME])
        reader.join(timeout=5)
    finally:
        os.close(read_fd)

    assert exc_info.value.code == 0, capsys.readouterr().err
    assert received == [f"/rename {NAME}\r".encode()]


# --- session script dispatch (tmux launches, local and -R) -------------------------


def _dispatch_block(engine: str) -> str:
    script = get_engine_script(
        engine=engine,
        ai_name=NAME,
        session=f"{engine}-{NAME}",
        prefix=f"{engine}-myproject-",
        project_prefix="myproject",
    )
    start = script.index("_ai_run_codex() {")
    # The Claude-only attach branch opens before the Codex dispatch and closes after it.
    end = script.index("# END BACKGROUND ATTACH", script.index("# BEGIN BACKGROUND ATTACH prompted-end"))
    return script[start:end]


def _run_dispatch(tmp_path: Path, engine: str, *, first_run: bool, thread: str = "", prompt: str = "") -> list[str]:
    log = tmp_path / "calls.log"
    prompt_file = tmp_path / "resume-prompt"
    if prompt:
        prompt_file.write_text(prompt, encoding="utf-8")
    harness = tmp_path / "dispatch.sh"
    harness.write_text(
        f"""
engine={engine}
ai_name={NAME}
tmux_session={engine}-{NAME}
first_run={"true" if first_run else "false"}
prompt_file={prompt_file}
_ai_launch_log=""
resolve_status=1
matched_file=""
bg_target=""
claude_perms_flag=""
run_agent() {{ printf 'RUN %s\\n' "$*" >> {log}; }}
ai() {{
  printf 'AI %s\\n' "$*" >> {log}
  if [[ "$1 $2" == "internal resolve-codex-session" && -n "{thread}" ]]; then echo "{thread}"; fi
  return 0
}}
tmux() {{ printf 'TMUX %s\\n' "$*" >> {log}; }}
sleep() {{ :; }}
{_dispatch_block(engine)}
wait
""",
        encoding="utf-8",
    )
    done = subprocess.run(["bash", str(harness)], capture_output=True, text=True, timeout=30, check=False)
    assert done.returncode == 0, done.stderr
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_given_named_codex_thread_when_relaunching_ai_cx_then_resumes_that_thread_without_renaming(tmp_path):
    # The owner's relaunch: a new tmux session, so first_run is true.
    calls = _run_dispatch(tmp_path, "cx", first_run=True, thread=THREAD)

    assert f"RUN codex resume {THREAD}" in calls
    assert not any("name-codex-session" in call for call in calls)


def test_given_no_named_codex_thread_when_launching_ai_cx_then_starts_codex_and_names_it(tmp_path):
    calls = _run_dispatch(tmp_path, "cx", first_run=True)

    assert "RUN codex" in calls
    assert f"AI internal name-codex-session cx-{NAME} {NAME}" in calls


def test_given_in_loop_restart_with_no_named_thread_when_relaunching_then_resumes_last_and_names_it(tmp_path):
    calls = _run_dispatch(tmp_path, "cx", first_run=False)

    assert "RUN codex resume --last" in calls
    assert f"AI internal name-codex-session cx-{NAME} {NAME}" in calls


def test_given_resume_prompt_and_named_thread_when_relaunching_then_prompt_rides_on_the_resume(tmp_path):
    calls = _run_dispatch(tmp_path, "cx", first_run=True, thread=THREAD, prompt="continue the task")

    assert f"RUN codex resume {THREAD} continue the task" in calls
    assert not any(call.startswith("TMUX send-keys") for call in calls)


def test_given_resume_prompt_and_no_named_thread_when_launching_then_namer_delivers_it_after_naming(tmp_path):
    calls = _run_dispatch(tmp_path, "cx", first_run=True, prompt="continue the task")

    assert "RUN codex" in calls
    namer = [call for call in calls if "name-codex-session" in call]
    assert namer == [f"AI internal name-codex-session cx-{NAME} {NAME} --prompt-file {tmp_path}/resume-prompt.codex"]
    assert (tmp_path / "resume-prompt.codex").read_text(encoding="utf-8") == "continue the task"
    assert not any(call.startswith("TMUX send-keys") for call in calls)


def test_given_existing_pi_session_when_relaunching_ai_p_then_continues_it(tmp_path):
    # AI-CLI-tc25: a relaunch is a new tmux session, so first_run is true.
    calls = _run_dispatch(tmp_path, "p", first_run=True)

    assert f"RUN pi --continue --provider openai-codex --name {NAME}" in calls


# --- bare launches ------------------------------------------------------------------


def test_given_named_codex_thread_when_bare_launching_ai_cx_then_resumes_that_thread(codex_home):
    _index(codex_home, (THREAD, NAME))
    _rollout(codex_home, THREAD)

    command = _bare_engine_command("cx", NAME, Path.cwd(), None, "gemini", "--no-sandbox", [])

    assert command == ["codex", "resume", THREAD]


def test_given_pi_bare_launch_without_resume_flag_when_launching_then_continues_its_session():
    command = _bare_engine_command("p", NAME, Path.cwd(), None, "gemini", "--no-sandbox", [])

    assert command == ["pi", "--continue", "--provider", "openai-codex", "--name", NAME]
