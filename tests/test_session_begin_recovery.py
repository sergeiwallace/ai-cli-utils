"""Generation acquisition must survive template upgrades and stop old supervisors."""

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time

import pytest
from click.testing import CliRunner

from ai_cli import config
from ai_cli import session_registry as registry
from ai_cli import session_run_state as state
from ai_cli.main import _cli_group
from ai_cli.session_script import get_engine_script


@pytest.fixture
def launch_metadata(tmp_path):
    return {
        "engine": "c",
        "ai_name": "session-1",
        "session": "c-session-1",
        "project_prefix": "myproject",
        "project_name": "myproject",
        "worktree_dir": str(tmp_path),
        "is_remote": False,
        "sandbox": False,
    }


@pytest.mark.parametrize("engine", ["c", "g", "p", "cx"])
@pytest.mark.parametrize("remote", [False, True])
def test_given_unregistered_launch_when_generation_recovers_then_exact_local_recipe_is_durable(
    launch_metadata, monkeypatch, engine, remote
):
    meta = launch_metadata | {"engine": engine, "is_remote": remote}
    name = f"{engine}-{'r-' if remote else ''}session-1"
    meta["session"] = name
    monkeypatch.setattr(config, "load_config", lambda: {"machine": {"host_id": "host-a"}})
    result = CliRunner().invoke(_cli_group, ["sessions", "_begin", name, "--launch-metadata", json.dumps(meta)])
    assert result.exit_code == 0, result.output
    record = registry.load_registry()["sessions"][0]
    assert record["name"] == name and record["kind"] == "local"
    assert record["generation"] == result.output.strip()
    assert record["relaunch"] == "never"
    assert state.valid_boot_recipe(record)
    assert record["boot_recipe"]["options"]["is_remote"] is remote
    assert record["boot_recipe"]["cwd"] == meta["worktree_dir"]
    assert state.mark_running(name, generation=record["generation"])


@pytest.mark.parametrize("existing", ["recipe", "legacy", "foreign"])
def test_given_existing_record_when_generation_recovers_then_policy_and_ownership_are_preserved(
    launch_metadata, monkeypatch, existing
):
    monkeypatch.setattr(config, "load_config", lambda: {"machine": {"host_id": "host-a"}})
    name = launch_metadata["session"]
    state.register(name, recipe={"argv": ["ai", "c", "1"], "cwd": launch_metadata["worktree_dir"]}, host="host-a")

    def prepare(doc):
        record = doc["sessions"][0]
        record.update(relaunch="always", custom="keep")
        if existing != "recipe":
            record.pop("boot_recipe")
        if existing == "foreign":
            record["owning_host"] = "host-b"
        return True

    registry._mutate(prepare)
    before = registry.load_registry()["sessions"][0]
    result = CliRunner().invoke(
        _cli_group, ["sessions", "_begin", name, "--launch-metadata", json.dumps(launch_metadata)]
    )
    after = registry.load_registry()["sessions"][0]
    if existing == "foreign":
        assert result.exit_code != 0
        assert after == before
    else:
        assert result.exit_code == 0, result.output
        assert after["relaunch"] == "always" and after["custom"] == "keep"
        assert after["id"] == before["id"]
        if existing == "recipe":
            assert after["boot_recipe"] == before["boot_recipe"]
        else:
            assert state.valid_boot_recipe(after)


@pytest.mark.parametrize("change", [{"session": "c-session-2"}, {"engine": "bad"}, {"worktree_dir": "relative"}])
def test_given_mismatched_metadata_when_generation_recovers_then_no_record_is_created(launch_metadata, change):
    result = CliRunner().invoke(
        _cli_group,
        ["sessions", "_begin", launch_metadata["session"], "--launch-metadata", json.dumps(launch_metadata | change)],
    )
    assert result.exit_code != 0
    assert registry.load_registry()["sessions"] == []


@pytest.fixture(params=["bash", "zsh"])
def legacy_wrapper(tmp_path, request):
    shell = shutil.which(request.param)
    if shell is None or os.name != "posix":
        pytest.skip("POSIX shell required")
    generated = get_engine_script("c", "session-1", "c-session-1", "c-session-", "myproject")
    fragment = generated.split("# BEGIN RUN STATE legacy-child-generation\n", 1)[1].split("# END RUN STATE", 1)[0]
    blocker = shlex.join(
        [
            sys.executable,
            "-c",
            "import signal, sys, time; from pathlib import Path; "
            "signal.signal(signal.SIGINT, lambda *_: sys.exit(130)); "
            f"Path({str(tmp_path / 'ready')!r}).write_text('ready'); time.sleep(30)",
        ]
    )
    child = tmp_path / "child.sh"
    child.write_text(
        f"_ai_state_dir={shlex.quote(str(tmp_path))}\ntmux_session=c-session-1\n"
        'ai() { printf call >> "$_ai_state_dir/calls"; '
        f'if [[ -f "$_ai_state_dir/slow" ]]; then {blocker}; fi; return 1; }}\n'
        f"sleep() {{ {blocker}; }}\n" + fragment + "\nprintf agent-started\n"
    )
    # The historical supervisor's restart-unless-77-or-79 contract, in a real shell.
    wrapper = (
        'trap \':\' INT\nwhile true; do "$1" "$2"; rc=$?; if [ "$rc" = 77 ] || [ "$rc" = 79 ]; then exit 0; fi; done'
    )
    env = {k: v for k, v in os.environ.items() if k != "AI_CLI_RUN_GENERATION"}
    env["ZDOTDIR"] = str(tmp_path)
    return shell, child, wrapper, env


@pytest.mark.parametrize("interrupt", [False, "delay", "acquire"])
def test_given_old_supervisor_when_begin_exhausts_or_is_interrupted_then_launcher_stops_without_relaunch(
    legacy_wrapper, tmp_path, interrupt
):
    shell, child, wrapper, env = legacy_wrapper
    if interrupt == "acquire":
        (tmp_path / "slow").touch()
    (tmp_path / "session-begin-failures-c-session-1").write_text("0\n" if interrupt else "19\n")
    process = subprocess.Popen(
        [shell, "-c", wrapper, "wrapper", shell, str(child)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 2
        if interrupt:
            while not (tmp_path / "ready").exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert (tmp_path / "ready").exists()
            os.killpg(process.pid, signal.SIGINT)
        out, err = process.communicate(timeout=2)
        assert process.returncode == 0, err
        assert "agent-started" not in out
        assert (tmp_path / "calls").read_text() == "call"
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            process.communicate(timeout=2)


@pytest.mark.real_tmux
@pytest.mark.parametrize("interrupt", [False, "delay", "acquire"])
def test_given_tmux_legacy_supervisor_when_begin_stops_then_pane_accepts_shell_input(
    legacy_wrapper, tmp_path, interrupt
):
    import tempfile
    from pathlib import Path

    from conftest import tmux_runnable

    runnable, reason = tmux_runnable()
    if not runnable:
        pytest.skip(reason)
    shell, child, wrapper, env = legacy_wrapper
    if interrupt == "acquire":
        (tmp_path / "slow").touch()
    counter = tmp_path / "session-begin-failures-c-session-1"
    counter.write_text("0\n" if interrupt else "19\n")
    done = tmp_path / "done"
    usable = tmp_path / "usable"
    command = (
        "trap ':' INT\n"
        + shlex.join([shell, "-c", wrapper, "wrapper", shell, str(child)])
        + f"; printf stopped > {shlex.quote(str(done))}; exec {shlex.quote(shell)} -f"
    )
    with tempfile.TemporaryDirectory(prefix="ai-cli-begin-") as directory:
        sock = str(Path(directory) / "tmux.sock")

        def tmux(*args):
            result = subprocess.run(
                ["tmux", "-S", sock, *args], env=env, capture_output=True, text=True, check=False, timeout=5
            )
            assert result.returncode == 0, result.stderr
            return result

        try:
            tmux("new-session", "-d", "-s", "session-1", shell, "-c", command)
            deadline = time.monotonic() + 5
            if interrupt:
                while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert (tmp_path / "ready").exists()
                tmux("send-keys", "-t", "=session-1:", "C-c")
            while not done.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert done.exists(), tmux("capture-pane", "-p", "-t", "=session-1:").stdout
            assert (tmp_path / "calls").read_text() == "call"
            tmux("send-keys", "-t", "=session-1:", f"printf usable > {shlex.quote(str(usable))}", "Enter")
            deadline = time.monotonic() + 5
            while not usable.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert usable.read_text() == "usable"
        finally:
            # This test creates no detached descendants; its private server owns all children.
            subprocess.run(["tmux", "-S", sock, "kill-server"], env=env, capture_output=True, check=False, timeout=5)


@pytest.mark.parametrize("engine", ["c", "g", "p", "cx"])
def test_given_generated_child_when_legacy_registry_is_missing_then_real_cli_recovers_before_agent_start(
    tmp_path, engine
):
    shell = shutil.which("bash")
    if shell is None or os.name != "posix":
        pytest.skip("POSIX shell required")
    name = f"{engine}-r-session-1"
    generated = get_engine_script(
        engine,
        "session-1",
        name,
        f"{engine}-r-session-",
        "myproject",
        worktree_dir=str(tmp_path),
        is_remote=True,
    )
    fragment = generated.split("# BEGIN RUN STATE legacy-child-generation\n", 1)[1].split("# END RUN STATE", 1)[0]
    binary = tmp_path / "ai"
    binary.write_text(
        f"#!{sys.executable}\n"
        "from ai_cli import session_registry, main\n"
        "session_registry.current_boot_id = lambda: 'test-boot'\n"
        "main.cli()\n"
    )
    binary.chmod(0o700)
    root = config.get_xdg_state_home()
    root.mkdir(parents=True, exist_ok=True)
    body = f"_ai_state_dir={shlex.quote(str(root))}\ntmux_session={name}\n" + fragment + "\nprintf agent-started\n"
    environment = {key: value for key, value in os.environ.items() if key != "AI_CLI_RUN_GENERATION"}
    environment["PATH"] = str(tmp_path) + os.pathsep + environment["PATH"]
    result = subprocess.run(
        [shell, "-c", body], env=environment, capture_output=True, text=True, timeout=5, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "agent-started"
    record = registry.load_registry()["sessions"][0]
    assert record["name"] == name and state.valid_boot_recipe(record)
    assert record["generation"]
