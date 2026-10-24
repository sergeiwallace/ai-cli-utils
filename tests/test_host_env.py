"""Refresh the launch host without trusting a long-lived pane environment."""

import os
import shlex
import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest

from ai_cli.config import read_relaunch_config
from ai_cli.main import _handle_internal
from ai_cli.session_script import get_engine_script

_POSIX_SHELL_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="fake shell scripts are POSIX-only")


@pytest.fixture
def host_tools(tmp_path, monkeypatch):
    if sys.platform == "win32":
        pytest.skip("fake shell scripts are POSIX-only")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shell = bin_dir / "login-shell"
    tmux = bin_dir / "tmux"

    def configure(shell_body='printf "AI_HOST_FRESH=login-host\\n"', tmux_body='printf "AI_HOST=tmux-host\\n"'):
        for path, body in ((shell, shell_body), (tmux, tmux_body)):
            path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
            path.chmod(0o700)

    configure()
    monkeypatch.setenv("SHELL", str(shell))
    monkeypatch.setenv("AI_HOST", "stale-host")
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.defpath)
    real_run = subprocess.run

    def run(command, *args, **kwargs):
        # Execute our fake tmux through sh; the suite forbids direct tmux spawns.
        if command[0] == "tmux":
            assert shutil.which("tmux") == str(tmux)
            return real_run(["/bin/sh", str(tmux), *command[1:]], *args, **kwargs)
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    return configure, bin_dir


def _resolve(capsys, config=None):
    with patch("ai_cli.config.load_config", return_value=config or {}), pytest.raises(SystemExit) as exited:
        _handle_internal(["resolve-host-env"])
    assert exited.value.code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    return captured.out


def test_given_fresh_login_host_when_resolving_then_login_wins_and_only_host_is_requested(host_tools, capsys):
    configure, _ = host_tools
    configure(
        '[ "$1" = -lic ] || exit 1\n'
        '[ "$2" = \'printf "AI_HOST_FRESH=%s\\n" "${AI_HOST:-}"\' ] || exit 2\n'
        "read -r ignored && exit 3\n"
        'AI_HOST=login-host\n/bin/sh -c "$2"',
        "exit 99",
    )
    assert _resolve(capsys) == "login-shell login-host\n"


@pytest.mark.parametrize(
    "shell_body",
    [
        'printf "AI_HOST_FRESH=\\n"',
        'printf "banner only\\n"',
        'printf "AI_HOST_FRESH=bad host\\n"',
        'printf "AI_HOST_FRESH=bad/host\\n"',
        'printf "AI_HOST_FRESH=valid\\nAI_HOST_FRESH=bad host\\n"',
        'printf "AI_HOST_FRESH=login-host\\n"; exit 1',
        "exit 1",
    ],
)
def test_given_unusable_login_host_when_resolving_then_tmux_wins(host_tools, capsys, shell_body):
    configure, _ = host_tools
    configure(shell_body, '[ "$*" = "show-environment -g AI_HOST" ] || exit 1\nprintf "AI_HOST=tmux-host\\n"')
    assert _resolve(capsys) == "tmux tmux-host\n"


def test_given_banner_noise_when_resolving_then_last_sentinel_wins(host_tools, capsys):
    configure, _ = host_tools
    configure('printf "banner\\nAI_HOST_FRESH=earlier\\nprompt>\\nAI_HOST_FRESH=Host_2.example-1\\nfooter\\n"')
    assert _resolve(capsys) == "login-shell Host_2.example-1\n"


@pytest.mark.parametrize(
    "tmux_body", ['printf "AI_HOST=\\n"', 'printf -- "-AI_HOST\\n"', "exit 1", 'printf "AI_HOST=bad host\\n"']
)
def test_given_both_sources_unusable_when_resolving_then_inherited_host_is_kept(host_tools, capsys, tmux_body):
    configure, _ = host_tools
    configure("exit 1", tmux_body)
    assert _resolve(capsys) == "inherited stale-host\n"


def test_given_changed_timeout_config_when_resolving_then_each_call_honours_current_limit(host_tools, capsys):
    configure, _ = host_tools
    # exec avoids leaving a sleeping descendant behind when the shell times out.
    configure(
        f"exec {shlex.quote(sys.executable)} -c "
        + shlex.quote('import time; time.sleep(0.2); print("AI_HOST_FRESH=login-host")')
    )
    assert _resolve(capsys, {"relaunch": {"host_refresh_timeout_seconds": 0.02}}) == "tmux tmux-host\n"
    assert _resolve(capsys, {"relaunch": {"host_refresh_timeout_seconds": 2}}) == "login-shell login-host\n"


def test_given_default_config_when_reading_relaunch_then_host_refresh_timeout_is_five_seconds():
    assert read_relaunch_config({})["host_refresh_timeout_seconds"] == 5


@pytest.mark.parametrize("value", [0, -1, True, "5", float("inf"), float("nan")])
def test_given_invalid_host_timeout_when_reading_relaunch_then_it_is_rejected(value):
    with pytest.raises(ValueError, match=r"relaunch\.host_refresh_timeout_seconds"):
        read_relaunch_config({"relaunch": {"host_refresh_timeout_seconds": value}})


@_POSIX_SHELL_ONLY
@pytest.mark.parametrize("source", ["login-shell", "tmux", "inherited", "failed-helper"])
def test_given_stale_wrapper_host_when_each_agent_launches_then_host_is_refreshed_or_preserved(
    host_tools, tmp_path, source
):
    configure, bin_dir = host_tools
    host_file = tmp_path / "fresh-host"
    host_file.write_text("first-host", encoding="utf-8")
    shell_body = f'printf "banner\\nAI_HOST_FRESH=%s\\nfooter\\n" "$(cat {shlex.quote(str(host_file))})"'
    tmux_body = f'printf "AI_HOST=%s\\n" "$(cat {shlex.quote(str(host_file))})"'
    configure(shell_body if source == "login-shell" else "exit 1", tmux_body if source == "tmux" else "exit 1")
    ai = bin_dir / "ai"
    ai.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        + (
            "sys.exit(1)\n"
            if source == "failed-helper"
            else "from ai_cli.main import _handle_internal\n_handle_internal(sys.argv[2:])\n"
        ),
        encoding="utf-8",
    )
    ai.chmod(0o700)
    observed = tmp_path / "agent-hosts"
    agent = bin_dir / "agent"
    agent.write_text(f'#!/bin/sh\nprintf "%s\\n" "$AI_HOST" >> {shlex.quote(str(observed))}\n', encoding="utf-8")
    agent.chmod(0o700)
    script = get_engine_script("c", "session-1", "c-session-1", "c-session-", "myproject")
    start = script.index("    while true; do\n      # A double Ctrl+C")
    end = script.index("      start_watcher", start)
    harness = tmp_path / "wrapper.sh"
    harness.write_text(
        "set -e\n"
        f"_child_int_exit_file={shlex.quote(str(tmp_path / 'no-exit'))}\n"
        f"_child_int_escape_file={shlex.quote(str(tmp_path / 'no-escape'))}\n"
        f"_script_stable_path={shlex.quote(str(tmp_path / 'no-script'))}\n"
        f"_ai_reload_run_file={shlex.quote(str(tmp_path / 'no-reload'))}\n"
        '_ai_close_progress() { :; }\n_ai_emit() { printf "%s\\n" "$*" >&2; }\nn=0\n'
        + script[start:end]
        + "agent\nn=$((n+1))\n[[ $n -lt 2 ]] || break\n"
        + f"printf second-host > {shlex.quote(str(host_file))}\ndone\n",
        encoding="utf-8",
    )
    completed = subprocess.run(["bash", str(harness)], capture_output=True, text=True, timeout=30, check=False)
    assert completed.returncode == 0, completed.stderr
    expected = ["first-host", "second-host"] if source in ("login-shell", "tmux") else ["stale-host"] * 2
    assert observed.read_text(encoding="utf-8").splitlines() == expected
    if source in ("login-shell", "tmux"):
        assert completed.stderr.count("info host") == 2
        assert "stale-host -> first-host" in completed.stderr
        assert "first-host -> second-host" in completed.stderr
        assert ("login shell" if source == "login-shell" else "tmux") in completed.stderr
    else:
        assert "inherited" in completed.stderr and "stale-host" in completed.stderr
