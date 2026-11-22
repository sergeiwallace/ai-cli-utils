"""The session script hands Python the pid Python can see, not the shell's own number.

Under Git Bash (MSYS2) on Windows, ``$$`` is an MSYS2 pid. The ``ai`` the script calls is
native Windows Python, whose psutil knows only Windows pids, so the supervisor's
``ai sessions _begin --supervisor-pid "$$"`` failed with "process PID not found" and the
session never started. MSYS2 publishes each process's Windows pid at
``/proc/<pid>/winpid``.

These tests render the script as a Windows launcher does and stand a directory in for
MSYS2's ``/proc``, mapping the shell's pid to a pid that is alive. A real ``/proc`` cannot
be used for that: its winpid names the Windows process, and MSYS2 replaces that process
on every exec while keeping the shell's pid, so no value read before the script starts is
the one it reads. On Windows CI they run under the real MSYS2 shell, and one more test
reads the real ``/proc`` from inside the shell it describes.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from ai_cli import config, session_script

# The stub `ai` records the pid it was handed and whether native Python can see it,
# then fails, which ends the script at its first generation request.
_STUB_AI = """#!/usr/bin/env bash
if [[ "$1 $2" == "sessions _begin" ]]; then
  while (( $# )); do
    if [[ "$1" == --supervisor-pid ]]; then
      {python} -c 'import psutil, sys; print(sys.argv[1], psutil.pid_exists(int(sys.argv[1])))' "$2" >> {record}
    fi
    shift
  done
  printf 'cannot acquire a durable supervisor generation: stub\\n' >&2
  exit 1
fi
exit 0
"""

# Publishes the shell's Windows pid in the stand-in /proc, records it, then execs the
# session script, which keeps `$$` (MSYS2 included).
_WRAPPER = """
root="$1"; expected="$2"; mapped="$3"; shift 3
mkdir -p "$root/$$"
printf '%s\\n' "$mapped" > "$root/$$/winpid"
read -r winpid < "$root/$$/winpid"
printf '%s\\n' "$winpid" > "$expected"
exec "$@"
"""


def _stand_in_proc(tmp_path: Path, monkeypatch) -> str:
    """Render as a Windows launcher, reading winpids from a directory standing in for /proc."""
    root = (tmp_path / "proc").as_posix()
    monkeypatch.setattr(session_script, "MSYS_PROC_ROOT", root, raising=False)
    monkeypatch.setattr(session_script, "_native_windows_launcher", lambda: True, raising=False)
    return root


@pytest.fixture
def windows_launch(tmp_path, monkeypatch):
    bash = session_script.shutil.which("bash")
    shell = session_script.resolve_session_shell()
    if bash is None or shell is None:
        pytest.skip("the session script needs bash")
    root = _stand_in_proc(tmp_path, monkeypatch)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "begin-pids"
    stub = bin_dir / "ai"
    stub.write_text(
        _STUB_AI.format(python=shlex.quote(Path(sys.executable).as_posix()), record=shlex.quote(record.as_posix())),
        encoding="utf-8",
    )
    stub.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if key != "AI_CLI_RUN_GENERATION"}
    env["PATH"] = bin_dir.as_posix() + os.pathsep + env.get("PATH", "")
    env["XDG_STATE_HOME"] = (tmp_path / "state").as_posix()

    def run(*script_args: str) -> tuple[subprocess.CompletedProcess, str, list[str]]:
        script = tmp_path / "c-session-1.sh"
        script.write_text(
            session_script.get_engine_script("c", "session-1", "c-session-1", "c-session-", "myproject"),
            encoding="utf-8",
        )
        expected = tmp_path / "expected-pid"
        result = subprocess.run(
            [
                bash,
                "-c",
                _WRAPPER,
                "wrapper",
                root,
                expected.as_posix(),
                str(os.getpid()),
                shell,
                script.as_posix(),
                *script_args,
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        recorded = record.read_text().split() if record.exists() else []
        return result, expected.read_text().strip(), recorded

    return run


def test_given_msys_shell_pid_when_supervisor_begins_then_python_gets_the_windows_pid(windows_launch):
    result, winpid, recorded = windows_launch()
    assert result.returncode == 1, result.stderr
    assert recorded == [winpid, "True"], result.stderr


def test_given_msys_shell_pid_when_legacy_child_begins_then_python_gets_the_windows_pid(windows_launch):
    result, winpid, recorded = windows_launch("--ai-cli-child-body")
    assert result.returncode == 76, result.stderr
    assert recorded == [winpid, "True"], result.stderr


def test_given_unreadable_winpid_when_supervisor_begins_then_it_stops_with_the_reason(tmp_path, monkeypatch):
    bash = session_script.shutil.which("bash")
    if bash is None:
        pytest.skip("the session script needs bash")
    _stand_in_proc(tmp_path, monkeypatch)
    script = tmp_path / "c-session-1.sh"
    script.write_text(
        session_script.get_engine_script("c", "session-1", "c-session-1", "c-session-", "myproject"), encoding="utf-8"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ai").write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> " + shlex.quote(str(tmp_path / "calls")) + "\n", encoding="utf-8"
    )
    (bin_dir / "ai").chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", XDG_STATE_HOME=str(tmp_path / "state"))
    result = subprocess.run(
        [bash, script.as_posix()],
        check=False,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "cannot read the Windows pid of shell process" in result.stderr
    calls = (tmp_path / "calls").read_text() if (tmp_path / "calls").exists() else ""
    assert "sessions _begin" not in calls


def test_given_windows_launcher_when_supervisor_runs_child_then_child_body_runs_and_its_exit_ends_the_loop(
    tmp_path, monkeypatch
):
    bash = session_script.shutil.which("bash")
    shell = session_script.resolve_session_shell()
    if bash is None or shell is None:
        pytest.skip("the session script needs bash")
    root = _stand_in_proc(tmp_path, monkeypatch)
    state = tmp_path / "state"
    sessions = state / "ai-cli-utils" / "sessions"
    sessions.mkdir(parents=True)
    runs = tmp_path / "child-runs"
    # The supervisor runs the stable script as its child body; this one records how it
    # was started and ends the session cleanly, as a deliberate exit does.
    (sessions / "c-session-1.sh").write_text(
        f"printf '%s\\n' \"$*\" >> {shlex.quote(runs.as_posix())}\nexit 77\n", encoding="utf-8"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ai").write_text(
        '#!/usr/bin/env bash\nif [[ "$1 $2" == "sessions _begin" ]]; then printf "generation-1\\n"; fi\nexit 0\n',
        encoding="utf-8",
    )
    (bin_dir / "ai").chmod(0o755)
    # The stub generation is not in any registry, so recording the exit fails; bound that
    # failure's retries, which are not under test.
    load = config.load_config
    policy = {"deadline_seconds": 1, "command_timeout_seconds": 1, "backoff_seconds": 0.1, "max_backoff_seconds": 0.2}
    monkeypatch.setattr(
        config, "load_config", lambda **kwargs: load(**kwargs) | {"session_relaunch": {"exit_persistence": policy}}
    )
    script = tmp_path / "c-session-1.sh"
    script.write_text(
        session_script.get_engine_script("c", "session-1", "c-session-1", "c-session-", "myproject"), encoding="utf-8"
    )
    env = {key: value for key, value in os.environ.items() if key != "AI_CLI_RUN_GENERATION"}
    env.update(PATH=bin_dir.as_posix() + os.pathsep + env["PATH"], XDG_STATE_HOME=state.as_posix())
    command = [bash, "-c", _WRAPPER, "wrapper", root, (tmp_path / "expected-pid").as_posix(), str(os.getpid())]
    command += [shell, script.as_posix()]
    result = subprocess.run(
        command, check=False, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120
    )
    assert runs.read_text().splitlines() == ["--ai-cli-child-body"], result.stderr
    assert "native Windows programs have no POSIX process groups" in result.stderr
    assert "never reported a process group" not in result.stderr


def _function(script: str, name: str) -> str:
    """The text of shell function ``name`` as the script defines it."""
    start = script.index(f"    {name}() {{\n")
    return script[start : script.index("\n    }\n", start) + len("\n    }\n")]


def test_given_msys_shell_pid_when_watcher_takes_its_lock_then_session_migration_sees_a_live_holder(
    tmp_path, monkeypatch
):
    from ai_cli.session_migrate import _watcher_holder

    bash = session_script.shutil.which("bash")
    if bash is None:
        pytest.skip("the session script needs bash")
    root = _stand_in_proc(tmp_path, monkeypatch)
    script = session_script.get_engine_script("c", "session-1", "c-session-1", "c-session-", "myproject")
    lock = tmp_path / "ai-watcher-lock-c-session-1"
    expected = tmp_path / "expected-pid"
    # The watcher loops for the life of the child body; this stops it once the lock is written,
    # and keeps the lock its exit trap would remove so it can be read.
    body = (
        "_ai_emit() { printf '%s\\n' \"$*\" >&2; }\n"
        + _function(script, "_ai_native_pid")
        + _function(script, "start_watcher")
        + f"lock_file={shlex.quote(lock.as_posix())}; watcher_pid=''; engine=c\n"
        + "start_watcher\n"
        + 'for _ in $(seq 100); do [[ -s "$lock_file" ]] && break; sleep 0.05; done\n'
        + 'kill "$watcher_pid"; wait "$watcher_pid" 2>/dev/null\n'
    )
    script_file = tmp_path / "watcher.sh"
    script_file.write_text(body.replace("trap 'rm -f \"$lock_file\"' EXIT", ":"), encoding="utf-8")
    result = subprocess.run(
        [bash, "-c", _WRAPPER, "wrapper", root, expected.as_posix(), str(os.getpid()), bash, script_file.as_posix()],
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert lock.read_text().strip() == expected.read_text().strip(), result.stderr
    assert _watcher_holder(lock) is not None


@pytest.mark.skipif(sys.platform != "win32", reason="reads MSYS2's real /proc")
def test_given_real_msys_proc_when_the_shell_names_itself_then_native_python_sees_that_shell():
    bash = session_script.shutil.which("bash")
    if bash is None:
        pytest.skip("the session script needs bash")
    script = session_script.get_engine_script("c", "session-1", "c-session-1", "c-session-", "myproject")
    python = shlex.quote(Path(sys.executable).as_posix())
    body = (
        "_ai_emit() { printf '%s\\n' \"$*\" >&2; }\n"
        + _function(script, "_ai_native_pid")
        + 'pid=$(_ai_native_pid "$$") || exit 1\n'
        + f"{python} -c 'import psutil, sys; print(psutil.Process(int(sys.argv[1])).name())' \"$pid\"\n"
    )
    result = subprocess.run(
        [bash, "-c", body], check=False, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().lower() == "bash.exe"
