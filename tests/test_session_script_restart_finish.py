"""A supervisor restart of NEW finishes a migration this host recorded for NEW, between agents.

The supervisor bug: the generated wrapper restarts its agent without going back
through ``ai c``, so a migration whose launch-time finish was refused was never
finished by a restart. Each test migrates the fixture slot for real (which writes
the ledger row), drops its ``finished`` row, seeds OLD-keyed launch logs that only a
finish carries, then runs the wrapper generated for NEW. Its first agent exits
after a healthy run, the supervisor starts a replacement, and each agent records
what it found when it started. ``tmux``, ``claude`` and most of ``ai`` are fixture
programs; the run-state commands and the restart finish run the real ``ai_cli``.
No tmux server or agent outside this test is used.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from contextlib import ExitStack
from pathlib import Path

import pytest
import test_session_migrate
import test_session_migrate_on_launch
from process_ownership import bounded_supervisor, owned_sleeper, watch_session
from test_restart_transcript_resolution import LATEST_ID, _record_exit
from test_session_migrate import NEW, OLD

from ai_cli import main, session_run_state

slot = test_session_migrate.slot
migrated = test_session_migrate_on_launch.migrated

SESSION = f"c-{NEW}"

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX supervisor fixture"),
    # Allows the fixture executable named tmux; no tmux server is used.
    pytest.mark.real_tmux,
]


def _records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n")[:-1] if line]


def _wait_for(check, process, log: Path, timeout: float = 90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        assert process.poll() is None, "the wrapper exited:\n" + log.read_text(encoding="utf-8")
        time.sleep(0.05)
    pytest.fail("the replacement agent did not start before the deadline:\n" + log.read_text(encoding="utf-8"))


def _preamble() -> str:
    return f"#!{sys.executable}\nimport json, os, sys, time\nfrom pathlib import Path\nroot = Path(os.environ['RESTART_FIXTURE_ROOT'])\n"


@pytest.fixture
def wrapper(migrated, tmp_path, monkeypatch, request):
    """Start the generated wrapper of NEW; yield ``(root, process, log)`` and reap it afterwards."""
    root = tmp_path / "wrapper"
    binaries = root / "bin"
    binaries.mkdir(parents=True)
    for name in (
        "bash",
        "base64",
        "cat",
        "date",
        "git",
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
        assert executable is not None, f"wrapper fixture requires {name}"
        (binaries / name).symlink_to(executable)
    (binaries / "python3").symlink_to(sys.executable)
    (binaries / "tmux").write_text(
        _preamble()
        + f"""args = sys.argv[1:]
started = root / 'session-started'
if args[:1] == ['list-sessions']:
    print({SESSION!r})
    extra = root / 'other-tmux-sessions'
    if extra.exists(): print(extra.read_text(encoding='utf-8'), end='')
elif args[:1] == ['list-clients']:
    print('client-1')
elif args[:1] == ['display-message'] and '#{{session_name}}' in args:
    print({SESSION!r})
elif args[:1] == ['set-environment'] and 'AI_SESSION_STARTED' in args:
    started.touch()
elif args[:1] == ['show-environment'] and started.exists():
    print('AI_SESSION_STARTED=1')
""",
        encoding="utf-8",
    )
    (binaries / "ai").write_text(
        _preamble()
        + """args = sys.argv[1:]
if args[:2] == ['internal', 'get-version']: print('unknown')
elif args[:2] == ['internal', 'acquire-generation-lease']: sys.exit(1)
elif args[:2] == ['internal', 'resolve-background-target']: sys.exit(0)
elif args[:2] == ['internal', 'finish-migrations'] or (
    args[:2] == ['internal', 'resolve-continue-target'] and (root / 'resolve-transcript').is_file()
) or (
    args[:1] == ['sessions'] and args[1:2] and args[1] in ('_begin', '_sequence', 'mark')
):
    os.execv(sys.executable, [sys.executable, '-m', 'ai_cli.main', *args])
""",
        encoding="utf-8",
    )
    (binaries / "claude").write_text(
        _preamble()
        + f"""state = Path(os.environ['XDG_STATE_HOME']) / 'ai-cli-utils'
ledger = state / 'session-migrations.jsonl'
rows = [json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines() if line]
launches = root / 'launches'
seen = sum(1 for _ in launches.open(encoding='utf-8')) if launches.exists() else 0
record = {{
    'pid': os.getpid(),
    'argv': sys.argv[1:],
    'old_logs': sorted(p.name for p in (state / 'launch-logs').glob({OLD!r} + '-*')),
    'finished': [[r['old'], r['new']] for r in rows if r.get('finished')],
}}
with launches.open('a', encoding='utf-8') as out: out.write(json.dumps(record) + '\\n')
if seen == 0:
    # A healthy run that ends in an error: the supervisor restarts the agent.
    time.sleep(4)
    sys.exit(1)
while True:
    time.sleep(1)
""",
        encoding="utf-8",
    )
    for program in binaries.iterdir():
        if not program.is_symlink():
            program.chmod(0o700)
    config_dir = Path(os.environ["XDG_CONFIG_HOME"]) / "ai-cli-utils"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(
        f'[worktree]\nmarker_prefix = "hub"\n\n[project]\nprojects_dir = "{migrated["repo"].parent.as_posix()}"\n',
        encoding="utf-8",
    )
    new_wt = migrated["repo"] / ".worktrees" / NEW
    restart_case = getattr(request, "param", None)
    if restart_case:
        (root / "resolve-transcript").touch()
        project = main._cc_project_dir(new_wt)
        latest = project / f"{LATEST_ID}.jsonl"
        latest.write_text(json.dumps({"customTitle": NEW, "type": "custom-title"}) + "\n", encoding="utf-8")
        os.utime(latest, (2_000_000_000, 2_000_000_000))
        sessions = Path.home() / ".claude" / "sessions"
        sessions.mkdir(exist_ok=True)
        (sessions / "9001.json").write_text(json.dumps({"sessionId": LATEST_ID, "kind": "bg"}), encoding="utf-8")
        if restart_case == "recorded":
            _record_exit(new_wt, latest, stamped=latest, title=NEW)
    session_run_state.register(
        SESSION,
        recipe={
            "engine": "c",
            "session_name": SESSION,
            "owning_host": "host-a",
            "argv": ["ai", "c", NEW],
            "extra_args": [],
            "options": {},
            "cwd": str(new_wt),
            "project": "myrepo",
        },
        host="host-a",
    )
    monkeypatch.setattr(main._session_script, "resolve_session_shell", lambda: shutil.which("bash"))
    source = main._engine_script_from_meta(
        {
            "engine": "c",
            "ai_name": NEW,
            "session": SESSION,
            "prefix": "c-myrepo-box-",
            "project_prefix": "myrepo-box",
            "worktree_dir": str(new_wt),
            "launch_log_path": str(root / "launch.log"),
        }
    )
    script = migrated["state"] / "sessions" / f"{SESSION}.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(source, encoding="utf-8")
    env = {
        **os.environ,
        "PATH": str(binaries),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "RESTART_FIXTURE_ROOT": str(root),
        "TMUX": "fixture-tmux-socket,1,0",
        "TMUX_PANE": "%1",
        "AI_HOST": "host-a",
    }
    env.pop("BASH_ENV", None)
    log = root / "wrapper.log"
    with (
        log.open("w", encoding="utf-8") as diagnostics,
        # Leave a minute for teardown after the suite's 300-second test timeout.
        bounded_supervisor(script, diagnostics, default_lifetime=360, env=env) as process,
    ):
        yield root, process, log


def _replacement(wrapper) -> tuple[list[dict], str]:
    root, process, log = wrapper
    launches = _wait_for(lambda: (found := _records(root / "launches")) and len(found) >= 2 and found, process, log)
    return launches, log.read_text(encoding="utf-8") + (root / "launch.log").read_text(encoding="utf-8")


def test_given_a_pending_migration_when_the_supervisor_restarts_new_then_the_finish_runs_between_agents(
    migrated, wrapper
):
    launches, output = _replacement(wrapper)

    first, replacement = launches[0], launches[1]
    assert first["old_logs"], "the first agent started with the migration unfinished"
    assert first["finished"] == []
    assert replacement["old_logs"] == [], f"the restart did not finish the migration:\n{output}"
    assert replacement["finished"] == [[OLD, NEW]]
    test_session_migrate._assert_logs_and_evidence_follow_new(migrated, migrated["seeded"])


@pytest.mark.parametrize("live", ["agent-in-slot", "other-tmux-session"])
def test_given_new_is_live_elsewhere_when_the_supervisor_restarts_then_the_finish_does_what_is_safe_and_the_agent_restarts(
    migrated, wrapper, live
):
    """None of the seeded leftovers is a file of the live NEW, so the finish completes beside it."""
    root, _process, _log = wrapper
    new_wt = migrated["repo"] / ".worktrees" / NEW
    with ExitStack() as ownership:
        if live == "agent-in-slot":
            sleeper = ownership.enter_context(owned_sleeper(120))
            ownership.enter_context(watch_session(sleeper))
            sessions = Path.home() / ".claude" / "sessions"
            sessions.mkdir(parents=True, exist_ok=True)
            (sessions / f"{sleeper.pid}.json").write_text(
                json.dumps({"pid": sleeper.pid, "name": "helper", "cwd": str(new_wt / "sub")}), encoding="utf-8"
            )
        else:
            (root / "other-tmux-sessions").write_text(f"c-r-{NEW}\n", encoding="utf-8")
        launches, output = _replacement(wrapper)

    first, replacement = launches[0], launches[1]
    assert first["old_logs"], "the first agent started with the migration unfinished"
    assert replacement["old_logs"] == [], f"the restart did not carry the launch logs:\n{output}"
    assert replacement["finished"] == [[OLD, NEW]]
    assert f"migration {OLD} -> {NEW}" in output, output


@pytest.mark.parametrize("wrapper", ["recorded"], indirect=True)
def test_given_a_recorded_bg_transcript_when_the_supervisor_restarts_then_passes_its_uuid(wrapper):
    launches, _ = _replacement(wrapper)

    assert LATEST_ID not in launches[0]["argv"]
    assert launches[1]["argv"][-4:] == ["--resume", LATEST_ID, "--name", NEW]


@pytest.mark.parametrize("wrapper", ["unrecorded"], indirect=True)
def test_given_a_transcript_conflict_when_the_supervisor_restarts_then_stops_before_launching(wrapper):
    root, process, log = wrapper
    assert process.wait(timeout=90) == 0

    assert len(_records(root / "launches")) == 1
    output = log.read_text(encoding="utf-8") + (root / "launch.log").read_text(encoding="utf-8")
    assert LATEST_ID in output and "disagrees with" in output
