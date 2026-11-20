import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from ai_cli.agy import conversation_cache, launch_command
from ai_cli.session_script import get_engine_script

SESSION = "myproject-1"
CONVERSATION = "11111111-2222-4333-8444-555555555555"
OTHER_CONVERSATION = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / "myproject" / ".worktrees" / SESSION
    path.mkdir(parents=True)
    return path.resolve()


@pytest.fixture
def agy_command(tmp_path, monkeypatch):
    fake = tmp_path / "fake agy.py"
    fake.write_text(
        "import json, os, sys\nprint(json.dumps([os.getcwd(), os.environ['AI_CLI_SESSION_NAME'], sys.argv[1:]]))\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        return shlex.join([Path(sys.executable).as_posix(), fake.as_posix()])
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "agy"
    executable.write_text(f'#!/bin/sh\nexec {shlex.join([sys.executable, str(fake)])} "$@"\n', encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return "agy"


def _run(workspace, command, *, isolated=True, sandbox=False, extra_args=()):
    return subprocess.run(
        launch_command(command, SESSION, sandbox=sandbox, isolated=isolated, extra_args=list(extra_args)),
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_given_a_new_session_when_launched_then_agy_gets_its_worktree_name_and_options(workspace, agy_command):
    result = _run(workspace, agy_command, sandbox=True, extra_args=["--effort", "high"])

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [str(workspace), SESSION, ["--sandbox", "--effort", "high"]]


@pytest.mark.parametrize(("isolated", "extra"), [(True, []), (False, []), (True, ["--continue"])])
def test_given_cached_conversations_when_relaunched_then_only_the_exact_workspace_is_resumed(
    workspace, agy_command, isolated, extra
):
    cache = conversation_cache(Path.home())
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({str(workspace): CONVERSATION, str(workspace.parent): OTHER_CONVERSATION}))

    result = _run(workspace, agy_command, isolated=isolated, extra_args=extra)

    assert result.returncode == 0, result.stderr
    expected = ["--conversation", CONVERSATION] if isolated and not extra else extra
    assert json.loads(result.stdout) == [str(workspace), SESSION, expected]


def test_given_a_parent_workspace_conversation_when_launched_then_agy_starts_fresh(workspace, agy_command):
    cache = conversation_cache(Path.home())
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({str(workspace.parent): OTHER_CONVERSATION, str(workspace): "invalid"}))

    result = _run(workspace, agy_command)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [str(workspace), SESSION, []]


@pytest.mark.parametrize("command", ["missing-agy", '"unterminated'])
def test_given_an_unstartable_command_when_launched_then_one_error_names_the_command(workspace, command):
    result = _run(workspace, command)

    assert result.returncode == 126
    [error] = result.stderr.splitlines()
    assert repr(command) in error
    assert "[agy] command" in error


def test_given_an_agy_override_when_generating_a_managed_session_then_it_uses_the_same_launch_adapter(workspace):
    command = "agy-wrapper --model 'test model'"
    script = get_engine_script(
        "g",
        SESSION,
        f"g-{SESSION}",
        "g-myproject-",
        "myproject",
        worktree_dir=str(workspace),
        agy_cmd=command,
        agy_args=["--effort", "high"],
    )
    expected = launch_command(command, SESSION, sandbox=False, isolated=True, extra_args=["--effort", "high"])

    assert f"cd -- {shlex.quote(str(workspace))}" in script
    assert f"run_agent {shlex.join(expected)}" in script
    assert f'run_agent {shlex.join(expected)} --prompt-interactive "$resume_msg"' in script
