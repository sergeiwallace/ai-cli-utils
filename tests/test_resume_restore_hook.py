"""A configured restore command runs before every Claude Code resume.

A deployment may archive an idle transcript as ``<session-id>.jsonl.zst`` beside
where it was and remove the original; ``claude --resume`` cannot read the archive.
``[resume_restore] command`` names a program the launcher runs, with the session
id substituted for ``{session_id}``, before it resumes that session. Exit 0 lets
the resume go ahead; anything else stops it and prints the command's output and
exit status. With no command configured, nothing changes.

The restore program here is a fixture: it "decompresses" an archive by copying
its bytes back to ``<session-id>.jsonl`` and records each call in a log.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import run_cli
from process_ownership import bounded_supervisor
from test_bare_worktree import _launch_kwargs, _write_transcript, real_repo  # noqa: F401

from ai_cli import main, session_run_state
from ai_cli.main import _bare_engine_command, _cc_project_dir, _do_session_launch

SESSION_ID = "11111111-1111-4111-8111-111111111111"
OLDER_ID = "22222222-2222-4222-8222-222222222222"
TITLE = "myproject-box-1"
SESSION = f"c-{TITLE}"

_RESTORE_TOOL = """\
import json, sys, time
from pathlib import Path

project, session_id, log, mode = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), sys.argv[4]
with log.open("a", encoding="utf-8") as out:
    out.write(json.dumps({"session_id": session_id, "at": time.time()}) + "\\n")
if mode == "fail":
    print(f"restore failed: archive for {session_id} does not verify; it is kept")
    sys.exit(3)
if mode == "hang":
    time.sleep(60)
archives = [project / f"{session_id}.jsonl.zst", *sorted((project / session_id).glob("**/*.jsonl.zst"))]
for archive in archives:
    if archive.is_file():
        archive.with_name(archive.name[: -len(".zst")]).write_bytes(archive.read_bytes())
        archive.unlink()
"""


def _archive(path: Path) -> Path:
    """Replace ``path`` by its archive, the fixture's stand-in for a compressed copy."""
    archive = path.with_name(path.name + ".zst")
    archive.write_bytes(path.read_bytes())
    stat = path.stat()
    os.utime(archive, (stat.st_atime, stat.st_mtime))
    path.unlink()
    return archive


def _calls(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def _hook(tmp_path: Path, project_dir: Path, mode: str = "ok", timeout: float | None = None) -> dict:
    """A ``[resume_restore]`` table running the fixture tool, and the log it writes."""
    tool = tmp_path / "restore_tool.py"
    tool.write_text(_RESTORE_TOOL, encoding="utf-8")
    log = tmp_path / "restore-calls.jsonl"
    table: dict = {"command": [sys.executable, str(tool), str(project_dir), "{session_id}", str(log), mode]}
    if timeout is not None:
        table["timeout_seconds"] = timeout
    return {"resume_restore": table, "log": log}


def _write_config(table: dict) -> None:
    lines = ["[resume_restore]"]
    lines.append("command = [" + ", ".join(json.dumps(word) for word in table["command"]) + "]")
    if "timeout_seconds" in table:
        lines.append(f"timeout_seconds = {table['timeout_seconds']}")
    config_dir = Path(os.environ["XDG_CONFIG_HOME"]) / "ai-cli-utils"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- bare launch (no tmux): the launcher builds the claude argv itself -----------


def _bare(target: Path, config: dict) -> list[str]:
    with patch("ai_cli.config.load_config", return_value=config):
        return _bare_engine_command("c", TITLE, target, None, "gemini", "--no-sandbox", [])


def test_given_an_archived_transcript_and_a_restore_command_when_bare_claude_resumes_then_it_is_restored_first(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    target = tmp_path / "wt"
    project = _cc_project_dir(target)
    transcript = _write_transcript(project, SESSION_ID, TITLE)
    original = transcript.read_bytes()
    _archive(transcript)
    hook = _hook(tmp_path, project)

    argv = _bare(target, {"resume_restore": hook["resume_restore"]})

    assert argv[argv.index("--resume") + 1] == SESSION_ID
    assert transcript.read_bytes() == original
    assert [call["session_id"] for call in _calls(hook["log"])][:1] == [SESSION_ID]


def test_given_an_older_archived_transcript_with_the_same_title_when_restored_then_the_newer_one_is_still_resumed(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    target = tmp_path / "wt"
    project = _cc_project_dir(target)
    older = _write_transcript(project, OLDER_ID, TITLE)
    os.utime(older, (1_000_000, 1_000_000))
    _archive(older)
    newer = _write_transcript(project, SESSION_ID, TITLE)
    os.utime(newer, (2_000_000, 2_000_000))
    hook = _hook(tmp_path, project)

    argv = _bare(target, {"resume_restore": hook["resume_restore"]})

    assert older.is_file()
    assert argv[argv.index("--resume") + 1] == SESSION_ID


def test_given_no_restore_command_when_bare_claude_launches_then_an_archived_transcript_is_left_alone(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    target = tmp_path / "wt"
    archive = _archive(_write_transcript(_cc_project_dir(target), SESSION_ID, TITLE))

    argv = _bare(target, {})

    assert "--resume" not in argv
    assert archive.is_file()


def test_given_no_restore_command_when_bare_claude_resumes_a_plain_transcript_then_runs_nothing_extra(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    target = tmp_path / "wt"
    _write_transcript(_cc_project_dir(target), SESSION_ID, TITLE)

    with patch("ai_cli.transcript_restore.subprocess.run") as run:
        argv = _bare(target, {})

    assert argv[argv.index("--resume") + 1] == SESSION_ID
    run.assert_not_called()


@pytest.mark.parametrize(
    ("mode", "timeout", "expected"),
    [
        ("fail", None, ["exit 3", "restore failed: archive for", "does not verify; it is kept"]),
        ("hang", 1, ["timed out after 1s"]),
    ],
)
def test_given_a_failing_restore_command_when_a_bare_launch_resumes_then_it_stops_with_its_output_and_status(
    real_repo,  # noqa: F811
    tmp_path,
    monkeypatch,
    capsys,
    mode,
    timeout,
    expected,
):
    monkeypatch.chdir(real_repo)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    project = _cc_project_dir(real_repo)
    _archive(_write_transcript(project, SESSION_ID, "kg-tst-1"))
    hook = _hook(tmp_path, project, mode=mode, timeout=timeout)
    execs: list = []

    with (
        patch("ai_cli.config.load_config", return_value={"resume_restore": hook["resume_restore"]}),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.main.shutil.which", return_value=None),
        patch("ai_cli.main.os.execvp", side_effect=lambda *args: execs.append(args)),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.trust.ensure_workspace_trusted"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            _do_session_launch(**_launch_kwargs(no_worktree=True))

    assert exc_info.value.code == 1
    assert execs == []
    stderr = capsys.readouterr().err
    assert SESSION_ID in stderr
    for text in expected:
        assert text in stderr
    assert (project / f"{SESSION_ID}.jsonl.zst").is_file()


def test_given_a_restore_command_without_the_placeholder_when_resuming_then_it_refuses_naming_the_setting(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    target = tmp_path / "wt"
    _archive(_write_transcript(_cc_project_dir(target), SESSION_ID, TITLE))

    from ai_cli.transcript_restore import TranscriptRestoreError

    with pytest.raises(TranscriptRestoreError, match=r"resume_restore\.command"):
        _bare(target, {"resume_restore": {"command": ["restore-tool", "--all"]}})


# --- capability probe ---------------------------------------------------------------


def test_given_a_restore_command_when_capabilities_are_listed_then_transcript_restore_is_one_line(tmp_path):
    code, out, _err = run_cli(
        ["ai", "list-capabilities"], config={"resume_restore": {"command": ["restore-tool", "{session_id}"]}}
    )

    assert code == 0
    assert "transcript-restore" in out.splitlines()


def test_given_no_restore_command_when_capabilities_are_listed_then_transcript_restore_is_absent():
    code, out, _err = run_cli(["ai", "list-capabilities"], config={})

    assert code == 0
    assert "transcript-restore" not in out.splitlines()


# --- tmux session wrapper: the supervisor resolves and resumes on every start --------


def _preamble() -> str:
    return f"#!{sys.executable}\nimport json, os, sys, time\nfrom pathlib import Path\nroot = Path(os.environ['RESTORE_FIXTURE_ROOT'])\n"


@pytest.fixture
def wrapper(tmp_path, monkeypatch, register_repo):
    """Render the session wrapper for TITLE with fixture tmux, ai and claude programs; return a starter."""
    if sys.platform == "win32":
        pytest.skip("POSIX supervisor fixture")
    root = tmp_path / "wrapper"
    binaries = root / "bin"
    binaries.mkdir(parents=True)
    for name in ("bash", "base64", "cat", "date", "id", "mkdir", "mktemp", "ps", "rm", "sed", "sleep", "stat", "tr"):
        executable = shutil.which(name)
        assert executable is not None, f"wrapper fixture requires {name}"
        (binaries / name).symlink_to(executable)
    (binaries / "wc").symlink_to(shutil.which("wc"))
    (binaries / "python3").symlink_to(sys.executable)
    (binaries / "tmux").write_text(
        _preamble()
        + f"""args = sys.argv[1:]
started = root / 'session-started'
if args[:1] == ['list-sessions']: print({SESSION!r})
elif args[:1] == ['list-clients']: print('client-1')
elif args[:1] == ['display-message'] and '#{{session_name}}' in args: print({SESSION!r})
elif args[:1] == ['set-environment'] and 'AI_SESSION_STARTED' in args: started.touch()
elif args[:1] == ['show-environment'] and started.exists(): print('AI_SESSION_STARTED=1')
""",
        encoding="utf-8",
    )
    (binaries / "ai").write_text(
        _preamble()
        + """args = sys.argv[1:]
if args[:2] == ['internal', 'get-version']: print('unknown')
elif args[:2] == ['internal', 'acquire-generation-lease']: sys.exit(1)
elif args[:2] == ['internal', 'resolve-background-target']: sys.exit(0)
elif args[:2] in (['internal', 'resolve-continue-target'], ['internal', 'restore-transcript']) or (
    args[:1] == ['sessions'] and args[1:2] and args[1] in ('_begin', '_sequence', 'mark')
):
    os.execv(sys.executable, [sys.executable, '-m', 'ai_cli.main', *args])
""",
        encoding="utf-8",
    )
    repo = tmp_path / "myproject"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True)
    register_repo(repo, "myproject")
    worktree = repo / ".worktrees" / TITLE
    worktree.mkdir(parents=True)
    project = _cc_project_dir(worktree)
    (binaries / "claude").write_text(
        _preamble()
        + f"""project = Path({str(project)!r})
args = sys.argv[1:]
session_id = args[args.index('--resume') + 1] if '--resume' in args else None
record = {{
    'argv': args,
    'at': time.time(),
    'transcripts': sorted(str(p.relative_to(project)) for p in project.glob('**/*.jsonl')),
}}
with (root / 'launches').open('a', encoding='utf-8') as out: out.write(json.dumps(record) + '\\n')
sys.exit(130)
""",
        encoding="utf-8",
    )
    for program in binaries.iterdir():
        if not program.is_symlink():
            program.chmod(0o700)
    session_run_state.register(
        SESSION,
        recipe={
            "engine": "c",
            "session_name": SESSION,
            "owning_host": "host-a",
            "argv": ["ai", "c", TITLE],
            "extra_args": [],
            "options": {},
            "cwd": str(worktree),
            "project": "myproject",
        },
        host="host-a",
    )
    monkeypatch.setattr(main._session_script, "resolve_session_shell", lambda: shutil.which("bash"))
    source = main._engine_script_from_meta(
        {
            "engine": "c",
            "ai_name": TITLE,
            "session": SESSION,
            "prefix": "c-myproject-box-",
            "project_prefix": "myproject-box",
            "worktree_dir": str(worktree),
            "launch_log_path": str(root / "launch.log"),
        }
    )
    script = root / f"{SESSION}.sh"
    script.write_text(source, encoding="utf-8")
    env = {
        **os.environ,
        "PATH": str(binaries),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "RESTORE_FIXTURE_ROOT": str(root),
        "TMUX": "fixture-tmux-socket,1,0",
        "TMUX_PANE": "%1",
        "AI_HOST": "host-a",
    }
    env.pop("BASH_ENV", None)

    def run() -> tuple[int, list[dict], str]:
        log = root / "wrapper.log"
        with (
            log.open("w", encoding="utf-8") as diagnostics,
            bounded_supervisor(script, diagnostics, default_lifetime=240, env=env) as process,
        ):
            status = process.wait(timeout=180)
        launch_log = root / "launch.log"
        output = log.read_text(encoding="utf-8") + (
            launch_log.read_text(encoding="utf-8") if launch_log.exists() else ""
        )
        return status, _calls(root / "launches"), output

    return {"run": run, "project": project, "root": root}


def test_given_an_archived_transcript_when_the_supervisor_resumes_then_claude_starts_after_it_is_restored(
    wrapper, tmp_path
):
    project = wrapper["project"]
    _archive(_write_transcript(project, SESSION_ID, TITLE))
    hook = _hook(tmp_path, project)
    _write_config(hook["resume_restore"])

    _status, launches, output = wrapper["run"]()

    assert len(launches) == 1, output
    assert launches[0]["argv"][-4:] == ["--resume", SESSION_ID, "--name", TITLE]
    assert f"{SESSION_ID}.jsonl" in launches[0]["transcripts"]
    calls = _calls(hook["log"])
    assert calls and all(call["at"] < launches[0]["at"] for call in calls)
    assert {call["session_id"] for call in calls} == {SESSION_ID}


def test_given_archived_subagent_transcripts_when_the_supervisor_resumes_the_session_it_resolves_then_they_are_restored(
    wrapper, tmp_path
):
    project = wrapper["project"]
    _write_transcript(project, SESSION_ID, TITLE)
    subagent = project / SESSION_ID / "subagents" / "agent-1.jsonl"
    subagent.parent.mkdir(parents=True)
    subagent.write_text('{"type": "user"}\n', encoding="utf-8")
    _archive(subagent)
    hook = _hook(tmp_path, project)
    _write_config(hook["resume_restore"])

    _status, launches, output = wrapper["run"]()

    assert len(launches) == 1, output
    assert launches[0]["argv"][-4:] == ["--resume", SESSION_ID, "--name", TITLE]
    assert f"{SESSION_ID}/subagents/agent-1.jsonl" in launches[0]["transcripts"]
    assert [call["session_id"] for call in _calls(hook["log"])] == [SESSION_ID]


def test_given_a_failing_restore_command_when_the_supervisor_resumes_then_claude_never_starts(wrapper, tmp_path):
    project = wrapper["project"]
    _archive(_write_transcript(project, SESSION_ID, TITLE))
    hook = _hook(tmp_path, project, mode="fail")
    _write_config(hook["resume_restore"])

    _status, launches, output = wrapper["run"]()

    assert launches == [], output
    assert "exit 3" in output
    assert f"restore failed: archive for {SESSION_ID} does not verify; it is kept" in output
    assert (project / f"{SESSION_ID}.jsonl.zst").is_file()


def test_given_no_restore_command_when_the_supervisor_resumes_then_it_resumes_as_before(wrapper):
    project = wrapper["project"]
    _write_transcript(project, SESSION_ID, TITLE)

    _status, launches, output = wrapper["run"]()

    assert len(launches) == 1, output
    assert launches[0]["argv"][-4:] == ["--resume", SESSION_ID, "--name", TITLE]
