"""On Windows the agent starts under the project's direnv environment without ``direnv exec``.

``direnv exec`` cannot run a native program on Windows: its PATH search splits on ``:``,
accepts only files with a POSIX execute bit, and ends in Go's unsupported
``syscall.Exec``. A bare launch there printed "command 'claude' not found on PATH" for a
``claude.exe`` on that PATH. The launcher now loads the environment with
``direnv export json`` and finds the agent with ``shutil.which``.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_cli import compact_transport, direnv_exec, main, session_script
from ai_cli.main import cli

# Taken at import, before the suite's autouse fixture routes it to os.execvp.
_RUN_ON_THIS_CONSOLE = main._run_on_this_console

# A stand-in for the agent: records its argv and one variable the project environment sets.
_AGENT = """import os, sys
from pathlib import Path
Path(sys.argv[1]).write_text(repr([sys.argv[2:], os.environ.get("PROJECT_ENV")]))
sys.exit(5)
"""


def _program(directory: Path, name: str, script: str) -> Path:
    """An executable named ``name`` in ``directory`` that runs the Python ``script``.

    A ``.cmd`` on Windows, found through PATHEXT as a real agent's ``.exe`` is.
    """
    source = directory / f"{name}.py"
    source.write_text(script)
    python = Path(sys.executable).as_posix()
    if sys.platform == "win32":
        program = directory / f"{name}.cmd"
        program.write_text(f'@"{python}" "{source}" %*\r\n')
    else:
        program = directory / name
        program.write_text(f'#!/bin/sh\nexec {shlex.quote(python)} {shlex.quote(str(source))} "$@"\n')
        program.chmod(0o755)
    return program


def _fake_direnv(directory: Path, changes: dict | None, status: int = 0) -> None:
    printed = json.dumps(changes) if changes is not None else ""
    _program(
        directory,
        "direnv",
        f"import sys\nassert sys.argv[1:] == ['export', 'json'], sys.argv\nprint({printed!r})\nsys.exit({status})\n",
    )


@pytest.fixture
def project(tmp_path):
    tools, agents, root = tmp_path / "tools", tmp_path / "agents", tmp_path / "project"
    for directory in (tools, agents, root):
        directory.mkdir()
    _program(agents, "agent", _AGENT)
    env = dict(os.environ, PATH=os.pathsep.join([str(tools), os.environ.get("PATH", "")]))
    env.pop("PROJECT_ENV", None)
    return tools, agents, root, env, tmp_path / "agent-ran"


def _run(root: Path, env: dict, *command: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", direnv_exec.MODULE, str(root), *command],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def test_given_an_agent_only_on_the_project_path_when_run_then_it_runs_with_the_project_environment(project):
    tools, agents, root, env, ran = project
    _fake_direnv(tools, {"PATH": os.pathsep.join([str(agents), env["PATH"]]), "PROJECT_ENV": "1"})
    result = _run(root, env, "agent", str(ran), "--flag")
    assert result.returncode == 5, result.stderr
    assert ran.read_text() == repr([["--flag"], "1"])


def test_given_direnv_fails_when_run_then_the_agent_still_starts_and_the_reason_is_shown(project):
    tools, agents, root, env, ran = project
    _fake_direnv(tools, None, status=1)
    env["PATH"] = os.pathsep.join([str(agents), env["PATH"]])
    result = _run(root, env, "agent", str(ran))
    assert result.returncode == 5, result.stderr
    assert ran.read_text() == repr([[], None])
    assert "direnv exited with status 1" in result.stderr
    assert "without the project environment" in result.stderr


def test_given_the_agent_is_on_no_path_when_run_then_it_says_so_and_exits_127(project):
    tools, _agents, root, env, ran = project
    _fake_direnv(tools, {})
    result = _run(root, env, "no-such-agent", str(ran))
    assert result.returncode == 127
    assert "no-such-agent not found on PATH" in result.stderr
    assert not ran.exists()


def test_given_windows_when_a_bare_claude_launch_has_an_envrc_then_it_runs_through_the_portable_direnv_exec(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".envrc").write_text("export PROJECT_ENV=1\n")
    monkeypatch.setattr(direnv_exec, "direnv_exec_works", lambda: False)
    with (
        patch("sys.argv", ["ai", "c", "--bare"]),
        patch("ai_cli.config.load_config", return_value={}),
        patch("ai_cli.session.get_project_prefix", return_value="session"),
        patch("ai_cli.session.is_current_project_resolved", return_value=True),
        patch("ai_cli.session.create_worktree", return_value=(tmp_path, False)),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.main.trigger_background_update"),
        patch("ai_cli.main._direnv_env_usable", return_value=True),
        patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")),
        patch("os.execvp", side_effect=SystemExit(0)) as handoff,
        patch("ai_cli.main._pty_handoff", return_value=handoff),
    ):
        with pytest.raises(SystemExit):
            cli()
    executable, command = handoff.call_args[0]
    assert executable == sys.executable
    assert command[:4] == [sys.executable, "-m", "ai_cli.direnv_exec", str(tmp_path)]
    assert command[4] == "claude"


def test_given_windows_session_script_when_direnv_exports_a_windows_path_then_the_shell_path_is_its_own(
    tmp_path, monkeypatch
):
    bash = session_script.shutil.which("bash")
    if bash is None:
        pytest.skip("the session script needs bash")
    monkeypatch.setattr(session_script, "_native_windows_launcher", lambda: True)
    script = session_script.get_engine_script("c", "session-1", "c-session-1", "c-session-", "myproject")
    prelude = script[script.index("direnv() {\n") : script.index("\n}\n", script.index("direnv() {\n")) + 3]
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "direnv").write_text("#!/bin/sh\nprintf '%s\\n' \"export PATH='C:\\\\fake;C:\\\\other'\"\n")
    (tools / "direnv").chmod(0o755)
    if sys.platform != "win32":
        # MSYS2's cygpath, for this one path list.
        (tools / "cygpath").write_text("#!/bin/sh\nprintf '%s\\n' /c/fake:/c/other\n")
        (tools / "cygpath").chmod(0o755)
    body = tmp_path / "load.sh"
    body.write_text(
        f"PATH={shlex.quote(tools.as_posix())}:$PATH\n"
        + prelude
        + 'eval "$(direnv export bash)"\nprintf "%s" "$PATH"\n'
    )
    result = subprocess.run(
        [bash, body.as_posix()],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.stdout == "/c/fake:/c/other", result.stderr


class _WindowsSys:
    """The launcher's view of ``sys`` on Windows, and only the launcher's."""

    platform = "win32"

    def __getattr__(self, name: str):
        return getattr(sys, name)


def test_given_windows_when_a_bare_launch_hands_off_then_it_waits_for_the_agent_and_returns_its_status(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main, "sys", _WindowsSys())
    monkeypatch.setattr(main, "_run_on_this_console", _RUN_ON_THIS_CONSOLE)
    ran = []

    def agent(args, executable=None, env=None):
        ran.append(args)
        return 3

    with (
        patch("sys.argv", ["ai", "c", "--bare"]),
        patch("ai_cli.config.load_config", return_value={}),
        patch("ai_cli.session.get_project_prefix", return_value="session"),
        patch("ai_cli.session.is_current_project_resolved", return_value=True),
        patch("ai_cli.session.create_worktree", return_value=(tmp_path, False)),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.main.trigger_background_update"),
        patch("ai_cli.direnv_setup.is_bypassed", return_value=True),
        patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")),
        patch("os.execvp", side_effect=AssertionError("Windows has no exec")),
        patch("ai_cli.foreground.run_in_foreground", side_effect=agent),
        # Windows has no pty API, so a bare launch there never takes the pty handoff.
        patch(
            "ai_cli.compact_transport.probe_pty",
            return_value=compact_transport.PtyProbe(False, "no termios module", platform_supported=False),
        ),
    ):
        with pytest.raises(SystemExit) as exit_info:
            cli()
    assert exit_info.value.code == 3
    assert [Path(args[0]).name for args in ran] == ["claude"]


def test_given_an_agent_only_on_the_project_path_when_doctor_runs_then_it_reports_where_a_launch_finds_it(
    project, monkeypatch
):
    from conftest import run_cli

    _tools, agents, root, env, _ran = project
    _program(agents, "claude", "import sys\n")
    monkeypatch.chdir(root)
    # Only the stand-in agents, so the host's own agent CLIs cannot answer.
    loaded = dict(env, PATH=str(agents))
    with (
        patch("ai_cli.direnv_setup.is_bypassed", return_value=False),
        patch("ai_cli.direnv_setup.find_envrc", return_value=root / ".envrc"),
        patch("ai_cli.direnv_exec.loaded_environment", return_value=(loaded, "")) as load,
        patch("ai_cli.direnv_setup.ensure_direnv", return_value=MagicMock(installed=True, detail="usable")),
    ):
        _code, stdout, _err = run_cli(["ai", "doctor", "--dry-run"])
    assert load.call_args.args[0] == root
    claude = next(line for line in stdout.splitlines() if " claude " in line)
    codex = next(line for line in stdout.splitlines() if " codex " in line)
    assert "OK" in claude and str(agents) in claude, stdout
    assert "MISS" in codex and "project environment's PATH" in codex, stdout
