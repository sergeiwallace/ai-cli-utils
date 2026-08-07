"""Tests for ai_cli.session_audit — the fleet-wide survey of titled CC sessions.

Derived from the acceptance criteria, not from the implementation:

* AC-1 the survey reports title, path, line count, owning repo, slot correctness,
  liveness and whether ``ai c`` resolves it;
* AC-2 discovery covers repo roots, ``.worktrees/<name>`` and the agent worktrees
  under ``.claude/worktrees/<id>``, with no caller-supplied path;
* AC-3 a title claimed by more than one transcript is a distinct reported
  condition, surfaced before any adoption;
* AC-4 adoption delegates to ``session_adopt`` rather than reimplementing it;
* AC-5 a dry run writes nothing — asserted against a filesystem snapshot;
* AC-6 the scope options narrow by repo and by title;
* AC-7 a live or colliding session is a reported skip and the batch continues;
* AC-8 both refusals still fire through the new path, and ``-y`` does not cover
  the collision gate;
* AC-9 every CLI option has a short and a long form;
* AC-10 zero titled sessions and zero repos are both handled without raising.

Everything writes inside ``tmp_path``: the fake ``~/.claude`` home, the fake repos
and their worktrees. No test touches the real user state or this repository's tree.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from conftest import run_cli

from ai_cli.cc_migrate import cc_project_dir
from ai_cli.session_audit import (
    AGENT_WORKTREE,
    REPO_ROOT,
    REPO_SUBDIR,
    SESSION_WORKTREE,
    UNKNOWN,
    WORKTREES,
    adopt_ready,
    owning_repo,
    survey,
    triage,
)

UUID_A = "aaaaaaaa-2222-4333-8444-555555555555"
UUID_B = "bbbbbbbb-2222-4333-8444-555555555555"
UUID_C = "cccccccc-2222-4333-8444-555555555555"


def _record(**kw) -> str:
    return json.dumps(kw, separators=(",", ":"))


def _write_transcript(home: Path, uuid: str, title: str | None, cwd: Path) -> Path:
    """Write a transcript into the project dir the given cwd slugifies to.

    The recorded ``cwd`` is created too: a session that ran there had a directory
    to run in, and ``find_title_candidates`` only scans worktree directories that
    exist — so omitting it silently hides a duplicate title from the collision
    gate, which is the opposite of what these tests need to prove.
    """
    cwd.mkdir(parents=True, exist_ok=True)
    project_dir = cc_project_dir(cwd, home)
    project_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    first = {"type": "user", "sessionId": uuid, "cwd": str(cwd)}
    if title is not None:
        first["customTitle"] = title
    lines = [
        _record(**first),
        _record(type="assistant", sessionId=uuid, cwd=str(cwd)),
    ]
    path = project_dir / f"{uuid}.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return path


def _write_transcript_at(
    home: Path, uuid: str, title: str, project_cwd: Path, recorded_cwd: Path, records: int = 6
) -> Path:
    """Place a transcript in ``project_cwd``'s project dir while its records claim ``recorded_cwd``.

    This is the real post-adoption shape: the transcript file has been moved into
    the slot's project directory (which is what makes ``ai c`` resolve it), but the
    bulk of its records still carry the working directory the session originally
    ran in — an adoption rewrites the cwds it needs to and legitimately leaves
    historical ones alone. Observed on real data at a ratio of 5062 old to 140
    rewritten.
    """
    project_cwd.mkdir(parents=True, exist_ok=True)
    project_dir = cc_project_dir(project_cwd, home)
    project_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lines = [_record(type="user", sessionId=uuid, cwd=str(recorded_cwd), customTitle=title)]
    lines += [_record(type="user", sessionId=uuid, cwd=str(recorded_cwd), n=i) for i in range(records)]
    path = project_dir / f"{uuid}.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return path


def _make_repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / "projects" / name
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    (repo / WORKTREES).mkdir(parents=True, exist_ok=True)
    return repo


def _snapshot(*roots: Path) -> dict[str, str]:
    """Path -> content hash for every file under each root, for dry-run proof.

    Hashes rather than mtimes: a write that happens to preserve the mtime would
    still change the content, and a hash catches it either way.
    """
    state: dict[str, str] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file():
                state[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                state[str(path)] = "<dir>"
    return state


@pytest.fixture
def fleet(tmp_path):
    """A fake ~/.claude plus two repos holding sessions in every location that occurs.

    * ``myproject-2`` ran at the repo root — the ordinary mistake to correct.
    * ``myproject-5`` ran in a Claude Code AGENT worktree under
      ``.claude/worktrees/<id>``, the location that was wrongly recorded as the
      repo root in the case that motivated this command.
    * ``myapp-1`` already sits in its own ``.worktrees/myapp-1`` slot.
    * one untitled transcript, which must never be reported.
    """
    home = tmp_path / "claude-home"
    (home / "sessions").mkdir(parents=True)
    proc = tmp_path / "proc"
    proc.mkdir()

    myproject = _make_repo(tmp_path, "myproject")
    myapp = _make_repo(tmp_path, "myapp")

    agent_dir = myproject / ".claude" / "worktrees" / "agent-1234"
    agent_dir.mkdir(parents=True)
    adopted_slot = myapp / ".worktrees" / "myapp-1"
    adopted_slot.mkdir(parents=True)

    root_session = _write_transcript(home, UUID_A, "myproject-2", myproject)
    agent_session = _write_transcript(home, UUID_B, "myproject-5", agent_dir)
    slot_session = _write_transcript(home, UUID_C, "myapp-1", adopted_slot)
    _write_transcript(home, "dddddddd-2222-4333-8444-555555555555", None, myproject)

    return {
        "home": home,
        "proc": proc,
        "tmp": tmp_path,
        "myproject": myproject,
        "myapp": myapp,
        "agent_dir": agent_dir,
        "adopted_slot": adopted_slot,
        "root_session": root_session,
        "agent_session": agent_session,
        "slot_session": slot_session,
    }


@pytest.fixture(autouse=True)
def _no_real_worktrees(tmp_path, monkeypatch):
    """Create session worktrees inside the FAKE repos under tmp_path, never the real one.

    ``session.create_worktree`` takes only a name and derives the repo root from
    the *process's* cwd, so an adoption that needs a worktree would create one in
    this actual repository — a test writing real state. Observed for real: a
    mutation-testing run left a registered ``.worktrees/myproject-2`` behind in
    the shared checkout. Binding it here means no test can reach the real tree
    even if the code under test changes, and the fake stays faithful enough that
    the dry-run snapshot, not a process guard, is what catches a dry run that
    writes.
    """

    def _fake_create_worktree(ai_name):
        repo = tmp_path / "projects" / ai_name.rsplit("-", 1)[0]
        if not repo.is_dir():
            raise AssertionError(f"unexpected worktree request for {ai_name!r} — no fake repo at {repo}")
        path = repo / WORKTREES / ai_name
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    monkeypatch.setattr("ai_cli.session.create_worktree", _fake_create_worktree)


@pytest.fixture
def audit(fleet):
    """Call ``survey`` with this fleet's fake home and /proc pre-bound."""

    def _audit(**kw):
        kw.setdefault("claude_home", fleet["home"])
        kw.setdefault("proc_dir", fleet["proc"])
        return survey(**kw)

    return _audit


def _mark_live(fleet, pid: int, name: str, cwd: Path, session_id: str = "") -> None:
    (fleet["proc"] / str(pid)).mkdir()
    (fleet["home"] / "sessions" / f"{pid}.json").write_text(
        json.dumps({"pid": pid, "name": name, "cwd": str(cwd), "sessionId": session_id})
    )


def _by_title(report):
    return {record.title: record for record in report.sessions}


# ---- AC-1: the survey reports every field a decision needs -------------------


def test_survey_given_titled_sessions_when_surveyed_then_each_field_is_reported(audit, fleet):
    """AC-1: title, path, lines, repo, slot correctness, liveness, resolvability."""
    record = _by_title(audit())["myproject-2"]
    assert record.transcript == fleet["root_session"]
    assert record.lines == 2
    assert record.size > 0
    assert record.repo_root == fleet["myproject"]
    assert record.location == REPO_ROOT
    assert record.in_correct_slot is False, "it ran at the repo root, not in .worktrees/myproject-2"
    assert record.live is False
    assert record.resolves is None, "`ai c 2` cannot resolve it from the worktree slot yet"
    assert record.index == 2
    assert record.slug_matches is True


def test_survey_given_an_untitled_transcript_when_surveyed_then_it_is_not_reported(audit):
    """AC-1: only *titled* sessions are sessions for this purpose."""
    report = audit()
    assert None not in _by_title(report)
    assert len(report.sessions) == 3
    assert report.scanned_transcripts == 4, "the untitled one is still scanned, just not reported"


def test_survey_given_a_session_already_in_its_slot_when_surveyed_then_reported_as_resolvable(audit, fleet):
    """AC-1: the report distinguishes an adopted session from an unadopted one."""
    record = _by_title(audit())["myapp-1"]
    assert record.location == SESSION_WORKTREE
    assert record.in_correct_slot is True
    assert record.resolves == fleet["slot_session"]


def test_survey_given_a_live_session_when_surveyed_then_its_pid_is_reported(audit, fleet):
    """AC-1: liveness is reported per session, with the pid."""
    _mark_live(fleet, 4242, "myproject-2", fleet["myproject"])
    record = _by_title(audit())["myproject-2"]
    assert record.live is True and record.live_pid == 4242


def test_survey_given_a_moved_transcript_when_surveyed_then_the_slug_mismatch_is_reported(fleet, audit):
    """AC-1: a transcript whose cwd no longer slugifies to its directory is flagged."""
    stray = cc_project_dir(fleet["tmp"] / "elsewhere", fleet["home"])
    stray.mkdir(parents=True)
    moved = stray / f"{UUID_A}.jsonl"
    moved.write_text(fleet["root_session"].read_text())
    record = next(r for r in audit().sessions if r.transcript == moved)
    assert record.slug_matches is False


def test_describe_given_a_record_when_rendered_then_the_flags_appear(audit, fleet):
    """AC-1: the human-readable line carries the same facts as the record."""
    _mark_live(fleet, 4343, "myproject-2", fleet["myproject"])
    text = _by_title(audit())["myproject-2"].describe()
    assert "myproject-2" in text
    assert "LIVE pid=4343" in text
    assert "not-in-slot" in text and "NOT-resolvable" in text
    assert REPO_ROOT in text


def test_describe_given_a_moved_transcript_when_rendered_then_the_slug_mismatch_is_flagged(fleet, audit):
    """AC-1: the slug mismatch reaches the rendered line, not just the record."""
    stray = cc_project_dir(fleet["tmp"] / "elsewhere", fleet["home"])
    stray.mkdir(parents=True)
    moved = stray / f"{UUID_A}.jsonl"
    moved.write_text(fleet["root_session"].read_text())
    record = next(r for r in audit().sessions if r.transcript == moved)
    assert "slug-mismatch" in record.describe()


def test_slot_given_a_session_with_no_owning_repo_when_asked_then_none(fleet, audit, tmp_path):
    """AC-1: a repo-less session has no worktree slot to report."""
    loose = tmp_path / "loose" / "scratch"
    _write_transcript(fleet["home"], UUID_B[:-1] + "4", "loose-2", loose)
    record = next(r for r in audit().sessions if r.title == "loose-2")
    assert record.repo_root is None
    assert record.slot is None
    assert "<none>" in record.describe()


# ---- AC-2: discovery covers every location a session actually occurs ---------


def test_survey_given_a_session_in_an_agent_worktree_when_surveyed_then_attributed_to_the_owning_repo(audit, fleet):
    """AC-2: motivating case 1 — found and attributed with NO caller-supplied path."""
    record = _by_title(audit())["myproject-5"]
    assert record.location == AGENT_WORKTREE
    assert record.repo_root == fleet["myproject"], "an agent worktree belongs to the repo above .claude/"
    assert record.cwd == str(fleet["agent_dir"])
    assert record.transcript == fleet["agent_session"]


def test_survey_given_all_three_locations_when_surveyed_then_each_is_classified(audit, fleet):
    """AC-2: repo root, session worktree and agent worktree are all discovered."""
    found = {r.title: r.location for r in audit().sessions}
    assert found == {
        "myproject-2": REPO_ROOT,
        "myproject-5": AGENT_WORKTREE,
        "myapp-1": SESSION_WORKTREE,
    }


def test_owning_repo_given_a_deleted_worktree_path_when_mapped_then_still_attributed(tmp_path):
    """AC-2: path shape decides, so a cleaned-up worktree is still attributed."""
    gone = tmp_path / "projects" / "myproject" / ".worktrees" / "myproject-9"
    repo, location = owning_repo(gone)
    assert (repo, location) == (tmp_path / "projects" / "myproject", SESSION_WORKTREE)


def test_owning_repo_given_a_subdirectory_of_a_repo_when_mapped_then_repo_subdir(tmp_path):
    """AC-2: a session run in a repo subdirectory is attributed to the repo."""
    repo = _make_repo(tmp_path, "myproject")
    found, location = owning_repo(repo / "src" / "pkg")
    assert (found, location) == (repo, REPO_SUBDIR)


def test_owning_repo_given_a_path_in_no_repo_when_mapped_then_unknown(tmp_path):
    """AC-2 / AC-10: a session outside any repo is reported, not crashed on."""
    loose = tmp_path / "not-a-repo" / "scratch"
    loose.mkdir(parents=True)
    assert owning_repo(loose) == (None, UNKNOWN)


def test_owning_repo_given_a_git_file_worktree_when_mapped_then_recognised_as_a_root(tmp_path):
    """AC-2: in a git worktree ``.git`` is a FILE, so is_dir() would miss the root."""
    repo = tmp_path / "myproject"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n")
    assert owning_repo(repo) == (repo, REPO_ROOT)


# ---- AC-3: collisions are a distinct condition, reported up front ------------


def test_survey_given_two_transcripts_claiming_one_title_when_surveyed_then_reported_as_a_collision(fleet, audit):
    """AC-3: the collision is named, with every claimant listed."""
    duplicate = _write_transcript(
        fleet["home"], UUID_C, "myproject-2", fleet["myproject"] / ".worktrees" / "myproject-2"
    )
    report = audit()
    assert "myproject-2" in report.collisions
    claimants = {r.transcript for r in report.collisions["myproject-2"]}
    assert claimants == {fleet["root_session"], duplicate}
    assert all(r.collides for r in report.collisions["myproject-2"])


def test_survey_given_no_duplicates_when_surveyed_then_no_collisions_reported(audit):
    """AC-3: the negative control — a clean fleet reports zero collisions."""
    report = audit()
    assert report.collisions == {}
    assert all(record.collides is False for record in report.sessions)


def test_triage_given_a_collision_when_triaged_then_skipped_before_any_adoption(fleet, audit):
    """AC-3: the collision is classified as un-adoptable before the adopter runs."""
    _write_transcript(fleet["home"], UUID_C, "myproject-2", fleet["myproject"] / ".worktrees" / "myproject-2")
    ready, skipped = triage(audit())
    assert "myproject-2" not in {r.title for r in ready}
    reason = next(reason for record, reason in skipped if record.title == "myproject-2")
    assert "collision" in reason


def test_survey_given_a_cross_repo_duplicate_title_when_surveyed_then_still_a_collision(fleet, audit):
    """AC-3: a title is a worktree name and an `ai c` argument, so duplicates count fleet-wide."""
    _write_transcript(fleet["home"], UUID_B[:-1] + "9", "myapp-1", fleet["myproject"])
    report = audit()
    assert len(report.collisions["myapp-1"]) == 2
    ready, _ = triage(report)
    assert "myapp-1" not in {r.title for r in ready}


# ---- AC-4: adoption delegates to the shipped module -------------------------


def test_adopt_ready_given_an_adoptable_session_when_adopted_then_session_adopt_is_called(fleet, audit, monkeypatch):
    """AC-4: the audit does not reimplement adoption — it calls adopt_session."""
    import ai_cli.session_audit as module

    calls = []

    def _spy(repo_root, name, **kw):
        calls.append((repo_root, name, kw.get("source_root")))
        raise AssertionError("stop after recording the delegation")

    monkeypatch.setattr(module, "adopt_session", _spy)
    with pytest.raises(AssertionError):
        adopt_ready(audit(), claude_home=fleet["home"], proc_dir=fleet["proc"])
    assert calls, "adopt_ready must delegate to session_adopt.adopt_session"
    repo_root, name, source_root = calls[0]
    assert repo_root in (fleet["myproject"], fleet["myapp"])
    assert source_root is not None, "the recorded cwd must be passed as the source root"


def test_adopt_ready_given_an_agent_worktree_session_when_adopted_then_it_resolves(fleet, audit, monkeypatch):
    """AC-2 + AC-4: motivating case 1 adopts end to end with no path supplied."""
    (fleet["myproject"] / ".worktrees" / "myproject-5").mkdir()
    report = survey(claude_home=fleet["home"], proc_dir=fleet["proc"], title="myproject-5")
    outcomes, skipped = adopt_ready(report, claude_home=fleet["home"], proc_dir=fleet["proc"])
    assert skipped == []
    (record, result) = outcomes[0]
    assert not isinstance(result, Exception), result
    assert result.resolved is not None, "`ai c 5` must resolve the adopted transcript"
    assert result.resolved.parent == cc_project_dir(fleet["myproject"] / ".worktrees" / "myproject-5", fleet["home"])


def test_triage_given_an_already_adopted_session_when_triaged_then_skipped_as_done(audit):
    """AC-4: a session already in its slot is not re-adopted."""
    _, skipped = triage(audit())
    reason = next(reason for record, reason in skipped if record.title == "myapp-1")
    assert "already adopted" in reason


def test_triage_given_a_slot_resident_session_whose_records_carry_the_old_cwd_when_triaged_then_already_adopted(
    fleet, audit
):
    """Adoptedness is decided by WHERE THE TRANSCRIPT SITS, not by what a cwd says.

    An adoption moves the transcript into the slot's project directory — that is
    what makes ``ai c <n>`` resolve it — but it does not rewrite every historical
    cwd in the file, and it should not. Deciding from the recorded cwd therefore
    reports a fully working session as needing adoption, which is the exact wrong
    answer: re-adopting it is at best a no-op and at worst disturbs a session that
    currently resumes correctly.
    """
    slot = fleet["myproject"] / WORKTREES / "myproject-7"
    adopted = _write_transcript_at(
        fleet["home"],
        "77777777-2222-4333-8444-555555555555",
        "myproject-7",
        project_cwd=slot,
        recorded_cwd=fleet["myproject"],  # the pre-adopt location, still in most records
    )

    record = next(r for r in audit().sessions if r.title == "myproject-7")

    assert record.transcript == adopted
    assert record.transcript.parent == cc_project_dir(slot, fleet["home"])
    assert record.resolves == adopted, "`ai c 7` already resolves it — it is adopted"
    assert record.cwd == str(fleet["myproject"]), "its records still point at the old location"
    assert record.in_correct_slot is True, "slot residency is a fact about the file, not about a cwd field"

    ready, skipped = triage(audit())
    assert "myproject-7" not in {r.title for r in ready}, "a working session must not be offered for re-adoption"
    reason = next(reason for r, reason in skipped if r.title == "myproject-7")
    assert "already adopted" in reason


def test_triage_given_an_unadopted_session_when_triaged_then_still_adoptable(fleet, audit):
    """The other direction: a transcript in the repo-root project dir IS adoptable.

    Paired with the test above so the discriminator is visible: both sessions
    record the repo root as their cwd, and the only difference is which project
    directory the transcript file lives in. If a fix made adoptedness unconditional
    this test goes red.
    """
    record = next(r for r in audit().sessions if r.title == "myproject-2")

    assert record.transcript.parent == cc_project_dir(fleet["myproject"], fleet["home"])
    assert record.resolves is None, "`ai c 2` cannot resolve it from the slot"
    assert record.in_correct_slot is False

    ready, _ = triage(audit())
    assert "myproject-2" in {r.title for r in ready}, "a genuinely unadopted session must still be adoptable"


def test_triage_given_a_title_without_an_index_when_triaged_then_skipped(fleet, audit):
    """AC-4: `ai c <n>` addresses sessions by index, so an unindexed title is not adoptable."""
    _write_transcript(fleet["home"], UUID_B[:-1] + "7", "noindex", fleet["myproject"] / "sub")
    _, skipped = triage(audit())
    reason = next(reason for record, reason in skipped if record.title == "noindex")
    assert "not <prefix>-<index>" in reason


def test_triage_given_a_transcript_with_no_recorded_cwd_when_triaged_then_skipped(fleet, audit):
    """AC-4: without a cwd there is no way to know which directory to adopt from."""
    project_dir = cc_project_dir(fleet["myproject"], fleet["home"])
    (project_dir / "eeeeeeee-2222-4333-8444-555555555555.jsonl").write_text(
        _record(type="user", customTitle="myproject-8") + "\n"
    )
    _, skipped = triage(audit())
    reason = next(reason for record, reason in skipped if record.title == "myproject-8")
    assert "no cwd" in reason


def test_triage_given_a_session_outside_any_repo_when_triaged_then_skipped(fleet, audit, tmp_path):
    """AC-4: nothing to adopt a repo-less session into."""
    loose = tmp_path / "loose" / "scratch"
    loose.mkdir(parents=True)
    _write_transcript(fleet["home"], UUID_B[:-1] + "5", "loose-1", loose)
    _, skipped = triage(audit())
    reason = next(reason for record, reason in skipped if record.title == "loose-1")
    assert "no owning repo" in reason


# ---- AC-5: the dry run writes nothing --------------------------------------


def test_adopt_ready_given_a_dry_run_when_run_then_the_filesystem_is_byte_identical(fleet, audit):
    """AC-5: proved by hashing every file before and after, not by reading output.

    A dry run that wrongly wrote would print exactly the same plan, so the printed
    output cannot distinguish the two. The snapshot can.
    """
    (fleet["myproject"] / ".worktrees" / "myproject-5").mkdir()
    roots = (fleet["home"], fleet["myproject"], fleet["myapp"])
    before = _snapshot(*roots)

    outcomes, _ = adopt_ready(audit(), dry_run=True, claude_home=fleet["home"], proc_dir=fleet["proc"])

    # The filesystem comparison comes FIRST: it is the assertion that actually
    # distinguishes a dry run from a real one, and checking the reported flag
    # before it would mask a genuine write behind a flag-shaped failure.
    after = _snapshot(*roots)
    assert after == before, "a dry run must not create, remove or modify any file"
    assert outcomes, "the dry run must still have planned something"
    assert all(getattr(result, "dry_run", False) for _, result in outcomes)


def test_adopt_ready_given_a_real_run_when_run_then_the_filesystem_does_change(fleet, audit):
    """AC-5 negative control: the snapshot probe can fail, so its silence means something."""
    (fleet["myproject"] / ".worktrees" / "myproject-5").mkdir()
    roots = (fleet["home"], fleet["myproject"], fleet["myapp"])
    before = _snapshot(*roots)

    adopt_ready(audit(), dry_run=False, claude_home=fleet["home"], proc_dir=fleet["proc"])

    assert _snapshot(*roots) != before, "if a real adoption also looks unchanged, the probe is broken"


# ---- AC-6: scope options ----------------------------------------------------


def test_survey_given_a_repo_filter_when_surveyed_then_only_that_repos_sessions_are_reported(audit, fleet):
    """AC-6: usable narrowly, on one repo."""
    report = audit(repo=fleet["myapp"])
    assert [r.title for r in report.sessions] == ["myapp-1"]


def test_survey_given_a_title_filter_when_surveyed_then_only_that_session_is_reported(audit):
    """AC-6: usable narrowly, on one title."""
    report = audit(title="myproject-5")
    assert [r.title for r in report.sessions] == ["myproject-5"]


def test_survey_given_a_filter_hiding_one_claimant_when_surveyed_then_the_collision_still_shows(fleet, audit):
    """AC-3 + AC-6: narrowing must not make a colliding title look safe."""
    _write_transcript(fleet["home"], UUID_C, "myproject-2", fleet["myproject"] / ".worktrees" / "myproject-2")
    report = audit(title="myproject-2", repo=fleet["myproject"])
    assert len(report.collisions["myproject-2"]) == 2
    assert all(record.collides for record in report.sessions)


def test_survey_given_a_repo_filter_matching_nothing_when_surveyed_then_empty(audit, tmp_path):
    """AC-6 + AC-10: a filter that matches nothing is empty, not an error."""
    report = audit(repo=tmp_path / "projects" / "absent")
    assert report.sessions == [] and report.collisions == {}


# ---- AC-7: one refusal never aborts the batch -------------------------------


def test_adopt_ready_given_a_live_session_when_run_then_it_is_skipped_and_the_batch_continues(fleet, audit):
    """AC-7: the live one is reported with its reason; the rest still adopt."""
    _mark_live(fleet, 5151, "myproject-2", fleet["myproject"], session_id=UUID_A)
    (fleet["myproject"] / ".worktrees" / "myproject-5").mkdir()

    outcomes, skipped = adopt_ready(audit(), claude_home=fleet["home"], proc_dir=fleet["proc"])

    reason = next(reason for record, reason in skipped if record.title == "myproject-2")
    assert "live session (pid 5151)" in reason
    adopted = {record.title for record, result in outcomes if not isinstance(result, Exception)}
    assert "myproject-5" in adopted, "one refusal must not abort the batch"
    assert "myproject-2" not in adopted


def test_adopt_ready_given_a_collision_when_run_then_it_is_skipped_and_the_batch_continues(fleet, audit):
    """AC-7: same for a collision — reported, skipped, batch proceeds."""
    _write_transcript(fleet["home"], UUID_C, "myproject-2", fleet["myproject"] / ".worktrees" / "myproject-2")
    (fleet["myproject"] / ".worktrees" / "myproject-5").mkdir()

    outcomes, skipped = adopt_ready(audit(), claude_home=fleet["home"], proc_dir=fleet["proc"])

    assert any("collision" in reason for record, reason in skipped if record.title == "myproject-2")
    adopted = {record.title for record, result in outcomes if not isinstance(result, Exception)}
    assert "myproject-5" in adopted


def test_adopt_ready_given_an_adoption_error_when_run_then_recorded_and_the_batch_continues(fleet, audit, monkeypatch):
    """AC-7: an unexpected refusal from the adopter is recorded against its session."""
    import ai_cli.session_audit as module
    from ai_cli.session_adopt import AdoptionError
    from ai_cli.session_adopt import adopt_session as real_adopt

    def _flaky(repo_root, name, **kw):
        if name == "myproject-2":
            raise AdoptionError("simulated refusal")
        return real_adopt(repo_root, name, **kw)

    monkeypatch.setattr(module, "adopt_session", _flaky)
    (fleet["myproject"] / ".worktrees" / "myproject-5").mkdir()

    outcomes, _ = adopt_ready(audit(), claude_home=fleet["home"], proc_dir=fleet["proc"])

    results = {record.title: result for record, result in outcomes}
    assert isinstance(results["myproject-2"], AdoptionError)
    assert results["myproject-5"].resolved is not None


# ---- AC-8: both gates remain in force through the new path ------------------


def test_adopt_ready_given_a_live_session_bypassing_triage_when_adopted_then_the_adopter_refuses(fleet, audit):
    """AC-8: the live-session gate is the adopter's, and it still fires here.

    Triage is asked for the *unfiltered* liveness view and then deliberately
    bypassed, so what is proved is the adopter's own refusal rather than triage's
    classification. Without the gate this would adopt the live session.
    """
    from ai_cli.session_adopt import LiveSessionError

    (fleet["myproject"] / ".worktrees" / "myproject-5").mkdir()
    _mark_live(fleet, 5252, "myproject-5", fleet["agent_dir"], session_id=UUID_B)
    report = survey(claude_home=fleet["home"], proc_dir=fleet["proc"], title="myproject-5")
    record = report.sessions[0]
    record.live_pid = None  # hide it from triage; the adopter must still refuse

    outcomes, skipped = adopt_ready(report, claude_home=fleet["home"], proc_dir=fleet["proc"])

    assert skipped == [], "triage was deliberately blinded, so the refusal must come from the adopter"
    assert isinstance(outcomes[0][1], LiveSessionError)
    assert fleet["agent_session"].is_file(), "the live session's transcript must not have moved"


def test_adopt_ready_given_a_collision_bypassing_triage_when_adopted_then_the_adopter_refuses(fleet, audit):
    """AC-8: the unconditional collision gate still fires when triage is bypassed."""
    from ai_cli.session_adopt import TitleCollision

    duplicate = _write_transcript(
        fleet["home"], UUID_C, "myproject-2", fleet["myproject"] / ".worktrees" / "myproject-2"
    )
    report = survey(claude_home=fleet["home"], proc_dir=fleet["proc"], title="myproject-2")
    target = next(r for r in report.sessions if r.transcript == fleet["root_session"])
    report.sessions = [target]
    target.claimants = 1  # hide the collision from triage; the adopter must still refuse
    before = _snapshot(fleet["home"])

    outcomes, _ = adopt_ready(report, claude_home=fleet["home"], proc_dir=fleet["proc"])

    assert isinstance(outcomes[0][1], TitleCollision)
    assert duplicate.is_file() and fleet["root_session"].is_file()
    assert _snapshot(fleet["home"]) == before, "a refused adoption must write nothing"


def test_cli_given_a_collision_and_yes_when_adopting_then_the_gate_still_holds(fleet, cli_audit):
    """AC-8: -y/--yes must NEVER cover the duplicate-title gate."""
    duplicate = _write_transcript(
        fleet["home"], UUID_C, "myproject-2", fleet["myproject"] / ".worktrees" / "myproject-2"
    )
    before = _snapshot(fleet["home"])

    code, out, err = run_cli(["ai", "session-audit", "-t", "myproject-2", "-a", "-y"])

    assert code == 1, "-y must not bypass the duplicate-title gate"
    assert "Title collisions" in out
    assert duplicate.is_file() and fleet["root_session"].is_file()
    assert _snapshot(fleet["home"]) == before, "nothing may be written when the gate refuses"


def test_cli_given_a_live_session_and_yes_when_adopting_then_it_is_skipped(fleet, cli_audit):
    """AC-8: -y does not cover the live-session refusal either."""
    _mark_live(fleet, 5353, "myproject-5", fleet["agent_dir"], session_id=UUID_B)
    before = _snapshot(fleet["home"])

    code, out, err = run_cli(["ai", "session-audit", "-t", "myproject-5", "-a", "-y"])

    assert code == 1
    assert "live session (pid 5353)" in out
    assert _snapshot(fleet["home"]) == before


# ---- CLI surface ------------------------------------------------------------


@pytest.fixture
def cli_audit(fleet, monkeypatch):
    """Bind the CLI's implicit home/proc lookups to the fake fleet."""
    import ai_cli.session_audit as module

    real_survey = module.survey
    real_adopt_ready = module.adopt_ready

    def _survey(**kw):
        kw.setdefault("claude_home", fleet["home"])
        kw.setdefault("proc_dir", fleet["proc"])
        return real_survey(**kw)

    def _adopt_ready(report, **kw):
        kw.setdefault("claude_home", fleet["home"])
        kw.setdefault("proc_dir", fleet["proc"])
        return real_adopt_ready(report, **kw)

    monkeypatch.setattr(module, "survey", _survey)
    monkeypatch.setattr(module, "adopt_ready", _adopt_ready)
    return fleet


def test_cli_given_a_survey_when_invoked_then_every_session_is_reported(cli_audit):
    """AC-1 via the CLI: the default run is a read-only report."""
    code, out, err = run_cli(["ai", "session-audit"])
    assert code == 0, err
    assert "myproject-2" in out and "myproject-5" in out and "myapp-1" in out
    assert "Scanned 4 transcripts" in out
    assert AGENT_WORKTREE in out


def test_cli_given_a_repo_filter_when_invoked_then_only_that_repo_is_reported(cli_audit, fleet):
    """AC-6 via the CLI."""
    code, out, err = run_cli(["ai", "session-audit", "--repo", str(fleet["myapp"])])
    assert code == 0, err
    assert "myapp-1" in out and "myproject-2" not in out


def test_cli_given_a_collision_when_invoked_then_it_is_reported_before_any_adoption(cli_audit, fleet):
    """AC-3 via the CLI: the collision is named in the survey itself."""
    _write_transcript(fleet["home"], UUID_C, "myproject-2", fleet["myproject"] / ".worktrees" / "myproject-2")
    code, out, err = run_cli(["ai", "session-audit"])
    assert code == 0
    assert "Title collisions (1)" in out
    assert "claimed by 2 transcripts" in out


def test_cli_given_the_adopt_flag_and_a_dry_run_when_invoked_then_nothing_is_written(cli_audit, fleet):
    """AC-5 via the CLI, proved against a filesystem snapshot."""
    (fleet["myproject"] / ".worktrees" / "myproject-5").mkdir()
    roots = (fleet["home"], fleet["myproject"], fleet["myapp"])
    before = _snapshot(*roots)

    code, out, err = run_cli(["ai", "session-audit", "-a", "-n"])

    assert "Would adopt" in out
    assert _snapshot(*roots) == before, "-n must not write anything"


def test_cli_given_the_adopt_flag_when_confirmed_then_sessions_are_adopted(cli_audit, fleet):
    """AC-4 via the CLI: real adoption reports the resolve probe."""
    (fleet["myproject"] / ".worktrees" / "myproject-5").mkdir()
    code, out, err = run_cli(["ai", "session-audit", "-a", "-y"])
    assert "Adopted myproject-5" in out
    assert "resolve probe" in out


def test_cli_given_a_declined_confirmation_when_prompted_then_nothing_is_adopted(cli_audit, fleet, monkeypatch):
    """AC-4: the confirmation prompt is honoured."""
    (fleet["myproject"] / ".worktrees" / "myproject-5").mkdir()
    monkeypatch.setattr("click.confirm", lambda *a, **k: False)
    before = _snapshot(fleet["home"])

    code, out, err = run_cli(["ai", "session-audit", "-a"])

    assert code == 1
    assert "Aborted" in err
    assert _snapshot(fleet["home"]) == before


def test_cli_given_an_accepted_confirmation_when_prompted_then_sessions_are_adopted(cli_audit, fleet, monkeypatch):
    """AC-4: accepting the prompt adopts."""
    (fleet["myproject"] / ".worktrees" / "myproject-5").mkdir()
    monkeypatch.setattr("click.confirm", lambda *a, **k: True)
    code, out, err = run_cli(["ai", "session-audit", "-a"])
    assert "Adopted myproject-5" in out


def test_cli_given_nothing_safe_to_adopt_when_invoked_then_reports_and_exits_nonzero(cli_audit, fleet):
    """AC-7 via the CLI: all skipped is a non-zero exit with reasons."""
    code, out, err = run_cli(["ai", "session-audit", "-t", "myapp-1", "-a", "-y"])
    assert code == 1
    assert "Nothing safe to adopt" in out
    assert "already adopted" in out


def test_cli_given_a_bulk_adopt_with_one_refusal_when_invoked_then_the_rest_adopt(cli_audit, fleet):
    """AC-7 via the CLI: the summary carries both counts."""
    _mark_live(fleet, 5454, "myproject-2", fleet["myproject"], session_id=UUID_A)
    (fleet["myproject"] / ".worktrees" / "myproject-5").mkdir()

    code, out, err = run_cli(["ai", "session-audit", "-a", "-y"])

    assert code == 1
    assert "Adopted myproject-5" in out
    assert "1 adopted, 2 skipped" in out


def test_cli_given_a_collision_during_bulk_adoption_when_invoked_then_the_remedy_is_printed(
    cli_audit, fleet, monkeypatch
):
    """AC-7 + AC-8: a collision raised by the adopter prints the retitle remedy."""
    import ai_cli.session_audit as module

    _write_transcript(fleet["home"], UUID_C, "myproject-2", fleet["myproject"] / ".worktrees" / "myproject-2")

    def _blind_triage(report):
        """Offer the colliding session as adoptable, so the ADOPTER must refuse it."""
        return [r for r in report.sessions if r.transcript == fleet["root_session"]], []

    monkeypatch.setattr(module, "triage", _blind_triage)

    code, out, err = run_cli(["ai", "session-audit", "-t", "myproject-2", "-a", "-y"])

    assert code == 1
    assert "-c retitle" in err
    assert "-y/--yes does not cover it" in err


# ---- AC-9: option forms -----------------------------------------------------


def test_cli_given_every_option_when_help_requested_then_each_has_a_short_and_long_form():
    """AC-9 / CLI convention: no long-only flags (CLAUDE.md)."""
    import click

    from ai_cli.main import cmd_session_audit

    seen = 0
    for param in cmd_session_audit.params:
        if not isinstance(param, click.Option):
            continue
        seen += 1
        shorts = [o for o in param.opts if len(o) == 2 and o.startswith("-") and not o.startswith("--")]
        longs = [o for o in param.opts if o.startswith("--")]
        assert shorts and longs, f"{param.name} needs both a short and a long form, has {param.opts}"
    assert seen >= 5, "every documented option must be present to be checked"


def test_cli_given_help_when_requested_then_both_forms_are_shown():
    """AC-9: the help output itself shows both forms for every option."""
    code, out, err = run_cli(["ai", "session-audit", "--help"])
    for short, long_ in (("-r", "--repo"), ("-t", "--title"), ("-a", "--adopt"), ("-n", "--dry-run"), ("-y", "--yes")):
        assert short in out and long_ in out, f"{short}/{long_} missing from --help"


# ---- AC-10: empty machines --------------------------------------------------


def test_survey_given_no_claude_home_at_all_when_surveyed_then_empty_without_raising(tmp_path):
    """AC-10: a machine that has never run Claude Code."""
    report = survey(claude_home=tmp_path / "absent", proc_dir=tmp_path / "proc")
    assert report.sessions == [] and report.collisions == {}
    assert report.scanned_project_dirs == 0 and report.scanned_transcripts == 0
    assert report.repos == []


def test_survey_given_projects_but_no_titled_sessions_when_surveyed_then_empty(tmp_path):
    """AC-10: transcripts exist but none is titled."""
    home = tmp_path / "home"
    repo = _make_repo(tmp_path, "myproject")
    _write_transcript(home, UUID_A, None, repo)
    (cc_project_dir(repo, home) / "not-a-transcript.txt").write_text("ignored")

    report = survey(claude_home=home, proc_dir=tmp_path / "proc")

    assert report.sessions == []
    assert report.scanned_transcripts == 1
    assert triage(report) == ([], [])


def test_survey_given_a_stray_file_in_projects_when_surveyed_then_it_is_not_a_project_dir(tmp_path):
    """AC-10: a file sitting in ~/.claude/projects must not be walked."""
    home = tmp_path / "home"
    (home / "projects").mkdir(parents=True)
    (home / "projects" / "stray.txt").write_text("x")
    report = survey(claude_home=home, proc_dir=tmp_path / "proc")
    assert report.scanned_project_dirs == 0


def test_adopt_ready_given_nothing_to_adopt_when_run_then_no_outcomes(tmp_path):
    """AC-10: driving adoption on an empty machine is a no-op, not an error."""
    report = survey(claude_home=tmp_path / "absent", proc_dir=tmp_path / "proc")
    assert adopt_ready(report, claude_home=tmp_path / "absent") == ([], [])


def test_cli_given_no_titled_sessions_when_invoked_then_reports_and_exits_zero(tmp_path, monkeypatch):
    """AC-10 via the CLI: an empty machine exits zero with a clear message."""
    import ai_cli.session_audit as module

    real_survey = module.survey
    monkeypatch.setattr(
        module,
        "survey",
        lambda **kw: real_survey(**{**kw, "claude_home": tmp_path / "absent", "proc_dir": tmp_path / "proc"}),
    )
    code, out, err = run_cli(["ai", "session-audit"])
    assert code == 0
    assert "No titled sessions found" in out


def test_cli_given_a_filter_matching_nothing_when_invoked_then_says_so(cli_audit):
    """AC-6 + AC-10: a filter that matches nothing reports the narrowed scope."""
    code, out, err = run_cli(["ai", "session-audit", "-t", "absent-1"])
    assert code == 0
    assert "matching the filters" in out
