"""Tests for bulk transcript repathing."""

import json
from pathlib import Path

import pytest

from ai_cli.transcript_repath import (
    MissingDestPolicy,
    _rewrite_jsonl_line,
    _slugify_cwd,
    plan_repath,
    repath_all,
    repath_project_dir,
)


def test_slugify_cwd():
    """Slugification replaces all non-alphanumeric with hyphens."""
    assert _slugify_cwd("/home/user/projects") == "-home-user-projects"
    assert _slugify_cwd("/mnt/efs/fs-089_abc/projects") == "-mnt-efs-fs-089-abc-projects"
    assert _slugify_cwd("C:\\Users\\Name\\repo") == "C--Users-Name-repo"


def test_rewrite_jsonl_line_cwd_field():
    """Top-level cwd fields are rewritten."""
    line = '{"type":"init","cwd":"/old/root/myproject","sessionId":"abc"}\n'
    rewritten, changed = _rewrite_jsonl_line(line, "/old/root", "/new/root")
    assert changed
    assert "/new/root/myproject" in rewritten
    assert "/old/root" not in rewritten
    # Verify it's still valid JSON
    assert json.loads(rewritten.strip())


def test_rewrite_jsonl_line_embedded_in_content():
    """Embedded path references in any string value are rewritten."""
    line = (
        json.dumps(
            {
                "type": "message",
                "role": "user",
                "content": "Read the file at /old/root/data/file.txt",
                "cwd": "/old/root/myproject",
            }
        )
        + "\n"
    )
    rewritten, changed = _rewrite_jsonl_line(line, "/old/root", "/new/root")
    assert changed
    record = json.loads(rewritten.strip())
    assert "/new/root/data/file.txt" in record["content"]
    assert record["cwd"] == "/new/root/myproject"


def test_rewrite_jsonl_line_no_match():
    """Lines not containing the old root are unchanged."""
    line = '{"type":"message","role":"assistant","content":"Hello"}\n'
    rewritten, changed = _rewrite_jsonl_line(line, "/old/root", "/new/root")
    assert not changed
    assert rewritten == line


def test_rewrite_jsonl_line_blank():
    """Blank lines are unchanged."""
    rewritten, changed = _rewrite_jsonl_line("\n", "/old/root", "/new/root")
    assert not changed
    assert rewritten == "\n"


def test_rewrite_jsonl_line_malformed():
    """Malformed JSON raises an exception."""
    line = "not json at all\n"
    with pytest.raises(json.JSONDecodeError):
        _rewrite_jsonl_line(line, "/old/root", "/new/root")


def test_rewrite_jsonl_line_nested_paths():
    """Nested structures with embedded paths are rewritten."""
    line = (
        json.dumps(
            {
                "type": "tool-result",
                "tool": "read",
                "args": {"path": "/old/root/src/main.py"},
                "result": {"content": "# File from /old/root/src"},
                "cwd": "/old/root",
            }
        )
        + "\n"
    )
    rewritten, changed = _rewrite_jsonl_line(line, "/old/root", "/new/root")
    assert changed
    record = json.loads(rewritten.strip())
    assert record["args"]["path"] == "/new/root/src/main.py"
    assert "/new/root/src" in record["result"]["content"]
    assert record["cwd"] == "/new/root"


def test_plan_repath_empty_projects_dir(tmp_path):
    """Planning when no projects dir exists returns empty plan."""
    fake_home = tmp_path / ".claude"
    fake_home.mkdir()
    plan = plan_repath(
        Path("/old"),
        Path("/new"),
        claude_home=fake_home,
        dest_exists=lambda p: True,
    )
    assert plan.project_dirs == []
    assert plan.total_jsonl_files == 0


def test_plan_repath_finds_matching_dirs(tmp_path):
    """Planning finds project dirs under the old root slug."""
    fake_home = tmp_path / ".claude"
    projects = fake_home / "projects"
    projects.mkdir(parents=True)

    old_root = Path("/old/root")
    old_slug = _slugify_cwd(str(old_root))
    # Create a project dir matching the old root
    proj_dir = projects / (old_slug + "-myproject")
    proj_dir.mkdir()
    # Write a sample jsonl with the old cwd
    jsonl = proj_dir / "test.jsonl"
    jsonl.write_text(json.dumps({"type": "init", "cwd": "/old/root/myproject", "sessionId": "abc"}) + "\n")

    plan = plan_repath(
        old_root,
        Path("/new/root"),
        claude_home=fake_home,
        dest_exists=lambda p: True,
    )
    assert len(plan.project_dirs) == 1
    assert plan.project_dirs[0][0] == proj_dir
    assert plan.total_jsonl_files == 1
    assert plan.total_bytes > 0


def test_repath_project_dir_dry_run(tmp_path):
    """Dry run counts what would be rewritten without writing."""
    old_dir = tmp_path / "old-proj"
    old_dir.mkdir()
    jsonl = old_dir / "session.jsonl"
    jsonl.write_text(json.dumps({"cwd": "/old/root/proj"}) + "\n" + json.dumps({"cwd": "/old/root/proj"}) + "\n")

    new_dir = tmp_path / "new-proj"
    result = repath_project_dir(old_dir, new_dir, "/old/root", "/new/root", dry_run=True)

    assert result.jsonl_files == 1
    assert result.total_lines == 2
    assert result.lines_rewritten == 2
    assert result.bytes_written == 0
    assert not new_dir.exists()  # Dry run writes nothing


def test_repath_project_dir_writes_rewritten_files(tmp_path):
    """Real run writes rewritten files to new_dir."""
    old_dir = tmp_path / "old-proj"
    old_dir.mkdir()
    jsonl = old_dir / "session.jsonl"
    content = json.dumps({"type": "init", "cwd": "/old/root/proj", "content": "file at /old/root/data.txt"}) + "\n"
    jsonl.write_text(content)

    new_dir = tmp_path / "new-proj"
    result = repath_project_dir(old_dir, new_dir, "/old/root", "/new/root", dry_run=False)

    assert result.jsonl_files == 1
    assert result.lines_rewritten == 1
    assert new_dir.exists()
    dest_jsonl = new_dir / "session.jsonl"
    assert dest_jsonl.exists()

    rewritten = dest_jsonl.read_text()
    assert "/new/root/proj" in rewritten
    assert "/new/root/data.txt" in rewritten
    assert "/old/root" not in rewritten


def test_repath_project_dir_preserves_mtime(tmp_path):
    """Rewritten files preserve source mtime."""
    old_dir = tmp_path / "old-proj"
    old_dir.mkdir()
    jsonl = old_dir / "session.jsonl"
    jsonl.write_text(json.dumps({"cwd": "/old/root"}) + "\n")
    original_mtime = jsonl.stat().st_mtime

    new_dir = tmp_path / "new-proj"
    repath_project_dir(old_dir, new_dir, "/old/root", "/new/root", dry_run=False)

    dest_jsonl = new_dir / "session.jsonl"
    assert abs(dest_jsonl.stat().st_mtime - original_mtime) < 1.0


def test_repath_project_dir_copies_sidecar(tmp_path):
    """Sidecar directories are copied alongside rewritten transcripts."""
    old_dir = tmp_path / "old-proj"
    old_dir.mkdir()
    jsonl = old_dir / "abc123.jsonl"
    jsonl.write_text(json.dumps({"cwd": "/old/root"}) + "\n")

    sidecar = old_dir / "abc123"
    sidecar.mkdir()
    # Valid JSON in the sidecar .jsonl file
    sidecar_content = json.dumps({"type": "subagent", "path": "/old/root/file"}) + "\n"
    (sidecar / "subagent.jsonl").write_text(sidecar_content)
    # Also a non-jsonl file
    (sidecar / "metadata.txt").write_text("some metadata")

    new_dir = tmp_path / "new-proj"
    repath_project_dir(old_dir, new_dir, "/old/root", "/new/root", dry_run=False)

    dest_sidecar = new_dir / "abc123"
    assert dest_sidecar.is_dir()
    # The .jsonl was rewritten
    rewritten = (dest_sidecar / "subagent.jsonl").read_text()
    assert "/new/root/file" in rewritten
    # The .txt was copied byte-for-byte
    assert (dest_sidecar / "metadata.txt").read_text() == "some metadata"


def test_repath_project_dir_leaves_originals_untouched(tmp_path):
    """Original files are never modified."""
    old_dir = tmp_path / "old-proj"
    old_dir.mkdir()
    jsonl = old_dir / "session.jsonl"
    original = json.dumps({"cwd": "/old/root"}) + "\n"
    jsonl.write_text(original)

    new_dir = tmp_path / "new-proj"
    repath_project_dir(old_dir, new_dir, "/old/root", "/new/root", dry_run=False)

    assert jsonl.read_text() == original


def test_repath_project_dir_idempotent(tmp_path):
    """Running repath twice on already-rewritten output changes nothing."""
    old_dir = tmp_path / "old-proj"
    old_dir.mkdir()
    jsonl = old_dir / "session.jsonl"
    jsonl.write_text(json.dumps({"cwd": "/old/root/proj"}) + "\n")

    # First repath
    new_dir = tmp_path / "new-proj"
    result1 = repath_project_dir(old_dir, new_dir, "/old/root", "/new/root", dry_run=False)
    assert result1.lines_rewritten == 1

    # Second repath on the output
    newer_dir = tmp_path / "newer-proj"
    result2 = repath_project_dir(new_dir, newer_dir, "/old/root", "/new/root", dry_run=False)
    assert result2.lines_rewritten == 0  # Already rewritten, nothing changed
    assert result2.total_lines == 1


def test_repath_all_with_dest_base(tmp_path):
    """repath_all can write to an alternate dest_base for testing."""
    fake_home = tmp_path / ".claude"
    projects = fake_home / "projects"
    projects.mkdir(parents=True)

    old_root = Path("/old/root")
    old_slug = _slugify_cwd(str(old_root))
    proj_dir = projects / (old_slug + "-myproject")
    proj_dir.mkdir()
    jsonl = proj_dir / "test.jsonl"
    jsonl.write_text(json.dumps({"cwd": "/old/root/myproject"}) + "\n")

    dest_base = tmp_path / "dest"
    dest_base.mkdir()

    results = repath_all(
        old_root,
        Path("/new/root"),
        dest_base=dest_base,
        dry_run=False,
        claude_home=fake_home,
        dest_exists=lambda p: True,
    )
    assert len(results) == 1
    assert results[0].jsonl_files == 1

    # Check that output went to dest_base, not back into fake_home/projects
    new_slug = _slugify_cwd("/new/root/myproject")
    expected = dest_base / new_slug
    assert expected.is_dir()
    assert (expected / "test.jsonl").exists()


def test_rewrite_jsonl_line_dict_key_untouched():
    """Dict keys containing the prefix are left alone."""
    line = json.dumps({"/old/root/key": "value", "cwd": "/old/root/proj"}) + "\n"
    rewritten, changed = _rewrite_jsonl_line(line, "/old/root", "/new/root")
    assert changed
    record = json.loads(rewritten.strip())
    # Key is unchanged
    assert "/old/root/key" in record
    # But cwd value is rewritten
    assert record["cwd"] == "/new/root/proj"


def test_rewrite_jsonl_line_longer_token_with_prefix():
    """A longer token embedding the prefix is rewritten per the stated policy."""
    # This tests that we accept partial rewriting of longer tokens
    line = json.dumps({"id": "abc-/old/root-xyz", "cwd": "/old/root"}) + "\n"
    rewritten, changed = _rewrite_jsonl_line(line, "/old/root", "/new/root")
    assert changed
    record = json.loads(rewritten.strip())
    # The id value is rewritten because it contains the old root substring
    assert record["id"] == "abc-/new/root-xyz"
    assert record["cwd"] == "/new/root"


def test_repath_project_dir_with_memory_and_nested_jsonl(tmp_path):
    """Memory dirs and nested .jsonl files are copied/rewritten."""
    old_dir = tmp_path / "old-proj"
    old_dir.mkdir()

    # Top-level jsonl
    jsonl = old_dir / "session.jsonl"
    jsonl.write_text(json.dumps({"cwd": "/old/root"}) + "\n")

    # Memory directory with MEMORY.md
    memory_dir = old_dir / "memory"
    memory_dir.mkdir()
    (memory_dir / "MEMORY.md").write_text("Some memory content")

    # Nested .jsonl that should also be rewritten
    nested_jsonl = memory_dir / "nested.jsonl"
    nested_jsonl.write_text(json.dumps({"path": "/old/root/file.txt"}) + "\n")

    new_dir = tmp_path / "new-proj"
    result = repath_project_dir(old_dir, new_dir, "/old/root", "/new/root", dry_run=False)

    # Check that both jsonl files were rewritten
    assert result.jsonl_files == 2

    # Check memory dir was copied
    assert (new_dir / "memory" / "MEMORY.md").exists()
    assert (new_dir / "memory" / "MEMORY.md").read_text() == "Some memory content"

    # Check nested jsonl was rewritten
    nested_content = (new_dir / "memory" / "nested.jsonl").read_text()
    nested_record = json.loads(nested_content.strip())
    assert nested_record["path"] == "/new/root/file.txt"


def test_repath_project_dir_collision_refuses(tmp_path):
    """Pre-existing destination is refused."""
    old_dir = tmp_path / "old-proj"
    old_dir.mkdir()
    jsonl = old_dir / "session.jsonl"
    jsonl.write_text(json.dumps({"cwd": "/old/root"}) + "\n")

    new_dir = tmp_path / "new-proj"
    new_dir.mkdir()  # Pre-create destination

    result = repath_project_dir(old_dir, new_dir, "/old/root", "/new/root", dry_run=False)

    assert len(result.errors) > 0
    assert "already exists" in result.errors[0]
    assert result.jsonl_files == 0


def test_repath_project_dir_malformed_jsonl_produces_error(tmp_path):
    """A file with malformed JSONL produces an error and no output."""
    old_dir = tmp_path / "old-proj"
    old_dir.mkdir()
    jsonl = old_dir / "session.jsonl"
    jsonl.write_text('{"cwd": "/old/root"}\n' + "not json\n")

    new_dir = tmp_path / "new-proj"
    result = repath_project_dir(old_dir, new_dir, "/old/root", "/new/root", dry_run=False)

    # Should have an error
    assert len(result.errors) > 0
    assert "malformed" in result.errors[0].lower() or "json" in result.errors[0].lower()

    # The destination file should not exist (no partial write)
    assert not (new_dir / "session.jsonl").exists()


def test_repath_all_detects_same_destination_collision(tmp_path):
    """Two source dirs mapping to same destination produces a collision error."""
    fake_home = tmp_path / ".claude"
    projects = fake_home / "projects"
    projects.mkdir(parents=True)

    # Create two projects that will map to the same destination
    # (this requires crafted slugs that differ in source but collapse in dest)
    old_root = Path("/old/root")
    old_slug = _slugify_cwd(str(old_root))

    proj1 = projects / (old_slug + "-proj")
    proj1.mkdir()
    (proj1 / "test.jsonl").write_text(json.dumps({"cwd": "/old/root/proj"}) + "\n")

    # Manually create a second dir that would map to the same new destination
    # by reading cwd from a jsonl that points to the same subpath
    proj2 = projects / (old_slug + "-other")
    proj2.mkdir()
    # Both have same subpath, so they map to same dest
    (proj2 / "test.jsonl").write_text(json.dumps({"cwd": "/old/root/proj"}) + "\n")

    dest_base = tmp_path / "dest"
    dest_base.mkdir()

    results = repath_all(
        old_root,
        Path("/new/root"),
        dest_base=dest_base,
        dry_run=False,
        claude_home=fake_home,
        dest_exists=lambda p: True,
    )

    # At least one result should have a collision error
    collision_errors = [r for r in results if any("collision" in e for e in r.errors)]
    assert len(collision_errors) > 0


# --- Missing-destination policy -------------------------------------------------
#
# A repath maps cwds by root-slug prefix alone. That is unsound on its own: a
# session whose cwd was a since-reaped worktree gets a destination path that has
# no checkout, so the rewritten transcript points Claude Code at nothing and the
# session is offered as resumable when it cannot resume. The policy below makes
# the caller state, explicitly, both how destination existence is determined and
# what happens when it is not satisfied.


def _seed_store(tmp_path, *, suffixes):
    """Build a fake ~/.claude/projects holding one project dir per suffix."""
    fake_home = tmp_path / ".claude"
    projects = fake_home / "projects"
    projects.mkdir(parents=True)
    old_root = Path("/old/root")
    old_slug = _slugify_cwd(str(old_root))
    made = {}
    for suffix in suffixes:
        proj_dir = projects / (old_slug + _slugify_cwd(suffix))
        proj_dir.mkdir()
        (proj_dir / "test.jsonl").write_text(json.dumps({"cwd": f"/old/root{suffix}"}) + "\n")
        made[suffix] = proj_dir
    return fake_home, old_root, made


def test_plan_repath_requires_a_dest_existence_source(tmp_path):
    """A policy that depends on destination existence must be told how to check it.

    Defaulting to Path.is_dir would be the confidently-wrong failure: run from the
    SOURCE machine, every destination reads missing and a legitimate whole-store
    repath silently degrades to copying everything unrepathed.
    """
    fake_home, old_root, _ = _seed_store(tmp_path, suffixes=["/myproject"])

    with pytest.raises(ValueError) as exc:
        plan_repath(
            old_root,
            Path("/new/root"),
            claude_home=fake_home,
            missing_dest_policy=MissingDestPolicy.UNREPATHED,
        )

    message = str(exc.value)
    assert "dest_exists" in message
    assert "missing_dest_policy" in message


def test_plan_repath_partitions_on_destination_existence(tmp_path):
    """A dir whose new cwd exists is repathable; one whose new cwd does not is not."""
    fake_home, old_root, made = _seed_store(tmp_path, suffixes=["/live", "/reaped"])

    plan = plan_repath(
        old_root,
        Path("/new/root"),
        claude_home=fake_home,
        dest_exists=lambda p: str(p) == "/new/root/live",
        missing_dest_policy=MissingDestPolicy.UNREPATHED,
    )

    assert [old for old, _ in plan.project_dirs] == [made["/live"]]
    assert [m.old_dir for m in plan.missing_dest] == [made["/reaped"]]
    assert plan.missing_dest[0].new_cwd == Path("/new/root/reaped")
    assert plan.missing_dest[0].disposition == MissingDestPolicy.UNREPATHED


def test_plan_repath_policy_repath_needs_no_existence_check(tmp_path):
    """The old unconditional behaviour stays reachable, but only by asking for it."""
    fake_home, old_root, made = _seed_store(tmp_path, suffixes=["/live", "/reaped"])

    plan = plan_repath(
        old_root,
        Path("/new/root"),
        claude_home=fake_home,
        missing_dest_policy=MissingDestPolicy.REPATH,
    )

    assert sorted(old for old, _ in plan.project_dirs) == sorted(made.values())
    assert plan.missing_dest == []


def test_repath_all_skip_policy_writes_nothing_for_a_missing_destination(tmp_path):
    """SKIP leaves the source alone and produces no destination directory."""
    fake_home, old_root, made = _seed_store(tmp_path, suffixes=["/reaped"])
    dest_base = tmp_path / "dest"
    dest_base.mkdir()

    results = repath_all(
        old_root,
        Path("/new/root"),
        dest_base=dest_base,
        claude_home=fake_home,
        dest_exists=lambda p: False,
        missing_dest_policy=MissingDestPolicy.SKIP,
    )

    assert [r.disposition for r in results] == ["skipped"]
    assert results[0].jsonl_files == 0
    assert list(dest_base.iterdir()) == []
    assert (made["/reaped"] / "test.jsonl").exists()


def test_repath_all_unrepathed_policy_copies_under_the_original_slug(tmp_path):
    """UNREPATHED keeps the transcript readable without making it falsely resumable.

    The copy lands under the ORIGINAL slug and still contains the OLD root, so the
    path it names does not resolve on the new machine and Claude Code cannot offer
    it as a resumable session there.
    """
    fake_home, old_root, made = _seed_store(tmp_path, suffixes=["/reaped"])
    dest_base = tmp_path / "dest"
    dest_base.mkdir()

    results = repath_all(
        old_root,
        Path("/new/root"),
        dest_base=dest_base,
        claude_home=fake_home,
        dest_exists=lambda p: False,
        missing_dest_policy=MissingDestPolicy.UNREPATHED,
    )

    assert [r.disposition for r in results] == ["unrepathed"]
    assert results[0].lines_rewritten == 0

    copied = dest_base / made["/reaped"].name
    assert copied.is_dir(), f"expected an unrepathed copy at {copied}"
    body = (copied / "test.jsonl").read_text()
    assert "/old/root/reaped" in body
    assert "/new/root" not in body
    # Byte-for-byte with the source
    assert body == (made["/reaped"] / "test.jsonl").read_text()


def test_repath_all_mixed_store_repaths_only_the_reachable_dirs(tmp_path):
    """One store, both dispositions, each reported honestly."""
    fake_home, old_root, made = _seed_store(tmp_path, suffixes=["/live", "/reaped"])
    dest_base = tmp_path / "dest"
    dest_base.mkdir()

    results = repath_all(
        old_root,
        Path("/new/root"),
        dest_base=dest_base,
        claude_home=fake_home,
        dest_exists=lambda p: str(p) == "/new/root/live",
        missing_dest_policy=MissingDestPolicy.UNREPATHED,
    )

    by_disposition = {r.disposition: r for r in results}
    assert set(by_disposition) == {"repathed", "unrepathed"}

    repathed = dest_base / _slugify_cwd("/new/root/live")
    assert repathed.is_dir()
    assert "/new/root/live" in (repathed / "test.jsonl").read_text()

    unrepathed = dest_base / made["/reaped"].name
    assert unrepathed.is_dir()
    assert "/old/root/reaped" in (unrepathed / "test.jsonl").read_text()
