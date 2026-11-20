"""Explicitly keeping colliding agent worktrees is bound to their current Git state."""

from __future__ import annotations

from pathlib import PureWindowsPath

import pytest
import test_session_migrate
from test_session_migrate import NEW, OLD, _finish, _git, _migrate
from test_session_migrate_on_launch import _launch, _ledger, _legacy_finished_row

from ai_cli import session_migrate, session_migrate_status

SLOT = "review-slot"
ACCEPT = "--accept-kept-hold"
slot = test_session_migrate.slot


@pytest.fixture
def migrated(slot):
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    _legacy_finished_row(slot)
    return slot


def _hold(repo, owner, name, branch=None):
    path = repo / ".worktrees" / f"{owner}-agents" / name
    options = ["-b", branch] if branch else ["--detach"]
    _git("worktree", "add", "-q", *options, str(path), cwd=repo)
    _git("commit", "-q", "--allow-empty", "-m", f"{owner} hold", cwd=path)
    return path


@pytest.fixture
def anchors(migrated):
    repo = migrated["repo"]
    return (
        _hold(repo, OLD, SLOT, "wt/reviewer/anchor-old"),
        _hold(repo, NEW, SLOT, "wt/reviewer/anchor-new"),
    )


def _judge(slot):
    return session_migrate_status.replan(slot["repo"], OLD, NEW, projects_dir=slot["repo"].parent, tmux_names=list)


def _git_state(repo, holds):
    return (
        _git("worktree", "list", "--porcelain", cwd=repo),
        _git("show-ref", cwd=repo),
        tuple(_git("rev-parse", "HEAD", cwd=hold).strip() for hold in holds),
    )


def _assert_accepted_hold(row, anchors, heads):
    assert SLOT in str(row) and row["time"]
    assert all(str(value) in str(row) for value in (*anchors, *heads)), row


def test_given_windows_anchors_when_acceptance_is_read_then_both_paths_and_tips_match(tmp_path):
    repo = PureWindowsPath(r"D:\my projects\myrepo")
    anchors = tuple(repo / ".worktrees" / f"{owner}-agents" / SLOT for owner in (OLD, NEW))
    heads = ("a" * 40, "b" * 40)
    record = {
        "slot": SLOT,
        "old_path": str(anchors[0]),
        "new_path": str(anchors[1]),
        "old_head": heads[0],
        "new_head": heads[1],
    }
    session_migrate._append_ledger(tmp_path, OLD, NEW, repo, accepted_kept_holds=[record])

    (row,) = _ledger({"state": tmp_path})
    _assert_accepted_hold(row, anchors, heads)


def test_given_two_anchors_when_finished_then_only_explicit_acceptance_completes_the_migration(migrated, anchors):
    repo = migrated["repo"]
    before = _git_state(repo, anchors)
    assert before[2][0] != before[2][1]
    for hold in anchors:
        (hold / "empty-dir").mkdir()
    old_git_files = [(hold / ".git").read_bytes() for hold in anchors]

    code, out, err, launched = _launch(migrated)
    assert code == 1 and not launched, out + err
    assert "is partial" in out + err
    assert all(str(hold) in out + err for hold in anchors)
    assert _git_state(repo, anchors) == before
    command = f"ai migrate-session {OLD} {NEW} --finish -r {repo} {ACCEPT} {SLOT}"
    assert command in out + err, out + err

    code, out, err = _finish(repo, "-y", ACCEPT, SLOT)
    assert code == 0, out + err
    assert f"{OLD} -> {NEW}: finished" in out
    assert f"kept (accepted: {SLOT})" in out
    assert not _judge(migrated)
    code, out, err, launched = _launch(migrated)
    assert code == 0 and launched, out + err
    assert _git_state(repo, anchors) == before
    assert [(hold / ".git").read_bytes() for hold in anchors] == old_git_files
    assert all((hold / "empty-dir").is_dir() for hold in anchors)

    rows = _ledger(migrated)
    assert rows[-1]["verified"] is True
    _assert_accepted_hold(rows[-2], anchors, before[2])
    code, out, err = _finish(repo, "-y", ACCEPT, SLOT)
    assert code == 0, out + err
    assert _ledger(migrated) == rows


@pytest.mark.parametrize(
    "changed", ["old-tip", "new-tip", "old-container", "new-container", "old-content", "new-content"]
)
def test_given_recorded_acceptance_when_a_tip_or_container_changes_then_launch_refuses(migrated, anchors, changed):
    repo = migrated["repo"]
    hold = anchors[0 if changed.startswith("old-") else 1]
    nested_file = hold / "notes" / "review.txt"
    if changed.endswith("content"):
        nested_file.parent.mkdir()
        nested_file.write_text("before", encoding="utf-8")
    code, out, err = _finish(repo, "-y", ACCEPT, SLOT)
    assert code == 0, out + err
    if changed.endswith("tip"):
        _git("commit", "-q", "--allow-empty", "-m", "changed tip", cwd=hold)
    elif changed.endswith("content"):
        nested_file.write_text("edited", encoding="utf-8")
    else:
        (hold.parent / "new-entry").mkdir()

    assert "agent worktree" in _judge(migrated)
    code, out, err, launched = _launch(migrated)
    assert code == 1 and not launched, out + err
    assert all(str(path) in out + err for path in anchors)


def test_given_recorded_acceptance_when_destination_removed_then_normal_move_is_planned(migrated, anchors):
    repo = migrated["repo"]
    code, out, err = _finish(repo, "-y", ACCEPT, SLOT)
    assert code == 0, out + err
    _git("worktree", "remove", str(anchors[1]), cwd=repo)

    code, out, err = _finish(repo, "-n")
    assert code == 0, out + err
    assert f"agent worktree: {anchors[0]} -> {anchors[1]}" in out
    assert "kept (accepted:" not in out


def test_given_empty_destination_when_acceptance_requested_then_refused(migrated):
    repo = migrated["repo"]
    old_hold = _hold(repo, OLD, SLOT)
    new_hold = repo / ".worktrees" / f"{NEW}-agents" / SLOT
    new_hold.mkdir(parents=True)
    before = _ledger(migrated)

    code, out, err = _finish(repo, "-y", ACCEPT, SLOT)
    assert code != 0, out + err
    assert "registered worktree" in out + err
    assert all(str(hold) in out + err for hold in (old_hold, new_hold))
    assert new_hold.is_dir() and not list(new_hold.iterdir())
    assert _ledger(migrated) == before


def test_given_two_collisions_when_one_accepted_then_other_still_blocks(migrated, anchors):
    repo = migrated["repo"]
    other = "other-slot"
    old_other = _hold(repo, OLD, other)
    new_other = _hold(repo, NEW, other, "review-branch")
    before = _git_state(repo, (*anchors, old_other, new_other))

    code, out, err = _finish(repo, "-y", ACCEPT, SLOT)
    assert code == 1, out + err
    left = _judge(migrated)
    assert str(old_other) in left["agent worktree"] and str(new_other) in left["agent worktree"]
    assert str(anchors[0]) not in left["agent worktree"]
    assert _git_state(repo, (*anchors, old_other, new_other)) == before

    code, out, err = _finish(repo, "-y", ACCEPT, other)
    assert code == 0, out + err
    assert not _judge(migrated)


def test_given_another_collision_when_uncolliding_slot_named_then_refused(migrated, anchors):
    repo = migrated["repo"]
    other = _hold(repo, OLD, "other-slot")
    before = _ledger(migrated)

    code, out, err = _finish(repo, "-y", ACCEPT, other.name)
    assert code == 2, out + err
    assert f"{other.name} is not colliding" in out + err
    assert other.is_dir()
    assert _ledger(migrated) == before


def test_given_no_collision_when_acceptance_requested_then_no_op(migrated):
    repo = migrated["repo"]
    other = _hold(repo, OLD, "other-slot")
    before = _ledger(migrated)

    code, out, err = _finish(repo, "-y", ACCEPT, other.name)
    assert code == 0, out + err
    assert "no kept-hold collision" in out + err
    assert other.is_dir()
    assert _ledger(migrated) == before
