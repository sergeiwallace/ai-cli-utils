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

# The resolver, the tmux namer and the bare builder run on every OS. The session script
# runs under bash inside tmux, and a submission FIFO is a POSIX pty-host feature.
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="bash dispatch and FIFOs are POSIX-only")

NAME = "myproject-1"
THREAD = "01a1e138-42c6-7643-af6a-48768d55c843"
OLDER = "01a1e100-0000-7000-8000-000000000001"
READY_PANE = "› Ask Codex to do anything\n  model default · /work · \n  ← for agents · ? for shortcuts\n"
LOADING_PANE = "  >_ OpenAI Codex\n     loading\n› \n"
PANE = "%42"


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


def _name(capsys, *extra: str) -> tuple[object, str]:
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


def test_given_codex_records_the_name_only_after_a_slow_startup_when_naming_then_it_is_confirmed(
    fake_codex_tmux, codex_home, capsys, monkeypatch
):
    # Measured live: on a first start the submitted /rename waited behind "Waiting for
    # startup" and landed 28 s after launch, past the namer's old 10 s window.
    fake_codex_tmux["records"] = False
    clock = {"now": 0.0}

    def sleep(seconds: float) -> None:
        clock["now"] += seconds
        if clock["now"] >= 30 and not (codex_home / "session_index.jsonl").exists():
            _index(codex_home, (THREAD, NAME))

    monkeypatch.setattr("ai_cli.codex_session.time.sleep", sleep)
    monkeypatch.setattr("ai_cli.codex_session.time.monotonic", lambda: clock["now"])

    code, err = _name(capsys)

    assert code == 0, err


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


@posix_only
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
    start = script.index("_ai_run_agy() {")
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
TMUX_PANE={PANE}
tmux_session={engine}-{NAME}
first_run={"true" if first_run else "false"}
prompt_file={prompt_file}
_ai_launch_log=""
_ai_state_dir={tmp_path}/state
agent_direnv_initialized=true
resolve_status=1
matched_file=""
bg_target=""
claude_perms_flag=""
run_agent() {{
  printf 'RUN %s\\n' "$*" >> {log}
  if [[ -n "${{AI_CLI_CODEX_PROMPT_FILE:-}}" ]]; then printf 'PROMPT %s\\n' "$(cat "$AI_CLI_CODEX_PROMPT_FILE")" >> {log}; fi
}}
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


def _launches(calls: list[str]) -> list[str]:
    return [call for call in calls if call.startswith("RUN ")]


@posix_only
def test_given_named_codex_thread_when_relaunching_ai_cx_then_launches_the_slot_without_renaming(tmp_path):
    # The owner's relaunch: a new tmux session, so first_run is true.
    calls = _run_dispatch(tmp_path, "cx", first_run=True, thread=THREAD)

    [launch] = _launches(calls)
    assert f"-m ai_cli.codex_session launch {tmp_path}/state/sessions/cx-{NAME}.codex.json" in launch
    assert launch.endswith(f" cx-{NAME} true local-tmux")
    assert not any("name-codex-session" in call for call in calls)


@posix_only
def test_given_no_named_codex_thread_when_launching_ai_cx_then_starts_codex_and_names_it(tmp_path):
    calls = _run_dispatch(tmp_path, "cx", first_run=True)

    assert len(_launches(calls)) == 1
    assert f"AI internal name-codex-session cx-{NAME} {NAME}" in calls


@posix_only
def test_given_in_loop_restart_with_no_named_thread_when_relaunching_then_names_it(tmp_path):
    calls = _run_dispatch(tmp_path, "cx", first_run=False)

    [launch] = _launches(calls)
    assert launch.endswith(f" cx-{NAME} false local-tmux")
    assert f"AI internal name-codex-session cx-{NAME} {NAME}" in calls


@posix_only
def test_given_resume_prompt_and_named_thread_when_relaunching_then_prompt_rides_on_the_launch(tmp_path):
    calls = _run_dispatch(tmp_path, "cx", first_run=True, thread=THREAD, prompt="continue the task")

    assert "PROMPT continue the task" in calls
    assert not any("name-codex-session" in call for call in calls)
    assert not any(call.startswith("TMUX send-keys") for call in calls)


@posix_only
def test_given_resume_prompt_and_no_named_thread_when_launching_then_namer_delivers_it_after_naming(tmp_path):
    calls = _run_dispatch(tmp_path, "cx", first_run=True, prompt="continue the task")

    assert len(_launches(calls)) == 1
    assert not any(call.startswith("PROMPT") for call in calls)
    namer = [call for call in calls if "name-codex-session" in call]
    assert namer == [f"AI internal name-codex-session cx-{NAME} {NAME} --prompt-file {tmp_path}/resume-prompt.codex"]
    assert (tmp_path / "resume-prompt.codex").read_text(encoding="utf-8") == "continue the task"
    assert not any(call.startswith("TMUX send-keys") for call in calls)


@posix_only
def test_given_existing_pi_session_when_relaunching_ai_p_then_continues_it(tmp_path):
    # AI-CLI-tc25: a relaunch is a new tmux session, so first_run is true.
    calls = _run_dispatch(tmp_path, "p", first_run=True)

    assert f"RUN pi --continue --provider openai-codex --name {NAME}" in calls


# --- the slot launch: which thread, which flags, which prompt ---------------------


HOOK = 'hooks.SessionStart=[{hooks=[{type="command",command="ai internal codex-session-confirm"}]}]'


def _owned_rollout(home: Path, thread_id: str, cwd: Path) -> Path:
    path = _rollout(home, thread_id)
    path.write_text(json.dumps({"type": "session_meta", "payload": {"id": thread_id, "cwd": str(cwd)}}) + "\n")
    return path


@pytest.fixture
def slot_launch(tmp_path, monkeypatch, codex_home):
    """Run ``codex_session launch`` up to its exec, against a fake Codex on PATH."""
    from ai_cli import codex_control, codex_session
    from ai_cli import main as main_module

    worktree = tmp_path / "myproject"
    worktree.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    codex = bin_dir / "codex"
    codex.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = --version ]; then echo "codex-cli 0.169.0"; exit 0; fi\n'
        'if [ "$1" = --help ]; then echo "      --no-daemon"; exit 0; fi\n',
        encoding="utf-8",
    )
    codex.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.setenv(codex_session.SESSION_NAME_ENV, NAME)
    # The launch sets its slot identity in this process's environment before the exec.
    for variable in (codex_session.PROMPT_FILE_ENV, codex_session.SLOT_RECORD_ENV, codex_session.LAUNCH_TOKEN_ENV):
        monkeypatch.setenv(variable, "")
        monkeypatch.delenv(variable)
    monkeypatch.setattr(codex_control, "start", lambda _record: None)
    execs: list[list[str]] = []
    monkeypatch.setattr(main_module, "_exec_handoff", lambda _file, argv: execs.append(argv))

    def launch(*, fresh: bool = True, route: str = "local-tmux") -> tuple[int, list[str]]:
        record = tmp_path / "state" / f"cx-{NAME}.codex.json"
        code = codex_session.main(["launch", str(record), str(worktree), f"cx-{NAME}", str(fresh).lower(), route])
        return code, execs[-1] if execs else []

    return worktree, launch


@posix_only
def test_given_session_named_by_hand_in_codex_before_slots_when_relaunching_with_remote_then_resumes_it_by_id(
    slot_launch, codex_home
):
    # Amendment 4: the owner ran /rename <name> inside Codex on the remote host, used it,
    # then relaunched with `ai cx <name> -R`. The launcher never recorded that name or a
    # slot; only Codex's own index holds it, and the remote session script runs on that host.
    worktree, launch = slot_launch
    _index(codex_home, (OLDER, "myproject-2"), (THREAD, NAME))
    _owned_rollout(codex_home, THREAD, worktree)
    _owned_rollout(codex_home, OLDER, worktree)

    code, argv = launch(route="paired-host-tmux")

    assert code == 0
    assert argv == ["codex", "-c", HOOK, "resume", "--no-daemon", THREAD]


@posix_only
def test_given_named_thread_of_another_directory_when_slot_has_no_record_then_the_launch_is_refused(
    slot_launch, codex_home, tmp_path
):
    _, launch = slot_launch
    _index(codex_home, (THREAD, NAME))
    _owned_rollout(codex_home, THREAD, tmp_path / "elsewhere")

    code, argv = launch(fresh=False)

    assert code == 76
    assert argv == []


@posix_only
def test_given_new_session_when_slot_launches_then_codex_runs_its_own_process_without_resuming(slot_launch):
    _, launch = slot_launch

    code, argv = launch()

    assert code == 0
    assert argv == ["codex", "-c", HOOK, "--no-daemon"]


@posix_only
def test_given_resume_prompt_file_when_slot_launches_then_codex_gets_the_prompt_and_the_file_is_gone(
    slot_launch, codex_home, tmp_path, monkeypatch
):
    from ai_cli import codex_session

    worktree, launch = slot_launch
    _index(codex_home, (THREAD, NAME))
    _owned_rollout(codex_home, THREAD, worktree)
    prompt = tmp_path / "resume-prompt.codex"
    prompt.write_text("continue the task", encoding="utf-8")
    monkeypatch.setenv(codex_session.PROMPT_FILE_ENV, str(prompt))

    code, argv = launch()

    assert code == 0
    assert argv == ["codex", "-c", HOOK, "resume", "--no-daemon", THREAD, "continue the task"]
    assert not prompt.exists()
    assert codex_session.PROMPT_FILE_ENV not in os.environ


# --- bare launches ------------------------------------------------------------------


def test_given_named_codex_thread_when_bare_launching_ai_cx_then_resumes_that_thread(codex_home):
    _index(codex_home, (THREAD, NAME))
    _rollout(codex_home, THREAD)

    command = _bare_engine_command("cx", NAME, Path.cwd(), None, "gemini", "--no-sandbox", [])

    assert command == ["codex", "resume", THREAD]


def test_given_pi_bare_launch_without_resume_flag_when_launching_then_continues_its_session():
    command = _bare_engine_command("p", NAME, Path.cwd(), None, "gemini", "--no-sandbox", [])

    assert command == ["pi", "--continue", "--provider", "openai-codex", "--name", NAME]


# --- its own Codex process ------------------------------------------------------------


def _fake_codex(bin_dir: Path, help_text: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    codex = bin_dir / "codex"
    codex.write_text(f"#!/bin/sh\ncat <<'HELP'\n{help_text}\nHELP\n", encoding="utf-8")
    codex.chmod(0o755)


@posix_only
def test_given_codex_offering_no_daemon_when_bare_launching_then_each_session_runs_its_own_codex_process(
    tmp_path, monkeypatch, codex_home
):
    _fake_codex(tmp_path / "bin", "      --no-daemon\n          Run without the shared background server")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    _index(codex_home, (THREAD, NAME))
    _rollout(codex_home, THREAD)

    assert _bare_engine_command("cx", "myproject-2", Path.cwd(), None, "gemini", "--no-sandbox", []) == [
        "codex",
        "--no-daemon",
    ]
    assert _bare_engine_command("cx", NAME, Path.cwd(), None, "gemini", "--no-sandbox", []) == [
        "codex",
        "resume",
        "--no-daemon",
        THREAD,
    ]


@posix_only
def test_given_older_codex_without_no_daemon_when_bare_launching_then_the_flag_is_not_passed(tmp_path, monkeypatch):
    _fake_codex(tmp_path / "bin", "      --search\n          Enable live web search")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")

    assert _bare_engine_command("cx", NAME, Path.cwd(), None, "gemini", "--no-sandbox", []) == ["codex"]


@posix_only
def test_given_codex_that_never_answers_help_when_probing_then_launch_goes_on_without_the_flag(tmp_path, monkeypatch):
    from ai_cli import codex_session

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "codex").write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    (bin_dir / "codex").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(codex_session, "_HELP_TIMEOUT_SECONDS", 0.5)

    assert codex_session.exclusive_process_flags(cache_dir=tmp_path / "cache") == []


@posix_only
def test_given_same_codex_install_when_probed_twice_then_its_help_runs_once(tmp_path, monkeypatch):
    from ai_cli import codex_session

    runs = tmp_path / "runs"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "codex").write_text(f"#!/bin/sh\necho run >> {runs}\necho '      --no-daemon'\n", encoding="utf-8")
    (bin_dir / "codex").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    first = codex_session.exclusive_process_flags(cache_dir=tmp_path / "cache")
    second = codex_session.exclusive_process_flags(cache_dir=tmp_path / "cache")

    assert first == second == ["--no-daemon"]
    assert runs.read_text().splitlines() == ["run"]


# --- pane binding (a tool inside Codex finds its own pane) --------------------------


@posix_only
@pytest.mark.parametrize("thread", ["", THREAD], ids=["new-session", "named-thread"])
def test_given_a_codex_launch_in_a_pane_when_the_session_script_dispatches_then_it_records_that_pane(tmp_path, thread):
    calls = _run_dispatch(tmp_path, "cx", first_run=True, thread=thread)

    assert f"AI internal bind-codex-pane {PANE} {NAME}" in calls


@pytest.fixture
def fake_pane_tmux(monkeypatch):
    """tmux answering ``display-message`` for one live pane in one session."""
    state: dict = {"sessions": {PANE: f"cx-{NAME}"}}
    real_run = subprocess.run

    def run(command, *args, **kwargs):
        if Path(str(command[0])).name != "tmux":
            return real_run(command, *args, **kwargs)
        if command[1] == "display-message":
            session = state["sessions"].get(command[command.index("-t") + 1], "")
            return subprocess.CompletedProcess(command, 0, f"{session}\n" if session else "\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: f"/usr/bin/{name}")
    return state


def _bind(capsys) -> tuple[object, str]:
    with patch("ai_cli.config.load_config", return_value={}), pytest.raises(SystemExit) as exc_info:
        _handle_internal(["bind-codex-pane", PANE, NAME])
    return exc_info.value.code, capsys.readouterr().err


def _binding(home: Path, thread: str = THREAD) -> dict:
    return json.loads((home / "pane-bindings" / f"{thread}.json").read_text(encoding="utf-8"))


def test_given_a_named_thread_when_binding_its_pane_then_the_record_names_thread_pane_and_tmux_session(
    fake_pane_tmux, codex_home, capsys
):
    # Named but not yet saved: a running thread is bound before Codex writes its rollout.
    _index(codex_home, (THREAD, NAME))

    code, err = _bind(capsys)

    assert code == 0, err
    record = _binding(codex_home)
    assert record["thread_id"] == THREAD
    assert record["pane_id"] == PANE
    assert record["tmux_session"] == f"cx-{NAME}"


def test_given_a_new_session_named_only_after_codex_starts_when_binding_then_it_waits_for_the_name(
    fake_pane_tmux, codex_home, capsys, monkeypatch
):
    polls = {"n": 0}

    def sleep(_seconds):
        polls["n"] += 1
        if polls["n"] == 3:
            _index(codex_home, (THREAD, NAME))

    monkeypatch.setattr("ai_cli.codex_session.time.sleep", sleep)

    code, err = _bind(capsys)

    assert code == 0, err
    assert polls["n"] >= 3
    assert _binding(codex_home)["pane_id"] == PANE


def test_given_a_rebinding_after_a_relaunch_in_another_pane_when_binding_then_the_record_follows_the_live_pane(
    fake_pane_tmux, codex_home, capsys
):
    _index(codex_home, (THREAD, NAME))
    (codex_home / "pane-bindings").mkdir(parents=True)
    (codex_home / "pane-bindings" / f"{THREAD}.json").write_text(
        json.dumps({"thread_id": THREAD, "pane_id": "%7", "tmux_session": "old", "written_at": 1}), encoding="utf-8"
    )

    code, err = _bind(capsys)

    assert code == 0, err
    assert _binding(codex_home)["pane_id"] == PANE
    assert not [p.name for p in (codex_home / "pane-bindings").iterdir() if p.name.startswith(".")]


def test_given_a_pane_tmux_does_not_report_when_binding_then_nothing_is_written_and_it_says_why(
    fake_pane_tmux, codex_home, capsys
):
    _index(codex_home, (THREAD, NAME))
    fake_pane_tmux["sessions"].clear()

    code, err = _bind(capsys)

    assert code == 1
    assert f"tmux does not report pane {PANE}" in err
    assert not (codex_home / "pane-bindings" / f"{THREAD}.json").exists()


def test_given_no_thread_ever_carries_the_name_when_binding_then_it_gives_up_and_writes_nothing(
    fake_pane_tmux, codex_home, capsys, monkeypatch
):
    clock = {"now": 0.0}

    def sleep(seconds):
        clock["now"] += seconds

    monkeypatch.setattr("ai_cli.codex_session.time.sleep", sleep)
    monkeypatch.setattr("ai_cli.codex_session.time.monotonic", lambda: clock["now"])

    code, err = _bind(capsys)

    assert code == 1
    assert "no Codex thread carried the name" in err
    assert not (codex_home / "pane-bindings").exists()
