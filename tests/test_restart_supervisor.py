"""The frozen pre-feature supervisor must load the pinned child on its first restart.

The shell fixture was rendered from the pre-feature program at b0e1d9d6. All
executables it calls for session services are fixture programs. No tmux server,
agent client, registry or messaging service outside this test is used.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import psutil
import pytest
from conftest import run_cli

from ai_cli import config, main, session_restart
from ai_cli.process_probe import probe_for

SESSION_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ID = "22222222-2222-4222-8222-222222222222"
SESSION = "c-myproject-box-1"
TITLE = "myproject-box-1"


def _wait_for(check, process, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        assert process.poll() is None, "fixture supervisor exited before its agent started"
        time.sleep(0.02)
    pytest.fail("fixture agent did not start before the deadline")


@pytest.fixture
def supervisor_fixture(tmp_path_factory, monkeypatch):
    root = tmp_path_factory.mktemp("restart")
    home = root / "home"
    cwd = root / "myproject"
    binaries = root / "bin"
    for directory in (home / ".claude", cwd / ".claude", binaries):
        directory.mkdir(parents=True)
    # Expose only checked OS utilities and the fixture service programs below.
    # Optional host tools such as direnv must not change the child environment.
    for name in (
        "bash",
        "base64",
        "cat",
        "date",
        "id",
        "mkdir",
        "mktemp",
        "ps",
        "rm",
        "sed",
        "sleep",
        "stat",
        "tr",
        "wc",
    ):
        executable = shutil.which(name)
        assert executable is not None, f"supervisor fixture requires {name}"
        (binaries / name).symlink_to(executable)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(root / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root / "config"))
    monkeypatch.setenv("PATH", str(binaries))
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src"))
    monkeypatch.setenv("RESTART_FIXTURE_ROOT", str(root))
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.delenv("BASH_ENV", raising=False)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(session_restart.socket, "socket", lambda *args: MagicMock())
    guarded_run = subprocess.run

    def fixture_run(argv, **kwargs):
        if argv[0] == "claude":
            assert argv == ["claude", "agents", "--json"]
            return guarded_run([sys.executable, str(binaries / "claude"), *argv[1:]], **kwargs)
        return guarded_run(argv, **kwargs)

    monkeypatch.setattr(main._session_script, "resolve_session_shell", lambda: shutil.which("bash"))
    (home / ".claude" / "settings.json").write_text('{"remoteControlAtStartup":true}', encoding="utf-8")
    transcript_dir = home / ".claude" / "projects" / "myproject"
    transcript_dir.mkdir(parents=True)
    transcript = transcript_dir / f"{SESSION_ID}.jsonl"
    transcript.write_text(json.dumps({"type": "custom-title", "customTitle": TITLE}) + "\n", encoding="utf-8")
    (root / "target").write_text(str(transcript), encoding="utf-8")
    preamble = (
        f"#!{sys.executable}\n"
        + """import json, os, sys, time
from pathlib import Path
root = Path(os.environ['RESTART_FIXTURE_ROOT'])
"""
    )
    (binaries / "tmux").write_text(
        preamble
        + """args = sys.argv[1:]
with (root / 'tmux-calls').open('a', encoding='utf-8') as out: out.write(json.dumps(args) + '\\n')
if args[0] == 'set-option' and '@ai_cli_session_generation' in args:
    (root / 'generation').write_text(args[-1], encoding='utf-8')
elif args[0] == 'display-message':
    if args[-1] == '#{session_id}': print('$1')
    elif 'pane_pid' in args[-1]:
        fields = ['c-myproject-box-1', '%1', (root / 'supervisor-pid').read_text(encoding="utf-8"), (root / 'generation').read_text(encoding="utf-8")]
        if 'pane_start_command' in args[-1]:
            fields.append('bash ' + str(root / 'state/ai-cli-utils/sessions/c-myproject-box-1.sh'))
        print('\\t'.join(fields))
elif args[0] == 'show-environment':
    print('AI_SESSION_STARTED=1')
""",
        encoding="utf-8",
    )
    (binaries / "ai").write_text(
        preamble
        + """args = sys.argv[1:]
if args[:2] == ['internal', 'get-version']: print('unknown')
elif args[:2] == ['internal', 'acquire-generation-lease']: sys.exit(1)
elif args[:2] == ['internal', 'resolve-continue-target']:
    with (root / 'resolver-calls').open('a', encoding='utf-8') as out: out.write('query\\n')
    print((root / 'target').read_text(encoding="utf-8"))
elif args[:2] == ['internal', 'restart-pin']:
    if args[2:] == ['--probe']:
        print('restart-pin-v1')
        sys.exit(0)
    from ai_cli.session_restart import child_pin
    # Model cold helper startup beyond the former two-second verification budget.
    time.sleep(3)
    status, transcript = child_pin(args[2], args[3])
    if transcript: print(transcript)
    sys.exit(status)
""",
        encoding="utf-8",
    )
    (binaries / "claude").write_text(
        preamble
        + """import signal
from ai_cli.process_probe import probe_for
sessions = Path.home() / '.claude/sessions'
sessions.mkdir(parents=True, exist_ok=True)
if sys.argv[1:] == ['agents', '--json']:
    print(json.dumps([json.loads(p.read_text(encoding="utf-8")) for p in sessions.glob('*.json')]))
    sys.exit(0)
pid = os.getpid()
with (root / 'launches').open('a', encoding='utf-8') as out:
    out.write(json.dumps({'pid': pid, 'ppid': os.getppid(), 'argv': sys.argv[1:]}) + '\\n')
if (root / 'fail-resume').exists(): sys.exit(1)
sid = sys.argv[sys.argv.index('--resume') + 1]
sockpath = str(root / f'{pid}.sock')
record = {'pid': pid, 'procStart': probe_for().capture_identity(pid).marker, 'sessionId': sid,
          'name': 'myproject-box-1', 'nameSource': 'user', 'status': 'idle',
          'statusUpdatedAt': (time.time() - 1000) * 1000, 'cwd': str(root / 'myproject'),
          'tmux': 'c-myproject-box-1:@1.%1', 'bridgeSessionId': 'bridge-1',
          'messagingSocketPath': sockpath, 'version': '2.1.1'}
registry = sessions / f'{pid}.json'
registry.write_text(json.dumps(record), encoding='utf-8')
transcript = Path.home() / '.claude/projects/myproject' / f'{sid}.jsonl'
with transcript.open('a', encoding='utf-8') as out:
    out.write(json.dumps({'type': 'assistant', 'message': {'model': 'claude-sonnet-4-6', 'usage': {
        'input_tokens': 1000, 'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0}}}) + '\\n')
    out.write(json.dumps({'type': 'system', 'subtype': 'turn_duration', 'pendingBackgroundAgentCount': 0}) + '\\n')
running = True
def stop(*args):
    global running
    running = False
signal.signal(signal.SIGTERM, stop)
try:
    while running:
        time.sleep(0.1)
finally:
    registry.unlink(missing_ok=True)
    Path(sockpath).unlink(missing_ok=True)
""",
        encoding="utf-8",
    )
    (binaries / "observer").write_text(
        preamble
        + """sessions = Path.home() / '.claude/sessions'
rows = [json.loads(p.read_text(encoding="utf-8")) for p in sessions.glob('*.json')]
print(json.dumps({'status': 'seen', 'complete': True, 'observed_at': time.time(), 'rows': [
    {'session_id': r['sessionId'], 'title': r['name'], 'bridge_session_id': r['bridgeSessionId']} for r in rows]}))
""",
        encoding="utf-8",
    )
    (binaries / "python3").symlink_to(sys.executable)
    for program in binaries.iterdir():
        if not program.is_symlink():
            program.chmod(0o700)
    commands = config.get_xdg_config_home() / "restart-session.d"
    commands.mkdir(parents=True)
    (commands / "observer.toml").write_text('[observer]\nargv = ["observer"]\n', encoding="utf-8")
    state = config.get_xdg_state_home()
    script = state / "sessions" / f"{SESSION}.sh"
    legacy = (
        (Path(__file__).parent / "fixtures" / "session_restart_legacy.txt")
        .read_text(encoding="utf-8")
        .replace("__PROJECT_DIR__", str(cwd))
    )
    main._write_launch_script_if_changed(script, legacy)
    # Freeze the old child's shell selection independently of the current preference.
    # The fixture uses bash, so the refreshed program must use the same shell here.
    monkeypatch.setattr(main._session_script, "resolve_session_shell", lambda: "bash")
    diagnostics = (root / "supervisor.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        ["bash", str(script)],
        stdin=subprocess.DEVNULL,
        stdout=diagnostics,
        stderr=diagnostics,
        start_new_session=True,
    )
    (root / "supervisor-pid").write_text(str(process.pid), encoding="utf-8")
    try:
        # This shared-module patch must unwind before the conftest spawn guard.
        with patch.object(session_restart.subprocess, "run", fixture_run):
            yield root, script, process
    finally:
        # Only test-owned PIDs are eligible. Capture births before signalling;
        # never use a group or a pattern, even for fixture cleanup.
        try:
            children = psutil.Process(process.pid).children(recursive=True)
        except psutil.Error:
            children = []
        targets = [(child.pid, probe_for().capture_identity(child.pid)) for child in children]
        targets.append((process.pid, probe_for().capture_identity(process.pid)))
        for pid, identity in reversed(targets):
            if identity is not None and probe_for().capture_identity(pid) == identity:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)
        for pid, identity in targets:
            if (
                identity is not None
                and probe_for().capture_identity(pid) == identity
                and not probe_for().has_ended(pid)
            ):
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
        process.wait(timeout=5)
        diagnostics.close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX supervisor fixture")
@pytest.mark.real_tmux  # Allow the fixture executable named tmux; no tmux server is used.
@pytest.mark.parametrize("prompt", [False, True])
@pytest.mark.parametrize("failed_resume", [False, True])
def test_given_old_supervisor_when_restart_then_pinned_conversation(
    supervisor_fixture, monkeypatch, prompt, failed_resume
):
    root, script, supervisor = supervisor_fixture
    launches_path = root / "launches"

    def launches():
        if not launches_path.exists():
            return []
        return [json.loads(line) for line in launches_path.read_text(encoding="utf-8").splitlines()]

    initial = _wait_for(launches, supervisor)[0]
    old_child = initial["ppid"]
    old_script = script.stat().st_ino
    # The real pre-feature child has the existing three-second quick-exit gate.
    # Process birth can precede the launch timer (for example, during exec
    # startup). Measure a full window after observing the running agent instead.
    observed = time.monotonic()
    _wait_for(lambda: time.monotonic() - observed >= 4, supervisor)
    original_kill = os.kill
    sent = []

    def signal_owned(pid, sig):
        assert pid == initial["pid"] and sig == signal.SIGTERM
        sent.append((pid, sig))
        # Introduce the same-title competitor after the act path's last check.
        competitor = root / "home/.claude/projects/myproject" / f"{OTHER_ID}.jsonl"
        competitor.write_text(json.dumps({"type": "custom-title", "customTitle": TITLE}) + "\n", encoding="utf-8")
        (root / "target").write_text(str(competitor), encoding="utf-8")
        if failed_resume:
            (root / "fail-resume").touch()
        original_kill(pid, sig)

    settings = {
        "restart_session": {
            "min_uptime_seconds": 4,
            "settle_seconds": 0,
            "term_grace_seconds": 5,
            "relaunch_timeout_seconds": 60,
            "cooldown_seconds": 0,
        }
    }
    if prompt:
        (config.get_xdg_state_home() / f"cc-resume-prompt-{SESSION}").write_text(
            "Continue the saved work.", encoding="utf-8"
        )
    with monkeypatch.context() as patcher:
        patcher.setattr(session_restart.os, "kill", signal_owned)
        code, out, err = run_cli(["ai", "-X", SESSION_ID, "-j"], config=settings)
    report = json.loads(out)
    assert (code, report["reason"]) == ((1, "verification-failed") if failed_resume else (0, "restarted")), (
        report,
        err,
        (root / "supervisor.log").read_text(encoding="utf-8"),
    )
    replacement = _wait_for(lambda: launches() if len(launches()) == 2 else None, supervisor)[1]
    assert replacement["argv"][replacement["argv"].index("--resume") + 1] == SESSION_ID
    assert replacement["argv"][replacement["argv"].index("--name") + 1] == TITLE
    assert ("Continue the saved work." in replacement["argv"]) is prompt
    assert supervisor.poll() is None
    if not failed_resume:
        assert psutil.Process(replacement["ppid"]).ppid() == supervisor.pid
    else:
        request = session_restart._object(session_restart.state_paths(SESSION)[2])
        assert request["status"] == "failed"
        assert session_restart.child_pin(SESSION, TITLE)[0] == 1
    assert replacement["ppid"] != old_child
    assert script.stat().st_ino != old_script
    assert sent == [(initial["pid"], signal.SIGTERM)]
    assert (root / "resolver-calls").read_text(encoding="utf-8").splitlines() == ["query"]
    calls = [json.loads(line) for line in (root / "tmux-calls").read_text(encoding="utf-8").splitlines()]
    assert not any("respawn-pane" in call for call in calls)
