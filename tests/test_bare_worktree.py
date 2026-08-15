"""Bare-mode (no-tmux) session launches must still get worktree isolation.

Regression tests for the ordering bug where ``if bare:`` exec'd the engine
*before* the worktree setup block, so every launch on a machine with
``[session] use_tmux = false`` (or any ``-b/--bare`` launch) silently degraded to
a plain ``claude`` in the repo root -- no worktree, no ``--name``, no resume.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_cli.main import (
    _bare_engine_command,
    _cc_project_dir,
    _cc_session_is_live,
    _do_session_launch,
    _find_cc_session_by_title,
)
from ai_cli.session import build_session_name, find_next_index

_HAS_PROC = Path("/proc/self/stat").exists()

# --- CC project-dir encoding ---------------------------------------------------


def test_given_path_with_underscore_when_encoded_then_underscore_becomes_dash():
    """CC replaces every non-alphanumeric char, not just '/' and '.'.

    The old ``sed 's|[/.]|-|g'`` equivalent left underscores intact and so looked
    for the transcript in a directory that never exists.
    """
    got = _cc_project_dir(Path("/mnt/efs/fs-abc_fsap-123/projects/my-repo"))
    assert got.name == "-mnt-efs-fs-abc-fsap-123-projects-my-repo"
    assert "_" not in got.name


def test_given_path_with_dots_when_encoded_then_dots_become_dash():
    got = _cc_project_dir(Path("/home/u/.worktrees/kg-1"))
    assert got.name == "-home-u--worktrees-kg-1"


# --- transcript lookup by customTitle ------------------------------------------


def _write_transcript(project_dir: Path, uuid: str, title: str | None) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"{uuid}.jsonl"
    lines = [{"type": "file-history-snapshot", "messageId": "x"}]
    if title is not None:
        lines.append({"type": "custom-title", "customTitle": title, "sessionId": uuid})
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    return path


def _write_session_registry(home: Path, pid: int, session_id: str, proc_start: int | None = None) -> Path:
    """Write the Claude Code session-registry entry used to mark a live session."""
    sessions_dir = home / ".claude" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    registry_entry = sessions_dir / f"{pid}.json"
    record = {"pid": pid, "sessionId": session_id, "kind": "background"}
    if proc_start is not None:
        record["procStart"] = proc_start
    registry_entry.write_text(json.dumps(record))
    return registry_entry


def _self_proc_start() -> int:
    """Field 22 (starttime) of this test process, as Claude Code records it."""
    line = Path("/proc/self/stat").read_text(encoding="utf-8")
    return int(line.rpartition(")")[2].split()[19])


def _write_proc_stat(proc_dir: Path, pid: int, starttime: int, comm: str = "claude (node) x") -> Path:
    """Write a fake ``/proc/<pid>/stat`` whose comm contains spaces and parens."""
    (proc_dir / str(pid)).mkdir(parents=True, exist_ok=True)
    fields = ["S"] + [str(n) for n in range(4, 22)] + [str(starttime)]
    stat = proc_dir / str(pid) / "stat"
    stat.write_text(f"{pid} ({comm}) " + " ".join(fields) + "\n")
    return stat


def _reaped_pid() -> int:
    """A pid that has exited and been reaped -- i.e. one that is provably dead."""
    proc = subprocess.Popen([sys.executable, "-c", ""])
    proc.wait()
    return proc.pid


def test_given_matching_title_when_searched_then_returns_that_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    cwd = Path("/repo/wt")
    project_dir = _cc_project_dir(cwd)
    _write_transcript(project_dir, "aaaaaaaa-0000-4000-8000-000000000001", "kg-2")
    want = _write_transcript(project_dir, "bbbbbbbb-0000-4000-8000-000000000002", "kg-1")

    assert _find_cc_session_by_title(cwd, "kg-1") == want


def test_given_later_different_title_when_searched_then_first_title_remains_the_session_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    cwd = Path("/repo/wt")
    transcript = _write_transcript(_cc_project_dir(cwd), "cccccccc-0000-4000-8000-000000000003", "proj-1-2")
    with transcript.open("a") as handle:
        handle.write(json.dumps({"type": "custom-title", "customTitle": "proj-1", "sessionId": transcript.stem}) + "\n")

    assert _find_cc_session_by_title(cwd, "proj-1") is None
    assert _find_cc_session_by_title(cwd, "proj-1-2") == transcript


def test_given_no_matching_title_when_searched_then_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    cwd = Path("/repo/wt")
    _write_transcript(_cc_project_dir(cwd), "cccccccc-0000-4000-8000-000000000003", "kg-9")

    assert _find_cc_session_by_title(cwd, "kg-1") is None


def test_given_untitled_transcript_when_searched_then_ignored(tmp_path, monkeypatch):
    """A transcript with no customTitle must not be claimed by an arbitrary name."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    cwd = Path("/repo/wt")
    _write_transcript(_cc_project_dir(cwd), "dddddddd-0000-4000-8000-000000000004", None)

    assert _find_cc_session_by_title(cwd, "kg-1") is None


def test_given_missing_project_dir_when_searched_then_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert _find_cc_session_by_title(Path("/nope"), "kg-1") is None


# --- bare argv construction ----------------------------------------------------


def test_given_bare_claude_when_no_prior_session_then_passes_name_without_continue(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    argv = _bare_engine_command("c", "kg-1", tmp_path / "wt", None, "gemini", "--no-sandbox", [])

    assert argv[0] == "claude"
    assert "--name" in argv and argv[argv.index("--name") + 1] == "kg-1"
    assert "--continue" not in argv


def test_given_bare_claude_when_prior_session_exists_then_adds_continue(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    target = tmp_path / "wt"
    transcript = _write_transcript(_cc_project_dir(target), "eeeeeeee-0000-4000-8000-000000000005", "kg-1")
    os.utime(transcript, (1, 1))

    argv = _bare_engine_command("c", "kg-1", target, None, "gemini", "--no-sandbox", [])

    assert "--continue" in argv
    assert "--name" in argv
    assert transcript.stat().st_mtime > 1


def test_given_two_named_transcripts_when_bare_claude_resumes_then_touches_only_exact_title(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    target = tmp_path / "wt"
    project_dir = _cc_project_dir(target)
    matching = _write_transcript(project_dir, "12121212-0000-4000-8000-000000000005", "proj-1")
    other = _write_transcript(project_dir, "34343434-0000-4000-8000-000000000005", "proj-1-2")
    os.utime(matching, (1, 1))
    os.utime(other, (2, 2))

    argv = _bare_engine_command("c", "proj-1", target, None, "gemini", "--no-sandbox", [])

    assert "--continue" in argv
    assert matching.stat().st_mtime > 2
    assert other.stat().st_mtime == 2


def test_given_only_different_named_transcript_when_bare_claude_launches_then_does_not_continue(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    target = tmp_path / "wt"
    other = _write_transcript(_cc_project_dir(target), "56565656-0000-4000-8000-000000000005", "proj-1-2")
    os.utime(other, (2, 2))

    argv = _bare_engine_command("c", "proj-1", target, None, "gemini", "--no-sandbox", [])

    assert "--continue" not in argv
    assert other.stat().st_mtime == 2


@pytest.mark.skipif(not _HAS_PROC, reason="needs /proc to register a genuinely live pid")
def test_given_live_title_matched_session_when_bare_claude_then_warns_without_touching_or_continuing(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    target = tmp_path / "wt"
    session_id = "ffffffff-0000-4000-8000-000000000006"
    transcript = _write_transcript(_cc_project_dir(target), session_id, "kg-1")
    os.utime(transcript, (1, 1))
    pid = os.getpid()
    _write_session_registry(tmp_path, pid, session_id, proc_start=_self_proc_start())

    argv = _bare_engine_command("c", "kg-1", target, None, "gemini", "--no-sandbox", [])

    assert "--continue" not in argv
    assert transcript.stat().st_mtime == 1
    stderr = capsys.readouterr().err
    assert "kg-1" in stderr
    assert f"pid {pid}" in stderr


# --- session-registry liveness (AI-CLI-cc-session-live-mvht) --------------------


def test_given_registry_record_for_dead_pid_when_checked_then_not_live(tmp_path, monkeypatch):
    """The reported bug: an abandoned record blocked its session name forever.

    Claude Code does not reliably remove ``~/.claude/sessions/<pid>.json`` when a
    session is killed or crashes, so matching on ``sessionId`` alone refused
    ``ai c kg-1`` against a pid that no longer existed.
    """
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    session_id = "11111111-0000-4000-8000-00000000000a"
    dead_pid = _reaped_pid()
    _write_session_registry(tmp_path, dead_pid, session_id, proc_start=12345)

    assert _cc_session_is_live(Path(f"/x/{session_id}.jsonl")) == (False, None)


def test_given_stale_registry_record_when_bare_claude_then_still_continues(tmp_path, monkeypatch, capsys):
    """End-to-end shape of the bug: the refusal must not reach the launch argv."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    target = tmp_path / "wt"
    session_id = "22222222-0000-4000-8000-00000000000b"
    transcript = _write_transcript(_cc_project_dir(target), session_id, "kg-1")
    os.utime(transcript, (1, 1))
    _write_session_registry(tmp_path, _reaped_pid(), session_id, proc_start=12345)

    argv = _bare_engine_command("c", "kg-1", target, None, "gemini", "--no-sandbox", [])

    assert "--continue" in argv
    assert "still running" not in capsys.readouterr().err


@pytest.mark.skipif(not _HAS_PROC, reason="needs /proc to read a real starttime")
def test_given_running_pid_with_matching_proc_start_when_checked_then_live(tmp_path, monkeypatch):
    """The fix must not degrade into "always False" -- a real session still blocks."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    session_id = "33333333-0000-4000-8000-00000000000c"
    pid = os.getpid()
    _write_session_registry(tmp_path, pid, session_id, proc_start=_self_proc_start())

    assert _cc_session_is_live(Path(f"/x/{session_id}.jsonl")) == (True, pid)


def test_given_recycled_pid_when_checked_then_not_live(tmp_path, monkeypatch):
    """A running pid whose starttime differs is a different process, not this session."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    session_id = "44444444-0000-4000-8000-00000000000d"
    proc_dir = tmp_path / "proc"
    _write_proc_stat(proc_dir, 4242, starttime=999)
    _write_session_registry(tmp_path, 4242, session_id, proc_start=111)

    assert _cc_session_is_live(Path(f"/x/{session_id}.jsonl"), proc_dir=proc_dir) == (False, None)


def test_given_matching_starttime_in_stat_when_checked_then_live(tmp_path, monkeypatch):
    """Field 22 is read from after the *last* ')' -- a comm with parens must parse."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    session_id = "55555555-0000-4000-8000-00000000000e"
    proc_dir = tmp_path / "proc"
    _write_proc_stat(proc_dir, 4242, starttime=777, comm="claude (worker) 1")
    _write_session_registry(tmp_path, 4242, session_id, proc_start=777)

    assert _cc_session_is_live(Path(f"/x/{session_id}.jsonl"), proc_dir=proc_dir) == (True, 4242)


def test_given_unreadable_proc_stat_when_checked_then_not_live(tmp_path, monkeypatch):
    """Fail open: an unverifiable record must not block a launch."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    session_id = "66666666-0000-4000-8000-00000000000f"
    proc_dir = tmp_path / "proc"
    (proc_dir / "4242").mkdir(parents=True)  # pid dir present, stat missing
    _write_session_registry(tmp_path, 4242, session_id, proc_start=777)

    assert _cc_session_is_live(Path(f"/x/{session_id}.jsonl"), proc_dir=proc_dir) == (False, None)


@pytest.mark.parametrize("stat_body", ["", "4242 (claude) S", "4242 claude S 1 2 3", "no-parens-at-all"])
def test_given_malformed_proc_stat_when_checked_then_not_live(tmp_path, monkeypatch, stat_body):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    session_id = "77777777-0000-4000-8000-000000000010"
    proc_dir = tmp_path / "proc"
    (proc_dir / "4242").mkdir(parents=True)
    (proc_dir / "4242" / "stat").write_text(stat_body)
    _write_session_registry(tmp_path, 4242, session_id, proc_start=777)

    assert _cc_session_is_live(Path(f"/x/{session_id}.jsonl"), proc_dir=proc_dir) == (False, None)


def test_given_dead_record_when_checked_then_record_is_pruned(tmp_path, monkeypatch):
    """Self-healing: the sweep that used to need a human happens on every check."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    proc_dir = tmp_path / "proc"
    proc_dir.mkdir()
    stale = _write_session_registry(tmp_path, 4242, "88888888-0000-4000-8000-000000000011", proc_start=777)
    unrelated = _write_session_registry(tmp_path, 4243, "99999999-0000-4000-8000-000000000012", proc_start=778)

    _cc_session_is_live(Path("/x/aaaaaaaa-0000-4000-8000-000000000013.jsonl"), proc_dir=proc_dir)

    assert not stale.exists()
    assert not unrelated.exists()


def test_given_live_record_when_checked_then_record_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    proc_dir = tmp_path / "proc"
    _write_proc_stat(proc_dir, 4242, starttime=777)
    kept = _write_session_registry(tmp_path, 4242, "bbbbbbbb-0000-4000-8000-000000000014", proc_start=777)

    _cc_session_is_live(Path("/x/cccccccc-0000-4000-8000-000000000015.jsonl"), proc_dir=proc_dir)

    assert kept.exists()


def test_given_unverifiable_record_when_checked_then_record_is_kept(tmp_path, monkeypatch):
    """Only a positively dead pid is pruned -- an unreadable one is left alone."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    proc_dir = tmp_path / "proc"
    (proc_dir / "4242").mkdir(parents=True)
    kept = _write_session_registry(tmp_path, 4242, "dddddddd-0000-4000-8000-000000000016", proc_start=777)

    _cc_session_is_live(Path("/x/eeeeeeee-0000-4000-8000-000000000017.jsonl"), proc_dir=proc_dir)

    assert kept.exists()


def test_given_recycled_pid_when_checked_then_record_is_kept(tmp_path, monkeypatch):
    """A starttime mismatch is not proof enough to delete: only a gone pid is.

    ``procStart`` in a unit this code does not expect would mismatch too, and
    deleting a live session's record misleads every other reader of the registry.
    """
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    proc_dir = tmp_path / "proc"
    _write_proc_stat(proc_dir, 4242, starttime=999)
    kept = _write_session_registry(tmp_path, 4242, "aaaabbbb-0000-4000-8000-000000000019", proc_start=111)

    _cc_session_is_live(Path("/x/ccccdddd-0000-4000-8000-00000000001a.jsonl"), proc_dir=proc_dir)

    assert kept.exists()


def test_given_record_without_proc_start_when_pid_runs_then_live(tmp_path, monkeypatch):
    """No recorded starttime cannot refute ownership, so the refusal is preserved."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    session_id = "eeeeffff-0000-4000-8000-00000000001b"
    proc_dir = tmp_path / "proc"
    _write_proc_stat(proc_dir, 4242, starttime=777)
    _write_session_registry(tmp_path, 4242, session_id)

    assert _cc_session_is_live(Path(f"/x/{session_id}.jsonl"), proc_dir=proc_dir) == (True, 4242)


def test_given_unlinkable_dead_record_when_checked_then_walk_continues(tmp_path, monkeypatch):
    """A failed prune (race, read-only file) must not abort the liveness answer.

    The record that cannot be unlinked sorts before the live one, so an escaping
    ``OSError`` would silently answer "not live" for a session that is running.
    """
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    proc_dir = tmp_path / "proc"
    _write_proc_stat(proc_dir, 4243, starttime=777)
    session_id = "ffffffff-0000-4000-8000-000000000018"
    _write_session_registry(tmp_path, 4242, "00001111-0000-4000-8000-00000000001c", proc_start=555)
    _write_session_registry(tmp_path, 4243, session_id, proc_start=777)

    def _boom(self, missing_ok=False):
        raise OSError("read-only")

    monkeypatch.setattr(Path, "unlink", _boom)

    assert _cc_session_is_live(Path(f"/x/{session_id}.jsonl"), proc_dir=proc_dir) == (True, 4243)


def test_given_bare_claude_when_extra_args_then_appended_last(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    argv = _bare_engine_command("c", "kg-1", tmp_path / "wt", None, "gemini", "--no-sandbox", ["--model", "opus"])

    assert argv[-2:] == ["--model", "opus"]


def test_given_bare_gemini_with_uuid_when_built_then_resumes_that_uuid(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    argv = _bare_engine_command("g", "kg-1", tmp_path / "wt", "uuid-123", "gemini", "-s", [])

    assert argv[:2] == ["gemini", "-y"]
    assert "-r" in argv and argv[argv.index("-r") + 1] == "uuid-123"


def test_given_bare_gemini_without_uuid_when_built_then_falls_back_to_resume_load(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    argv = _bare_engine_command("g", "kg-1", tmp_path / "wt", None, "gemini", "-s", [])

    assert "-i" in argv and argv[argv.index("-i") + 1] == "/resume load kg-1"


# --- index discovery without tmux ---------------------------------------------


def test_given_no_tmux_when_finding_index_then_does_not_shell_out_to_tmux():
    """Bare mode must not invoke tmux at all -- it may not be installed."""
    with (
        patch("ai_cli.session.subprocess.run") as mock_run,
        patch("ai_cli.session.detect_repo_root", return_value=None),
    ):
        idx = find_next_index("c-kg-", use_tmux=False)

    assert idx == 1
    assert mock_run.call_count == 0


def test_given_worktree_without_live_process_when_finding_index_then_slot_is_reused(tmp_path):
    """A leftover worktree dir with no engine running is a free slot.

    Bare mode has no session-exit hook to remove worktrees (the tmux path's EXIT
    trap does that), so treating any existing directory as occupied would make
    the index climb forever.
    """
    (tmp_path / ".worktrees" / "kg-1").mkdir(parents=True)
    with (
        patch("ai_cli.session.detect_repo_root", return_value=tmp_path),
        patch("ai_cli.session._worktree_has_live_session", return_value=False),
    ):
        assert find_next_index("c-kg-", use_tmux=False) == 1


def test_given_worktree_with_live_process_when_finding_index_then_skips_to_next(tmp_path):
    (tmp_path / ".worktrees" / "kg-1").mkdir(parents=True)

    def _live(path):
        return path.name == "kg-1"

    with (
        patch("ai_cli.session.detect_repo_root", return_value=tmp_path),
        patch("ai_cli.session._worktree_has_live_session", side_effect=_live),
    ):
        assert find_next_index("c-kg-", use_tmux=False) == 2


def test_given_bare_mode_when_building_session_name_then_no_tmux_call():
    with (
        patch("ai_cli.session.subprocess.run") as mock_run,
        patch("ai_cli.session.detect_repo_root", return_value=None),
    ):
        session_id, ai_name = build_session_name("c", "kg", "", use_tmux=False)

    assert (session_id, ai_name) == ("c-kg-1", "kg-1")
    assert mock_run.call_count == 0


def test_given_explicit_index_when_bare_then_index_respected_verbatim():
    """`ai c 1` must always mean session 1, tmux or not."""
    session_id, ai_name = build_session_name("c", "kg", "1", use_tmux=False)
    assert (session_id, ai_name) == ("c-kg-1", "kg-1")


def test_given_legacy_case_worktree_when_bare_explicit_index_then_reuses_legacy_slot(tmp_path):
    (tmp_path / ".worktrees" / "app-7").mkdir(parents=True)

    with patch("ai_cli.session.detect_repo_root", return_value=tmp_path):
        session_id, ai_name = build_session_name("c", "APP", "7", use_tmux=False)

    assert (session_id, ai_name) == ("c-app-7", "app-7")


# --- end-to-end launch ordering ------------------------------------------------


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def real_repo(tmp_path):
    """A real git repo with one commit and a real ``origin``, cloned from a bare remote.

    The clone is what makes ``origin/main`` exist. ``create_worktree`` hard-fails when it
    cannot set a worktree branch's upstream to ``origin/main`` (AI-CLI-128: a branch with
    no upstream is one ``git push`` away from publishing a same-named remote branch), so a
    bare ``git init`` fixture no longer represents a repo the launcher will accept.
    """
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)

    seed = tmp_path / "seed"
    seed.mkdir()
    _git("init", "-b", "main", cwd=seed)
    _git("config", "user.email", "t@example.com", cwd=seed)
    _git("config", "user.name", "T", cwd=seed)
    (seed / "README.md").write_text("hi\n")
    _git("add", "-A", cwd=seed)
    _git("commit", "-m", "init", cwd=seed)
    _git("push", "-q", str(remote), "main", cwd=seed)

    repo = tmp_path / "myproject"
    subprocess.run(["git", "clone", "-q", str(remote), str(repo)], check=True, capture_output=True)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "T", cwd=repo)
    return repo


def _launch_kwargs(**over):
    kwargs = {
        "engine": "c",
        "name": "1",
        "resume": False,
        "once": False,
        "bare": True,
        "notify": False,
        "sandbox": False,
        "no_worktree": False,
        "remote": False,
        "project": "",
        "is_remote": False,
        "project_prefix_override": "kg",
        "extra_args": [],
        "config": {"worktree": {"enabled": True}, "session": {"use_tmux": False}},
    }
    kwargs.update(over)
    return kwargs


def test_given_bare_launch_when_worktree_enabled_then_worktree_created_and_entered(real_repo, tmp_path, monkeypatch):
    """The regression: a bare launch must create the worktree and exec inside it."""
    monkeypatch.chdir(real_repo)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    execs: list[tuple] = []

    def fake_execvp(file, args):
        execs.append((file, list(args), str(Path.cwd())))
        raise SystemExit(0)

    with (
        patch("ai_cli.main.os.execvp", side_effect=fake_execvp),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.trust.ensure_workspace_trusted"),
    ):
        with pytest.raises(SystemExit):
            _do_session_launch(**_launch_kwargs())

    worktree = real_repo / ".worktrees" / "kg-1"
    assert worktree.is_dir(), "bare launch must create the worktree"

    listed = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=real_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    registered_worktrees = [
        Path(line.removeprefix("worktree ")) for line in listed.splitlines() if line.startswith("worktree ")
    ]
    assert worktree in registered_worktrees, "worktree must be registered with git"

    branches = subprocess.run(
        ["git", "branch", "--list", "wt-kg-1"],
        cwd=real_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "wt-kg-1" in branches, "worktree branch must be created"

    assert len(execs) == 1, "expected exactly one exec"
    file, args, cwd_at_exec = execs[0]
    # This fixture repo has no .envrc, so the engine is exec'd directly — direnv
    # has nothing to load and must not be a precondition for launching.
    assert file == "claude"
    assert cwd_at_exec == str(worktree), "must chdir into the worktree before exec"
    assert "--name" in args and args[args.index("--name") + 1] == "kg-1"


def test_given_bare_launch_when_repo_has_envrc_then_execs_under_direnv(real_repo, tmp_path, monkeypatch):
    """With a usable .envrc, the engine must start under the worktree's environment.

    ``direnv exec DIR`` must be pointed at the *worktree*, not the repo root, or
    the session gets the wrong project environment.
    """
    (real_repo / ".envrc").write_text("export PROJECT_ENV=1\n")
    monkeypatch.chdir(real_repo)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    execs: list[tuple] = []

    def fake_execvp(file, args):
        execs.append((file, list(args), str(Path.cwd())))
        raise SystemExit(0)

    with (
        patch("ai_cli.main.os.execvp", side_effect=fake_execvp),
        patch("ai_cli.main._direnv_env_usable", return_value=True),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.session._allow_trusted_worktree_envrc"),
        patch("ai_cli.trust.ensure_workspace_trusted"),
    ):
        with pytest.raises(SystemExit):
            _do_session_launch(**_launch_kwargs())

    worktree = real_repo / ".worktrees" / "kg-1"
    file, args, cwd_at_exec = execs[0]
    assert file == "direnv"
    assert args[:3] == ["direnv", "exec", str(worktree)]
    assert cwd_at_exec == str(worktree)
    assert "--name" in args and args[args.index("--name") + 1] == "kg-1"


def test_given_bare_launch_when_no_worktree_flag_then_runs_in_repo_root(real_repo, tmp_path, monkeypatch):
    monkeypatch.chdir(real_repo)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    execs: list[tuple] = []

    def fake_execvp(file, args):
        execs.append((file, list(args), str(Path.cwd())))
        raise SystemExit(0)

    with (
        patch("ai_cli.main.os.execvp", side_effect=fake_execvp),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.trust.ensure_workspace_trusted"),
    ):
        with pytest.raises(SystemExit):
            _do_session_launch(**_launch_kwargs(no_worktree=True))

    assert not (real_repo / ".worktrees").exists()
    assert execs[0][2] == str(real_repo)


def test_given_bare_launch_when_executed_then_task_list_id_pinned(real_repo, tmp_path, monkeypatch):
    """CLAUDE_CODE_TASK_LIST_ID must be pinned to ai_name, as the tmux path does."""
    monkeypatch.chdir(real_repo)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.delenv("CLAUDE_CODE_TASK_LIST_ID", raising=False)

    with (
        patch("ai_cli.main.os.execvp", side_effect=SystemExit(0)),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.trust.ensure_workspace_trusted"),
    ):
        with pytest.raises(SystemExit):
            _do_session_launch(**_launch_kwargs())

    assert os.environ.get("CLAUDE_CODE_TASK_LIST_ID") == "kg-1"


def test_given_bare_launch_when_tmux_absent_then_no_tmux_invocation(real_repo, tmp_path, monkeypatch):
    """A bare launch must never shell out to tmux."""
    monkeypatch.chdir(real_repo)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    tmux_calls: list[list] = []
    real_run = subprocess.run

    def watch_run(cmd, *a, **kw):
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "tmux":
            tmux_calls.append(list(cmd))
        return real_run(cmd, *a, **kw)

    with (
        patch("ai_cli.main.os.execvp", side_effect=SystemExit(0)),
        patch("ai_cli.main.subprocess.run", side_effect=watch_run),
        patch("ai_cli.session.subprocess.run", side_effect=watch_run),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.trust.ensure_workspace_trusted"),
    ):
        with pytest.raises(SystemExit):
            _do_session_launch(**_launch_kwargs())

    assert tmux_calls == [], f"bare mode invoked tmux: {tmux_calls!r}"


def test_given_use_tmux_false_config_when_launched_then_still_creates_worktree(real_repo, tmp_path, monkeypatch):
    """`[session] use_tmux = false` implies bare -- and must not lose worktrees.

    This is the exact configuration that silently disabled worktree isolation.
    """
    monkeypatch.chdir(real_repo)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))

    with (
        patch("ai_cli.main.os.execvp", side_effect=SystemExit(0)),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.trust.ensure_workspace_trusted"),
    ):
        # bare=False here: the config flag alone must promote it.
        with pytest.raises(SystemExit):
            _do_session_launch(**_launch_kwargs(bare=False))

    assert (real_repo / ".worktrees" / "kg-1").is_dir()
