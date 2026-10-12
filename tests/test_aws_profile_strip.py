"""An ``ai c`` session must not hand an inherited SSO ``AWS_PROFILE`` to Claude Code.

The reported failure: on an EC2 host whose instance role can call the model provider,
the shell that started the tmux server exported ``AWS_PROFILE`` naming an SSO-backed
profile. Every pane inherited it, so every agent's model calls rode a token that only a
human browser approval renews, and agents died mid-run when it expired.

Each test runs the generated session script as a real tmux pane, exactly as ``ai c``
hands it to ``tmux new-session``, with the profile already in the pane's environment at
launch. Only ``aws``, ``claude`` and the tool are stand-ins.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from aws_profile_stubs import (
    INSTANCE_ROLE_ARN,
    SSO_PROFILE,
    SSO_ROLE_ARN,
    TOOL,
    read_report,
    write_config,
    write_stubs,
)
from conftest import tmux_runnable

from ai_cli.session_script import get_engine_script

_TMUX_RUNNABLE, _TMUX_SKIP_REASON = tmux_runnable()
_OBSERVE_SECONDS = float(os.environ.get("AI_CLI_TEST_PROCESS_WAIT_SECONDS", "60"))

pytestmark = [
    pytest.mark.real_tmux,
    pytest.mark.skipif(sys.platform == "win32", reason="real tmux server behavior unverified under MSYS2 CI"),
]


@pytest.fixture
def tmux_socket():
    if not _TMUX_RUNNABLE:
        pytest.skip(_TMUX_SKIP_REASON)
    sock_dir = tempfile.mkdtemp(prefix="ai-cli-awsprofile-")
    sock = f"{sock_dir}/tmux.sock"
    yield sock
    subprocess.run(["tmux", "-S", sock, "kill-server"], capture_output=True, check=False)
    shutil.rmtree(sock_dir, ignore_errors=True)


def _run_session(sock: str, tmp_path: Path, shell: str, arn: str, monkeypatch) -> dict[str, str]:
    fake_bin = tmp_path / "fakes"
    report = tmp_path / "report"
    write_stubs(fake_bin, report, arn)
    (fake_bin / "ai").write_text(
        '#!/bin/sh\nif [ "$1" = internal ] && [ "$2" = get-version ]; then printf "%s\\n" unknown; fi\nexit 0\n'
    )
    (fake_bin / "ai").chmod(0o755)
    (fake_bin / "python3").symlink_to(sys.executable)
    config_home = tmp_path / "config"
    write_config(config_home, [TOOL])
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    zdotdir = tmp_path / "zdotdir"
    zdotdir.mkdir()
    session = f"awsprofile-{Path(shell).name}"
    sessions = tmp_path / "state" / "ai-cli-utils" / "sessions"
    sessions.mkdir(parents=True)
    script = sessions / f"{session}.sh"
    script.write_text(
        get_engine_script("c", "awsprofile", session, "c-awsprofile-", "awsprofile", worktree_dir=str(tmp_path))
    )
    created = subprocess.run(
        ["tmux", "-S", sock, "new-session", "-d", "-s", session, "-x", "120", "-y", "30", shell, str(script)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": os.pathsep.join((str(fake_bin), os.environ.get("PATH", ""))),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "XDG_CONFIG_HOME": str(config_home),
            "ZDOTDIR": str(zdotdir),
            "AWS_PROFILE": SSO_PROFILE,
        },
        check=False,
    )
    assert created.returncode == 0, created.stderr
    deadline = time.monotonic() + _OBSERVE_SECONDS
    while time.monotonic() < deadline and "agent.done" not in read_report(report):
        time.sleep(0.05)
    values = read_report(report)
    pane = subprocess.run(
        ["tmux", "-S", sock, "capture-pane", "-p", "-t", session], capture_output=True, text=True, check=False
    ).stdout
    assert "agent.done" in values, f"the agent never ran to the end: {pane!r}"
    return values


@pytest.mark.parametrize("shell_name", ["bash", "zsh"])
def test_given_pane_inherits_sso_profile_on_instance_role_host_when_session_starts_agent_then_agent_lacks_it(
    tmux_socket, tmp_path, monkeypatch, shell_name
):
    shell = shutil.which(shell_name)
    if shell is None:
        pytest.skip(f"{shell_name} is not installed")

    values = _run_session(tmux_socket, tmp_path, shell, INSTANCE_ROLE_ARN, monkeypatch)

    assert values["agent.AWS_PROFILE"] == "unset", "the agent's model calls would still ride the SSO token"
    assert values["agent.AI_SSO_PROFILE"] == SSO_PROFILE, "the profile name was dropped instead of set aside"
    assert values["agent.AWS_BEARER_TOKEN_BEDROCK"] == "unset"
    assert values["tool.AWS_PROFILE"] == SSO_PROFILE, "a configured tool run by the agent lost its profile"
    assert values["probe.AWS_PROFILE"] == "unset"
    assert values["probe.AWS_DEFAULT_PROFILE"] == "unset"
    assert values["probe.config"] == "absent"
    assert values["probe.credentials"] == "absent"
    assert values["probe.args"] == "sts get-caller-identity --query Arn --output text"


@pytest.mark.parametrize("shell_name", ["bash", "zsh"])
def test_given_pane_inherits_sso_profile_and_ambient_identity_is_sso_when_session_starts_agent_then_profile_kept(
    tmux_socket, tmp_path, monkeypatch, shell_name
):
    shell = shutil.which(shell_name)
    if shell is None:
        pytest.skip(f"{shell_name} is not installed")

    values = _run_session(tmux_socket, tmp_path, shell, SSO_ROLE_ARN, monkeypatch)

    assert values["agent.AWS_PROFILE"] == SSO_PROFILE
    assert values["agent.AI_SSO_PROFILE"] == "unset"
    assert values["tool.AWS_PROFILE"] == SSO_PROFILE
