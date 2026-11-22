"""Refresh the launch host without trusting a long-lived pane environment."""

import os
import shlex
import shutil
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from ai_cli.config import get_xdg_config_home, read_relaunch_config
from ai_cli.main import _handle_internal
from ai_cli.session_script import get_engine_script

_POSIX_SHELL_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="fake shell scripts are POSIX-only")
# The default five-second probe limit is exceeded by a fake /bin/sh on a loaded host, which
# turns an answering probe into a silent fallback; only the timeout test asks for a short one.
_GENEROUS_PROBE_LIMIT = {"relaunch": {"host_refresh_timeout_seconds": 60}}


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
    with (
        patch("ai_cli.config.load_config", return_value=config or _GENEROUS_PROBE_LIMIT),
        pytest.raises(SystemExit) as exited,
    ):
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
        'export AI_HOST=login-host\n/bin/sh -c "$2"',
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


def test_given_a_profile_that_does_not_set_the_host_when_resolving_then_the_stale_value_is_not_echoed_back(
    host_tools, capsys
):
    configure, _ = host_tools
    # A login shell whose profile never exports AI_HOST reports whatever it inherited.
    configure('printf "AI_HOST_FRESH=%s\\n" "${AI_HOST:-}"')
    assert _resolve(capsys) == "tmux tmux-host\n"


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
        + shlex.quote('import time; time.sleep(0.2); print("AI_HOST_FRESH=login-host")'),
        # A fallback probe shares the short limit, and even /bin/sh can outlast it on a loaded host.
        "exit 1",
    )
    assert _resolve(capsys, {"relaunch": {"host_refresh_timeout_seconds": 0.02}}) == "inherited stale-host\n"
    # The helper answers in well under a second; the generous limit only absorbs host load.
    assert _resolve(capsys, {"relaunch": {"host_refresh_timeout_seconds": 60}}) == "login-shell login-host\n"


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
    get_xdg_config_home().mkdir(parents=True, exist_ok=True)
    (get_xdg_config_home() / "config.toml").write_text(
        "[relaunch]\nhost_refresh_timeout_seconds = 60\n", encoding="utf-8"
    )
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
    completed = subprocess.run(["bash", str(harness)], capture_output=True, text=True, timeout=120, check=False)
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


_PROBE_CALLER = """
import sys
from ai_cli import host_env, tmux_binary

def no_tmux(*_args):
    raise tmux_binary.TmuxBinaryMissing("no tmux in this test")

host_env.tmux_binary.argv = no_tmux
print(host_env.resolve_host_env({"relaunch": {"host_refresh_timeout_seconds": float(sys.argv[1])}}), flush=True)
"""


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_until(predicate, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


@pytest.fixture
def stalled_login_shell(tmp_path):
    """A login shell that records the pid of a process it leaves stalled, and reaps it after."""
    if sys.platform == "win32":
        pytest.skip("fake shell scripts are POSIX-only")
    pid_file = tmp_path / "stalled-pid"
    shell = tmp_path / "login-shell"
    recorded = []

    def configure(body):
        shell.write_text("#!/bin/sh\n" + body.replace("PID_FILE", shlex.quote(str(pid_file))) + "\n", encoding="utf-8")
        shell.chmod(0o700)
        env = {**os.environ, "SHELL": str(shell)}
        env.pop("AI_HOST", None)
        return env

    def stalled_pid(seconds=30):
        if not _wait_until(lambda: pid_file.is_file() and pid_file.read_text(encoding="utf-8").strip(), seconds):
            return None
        recorded.append(int(pid_file.read_text(encoding="utf-8")))
        return recorded[-1]

    yield configure, stalled_pid
    for pid in recorded:
        if _alive(pid):
            os.kill(pid, 9)


def test_given_a_stalled_login_shell_when_the_caller_is_killed_mid_probe_then_no_shell_survives(stalled_login_shell):
    configure, stalled_pid = stalled_login_shell
    env = configure("echo $$ > PID_FILE\nexec sleep 60")
    caller = subprocess.Popen([sys.executable, "-c", _PROBE_CALLER, "60"], env=env, stdout=subprocess.DEVNULL)
    try:
        shell_pid = stalled_pid()
        assert shell_pid is not None, "the login shell never started"
        caller.kill()
        caller.wait(timeout=10)
        assert _wait_until(lambda: not _alive(shell_pid), 5), "the login shell outlived its killed caller"
    finally:
        if caller.poll() is None:
            caller.kill()
            caller.wait()


def test_given_a_login_shell_whose_child_holds_its_output_when_the_probe_times_out_then_it_returns_and_kills_both(
    stalled_login_shell,
):
    configure, stalled_pid = stalled_login_shell
    env = configure("sleep 60 &\necho $! > PID_FILE\nwait")
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _PROBE_CALLER, "20"],
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    finally:
        child_pid = stalled_pid(seconds=0)
    assert child_pid is not None, "the login shell never started its child"
    assert completed.stdout == "('inherited', '')\n", completed.stderr
    assert _wait_until(lambda: not _alive(child_pid), 5), "the login shell's child outlived the timed-out probe"


_LEAVES_ITS_GROUP = (
    'exec "' + sys.executable + '" -c "import os, sys, time; os.setpgid(0, 0); '
    "open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(60)\" PID_FILE"
)


def test_given_a_login_shell_that_leaves_the_probe_group_when_the_caller_is_killed_then_it_does_not_survive(
    stalled_login_shell,
):
    configure, stalled_pid = stalled_login_shell
    env = configure(_LEAVES_ITS_GROUP)
    caller = subprocess.Popen([sys.executable, "-c", _PROBE_CALLER, "60"], env=env, stdout=subprocess.DEVNULL)
    try:
        shell_pid = stalled_pid()
        assert shell_pid is not None, "the login shell never started"
        assert os.getpgid(shell_pid) == shell_pid, "the stand-in did not leave the probe's process group"
        caller.kill()
        caller.wait(timeout=10)
        assert _wait_until(lambda: not _alive(shell_pid), 5), (
            "a login shell in its own group outlived its killed caller"
        )
    finally:
        if caller.poll() is None:
            caller.kill()
            caller.wait()


def test_given_a_login_shell_that_leaves_the_probe_group_when_the_probe_times_out_then_it_is_killed(
    stalled_login_shell,
):
    configure, stalled_pid = stalled_login_shell
    env = configure(_LEAVES_ITS_GROUP)
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _PROBE_CALLER, "5"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        shell_pid = stalled_pid(seconds=0)
    assert shell_pid is not None, "the login shell never started"
    assert completed.stdout == "('inherited', '')\n", completed.stderr
    assert _wait_until(lambda: not _alive(shell_pid), 5), "a login shell in its own group outlived the timed-out probe"
