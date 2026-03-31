import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

from ai_cli.sync import (
    normalize_project_path,
    denormalize_project_name,
    get_local_prefix,
    get_source_machine,
    _default_remote_bare_url,
    _parse_flags,
    _handoff_queue_dir,
    load_sync_config,
    detect_jsonl_divergence,
    should_sync_file,
    is_memory_file,
    is_jsonl_file,
    file_hash,
    stage_project_files,
    apply_pull_files,
    stage_handoff_files,
    apply_handoff_files,
    git_commit_staged,
    is_cc_active_on_server,
    is_cc_active_locally,
    notify_conflicts,
    _detect_foreign_home,
    translate_cwd_paths,
    _pre_pull_push_memories,
    init_staging_repo,
    _detect_foreign_home_in_history,
    _write_jsonl_translated,
    SyncConfig,
)

# Fixed prefix strings for tests — mirror what get_local_prefix() would return on each platform
_MAC_PREFIX = "-Users-sergeiwallace-projects-"
_SERVER_PREFIX = "-home-sergei-projects-"


# ---------------------------------------------------------------------------
# normalize_project_path
# ---------------------------------------------------------------------------


def test_normalize_project_path_when_mac_prefix_then_returns_bare_name():
    assert normalize_project_path("-Users-sergeiwallace-projects-sergei", _MAC_PREFIX) == "sergei"


def test_normalize_project_path_when_worktree_suffix_then_preserves_it():
    result = normalize_project_path("-Users-sergeiwallace-projects-sergei--worktrees-sw-1", _MAC_PREFIX)
    assert result == "sergei--worktrees-sw-1"


def test_normalize_project_path_when_server_prefix_then_returns_bare_name():
    assert normalize_project_path("-home-sergei-projects-sergei", _SERVER_PREFIX) == "sergei"


def test_normalize_project_path_when_no_match_then_returns_none():
    assert normalize_project_path("-home-sergei-projects-sergei", _MAC_PREFIX) is None


def test_normalize_project_path_when_different_project_then_correct():
    assert normalize_project_path("-Users-sergeiwallace-projects-aurion", _MAC_PREFIX) == "aurion"


def test_normalize_project_path_when_server_worktree_then_preserves_suffix():
    result = normalize_project_path("-home-sergei-projects-sergei--worktrees-sw-2", _SERVER_PREFIX)
    assert result == "sergei--worktrees-sw-2"


# ---------------------------------------------------------------------------
# denormalize_project_name
# ---------------------------------------------------------------------------


def test_denormalize_project_name_when_bare_name_then_returns_mac_cc_dir():
    assert denormalize_project_name("sergei", _MAC_PREFIX) == "-Users-sergeiwallace-projects-sergei"


def test_denormalize_project_name_when_worktree_suffix_then_preserves_it():
    result = denormalize_project_name("sergei--worktrees-sw-1", _MAC_PREFIX)
    assert result == "-Users-sergeiwallace-projects-sergei--worktrees-sw-1"


def test_denormalize_project_name_when_server_prefix_then_correct():
    result = denormalize_project_name("aurion", _SERVER_PREFIX)
    assert result == "-home-sergei-projects-aurion"


def test_denormalize_normalize_roundtrip():
    cc_dir = "-Users-sergeiwallace-projects-sergei--worktrees-sw-3"
    bare = normalize_project_path(cc_dir, _MAC_PREFIX)
    assert denormalize_project_name(bare, _MAC_PREFIX) == cc_dir


# ---------------------------------------------------------------------------
# get_local_prefix
# ---------------------------------------------------------------------------


def test_get_local_prefix_returns_expected_format():
    prefix = get_local_prefix()
    assert prefix.endswith("-projects-")
    assert prefix.startswith("-")


def test_get_local_prefix_consistent_with_home():
    """get_local_prefix should encode Path.home() and append -projects-."""
    import re

    home = str(Path.home())
    expected = re.sub(r"[^a-zA-Z0-9]", "-", home) + "-projects-"
    assert get_local_prefix() == expected


# ---------------------------------------------------------------------------
# is_memory_file
# ---------------------------------------------------------------------------


def test_is_memory_file_when_MEMORY_md_then_true():
    assert is_memory_file(Path("MEMORY.md")) is True


def test_is_memory_file_when_file_in_memory_dir_then_true():
    assert is_memory_file(Path("memory/user_profile.md")) is True


def test_is_memory_file_when_file_in_memory_dir_nested_then_true():
    assert is_memory_file(Path("sergei/memory/project_current_work.md")) is True


def test_is_memory_file_when_jsonl_then_false():
    assert is_memory_file(Path("conversations.jsonl")) is False


def test_is_memory_file_when_tool_results_json_then_false():
    assert is_memory_file(Path("tool-results/uuid/result.json")) is False


def test_is_memory_file_when_MEMORY_md_in_memory_dir_then_true():
    assert is_memory_file(Path("memory/MEMORY.md")) is True


# ---------------------------------------------------------------------------
# is_jsonl_file
# ---------------------------------------------------------------------------


def test_is_jsonl_file_when_jsonl_extension_then_true():
    assert is_jsonl_file(Path("abc-uuid.jsonl")) is True


def test_is_jsonl_file_when_md_extension_then_false():
    assert is_jsonl_file(Path("MEMORY.md")) is False


def test_is_jsonl_file_when_conflict_jsonl_then_true():
    assert is_jsonl_file(Path("conflict-2026-03-23T14-30-00.jsonl")) is True


# ---------------------------------------------------------------------------
# should_sync_file
# ---------------------------------------------------------------------------


def test_should_sync_file_when_memory_file_and_memories_only_then_true():
    assert should_sync_file(Path("memory/project_current_work.md"), memories_only=True) is True


def test_should_sync_file_when_jsonl_and_memories_only_then_false():
    assert should_sync_file(Path("abc.jsonl"), memories_only=True) is False


def test_should_sync_file_when_tool_results_then_false():
    assert should_sync_file(Path("tool-results/uuid123/result.json"), memories_only=False) is False


def test_should_sync_file_when_jsonl_and_not_memories_only_then_true():
    assert should_sync_file(Path("abc.jsonl"), memories_only=False) is True


def test_should_sync_file_when_memory_and_not_memories_only_then_true():
    assert should_sync_file(Path("memory/user_profile.md"), memories_only=False) is True


def test_should_sync_file_when_tool_results_and_memories_only_then_false():
    assert should_sync_file(Path("tool-results/uuid/data.json"), memories_only=True) is False


def test_should_sync_file_when_ds_store_then_false():
    assert should_sync_file(Path(".DS_Store"), memories_only=False) is False


def test_should_sync_file_when_ds_store_in_subdir_then_false():
    assert should_sync_file(Path("abc123/.DS_Store"), memories_only=False) is False


def test_should_sync_file_when_subagents_file_then_false():
    # Subagents live inside session lock dirs — syncing them recreates the lock dir
    # on the remote, making CC think the session is active and hiding it from /resume.
    assert should_sync_file(Path("c444508f/subagents/agent-abc123.jsonl"), memories_only=False) is False


def test_should_sync_file_when_subagents_file_and_memories_only_then_false():
    assert should_sync_file(Path("c444508f/subagents/agent-abc123.jsonl"), memories_only=True) is False


# ---------------------------------------------------------------------------
# translate_history_jsonl
# ---------------------------------------------------------------------------


def test_translate_history_jsonl_when_foreign_paths_then_replaces(tmp_path, monkeypatch):
    from ai_cli.sync import translate_history_jsonl

    history_path = tmp_path / ".claude" / "history.jsonl"
    history_path.parent.mkdir()
    mac_home = "/Users/sergeiwallace"
    history_path.write_text(
        '{"project":"/Users/sergeiwallace/projects/sergei","sessionId":"abc"}\n'
        '{"project":"/Users/sergeiwallace/projects/aurion","sessionId":"def"}\n'
    )

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    with patch("pathlib.Path.home", return_value=tmp_path):
        count = translate_history_jsonl()

    lines = history_path.read_text().splitlines()
    assert str(tmp_path) in lines[0]
    assert mac_home not in lines[0]
    assert count == 2


def test_translate_history_jsonl_when_no_foreign_paths_then_noop(tmp_path):
    from ai_cli.sync import translate_history_jsonl

    history_path = tmp_path / ".claude" / "history.jsonl"
    history_path.parent.mkdir()
    local_home = str(tmp_path)
    history_path.write_text(f'{{"project":"{local_home}/projects/sergei","sessionId":"abc"}}\n')

    with patch("pathlib.Path.home", return_value=tmp_path):
        count = translate_history_jsonl()

    assert count == 0


def test_translate_history_jsonl_when_no_file_then_noop(tmp_path):
    from ai_cli.sync import translate_history_jsonl

    with patch("pathlib.Path.home", return_value=tmp_path):
        count = translate_history_jsonl()

    assert count == 0


# ---------------------------------------------------------------------------
# retranslate_project_jsonls
# ---------------------------------------------------------------------------


def test_retranslate_project_jsonls_when_foreign_cwd_then_translates(tmp_path):
    from ai_cli.sync import retranslate_project_jsonls

    projects_dir = tmp_path / ".claude" / "projects"
    proj_dir = projects_dir / "-home-sergei-projects-aido"
    proj_dir.mkdir(parents=True)
    mac_home = "/Users/sergeiwallace"
    conv = proj_dir / "abc123.jsonl"
    conv.write_text(
        '{"type":"summary","cwd":"/Users/sergeiwallace/projects/aido"}\n{"type":"user","content":"hello"}\n'
    )

    with patch("pathlib.Path.home", return_value=tmp_path):
        count = retranslate_project_jsonls()

    assert count == 1
    content = conv.read_text()
    assert mac_home not in content
    assert str(tmp_path) in content


def test_retranslate_project_jsonls_when_foreign_project_field_then_translates(tmp_path):
    from ai_cli.sync import retranslate_project_jsonls

    projects_dir = tmp_path / ".claude" / "projects"
    proj_dir = projects_dir / "-home-sergei-projects-sergei"
    proj_dir.mkdir(parents=True)
    mac_home = "/Users/sergeiwallace"
    conv = proj_dir / "def456.jsonl"
    # File with no cwd field, only project field
    conv.write_text(
        '{"type":"session","project":"/Users/sergeiwallace/projects/sergei"}\n{"type":"user","content":"hi"}\n'
    )

    with patch("pathlib.Path.home", return_value=tmp_path):
        count = retranslate_project_jsonls()

    assert count == 1
    content = conv.read_text()
    assert mac_home not in content
    assert str(tmp_path) in content


def test_retranslate_project_jsonls_when_already_translated_then_noop(tmp_path):
    from ai_cli.sync import retranslate_project_jsonls

    projects_dir = tmp_path / ".claude" / "projects"
    proj_dir = projects_dir / "-home-sergei-projects-aido"
    proj_dir.mkdir(parents=True)
    conv = proj_dir / "abc123.jsonl"
    local_home = str(tmp_path)
    conv.write_text(f'{{"type":"summary","cwd":"{local_home}/projects/aido"}}\n')

    with patch("pathlib.Path.home", return_value=tmp_path):
        count = retranslate_project_jsonls()

    assert count == 0


def test_retranslate_project_jsonls_when_no_projects_dir_then_noop(tmp_path):
    from ai_cli.sync import retranslate_project_jsonls

    with patch("pathlib.Path.home", return_value=tmp_path):
        count = retranslate_project_jsonls()

    assert count == 0


def test_detect_foreign_home_checks_project_field_fallback(tmp_path):
    from ai_cli.sync import _detect_foreign_home

    # File with no cwd field, only project field
    jsonl = tmp_path / "conv.jsonl"
    jsonl.write_text('{"type":"session","project":"/Users/someuser/projects/foo"}\n')

    with patch("pathlib.Path.home", return_value=tmp_path):
        result = _detect_foreign_home(jsonl)

    assert result == "/Users/someuser"


# ---------------------------------------------------------------------------
# file_hash
# ---------------------------------------------------------------------------


def test_file_hash_when_same_content_then_same_hash(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    content = b"hello world\n"
    a.write_bytes(content)
    b.write_bytes(content)
    assert file_hash(a) == file_hash(b)


def test_file_hash_when_different_content_then_different_hash(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_bytes(b"hello")
    b.write_bytes(b"world")
    assert file_hash(a) != file_hash(b)


# ---------------------------------------------------------------------------
# cwd path translation
# ---------------------------------------------------------------------------


_FOREIGN_HOME = "/home/foreign-user"  # Fake path — must not match actual home on any test machine


def test_detect_foreign_home_when_foreign_cwd_then_returns_home_prefix(tmp_path):
    f = tmp_path / "conv.jsonl"
    f.write_text(f'{{"type":"user","cwd":"{_FOREIGN_HOME}/projects/sergei/.worktrees/sw-1"}}\n')
    result = _detect_foreign_home(f)
    assert result == _FOREIGN_HOME


def test_detect_foreign_home_when_local_cwd_then_returns_none(tmp_path):
    f = tmp_path / "conv.jsonl"
    local_home = str(Path.home())
    f.write_text(f'{{"type":"user","cwd":"{local_home}/projects/sergei/.worktrees/sw-1"}}\n')
    result = _detect_foreign_home(f)
    assert result is None


def test_detect_foreign_home_when_no_cwd_then_returns_none(tmp_path):
    f = tmp_path / "conv.jsonl"
    f.write_text('{"type":"custom-title","customTitle":"sw-1"}\n')
    result = _detect_foreign_home(f)
    assert result is None


def test_translate_cwd_paths_replaces_foreign_home():
    content = f'{{"type":"user","cwd":"{_FOREIGN_HOME}/projects/sergei"}}\n'.encode()
    result = translate_cwd_paths(content, _FOREIGN_HOME)
    local_home = str(Path.home()).encode()
    assert _FOREIGN_HOME.encode() not in result
    assert local_home in result


def test_apply_pull_files_translates_cwd_on_new_file(tmp_path):
    """JSONL files from a foreign machine should have cwd paths translated on apply."""
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"

    (staging_dir / "sergei").mkdir(parents=True)
    (staging_dir / "sergei" / "abc123.jsonl").write_text(f'{{"type":"user","cwd":"{_FOREIGN_HOME}/projects/sergei"}}\n')

    with patch("ai_cli.sync._replicate_to_worktrees", return_value=0):
        result = apply_pull_files(
            staging_dir=staging_dir,
            cc_projects_dir=cc_projects_dir,
            local_prefix=_MAC_PREFIX,
            memories_only=False,
            verbose=False,
            dry_run=False,
        )

    dst = cc_projects_dir / "-Users-sergeiwallace-projects-sergei" / "abc123.jsonl"
    assert dst.exists()
    content = dst.read_text()
    assert _FOREIGN_HOME not in content
    assert str(Path.home()) in content
    assert result["applied_count"] == 1


# ---------------------------------------------------------------------------
# detect_jsonl_divergence
# ---------------------------------------------------------------------------


def test_detect_jsonl_divergence_when_identical_then_identical(tmp_path):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    content = b'{"type":"human","text":"hello"}\n'
    a.write_bytes(content)
    b.write_bytes(content)
    assert detect_jsonl_divergence(a, b) == "identical"


def test_detect_jsonl_divergence_when_local_ahead_then_fast_forward_local(tmp_path):
    a = tmp_path / "a.jsonl"  # local
    b = tmp_path / "b.jsonl"  # staging/remote
    base = b'{"type":"human","text":"hello"}\n'
    extended = base + b'{"type":"assistant","text":"hi"}\n'
    a.write_bytes(extended)
    b.write_bytes(base)
    assert detect_jsonl_divergence(a, b) == "fast_forward_local"


def test_detect_jsonl_divergence_when_remote_ahead_then_fast_forward_remote(tmp_path):
    a = tmp_path / "a.jsonl"  # local
    b = tmp_path / "b.jsonl"  # staging/remote
    base = b'{"type":"human","text":"hello"}\n'
    extended = base + b'{"type":"assistant","text":"hi"}\n'
    a.write_bytes(base)
    b.write_bytes(extended)
    assert detect_jsonl_divergence(a, b) == "fast_forward_remote"


def test_detect_jsonl_divergence_when_both_grew_independently_then_diverged(tmp_path):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    a.write_bytes(b'{"type":"human","text":"hello"}\n{"type":"assistant","text":"hi from local"}\n')
    b.write_bytes(b'{"type":"human","text":"hello"}\n{"type":"assistant","text":"hi from server"}\n')
    assert detect_jsonl_divergence(a, b) == "diverged"


def test_detect_jsonl_divergence_when_local_missing_then_fast_forward_remote(tmp_path):
    a = tmp_path / "a.jsonl"  # does not exist
    b = tmp_path / "b.jsonl"
    b.write_bytes(b'{"type":"human","text":"hello"}\n')
    assert detect_jsonl_divergence(a, b) == "fast_forward_remote"


def test_detect_jsonl_divergence_when_staging_missing_then_fast_forward_local(tmp_path):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"  # does not exist
    a.write_bytes(b'{"type":"human","text":"hello"}\n')
    assert detect_jsonl_divergence(a, b) == "fast_forward_local"


def test_detect_jsonl_divergence_when_both_missing_then_identical(tmp_path):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    assert detect_jsonl_divergence(a, b) == "identical"


# ---------------------------------------------------------------------------
# stage_project_files
# ---------------------------------------------------------------------------


def test_stage_project_files_when_mac_project_then_stages_to_bare_name(tmp_path):
    cc_projects_dir = tmp_path / "cc_projects"
    project_dir = cc_projects_dir / "-Users-sergeiwallace-projects-sergei"
    memory_dir = project_dir / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "user_profile.md").write_text("# Profile\nSergei Wallace")
    (project_dir / "abc123.jsonl").write_text('{"type":"human"}\n')

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    result = stage_project_files(
        staging_dir=staging_dir,
        cc_projects_dir=cc_projects_dir,
        local_prefix=_MAC_PREFIX,
        memories_only=False,
        verbose=False,
        dry_run=False,
    )

    assert "sergei" in result["project_names"]
    assert (staging_dir / "sergei" / "memory" / "user_profile.md").exists()
    assert (staging_dir / "sergei" / "abc123.jsonl").exists()
    assert result["memory_count"] == 1
    assert result["jsonl_count"] == 1


def test_stage_project_files_when_memories_only_then_skips_jsonl(tmp_path):
    cc_projects_dir = tmp_path / "cc_projects"
    project_dir = cc_projects_dir / "-Users-sergeiwallace-projects-sergei"
    memory_dir = project_dir / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "user_profile.md").write_text("# Profile")
    (project_dir / "abc123.jsonl").write_text('{"type":"human"}\n')

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    result = stage_project_files(
        staging_dir=staging_dir,
        cc_projects_dir=cc_projects_dir,
        local_prefix=_MAC_PREFIX,
        memories_only=True,
        verbose=False,
        dry_run=False,
    )

    assert (staging_dir / "sergei" / "memory" / "user_profile.md").exists()
    assert not (staging_dir / "sergei" / "abc123.jsonl").exists()
    assert result["memory_count"] == 1
    assert result["jsonl_count"] == 0


def test_stage_project_files_when_tool_results_then_skipped(tmp_path):
    cc_projects_dir = tmp_path / "cc_projects"
    project_dir = cc_projects_dir / "-Users-sergeiwallace-projects-sergei"
    tool_results_dir = project_dir / "tool-results" / "uuid123"
    tool_results_dir.mkdir(parents=True)
    (tool_results_dir / "result.json").write_text("{}")
    (project_dir / "abc123.jsonl").write_text('{"type":"human"}\n')

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    stage_project_files(
        staging_dir=staging_dir,
        cc_projects_dir=cc_projects_dir,
        local_prefix=_MAC_PREFIX,
        memories_only=False,
        verbose=False,
        dry_run=False,
    )

    assert not (staging_dir / "sergei" / "tool-results").exists()
    assert (staging_dir / "sergei" / "abc123.jsonl").exists()


def test_stage_project_files_when_dry_run_then_no_files_written(tmp_path):
    cc_projects_dir = tmp_path / "cc_projects"
    project_dir = cc_projects_dir / "-Users-sergeiwallace-projects-sergei"
    memory_dir = project_dir / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "user_profile.md").write_text("# Profile")

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    result = stage_project_files(
        staging_dir=staging_dir,
        cc_projects_dir=cc_projects_dir,
        local_prefix=_MAC_PREFIX,
        memories_only=False,
        verbose=False,
        dry_run=True,
    )

    assert len(result["staged_files"]) == 1
    assert not (staging_dir / "sergei").exists()


def test_stage_project_files_when_non_matching_prefix_then_skipped(tmp_path):
    cc_projects_dir = tmp_path / "cc_projects"
    # Server-prefixed dir should be ignored when running with _MAC_PREFIX
    project_dir = cc_projects_dir / "-home-sergei-projects-sergei"
    project_dir.mkdir(parents=True)
    (project_dir / "abc.jsonl").write_text('{"type":"human"}\n')

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    result = stage_project_files(
        staging_dir=staging_dir,
        cc_projects_dir=cc_projects_dir,
        local_prefix=_MAC_PREFIX,
        memories_only=False,
        verbose=False,
        dry_run=False,
    )

    assert result["project_names"] == []
    assert result["staged_files"] == []


def test_stage_project_files_when_multiple_projects_then_all_staged(tmp_path):
    cc_projects_dir = tmp_path / "cc_projects"
    for proj in ["sergei", "aurion", "aido"]:
        d = cc_projects_dir / f"-Users-sergeiwallace-projects-{proj}" / "memory"
        d.mkdir(parents=True)
        (d / "MEMORY.md").write_text(f"# {proj}")

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    result = stage_project_files(
        staging_dir=staging_dir,
        cc_projects_dir=cc_projects_dir,
        local_prefix=_MAC_PREFIX,
        memories_only=False,
        verbose=False,
        dry_run=False,
    )

    assert set(result["project_names"]) == {"sergei", "aurion", "aido"}


# ---------------------------------------------------------------------------
# apply_pull_files
# ---------------------------------------------------------------------------


def test_apply_pull_files_when_clean_memory_file_then_applied(tmp_path):
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"
    cc_projects_dir.mkdir()

    (staging_dir / "sergei" / "memory").mkdir(parents=True)
    (staging_dir / "sergei" / "memory" / "user_profile.md").write_text("# Profile\nSergei")

    result = apply_pull_files(
        staging_dir=staging_dir,
        cc_projects_dir=cc_projects_dir,
        local_prefix=_MAC_PREFIX,
        memories_only=False,
        verbose=False,
        dry_run=False,
    )

    assert result["conflicts"] == []
    expected = cc_projects_dir / "-Users-sergeiwallace-projects-sergei" / "memory" / "user_profile.md"
    assert expected.exists()
    assert expected.read_text() == "# Profile\nSergei"


def test_apply_pull_files_when_conflict_markers_then_conflict_file_written(tmp_path):
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"
    cc_projects_dir.mkdir()

    (staging_dir / "sergei" / "memory").mkdir(parents=True)
    conflict_content = "<<<<<<< HEAD\nlocal content\n=======\nremote content\n>>>>>>> origin/main\n"
    (staging_dir / "sergei" / "memory" / "project_current_work.md").write_text(conflict_content)

    result = apply_pull_files(
        staging_dir=staging_dir,
        cc_projects_dir=cc_projects_dir,
        local_prefix=_MAC_PREFIX,
        memories_only=False,
        verbose=False,
        dry_run=False,
    )

    assert len(result["conflicts"]) == 1
    project_dir = cc_projects_dir / "-Users-sergeiwallace-projects-sergei"
    conflict_path = project_dir / "memory" / "project_current_work.md.conflict"
    assert conflict_path.exists()
    # Original file should NOT be written when it has conflict markers
    original_path = project_dir / "memory" / "project_current_work.md"
    assert not original_path.exists()


def test_apply_pull_files_when_jsonl_diverged_then_keep_both(tmp_path):
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"

    (staging_dir / "sergei").mkdir(parents=True)
    (staging_dir / "sergei" / "abc123.jsonl").write_text('{"type":"human","text":"server msg"}\n')

    local_project_dir = cc_projects_dir / "-Users-sergeiwallace-projects-sergei"
    local_project_dir.mkdir(parents=True)
    (local_project_dir / "abc123.jsonl").write_text('{"type":"human","text":"local msg"}\n')

    result = apply_pull_files(
        staging_dir=staging_dir,
        cc_projects_dir=cc_projects_dir,
        local_prefix=_MAC_PREFIX,
        memories_only=False,
        verbose=False,
        dry_run=False,
    )

    assert len(result["conflicts"]) == 1
    # Local file untouched
    assert (local_project_dir / "abc123.jsonl").read_text() == '{"type":"human","text":"local msg"}\n'
    # Remote written as conflict file
    conflict_files = list(local_project_dir.glob("conflict-*.jsonl"))
    assert len(conflict_files) == 1


def test_apply_pull_files_when_prefer_remote_and_diverged_then_overwrites(tmp_path):
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"

    (staging_dir / "sergei").mkdir(parents=True)
    (staging_dir / "sergei" / "abc123.jsonl").write_text('{"type":"human","text":"remote msg"}\n')

    local_project_dir = cc_projects_dir / "-Users-sergeiwallace-projects-sergei"
    local_project_dir.mkdir(parents=True)
    (local_project_dir / "abc123.jsonl").write_text('{"type":"human","text":"local msg"}\n')

    with patch("ai_cli.sync._replicate_to_worktrees", return_value=0):
        result = apply_pull_files(
            staging_dir=staging_dir,
            cc_projects_dir=cc_projects_dir,
            local_prefix=_MAC_PREFIX,
            memories_only=False,
            verbose=False,
            dry_run=False,
            prefer_remote=True,
        )

    assert len(result["conflicts"]) == 0
    assert result["applied_count"] == 1
    # Remote version overwrote local
    assert (local_project_dir / "abc123.jsonl").read_text() == '{"type":"human","text":"remote msg"}\n'
    # No conflict files created
    assert list(local_project_dir.glob("conflict-*.jsonl")) == []


def test_apply_pull_files_when_jsonl_fast_forward_remote_then_applied(tmp_path):
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"

    base = '{"type":"human","text":"hello"}\n'
    extended = base + '{"type":"assistant","text":"hi"}\n'

    (staging_dir / "sergei").mkdir(parents=True)
    (staging_dir / "sergei" / "abc123.jsonl").write_text(extended)

    local_project_dir = cc_projects_dir / "-Users-sergeiwallace-projects-sergei"
    local_project_dir.mkdir(parents=True)
    (local_project_dir / "abc123.jsonl").write_text(base)

    result = apply_pull_files(
        staging_dir=staging_dir,
        cc_projects_dir=cc_projects_dir,
        local_prefix=_MAC_PREFIX,
        memories_only=False,
        verbose=False,
        dry_run=False,
    )

    assert result["conflicts"] == []
    assert (local_project_dir / "abc123.jsonl").read_text() == extended


def test_apply_pull_files_when_jsonl_fast_forward_local_then_no_change(tmp_path):
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"

    base = '{"type":"human","text":"hello"}\n'
    extended = base + '{"type":"assistant","text":"hi"}\n'

    (staging_dir / "sergei").mkdir(parents=True)
    (staging_dir / "sergei" / "abc123.jsonl").write_text(base)

    local_project_dir = cc_projects_dir / "-Users-sergeiwallace-projects-sergei"
    local_project_dir.mkdir(parents=True)
    (local_project_dir / "abc123.jsonl").write_text(extended)

    result = apply_pull_files(
        staging_dir=staging_dir,
        cc_projects_dir=cc_projects_dir,
        local_prefix=_MAC_PREFIX,
        memories_only=False,
        verbose=False,
        dry_run=False,
    )

    assert result["conflicts"] == []
    # Local file should remain as the extended version
    assert (local_project_dir / "abc123.jsonl").read_text() == extended


def test_apply_pull_files_when_jsonl_identical_then_no_action(tmp_path):
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"
    content = '{"type":"human","text":"hello"}\n'

    (staging_dir / "sergei").mkdir(parents=True)
    (staging_dir / "sergei" / "abc123.jsonl").write_text(content)

    local_project_dir = cc_projects_dir / "-Users-sergeiwallace-projects-sergei"
    local_project_dir.mkdir(parents=True)
    (local_project_dir / "abc123.jsonl").write_text(content)

    with patch("ai_cli.sync._replicate_to_worktrees", return_value=0):
        result = apply_pull_files(
            staging_dir=staging_dir,
            cc_projects_dir=cc_projects_dir,
            local_prefix=_MAC_PREFIX,
            memories_only=False,
            verbose=False,
            dry_run=False,
        )

    assert result["conflicts"] == []
    assert result["applied_count"] == 0


def test_apply_pull_files_when_memories_only_then_skips_jsonl(tmp_path):
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"
    cc_projects_dir.mkdir()

    (staging_dir / "sergei" / "memory").mkdir(parents=True)
    (staging_dir / "sergei" / "memory" / "user_profile.md").write_text("# Profile")
    (staging_dir / "sergei" / "abc123.jsonl").write_text('{"type":"human"}\n')

    apply_pull_files(
        staging_dir=staging_dir,
        cc_projects_dir=cc_projects_dir,
        local_prefix=_MAC_PREFIX,
        memories_only=True,
        verbose=False,
        dry_run=False,
    )

    local_project_dir = cc_projects_dir / "-Users-sergeiwallace-projects-sergei"
    assert (local_project_dir / "memory" / "user_profile.md").exists()
    assert not (local_project_dir / "abc123.jsonl").exists()


def test_apply_pull_files_when_dry_run_then_no_files_written(tmp_path):
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"
    cc_projects_dir.mkdir()

    (staging_dir / "sergei" / "memory").mkdir(parents=True)
    (staging_dir / "sergei" / "memory" / "user_profile.md").write_text("# Profile")

    apply_pull_files(
        staging_dir=staging_dir,
        cc_projects_dir=cc_projects_dir,
        local_prefix=_MAC_PREFIX,
        memories_only=False,
        verbose=False,
        dry_run=True,
    )

    local_project_dir = cc_projects_dir / "-Users-sergeiwallace-projects-sergei"
    assert not local_project_dir.exists()


# ---------------------------------------------------------------------------
# _pre_pull_push_memories
# ---------------------------------------------------------------------------


def test_pre_pull_push_memories_when_no_staging_repo_then_does_not_raise(tmp_path, monkeypatch):
    """_pre_pull_push_memories is non-fatal — must not raise even if staging repo is missing."""
    from ai_cli.sync import SyncConfig

    cfg = SyncConfig(
        staging_dir=tmp_path / "nonexistent-staging",
        remote_url="file:///nonexistent/bare.git",
        local_prefix=_SERVER_PREFIX,
        remote_host="localhost",
        source_machine="server",
    )
    cc_projects_dir = tmp_path / "cc_projects"
    cc_projects_dir.mkdir()

    # Should not raise — all errors are swallowed
    _pre_pull_push_memories(cfg, cc_projects_dir, verbose=False)


def test_pre_pull_push_memories_when_memory_changed_then_staged(tmp_path):
    """Local memory edits get staged into the staging repo so git can detect conflicts on pull."""
    import subprocess as _sp

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    _sp.run(["git", "init"], cwd=staging_dir, check=True, capture_output=True)
    _sp.run(["git", "config", "user.email", "test@test.com"], cwd=staging_dir, check=True, capture_output=True)
    _sp.run(["git", "config", "user.name", "Test"], cwd=staging_dir, check=True, capture_output=True)
    (staging_dir / ".gitkeep").write_text("")
    _sp.run(["git", "add", "."], cwd=staging_dir, check=True, capture_output=True)
    _sp.run(
        ["git", "commit", "-m", "init"],
        cwd=staging_dir,
        check=True,
        capture_output=True,
        env={
            **__import__("os").environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )

    # Local CC memory file
    cc_projects_dir = tmp_path / "cc_projects"
    project_dir = cc_projects_dir / "-home-sergei-projects-sergei"
    (project_dir / "memory").mkdir(parents=True)
    (project_dir / "memory" / "project_current_work.md").write_text("---\ntype: project\n---\nserver edits\n")

    from ai_cli.sync import SyncConfig

    cfg = SyncConfig(
        staging_dir=staging_dir,
        remote_url="file:///nonexistent/bare.git",  # push will fail but that's fine
        local_prefix=_SERVER_PREFIX,
        remote_host="localhost",
        source_machine="server",
    )

    _pre_pull_push_memories(cfg, cc_projects_dir, verbose=False)

    # The memory file should have been staged into the staging repo (even if push failed)
    staged = staging_dir / "sergei" / "memory" / "project_current_work.md"
    assert staged.exists(), "memory file should be staged before pull"
    assert "server edits" in staged.read_text()


# ---------------------------------------------------------------------------
# git_commit_staged
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> None:
    """Initialize a git repo with an initial commit for testing."""
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True)
    (path / ".gitkeep").write_text("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path,
        check=True,
        capture_output=True,
        env={
            **__import__("os").environ,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@test.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@test.com",
        },
    )


def test_git_commit_staged_when_changes_then_creates_commit(tmp_path):
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    _init_git_repo(staging_dir)

    (staging_dir / "sergei" / "memory").mkdir(parents=True)
    (staging_dir / "sergei" / "memory" / "user_profile.md").write_text("# Profile")

    committed = git_commit_staged(
        staging_dir=staging_dir,
        source_machine="mac",
        project_names=["sergei"],
        memory_count=1,
        jsonl_count=0,
        total_count=1,
    )

    assert committed is True
    res = subprocess.run(["git", "log", "--oneline", "-1"], cwd=staging_dir, capture_output=True, text=True)
    assert "sync push from mac" in res.stdout


def test_git_commit_staged_when_no_changes_then_returns_false(tmp_path):
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    _init_git_repo(staging_dir)

    committed = git_commit_staged(
        staging_dir=staging_dir,
        source_machine="mac",
        project_names=[],
        memory_count=0,
        jsonl_count=0,
        total_count=0,
    )

    assert committed is False


def test_git_commit_staged_when_already_committed_then_returns_false(tmp_path):
    """Calling git_commit_staged twice with no new changes returns False on the second call."""
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    _init_git_repo(staging_dir)

    (staging_dir / "sergei" / "memory").mkdir(parents=True)
    (staging_dir / "sergei" / "memory" / "user_profile.md").write_text("# Profile")

    git_commit_staged(
        staging_dir=staging_dir,
        source_machine="mac",
        project_names=["sergei"],
        memory_count=1,
        jsonl_count=0,
        total_count=1,
    )

    # Second call with no new changes — nothing to commit
    committed = git_commit_staged(
        staging_dir=staging_dir,
        source_machine="mac",
        project_names=["sergei"],
        memory_count=1,
        jsonl_count=0,
        total_count=1,
    )

    assert committed is False


def test_git_commit_staged_commit_message_contains_metadata(tmp_path):
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    _init_git_repo(staging_dir)

    (staging_dir / "sergei" / "memory").mkdir(parents=True)
    (staging_dir / "sergei" / "memory" / "user_profile.md").write_text("# Profile")
    (staging_dir / "aurion" / "memory").mkdir(parents=True)
    (staging_dir / "aurion" / "memory" / "MEMORY.md").write_text("# Aurion")

    git_commit_staged(
        staging_dir=staging_dir,
        source_machine="server",
        project_names=["sergei", "aurion"],
        memory_count=2,
        jsonl_count=0,
        total_count=2,
    )

    res = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=staging_dir, capture_output=True, text=True)
    msg = res.stdout
    assert "sync push from server" in msg
    assert "sergei" in msg
    assert "aurion" in msg
    assert "memories: 2" in msg


# ---------------------------------------------------------------------------
# is_cc_active_on_server
# ---------------------------------------------------------------------------


def test_is_cc_active_on_server_when_pgrep_returns_0_then_true():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        assert is_cc_active_on_server("root@1.2.3.4") is True


def test_is_cc_active_on_server_when_pgrep_returns_1_then_false():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        assert is_cc_active_on_server("root@1.2.3.4") is False


def test_is_cc_active_locally_when_pgrep_returns_0_then_true():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        assert is_cc_active_locally() is True


def test_is_cc_active_locally_when_pgrep_returns_1_then_false():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        assert is_cc_active_locally() is False


# ---------------------------------------------------------------------------
# notify_conflicts
# ---------------------------------------------------------------------------


def test_notify_conflicts_when_called_then_appends_to_log(tmp_path):
    log_path = tmp_path / ".claude-sync-conflicts.log"
    with patch("subprocess.run"), patch("ai_cli.sync.CONFLICT_LOG", log_path):
        notify_conflicts(["memory sergei/memory/file.md — .conflict file written"])

    log_content = log_path.read_text()
    assert "CONFLICT" in log_content
    assert "sergei/memory/file.md" in log_content


def test_notify_conflicts_when_many_conflicts_then_truncates_notification(tmp_path):
    log_path = tmp_path / "conflicts.log"
    conflicts = ["a", "b", "c", "d", "e"]
    with (
        patch("subprocess.run") as mock_run,
        patch("ai_cli.sync.CONFLICT_LOG", log_path),
        patch("ai_cli.sync._is_mac", return_value=True),
    ):
        notify_conflicts(conflicts)

    # Find the osascript call
    osascript_calls = [c for c in mock_run.call_args_list if c[0][0][0] == "osascript"]
    assert len(osascript_calls) == 1
    script_arg = osascript_calls[0][0][0][-1]
    assert "+2 more" in script_arg


def test_notify_conflicts_when_exactly_three_then_no_truncation(tmp_path):
    log_path = tmp_path / "conflicts.log"
    conflicts = ["a", "b", "c"]
    with (
        patch("subprocess.run") as mock_run,
        patch("ai_cli.sync.CONFLICT_LOG", log_path),
        patch("ai_cli.sync._is_mac", return_value=True),
    ):
        notify_conflicts(conflicts)

    osascript_calls = [c for c in mock_run.call_args_list if c[0][0][0] == "osascript"]
    script_arg = osascript_calls[0][0][0][-1]
    assert "more" not in script_arg


def test_notify_conflicts_when_appends_multiple_entries_then_all_logged(tmp_path):
    log_path = tmp_path / "conflicts.log"
    with patch("subprocess.run"), patch("ai_cli.sync.CONFLICT_LOG", log_path):
        notify_conflicts(["conflict1 — note", "conflict2 — note"])

    lines = [l for l in log_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 2


# ---------------------------------------------------------------------------
# stage_handoff_files
# ---------------------------------------------------------------------------


def test_stage_handoff_files_when_pending_exists_then_stages_files(tmp_path):
    queue_dir = tmp_path / "queue"
    (queue_dir / "pending").mkdir(parents=True)
    (queue_dir / "pending" / "001-do-something.md").write_text("handoff content")
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    count = stage_handoff_files(staging_dir, queue_dir, verbose=False, dry_run=False)

    assert count == 1
    staged = staging_dir / "--handoff-queue" / "pending" / "001-do-something.md"
    assert staged.exists()
    assert staged.read_text() == "handoff content"


def test_stage_handoff_files_when_no_pending_dir_then_returns_zero(tmp_path):
    queue_dir = tmp_path / "queue"
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    count = stage_handoff_files(staging_dir, queue_dir, verbose=False, dry_run=False)

    assert count == 0


def test_stage_handoff_files_when_dry_run_then_no_files_written(tmp_path):
    queue_dir = tmp_path / "queue"
    (queue_dir / "pending").mkdir(parents=True)
    (queue_dir / "pending" / "001-task.md").write_text("content")
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    count = stage_handoff_files(staging_dir, queue_dir, verbose=False, dry_run=True)

    assert count == 1
    assert not (staging_dir / "--handoff-queue" / "pending" / "001-task.md").exists()


# ---------------------------------------------------------------------------
# apply_handoff_files
# ---------------------------------------------------------------------------


def test_apply_handoff_files_when_new_pending_then_applies(tmp_path):
    staging_dir = tmp_path / "staging"
    staging_pending = staging_dir / "--handoff-queue" / "pending"
    staging_pending.mkdir(parents=True)
    (staging_pending / "001-task.md").write_text("handoff from remote")
    queue_dir = tmp_path / "queue"

    count = apply_handoff_files(staging_dir, queue_dir, verbose=False, dry_run=False)

    assert count == 1
    assert (queue_dir / "pending" / "001-task.md").read_text() == "handoff from remote"


def test_apply_handoff_files_when_already_pending_then_skips(tmp_path):
    staging_dir = tmp_path / "staging"
    staging_pending = staging_dir / "--handoff-queue" / "pending"
    staging_pending.mkdir(parents=True)
    (staging_pending / "001-task.md").write_text("remote content")
    queue_dir = tmp_path / "queue"
    (queue_dir / "pending").mkdir(parents=True)
    (queue_dir / "pending" / "001-task.md").write_text("local content")

    count = apply_handoff_files(staging_dir, queue_dir, verbose=False, dry_run=False)

    assert count == 0
    assert (queue_dir / "pending" / "001-task.md").read_text() == "local content"


def test_apply_handoff_files_when_already_claimed_then_skips(tmp_path):
    staging_dir = tmp_path / "staging"
    staging_pending = staging_dir / "--handoff-queue" / "pending"
    staging_pending.mkdir(parents=True)
    (staging_pending / "001-task.md").write_text("remote content")
    queue_dir = tmp_path / "queue"
    (queue_dir / "claimed").mkdir(parents=True)
    (queue_dir / "claimed" / "001-task.md").write_text("claimed content")

    count = apply_handoff_files(staging_dir, queue_dir, verbose=False, dry_run=False)

    assert count == 0
    assert not (queue_dir / "pending" / "001-task.md").exists()


def test_apply_handoff_files_when_dry_run_then_no_files_written(tmp_path):
    staging_dir = tmp_path / "staging"
    staging_pending = staging_dir / "--handoff-queue" / "pending"
    staging_pending.mkdir(parents=True)
    (staging_pending / "001-task.md").write_text("content")
    queue_dir = tmp_path / "queue"

    count = apply_handoff_files(staging_dir, queue_dir, verbose=False, dry_run=True)

    assert count == 1
    assert not (queue_dir / "pending" / "001-task.md").exists()


def test_apply_handoff_files_when_no_staging_namespace_then_returns_zero(tmp_path):
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    queue_dir = tmp_path / "queue"

    count = apply_handoff_files(staging_dir, queue_dir, verbose=False, dry_run=False)

    assert count == 0


# ---------------------------------------------------------------------------
# sync_watch
# ---------------------------------------------------------------------------


def test_push_to_remote_when_stale_rebase_merge_then_aborts_before_rebase(tmp_path):
    """_push_to_remote aborts stale rebase-merge dir before attempting pull --rebase."""
    from unittest.mock import patch
    from ai_cli.sync import _push_to_remote

    # Simulate stale rebase-merge directory
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    rebase_merge = git_dir / "rebase-merge"
    rebase_merge.mkdir()

    run_calls = []

    def fake_run(cmd, **kwargs):
        run_calls.append(cmd)
        result = type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()
        if cmd[:2] == ["git", "push"] and len(run_calls) == 1:
            result.returncode = 1
            result.stderr = "rejected (non-fast-forward)"
        return result

    with patch("ai_cli.sync.subprocess.run", side_effect=fake_run):
        _push_to_remote(tmp_path, verbose=False)

    assert ["git", "rebase", "--abort"] in run_calls
    abort_idx = run_calls.index(["git", "rebase", "--abort"])
    rebase_idx = next(i for i, c in enumerate(run_calls) if c == ["git", "pull", "--rebase", "origin", "main"])
    assert abort_idx < rebase_idx


def test_push_to_remote_when_no_stale_rebase_then_skips_abort(tmp_path):
    """_push_to_remote does not run git rebase --abort when no stale state exists."""
    from unittest.mock import patch
    from ai_cli.sync import _push_to_remote

    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    # No rebase-merge or rebase-apply dirs

    run_calls = []

    def fake_run(cmd, **kwargs):
        run_calls.append(cmd)
        result = type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()
        if cmd[:2] == ["git", "push"] and len(run_calls) == 1:
            result.returncode = 1
            result.stderr = "rejected (non-fast-forward)"
        return result

    with patch("ai_cli.sync.subprocess.run", side_effect=fake_run):
        _push_to_remote(tmp_path, verbose=False)

    assert ["git", "rebase", "--abort"] not in run_calls


def test_sync_watch_when_nats_unavailable_returns_nonzero():
    """sync_watch exits 1 when NATS is not reachable."""
    from unittest.mock import AsyncMock, patch
    from nats.errors import NoServersError
    from ai_cli.sync import sync_watch

    with patch("ai_cli.sync._acquire_pid_file", return_value=True):
        with patch("ai_cli.sync._release_pid_file"):
            with patch("nats.connect", new=AsyncMock(side_effect=NoServersError)):
                with patch("asyncio.sleep", new=AsyncMock()):
                    result = sync_watch([])

    assert result == 1


def test_sync_watch_when_nats_available_runs_pull_on_message(tmp_path):
    """sync_watch subscribes and calls sync_pull when a message arrives."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from ai_cli.sync import sync_watch

    mock_nc = MagicMock()
    received_cb = {}

    async def fake_subscribe(subject, cb):
        received_cb["cb"] = cb
        # Simulate one message then cancel
        msg = MagicMock()
        msg.data = b'{"machine": "mac"}'
        await cb(msg)

    mock_nc.subscribe = fake_subscribe

    pull_calls = []

    def fake_sync_pull(flags):
        pull_calls.append(flags)
        return 0

    with patch("ai_cli.sync._acquire_pid_file", return_value=True):
        with patch("ai_cli.sync._release_pid_file"):
            with patch("nats.connect", new=AsyncMock(return_value=mock_nc)):
                with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
                    with patch("ai_cli.sync.sync_pull", side_effect=fake_sync_pull):
                        result = sync_watch([])

    assert result == 0
    assert pull_calls == [["--force"]]


# ---------------------------------------------------------------------------
# get_source_machine
# ---------------------------------------------------------------------------


def test_get_source_machine_when_linux_then_returns_server():
    with patch("ai_cli.sync._is_mac", return_value=False):
        assert get_source_machine() == "server"


def test_get_source_machine_when_mac_then_returns_mac():
    with patch("ai_cli.sync._is_mac", return_value=True):
        assert get_source_machine() == "mac"


# ---------------------------------------------------------------------------
# _default_remote_bare_url
# ---------------------------------------------------------------------------


def test_default_remote_bare_url_when_user_at_host_then_uses_home():
    result = _default_remote_bare_url("sergei@server.com")
    assert result == "ssh://sergei@server.com/home/sergei/.claude-sync-staging.git"


def test_default_remote_bare_url_when_root_at_host_then_uses_root():
    result = _default_remote_bare_url("root@server.com")
    assert result == "ssh://root@server.com/root/.claude-sync-staging.git"


def test_default_remote_bare_url_when_no_user_then_uses_root():
    result = _default_remote_bare_url("server.com")
    assert result == "ssh://server.com/root/.claude-sync-staging.git"


# ---------------------------------------------------------------------------
# _parse_flags
# ---------------------------------------------------------------------------


def test_parse_flags_when_all_flags_then_all_true():
    result = _parse_flags(["--memories-only", "--dry-run", "--verbose", "--force", "--prefer-remote"])
    assert result == (True, True, True, True, True)


def test_parse_flags_when_no_flags_then_all_false():
    result = _parse_flags([])
    assert result == (False, False, False, False, False)


def test_parse_flags_when_partial_flags_then_mixed():
    result = _parse_flags(["--verbose", "--force"])
    assert result == (False, False, True, True, False)


# ---------------------------------------------------------------------------
# load_sync_config
# ---------------------------------------------------------------------------


def test_load_sync_config_when_remote_host_set_then_uses_it():
    config = {
        "sync": {"remote_host": "user@myhost"},
        "remote": {},
    }
    with patch("ai_cli.main.load_config", return_value=config):
        cfg = load_sync_config()
    assert cfg.remote_host == "user@myhost"
    assert isinstance(cfg, SyncConfig)


def test_load_sync_config_when_no_sync_host_then_derives_from_remote():
    config = {
        "sync": {},
        "remote": {"host": "1.2.3.4", "user": "ubuntu"},
    }
    with patch("ai_cli.main.load_config", return_value=config):
        cfg = load_sync_config()
    assert cfg.remote_host == "ubuntu@1.2.3.4"


def test_load_sync_config_when_no_host_at_all_then_exits():
    config = {"sync": {}, "remote": {}}
    with patch("ai_cli.main.load_config", return_value=config):
        import pytest

        with pytest.raises(SystemExit):
            load_sync_config()


def test_load_sync_config_when_server_then_uses_file_url():
    config = {
        "sync": {"remote_host": "user@host"},
        "remote": {},
    }
    with patch("ai_cli.main.load_config", return_value=config):
        with patch("ai_cli.sync._is_mac", return_value=False):
            cfg = load_sync_config()
    assert cfg.remote_url.startswith("file://")
    assert cfg.source_machine == "server"


def test_load_sync_config_when_mac_then_uses_ssh_url():
    config = {
        "sync": {"remote_host": "user@host"},
        "remote": {},
    }
    with patch("ai_cli.main.load_config", return_value=config):
        with patch("ai_cli.sync._is_mac", return_value=True):
            cfg = load_sync_config()
    assert cfg.remote_url.startswith("ssh://")
    assert cfg.source_machine == "mac"


# ---------------------------------------------------------------------------
# _handoff_queue_dir
# ---------------------------------------------------------------------------


def test_handoff_queue_dir_when_main_dir_then_returns_path():
    with patch("ai_cli.main._get_main_project_dir", return_value=Path("/home/u/projects/sergei")):
        result = _handoff_queue_dir()
    assert result == Path("/home/u/projects/sergei/.handoff-queue")


# ---------------------------------------------------------------------------
# init_staging_repo
# ---------------------------------------------------------------------------


def test_init_staging_repo_when_already_initialized_then_noop(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / ".git").mkdir()

    with patch("ai_cli.sync._git") as mock_git:
        mock_git.return_value = MagicMock(returncode=0)
        init_staging_repo(staging, "ssh://host/repo.git")
    # Should check remote, not re-init
    assert any("get-url" in str(c) for c in mock_git.call_args_list)


def test_init_staging_repo_when_clone_succeeds_then_done(tmp_path):
    staging = tmp_path / "staging"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        init_staging_repo(staging, "ssh://host/repo.git")
    mock_run.assert_called_once()
    assert "clone" in mock_run.call_args[0][0]


def test_init_staging_repo_when_clone_fails_then_inits_fresh(tmp_path):
    staging = tmp_path / "staging"

    clone_result = MagicMock(returncode=1)

    with patch("subprocess.run", return_value=clone_result):
        with patch("ai_cli.sync._git") as mock_git:
            init_staging_repo(staging, "ssh://host/repo.git")
    # Should have called git init, add, commit
    assert any("init" in str(c) for c in mock_git.call_args_list)
    assert any("commit" in str(c) for c in mock_git.call_args_list)


# ---------------------------------------------------------------------------
# _detect_foreign_home_in_history
# ---------------------------------------------------------------------------


def test_detect_foreign_home_in_history_when_foreign_then_detects(tmp_path):
    history = tmp_path / "history.jsonl"
    history.write_text('{"project":"/Users/otheruser/projects/myapp","sessionId":"abc"}\n')

    with patch("pathlib.Path.home", return_value=tmp_path):
        result = _detect_foreign_home_in_history(history)
    assert result == "/Users/otheruser"


def test_detect_foreign_home_in_history_when_local_then_none(tmp_path):
    history = tmp_path / "history.jsonl"
    local_home = str(tmp_path)
    history.write_text(f'{{"project":"{local_home}/projects/myapp","sessionId":"abc"}}\n')

    with patch("pathlib.Path.home", return_value=tmp_path):
        result = _detect_foreign_home_in_history(history)
    assert result is None


def test_detect_foreign_home_in_history_when_no_project_field_then_none(tmp_path):
    history = tmp_path / "history.jsonl"
    history.write_text('{"sessionId":"abc","title":"My Session"}\n')

    with patch("pathlib.Path.home", return_value=tmp_path):
        result = _detect_foreign_home_in_history(history)
    assert result is None


# ---------------------------------------------------------------------------
# _write_jsonl_translated
# ---------------------------------------------------------------------------


def test_write_jsonl_translated_when_foreign_home_then_translates(tmp_path):
    src = tmp_path / "src.jsonl"
    dst = tmp_path / "dst.jsonl"
    foreign = "/Users/otheruser"
    src.write_text(f'{{"cwd":"{foreign}/projects/app"}}\n')

    with patch("pathlib.Path.home", return_value=tmp_path):
        _write_jsonl_translated(src, dst)

    content = dst.read_text()
    assert foreign not in content
    assert str(tmp_path) in content


def test_write_jsonl_translated_when_no_foreign_then_copies(tmp_path):
    src = tmp_path / "src.jsonl"
    dst = tmp_path / "dst.jsonl"
    local_home = str(tmp_path)
    src.write_text(f'{{"cwd":"{local_home}/projects/app"}}\n')

    with patch("pathlib.Path.home", return_value=tmp_path):
        _write_jsonl_translated(src, dst)

    assert dst.read_text() == src.read_text()


# ---------------------------------------------------------------------------
# _get_remote_home
# ---------------------------------------------------------------------------


def test_get_remote_home_when_user_at_host_then_home():
    from ai_cli.sync import _get_remote_home

    assert _get_remote_home("sergei@server") == "/home/sergei"


def test_get_remote_home_when_root_at_host_then_root():
    from ai_cli.sync import _get_remote_home

    assert _get_remote_home("root@server") == "/root"


def test_get_remote_home_when_no_at_then_root():
    from ai_cli.sync import _get_remote_home

    assert _get_remote_home("server") == "/root"


# ---------------------------------------------------------------------------
# _translate_settings_paths
# ---------------------------------------------------------------------------


def test_translate_settings_paths_to_staging(tmp_path):
    from ai_cli.sync import _translate_settings_paths

    local_home = str(tmp_path)
    content = f'{{"path": "{local_home}/projects/foo"}}'
    with patch("pathlib.Path.home", return_value=tmp_path):
        result = _translate_settings_paths(content, "to_staging", "sergei@host")
    assert "/home/sergei/projects/foo" in result


def test_translate_settings_paths_to_local(tmp_path):
    from ai_cli.sync import _translate_settings_paths

    content = '{"path": "/home/sergei/projects/foo"}'
    with patch("pathlib.Path.home", return_value=tmp_path):
        result = _translate_settings_paths(content, "to_local", "sergei@host")
    assert str(tmp_path) in result


def test_translate_settings_paths_when_same_home_then_noop(tmp_path):
    from ai_cli.sync import _translate_settings_paths

    # When local and remote are the same, no translation
    content = '{"path": "/home/sergei/projects/foo"}'
    with patch("pathlib.Path.home", return_value=Path("/home/sergei")):
        result = _translate_settings_paths(content, "to_staging", "sergei@host")
    assert result == content


# ---------------------------------------------------------------------------
# stage_config_files
# ---------------------------------------------------------------------------


def test_stage_config_files_when_files_exist_then_stages(tmp_path):
    from ai_cli.sync import stage_config_files

    cc_dir = tmp_path / ".claude"
    cc_dir.mkdir()
    (cc_dir / "statusline-command.sh").write_text("#!/bin/bash\n")
    (cc_dir / "settings.json").write_text('{"key": "value"}')

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    with patch("pathlib.Path.home", return_value=tmp_path):
        count = stage_config_files(staging_dir, verbose=False, dry_run=False, remote_host="user@host")
    assert count >= 1


def test_stage_config_files_when_dry_run_then_no_files(tmp_path):
    from ai_cli.sync import stage_config_files

    cc_dir = tmp_path / ".claude"
    cc_dir.mkdir()
    (cc_dir / "statusline-command.sh").write_text("#!/bin/bash\n")

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    with patch("pathlib.Path.home", return_value=tmp_path):
        count = stage_config_files(staging_dir, verbose=False, dry_run=True)
    assert count >= 1
    # No files should be written in dry_run
    assert not (staging_dir / "--config").exists()


# ---------------------------------------------------------------------------
# apply_config_files
# ---------------------------------------------------------------------------


def test_apply_config_files_when_staging_has_files_then_applies(tmp_path):
    from ai_cli.sync import apply_config_files

    staging_dir = tmp_path / "staging"
    config_dir = staging_dir / "--config"
    config_dir.mkdir(parents=True)
    (config_dir / "statusline-command.sh").write_text("#!/bin/bash\necho status")
    (config_dir / "settings.json").write_text('{"key": "value"}')

    cc_dir = tmp_path / ".claude"
    cc_dir.mkdir()

    with patch("pathlib.Path.home", return_value=tmp_path):
        count = apply_config_files(staging_dir, verbose=False, dry_run=False, remote_host="user@host")
    assert count >= 1
    assert (cc_dir / "statusline-command.sh").exists()


def test_apply_config_files_when_no_staging_then_returns_zero(tmp_path):
    from ai_cli.sync import apply_config_files

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    with patch("pathlib.Path.home", return_value=tmp_path):
        count = apply_config_files(staging_dir, verbose=False, dry_run=False)
    assert count == 0


# ---------------------------------------------------------------------------
# _detect_foreign_home edge cases
# ---------------------------------------------------------------------------


def test_detect_foreign_home_when_invalid_json_then_skips_line(tmp_path):
    f = tmp_path / "conv.jsonl"
    f.write_text('{"cwd": invalid json}\n{"cwd": "/Users/other/projects/x"}\n')

    with patch("pathlib.Path.home", return_value=tmp_path):
        result = _detect_foreign_home(f)
    assert result == "/Users/other"


def test_detect_foreign_home_when_exception_then_returns_none(tmp_path):
    # Non-existent file
    f = tmp_path / "nonexistent.jsonl"
    result = _detect_foreign_home(f)
    assert result is None


# ---------------------------------------------------------------------------
# init_server_bare_repo
# ---------------------------------------------------------------------------


def test_init_server_bare_repo_runs_ssh():
    from ai_cli.sync import init_server_bare_repo

    with patch("subprocess.run") as mock_run:
        init_server_bare_repo("user@host")
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "ssh" in cmd
    assert "user@host" in cmd


# ---------------------------------------------------------------------------
# is_cc_active detection
# ---------------------------------------------------------------------------


def test_is_cc_active_on_server_when_running_then_true():
    with patch("subprocess.run", return_value=MagicMock(returncode=0)):
        assert is_cc_active_on_server("user@host") is True


def test_is_cc_active_on_server_when_not_running_then_false():
    with patch("subprocess.run", return_value=MagicMock(returncode=1)):
        assert is_cc_active_on_server("user@host") is False


def test_is_cc_active_locally_when_running_then_true():
    with patch("subprocess.run", return_value=MagicMock(returncode=0)):
        assert is_cc_active_locally() is True


def test_is_cc_active_locally_when_not_running_then_false():
    with patch("subprocess.run", return_value=MagicMock(returncode=1)):
        assert is_cc_active_locally() is False


# ---------------------------------------------------------------------------
# notify_conflicts
# ---------------------------------------------------------------------------


def test_notify_conflicts_when_conflicts_exist_then_writes_log(tmp_path):
    conflicts = ["project1/memory/MEMORY.md"]
    with patch("ai_cli.sync.CONFLICT_LOG", tmp_path / "conflicts.log"):
        with patch("ai_cli.sync._is_mac", return_value=False):
            notify_conflicts(conflicts)
    assert (tmp_path / "conflicts.log").exists()
    content = (tmp_path / "conflicts.log").read_text()
    assert "project1" in content


def test_notify_conflicts_when_empty_then_noop():
    with patch("ai_cli.sync.CONFLICT_LOG", Path("/tmp/nonexistent-test-log.log")):
        notify_conflicts([])  # Should not raise


# ---------------------------------------------------------------------------
# sync_push integration
# ---------------------------------------------------------------------------


def test_sync_push_when_dry_run_then_reports_files(tmp_path, capsys):
    from ai_cli.sync import sync_push

    cc_projects_dir = tmp_path / ".claude" / "projects"
    project_dir = cc_projects_dir / "-home-sergei-projects-myapp"
    memory_dir = project_dir / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("# Memory")

    staging_dir = tmp_path / "staging"
    cfg = SyncConfig(
        staging_dir=staging_dir,
        remote_url="ssh://user@host/repo.git",
        local_prefix=_SERVER_PREFIX,
        remote_host="user@host",
        source_machine="server",
    )

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_projects_dir):
            with patch("ai_cli.sync._handoff_queue_dir", return_value=tmp_path / ".handoff-queue"):
                result = sync_push(["--dry-run"])

    assert result == 0
    output = capsys.readouterr().out
    assert "Would sync" in output


def test_sync_push_when_no_cc_dir_then_returns_1(tmp_path):
    from ai_cli.sync import sync_push

    cfg = SyncConfig(
        staging_dir=tmp_path / "staging",
        remote_url="ssh://user@host/repo.git",
        local_prefix=_SERVER_PREFIX,
        remote_host="user@host",
        source_machine="server",
    )

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.init_server_bare_repo"):
            with patch("ai_cli.sync.init_staging_repo"):
                with patch("ai_cli.sync.is_cc_active_on_server", return_value=False):
                    with patch("ai_cli.sync._wait_for_dream_completion"):
                        with patch("ai_cli.sync._cc_projects_dir", return_value=tmp_path / "nonexistent"):
                            result = sync_push([])
    assert result == 1


def test_sync_push_when_cc_active_then_aborts(tmp_path):
    from ai_cli.sync import sync_push

    cc_projects_dir = tmp_path / ".claude" / "projects"
    cc_projects_dir.mkdir(parents=True)

    cfg = SyncConfig(
        staging_dir=tmp_path / "staging",
        remote_url="ssh://user@host/repo.git",
        local_prefix=_SERVER_PREFIX,
        remote_host="user@host",
        source_machine="server",
    )

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.init_server_bare_repo"):
            with patch("ai_cli.sync.init_staging_repo"):
                with patch("ai_cli.sync.is_cc_active_on_server", return_value=True):
                    result = sync_push([])
    assert result == 1


def test_sync_push_when_success_then_pushes(tmp_path):
    from ai_cli.sync import sync_push

    cc_projects_dir = tmp_path / ".claude" / "projects"
    project_dir = cc_projects_dir / "-home-sergei-projects-myapp"
    memory_dir = project_dir / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("# Memory")

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    cfg = SyncConfig(
        staging_dir=staging_dir,
        remote_url="ssh://user@host/repo.git",
        local_prefix=_SERVER_PREFIX,
        remote_host="user@host",
        source_machine="server",
    )

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.init_server_bare_repo"):
            with patch("ai_cli.sync.init_staging_repo"):
                with patch("ai_cli.sync.is_cc_active_on_server", return_value=False):
                    with patch("ai_cli.sync._wait_for_dream_completion"):
                        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_projects_dir):
                            with patch("ai_cli.sync.git_commit_staged", return_value=True):
                                with patch("ai_cli.sync._push_to_remote", return_value=True):
                                    with patch("ai_cli.sync._handoff_queue_dir", return_value=tmp_path / "hq"):
                                        with patch("ai_cli.sync.stage_config_files", return_value=0):
                                            with patch("ai_cli.messaging.NATSClient", side_effect=Exception("no nats")):
                                                result = sync_push([])
    assert result == 0


# ---------------------------------------------------------------------------
# sync_pull integration
# ---------------------------------------------------------------------------


def test_sync_pull_when_dry_run_then_succeeds(tmp_path):
    from ai_cli.sync import sync_pull

    staging_dir = tmp_path / "staging"
    (staging_dir / "myapp" / "memory").mkdir(parents=True)
    (staging_dir / "myapp" / "memory" / "MEMORY.md").write_text("# Remote memory")

    cc_projects_dir = tmp_path / ".claude" / "projects"

    cfg = SyncConfig(
        staging_dir=staging_dir,
        remote_url="ssh://user@host/repo.git",
        local_prefix=_SERVER_PREFIX,
        remote_host="user@host",
        source_machine="server",
    )

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_projects_dir):
            with patch("ai_cli.sync.is_cc_active_locally", return_value=False):
                with patch("ai_cli.sync._handoff_queue_dir", return_value=tmp_path / "hq"):
                    result = sync_pull(["--dry-run"])

    assert result == 0


def test_sync_pull_when_config_error_then_returns_1():
    from ai_cli.sync import sync_pull

    with patch("ai_cli.sync.load_sync_config", side_effect=RuntimeError("broken")):
        result = sync_pull([])
    assert result == 1


def test_sync_pull_when_full_run_then_applies_and_translates(tmp_path):
    from ai_cli.sync import sync_pull

    staging_dir = tmp_path / "staging"
    (staging_dir / "myapp" / "memory").mkdir(parents=True)
    (staging_dir / "myapp" / "memory" / "MEMORY.md").write_text("# Remote memory")

    cc_projects_dir = tmp_path / ".claude" / "projects"

    cfg = SyncConfig(
        staging_dir=staging_dir,
        remote_url="file:///tmp/test.git",
        local_prefix=_SERVER_PREFIX,
        remote_host="user@host",
        source_machine="server",
    )

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.is_cc_active_locally", return_value=False):
            with patch("ai_cli.sync.init_staging_repo"):
                with patch("ai_cli.sync._pre_pull_push_memories"):
                    with patch("subprocess.run", return_value=MagicMock(returncode=0)):
                        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_projects_dir):
                            with patch("ai_cli.sync._handoff_queue_dir", return_value=tmp_path / "hq"):
                                with patch("ai_cli.sync.translate_history_jsonl", return_value=0):
                                    with patch("ai_cli.sync.retranslate_project_jsonls", return_value=0):
                                        with patch("ai_cli.sync.replicate_history_to_worktrees", return_value=0):
                                            result = sync_pull(["--force"])

    assert result == 0


def test_sync_push_when_config_error_then_returns_1():
    from ai_cli.sync import sync_push

    with patch("ai_cli.sync.load_sync_config", side_effect=RuntimeError("broken")):
        result = sync_push([])
    assert result == 1


def test_sync_push_when_nothing_to_commit_then_returns_0(tmp_path):
    from ai_cli.sync import sync_push

    cc_projects_dir = tmp_path / ".claude" / "projects"
    cc_projects_dir.mkdir(parents=True)

    cfg = SyncConfig(
        staging_dir=tmp_path / "staging",
        remote_url="ssh://user@host/repo.git",
        local_prefix=_SERVER_PREFIX,
        remote_host="user@host",
        source_machine="server",
    )

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.init_server_bare_repo"):
            with patch("ai_cli.sync.init_staging_repo"):
                with patch("ai_cli.sync.is_cc_active_on_server", return_value=False):
                    with patch("ai_cli.sync._wait_for_dream_completion"):
                        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_projects_dir):
                            with patch("ai_cli.sync.git_commit_staged", return_value=False):
                                with patch("ai_cli.sync._handoff_queue_dir", return_value=tmp_path / "hq"):
                                    with patch("ai_cli.sync.stage_config_files", return_value=0):
                                        result = sync_push([])
    assert result == 0


def test_sync_push_when_push_fails_then_returns_1(tmp_path):
    from ai_cli.sync import sync_push

    cc_projects_dir = tmp_path / ".claude" / "projects"
    cc_projects_dir.mkdir(parents=True)

    cfg = SyncConfig(
        staging_dir=tmp_path / "staging",
        remote_url="ssh://user@host/repo.git",
        local_prefix=_SERVER_PREFIX,
        remote_host="user@host",
        source_machine="server",
    )

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.init_server_bare_repo"):
            with patch("ai_cli.sync.init_staging_repo"):
                with patch("ai_cli.sync.is_cc_active_on_server", return_value=False):
                    with patch("ai_cli.sync._wait_for_dream_completion"):
                        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_projects_dir):
                            with patch("ai_cli.sync.git_commit_staged", return_value=True):
                                with patch("ai_cli.sync._push_to_remote", return_value=False):
                                    with patch("ai_cli.sync._handoff_queue_dir", return_value=tmp_path / "hq"):
                                        with patch("ai_cli.sync.stage_config_files", return_value=0):
                                            result = sync_push([])
    assert result == 1


# ---------------------------------------------------------------------------
# sync_conflicts
# ---------------------------------------------------------------------------


def test_sync_conflicts_when_no_conflicts_then_returns_0(tmp_path, capsys):
    from ai_cli.sync import sync_conflicts

    cc_dir = tmp_path / ".claude" / "projects"
    cc_dir.mkdir(parents=True)

    with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
        with patch("ai_cli.sync.CONFLICT_LOG", tmp_path / "nonexistent.log"):
            result = sync_conflicts([])
    assert result == 0
    assert "No unresolved" in capsys.readouterr().out


def test_sync_conflicts_when_conflict_files_exist_then_returns_2(tmp_path, capsys):
    from ai_cli.sync import sync_conflicts

    cc_dir = tmp_path / ".claude" / "projects"
    proj_dir = cc_dir / "myproject" / "memory"
    proj_dir.mkdir(parents=True)
    (proj_dir / "MEMORY.md.conflict").write_text("conflict content")

    log_file = tmp_path / "conflicts.log"
    log_file.write_text("2026-03-31T12:00:00 CONFLICT myproject/memory/MEMORY.md\n")

    with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
        with patch("ai_cli.sync.CONFLICT_LOG", log_file):
            result = sync_conflicts([])
    assert result == 2
    output = capsys.readouterr().out
    assert "MEMORY.md.conflict" in output
    assert "Recent conflict log" in output


# ---------------------------------------------------------------------------
# _push_to_remote
# ---------------------------------------------------------------------------


def test_push_to_remote_when_success_then_true(tmp_path):
    from ai_cli.sync import _push_to_remote

    with patch("subprocess.run", return_value=MagicMock(returncode=0)):
        assert _push_to_remote(tmp_path, verbose=False) is True


def test_push_to_remote_when_rejected_and_rebase_fails_then_false(tmp_path):
    from ai_cli.sync import _push_to_remote

    def mock_run(cmd, **kwargs):
        m = MagicMock()
        if "push" in cmd:
            m.returncode = 1
            m.stderr = "non-fast-forward rejected"
        elif "pull" in cmd:
            m.returncode = 1
            m.stderr = "rebase failed"
        else:
            m.returncode = 0
        return m

    with patch("subprocess.run", side_effect=mock_run):
        assert _push_to_remote(tmp_path, verbose=False) is False


def test_push_to_remote_when_rejected_and_rebase_succeeds_then_retries(tmp_path):
    from ai_cli.sync import _push_to_remote

    call_count = {"push": 0}

    def mock_run(cmd, **kwargs):
        m = MagicMock()
        if "push" in cmd:
            call_count["push"] += 1
            if call_count["push"] == 1:
                m.returncode = 1
                m.stderr = "non-fast-forward"
            else:
                m.returncode = 0
        elif "pull" in cmd:
            m.returncode = 0
        else:
            m.returncode = 0
        m.stdout = ""
        return m

    with patch("subprocess.run", side_effect=mock_run):
        assert _push_to_remote(tmp_path, verbose=False) is True
    assert call_count["push"] == 2
