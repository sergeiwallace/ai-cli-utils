"""Real tmux upgrades use a frozen old supervisor and historical acquisition body."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from ai_cli import config, main
from ai_cli import session_registry as registry

pytestmark = [pytest.mark.real_tmux, pytest.mark.skipif(os.name != "posix", reason="POSIX supervisor")]


def wait_for(check, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture(params=["bash", "zsh"])
def old_session(tmp_path, monkeypatch, request):
    shell = shutil.which(request.param)
    if shell is None:
        pytest.skip(f"{request.param} unavailable")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    (home / ".zshrc").write_text("")
    state = tmp_path / "state/ai-cli-utils"
    scripts = state / "sessions"
    scripts.mkdir(parents=True)
    script = scripts / "c-myproject-box-1.sh"
    fixtures = Path(__file__).parent / "fixtures"
    legacy = (fixtures / "session_restart_legacy.txt").read_text().replace("__PROJECT_DIR__", str(tmp_path))
    acquisition = (fixtures / "session_acquisition_legacy.txt").read_text()
    child = f"""
if [[ "${{1:-}}" == --ai-cli-child-body ]]; then
    tmux_session=c-myproject-box-1
    _ai_state_dir={shlex.quote(str(state))}
    # Model the pane keyboard mode surviving an abrupt agent exit.
    printf '\\033[>4;2m'
    {acquisition}
    printf started > {shlex.quote(str(tmp_path / "started"))}
    sleep 60
    exit 77
fi
"""
    script.write_text(child + legacy)
    script.chmod(0o700)
    meta_line = next(line for line in legacy.splitlines() if "printf '%s'" in line and "session-meta-" in line)
    meta = json.loads(shlex.split(meta_line)[2])
    (state / "session-meta-c-myproject-box-1.json").write_text(json.dumps(meta))
    for name in ("bash", "zsh", "base64", "cat", "date", "id", "mkdir", "mktemp", "ps", "rm", "sleep", "stat", "tr"):
        found = shutil.which(name)
        if found:
            (binaries / name).symlink_to(found)
    (binaries / "python3").symlink_to(sys.executable)
    tmux = shutil.which("tmux")
    assert tmux
    (binaries / "tmux").symlink_to(tmux)
    ai = binaries / "ai"
    ai.write_text(
        f"#!{sys.executable}\n"
        "import os, sys, time\nfrom pathlib import Path\n"
        f"root = Path({str(tmp_path)!r})\n"
        "if sys.argv[1:3] == ['sessions', '_begin']:\n"
        "    (root / 'attempted').touch()\n"
        "    if not (root / 'upgraded').exists(): sys.exit(1)\n"
        "    from ai_cli import session_run_state as state\n"
        "    if (root / 'block').exists():\n"
        "        def begin(*a, **kw):\n"
        "            (root / 'ready').touch()\n"
        "            time.sleep(60)\n"
        "        state.begin = begin\n"
        "    from ai_cli.main import cli\n"
        "    cli()\n"
        "elif sys.argv[1:3] == ['internal', 'get-version']: print('unknown')\n"
        "elif sys.argv[1:3] == ['internal', 'acquire-generation-lease']: sys.exit(1)\n"
    )
    ai.chmod(0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ZDOTDIR", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("AI_HOST", "host-a")
    monkeypatch.setenv("PATH", str(binaries))
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src"))
    monkeypatch.delenv("AI_CLI_RUN_GENERATION", raising=False)
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setattr(config, "get_xdg_state_home", lambda: state)
    monkeypatch.setattr(registry, "registry_path", lambda: state / "iterm2-sessions.json")
    monkeypatch.setattr(main._session_script, "resolve_session_shell", lambda: shell)
    sockdir = Path(tempfile.mkdtemp(prefix="session-upgrade-", dir="/tmp"))
    sock = str(sockdir / "tmux.sock")

    def run(*args):
        return subprocess.run([tmux, "-S", sock, *args], capture_output=True, text=True, check=False)

    try:
        assert (
            run("new-session", "-d", "-c", str(tmp_path), "-s", "c-myproject-box-1", shell, str(script)).returncode == 0
        )
        assert run("set-option", "extended-keys", "on").returncode == 0
        # Nested CLI calls must discover this test's server, never the user's.
        monkeypatch.setenv("TMUX", f"{sock},0,0")
        assert wait_for(lambda: (tmp_path / "attempted").exists()), run("capture-pane", "-p").stdout
        yield tmp_path, script, run
    finally:
        # This server has one disposable pane, no heartbeat ticker and no agent.
        # Explicit socket ownership avoids host-wide process enumeration.
        names = run("list-sessions", "-F", "#{session_name}")
        if names.returncode == 0:
            assert names.stdout.strip() == "c-myproject-box-1"
            assert run("kill-server").returncode == 0


def test_given_old_retrying_supervisor_when_package_changes_then_script_and_registry_converge_without_restart(
    old_session,
):
    root, script, tmux = old_session
    leader = tmux("display-message", "-p", "#{pane_pid}").stdout.strip()
    (root / "upgraded").touch()
    assert wait_for(lambda: (root / "started").exists()), tmux("capture-pane", "-p").stdout
    assert "--launch-metadata" in script.read_text()
    assert tmux("display-message", "-p", "#{pane_pid}").stdout.strip() == leader
    document = json.loads(registry.registry_path().read_text())
    assert len(document["sessions"]) == 1
    assert document["sessions"][0]["relaunch"] == "never"


def test_given_old_acquisition_and_extended_keys_when_terminal_interrupts_then_supervisor_stops_without_respawn(
    old_session,
):
    root, _script, tmux = old_session
    (root / "block").touch()
    (root / "upgraded").touch()
    assert wait_for(lambda: (root / "ready").exists()), tmux("capture-pane", "-p").stdout
    assert tmux("send-keys", "C-c").returncode == 0
    assert wait_for(lambda: tmux("has-session", "-t", "=c-myproject-box-1").returncode != 0, timeout=8), (
        tmux("capture-pane", "-p", "-S", "-").stdout,
        (root / "ready").read_text(),
    )
    assert not (root / "started").exists()
