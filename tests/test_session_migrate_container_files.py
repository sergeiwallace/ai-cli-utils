"""Loose agent prompts migrate without overwriting another session's files."""

import sys

import pytest
import test_session_migrate
from conftest import snapshot_tree
from test_session_migrate import NEW, OLD, _finish, _git, _migrate

slot = test_session_migrate.slot

# A symlink needs a privilege Windows runners lack; probe before any fixture is built.
needs_symlinks = pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a privilege Windows runners lack")


@pytest.fixture
def prompts(slot):
    source = slot["repo"] / ".worktrees" / f"{OLD}-agents"
    target = source.with_name(f"{NEW}-agents")
    source.mkdir()
    (source / "prompt.txt").write_bytes(b"saved prompt\n")
    return source, target


@pytest.mark.parametrize("existing", [False, True])
def test_given_saved_prompts_when_previewed_and_migrated_then_files_move_and_replan_converges(slot, prompts, existing):
    source, target = prompts
    if existing:
        target.mkdir()
        (target / "other.txt").write_text("other prompt\n")
    before = snapshot_tree(slot["repo"])
    code, out, err = _migrate(slot["repo"], "-n")
    assert code == 0, out + err
    assert f"agent container file: {source / 'prompt.txt'} -> {target / 'prompt.txt'}" in out
    assert snapshot_tree(slot["repo"]) == before

    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    assert not source.exists()
    assert (target / "prompt.txt").read_bytes() == b"saved prompt\n"
    if existing:
        assert (target / "other.txt").read_text() == "other prompt\n"
    code, out, err = _finish(slot["repo"], "-n")
    assert code == 0, out + err
    assert "agent container file:" not in out


def test_given_a_name_clash_when_migrated_and_retried_then_both_files_survive_and_retry_finishes(slot, prompts):
    source, target = prompts
    target.mkdir()
    (target / "prompt.txt").write_bytes(b"new prompt\n")
    (source / "extra.txt").write_bytes(b"extra\n")
    for flags in [("-n",), ("-y",)]:
        code, out, err = _migrate(slot["repo"], *flags)
        assert code == 1, out + err
        assert (
            f"agent container file: {source / 'prompt.txt'} -> {target / 'prompt.txt'} SKIPPED (target exists)" in out
        )
        assert (source / "prompt.txt").read_bytes() == b"saved prompt\n"
        assert (target / "prompt.txt").read_bytes() == b"new prompt\n"
    assert (target / "extra.txt").read_bytes() == b"extra\n"
    (target / "prompt.txt").rename(target / "preserved.txt")
    code, out, err = _migrate(slot["repo"], "-y")
    assert code == 0, out + err
    assert not source.exists()
    assert (target / "prompt.txt").read_bytes() == b"saved prompt\n"
    assert (target / "preserved.txt").read_bytes() == b"new prompt\n"


@pytest.mark.parametrize("kind", [pytest.param("symlink", marks=needs_symlinks), "worktree", "open-file"])
def test_given_unsafe_container_content_when_migrated_then_prompts_stay(slot, prompts, kind):
    source, target = prompts
    if kind == "symlink":
        (source / "link").symlink_to(source / "prompt.txt")
    elif kind == "worktree":
        _git("worktree", "add", "-q", str(source / "foreign"), "-b", "wt/session-1/task", cwd=slot["repo"])
        _git("worktree", "lock", str(source / "foreign"), cwd=slot["repo"])
    with (source / "prompt.txt").open("rb") as stream:
        # Only the open-file case keeps the descriptor during migration.
        if kind != "open-file":
            stream.close()
        code, out, err = _migrate(slot["repo"], "-y")
    assert code == 1, out + err
    assert (source / "prompt.txt").read_bytes() == b"saved prompt\n"
    assert not (target / "prompt.txt").exists()
    assert f"agent container: {source} (kept:" in out


@pytest.mark.parametrize(
    "kind",
    ["file", pytest.param("symlink", marks=needs_symlinks), pytest.param("dangling-symlink", marks=needs_symlinks)],
)
def test_given_an_occupied_destination_when_previewed_and_migrated_then_no_content_is_overwritten(slot, prompts, kind):
    source, target = prompts
    occupied = target if kind == "file" else target / "prompt.txt"
    if kind == "file":
        target.write_text("keep target\n")
    else:
        target.mkdir()
        referent = target / "other.txt"
        if kind == "symlink":
            referent.write_text("keep target\n")
        occupied.symlink_to(referent)
    before = snapshot_tree(source), snapshot_tree(target)
    for flags in [("-n",), ("-y",)]:
        code, out, err = _migrate(slot["repo"], *flags)
        assert code == 1, out + err
        assert "SKIPPED (target exists)" in out
        assert (snapshot_tree(source), snapshot_tree(target)) == before


def test_given_prompts_left_after_migration_when_finished_then_preview_and_replan_agree(slot, prompts):
    source, target = prompts
    with (source / "prompt.txt").open("rb"):
        code, out, err = _migrate(slot["repo"], "-y")
    assert code == 1, out + err
    code, out, err = _finish(slot["repo"], "-n")
    assert code == 0, out + err
    assert f"agent container file: {source / 'prompt.txt'} -> {target / 'prompt.txt'}" in out
    assert (source / "prompt.txt").exists()
    code, out, err = _finish(slot["repo"], "-y")
    assert code == 0, out + err
    assert not source.exists()
    assert (target / "prompt.txt").read_bytes() == b"saved prompt\n"
    code, out, err = _finish(slot["repo"], "-n")
    assert code == 0, out + err
    assert "agent container file:" not in out


def test_given_prompts_beside_an_empty_reaped_directory_when_previewed_then_plan_matches_migration(slot, prompts):
    source, target = prompts
    (source / "reaped-leaf").mkdir()
    for flags in [("-n",), ("-y",)]:
        code, out, err = _migrate(slot["repo"], *flags)
        assert code == 0, out + err
        assert f"agent container file: {source / 'prompt.txt'} -> {target / 'prompt.txt'}" in out
    assert not source.exists()
    assert (target / "prompt.txt").read_bytes() == b"saved prompt\n"
