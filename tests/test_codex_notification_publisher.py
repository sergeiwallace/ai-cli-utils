"""A supervised `ai cx` session drives the notification publisher through its phases.

Design: docs/designs/codex-notification-publisher.md.

The session script's Codex dispatch runs for real under bash, with the real
``ai internal codex-publisher`` steps behind it, a fake publisher executable that answers
each phase from a scenario file, and a fake ``codex`` that plays both the app-server
(it listens on the Unix socket it is given) and the TUI (it stays up for a set time).
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_cli.main import _bare_engine_command, _handle_internal
from ai_cli.session_script import get_engine_script

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="the session script and Unix sockets are POSIX-only")

REPO = Path(__file__).resolve().parents[1]
NAME = "myproject-1"
SESSION = f"cx-{NAME}"
RECORD = "0b9e7d64-2f43-4c2a-9d64-1f1d6f0a5c11"
NOTICE = "Codex notifications are unavailable for this session"

FAKE_PUBLISHER = r"""
import json, os, subprocess, sys, time

scenario_path, phase = sys.argv[1], sys.argv[2]
with open(scenario_path, encoding="utf-8") as handle:
    scenario = json.load(handle)
counts_path = scenario_path + ".counts"
try:
    with open(counts_path, encoding="utf-8") as handle:
        counts = json.load(handle)
except OSError:
    counts = {}
seen = counts.get(phase, 0)
counts[phase] = seen + 1
with open(counts_path, "w", encoding="utf-8") as handle:
    json.dump(counts, handle)
raw = sys.stdin.buffer.read()


def note(**fields):
    with open(scenario["log"], "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"t": time.time(), **fields}) + "\n")


note(phase=phase, request=json.loads(raw), size=len(raw), pid=os.getpid(), pgid=os.getpgrp())
answers = scenario["phases"].get(phase, [{}])
answer = answers[min(seen, len(answers) - 1)]
if answer.get("grandchild"):
    grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    note(grandchild=grandchild.pid)
time.sleep(answer.get("sleep", 0))
code = answer.get("exit", 0)
if "stdout" in answer:
    sys.stdout.write(answer["stdout"])
else:
    sys.stdout.write(json.dumps({
        "schemaVersion": 1,
        "phase": phase,
        "status": answer.get("status", "accepted" if code == 0 else "unavailable"),
        "reason": answer.get("reason", "ok"),
        "possiblyPersisted": answer.get("possiblyPersisted", False),
        "recordId": answer.get("recordId"),
        "socketPath": answer.get("socketPath"),
    }))
sys.exit(code)
"""

FAKE_CODEX = """#!{python}
import json, os, signal, socket, sys, time

def note(**fields):
    with open(os.environ["FAKE_CODEX_LOG"], "a", encoding="utf-8") as handle:
        handle.write(json.dumps({{"t": time.time(), **fields}}) + "\\n")

args = sys.argv[1:]
if args[:2] == ["app-server", "--listen"]:
    server = socket.socket(socket.AF_UNIX)
    server.bind(args[2].removeprefix("unix://"))
    server.listen(8)
    signal.signal(signal.SIGTERM, lambda *_: (note(engine_end=os.getpid()), os._exit(0)))
    note(engine=os.getpid(), argv=args)
    while True:
        connection, _ = server.accept()
        connection.close()
note(tui=args)
time.sleep(float(os.environ.get("FAKE_TUI_SECONDS", "0")))
note(tui_end=True)
"""


@dataclass
class Launch:
    returncode: int
    stderr: str
    calls: list[str]
    publisher: list[dict]
    codex: list[dict]
    wrapper_pid: int

    @property
    def phases(self) -> list[str]:
        return [entry["phase"] for entry in self.publisher if "phase" in entry]

    def requests(self, phase: str) -> list[dict]:
        return [entry["request"] for entry in self.publisher if entry.get("phase") == phase]

    def tui_args(self) -> list[list[str]]:
        return [entry["tui"] for entry in self.codex if "tui" in entry]

    def engines(self) -> list[int]:
        return [entry["engine"] for entry in self.codex if "engine" in entry]

    def notices(self) -> int:
        return self.stderr.count(NOTICE)


@pytest.fixture
def world(tmp_path):
    """A short socket directory (a Unix socket path is limited to about 100 bytes) and a fake bin."""
    short = Path(tempfile.mkdtemp(prefix="cxp-", dir="/tmp" if sys.platform != "win32" else None))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    codex = bin_dir / "codex"
    codex.write_text(FAKE_CODEX.format(python=sys.executable), encoding="utf-8")
    codex.chmod(0o755)
    publisher = tmp_path / "fake_publisher.py"
    publisher.write_text(FAKE_PUBLISHER, encoding="utf-8")
    yield {"tmp": tmp_path, "short": short, "bin": bin_dir, "publisher": publisher}
    shutil.rmtree(short, ignore_errors=True)


def _scenario(world, phases: dict) -> Path:
    scenario = world["tmp"] / "scenario.json"
    scenario.write_text(json.dumps({"log": str(world["tmp"] / "publisher.log"), "phases": phases}), encoding="utf-8")
    return scenario


def _accepted_begin(world) -> dict:
    return {"exit": 0, "recordId": RECORD, "socketPath": str(world["short"] / "s")}


def _write_config(world, settings: dict | None) -> None:
    config_dir = world["tmp"] / "config" / "ai-cli-utils"
    config_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    if settings is not None:
        lines.append("[codex.publisher]")
        for key, value in settings.items():
            lines.append(f"{key} = {json.dumps(value)}")
    (config_dir / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _publisher_settings(world, phases: dict, **overrides) -> dict:
    scenario = _scenario(world, phases)
    return {
        "command": [sys.executable, str(world["publisher"]), str(scenario)],
        "timeout_ms": 5000,
        "refresh_seconds": 1,
        "enroll_window_seconds": 60,
    } | overrides


def _read_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _dispatch_block() -> str:
    script = get_engine_script(
        engine="cx", ai_name=NAME, session=SESSION, prefix="cx-myproject-", project_prefix="myproject"
    )
    start = script.index("_ai_run_codex() {")
    end = script.index("# END BACKGROUND ATTACH", script.index("# BEGIN BACKGROUND ATTACH prompted-end"))
    return script[start:end]


def _launch(
    world,
    *,
    tui_seconds: float = 0.0,
    ai_name: str = NAME,
    generation: str = "gen-1",
    tmux: tuple[str, str] | None = None,
) -> Launch:
    tmp = world["tmp"]
    calls = tmp / "calls.log"
    calls.write_text("", encoding="utf-8")
    (tmp / "codex.log").write_text("", encoding="utf-8")
    harness = tmp / "dispatch.sh"
    harness.write_text(
        f"""
engine=cx
ai_name={shlex.quote(ai_name)}
tmux_session={SESSION}
first_run=true
prompt_file={tmp}/no-prompt
_ai_launch_log=""
resolve_status=1
matched_file=""
bg_target=""
claude_perms_flag=""
printf 'WRAPPER %s\\n' "$$" >> {calls}
run_agent() {{ printf 'RUN %s\\n' "$*" >> {calls}; "$@"; }}
ai() {{
  if [[ "$1 $2" == "internal codex-publisher" ]]; then {shlex.quote(sys.executable)} -m ai_cli.main "$@"; return; fi
  printf 'AI %s\\n' "$*" >> {calls}
  return 0
}}
tmux() {{ :; }}
{_dispatch_block()}
wait
""",
        encoding="utf-8",
    )
    env = {key: value for key, value in os.environ.items() if key not in ("TMUX", "TMUX_PANE")}
    env |= {
        "PATH": f"{world['bin']}{os.pathsep}{os.environ['PATH']}",
        "PYTHONPATH": str(REPO / "src"),
        "XDG_CONFIG_HOME": str(tmp / "config"),
        "XDG_STATE_HOME": str(tmp / "state"),
        "CODEX_HOME": str(tmp / "codex-home"),
        "AI_CLI_RUN_GENERATION": generation,
        "FAKE_CODEX_LOG": str(tmp / "codex.log"),
        "FAKE_TUI_SECONDS": str(tui_seconds),
    }
    if tmux:
        env |= {"TMUX": tmux[0], "TMUX_PANE": tmux[1]}
    done = subprocess.run(
        ["bash", str(harness)], capture_output=True, text=True, timeout=120, check=False, env=env, cwd=tmp
    )
    lines = calls.read_text(encoding="utf-8").splitlines()
    wrapper = next(int(line.split()[1]) for line in lines if line.startswith("WRAPPER "))
    return Launch(
        done.returncode,
        done.stderr,
        lines,
        _read_log(tmp / "publisher.log"),
        _read_log(tmp / "codex.log"),
        wrapper,
    )


def _gone(pid: int, within: float = 5.0) -> bool:
    import psutil

    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.05)
    return False


# --- the full sequence ------------------------------------------------------------


@posix_only
def test_given_a_fresh_start_whose_first_two_enrolls_see_no_thread_when_the_third_sees_one_then_it_enrolls_and_refreshes(
    world,
):
    pending = {"exit": 3, "status": "pending", "reason": "thread-not-loaded"}
    _write_config(
        world,
        _publisher_settings(
            world,
            {"begin": [_accepted_begin(world)], "enroll": [pending, pending, {"exit": 0}], "refresh": [{"exit": 0}]},
        ),
    )

    launch = _launch(world, tui_seconds=10)

    assert launch.returncode == 0, launch.stderr
    phases = launch.phases
    assert phases[:5] == ["begin", "running", "enroll", "enroll", "enroll"], phases
    assert "refresh" in phases[5:] and phases[-1] == "finish" and phases.count("finish") == 1, phases
    assert set(phases[5:-1]) == {"refresh"}, phases
    begin = launch.requests("begin")[0]
    assert begin == {
        "schemaVersion": 1,
        "sessionName": NAME,
        "launcherRunGeneration": "gen-1",
        "wrapperPid": launch.wrapper_pid,
        "worktreeRoot": str(world["tmp"].resolve()),
        "cwd": str(world["tmp"].resolve()),
        "repoRoot": str(world["tmp"].resolve()),
        "tmuxSession": "",
        "paneId": "",
    }
    [engine] = launch.engines()
    assert launch.codex[0]["argv"] == ["app-server", "--listen", f"unix://{world['short'] / 's'}"]
    assert launch.requests("running") == [{"schemaVersion": 1, "recordId": RECORD, "enginePid": engine}]
    assert launch.requests("enroll")[0] == {"schemaVersion": 1, "recordId": RECORD, "enginePid": engine}
    assert launch.requests("finish") == [{"schemaVersion": 1, "recordId": RECORD}]
    assert launch.tui_args() == [["--remote", f"unix://{world['short'] / 's'}"]]
    assert launch.notices() == 0, launch.stderr
    assert _gone(engine)
    # The launcher never deletes the socket: cleanup is the publisher's.
    assert (world["short"] / "s").exists()


@posix_only
def test_given_no_thread_through_the_whole_enroll_window_when_it_expires_then_finish_runs_once_with_one_notice(world):
    pending = {"exit": 3, "status": "pending", "reason": "thread-not-loaded"}
    _write_config(
        world,
        _publisher_settings(world, {"begin": [_accepted_begin(world)], "enroll": [pending]}, enroll_window_seconds=3),
    )

    launch = _launch(world, tui_seconds=8)

    assert launch.returncode == 0, launch.stderr
    phases = launch.phases
    assert phases[:3] == ["begin", "running", "enroll"], phases
    assert "refresh" not in phases and phases.count("finish") == 1 and phases[-1] == "finish", phases
    finish_at = next(entry["t"] for entry in launch.publisher if entry.get("phase") == "finish")
    tui_end_at = next(entry["t"] for entry in launch.codex if "tui_end" in entry)
    assert finish_at < tui_end_at, "the window expiry, not the session end, must call finish"
    assert launch.notices() == 1, launch.stderr
    assert launch.tui_args() == [["--remote", f"unix://{world['short'] / 's'}"]]


@posix_only
def test_given_an_enrolled_session_when_a_refresh_is_refused_then_refreshing_stops_and_finish_runs(world):
    _write_config(
        world,
        _publisher_settings(
            world,
            {
                "begin": [_accepted_begin(world)],
                "enroll": [{"exit": 0}],
                "refresh": [{"exit": 3, "status": "unavailable", "reason": "thread-changed"}],
            },
        ),
    )

    launch = _launch(world, tui_seconds=7)

    assert launch.returncode == 0, launch.stderr
    assert launch.phases == ["begin", "running", "enroll", "refresh", "finish"], launch.phases
    finish_at = next(entry["t"] for entry in launch.publisher if entry.get("phase") == "finish")
    tui_end_at = next(entry["t"] for entry in launch.codex if "tui_end" in entry)
    assert finish_at < tui_end_at
    assert launch.notices() == 1, launch.stderr


# --- begin failures: Codex launches as it does today -------------------------------


@posix_only
def test_given_begin_times_out_when_launching_then_its_process_group_is_killed_reconcile_runs_once_and_codex_still_launches(
    world,
):
    _write_config(
        world,
        _publisher_settings(world, {"begin": [{"sleep": 30, "grandchild": True}]}, timeout_ms=1000),
    )

    launch = _launch(world)

    assert launch.returncode == 0, launch.stderr
    assert launch.phases == ["begin", "reconcile"], launch.phases
    begin = next(entry for entry in launch.publisher if entry.get("phase") == "begin")
    assert begin["pgid"] == begin["pid"], "each phase child runs in its own process group"
    grandchild = next(entry["grandchild"] for entry in launch.publisher if "grandchild" in entry)
    assert _gone(begin["pid"]) and _gone(grandchild), "the whole group is killed"
    assert launch.requests("reconcile") == [
        {"schemaVersion": 1, "launcherRunGeneration": "gen-1", "wrapperPid": launch.wrapper_pid}
    ]
    assert launch.engines() == []
    assert launch.tui_args() == [[]]
    assert launch.notices() == 1, launch.stderr

    again = _launch(world)

    assert again.phases == ["begin", "reconcile"], "begin is never retried for the same run generation"
    assert again.tui_args() == [[]]
    assert again.notices() == 0, "the notice is emitted once per run generation"


@posix_only
def test_given_begin_refused_with_exit_3_when_launching_then_no_app_server_starts_and_codex_launches_as_today(world):
    _write_config(
        world,
        _publisher_settings(world, {"begin": [{"exit": 3, "status": "unavailable", "reason": "unsupported-host"}]}),
    )

    launch = _launch(world, tmux=("/tmp/tmux-1000/default,1,0", "%42"))

    assert launch.returncode == 0, launch.stderr
    assert launch.phases == ["begin"]
    assert launch.requests("begin")[0]["tmuxSession"] == SESSION
    assert launch.requests("begin")[0]["paneId"] == "%42"
    assert launch.engines() == []
    assert launch.tui_args() == [[]]
    assert launch.notices() == 1, launch.stderr


@posix_only
def test_given_begin_rejected_as_invalid_with_exit_2_when_launching_then_one_notice_names_a_launcher_defect(world):
    _write_config(
        world,
        _publisher_settings(world, {"begin": [{"exit": 2, "status": "unavailable", "reason": "invalid-request"}]}),
    )

    launch = _launch(world)

    assert launch.phases == ["begin"]
    assert launch.notices() == 1, launch.stderr
    assert "launcher defect" in launch.stderr
    assert launch.tui_args() == [[]]

    again = _launch(world)

    assert again.phases == ["begin"], "no further phase for a generation the publisher called invalid"
    assert again.notices() == 0


@posix_only
def test_given_begin_prints_unparsable_stdout_when_launching_then_it_is_handled_as_outcome_uncertain(world):
    _write_config(world, _publisher_settings(world, {"begin": [{"exit": 0, "stdout": "ok, record made\n"}]}))

    launch = _launch(world)

    assert launch.phases == ["begin", "reconcile"]
    assert launch.engines() == []
    assert launch.tui_args() == [[]]


# --- when the publisher is never asked --------------------------------------------


@posix_only
def test_given_no_publisher_command_configured_when_launching_then_no_phase_is_called_and_codex_launches_as_today(
    world,
):
    _write_config(world, None)

    launch = _launch(world)

    assert launch.returncode == 0, launch.stderr
    assert launch.publisher == []
    assert launch.engines() == []
    assert launch.tui_args() == [[]]
    assert launch.notices() == 0 and "publisher" not in launch.stderr


@posix_only
def test_given_an_invalid_timeout_when_launching_then_the_publisher_is_off_with_one_message_and_codex_starts(world):
    _write_config(world, _publisher_settings(world, {"begin": [_accepted_begin(world)]}, timeout_ms=0))

    launch = _launch(world)

    assert launch.returncode == 0, launch.stderr
    assert launch.publisher == []
    assert launch.tui_args() == [[]]
    assert launch.stderr.count("timeout_ms") == 1, launch.stderr


# --- in-process: the begin step on its own ----------------------------------------


@pytest.fixture
def begin_env(world, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(world["tmp"] / "state"))
    monkeypatch.setenv("AI_CLI_RUN_GENERATION", "gen-1")
    monkeypatch.setenv("CODEX_HOME", str(world["tmp"] / "codex-home"))
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.chdir(world["tmp"])
    return world


def _begin(capsys, config: dict, ai_name: str = NAME) -> tuple[object, str, str]:
    with patch("ai_cli.config.load_config", return_value=config), pytest.raises(SystemExit) as exc_info:
        _handle_internal(["codex-publisher", "begin", SESSION, ai_name, str(os.getpid())])
    captured = capsys.readouterr()
    return exc_info.value.code, captured.out, captured.err


@pytest.mark.parametrize("value", [-5, True, 1.5, "15000", 10**9], ids=["negative", "bool", "float", "text", "huge"])
def test_given_an_invalid_timeout_value_when_beginning_then_no_phase_runs_and_one_message_says_why(
    begin_env, capsys, value
):
    settings = _publisher_settings(begin_env, {"begin": [_accepted_begin(begin_env)]}, timeout_ms=value)

    code, out, err = _begin(capsys, {"codex": {"publisher": settings}})

    assert code != 0 and out == ""
    assert _read_log(begin_env["tmp"] / "publisher.log") == []
    assert err.count("timeout_ms") == 1, err


def test_given_a_request_over_the_stdin_bound_when_beginning_then_the_publisher_is_never_called(begin_env, capsys):
    settings = _publisher_settings(begin_env, {"begin": [_accepted_begin(begin_env)]})

    code, out, err = _begin(capsys, {"codex": {"publisher": settings}}, ai_name="x" * 17000)

    assert code != 0 and out == ""
    assert _read_log(begin_env["tmp"] / "publisher.log") == []
    assert err.count(NOTICE) == 1, err


def test_given_a_request_just_under_the_stdin_bound_when_beginning_then_it_is_sent(begin_env, capsys):
    settings = _publisher_settings(begin_env, {"begin": [{"exit": 3, "status": "unavailable", "reason": "disabled"}]})

    # Every other field is short here, so a 15000-byte name keeps the request under 16384.
    _begin(capsys, {"codex": {"publisher": settings}}, ai_name="x" * 15000)

    [entry] = _read_log(begin_env["tmp"] / "publisher.log")
    assert 15000 < entry["size"] <= 16384


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        '{"schemaVersion": 1}',
        '{"schemaVersion":1,"phase":"begin","status":"accepted","reason":"ok","possiblyPersisted":false,'
        '"recordId":"r","socketPath":"/tmp/s","extra":1}',
        '{"schemaVersion":1,"phase":"begin","status":"accepted","reason":"ok","possiblyPersisted":false,'
        '"recordId":"r","recordId":"r2","socketPath":"/tmp/s"}',
        '{"schemaVersion":1,"phase":"begin","status":"accepted","reason":"ok","possiblyPersisted":false,'
        '"recordId":"r","socketPath":"/tmp/s"} {}',
        '{"schemaVersion":true,"phase":"begin","status":"accepted","reason":"ok","possiblyPersisted":false,'
        '"recordId":"r","socketPath":"/tmp/s"}',
        '{"schemaVersion":1,"phase":"running","status":"accepted","reason":"ok","possiblyPersisted":false,'
        '"recordId":"r","socketPath":"/tmp/s"}',
    ],
    ids=["empty", "missing-keys", "unknown-key", "duplicate-key", "two-values", "bool-version", "wrong-phase"],
)
def test_given_a_response_that_does_not_match_the_fixed_keys_when_beginning_then_reconcile_runs(
    begin_env, capsys, stdout
):
    settings = _publisher_settings(begin_env, {"begin": [{"exit": 0, "stdout": stdout}]})

    code, out, _err = _begin(capsys, {"codex": {"publisher": settings}})

    assert code != 0 and out == ""
    phases = [entry["phase"] for entry in _read_log(begin_env["tmp"] / "publisher.log") if "phase" in entry]
    assert phases == ["begin", "reconcile"]


def test_given_windows_when_beginning_then_no_phase_is_called(begin_env, capsys, monkeypatch):
    monkeypatch.setattr("ai_cli.codex_publisher.sys.platform", "win32")
    settings = _publisher_settings(begin_env, {"begin": [_accepted_begin(begin_env)]})

    code, out, err = _begin(capsys, {"codex": {"publisher": settings}})

    assert code != 0 and out == "" and err == ""
    assert _read_log(begin_env["tmp"] / "publisher.log") == []


def test_given_a_publisher_configured_when_bare_launching_ai_cx_then_no_phase_is_called(begin_env):
    settings = _publisher_settings(begin_env, {"begin": [_accepted_begin(begin_env)]})

    with patch("ai_cli.config.load_config", return_value={"codex": {"publisher": settings}}):
        command = _bare_engine_command("cx", NAME, Path.cwd(), None, "gemini", "--no-sandbox", [])

    assert "--remote" not in command
    assert _read_log(begin_env["tmp"] / "publisher.log") == []


def _phases(world) -> list[str]:
    return [entry["phase"] for entry in _read_log(world["tmp"] / "publisher.log") if "phase" in entry]


@pytest.mark.parametrize(
    "answer",
    [
        {"exit": 3, "status": "unavailable", "reason": "registry-busy", "possiblyPersisted": True},
        {"exit": 3, "status": "unavailable", "reason": "registry-busy", "recordId": RECORD},
    ],
    ids=["possibly-persisted", "record-known"],
)
def test_given_begin_refused_with_a_possible_effect_when_beginning_then_reconcile_runs_once_and_begin_is_never_retried(
    begin_env, capsys, answer
):
    settings = _publisher_settings(begin_env, {"begin": [answer]})
    config = {"codex": {"publisher": settings}}

    first, _out, _err = _begin(capsys, config)
    second, _out, _err = _begin(capsys, config)

    assert first != 0 and second != 0
    assert _phases(begin_env) == ["begin", "reconcile"]


def test_given_begin_refused_with_no_effect_when_the_agent_restarts_then_begin_is_tried_again(begin_env, capsys):
    settings = _publisher_settings(begin_env, {"begin": [{"exit": 3, "status": "unavailable", "reason": "disabled"}]})
    config = {"codex": {"publisher": settings}}

    _begin(capsys, config)
    _begin(capsys, config)

    assert _phases(begin_env) == ["begin", "begin"]


def test_given_begin_accepted_with_a_record_id_that_is_not_a_uuid_when_beginning_then_reconcile_runs(begin_env, capsys):
    answer = {"exit": 0, "recordId": "not-a-uuid", "socketPath": str(begin_env["short"] / "s")}
    settings = _publisher_settings(begin_env, {"begin": [answer]})

    code, out, _err = _begin(capsys, {"codex": {"publisher": settings}})

    assert code != 0 and out == ""
    assert _phases(begin_env) == ["begin", "reconcile"]


@posix_only
def test_given_the_session_shell_is_gone_when_the_monitor_sees_it_then_it_stops_the_engine_without_calling_finish(
    begin_env, capsys
):
    settings = _publisher_settings(begin_env, {"begin": [_accepted_begin(begin_env)]})
    config = {"codex": {"publisher": settings}}
    _begin(capsys, config)
    wrapper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    wrapper.kill()
    wrapper.wait()
    engine = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])

    try:
        with patch("ai_cli.config.load_config", return_value=config), pytest.raises(SystemExit):
            _handle_internal(["codex-publisher", "monitor", SESSION, str(wrapper.pid), str(engine.pid)])
        engine.wait(timeout=10)
    finally:
        if engine.poll() is None:
            engine.kill()
            engine.wait()

    assert _phases(begin_env) == ["begin"]
    assert capsys.readouterr().err.count(NOTICE) == 1
