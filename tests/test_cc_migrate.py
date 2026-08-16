"""Tests for ai_cli.cc_migrate — transcript migration between project roots.

The scenario under test: a Claude Code session was launched at a repo root, so
its transcript lives in the repo root's ~/.claude/projects/<slug>/ directory.
``ai c <n>`` (bare mode) resumes worktree sessions by scanning the *worktree's*
project directory for a transcript whose customTitle matches the ai_name, so
the transcript must be moved there — with recorded cwd fields rewritten — for
resume to find it.
"""

import json
from pathlib import Path

import pytest

from ai_cli.cc_migrate import (
    _rewrite_line,
    cc_project_dir,
    find_transcript,
    migrate_session,
    transcript_title,
)


def _record(**kw):
    return json.dumps(kw, separators=(",", ":"))


@pytest.fixture
def roots(tmp_path):
    """A fake repo root + worktree root + claude home, with one titled session."""
    repo = tmp_path / "projects" / "myproject"
    worktree = repo / ".worktrees" / "myproject-2"
    worktree.mkdir(parents=True)
    claude_home = tmp_path / "claude-home"

    src_dir = cc_project_dir(repo, claude_home)
    src_dir.mkdir(parents=True)
    uuid = "11111111-2222-4333-8444-555555555555"
    lines = [
        _record(type="user", sessionId=uuid, cwd=str(repo), customTitle="myproject-2", version="2.1.220"),
        _record(type="assistant", sessionId=uuid, cwd=str(repo), customTitle="myproject-2"),
        _record(type="user", sessionId=uuid, cwd=str(repo / "docs"), originalCwd=str(repo)),
        "not json at all",
        _record(type="user", sessionId=uuid, cwd=str(tmp_path / "elsewhere")),
    ]
    (src_dir / f"{uuid}.jsonl").write_text("\n".join(lines) + "\n")
    sidecar = src_dir / uuid / "tool-results"
    sidecar.mkdir(parents=True)
    (sidecar / "r1.json").write_text("{}")
    return {"repo": repo, "worktree": worktree, "home": claude_home, "uuid": uuid, "src_dir": src_dir}


# ---- cc_project_dir --------------------------------------------------------


def test_project_dir_given_underscored_path_when_slugified_then_every_nonalnum_becomes_dash(tmp_path):
    d = cc_project_dir(Path("/home/me/my_proj.x"), tmp_path)
    assert d == tmp_path / "projects" / "-home-me-my-proj-x"


def test_given_windows_root_cwd_when_rewritten_then_exact_root_is_replaced():
    source_root = r"C:\Users\user\projects\myproject"
    dest_root = r"D:\worktrees\myproject-1"

    rewritten = _rewrite_line(_record(cwd=source_root, originalCwd=source_root) + "\n", source_root, dest_root)

    assert json.loads(rewritten) == {"cwd": dest_root, "originalCwd": dest_root}


def test_given_windows_nested_cwd_when_rewritten_then_backslash_suffix_is_preserved():
    source_root = r"C:\Users\user\projects\myproject"
    dest_root = r"D:\worktrees\myproject-1"
    nested_cwd = source_root + r"\docs"
    nested_original_cwd = source_root + r"\.worktrees\myproject-2"

    rewritten = _rewrite_line(_record(cwd=nested_cwd, originalCwd=nested_original_cwd) + "\n", source_root, dest_root)

    assert json.loads(rewritten) == {
        "cwd": dest_root + r"\docs",
        "originalCwd": dest_root + r"\.worktrees\myproject-2",
    }


# ---- find_transcript / transcript_title ------------------------------------


def test_find_given_title_when_present_then_returns_matching_transcript(roots):
    found = find_transcript(roots["src_dir"], title="myproject-2")
    assert found is not None and found.name == f"{roots['uuid']}.jsonl"


def test_find_given_uuid_when_present_then_filename_match_wins(roots):
    found = find_transcript(roots["src_dir"], session_id=roots["uuid"])
    assert found is not None and found.stem == roots["uuid"]


def test_find_given_unknown_title_when_searched_then_returns_none(roots):
    assert find_transcript(roots["src_dir"], title="nope") is None


def test_title_given_titled_transcript_when_read_then_first_title_returned(roots):
    assert transcript_title(roots["src_dir"] / f"{roots['uuid']}.jsonl") == "myproject-2"


# ---- migrate_session: happy path --------------------------------------------


def test_migrate_given_titled_session_when_moved_then_dest_has_rewritten_cwds(roots):
    result = migrate_session(roots["repo"], roots["worktree"], title="myproject-2", claude_home=roots["home"])
    dest_dir = cc_project_dir(roots["worktree"], roots["home"])
    dest = dest_dir / f"{roots['uuid']}.jsonl"
    assert result.dest_jsonl == dest and dest.is_file()

    records = []
    for raw in dest.read_text().splitlines():
        try:
            records.append(json.loads(raw))
        except json.JSONDecodeError:
            records.append(raw)
    wt = str(roots["worktree"])
    assert records[0]["cwd"] == wt
    assert records[1]["cwd"] == wt
    # Subpath rewrites keep the suffix; originalCwd is rewritten too.
    assert records[2]["cwd"] == wt + "/docs"
    assert records[2]["originalCwd"] == wt
    # Non-JSON lines and unrelated paths are byte-preserved.
    assert records[3] == "not json at all"
    assert "elsewhere" in records[4]["cwd"]
    assert result.lines == 5 and result.rewritten == 3


def test_migrate_given_move_semantics_when_done_then_source_jsonl_and_sidecar_gone(roots):
    migrate_session(roots["repo"], roots["worktree"], title="myproject-2", claude_home=roots["home"])
    assert not (roots["src_dir"] / f"{roots['uuid']}.jsonl").exists()
    assert not (roots["src_dir"] / roots["uuid"]).exists()
    dest_dir = cc_project_dir(roots["worktree"], roots["home"])
    assert (dest_dir / roots["uuid"] / "tool-results" / "r1.json").is_file()


def test_migrate_given_keep_source_when_copied_then_source_still_present(roots):
    result = migrate_session(
        roots["repo"], roots["worktree"], title="myproject-2", keep_source=True, claude_home=roots["home"]
    )
    assert not result.moved
    assert (roots["src_dir"] / f"{roots['uuid']}.jsonl").is_file()
    assert (roots["src_dir"] / roots["uuid"]).is_dir()
    assert result.dest_jsonl.is_file()


def test_migrate_given_source_mtime_when_migrated_then_dest_mtime_preserved(roots):
    import os

    src = roots["src_dir"] / f"{roots['uuid']}.jsonl"
    os.utime(src, (1_000_000_000, 1_000_000_000))
    result = migrate_session(roots["repo"], roots["worktree"], title="myproject-2", claude_home=roots["home"])
    assert result.dest_jsonl.stat().st_mtime == pytest.approx(1_000_000_000)


def test_migrate_given_preserve_cwd_when_migrated_then_no_rewrites(roots):
    result = migrate_session(
        roots["repo"], roots["worktree"], title="myproject-2", preserve_cwd=True, claude_home=roots["home"]
    )
    assert result.rewritten == 0
    first = json.loads(result.dest_jsonl.read_text().splitlines()[0])
    assert first["cwd"] == str(roots["repo"])


def test_migrate_given_uuid_selector_when_used_then_same_transcript_found(roots):
    result = migrate_session(roots["repo"], roots["worktree"], session_id=roots["uuid"], claude_home=roots["home"])
    assert result.dest_jsonl.stem == roots["uuid"]


def test_migrate_given_dry_run_when_invoked_then_nothing_written(roots):
    result = migrate_session(
        roots["repo"], roots["worktree"], title="myproject-2", dry_run=True, claude_home=roots["home"]
    )
    assert result.dry_run and not result.moved
    assert result.lines == 5 and result.rewritten == 3
    assert not cc_project_dir(roots["worktree"], roots["home"]).exists()
    assert (roots["src_dir"] / f"{roots['uuid']}.jsonl").is_file()


# ---- migrate_session: failure paths ------------------------------------------


def test_migrate_given_no_selector_when_called_then_raises(roots):
    with pytest.raises(ValueError, match="title or a session UUID"):
        migrate_session(roots["repo"], roots["worktree"], claude_home=roots["home"])


def test_migrate_given_missing_dest_root_when_called_then_raises(roots):
    with pytest.raises(ValueError, match="does not exist"):
        migrate_session(
            roots["repo"], roots["repo"] / ".worktrees" / "nope", title="myproject-2", claude_home=roots["home"]
        )


def test_migrate_given_unknown_title_when_called_then_raises_naming_the_title(roots):
    with pytest.raises(ValueError, match="myproject-9"):
        migrate_session(roots["repo"], roots["worktree"], title="myproject-9", claude_home=roots["home"])


def test_migrate_given_existing_dest_when_no_force_then_raises_and_source_untouched(roots):
    dest_dir = cc_project_dir(roots["worktree"], roots["home"])
    dest_dir.mkdir(parents=True)
    (dest_dir / f"{roots['uuid']}.jsonl").write_text("occupied\n")
    with pytest.raises(ValueError, match="already exists"):
        migrate_session(roots["repo"], roots["worktree"], title="myproject-2", claude_home=roots["home"])
    assert (roots["src_dir"] / f"{roots['uuid']}.jsonl").is_file()


def test_migrate_given_existing_dest_when_forced_then_overwritten(roots):
    dest_dir = cc_project_dir(roots["worktree"], roots["home"])
    dest_dir.mkdir(parents=True)
    (dest_dir / f"{roots['uuid']}.jsonl").write_text("occupied\n")
    result = migrate_session(
        roots["repo"], roots["worktree"], title="myproject-2", force=True, claude_home=roots["home"]
    )
    assert result.lines == 5
    assert "occupied" not in result.dest_jsonl.read_text()


def test_migrate_given_title_mismatch_when_dest_name_differs_then_warns(roots):
    other = roots["repo"] / ".worktrees" / "myproject-7"
    other.mkdir(parents=True)
    result = migrate_session(roots["repo"], other, title="myproject-2", claude_home=roots["home"])
    assert any("myproject-7" in w for w in result.warnings)
