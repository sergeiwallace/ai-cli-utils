"""Exercise legacy generation acquisition without an agent or real registry."""

import os
import shlex
import shutil
import subprocess
import sys

import pytest

from ai_cli.session_script import get_engine_script


@pytest.fixture
def legacy_begin(tmp_path):
    shell = shutil.which("bash")
    if shell is None or sys.platform == "win32":
        pytest.skip("POSIX shell signals are required")
    generated = get_engine_script("c", "session-1", "c-session-1", "c-session-", "myproject")
    fragment = generated.split("# BEGIN RUN STATE legacy-child-generation\n", 1)[1].split("# END RUN STATE", 1)[0]
    counter = tmp_path / "session-begin-failures-c-session-1"
    delays = tmp_path / "delays"
    parked = tmp_path / "parked"
    prefix = f"""
_ai_state_dir={shlex.quote(str(tmp_path))}
tmux_session=c-session-1
ai() {{
  printf 'called\\n' >> {shlex.quote(str(tmp_path / "calls"))}
  if [[ -f {shlex.quote(str(tmp_path / "success"))} ]]; then
    printf 'generation-1\\n'
  else
    printf 'cannot acquire a durable supervisor generation: session has no boot recipe\\n' >&2
    return 1
  fi
}}
sleep() {{
  printf '%s\\n' "$1" >> {shlex.quote(str(delays))}
  if [[ -f {shlex.quote(str(counter))} ]] && (( $(cat {shlex.quote(str(counter))}) >= 20 )); then
    printf ready > {shlex.quote(str(parked))}
    command sleep 0.05
  fi
}}
"""
    environment = {key: value for key, value in os.environ.items() if key != "AI_CLI_RUN_GENERATION"}
    return shell, prefix + fragment + '\nprintf "agent-started:%s" "$AI_CLI_RUN_GENERATION"\n', environment, tmp_path


def test_given_legacy_begin_failures_when_repeated_then_delay_doubles_caps_and_success_resets(legacy_begin):
    shell, body, env, root = legacy_begin
    for _ in range(19):
        result = subprocess.run([shell, "-c", body], env=env, capture_output=True, text=True, check=False, timeout=5)
        assert result.returncode == 76
        assert "agent-started" not in result.stdout
    assert (root / "delays").read_text().splitlines() == ["1", "2", "4", "8", "16", *(["30"] * 14)]
    assert (root / "session-begin-failures-c-session-1").read_text().strip() == "19"
    (root / "success").touch()
    result = subprocess.run([shell, "-c", body], env=env, capture_output=True, text=True, check=False, timeout=5)
    assert result.returncode == 0 and result.stdout == "agent-started:generation-1"
    assert (root / "session-begin-failures-c-session-1").read_text().strip() == "0"
    assert (root / "success").read_bytes() == b""
    (root / "success").unlink()
    result = subprocess.run([shell, "-c", body], env=env, capture_output=True, text=True, check=False, timeout=5)
    assert result.returncode == 76
    assert (root / "delays").read_text().splitlines()[-1] == "1"


def test_given_twentieth_failure_when_acquired_then_clean_stop_prevents_legacy_relaunch(legacy_begin):
    shell, body, env, root = legacy_begin
    (root / "session-begin-failures-c-session-1").write_text("19\n")
    process = subprocess.Popen([shell, "-c", body], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = process.communicate(timeout=2)
        assert process.returncode == 77
        assert (root / "calls").read_text().splitlines() == ["called"]
        assert (root / "session-begin-failures-c-session-1").read_text().strip() == "20"
        assert "agent-started" not in out
        assert err.count("not retrying") == 1
        assert "cannot acquire a durable supervisor generation" in err
        assert "session has no boot recipe" in err
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=2)


def test_given_supervisor_generation_when_child_starts_then_no_begin_or_retry_state(legacy_begin):
    shell, body, env, root = legacy_begin
    env["AI_CLI_RUN_GENERATION"] = "supervisor-generation"
    result = subprocess.run([shell, "-c", body], env=env, capture_output=True, text=True, check=False, timeout=5)
    assert result.returncode == 0 and result.stdout == "agent-started:supervisor-generation"
    assert not list(root.iterdir())
