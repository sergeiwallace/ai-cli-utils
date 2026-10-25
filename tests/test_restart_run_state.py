"""Lifecycle insertions preserve the supported supervisor upgrade contract."""

import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest
from test_restart_supervisor import SESSION, _wait_for
from test_restart_supervisor import supervisor_fixture as supervisor_fixture
from test_session_lifecycle_script import shell_environment as shell_environment
from test_session_restart import world as world

from ai_cli import config, main, session_restart
from ai_cli import session_registry as registry
from ai_cli import session_run_state as state
from ai_cli.session_lifecycle_script import SESSION_STATE_FUNCTIONS
from ai_cli.session_script import get_engine_script


@pytest.fixture
def programs(tmp_path, monkeypatch):
    legacy = (Path(__file__).parent / "fixtures/session_restart_legacy.txt").read_text(encoding="utf-8")
    legacy = legacy.replace("__PROJECT_DIR__", tmp_path.as_posix())
    metadata_line = next(line for line in legacy.splitlines() if "printf '%s'" in line and "session-meta-" in line)
    metadata = json.loads(shlex.split(metadata_line)[2])
    monkeypatch.setattr(main._session_script, "resolve_session_shell", lambda: "bash")
    return legacy, main._engine_script_from_meta(metadata)


@pytest.fixture
def promotion_environment(shell_environment):
    shell, environment, name, root = shell_environment
    allowed = {"PATH", "HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "PYTHONPATH", "AI_CLI_RUN_GENERATION", "ZDOTDIR"}
    return shell, {key: value for key, value in environment.items() if key in allowed}, name, root


def test_given_pre_feature_program_when_ac_i_1_compared_then_lifecycle_insertions_are_compatible(programs):
    legacy, current = programs
    assert session_restart._compatible(legacy, current)
    assert "_ai_mark_running || exit 76" in current
    assert "(( _promotion_status == 2 )) && break" in current


def test_given_program_launched_before_host_refresh_when_compared_then_it_is_compatible(programs):
    _, current = programs
    bracket = r"(?ms)^    # BEGIN HOST REFRESH[^\n]*\n.*?^    # END HOST REFRESH\n"
    before_feature = re.sub(bracket, "", current)
    assert before_feature != current
    assert "resolve-host-env" in current and "resolve-host-env" not in before_feature
    assert session_restart._compatible(before_feature, current)
    assert session_restart._compatible(current, before_feature)


def test_given_program_launched_before_plugin_refresh_when_compared_then_it_is_compatible(programs):
    _, current = programs
    bracket = r"(?ms)^    # BEGIN PLUGIN REFRESH[^\n]*\n.*?^    # END PLUGIN REFRESH\n"
    before_feature = re.sub(bracket, "", current)
    assert before_feature != current
    assert session_restart._compatible(before_feature, current)
    assert session_restart._compatible(current, before_feature)


@pytest.mark.parametrize("program", ["legacy", "current"])
def test_given_outside_insertion_edit_when_ac_i_2_compared_then_program_is_rejected(programs, program):
    legacy, current = programs
    assert session_restart._compatible(legacy, current)
    source = legacy if program == "legacy" else current
    changed = source.replace("_child_status=$?", "_child_status=0")
    assert changed != source
    assert not session_restart._compatible(changed, current)


def test_given_outside_insertion_edit_when_ac_i_2_gate_runs_then_no_action_is_taken(world):
    code, report, _ = world.cli("-Y", world.record["sessionId"])
    assert code == 0 and report["reason"] == "ready"
    source = world.script.read_text(encoding="utf-8")
    changed = source.replace("_child_status=$?", "_child_status=0")
    assert changed != source
    world.script.write_text(changed, encoding="utf-8")
    code, report, _ = world.cli("-X", world.record["sessionId"])
    assert code == 1 and report["reason"] == "supervisor-upgrade-required"
    assert not world.signals and not world.injected


def _install_real_lifecycle(ai, fault_root):
    """Delegate lifecycle commands to the real CLI, injecting only storage faults."""
    source = ai.read_text(encoding="utf-8")
    insertion = f"""
if args and args[0] == 'sessions':
    from pathlib import Path
    from ai_cli import session_registry as registry
    from ai_cli.main import cli
    original_write = registry._write
    def write(path, doc):
        fault = Path({str(fault_root)!r}) / 'storage-fault'
        if 'exited' in args and fault.exists():
            mode = fault.read_text(encoding='utf-8')
            if mode == 'both' or path == registry.registry_path():
                (fault.parent / 'write-failed').touch()
                raise OSError('fixture storage unavailable')
        original_write(path, doc)
    registry._write = write
    cli()
    sys.exit(0)
"""
    assert "args = sys.argv[1:]\n" in source
    ai.write_text(source.replace("args = sys.argv[1:]\n", "args = sys.argv[1:]\n" + insertion), encoding="utf-8")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX supervisor fixture")
@pytest.mark.real_tmux
@pytest.mark.parametrize("fault", ["none", "registry", "both"])
def test_given_old_supervisor_when_ac_i_4_child_refreshes_then_real_final_exit_is_durable(supervisor_fixture, fault):
    root, script, supervisor = supervisor_fixture
    # The supervisor inherits this process's environment. Check it here too, because
    # macOS hides a platform binary's environment from psutil and the line below
    # then passes vacuously.
    assert os.environ.get("AI_CLI_RUN_GENERATION") is None
    assert not psutil.Process(supervisor.pid).environ().get("AI_CLI_RUN_GENERATION")

    def launches():
        path = root / "launches"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    initial = _wait_for(launches, supervisor)[0]
    # The legacy quick-exit guard must see a healthy initial agent.
    observed = time.monotonic()
    _wait_for(lambda: time.monotonic() - observed >= 4, supervisor)
    state.register(SESSION, recipe={"argv": ["ai", "c", "1"], "cwd": str(root / "myproject")}, host="host-a")
    _install_real_lifecycle(root / "bin/ai", root)
    metadata = session_restart._object(config.get_xdg_state_home() / f"session-meta-{SESSION}.json")
    old_inode = script.stat().st_ino
    main._write_launch_script_if_changed(script, main._engine_script_from_meta(metadata))
    assert script.stat().st_ino != old_inode
    os.kill(initial["pid"], signal.SIGTERM)
    replacement = _wait_for(lambda: launches()[1] if len(launches()) == 2 else None, supervisor)
    assert psutil.Process(replacement["ppid"]).ppid() == supervisor.pid
    record = registry.load_registry()["sessions"][0]
    assert record["run_state"] == "running"
    generation, sequence = record["generation"], record["transition_seq"]
    assert generation and sequence > 0
    assert not state.mark_running(SESSION, generation="stale-generation", sequence=sequence + 1)
    assert not state.mark_running(SESSION, generation=generation, sequence=sequence)
    assert registry.load_registry()["sessions"][0] == record

    if fault != "none":
        (root / "storage-fault").write_text(fault, encoding="utf-8")
    (config.get_xdg_state_home() / f"session-int-exit-{SESSION}").touch()
    os.kill(replacement["pid"], signal.SIGTERM)
    if fault == "both":
        _wait_for(lambda: (root / "write-failed").exists(), supervisor)
        time.sleep(1.2)
        assert supervisor.poll() is None
        assert len(launches()) == 2
        assert not (config.get_xdg_state_home() / f"session-final-exit-{SESSION}").exists()
        assert registry.load_registry()["sessions"][0]["run_state"] == "running"
        assert (root / "storage-fault").read_text(encoding="utf-8") == "both"
        (root / "storage-fault").unlink()
    assert supervisor.wait(timeout=30) == 0
    if fault == "registry":
        safety = state.tombstone_path(SESSION, "host-a")
        saved = json.loads(safety.read_text(encoding="utf-8"))
        assert saved["deny_boot"] and saved["generation"] == generation
        assert saved["record"]["supervisor_exit"]["cause"] == "manual_exit"
        assert (root / "storage-fault").read_text(encoding="utf-8") == "registry"
        (root / "storage-fault").unlink()
        assert not state.mark_running(SESSION, generation=generation, sequence=saved["record"]["transition_seq"] + 1)
        state.reconcile_safety()
    final = registry.load_registry()["sessions"][0]
    assert final["run_state"] == "exited"
    assert final["supervisor_exit"]["cause"] == "manual_exit"
    assert state.cause(final, "next-boot") == "manual_exit"
    assert not state.mark_running(SESSION, generation=generation, sequence=sequence)
    assert len(launches()) == 2
    calls = [json.loads(line) for line in (root / "tmux-calls").read_text(encoding="utf-8").splitlines()]
    assert not any("respawn-pane" in call for call in calls)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX supervisor fixture")
@pytest.mark.parametrize("fault", ["none", "registry", "both"])
def test_given_promotion_interrupt_when_ac_i_3_breaks_then_final_exit_persists_first(promotion_environment, fault):
    shell, env, name, tmp_path = promotion_environment
    generated = get_engine_script("c", "session-1", name, "c-session-", "myproject")
    start = generated.index("          # A Ctrl+C while a child is still starting")
    end = generated.index("          exit 1", start)
    interrupt = generated[start:end]
    assert "(( _promotion_status == 2 )) && break" in interrupt
    assert interrupt.index("_ai_final_manual_exit") < interrupt.index("(( _promotion_status == 2 )) && break")
    ai = tmp_path / "bin/ai"
    # This fixture's executable is a shell trampoline; replace it after inspecting it.
    assert '-m ai_cli.main "$@"' in ai.read_text(encoding="utf-8")
    ai.write_text(f"#!{sys.executable}\nimport sys\nargs = sys.argv[1:]\n", encoding="utf-8")
    _install_real_lifecycle(ai, tmp_path)
    if fault != "none":
        (tmp_path / "storage-fault").write_text(fault, encoding="utf-8")
    body = (
        SESSION_STATE_FUNCTIONS
        + f"\ntmux_session={name}\n_ai_state_dir={shlex.quote(str(registry.registry_path().parent))}\n"
        + '_ai_emit() { printf "%s\\n" "$*"; }\n_promotion_status=2\nwhile true; do\n'
        + interrupt
        + "\ndone\nprintf shell-returned\n"
    )
    process = subprocess.Popen([shell, "-c", body], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        if fault == "both":
            _wait_for(lambda: (tmp_path / "write-failed").exists(), process)
            time.sleep(1.2)
            assert process.poll() is None
            assert not (registry.registry_path().parent / f"session-final-exit-{name}").exists()
            assert (tmp_path / "storage-fault").read_text(encoding="utf-8") == "both"
            (tmp_path / "storage-fault").unlink()
        out, err = process.communicate(timeout=20)
        assert process.returncode == 0, err
        assert out.endswith("shell-returned")
        if fault == "both":
            assert "retrying" in out
        if fault == "registry":
            saved = json.loads(state.tombstone_path(name, "host-a").read_text(encoding="utf-8"))
            assert saved["deny_boot"] and saved["record"]["supervisor_exit"]["cause"] == "manual_exit"
            assert (tmp_path / "storage-fault").read_text(encoding="utf-8") == "registry"
            (tmp_path / "storage-fault").unlink()
            state.reconcile_safety()
        assert registry.load_registry()["sessions"][0]["supervisor_exit"]["cause"] == "manual_exit"
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
