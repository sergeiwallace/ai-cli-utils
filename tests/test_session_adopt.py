"""Tests for ai_cli.session_adopt — full adoption of an unmanaged CC session.

Derived from the acceptance criteria, not from the implementation:

* the worktree is created when absent and reused (never clobbered) when present;
* a duplicate title is an unconditional human gate — non-zero exit, nothing
  written, and ``-y/--yes`` does not cover it;
* the transcript move is delegated to ``cc_migrate``;
* task ids are namespace-scoped, so a merge renumbers instead of clobbering;
* auto-memory is copied (never moved, never overwritten);
* a live session is refused;
* a re-run adopts nothing;
* the free-space precheck fails cleanly before writing;
* the post-adopt probe asserts that ``ai c`` really resolves the transcript.

Everything writes inside ``tmp_path``: the fake ``~/.claude`` home, the fake repo
and its worktrees. No test touches the real user state or this repository's tree.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_cli.cc_migrate import cc_project_dir, transcript_title
from ai_cli.session_adopt import (
    AdoptionError,
    InsufficientSpaceError,
    LiveSessionError,
    TitleCollision,
    adopt_all,
    adopt_memory,
    adopt_session,
    check_free_space,
    find_title_candidates,
    live_sessions,
    merge_task_namespace,
    next_free_index,
    probe_resolves,
    retitle_transcript,
    split_ai_name,
    task_namespace_candidates,
    titled_sessions,
    used_indexes,
)
from conftest import run_cli


UUID = "11111111-2222-4333-8444-555555555555"
OTHER_UUID = "99999999-8888-4777-8666-555555555555"


def _record(**kw) -> str:
    return json.dumps(kw, separators=(",", ":"))


def _write_transcript(project_dir: Path, uuid: str, title: str, cwd: Path, extra_lines: int = 0) -> Path:
    project_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lines = [
        _record(type="user", sessionId=uuid, cwd=str(cwd), customTitle=title),
        _record(type="assistant", sessionId=uuid, cwd=str(cwd), customTitle=title),
        _record(type="user", sessionId=uuid, cwd=str(cwd / "docs")),
    ]
    lines += [_record(type="user", sessionId=uuid, cwd=str(cwd), n=i) for i in range(extra_lines)]
    path = project_dir / f"{uuid}.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return path


@pytest.fixture
def world(tmp_path):
    """A fake repo with .worktrees/, a fake ~/.claude, and one adoptable session.

    The session ``myproject-2`` was started at the repo root (the mistake this
    command exists to correct), so its transcript sits in the *root's* project
    directory. Nothing is live: ``proc`` is an empty fake /proc.
    """
    repo = tmp_path / "projects" / "myproject"
    (repo / ".worktrees").mkdir(parents=True)
    home = tmp_path / "claude-home"
    (home / "sessions").mkdir(parents=True)
    proc = tmp_path / "proc"
    proc.mkdir()

    src_dir = cc_project_dir(repo, home)
    _write_transcript(src_dir, UUID, "myproject-2", repo)
    return {"repo": repo, "home": home, "proc": proc, "src_dir": src_dir, "tmp": tmp_path}


@pytest.fixture
def adopt(world):
    """Call ``adopt_session`` with this world's fake home and /proc pre-bound."""

    def _adopt(name="myproject-2", **kw):
        kw.setdefault("claude_home", world["home"])
        kw.setdefault("proc_dir", world["proc"])
        return adopt_session(world["repo"], name, **kw)

    return _adopt


@pytest.fixture
def existing_worktree(world):
    """Pre-create .worktrees/myproject-2 so no git call is ever needed."""
    wt = world["repo"] / ".worktrees" / "myproject-2"
    wt.mkdir(parents=True, exist_ok=True)
    return wt


def _mark_live(world, pid: int, name: str, cwd: Path) -> None:
    (world["proc"] / str(pid)).mkdir()
    (world["home"] / "sessions" / f"{pid}.json").write_text(json.dumps({"pid": pid, "name": name, "cwd": str(cwd)}))


# ---- helpers: names and indexes ---------------------------------------------


def test_split_given_prefixed_name_when_split_then_prefix_and_index_returned():
    assert split_ai_name("myproject-12") == ("myproject", 12)


def test_split_given_hyphenated_prefix_when_split_then_only_trailing_number_is_the_index():
    assert split_ai_name("my-long-project-3") == ("my-long-project", 3)


def test_split_given_name_without_index_when_split_then_none():
    assert split_ai_name("myproject") is None


def test_used_indexes_given_worktrees_and_titles_when_scanned_then_both_sources_counted(world):
    (world["repo"] / ".worktrees" / "myproject-5").mkdir()
    (world["repo"] / ".worktrees" / "otherproject-9").mkdir()
    used = used_indexes(world["repo"], "myproject", world["home"])
    assert used == {2, 5}, "index 2 comes from the transcript title, 5 from the worktree"


def test_next_free_index_given_lower_indexes_taken_when_computed_then_lowest_gap_returned(world):
    (world["repo"] / ".worktrees" / "myproject-1").mkdir()
    (world["repo"] / ".worktrees" / "myproject-3").mkdir()
    assert next_free_index(world["repo"], "myproject", world["home"]) == 4


def test_next_free_index_given_a_gap_below_the_top_when_computed_then_the_gap_is_used(world):
    (world["repo"] / ".worktrees" / "myproject-3").mkdir()
    assert next_free_index(world["repo"], "myproject", world["home"]) == 1


# ---- live-session refusal ----------------------------------------------------


def test_live_sessions_given_a_record_whose_pid_is_gone_when_scanned_then_not_reported(world):
    (world["home"] / "sessions" / "4242.json").write_text(json.dumps({"pid": 4242, "name": "session-1", "cwd": "/x"}))
    assert live_sessions(world["home"], world["proc"]) == []


def test_live_sessions_given_a_running_pid_when_scanned_then_reported_with_name_and_cwd(world):
    _mark_live(world, 4242, "session-1", world["repo"])
    found = live_sessions(world["home"], world["proc"])
    assert [(s.pid, s.name) for s in found] == [(4242, "session-1")]


def test_adopt_given_the_named_session_is_live_when_adopted_then_refused_and_nothing_written(
    world, adopt, existing_worktree
):
    _mark_live(world, 4242, "myproject-2", world["tmp"] / "elsewhere")
    with pytest.raises(LiveSessionError, match="4242"):
        adopt()
    assert (world["src_dir"] / f"{UUID}.jsonl").is_file(), "source transcript must be untouched"
    assert not cc_project_dir(existing_worktree, world["home"]).exists()


def test_adopt_given_a_live_session_holding_the_destination_worktree_when_adopted_then_refused(
    world, adopt, existing_worktree
):
    _mark_live(world, 4242, "some-other-name", existing_worktree)
    with pytest.raises(LiveSessionError, match="destination worktree"):
        adopt()


def test_adopt_given_a_live_session_sharing_the_transcript_uuid_when_adopted_then_refused(
    world, adopt, existing_worktree
):
    """A renamed live session is still the session being adopted — match on UUID too."""
    (world["proc"] / "4242").mkdir()
    (world["home"] / "sessions" / "4242.json").write_text(
        json.dumps({"pid": 4242, "name": "renamed-since", "cwd": str(world["tmp"]), "sessionId": UUID})
    )
    with pytest.raises(LiveSessionError, match="being adopted"):
        adopt()


def test_adopt_given_an_unrelated_live_session_in_the_source_root_when_adopted_then_allowed(
    world, adopt, existing_worktree
):
    """A sibling session sharing the source root must not block the adoption.

    Memory is copied rather than moved and each transcript is its own file, so the
    sibling is unaffected. Refusing here would report "still running" for every
    error, including an unknown title — masking the real cause.
    """
    (world["proc"] / "4242").mkdir()
    (world["home"] / "sessions" / "4242.json").write_text(
        json.dumps({"pid": 4242, "name": "myproject-8", "cwd": str(world["repo"]), "sessionId": OTHER_UUID})
    )
    result = adopt()
    assert result.resolved is not None


def test_adopt_given_an_unknown_title_and_an_unrelated_live_session_when_adopted_then_the_real_error_surfaces(
    world, adopt, existing_worktree
):
    """The refusal must not mask a different failure (probe-adequacy regression)."""
    (world["proc"] / "4242").mkdir()
    (world["home"] / "sessions" / "4242.json").write_text(
        json.dumps({"pid": 4242, "name": "myproject-8", "cwd": str(world["repo"]), "sessionId": OTHER_UUID})
    )
    with pytest.raises(AdoptionError, match="no transcript titled"):
        adopt(name="myproject-99")


def test_adopt_given_a_dead_session_record_when_adopted_then_allowed(world, adopt, existing_worktree):
    (world["home"] / "sessions" / "4242.json").write_text(
        json.dumps({"pid": 4242, "name": "myproject-2", "cwd": str(world["repo"])})
    )
    result = adopt()
    assert result.resolved is not None


# ---- free-space precheck -----------------------------------------------------


def test_check_space_given_more_needed_than_free_when_checked_then_raises_naming_the_shortfall(tmp_path):
    with pytest.raises(InsufficientSpaceError, match="refusing to start"):
        check_free_space(tmp_path, needed=10**15)


def test_check_space_given_ample_room_when_checked_then_returns_free_bytes(tmp_path):
    assert check_free_space(tmp_path, needed=1, margin=0) > 0


def test_adopt_given_insufficient_space_when_adopted_then_fails_before_writing(
    world, adopt, existing_worktree, monkeypatch
):
    monkeypatch.setattr("ai_cli.session_adopt._free_bytes", lambda path: 1024)
    with pytest.raises(InsufficientSpaceError):
        adopt()
    assert (world["src_dir"] / f"{UUID}.jsonl").is_file()
    assert not cc_project_dir(existing_worktree, world["home"]).exists()


# ---- duplicate-title collision: the hard gate -------------------------------


@pytest.fixture
def collision(world):
    """A second transcript in an existing worktree claiming the same title."""
    wt = world["repo"] / ".worktrees" / "myproject-2"
    wt.mkdir(parents=True, exist_ok=True)
    other_dir = cc_project_dir(wt, world["home"])
    other = _write_transcript(other_dir, OTHER_UUID, "myproject-2", wt, extra_lines=40)
    return {"worktree": wt, "path": other, "project_dir": other_dir}


def test_find_candidates_given_two_transcripts_with_one_title_when_scanned_then_both_returned(world, collision):
    found = find_title_candidates(world["repo"], "myproject-2", world["home"])
    assert len(found) == 2
    assert {c.path for c in found} == {world["src_dir"] / f"{UUID}.jsonl", collision["path"]}


def test_find_candidates_given_candidates_when_described_then_size_lines_cwd_and_mtime_present(world, collision):
    found = find_title_candidates(world["repo"], "myproject-2", world["home"])
    small = min(found, key=lambda c: c.lines)
    large = max(found, key=lambda c: c.lines)
    assert small.lines == 3 and large.lines == 43
    assert large.size > small.size
    assert small.cwd == str(world["repo"])
    assert large.cwd == str(collision["worktree"])
    assert all(c.mtime > 0 for c in found)


def test_adopt_given_a_duplicate_title_when_adopted_then_gated_with_both_candidates(world, adopt, collision):
    with pytest.raises(TitleCollision) as caught:
        adopt()
    assert len(caught.value.candidates) == 2
    assert caught.value.prefix == "myproject"
    assert caught.value.free_index == 1, "index 1 is claimed by neither a worktree nor a title"


def test_adopt_given_a_duplicate_title_when_gated_then_nothing_is_written(world, adopt, collision):
    before = {
        "source": (world["src_dir"] / f"{UUID}.jsonl").read_bytes(),
        "other": collision["path"].read_bytes(),
    }
    with pytest.raises(TitleCollision):
        adopt()
    assert (world["src_dir"] / f"{UUID}.jsonl").read_bytes() == before["source"]
    assert collision["path"].read_bytes() == before["other"]
    assert not (world["home"] / "tasks").exists()


def test_adopt_given_a_collision_and_a_free_index_when_scanned_then_the_index_collides_with_neither(world, collision):
    (world["repo"] / ".worktrees" / "myproject-1").mkdir()
    _write_transcript(
        cc_project_dir(world["repo"] / ".worktrees" / "myproject-1", world["home"]),
        "22222222-2222-4333-8444-555555555555",
        "myproject-3",
        world["repo"] / ".worktrees" / "myproject-1",
    )
    # 1 taken by a worktree, 2 by the collision, 3 by a title in another worktree.
    assert next_free_index(world["repo"], "myproject", world["home"]) == 4


def test_adopt_given_a_new_title_without_retitle_mode_when_adopted_then_refused(world, adopt, existing_worktree):
    with pytest.raises(AdoptionError, match="on_collision"):
        adopt(new_title="myproject-4")


# ---- retitle-to-free-index ---------------------------------------------------


def test_retitle_given_a_transcript_when_retitled_then_every_matching_record_is_rewritten(world):
    path = world["src_dir"] / f"{UUID}.jsonl"
    changed = retitle_transcript(path, "myproject-2", "myproject-7")
    assert changed == 2
    assert transcript_title(path) == "myproject-7"
    assert "myproject-2" not in path.read_text()


def test_retitle_given_a_transcript_when_retitled_then_mtime_is_preserved(world):
    import os

    path = world["src_dir"] / f"{UUID}.jsonl"
    os.utime(path, (1_000_000_000, 1_000_000_000))
    retitle_transcript(path, "myproject-2", "myproject-7")
    assert path.stat().st_mtime == pytest.approx(1_000_000_000)


def test_retitle_given_an_unrelated_title_when_retitled_then_nothing_changes(world):
    path = world["src_dir"] / f"{UUID}.jsonl"
    before = path.read_bytes()
    assert retitle_transcript(path, "myproject-9", "myproject-7") == 0
    assert path.read_bytes() == before


def test_adopt_given_a_confirmed_new_title_when_retitled_then_new_index_resolves(world, adopt, collision):
    target = world["repo"] / ".worktrees" / "myproject-1"
    target.mkdir(parents=True)
    result = adopt(on_collision="retitle", new_title="myproject-1")
    assert result.retitled_from == "myproject-2"
    assert result.dest_root == target
    assert probe_resolves(target, "myproject-1", world["home"]) == result.migration.dest_jsonl


def test_adopt_given_a_retitle_when_done_then_the_original_title_still_resolves(world, adopt, collision):
    (world["repo"] / ".worktrees" / "myproject-1").mkdir(parents=True)
    adopt(on_collision="retitle", new_title="myproject-1")
    still = probe_resolves(collision["worktree"], "myproject-2", world["home"])
    assert still == collision["path"], "the transcript that kept the original title must still resolve"


# ---- transcript adoption + the resolve probe --------------------------------


def test_adopt_given_an_unmanaged_session_when_adopted_then_probe_resolves_the_transcript(
    world, adopt, existing_worktree
):
    result = adopt()
    dest = cc_project_dir(existing_worktree, world["home"]) / f"{UUID}.jsonl"
    assert result.migration.dest_jsonl == dest and dest.is_file()
    assert result.resolved == dest
    assert not result.warnings


def test_probe_given_nothing_adopted_when_probed_then_returns_none(world, existing_worktree):
    assert probe_resolves(existing_worktree, "myproject-2", world["home"]) is None


def test_probe_given_the_transcript_in_the_wrong_project_dir_when_probed_then_returns_none(world, existing_worktree):
    """The probe must fail when a transcript exists but not where resume looks."""
    assert (world["src_dir"] / f"{UUID}.jsonl").is_file()
    assert probe_resolves(existing_worktree, "myproject-2", world["home"]) is None


def test_probe_given_the_transcript_under_a_different_title_when_probed_then_returns_none(world, existing_worktree):
    dest_dir = cc_project_dir(existing_worktree, world["home"])
    _write_transcript(dest_dir, UUID, "myproject-9", existing_worktree)
    assert probe_resolves(existing_worktree, "myproject-2", world["home"]) is None


def test_adopt_given_the_probe_finds_the_wrong_file_when_adopted_then_reported_as_failed(
    world, adopt, existing_worktree, monkeypatch
):
    """The post-adopt probe must be able to fail, and say so.

    Standing in for the real defect class the probe exists to catch — a transcript
    written where ``ai c`` does not look — by making the probe return a different
    path than the one adoption wrote.
    """
    decoy = world["tmp"] / "decoy.jsonl"
    monkeypatch.setattr("ai_cli.session_adopt.probe_resolves", lambda *a, **k: decoy)
    result = adopt()
    assert any("post-adopt check FAILED" in w for w in result.warnings)


def test_cli_given_a_failed_probe_when_invoked_then_exits_nonzero(world, existing_worktree, monkeypatch):
    import ai_cli.session_adopt as module

    real = module.adopt_session

    def _adopt(repo_root, name, **kw):
        kw.setdefault("claude_home", world["home"])
        kw.setdefault("proc_dir", world["proc"])
        return real(world["repo"], name, **kw)

    monkeypatch.setattr(module, "adopt_session", _adopt)
    monkeypatch.setattr("ai_cli.session.detect_repo_root", lambda: world["repo"])
    monkeypatch.setattr(module, "probe_resolves", lambda *a, **k: world["tmp"] / "decoy.jsonl")
    code, out, err = run_cli(["ai", "session-adopt", "myproject-2", "-y"])
    assert code == 1
    assert "post-adopt check FAILED" in err


def test_adopt_given_a_retitle_whose_original_stops_resolving_when_adopted_then_warned(
    world, adopt, collision, monkeypatch
):
    """The both-directions check must also report the reverse failure.

    Adopting under a new title must leave the original title still resolving; if
    it does not, the transcript that kept it is not where it should be.
    """
    (world["repo"] / ".worktrees" / "myproject-1").mkdir(parents=True)

    def _probe(dest_root, title, home=None):
        return None if title == "myproject-2" else probe_resolves(dest_root, title, home)

    monkeypatch.setattr("ai_cli.session_adopt.probe_resolves", _probe)
    result = adopt(on_collision="retitle", new_title="myproject-1")
    assert any("no longer resolves" in w for w in result.warnings)


def test_adopt_given_move_semantics_when_adopted_then_source_transcript_is_gone(world, adopt, existing_worktree):
    adopt()
    assert not (world["src_dir"] / f"{UUID}.jsonl").exists()


# ---- malformed and unreadable inputs ----------------------------------------


def test_live_sessions_given_an_unparseable_record_when_scanned_then_skipped(world):
    (world["home"] / "sessions" / "bad.json").write_text("not json at all")
    _mark_live(world, 4242, "session-1", world["repo"])
    assert [s.pid for s in live_sessions(world["home"], world["proc"])] == [4242]


def test_live_sessions_given_a_record_that_is_not_an_object_when_scanned_then_skipped(world):
    (world["home"] / "sessions" / "list.json").write_text("[1, 2, 3]")
    assert live_sessions(world["home"], world["proc"]) == []


def test_live_sessions_given_a_nonnumeric_pid_when_scanned_then_skipped(world):
    (world["home"] / "sessions" / "weird.json").write_text(json.dumps({"pid": "not-a-pid", "name": "session-1"}))
    assert live_sessions(world["home"], world["proc"]) == []


def test_live_sessions_given_no_sessions_dir_when_scanned_then_empty(tmp_path):
    assert live_sessions(tmp_path / "absent", tmp_path / "proc") == []


def test_pid_live_given_a_nonpositive_pid_when_checked_then_not_live(world):
    from ai_cli.session_adopt import _pid_is_live

    assert _pid_is_live(0, world["proc"]) is False


def test_pid_live_given_no_proc_filesystem_when_checked_then_psutil_answers(tmp_path, monkeypatch):
    """macOS and Windows have no /proc, so the check falls through to psutil."""
    import psutil

    from ai_cli.session_adopt import _pid_is_live

    monkeypatch.setattr(psutil, "pid_exists", lambda pid: pid == 777)
    assert _pid_is_live(777, tmp_path / "no-proc-here") is True
    assert _pid_is_live(778, tmp_path / "no-proc-here") is False


def test_pid_live_given_psutil_unavailable_when_checked_then_assumed_live(tmp_path, monkeypatch):
    """Unable to tell must mean "live" — the other guess moves files under a running session."""
    import builtins

    from ai_cli.session_adopt import _pid_is_live

    real_import = builtins.__import__

    def _no_psutil(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("no psutil")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_psutil)
    assert _pid_is_live(778, tmp_path / "no-proc-here") is True


def test_dir_size_given_a_file_that_vanishes_mid_scan_when_measured_then_skipped(tmp_path, monkeypatch):
    from ai_cli.session_adopt import _dir_size

    (tmp_path / "a.txt").write_text("12345")
    real_stat = Path.stat

    def _flaky_stat(self, *args, **kwargs):
        if self.name == "a.txt":
            raise OSError("vanished")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _flaky_stat)
    assert _dir_size(tmp_path) == 0


def test_describe_given_a_transcript_with_no_recorded_cwd_when_described_then_cwd_is_empty(world):
    path = world["src_dir"] / "e0000000-0000-4000-8000-000000000000.jsonl"
    path.write_text(_record(type="user", customTitle="myproject-8") + "\n")
    from ai_cli.session_adopt import describe_candidate

    candidate = describe_candidate(path)
    assert candidate.cwd == "" and "<unrecorded>" in candidate.describe()


def test_describe_given_a_transcript_with_unparseable_lines_when_described_then_they_are_skipped(world):
    path = world["src_dir"] / "e1000000-0000-4000-8000-000000000000.jsonl"
    path.write_text("not json\n\n" + _record(type="user", cwd="/somewhere", customTitle="myproject-8") + "\n")
    from ai_cli.session_adopt import describe_candidate

    assert describe_candidate(path).cwd == "/somewhere"


def test_describe_given_a_missing_file_when_described_then_zeroed_rather_than_raising(tmp_path):
    from ai_cli.session_adopt import describe_candidate

    candidate = describe_candidate(tmp_path / "absent.jsonl")
    assert candidate.size == 0 and candidate.lines == 0 and candidate.mtime == 0.0


def test_retitle_given_unparseable_lines_when_retitled_then_they_are_byte_preserved(world):
    path = world["src_dir"] / "e2000000-0000-4000-8000-000000000000.jsonl"
    path.write_text("not json at all\n" + _record(type="user", customTitle="myproject-8") + "\n")
    retitle_transcript(path, "myproject-8", "myproject-9")
    assert path.read_text().splitlines()[0] == "not json at all"
    assert transcript_title(path) == "myproject-9"


def test_retitle_given_a_write_failure_when_retitled_then_the_temp_file_is_removed(world, monkeypatch):
    path = world["src_dir"] / f"{UUID}.jsonl"
    before = path.read_bytes()

    def _boom(*args, **kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr("ai_cli.session_adopt.os.replace", _boom)
    with pytest.raises(OSError):
        retitle_transcript(path, "myproject-2", "myproject-9")
    assert path.read_bytes() == before
    assert not list(world["src_dir"].glob("*.retitle-tmp"))


def test_merge_tasks_given_a_source_with_no_numeric_task_files_when_merged_then_no_op(world):
    source = world["home"] / "tasks" / "session-11111111"
    source.mkdir(parents=True)
    (source / "notes.txt").write_text("not a task")
    dest = world["home"] / "tasks" / "myproject-2"
    assert merge_task_namespace(source, dest) == []
    assert not dest.exists()


def test_merge_tasks_given_an_unparseable_task_file_when_merged_then_moved_verbatim(world):
    source = world["home"] / "tasks" / "session-11111111"
    source.mkdir(parents=True)
    (source / "1.json").write_text("not json at all")
    dest = _tasks(world["home"], "myproject-2", {"1": "dest one"})
    merge_task_namespace(source, dest)
    assert (dest / "2.json").read_text() == "not json at all"
    assert not (source / "1.json").exists()


def test_merge_tasks_given_the_same_namespace_for_source_and_dest_when_merged_then_no_op(world):
    same = _tasks(world["home"], "myproject-2", {"1": "one"})
    assert merge_task_namespace(same, same) == []
    assert json.loads((same / "1.json").read_text())["subject"] == "one"


def test_adopt_given_an_adoption_when_recorded_cwds_checked_then_they_point_at_the_worktree(
    world, adopt, existing_worktree
):
    result = adopt()
    first = json.loads(result.migration.dest_jsonl.read_text().splitlines()[0])
    assert first["cwd"] == str(existing_worktree)


# ---- worktree handling -------------------------------------------------------


def test_adopt_given_an_existing_worktree_when_adopted_then_it_is_reused_not_recreated(
    world, adopt, existing_worktree, monkeypatch
):
    sentinel = existing_worktree / "uncommitted.txt"
    sentinel.write_text("work in progress\n")

    def _fail(*args, **kwargs):
        raise AssertionError("create_worktree must not be called for an existing worktree")

    monkeypatch.setattr("ai_cli.session.create_worktree", _fail)
    result = adopt()
    assert not result.worktree_created
    assert sentinel.read_text() == "work in progress\n"


def test_adopt_given_a_missing_worktree_when_adopted_then_it_is_created(world, adopt, monkeypatch):
    target = world["repo"] / ".worktrees" / "myproject-2"

    def _create(ai_name):
        path = world["repo"] / ".worktrees" / ai_name
        path.mkdir(parents=True)
        return path

    monkeypatch.setattr("ai_cli.session.create_worktree", _create)
    result = adopt()
    assert result.worktree_created and result.dest_root == target


def test_adopt_given_worktree_creation_fails_when_adopted_then_raises_without_moving(world, adopt, monkeypatch):
    monkeypatch.setattr("ai_cli.session.create_worktree", lambda ai_name: None)
    with pytest.raises(AdoptionError, match="could not create"):
        adopt()
    assert (world["src_dir"] / f"{UUID}.jsonl").is_file()


# ---- CC task namespaces ------------------------------------------------------


def test_namespace_candidates_given_a_uuid_when_derived_then_short_and_full_forms_offered():
    assert task_namespace_candidates(UUID) == [f"session-{UUID[:8]}", UUID]


def test_namespace_candidates_given_no_uuid_when_derived_then_empty():
    assert task_namespace_candidates("") == []


def _tasks(base: Path, ns: str, ids_to_subjects: dict[str, str], blocks=None) -> Path:
    directory = base / "tasks" / ns
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    for task_id, subject in ids_to_subjects.items():
        payload = {"id": task_id, "subject": subject, "status": "pending", "blocks": (blocks or {}).get(task_id, [])}
        (directory / f"{task_id}.json").write_text(json.dumps(payload))
    return directory


def test_merge_tasks_given_colliding_ids_when_merged_then_existing_files_are_not_clobbered(world):
    source = _tasks(world["home"], "session-11111111", {"1": "source one", "2": "source two"})
    dest = _tasks(world["home"], "myproject-2", {"1": "dest one"})
    moves = merge_task_namespace(source, dest)
    assert json.loads((dest / "1.json").read_text())["subject"] == "dest one"
    subjects = {json.loads(p.read_text())["subject"] for p in dest.glob("*.json")}
    assert subjects == {"dest one", "source one", "source two"}
    assert len(moves) == 2 and sum(1 for m in moves if m.renumbered_from) == 1


def test_merge_tasks_given_a_renumbered_task_when_merged_then_its_id_field_matches_its_filename(world):
    source = _tasks(world["home"], "session-11111111", {"1": "source one"})
    dest = _tasks(world["home"], "myproject-2", {"1": "dest one"})
    merge_task_namespace(source, dest)
    moved = json.loads((dest / "2.json").read_text())
    assert moved["subject"] == "source one" and moved["id"] == "2"


def test_merge_tasks_given_references_between_moved_tasks_when_renumbered_then_references_are_remapped(world):
    source = _tasks(world["home"], "session-11111111", {"1": "a", "2": "b"}, blocks={"1": ["2"]})
    dest = _tasks(world["home"], "myproject-2", {"1": "dest one", "2": "dest two"})
    merge_task_namespace(source, dest)
    remapped = next(
        json.loads(p.read_text()) for p in dest.glob("*.json") if json.loads(p.read_text())["subject"] == "a"
    )
    blocked = next(p.stem for p in dest.glob("*.json") if json.loads(p.read_text())["subject"] == "b")
    assert remapped["blocks"] == [blocked]


def test_merge_tasks_given_no_collision_when_merged_then_ids_are_preserved(world):
    source = _tasks(world["home"], "session-11111111", {"3": "keep three"})
    dest = _tasks(world["home"], "myproject-2", {"1": "dest one"})
    moves = merge_task_namespace(source, dest)
    assert (dest / "3.json").is_file()
    assert moves[0].renumbered_from is None


def test_merge_tasks_given_a_missing_source_namespace_when_merged_then_no_op(world):
    dest = world["home"] / "tasks" / "myproject-2"
    assert merge_task_namespace(world["home"] / "tasks" / "nope", dest) == []
    assert not dest.exists()


def test_merge_tasks_given_a_dry_run_when_merged_then_files_stay_put(world):
    source = _tasks(world["home"], "session-11111111", {"1": "source one"})
    dest = _tasks(world["home"], "myproject-2", {"1": "dest one"})
    moves = merge_task_namespace(source, dest, dry_run=True)
    assert len(moves) == 1
    assert (source / "1.json").is_file()
    assert json.loads((dest / "1.json").read_text())["subject"] == "dest one"
    assert not (dest / "2.json").exists()


def test_adopt_given_a_uuid_task_namespace_when_adopted_then_tasks_land_in_the_pinned_namespace(
    world, adopt, existing_worktree
):
    _tasks(world["home"], f"session-{UUID[:8]}", {"1": "unpinned work"})
    result = adopt()
    pinned = world["home"] / "tasks" / "myproject-2"
    assert json.loads((pinned / "1.json").read_text())["subject"] == "unpinned work"
    assert len(result.tasks_moved) == 1


def test_adopt_given_an_explicit_task_namespace_when_adopted_then_that_namespace_is_used(
    world, adopt, existing_worktree
):
    _tasks(world["home"], "some-other-namespace", {"1": "explicit"})
    adopt(task_namespace="some-other-namespace")
    assert json.loads((world["home"] / "tasks" / "myproject-2" / "1.json").read_text())["subject"] == "explicit"


# ---- auto-memory -------------------------------------------------------------


def test_memory_given_a_source_memory_dir_when_adopted_then_copied_not_moved(tmp_path):
    source, dest = tmp_path / "src", tmp_path / "dst"
    (source / "memory").mkdir(parents=True)
    (source / "memory" / "MEMORY.md").write_text("state\n")
    copied, conflicts = adopt_memory(source, dest)
    assert (dest / "memory" / "MEMORY.md").read_text() == "state\n"
    assert (source / "memory" / "MEMORY.md").read_text() == "state\n", "the source root may still have sessions"
    assert copied and not conflicts


def test_memory_given_a_file_already_in_the_destination_when_adopted_then_kept_and_reported(tmp_path):
    source, dest = tmp_path / "src", tmp_path / "dst"
    (source / "memory").mkdir(parents=True)
    (source / "memory" / "MEMORY.md").write_text("incoming\n")
    (dest / "memory").mkdir(parents=True)
    (dest / "memory" / "MEMORY.md").write_text("the worktree's own\n")
    copied, conflicts = adopt_memory(source, dest)
    assert (dest / "memory" / "MEMORY.md").read_text() == "the worktree's own\n"
    assert not copied and len(conflicts) == 1


def test_memory_given_nested_memory_files_when_adopted_then_the_tree_shape_is_kept(tmp_path):
    source, dest = tmp_path / "src", tmp_path / "dst"
    (source / "memory" / "topics").mkdir(parents=True)
    (source / "memory" / "topics" / "a.md").write_text("a\n")
    adopt_memory(source, dest)
    assert (dest / "memory" / "topics" / "a.md").read_text() == "a\n"


def test_memory_given_no_source_memory_when_adopted_then_no_op(tmp_path):
    source, dest = tmp_path / "src", tmp_path / "dst"
    source.mkdir()
    assert adopt_memory(source, dest) == ([], [])
    assert not dest.exists()


def test_memory_given_a_dry_run_when_adopted_then_reported_but_not_written(tmp_path):
    source, dest = tmp_path / "src", tmp_path / "dst"
    (source / "memory").mkdir(parents=True)
    (source / "memory" / "MEMORY.md").write_text("state\n")
    copied, _ = adopt_memory(source, dest, dry_run=True)
    assert len(copied) == 1
    assert not (dest / "memory").exists()


def test_adopt_given_a_session_with_memory_when_adopted_then_memory_is_copied_into_the_worktree(
    world, adopt, existing_worktree
):
    (world["src_dir"] / "memory").mkdir()
    (world["src_dir"] / "memory" / "MEMORY.md").write_text("carried over\n")
    result = adopt()
    dest_memory = cc_project_dir(existing_worktree, world["home"]) / "memory" / "MEMORY.md"
    assert dest_memory.read_text() == "carried over\n"
    assert (world["src_dir"] / "memory" / "MEMORY.md").is_file()
    assert len(result.memory_copied) == 1


# ---- dry run and idempotence -------------------------------------------------


def test_adopt_given_a_dry_run_when_previewed_then_nothing_is_written(world, adopt, existing_worktree):
    _tasks(world["home"], f"session-{UUID[:8]}", {"1": "unpinned work"})
    (world["src_dir"] / "memory").mkdir()
    (world["src_dir"] / "memory" / "MEMORY.md").write_text("state\n")
    result = adopt(dry_run=True)
    assert result.dry_run
    assert (world["src_dir"] / f"{UUID}.jsonl").is_file()
    assert not cc_project_dir(existing_worktree, world["home"]).exists()
    assert (world["home"] / "tasks" / f"session-{UUID[:8]}" / "1.json").is_file()
    assert not (world["home"] / "tasks" / "myproject-2").exists()
    assert result.tasks_moved and result.memory_copied


def test_adopt_given_a_missing_worktree_and_a_dry_run_when_previewed_then_no_worktree_is_created(world, adopt):
    result = adopt(dry_run=True)
    assert result.worktree_created and not result.dest_root.exists()
    assert result.source_lines == 3


def test_adopt_given_an_already_adopted_session_when_rerun_then_it_is_a_no_op(world, adopt, existing_worktree):
    first = adopt()
    dest = first.migration.dest_jsonl
    before = dest.read_bytes()
    second = adopt()
    assert second.already_adopted
    assert second.migration is None and not second.tasks_moved and not second.memory_copied
    assert dest.read_bytes() == before


def test_adopt_given_nothing_to_adopt_and_nothing_adopted_when_run_then_raises(world, adopt, existing_worktree):
    with pytest.raises(AdoptionError, match="no transcript titled"):
        adopt(name="myproject-9")


# ---- bulk mode ---------------------------------------------------------------


def test_titled_sessions_given_several_transcripts_when_listed_then_each_title_once(world):
    _write_transcript(world["src_dir"], OTHER_UUID, "myproject-3", world["repo"])
    assert sorted(titled_sessions(world["repo"], world["home"])) == ["myproject-2", "myproject-3"]


def test_adopt_all_given_one_collision_when_run_then_the_batch_continues(world, monkeypatch):
    _write_transcript(world["src_dir"], OTHER_UUID, "myproject-3", world["repo"])
    # myproject-2 collides with a transcript already in its worktree.
    wt2 = world["repo"] / ".worktrees" / "myproject-2"
    wt2.mkdir(parents=True)
    _write_transcript(cc_project_dir(wt2, world["home"]), "33333333-2222-4333-8444-555555555555", "myproject-2", wt2)
    (world["repo"] / ".worktrees" / "myproject-3").mkdir(parents=True)

    outcomes = dict(adopt_all(world["repo"], claude_home=world["home"], proc_dir=world["proc"]))
    assert isinstance(outcomes["myproject-2"], TitleCollision)
    assert outcomes["myproject-3"].resolved is not None, "the rest of the batch must still be adopted"


def test_adopt_all_given_no_titled_sessions_when_run_then_empty(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert adopt_all(repo, claude_home=tmp_path / "home", proc_dir=tmp_path / "proc") == []


# ---- CLI surface -------------------------------------------------------------


@pytest.fixture
def cli_world(world, existing_worktree, monkeypatch):
    """Bind the CLI's implicit home/proc/repo lookups to the fake world."""
    import ai_cli.session_adopt as module

    real_adopt_session = module.adopt_session
    real_adopt_all = module.adopt_all

    def _adopt_session(repo_root, name, **kw):
        kw.setdefault("claude_home", world["home"])
        kw.setdefault("proc_dir", world["proc"])
        return real_adopt_session(world["repo"], name, **kw)

    def _adopt_all(repo_root, **kw):
        kw.setdefault("claude_home", world["home"])
        kw.setdefault("proc_dir", world["proc"])
        return real_adopt_all(world["repo"], **kw)

    monkeypatch.setattr(module, "adopt_session", _adopt_session)
    monkeypatch.setattr(module, "adopt_all", _adopt_all)
    monkeypatch.setattr("ai_cli.session.detect_repo_root", lambda: world["repo"])
    return world


def test_cli_given_a_dry_run_when_invoked_then_reports_the_plan_and_exits_zero(cli_world):
    code, out, err = run_cli(["ai", "session-adopt", "myproject-2", "-n"])
    assert code == 0
    assert "Would adopt myproject-2" in out


def test_cli_given_an_adoption_when_confirmed_then_reports_the_resolve_probe(cli_world):
    code, out, err = run_cli(["ai", "session-adopt", "myproject-2", "-y"])
    assert code == 0
    assert "resolve probe" in out and f"{UUID}.jsonl" in out


def test_cli_given_a_collision_when_invoked_then_exits_nonzero_printing_both_candidates(cli_world):
    wt = cli_world["repo"] / ".worktrees" / "myproject-2"
    _write_transcript(cc_project_dir(wt, cli_world["home"]), OTHER_UUID, "myproject-2", wt, extra_lines=40)
    code, out, err = run_cli(["ai", "session-adopt", "myproject-2"])
    assert code == 1
    assert UUID in err and OTHER_UUID in err
    assert "MB" in err and "lines=" in err and "cwd=" in err
    assert "-c retitle" in err


def test_cli_given_a_collision_and_yes_when_invoked_then_the_gate_still_holds(cli_world):
    wt = cli_world["repo"] / ".worktrees" / "myproject-2"
    other = _write_transcript(cc_project_dir(wt, cli_world["home"]), OTHER_UUID, "myproject-2", wt)
    before = (cli_world["src_dir"] / f"{UUID}.jsonl").read_bytes()
    code, out, err = run_cli(["ai", "session-adopt", "myproject-2", "-y"])
    assert code == 1, "-y must not bypass the duplicate-title gate"
    assert "-y/--yes does not cover it" in err
    assert (cli_world["src_dir"] / f"{UUID}.jsonl").read_bytes() == before
    assert other.is_file()


def test_cli_given_retitle_without_a_title_or_index_when_invoked_then_refused(cli_world):
    code, out, err = run_cli(["ai", "session-adopt", "myproject-2", "-c", "retitle", "-y"])
    assert code == 1
    assert "human-confirmed title" in err


def test_cli_given_retitle_with_a_confirmed_index_when_invoked_then_the_new_title_is_derived(cli_world):
    wt = cli_world["repo"] / ".worktrees" / "myproject-2"
    _write_transcript(cc_project_dir(wt, cli_world["home"]), OTHER_UUID, "myproject-2", wt)
    (cli_world["repo"] / ".worktrees" / "myproject-4").mkdir()
    code, out, err = run_cli(["ai", "session-adopt", "myproject-2", "-c", "retitle", "-I", "4", "-y"])
    assert code == 0, err
    assert "retitled 'myproject-2' -> 'myproject-4'" in out
    assert probe_resolves(cli_world["repo"] / ".worktrees" / "myproject-4", "myproject-4", cli_world["home"])


def test_cli_given_no_name_and_no_all_flag_when_invoked_then_refused(cli_world):
    code, out, err = run_cli(["ai", "session-adopt"])
    assert code == 1
    assert "NAME is required" in err


def test_cli_given_a_live_session_when_invoked_then_exits_nonzero(cli_world):
    _mark_live(cli_world, 4242, "myproject-2", cli_world["repo"])
    code, out, err = run_cli(["ai", "session-adopt", "myproject-2", "-y"])
    assert code == 1
    assert "still" in err and "running" in err


def test_cli_given_bulk_mode_when_invoked_then_each_session_is_reported(cli_world):
    _write_transcript(cli_world["src_dir"], OTHER_UUID, "myproject-3", cli_world["repo"])
    (cli_world["repo"] / ".worktrees" / "myproject-3").mkdir()
    code, out, err = run_cli(["ai", "session-adopt", "-a", "-n"])
    assert code == 0
    assert "myproject-2" in out and "myproject-3" in out
    assert "2 adopted, 0 skipped" in out


def test_cli_given_bulk_mode_with_a_collision_when_invoked_then_the_rest_still_adopt(cli_world):
    _write_transcript(cli_world["src_dir"], OTHER_UUID, "myproject-3", cli_world["repo"])
    (cli_world["repo"] / ".worktrees" / "myproject-3").mkdir()
    wt = cli_world["repo"] / ".worktrees" / "myproject-2"
    _write_transcript(cc_project_dir(wt, cli_world["home"]), "33333333-2222-4333-8444-555555555555", "myproject-2", wt)
    code, out, err = run_cli(["ai", "session-adopt", "-a", "-y"])
    assert code == 1
    assert "Paused on myproject-2" in err
    assert "Adopted myproject-3" in out
    assert "1 adopted, 1 skipped" in out


def test_cli_given_bulk_mode_with_nothing_titled_when_invoked_then_reports_nothing_to_adopt(cli_world, monkeypatch):
    monkeypatch.setattr("ai_cli.session_adopt.titled_sessions", lambda *a, **k: [])
    code, out, err = run_cli(["ai", "session-adopt", "-a"])
    assert code == 0
    assert "No titled sessions" in out


def test_cli_given_a_declined_confirmation_when_prompted_then_nothing_is_adopted(cli_world, monkeypatch):
    monkeypatch.setattr("click.confirm", lambda *a, **k: False)
    code, out, err = run_cli(["ai", "session-adopt", "myproject-2"])
    assert code == 1
    assert "Aborted" in err
    assert (cli_world["src_dir"] / f"{UUID}.jsonl").is_file()


def test_cli_given_an_accepted_confirmation_when_prompted_then_the_session_is_adopted(cli_world, monkeypatch):
    monkeypatch.setattr("click.confirm", lambda *a, **k: True)
    code, out, err = run_cli(["ai", "session-adopt", "myproject-2"])
    assert code == 0
    assert "Adopted myproject-2" in out


def test_cli_given_an_index_on_an_unindexed_name_when_invoked_then_refused(cli_world):
    code, out, err = run_cli(["ai", "session-adopt", "noindex", "-I", "4", "-y"])
    assert code == 1
    assert "cannot derive a prefix" in err


def test_cli_given_every_option_when_help_requested_then_each_has_a_short_and_long_form():
    """CLI convention: no long-only flags (CLAUDE.md)."""
    from ai_cli.main import cmd_session_adopt

    for param in cmd_session_adopt.params:
        if not isinstance(param, __import__("click").Option):
            continue
        shorts = [o for o in param.opts if len(o) == 2 and o.startswith("-") and not o.startswith("--")]
        longs = [o for o in param.opts if o.startswith("--")]
        assert shorts and longs, f"{param.name} needs both a short and a long form, has {param.opts}"
