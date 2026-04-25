import re
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from ai_cli.sync import (
    normalize_project_path,
    denormalize_project_name,
    get_local_prefix,
    get_source_machine,
    _default_remote_bare_url,
    _parse_flags,
    load_sync_config,
    detect_jsonl_divergence,
    should_sync_file,
    is_memory_file,
    is_jsonl_file,
    file_hash,
    stage_project_files,
    apply_pull_files,
    git_commit_staged,
    is_cc_active_on_server,
    is_cc_active_locally,
    notify_conflicts,
    _detect_foreign_home,
    translate_cwd_paths,
    init_staging_repo,
    _detect_foreign_home_in_history,
    _write_jsonl_translated,
    SyncConfig,
    _find_project_worktrees,
    _replicate_to_worktrees,
    replicate_history_to_worktrees,
    purge_phantom_history_entries,
    retranslate_project_jsonls,
    clean_worktree_cc_dirs,
    repair_worktree_cc_dir,
    sync_watch,
    sync_push,
    sync_pull,
    _push_to_remote,
    _remote_newer_files,
    _release_pid_file,
    _wait_for_dream_completion,
    _pre_pull_push_memories,
)

# Fixed prefix strings for tests — mirror what get_local_prefix() would return on each platform
_MAC_PREFIX = "-Users-user-projects-"
_SERVER_PREFIX = "-home-user-projects-"


# ---------------------------------------------------------------------------
# normalize_project_path
# ---------------------------------------------------------------------------


def test_normalize_project_path_when_mac_prefix_then_returns_bare_name():
    assert normalize_project_path("-Users-user-projects-myproject", _MAC_PREFIX) == "myproject"


def test_normalize_project_path_when_worktree_suffix_then_preserves_it():
    result = normalize_project_path("-Users-user-projects-myproject--worktrees-sw-1", _MAC_PREFIX)
    assert result == "myproject--worktrees-sw-1"


def test_normalize_project_path_when_server_prefix_then_returns_bare_name():
    assert normalize_project_path("-home-user-projects-myproject", _SERVER_PREFIX) == "myproject"


def test_normalize_project_path_when_no_match_then_returns_none():
    assert normalize_project_path("-home-user-projects-myproject", _MAC_PREFIX) is None


def test_normalize_project_path_when_different_project_then_correct():
    assert normalize_project_path("-Users-user-projects-aurion", _MAC_PREFIX) == "aurion"


def test_normalize_project_path_when_server_worktree_then_preserves_suffix():
    result = normalize_project_path("-home-user-projects-myproject--worktrees-sw-2", _SERVER_PREFIX)
    assert result == "myproject--worktrees-sw-2"


# ---------------------------------------------------------------------------
# denormalize_project_name
# ---------------------------------------------------------------------------


def test_denormalize_project_name_when_bare_name_then_returns_mac_cc_dir():
    assert denormalize_project_name("myproject", _MAC_PREFIX) == "-Users-user-projects-myproject"


def test_denormalize_project_name_when_worktree_suffix_then_preserves_it():
    result = denormalize_project_name("myproject--worktrees-sw-1", _MAC_PREFIX)
    assert result == "-Users-user-projects-myproject--worktrees-sw-1"


def test_denormalize_project_name_when_server_prefix_then_correct():
    result = denormalize_project_name("aurion", _SERVER_PREFIX)
    assert result == "-home-user-projects-aurion"


def test_denormalize_normalize_roundtrip():
    cc_dir = "-Users-user-projects-myproject--worktrees-sw-3"
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
    assert is_memory_file(Path("myproject/memory/project_current_work.md")) is True


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


def test_should_sync_file_when_memory_lock_file_then_false():
    # Lock files in memory/ dirs are machine-local state and must not cross machines.
    assert should_sync_file(Path("memory/.consolidate-lock"), memories_only=False) is False


def test_should_sync_file_when_memory_non_md_file_then_false():
    assert should_sync_file(Path("memory/scratch.txt"), memories_only=False) is False


def test_should_sync_file_when_memory_md_file_and_not_memories_only_then_true():
    assert should_sync_file(Path("memory/user_profile.md"), memories_only=False) is True


# ---------------------------------------------------------------------------
# translate_history_jsonl
# ---------------------------------------------------------------------------


def test_translate_history_jsonl_when_foreign_paths_then_replaces(tmp_path, monkeypatch):
    from ai_cli.sync import translate_history_jsonl

    history_path = tmp_path / ".claude" / "history.jsonl"
    history_path.parent.mkdir()
    mac_home = "/Users/user"
    history_path.write_text(
        '{"project":"/Users/user/projects/myproject","sessionId":"abc"}\n'
        '{"project":"/Users/user/projects/aurion","sessionId":"def"}\n'
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
    history_path.write_text(f'{{"project":"{local_home}/projects/myproject","sessionId":"abc"}}\n')

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
    proj_dir = projects_dir / "-home-user-projects-mytools"
    proj_dir.mkdir(parents=True)
    mac_home = "/Users/user"
    conv = proj_dir / "abc123.jsonl"
    conv.write_text('{"type":"summary","cwd":"/Users/user/projects/mytools"}\n{"type":"user","content":"hello"}\n')

    with patch("pathlib.Path.home", return_value=tmp_path):
        count = retranslate_project_jsonls()

    assert count == 1
    content = conv.read_text()
    assert mac_home not in content
    assert str(tmp_path) in content


def test_retranslate_project_jsonls_when_foreign_project_field_then_translates(tmp_path):
    from ai_cli.sync import retranslate_project_jsonls

    projects_dir = tmp_path / ".claude" / "projects"
    proj_dir = projects_dir / "-home-user-projects-myproject"
    proj_dir.mkdir(parents=True)
    mac_home = "/Users/user"
    conv = proj_dir / "def456.jsonl"
    # File with no cwd field, only project field
    conv.write_text('{"type":"session","project":"/Users/user/projects/myproject"}\n{"type":"user","content":"hi"}\n')

    with patch("pathlib.Path.home", return_value=tmp_path):
        count = retranslate_project_jsonls()

    assert count == 1
    content = conv.read_text()
    assert mac_home not in content
    assert str(tmp_path) in content


def test_retranslate_project_jsonls_when_already_translated_then_noop(tmp_path):
    from ai_cli.sync import retranslate_project_jsonls

    projects_dir = tmp_path / ".claude" / "projects"
    proj_dir = projects_dir / "-home-user-projects-mytools"
    proj_dir.mkdir(parents=True)
    conv = proj_dir / "abc123.jsonl"
    local_home = str(tmp_path)
    conv.write_text(f'{{"type":"summary","cwd":"{local_home}/projects/mytools"}}\n')

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
    f.write_text(f'{{"type":"user","cwd":"{_FOREIGN_HOME}/projects/myproject/.worktrees/sw-1"}}\n')
    result = _detect_foreign_home(f)
    assert result == _FOREIGN_HOME


def test_detect_foreign_home_when_local_cwd_then_returns_none(tmp_path):
    f = tmp_path / "conv.jsonl"
    local_home = str(Path.home())
    f.write_text(f'{{"type":"user","cwd":"{local_home}/projects/myproject/.worktrees/sw-1"}}\n')
    result = _detect_foreign_home(f)
    assert result is None


def test_detect_foreign_home_when_no_cwd_then_returns_none(tmp_path):
    f = tmp_path / "conv.jsonl"
    f.write_text('{"type":"custom-title","customTitle":"sw-1"}\n')
    result = _detect_foreign_home(f)
    assert result is None


def test_translate_cwd_paths_replaces_foreign_home():
    content = f'{{"type":"user","cwd":"{_FOREIGN_HOME}/projects/myproject"}}\n'.encode()
    result = translate_cwd_paths(content, _FOREIGN_HOME)
    local_home = str(Path.home()).encode()
    assert _FOREIGN_HOME.encode() not in result
    assert local_home in result


def test_apply_pull_files_translates_cwd_on_new_file(tmp_path):
    """JSONL files from a foreign machine should have cwd paths translated on apply."""
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"

    (staging_dir / "myproject").mkdir(parents=True)
    (staging_dir / "myproject" / "abc123.jsonl").write_text(
        f'{{"type":"user","cwd":"{_FOREIGN_HOME}/projects/myproject"}}\n'
    )

    with patch("ai_cli.sync._replicate_to_worktrees", return_value=0):
        result = apply_pull_files(
            staging_dir=staging_dir,
            cc_projects_dir=cc_projects_dir,
            local_prefix=_MAC_PREFIX,
            memories_only=False,
            verbose=False,
            dry_run=False,
        )

    dst = cc_projects_dir / "-Users-user-projects-myproject" / "abc123.jsonl"
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
    project_dir = cc_projects_dir / "-Users-user-projects-myproject"
    memory_dir = project_dir / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "user_profile.md").write_text("# Profile\nExample User")
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

    assert "myproject" in result["project_names"]
    assert (staging_dir / "myproject" / "memory" / "user_profile.md").exists()
    assert (staging_dir / "myproject" / "abc123.jsonl").exists()
    assert result["memory_count"] == 1
    assert result["jsonl_count"] == 1


def test_stage_project_files_when_memories_only_then_skips_jsonl(tmp_path):
    cc_projects_dir = tmp_path / "cc_projects"
    project_dir = cc_projects_dir / "-Users-user-projects-myproject"
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

    assert (staging_dir / "myproject" / "memory" / "user_profile.md").exists()
    assert not (staging_dir / "myproject" / "abc123.jsonl").exists()
    assert result["memory_count"] == 1
    assert result["jsonl_count"] == 0


def test_stage_project_files_when_tool_results_then_skipped(tmp_path):
    cc_projects_dir = tmp_path / "cc_projects"
    project_dir = cc_projects_dir / "-Users-user-projects-myproject"
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

    assert not (staging_dir / "myproject" / "tool-results").exists()
    assert (staging_dir / "myproject" / "abc123.jsonl").exists()


def test_stage_project_files_when_dry_run_then_no_files_written(tmp_path):
    cc_projects_dir = tmp_path / "cc_projects"
    project_dir = cc_projects_dir / "-Users-user-projects-myproject"
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
    assert not (staging_dir / "myproject").exists()


def test_stage_project_files_when_non_matching_prefix_then_skipped(tmp_path):
    cc_projects_dir = tmp_path / "cc_projects"
    # Server-prefixed dir should be ignored when running with _MAC_PREFIX
    project_dir = cc_projects_dir / "-home-user-projects-myproject"
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
    for proj in ["myproject", "aurion", "mytools"]:
        d = cc_projects_dir / f"-Users-user-projects-{proj}" / "memory"
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

    assert set(result["project_names"]) == {"myproject", "aurion", "mytools"}


def test_stage_project_files_when_worktree_cc_dir_then_stages_with_bare_name(tmp_path):
    """Worktree CC dirs (containing '--worktrees-') are staged like any other CC dir.

    The prefix strip removes the machine-local home prefix; the '--worktrees-' suffix
    stays in the bare name so the staging dir has the full worktree identifier.
    """
    cc_projects_dir = tmp_path / "cc_projects"
    wt_dir = cc_projects_dir / "-home-user-projects-ai-cli-utils--worktrees-ai-cli-1"
    wt_dir.mkdir(parents=True)
    (wt_dir / "abc123.jsonl").write_text(
        '{"cwd":"/home/user/projects/ai-cli-utils/.worktrees/ai-cli-1","customTitle":"ai-cli-1"}\n'
    )

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    result = stage_project_files(
        staging_dir=staging_dir,
        cc_projects_dir=cc_projects_dir,
        local_prefix=_SERVER_PREFIX,
        memories_only=False,
        verbose=False,
        dry_run=False,
    )

    assert "ai-cli-utils--worktrees-ai-cli-1" in result["project_names"]
    staged = staging_dir / "ai-cli-utils--worktrees-ai-cli-1" / "abc123.jsonl"
    assert staged.exists()


def test_stage_project_files_when_worktree_cc_dir_multiple_machines_then_both_staged(tmp_path):
    """Both main project and worktree CC dirs from the same project get staged."""
    cc_projects_dir = tmp_path / "cc_projects"
    for name in [
        "-home-user-projects-ai-cli-utils",
        "-home-user-projects-ai-cli-utils--worktrees-ai-cli-1",
    ]:
        d = cc_projects_dir / name
        d.mkdir(parents=True)
        (d / "conv.jsonl").write_text('{"cwd":"/home/user/projects/ai-cli-utils"}\n')

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    result = stage_project_files(
        staging_dir=staging_dir,
        cc_projects_dir=cc_projects_dir,
        local_prefix=_SERVER_PREFIX,
        memories_only=False,
        verbose=False,
        dry_run=False,
    )

    assert "ai-cli-utils" in result["project_names"]
    assert "ai-cli-utils--worktrees-ai-cli-1" in result["project_names"]


def test_stage_project_files_when_file_unchanged_then_not_staged(tmp_path):
    """Unchanged files (same hash in staging and CC dir) are skipped to avoid git blob churn."""
    cc_projects_dir = tmp_path / "cc_projects"
    project_dir = cc_projects_dir / "-Users-user-projects-myproject"
    project_dir.mkdir(parents=True)
    (project_dir / "session.jsonl").write_text('{"type":"human"}\n')

    staging_dir = tmp_path / "staging"
    staged_project = staging_dir / "myproject"
    staged_project.mkdir(parents=True)
    # Pre-populate staging with identical content
    (staged_project / "session.jsonl").write_text('{"type":"human"}\n')

    result = stage_project_files(
        staging_dir=staging_dir,
        cc_projects_dir=cc_projects_dir,
        local_prefix=_MAC_PREFIX,
        memories_only=False,
        verbose=False,
        dry_run=False,
    )

    # Unchanged file must not appear in staged_files (no git blob created)
    assert result["staged_files"] == []


def test_stage_project_files_when_file_changed_then_staged(tmp_path):
    """Modified files (different hash) are staged even when they exist in staging."""
    cc_projects_dir = tmp_path / "cc_projects"
    project_dir = cc_projects_dir / "-Users-user-projects-myproject"
    project_dir.mkdir(parents=True)
    (project_dir / "session.jsonl").write_text('{"type":"human"}\n{"type":"assistant"}\n')

    staging_dir = tmp_path / "staging"
    staged_project = staging_dir / "myproject"
    staged_project.mkdir(parents=True)
    # Staging has older content
    (staged_project / "session.jsonl").write_text('{"type":"human"}\n')

    result = stage_project_files(
        staging_dir=staging_dir,
        cc_projects_dir=cc_projects_dir,
        local_prefix=_MAC_PREFIX,
        memories_only=False,
        verbose=False,
        dry_run=False,
    )

    assert len(result["staged_files"]) == 1
    # Staging file must be updated
    assert (staged_project / "session.jsonl").read_text() == '{"type":"human"}\n{"type":"assistant"}\n'


# ---------------------------------------------------------------------------
# apply_pull_files — worktree CC dir cross-machine sync
# ---------------------------------------------------------------------------


def test_apply_pull_files_when_worktree_staged_then_creates_correct_cc_dir(tmp_path):
    """Applying a staged worktree CC dir creates the correct machine-local CC dir name.

    When Hetzner pushes 'ai-cli-utils--worktrees-ai-cli-1' and Mac pulls,
    it should land in '-Users-user-projects-ai-cli-utils--worktrees-ai-cli-1'.
    """
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"
    staged_wt = staging_dir / "ai-cli-utils--worktrees-ai-cli-1"
    staged_wt.mkdir(parents=True)
    (staged_wt / "abc123.jsonl").write_text(
        '{"cwd":"/home/user/projects/ai-cli-utils/.worktrees/ai-cli-1","customTitle":"ai-cli-1"}\n'
    )

    with patch("ai_cli.sync._replicate_to_worktrees", return_value=0):
        result = apply_pull_files(
            staging_dir=staging_dir,
            cc_projects_dir=cc_projects_dir,
            local_prefix=_MAC_PREFIX,
            memories_only=False,
            verbose=False,
            dry_run=False,
        )

    expected_dir = cc_projects_dir / "-Users-user-projects-ai-cli-utils--worktrees-ai-cli-1"
    assert expected_dir.is_dir()
    assert result["applied_count"] == 1


def test_apply_pull_files_when_worktree_jsonl_then_translates_cwd(tmp_path):
    """JSONL files in worktree CC dirs have foreign home paths translated on apply.

    Uses _FOREIGN_HOME (/home/foreign-user) as the simulated remote machine's home
    so the test passes on any CI runner regardless of its own home path.
    """
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"
    staged_wt = staging_dir / "myproject--worktrees-sw-1"
    staged_wt.mkdir(parents=True)
    (staged_wt / "conv.jsonl").write_text(
        f'{{"cwd":"{_FOREIGN_HOME}/projects/myproject/.worktrees/sw-1","customTitle":"sw-1"}}\n'
    )

    with patch("ai_cli.sync._replicate_to_worktrees", return_value=0):
        apply_pull_files(
            staging_dir=staging_dir,
            cc_projects_dir=cc_projects_dir,
            local_prefix=_MAC_PREFIX,
            memories_only=False,
            verbose=False,
            dry_run=False,
        )

    dst = cc_projects_dir / "-Users-user-projects-myproject--worktrees-sw-1" / "conv.jsonl"
    assert dst.exists()
    content = dst.read_text()
    assert _FOREIGN_HOME not in content
    assert str(Path.home()) in content


def test_apply_pull_files_worktree_cc_dir_end_to_end_roundtrip(tmp_path):
    """Full roundtrip: foreign machine stages worktree CC dir, local machine applies with correct names and cwd."""
    _foreign_prefix = re.sub(r"[^a-zA-Z0-9]", "-", _FOREIGN_HOME) + "-projects-"

    # Remote has worktree CC dir with conversation
    remote_cc = tmp_path / "remote_cc"
    wt_cc = remote_cc / f"{_foreign_prefix}foo--worktrees-sw-2"
    wt_cc.mkdir(parents=True)
    (wt_cc / "session.jsonl").write_text(
        f'{{"cwd":"{_FOREIGN_HOME}/projects/foo/.worktrees/sw-2","customTitle":"sw-2","type":"user"}}\n'
    )

    # Remote stages
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    stage_project_files(
        staging_dir=staging_dir,
        cc_projects_dir=remote_cc,
        local_prefix=_foreign_prefix,
        memories_only=False,
        verbose=False,
        dry_run=False,
    )
    assert (staging_dir / "foo--worktrees-sw-2" / "session.jsonl").exists()

    # Local machine applies with MAC_PREFIX
    local_cc = tmp_path / "local_cc"
    local_cc.mkdir()
    with patch("ai_cli.sync._replicate_to_worktrees", return_value=0):
        apply_pull_files(
            staging_dir=staging_dir,
            cc_projects_dir=local_cc,
            local_prefix=_MAC_PREFIX,
            memories_only=False,
            verbose=False,
            dry_run=False,
        )

    mac_wt_dir = local_cc / "-Users-user-projects-foo--worktrees-sw-2"
    assert mac_wt_dir.is_dir()
    applied = (mac_wt_dir / "session.jsonl").read_text()
    assert _FOREIGN_HOME not in applied
    assert str(Path.home()) in applied


# ---------------------------------------------------------------------------
# apply_pull_files
# ---------------------------------------------------------------------------


def test_apply_pull_files_when_clean_memory_file_then_applied(tmp_path):
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"
    cc_projects_dir.mkdir()

    (staging_dir / "myproject" / "memory").mkdir(parents=True)
    (staging_dir / "myproject" / "memory" / "user_profile.md").write_text("# Profile\nUser")

    result = apply_pull_files(
        staging_dir=staging_dir,
        cc_projects_dir=cc_projects_dir,
        local_prefix=_MAC_PREFIX,
        memories_only=False,
        verbose=False,
        dry_run=False,
    )

    assert result["conflicts"] == []
    expected = cc_projects_dir / "-Users-user-projects-myproject" / "memory" / "user_profile.md"
    assert expected.exists()
    assert expected.read_text() == "# Profile\nUser"


def test_apply_pull_files_when_conflict_markers_then_conflict_file_written(tmp_path):
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"
    conflict_dir = tmp_path / "conflicts"
    cc_projects_dir.mkdir()

    (staging_dir / "myproject" / "memory").mkdir(parents=True)
    conflict_content = "<<<<<<< HEAD\nlocal content\n=======\nremote content\n>>>>>>> origin/main\n"
    (staging_dir / "myproject" / "memory" / "project_current_work.md").write_text(conflict_content)

    with patch("ai_cli.sync.CONFLICT_DIR", conflict_dir):
        result = apply_pull_files(
            staging_dir=staging_dir,
            cc_projects_dir=cc_projects_dir,
            local_prefix=_MAC_PREFIX,
            memories_only=False,
            verbose=False,
            dry_run=False,
        )

    assert len(result["conflicts"]) == 1
    # Conflict file goes to CONFLICT_DIR / bare_name (staging name), not the CC project dir
    conflict_path = conflict_dir / "myproject" / "memory" / "project_current_work.md.conflict"
    assert conflict_path.exists()
    # Original file should NOT be written when it has conflict markers
    project_dir = cc_projects_dir / "-Users-user-projects-myproject"
    original_path = project_dir / "memory" / "project_current_work.md"
    assert not original_path.exists()


def test_apply_pull_files_when_jsonl_diverged_then_keep_both(tmp_path):
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"
    conflict_dir = tmp_path / "conflicts"

    (staging_dir / "myproject").mkdir(parents=True)
    (staging_dir / "myproject" / "abc123.jsonl").write_text('{"type":"human","text":"server msg"}\n')

    local_project_dir = cc_projects_dir / "-Users-user-projects-myproject"
    local_project_dir.mkdir(parents=True)
    (local_project_dir / "abc123.jsonl").write_text('{"type":"human","text":"local msg"}\n')

    with patch("ai_cli.sync.CONFLICT_DIR", conflict_dir):
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
    # Remote version saved in conflict dir / bare_name (staging name), not the CC project dir
    conflict_files = list((conflict_dir / "myproject").glob("conflict-*.jsonl"))
    assert len(conflict_files) == 1
    assert not list(local_project_dir.glob("conflict-*.jsonl"))


def test_apply_pull_files_when_prefer_remote_and_diverged_then_overwrites(tmp_path):
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"

    (staging_dir / "myproject").mkdir(parents=True)
    (staging_dir / "myproject" / "abc123.jsonl").write_text('{"type":"human","text":"remote msg"}\n')

    local_project_dir = cc_projects_dir / "-Users-user-projects-myproject"
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

    (staging_dir / "myproject").mkdir(parents=True)
    (staging_dir / "myproject" / "abc123.jsonl").write_text(extended)

    local_project_dir = cc_projects_dir / "-Users-user-projects-myproject"
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

    (staging_dir / "myproject").mkdir(parents=True)
    (staging_dir / "myproject" / "abc123.jsonl").write_text(base)

    local_project_dir = cc_projects_dir / "-Users-user-projects-myproject"
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

    (staging_dir / "myproject").mkdir(parents=True)
    (staging_dir / "myproject" / "abc123.jsonl").write_text(content)

    local_project_dir = cc_projects_dir / "-Users-user-projects-myproject"
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

    (staging_dir / "myproject" / "memory").mkdir(parents=True)
    (staging_dir / "myproject" / "memory" / "user_profile.md").write_text("# Profile")
    (staging_dir / "myproject" / "abc123.jsonl").write_text('{"type":"human"}\n')

    apply_pull_files(
        staging_dir=staging_dir,
        cc_projects_dir=cc_projects_dir,
        local_prefix=_MAC_PREFIX,
        memories_only=True,
        verbose=False,
        dry_run=False,
    )

    local_project_dir = cc_projects_dir / "-Users-user-projects-myproject"
    assert (local_project_dir / "memory" / "user_profile.md").exists()
    assert not (local_project_dir / "abc123.jsonl").exists()


def test_apply_pull_files_when_dry_run_then_no_files_written(tmp_path):
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"
    cc_projects_dir.mkdir()

    (staging_dir / "myproject" / "memory").mkdir(parents=True)
    (staging_dir / "myproject" / "memory" / "user_profile.md").write_text("# Profile")

    apply_pull_files(
        staging_dir=staging_dir,
        cc_projects_dir=cc_projects_dir,
        local_prefix=_MAC_PREFIX,
        memories_only=False,
        verbose=False,
        dry_run=True,
    )

    local_project_dir = cc_projects_dir / "-Users-user-projects-myproject"
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
    project_dir = cc_projects_dir / "-home-user-projects-myproject"
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
    staged = staging_dir / "myproject" / "memory" / "project_current_work.md"
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

    (staging_dir / "myproject" / "memory").mkdir(parents=True)
    (staging_dir / "myproject" / "memory" / "user_profile.md").write_text("# Profile")

    committed = git_commit_staged(
        staging_dir=staging_dir,
        source_machine="mac",
        project_names=["myproject"],
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

    (staging_dir / "myproject" / "memory").mkdir(parents=True)
    (staging_dir / "myproject" / "memory" / "user_profile.md").write_text("# Profile")

    git_commit_staged(
        staging_dir=staging_dir,
        source_machine="mac",
        project_names=["myproject"],
        memory_count=1,
        jsonl_count=0,
        total_count=1,
    )

    # Second call with no new changes — nothing to commit
    committed = git_commit_staged(
        staging_dir=staging_dir,
        source_machine="mac",
        project_names=["myproject"],
        memory_count=1,
        jsonl_count=0,
        total_count=1,
    )

    assert committed is False


def test_git_commit_staged_commit_message_contains_metadata(tmp_path):
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    _init_git_repo(staging_dir)

    (staging_dir / "myproject" / "memory").mkdir(parents=True)
    (staging_dir / "myproject" / "memory" / "user_profile.md").write_text("# Profile")
    (staging_dir / "aurion" / "memory").mkdir(parents=True)
    (staging_dir / "aurion" / "memory" / "MEMORY.md").write_text("# Aurion")

    git_commit_staged(
        staging_dir=staging_dir,
        source_machine="server",
        project_names=["myproject", "aurion"],
        memory_count=2,
        jsonl_count=0,
        total_count=2,
    )

    res = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=staging_dir, capture_output=True, text=True)
    msg = res.stdout
    assert "sync push from server" in msg
    assert "myproject" in msg
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
        notify_conflicts(["memory myproject/memory/file.md — .conflict file written"])

    log_content = log_path.read_text()
    assert "CONFLICT" in log_content
    assert "myproject/memory/file.md" in log_content


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
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


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
    result = _default_remote_bare_url("user@server.com")
    assert result == "ssh://user@server.com/home/user/.claude-sync-staging.git"


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
    with patch("ai_cli.config.load_config", return_value=config):
        cfg = load_sync_config()
    assert cfg.remote_host == "user@myhost"
    assert isinstance(cfg, SyncConfig)


def test_load_sync_config_when_no_sync_host_then_derives_from_remote():
    config = {
        "sync": {},
        "remote": {"host": "1.2.3.4", "user": "ubuntu"},
    }
    with patch("ai_cli.config.load_config", return_value=config):
        cfg = load_sync_config()
    assert cfg.remote_host == "ubuntu@1.2.3.4"


def test_load_sync_config_when_no_host_at_all_then_exits():
    config = {"sync": {}, "remote": {}}
    with patch("ai_cli.config.load_config", return_value=config):
        import pytest

        with pytest.raises(SystemExit):
            load_sync_config()


def test_load_sync_config_when_server_then_uses_file_url():
    config = {
        "sync": {"remote_host": "user@host"},
        "remote": {},
    }
    with patch("ai_cli.config.load_config", return_value=config):
        with patch("ai_cli.sync._is_mac", return_value=False):
            cfg = load_sync_config()
    assert cfg.remote_url.startswith("file://")
    assert cfg.source_machine == "server"


def test_load_sync_config_when_mac_then_uses_ssh_url():
    config = {
        "sync": {"remote_host": "user@host"},
        "remote": {},
    }
    with patch("ai_cli.config.load_config", return_value=config):
        with patch("ai_cli.sync._is_mac", return_value=True):
            cfg = load_sync_config()
    assert cfg.remote_url.startswith("ssh://")
    assert cfg.source_machine == "mac"


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
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


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
    project_dir = cc_projects_dir / "-home-user-projects-myapp"
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
    project_dir = cc_projects_dir / "-home-user-projects-myapp"
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
                            with patch("ai_cli.sync.translate_history_jsonl", return_value=0):
                                with patch("ai_cli.sync.retranslate_project_jsonls", return_value=0):
                                    with patch("ai_cli.sync.purge_phantom_history_entries", return_value=0):
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
                                with patch("ai_cli.sync._remote_newer_files", return_value=[]):
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
                                    with patch("ai_cli.sync._remote_newer_files", return_value=[]):
                                        result = sync_push([])
    assert result == 1


# ---------------------------------------------------------------------------
# sync_conflicts
# ---------------------------------------------------------------------------


def test_sync_conflicts_when_no_conflicts_then_returns_0(tmp_path, capsys):
    from ai_cli.sync import sync_conflicts

    cc_dir = tmp_path / ".claude" / "projects"
    cc_dir.mkdir(parents=True)
    conflict_dir = tmp_path / "conflicts"

    with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
        with patch("ai_cli.sync.CONFLICT_LOG", tmp_path / "nonexistent.log"):
            with patch("ai_cli.sync.CONFLICT_DIR", conflict_dir):
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


# ---------------------------------------------------------------------------
# Coverage gap tests: sync.py error branches
# ---------------------------------------------------------------------------


def test_detect_jsonl_divergence_when_foreign_home_detected_then_translates(tmp_path):
    """Covers line 316: translate_cwd_paths called on staging bytes."""
    local = tmp_path / "local.jsonl"
    staging = tmp_path / "staging.jsonl"
    local_home = str(Path.home())
    foreign_home = "/Users/foreign"
    # Local has local_home path, staging has foreign path
    local.write_text(f'{{"cwd":"{local_home}/projects/test"}}\n')
    staging.write_text(f'{{"cwd":"{foreign_home}/projects/test"}}\n')

    with patch("ai_cli.sync._detect_foreign_home", return_value=foreign_home):
        with patch("ai_cli.sync.translate_cwd_paths", return_value=local.read_bytes()):
            result = detect_jsonl_divergence(local, staging)
    assert result == "identical"


def test_init_staging_repo_when_remote_not_set_then_adds_remote(tmp_path):
    """Covers line 347: remote add when get-url fails."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / ".git").mkdir()

    calls = []

    def mock_git(cmd, cwd=None, check=True, **kwargs):
        calls.append(cmd)
        m = MagicMock()
        if "get-url" in cmd:
            m.returncode = 1  # no remote set
        else:
            m.returncode = 0
        return m

    with patch("ai_cli.sync._git", side_effect=mock_git):
        init_staging_repo(staging, "git@server:repo.git")
    add_calls = [c for c in calls if "add" in c and "origin" in c]
    assert len(add_calls) == 1


def test_stage_project_files_when_cc_dir_not_exists_then_returns_empty(tmp_path):
    """Covers lines 406-412: cc_projects_dir doesn't exist."""
    result = stage_project_files(
        staging_dir=tmp_path / "staging",
        cc_projects_dir=tmp_path / "nonexistent",
        local_prefix=_SERVER_PREFIX,
        memories_only=False,
        verbose=False,
        dry_run=False,
    )
    assert result["staged_files"] == []
    assert result["memory_count"] == 0


def test_stage_project_files_when_non_dir_entry_then_skips(tmp_path):
    """Covers line 416: non-directory entry in cc_projects_dir."""
    cc_dir = tmp_path / "cc"
    cc_dir.mkdir()
    # Create a file instead of directory
    (cc_dir / "somefile.txt").write_text("not a dir")

    result = stage_project_files(
        staging_dir=tmp_path / "staging",
        cc_projects_dir=cc_dir,
        local_prefix=_SERVER_PREFIX,
        memories_only=False,
        verbose=False,
        dry_run=False,
    )
    assert result["staged_files"] == []


def test_stage_project_files_when_verbose_then_prints(tmp_path, capsys):
    """Covers line 444: verbose output during staging."""
    cc_dir = tmp_path / "cc"
    proj_dir = cc_dir / f"{_SERVER_PREFIX}testproj"
    memory_dir = proj_dir / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("# Memory")

    staging = tmp_path / "staging"

    result = stage_project_files(
        staging_dir=staging,
        cc_projects_dir=cc_dir,
        local_prefix=_SERVER_PREFIX,
        memories_only=True,
        verbose=True,
        dry_run=False,
    )
    assert result["memory_count"] >= 1
    output = capsys.readouterr().out
    assert "stage:" in output


def test_detect_foreign_home_in_history_when_exception_then_returns_none(tmp_path):
    """Covers lines 522-525: exception reading history."""
    # Create a file that will cause parse issues
    history = tmp_path / "history.jsonl"
    history.write_text("")

    result = _detect_foreign_home_in_history(history)
    assert result is None


def test_translate_history_jsonl_when_no_changes_then_returns_zero(tmp_path):
    """Covers line 552: updated == content (no replacements needed)."""
    from ai_cli.sync import translate_history_jsonl

    history = tmp_path / ".claude" / "history.jsonl"
    history.parent.mkdir(parents=True)
    local_home = str(Path.home())
    history.write_text(f'{{"project": "{local_home}/projects/test"}}\n')

    with patch("pathlib.Path.home", return_value=tmp_path):
        with patch("ai_cli.sync._detect_foreign_home_in_history", return_value="/Users/other"):
            result = translate_history_jsonl(verbose=False)
    # File doesn't contain /Users/other, so no replacements
    assert result == 0


def test_translate_history_jsonl_when_verbose_then_prints(tmp_path, capsys):
    """Covers line 557: verbose output."""
    from ai_cli.sync import translate_history_jsonl

    history = tmp_path / ".claude" / "history.jsonl"
    history.parent.mkdir(parents=True)
    foreign_home = "/Users/foreign"
    history.write_text(f'{{"project": "{foreign_home}/projects/test"}}\n')

    with patch("pathlib.Path.home", return_value=tmp_path):
        with patch("ai_cli.sync._detect_foreign_home_in_history", return_value=foreign_home):
            result = translate_history_jsonl(verbose=True)
    assert result >= 1
    output = capsys.readouterr().out
    assert "translate history" in output


def test_replicate_history_to_worktrees_when_no_history_then_returns_zero():
    """Covers line 580: history.jsonl doesn't exist."""
    from ai_cli.sync import replicate_history_to_worktrees

    with patch("pathlib.Path.home", return_value=Path("/tmp/nonexistent")):
        result = replicate_history_to_worktrees(verbose=False)
    assert result == 0


def test_replicate_history_to_worktrees_when_worktrees_exist_then_adds_entries(tmp_path):
    """Covers lines 577-637: full replicate path with worktrees."""
    import json as _json
    from ai_cli.sync import replicate_history_to_worktrees

    # Setup history
    history = tmp_path / ".claude" / "history.jsonl"
    history.parent.mkdir(parents=True)
    projects_base = tmp_path / "projects"
    main_cwd = str(projects_base / "myapp")
    entry = _json.dumps({"project": main_cwd, "sessionId": "s1"})
    history.write_text(entry + "\n")

    # Setup worktree
    wt_dir = projects_base / "myapp" / ".worktrees" / "wt-1"
    wt_dir.mkdir(parents=True)
    (wt_dir / ".git").write_text("gitdir: ...")

    with patch("pathlib.Path.home", return_value=tmp_path):
        with patch("ai_cli.config._get_projects_dir", return_value=projects_base):
            result = replicate_history_to_worktrees(verbose=False)
    assert result >= 1


def test_retranslate_project_jsonls_when_no_cc_dir_then_returns_zero(tmp_path):
    """Covers sync retranslate early exit."""
    from ai_cli.sync import retranslate_project_jsonls

    with patch("ai_cli.sync._cc_projects_dir", return_value=tmp_path / "nonexistent"):
        result = retranslate_project_jsonls(verbose=False)
    assert result == 0


def test_apply_pull_files_when_staging_dir_has_dotdir_then_skips(tmp_path):
    """Covers line 691-692: skip entries starting with '.'."""
    staging = tmp_path / "staging"
    staging.mkdir()
    dot_dir = staging / ".git"
    dot_dir.mkdir(parents=True)
    (dot_dir / "somefile").write_text("data")

    cc_dir = tmp_path / "cc"
    cc_dir.mkdir()

    result = apply_pull_files(
        staging_dir=staging,
        cc_projects_dir=cc_dir,
        local_prefix=_SERVER_PREFIX,
        memories_only=False,
        verbose=False,
        dry_run=False,
    )
    assert result["applied_count"] == 0


def test_apply_pull_files_when_verbose_ff_local_then_prints(tmp_path, capsys):
    """Covers lines 717-720: verbose output for fast_forward_local skip."""
    staging = tmp_path / "staging" / "testproj"
    staging.mkdir(parents=True)
    staging_jsonl = staging / "conv.jsonl"
    staging_jsonl.write_text('{"line": 1}\n')

    cc_dir = tmp_path / "cc"
    cc_proj = cc_dir / f"{_SERVER_PREFIX}testproj"
    cc_proj.mkdir(parents=True)
    dst = cc_proj / "conv.jsonl"
    # Local is ahead: has staging content plus more
    dst.write_text('{"line": 1}\n{"line": 2}\n')

    with patch("ai_cli.sync.denormalize_project_name", return_value=f"{_SERVER_PREFIX}testproj"):
        with patch("ai_cli.sync.detect_jsonl_divergence", return_value="fast_forward_local"):
            apply_pull_files(
                staging_dir=tmp_path / "staging",
                cc_projects_dir=cc_dir,
                local_prefix=_SERVER_PREFIX,
                memories_only=False,
                verbose=True,
                dry_run=False,
            )
    output = capsys.readouterr().out
    assert "skip (local ahead)" in output


# ---------------------------------------------------------------------------
# _detect_foreign_home_in_history — exception branches
# ---------------------------------------------------------------------------


def test_detect_foreign_home_in_history_when_json_parse_error_then_skips(tmp_path):
    """Covers lines 522-523: inner exception (bad JSON line) skipped."""
    history = tmp_path / "history.jsonl"
    history.write_bytes(b'"project":invalid json\n')
    with patch("pathlib.Path.home", return_value=tmp_path):
        result = _detect_foreign_home_in_history(history)
    assert result is None


def test_detect_foreign_home_in_history_when_file_unreadable_then_returns_none(tmp_path):
    """Covers lines 524-525: outer exception (cannot open file) returns None."""
    history = tmp_path / "history.jsonl"
    history.write_bytes(b'{"project":"/home/remote/projects/x"}')
    with patch("builtins.open", side_effect=OSError("permission denied")):
        result = _detect_foreign_home_in_history(history)
    assert result is None


# ---------------------------------------------------------------------------
# replicate_history_to_worktrees — uncovered paths
# ---------------------------------------------------------------------------


def test_replicate_history_to_worktrees_when_empty_line_then_skips(tmp_path):
    """Covers line 592: empty line in history.jsonl is skipped."""
    history = tmp_path / ".claude" / "history.jsonl"
    history.parent.mkdir(parents=True)
    # First line is empty, second has no project — no new entries
    history.write_text("\n{}\n")

    with patch("pathlib.Path.home", return_value=tmp_path):
        with patch("ai_cli.config._get_projects_dir", return_value=tmp_path / "projects"):
            result = replicate_history_to_worktrees()
    assert result == 0


def test_replicate_history_to_worktrees_when_json_error_then_skips(tmp_path):
    """Covers lines 600-601: JSON parse error in loop skipped."""
    history = tmp_path / ".claude" / "history.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text('"project":bad json\n')

    with patch("pathlib.Path.home", return_value=tmp_path):
        with patch("ai_cli.config._get_projects_dir", return_value=tmp_path / "projects"):
            result = replicate_history_to_worktrees()
    assert result == 0


def test_replicate_history_to_worktrees_when_project_not_dir_then_skips(tmp_path):
    """Covers line 609: project path not a directory → skip."""
    history = tmp_path / ".claude" / "history.jsonl"
    history.parent.mkdir(parents=True)
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    # Note: myapp directory does NOT exist
    history.write_text(f'{{"project":"{tmp_path}/projects/myapp"}}\n')

    with patch("pathlib.Path.home", return_value=tmp_path):
        with patch("ai_cli.config._get_projects_dir", return_value=projects_dir):
            result = replicate_history_to_worktrees()
    assert result == 0


def test_replicate_history_to_worktrees_when_no_worktrees_dir_then_skips(tmp_path):
    """Covers line 613: project exists but has no .worktrees dir."""
    history = tmp_path / ".claude" / "history.jsonl"
    history.parent.mkdir(parents=True)
    projects_dir = tmp_path / "projects"
    myapp = projects_dir / "myapp"
    myapp.mkdir(parents=True)
    # No .worktrees dir
    history.write_text(f'{{"project":"{tmp_path}/projects/myapp"}}\n')

    with patch("pathlib.Path.home", return_value=tmp_path):
        with patch("ai_cli.config._get_projects_dir", return_value=projects_dir):
            result = replicate_history_to_worktrees()
    assert result == 0


def test_replicate_history_to_worktrees_when_wt_not_dir_then_skips(tmp_path):
    """Covers line 617: worktree entry without .git is skipped."""
    history = tmp_path / ".claude" / "history.jsonl"
    history.parent.mkdir(parents=True)
    projects_dir = tmp_path / "projects"
    myapp = projects_dir / "myapp"
    myapp.mkdir(parents=True)
    wt_dir = myapp / ".worktrees" / "myapp-1"
    wt_dir.mkdir(parents=True)
    # No .git → not a valid worktree
    history.write_text(f'{{"project":"{tmp_path}/projects/myapp"}}\n')

    with patch("pathlib.Path.home", return_value=tmp_path):
        with patch("ai_cli.config._get_projects_dir", return_value=projects_dir):
            result = replicate_history_to_worktrees()
    assert result == 0


def test_replicate_history_to_worktrees_when_wt_already_in_existing_then_skips(tmp_path):
    """Covers line 620: worktree cwd already in existing_projects → skipped."""
    projects_dir = tmp_path / "projects"
    myapp = projects_dir / "myapp"
    myapp.mkdir(parents=True)
    wt_dir = myapp / ".worktrees" / "myapp-1"
    wt_dir.mkdir(parents=True)
    (wt_dir / ".git").write_text("gitdir: ../.git/worktrees/myapp-1")

    history = tmp_path / ".claude" / "history.jsonl"
    history.parent.mkdir(parents=True)
    # wt_cwd already in existing_projects → skip (no new entries added)
    wt_cwd = str(wt_dir)
    history.write_text(f'{{"project":"{tmp_path}/projects/myapp"}}\n{{"project":"{wt_cwd}"}}\n')

    with patch("pathlib.Path.home", return_value=tmp_path):
        with patch("ai_cli.config._get_projects_dir", return_value=projects_dir):
            result = replicate_history_to_worktrees()
    assert result == 0  # wt already present, no new entries


def test_replicate_history_to_worktrees_when_verbose_then_prints(tmp_path, capsys):
    """Covers line 635: verbose print when new entries added."""
    projects_dir = tmp_path / "projects"
    myapp = projects_dir / "myapp"
    myapp.mkdir(parents=True)
    wt_dir = myapp / ".worktrees" / "myapp-1"
    wt_dir.mkdir(parents=True)
    (wt_dir / ".git").write_text("gitdir: ../.git/worktrees/myapp-1")

    history = tmp_path / ".claude" / "history.jsonl"
    history.parent.mkdir(parents=True)
    main_cwd = f"{tmp_path}/projects/myapp"
    history.write_text(f'{{"project":"{main_cwd}"}}\n')

    with patch("pathlib.Path.home", return_value=tmp_path):
        with patch("ai_cli.config._get_projects_dir", return_value=projects_dir):
            result = replicate_history_to_worktrees(verbose=True)
    assert result == 1
    assert "replicate history" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# retranslate_project_jsonls — verbose branch
# ---------------------------------------------------------------------------


def test_retranslate_project_jsonls_when_verbose_then_prints(tmp_path, capsys):
    """Covers line 668: verbose output when file is retranslated."""
    cc_projects = tmp_path / ".claude" / "projects"
    cc_projects.mkdir(parents=True)
    proj_dir = cc_projects / "myproject"
    proj_dir.mkdir()
    jsonl = proj_dir / "conv.jsonl"
    # Write content with a foreign home path
    foreign_home = "/Users/remote"
    jsonl.write_bytes(f'{{"cwd":"{foreign_home}/projects/myapp"}}\n'.encode())

    with patch("pathlib.Path.home", return_value=tmp_path):
        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_projects):
            with patch("ai_cli.sync._detect_foreign_home", return_value=foreign_home):
                result = retranslate_project_jsonls(verbose=True)
    assert result == 1
    assert "retranslate" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# apply_pull_files — more verbose/path branches
# ---------------------------------------------------------------------------


def test_apply_pull_files_when_ff_remote_verbose_then_prints(tmp_path, capsys):
    """Covers line 717: verbose print for fast_forward_remote case."""
    staging = tmp_path / "staging" / "testproj"
    staging.mkdir(parents=True)
    staging_jsonl = staging / "conv.jsonl"
    staging_jsonl.write_text('{"line": 1}\n')

    cc_dir = tmp_path / "cc"
    cc_proj = cc_dir / f"{_SERVER_PREFIX}testproj"
    cc_proj.mkdir(parents=True)

    with patch("ai_cli.sync.denormalize_project_name", return_value=f"{_SERVER_PREFIX}testproj"):
        with patch("ai_cli.sync.detect_jsonl_divergence", return_value="fast_forward_remote"):
            with patch("ai_cli.sync._write_jsonl_translated"):
                with patch("ai_cli.sync._replicate_to_worktrees", return_value=0):
                    apply_pull_files(
                        staging_dir=tmp_path / "staging",
                        cc_projects_dir=cc_dir,
                        local_prefix=_SERVER_PREFIX,
                        memories_only=False,
                        verbose=True,
                        dry_run=False,
                    )
    assert "apply (ff)" in capsys.readouterr().out


def test_apply_pull_files_when_prefer_remote_verbose_then_prints(tmp_path, capsys):
    """Covers line 728: verbose print for diverged+prefer_remote case."""
    staging = tmp_path / "staging" / "testproj"
    staging.mkdir(parents=True)
    staging_jsonl = staging / "conv.jsonl"
    staging_jsonl.write_text('{"line": 1}\n')

    cc_dir = tmp_path / "cc"
    cc_proj = cc_dir / f"{_SERVER_PREFIX}testproj"
    cc_proj.mkdir(parents=True)

    with patch("ai_cli.sync.denormalize_project_name", return_value=f"{_SERVER_PREFIX}testproj"):
        with patch("ai_cli.sync.detect_jsonl_divergence", return_value="diverged"):
            with patch("ai_cli.sync._write_jsonl_translated"):
                with patch("ai_cli.sync._replicate_to_worktrees", return_value=0):
                    apply_pull_files(
                        staging_dir=tmp_path / "staging",
                        cc_projects_dir=cc_dir,
                        local_prefix=_SERVER_PREFIX,
                        memories_only=False,
                        verbose=True,
                        dry_run=False,
                        prefer_remote=True,
                    )
    assert "apply (prefer-remote)" in capsys.readouterr().out


def test_apply_pull_files_when_identical_text_file_then_skips(tmp_path):
    """Covers line 745: identical text file (same hash) → continue."""
    staging = tmp_path / "staging" / "testproj"
    staging.mkdir(parents=True)
    (staging / "MEMORY.md").write_text("same content")

    cc_dir = tmp_path / "cc"
    cc_proj = cc_dir / f"{_SERVER_PREFIX}testproj"
    cc_proj.mkdir(parents=True)
    (cc_proj / "MEMORY.md").write_text("same content")

    with patch("ai_cli.sync.denormalize_project_name", return_value=f"{_SERVER_PREFIX}testproj"):
        with patch("ai_cli.sync._replicate_to_worktrees", return_value=0):
            result = apply_pull_files(
                staging_dir=tmp_path / "staging",
                cc_projects_dir=cc_dir,
                local_prefix=_SERVER_PREFIX,
                memories_only=False,
                verbose=False,
                dry_run=False,
            )
    assert result["applied_count"] == 0


def test_apply_pull_files_when_text_file_changed_verbose_then_prints(tmp_path, capsys):
    """Covers line 767: verbose print for text file applied."""
    staging = tmp_path / "staging" / "testproj"
    staging.mkdir(parents=True)
    (staging / "MEMORY.md").write_text("new content")

    cc_dir = tmp_path / "cc"
    cc_proj = cc_dir / f"{_SERVER_PREFIX}testproj"
    cc_proj.mkdir(parents=True)
    # dst does not exist → different hash → apply

    with patch("ai_cli.sync.denormalize_project_name", return_value=f"{_SERVER_PREFIX}testproj"):
        with patch("ai_cli.sync._replicate_to_worktrees", return_value=0):
            apply_pull_files(
                staging_dir=tmp_path / "staging",
                cc_projects_dir=cc_dir,
                local_prefix=_SERVER_PREFIX,
                memories_only=False,
                verbose=True,
                dry_run=False,
            )
    assert "apply:" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _find_project_worktrees — no worktrees dir
# ---------------------------------------------------------------------------


def test_find_project_worktrees_when_no_worktrees_dir_then_returns_empty(tmp_path):
    """Covers line 786: return [] when .worktrees/ doesn't exist."""
    project = tmp_path / "myproject"
    project.mkdir()
    # No .worktrees directory
    result = _find_project_worktrees(project)
    assert result == []


# ---------------------------------------------------------------------------
# _replicate_to_worktrees — multiple branches
# ---------------------------------------------------------------------------


def test_replicate_to_worktrees_when_non_dir_entry_then_skips(tmp_path):
    """Covers line 814: non-directory entry in cc_projects_dir is skipped."""
    cc_projects = tmp_path / "cc"
    cc_projects.mkdir()
    (cc_projects / "file.txt").write_text("not a dir")

    with patch("ai_cli.config._get_projects_dir", return_value=tmp_path / "projects"):
        result = _replicate_to_worktrees(cc_projects, _SERVER_PREFIX, verbose=False)
    assert result == 0


def test_replicate_to_worktrees_when_bare_name_none_then_skips(tmp_path):
    """Covers line 818: bare_name is None (prefix doesn't match) → skip."""
    cc_projects = tmp_path / "cc"
    cc_projects.mkdir()
    # Dir name doesn't match server prefix → normalize returns None
    (cc_projects / "other-prefix-myapp").mkdir()

    with patch("ai_cli.config._get_projects_dir", return_value=tmp_path / "projects"):
        result = _replicate_to_worktrees(cc_projects, _SERVER_PREFIX, verbose=False)
    assert result == 0


def test_replicate_to_worktrees_when_worktrees_in_name_then_skips(tmp_path):
    """Covers line 822: skip worktree CC dirs themselves."""
    cc_projects = tmp_path / "cc"
    cc_projects.mkdir()
    # This is a worktree CC dir — should be skipped
    wt_cc = cc_projects / f"{_SERVER_PREFIX}myapp--worktrees-myapp-1"
    wt_cc.mkdir()

    with patch("ai_cli.config._get_projects_dir", return_value=tmp_path / "projects"):
        result = _replicate_to_worktrees(cc_projects, _SERVER_PREFIX, verbose=False)
    assert result == 0


def test_replicate_to_worktrees_when_project_not_on_disk_then_skips(tmp_path):
    """Covers line 831 implicitly (no worktrees → continue) via project missing."""
    cc_projects = tmp_path / "cc"
    cc_projects.mkdir()
    (cc_projects / f"{_SERVER_PREFIX}myapp").mkdir()
    # projects_base exists but myapp subdir does not

    projects_base = tmp_path / "projects"
    projects_base.mkdir()

    with patch("ai_cli.config._get_projects_dir", return_value=projects_base):
        result = _replicate_to_worktrees(cc_projects, _SERVER_PREFIX, verbose=False)
    assert result == 0


def test_replicate_to_worktrees_when_dst_exists_not_symlink_then_skips(tmp_path):
    """Covers line 854: dst exists and is not a symlink → skip."""
    projects_base = tmp_path / "projects"
    myapp = projects_base / "myapp"
    myapp.mkdir(parents=True)
    wt_dir = myapp / ".worktrees" / "myapp-1"
    wt_dir.mkdir(parents=True)
    (wt_dir / ".git").write_text("gitdir")

    cc_projects = tmp_path / "cc"
    cc_dir = cc_projects / f"{_SERVER_PREFIX}myapp"
    cc_dir.mkdir(parents=True)
    # Create a JSONL file with a matching title
    jsonl_content = f'{{"customTitle":"myapp-1","cwd":"{myapp}"}}\n'
    (cc_dir / "conv.jsonl").write_text(jsonl_content)

    # Pre-create dst (not a symlink) so it gets skipped
    wt_cc_dir = cc_projects / f"{_SERVER_PREFIX}myapp--worktrees-myapp-1"
    wt_cc_dir.mkdir(parents=True)
    (wt_cc_dir / "conv.jsonl").write_text("existing content")

    with patch("ai_cli.config._get_projects_dir", return_value=projects_base):
        _replicate_to_worktrees(cc_projects, _SERVER_PREFIX, verbose=False)
    # File already existed, not a symlink → skipped
    assert (wt_cc_dir / "conv.jsonl").read_text() == "existing content"


def test_replicate_to_worktrees_when_matching_conv_then_replicates(tmp_path, capsys):
    """Covers lines 868-885, 891-897: full replication with title match and lock dir copy."""
    projects_base = tmp_path / "projects"
    myapp = projects_base / "myapp"
    myapp.mkdir(parents=True)
    wt_dir = myapp / ".worktrees" / "myapp-1"
    wt_dir.mkdir(parents=True)
    (wt_dir / ".git").write_text("gitdir")

    cc_projects = tmp_path / "cc"
    cc_dir = cc_projects / f"{_SERVER_PREFIX}myapp"
    cc_dir.mkdir(parents=True)

    # Create a JSONL file with matching customTitle
    main_cwd = str(myapp)
    jsonl_content = f'{{"customTitle":"myapp-1","cwd":"{main_cwd}"}}\n'
    conv_file = cc_dir / "abc123.jsonl"
    conv_file.write_text(jsonl_content)

    # Create a lock dir that matches the replicated UUID
    lock_dir = cc_dir / "abc123"
    lock_dir.mkdir()
    (lock_dir / "lock").write_text("lock data")

    with patch("ai_cli.config._get_projects_dir", return_value=projects_base):
        result = _replicate_to_worktrees(cc_projects, _SERVER_PREFIX, verbose=True)

    # Should have replicated the JSONL file
    wt_cc_dir = cc_projects / f"{_SERVER_PREFIX}myapp--worktrees-myapp-1"
    assert (wt_cc_dir / "abc123.jsonl").exists()
    # Should have copied the lock dir
    assert (wt_cc_dir / "abc123").exists()
    assert result >= 1
    output = capsys.readouterr().out
    assert "replicate to worktree" in output
    assert "replicate dir" in output


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _push_to_remote — post-rebase push failure
# ---------------------------------------------------------------------------


def test_push_to_remote_when_rebase_push_fails_then_returns_false(tmp_path, capsys):
    """Covers lines 1202-1203: push after rebase succeeds but second push fails."""
    call_count = 0

    def fake_run(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        if "push" in cmd and call_count == 1:
            # First push fails — triggers rebase
            return MagicMock(returncode=1, stderr="push rejected")
        elif "rebase" in cmd or "pull" in cmd:
            return MagicMock(returncode=0, stderr="")
        elif "push" in cmd and call_count > 1:
            # Second push (after rebase) fails
            return MagicMock(returncode=1, stderr="still rejected")
        return MagicMock(returncode=0, stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        result = _push_to_remote(tmp_path, verbose=False)
    assert result is False
    assert "after rebase" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _release_pid_file — exception branch
# ---------------------------------------------------------------------------


def test_release_pid_file_when_unlink_raises_then_silent(tmp_path):
    """Covers lines 1604-1605: exception in unlink is silently swallowed."""
    with patch("ai_cli.sync._pid_file_path", return_value=tmp_path / "test.pid"):
        with patch("pathlib.Path.unlink", side_effect=OSError("busy")):
            _release_pid_file("test")  # Should not raise


# ---------------------------------------------------------------------------
# sync_push — various error and verbose paths
# ---------------------------------------------------------------------------


def test_sync_push_when_init_server_raises_oserror_then_continues(tmp_path, capsys):
    """Covers lines 1347-1348: init_server_bare_repo raises OSError → non-fatal, continues."""
    from ai_cli.sync import SyncConfig

    cfg = SyncConfig(
        remote_host="host",
        remote_url="ssh://host/repo.git",
        staging_dir=tmp_path / "staging",
        source_machine="server",
        local_prefix=_SERVER_PREFIX,
    )

    cc_dir = tmp_path / ".claude" / "projects"
    cc_dir.mkdir(parents=True)

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.init_server_bare_repo", side_effect=OSError("no route")):
            with patch("ai_cli.sync.init_staging_repo"):
                with patch("ai_cli.sync.is_cc_active_on_server", return_value=False):
                    with patch("ai_cli.sync._wait_for_dream_completion"):
                        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
                            with patch(
                                "ai_cli.sync.stage_project_files",
                                return_value={
                                    "staged_files": [],
                                    "project_names": [],
                                    "memory_count": 0,
                                    "jsonl_count": 0,
                                },
                            ):
                                with patch("ai_cli.sync._remote_newer_files", return_value=[]):
                                    with patch("ai_cli.sync.git_commit_staged", return_value=False):
                                        result = sync_push([])
    assert result == 0


def test_sync_push_when_init_staging_raises_then_returns_1(tmp_path, capsys):
    """Covers lines 1351-1353: init_staging_repo raises → error printed, return 1."""
    from ai_cli.sync import SyncConfig

    cfg = SyncConfig(
        remote_host="host",
        remote_url="ssh://host/repo.git",
        staging_dir=tmp_path / "staging",
        source_machine="server",
        local_prefix=_SERVER_PREFIX,
    )

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.init_server_bare_repo"):
            with patch("ai_cli.sync.init_staging_repo", side_effect=Exception("connection refused")):
                result = sync_push([])
    assert result == 1
    assert "Error initializing staging repo" in capsys.readouterr().err


def test_sync_push_when_cc_active_on_server_then_aborts(tmp_path, capsys):
    """Covers lines 1357-1363: CC active on server → print warning, return 1."""
    from ai_cli.sync import SyncConfig

    cfg = SyncConfig(
        remote_host="host",
        remote_url="ssh://host/repo.git",
        staging_dir=tmp_path / "staging",
        source_machine="server",
        local_prefix=_SERVER_PREFIX,
    )

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.init_server_bare_repo"):
            with patch("ai_cli.sync.init_staging_repo"):
                with patch("ai_cli.sync.is_cc_active_on_server", return_value=True):
                    result = sync_push([])
    assert result == 1
    assert "WARNING" in capsys.readouterr().err


def test_sync_push_when_cc_check_times_out_then_proceeds(tmp_path, capsys):
    """Covers lines 1364-1365: is_cc_active_on_server raises TimeoutExpired → warning, continues."""
    from ai_cli.sync import SyncConfig

    cfg = SyncConfig(
        remote_host="host",
        remote_url="ssh://host/repo.git",
        staging_dir=tmp_path / "staging",
        source_machine="server",
        local_prefix=_SERVER_PREFIX,
    )

    cc_dir = tmp_path / ".claude" / "projects"
    cc_dir.mkdir(parents=True)

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.init_server_bare_repo"):
            with patch("ai_cli.sync.init_staging_repo"):
                with patch("ai_cli.sync.is_cc_active_on_server", side_effect=subprocess.TimeoutExpired("ssh", 5)):
                    with patch("ai_cli.sync._wait_for_dream_completion"):
                        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
                            with patch(
                                "ai_cli.sync.stage_project_files",
                                return_value={
                                    "staged_files": [],
                                    "project_names": [],
                                    "memory_count": 0,
                                    "jsonl_count": 0,
                                },
                            ):
                                with patch("ai_cli.sync._remote_newer_files", return_value=[]):
                                    with patch("ai_cli.sync.git_commit_staged", return_value=False):
                                        result = sync_push([])
    assert result == 0
    assert "WARNING" in capsys.readouterr().err


def test_sync_push_when_cc_check_exception_then_proceeds_silently(tmp_path):
    """Covers lines 1366-1367: other exception from is_cc_active_on_server → silent, continues."""
    from ai_cli.sync import SyncConfig

    cfg = SyncConfig(
        remote_host="host",
        remote_url="ssh://host/repo.git",
        staging_dir=tmp_path / "staging",
        source_machine="server",
        local_prefix=_SERVER_PREFIX,
    )

    cc_dir = tmp_path / ".claude" / "projects"
    cc_dir.mkdir(parents=True)

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.init_server_bare_repo"):
            with patch("ai_cli.sync.init_staging_repo"):
                with patch("ai_cli.sync.is_cc_active_on_server", side_effect=OSError("network gone")):
                    with patch("ai_cli.sync._wait_for_dream_completion"):
                        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
                            with patch(
                                "ai_cli.sync.stage_project_files",
                                return_value={
                                    "staged_files": [],
                                    "project_names": [],
                                    "memory_count": 0,
                                    "jsonl_count": 0,
                                },
                            ):
                                with patch("ai_cli.sync._remote_newer_files", return_value=[]):
                                    with patch("ai_cli.sync.git_commit_staged", return_value=False):
                                        result = sync_push([])
    assert result == 0


def test_sync_push_when_dry_run_then_prints_would_sync(tmp_path, capsys):
    """Covers line 1357: dry_run prints 'Would sync' summary."""
    from ai_cli.sync import SyncConfig

    cfg = SyncConfig(
        remote_host="host",
        remote_url="ssh://host/repo.git",
        staging_dir=tmp_path / "staging",
        source_machine="server",
        local_prefix=_SERVER_PREFIX,
    )

    cc_dir = tmp_path / ".claude" / "projects"
    cc_dir.mkdir(parents=True)

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync._wait_for_dream_completion"):
            with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
                with patch(
                    "ai_cli.sync.stage_project_files",
                    return_value={
                        "staged_files": [("a", "b")],
                        "project_names": ["proj"],
                        "memory_count": 0,
                        "jsonl_count": 0,
                    },
                ):
                    result = sync_push(["--dry-run"])
    assert result == 0
    output = capsys.readouterr().out
    assert "Would sync" in output


def test_sync_push_when_not_committed_verbose_then_prints(tmp_path, capsys):
    """Covers line 1426: nothing to commit, verbose prints message."""
    from ai_cli.sync import SyncConfig

    cfg = SyncConfig(
        remote_host="host",
        remote_url="ssh://host/repo.git",
        staging_dir=tmp_path / "staging",
        source_machine="server",
        local_prefix=_SERVER_PREFIX,
    )

    cc_dir = tmp_path / ".claude" / "projects"
    cc_dir.mkdir(parents=True)

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.init_server_bare_repo"):
            with patch("ai_cli.sync.init_staging_repo"):
                with patch("ai_cli.sync.is_cc_active_on_server", return_value=False):
                    with patch("ai_cli.sync._wait_for_dream_completion"):
                        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
                            with patch(
                                "ai_cli.sync.stage_project_files",
                                return_value={
                                    "staged_files": [],
                                    "project_names": [],
                                    "memory_count": 0,
                                    "jsonl_count": 0,
                                },
                            ):
                                with patch("ai_cli.sync._remote_newer_files", return_value=[]):
                                    with patch("ai_cli.sync.git_commit_staged", return_value=False):
                                        result = sync_push(["--verbose"])
    assert result == 0
    assert "Nothing to commit" in capsys.readouterr().out


def test_sync_push_when_push_succeeds_and_nats_notified_then_returns_0(tmp_path, capsys):
    """Covers lines 1438, 1443: NATS notify and verbose success message."""
    from ai_cli.sync import SyncConfig

    cfg = SyncConfig(
        remote_host="host",
        remote_url="ssh://host/repo.git",
        staging_dir=tmp_path / "staging",
        source_machine="server",
        local_prefix=_SERVER_PREFIX,
    )

    cc_dir = tmp_path / ".claude" / "projects"
    cc_dir.mkdir(parents=True)

    mock_client = MagicMock()
    mock_client.publish = AsyncMock(return_value=True)

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.init_server_bare_repo"):
            with patch("ai_cli.sync.init_staging_repo"):
                with patch("ai_cli.sync.is_cc_active_on_server", return_value=False):
                    with patch("ai_cli.sync._wait_for_dream_completion"):
                        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
                            with patch(
                                "ai_cli.sync.stage_project_files",
                                return_value={
                                    "staged_files": [("a", "b")],
                                    "project_names": ["proj"],
                                    "memory_count": 1,
                                    "jsonl_count": 0,
                                },
                            ):
                                with patch("ai_cli.sync._remote_newer_files", return_value=[]):
                                    with patch("ai_cli.sync.git_commit_staged", return_value=True):
                                        with patch("ai_cli.sync._push_to_remote", return_value=True):
                                            with patch("ai_cli.messaging.NATSClient", return_value=mock_client):
                                                result = sync_push(["--verbose"])
    assert result == 0
    output = capsys.readouterr().out
    assert "Pushed" in output


# ---------------------------------------------------------------------------
# _remote_newer_files — remote-is-newer guard
# ---------------------------------------------------------------------------


def _make_git_result(stdout: str = "", returncode: int = 0) -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout.encode()
    return result


def test_remote_newer_files_when_fetch_fails_then_returns_empty(tmp_path):
    """Non-fatal: if git fetch fails (offline), guard returns empty list."""
    with patch("ai_cli.sync._git") as mock_git:
        mock_git.return_value = _make_git_result(returncode=1)
        result = _remote_newer_files(tmp_path)
    assert result == []


def test_remote_newer_files_when_no_modified_files_then_returns_empty(tmp_path):
    """If nothing was modified in the staging dir, no conflict is possible."""

    def _git_side_effect(args, *a, **kw):
        if args[0] == "fetch":
            return _make_git_result()
        if args[0] == "status":
            return _make_git_result("")  # empty — nothing staged
        return _make_git_result()

    with patch("ai_cli.sync._git", side_effect=_git_side_effect):
        result = _remote_newer_files(tmp_path)
    assert result == []


def test_remote_newer_files_when_remote_is_newer_then_returns_file_path(tmp_path):
    """Modified file with remote ts > local ts is returned in the list."""
    status_output = " M some/project/session.jsonl\n"
    remote_ts = "1700000100"  # newer
    local_ts = "1700000000"  # older

    call_count = [0]

    def _git_side_effect(args, *a, **kw):
        if args[0] == "fetch":
            return _make_git_result()
        if args[0] == "status":
            return _make_git_result(status_output)
        if args[0] == "log":
            # First log call = origin/main (remote), second = HEAD (local)
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_git_result(remote_ts)
            return _make_git_result(local_ts)
        return _make_git_result()

    with patch("ai_cli.sync._git", side_effect=_git_side_effect):
        result = _remote_newer_files(tmp_path)
    assert result == ["some/project/session.jsonl"]


def test_remote_newer_files_when_local_is_newer_then_returns_empty(tmp_path):
    """Local file is newer than remote — no conflict, push is safe."""
    status_output = " M some/project/session.jsonl\n"
    remote_ts = "1700000000"
    local_ts = "1700000100"  # local is newer

    call_count = [0]

    def _git_side_effect(args, *a, **kw):
        if args[0] == "fetch":
            return _make_git_result()
        if args[0] == "status":
            return _make_git_result(status_output)
        if args[0] == "log":
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_git_result(remote_ts)
            return _make_git_result(local_ts)
        return _make_git_result()

    with patch("ai_cli.sync._git", side_effect=_git_side_effect):
        result = _remote_newer_files(tmp_path)
    assert result == []


def test_remote_newer_files_when_file_not_on_remote_then_skips(tmp_path):
    """New file (not yet on remote) — remote log returns empty, no conflict."""
    status_output = " M brand-new-project/session.jsonl\n"

    def _git_side_effect(args, *a, **kw):
        if args[0] == "fetch":
            return _make_git_result()
        if args[0] == "status":
            return _make_git_result(status_output)
        if args[0] == "log":
            return _make_git_result("")  # empty = not on remote
        return _make_git_result()

    with patch("ai_cli.sync._git", side_effect=_git_side_effect):
        result = _remote_newer_files(tmp_path)
    assert result == []


def test_remote_newer_files_when_new_untracked_file_then_skips(tmp_path):
    """?? prefix = untracked new file — skipped entirely, no remote comparison."""
    status_output = "?? brand-new/session.jsonl\n"

    def _git_side_effect(args, *a, **kw):
        if args[0] == "fetch":
            return _make_git_result()
        if args[0] == "status":
            return _make_git_result(status_output)
        return _make_git_result()

    with patch("ai_cli.sync._git", side_effect=_git_side_effect) as mock_git:
        result = _remote_newer_files(tmp_path)
    assert result == []
    # log should never have been called for untracked files
    log_calls = [c for c in mock_git.call_args_list if c[0][0][0] == "log"]
    assert log_calls == []


def test_sync_push_when_remote_is_newer_then_returns_1_with_error(tmp_path, capsys):
    """Guard fires when remote has newer content — returns 1, prints error."""
    from ai_cli.sync import SyncConfig

    cfg = SyncConfig(
        remote_host="host",
        remote_url="ssh://host/repo.git",
        staging_dir=tmp_path / "staging",
        source_machine="mac",
        local_prefix=_MAC_PREFIX,
    )
    cc_dir = tmp_path / ".claude" / "projects"
    cc_dir.mkdir(parents=True)

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.init_server_bare_repo"):
            with patch("ai_cli.sync.init_staging_repo"):
                with patch("ai_cli.sync.is_cc_active_on_server", return_value=False):
                    with patch("ai_cli.sync._wait_for_dream_completion"):
                        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
                            with patch(
                                "ai_cli.sync.stage_project_files",
                                return_value={
                                    "staged_files": [],
                                    "project_names": [],
                                    "memory_count": 0,
                                    "jsonl_count": 0,
                                },
                            ):
                                with patch(
                                    "ai_cli.sync._remote_newer_files",
                                    return_value=["proj/session.jsonl"],
                                ):
                                    result = sync_push([])
    assert result == 1
    err = capsys.readouterr().err
    assert "remote has newer content" in err
    assert "proj/session.jsonl" in err
    assert "ai sync pull" in err


def test_sync_push_when_remote_is_newer_and_force_then_proceeds(tmp_path):
    """--force bypasses the remote-is-newer guard."""
    from ai_cli.sync import SyncConfig

    cfg = SyncConfig(
        remote_host="host",
        remote_url="ssh://host/repo.git",
        staging_dir=tmp_path / "staging",
        source_machine="mac",
        local_prefix=_MAC_PREFIX,
    )
    cc_dir = tmp_path / ".claude" / "projects"
    cc_dir.mkdir(parents=True)

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.init_server_bare_repo"):
            with patch("ai_cli.sync.init_staging_repo"):
                with patch("ai_cli.sync.is_cc_active_on_server", return_value=False):
                    with patch("ai_cli.sync._wait_for_dream_completion"):
                        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
                            with patch(
                                "ai_cli.sync.stage_project_files",
                                return_value={
                                    "staged_files": [],
                                    "project_names": [],
                                    "memory_count": 0,
                                    "jsonl_count": 0,
                                },
                            ):
                                with patch("ai_cli.sync._remote_newer_files") as mock_guard:
                                    with patch("ai_cli.sync.git_commit_staged", return_value=False):
                                        result = sync_push(["--force"])
    assert result == 0
    mock_guard.assert_not_called()


# ---------------------------------------------------------------------------
# sync_pull — various paths
# ---------------------------------------------------------------------------


def test_sync_pull_when_cc_active_locally_then_prints_warning(tmp_path, capsys):
    """Covers lines 1463-1467: CC active locally → print warning, continue."""
    from ai_cli.sync import SyncConfig

    cfg = SyncConfig(
        remote_host="host",
        remote_url="ssh://host/repo.git",
        staging_dir=tmp_path / "staging",
        source_machine="server",
        local_prefix=_SERVER_PREFIX,
    )

    cc_dir = tmp_path / ".claude" / "projects"
    cc_dir.mkdir(parents=True)

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.is_cc_active_locally", return_value=True):
            with patch("ai_cli.sync.init_staging_repo"):
                with patch("ai_cli.sync._pre_pull_push_memories"):
                    with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
                        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
                            with patch(
                                "ai_cli.sync.apply_pull_files", return_value={"conflicts": [], "applied_count": 0}
                            ):
                                with patch("ai_cli.sync.translate_history_jsonl"):
                                    with patch("ai_cli.sync.retranslate_project_jsonls"):
                                        with patch("ai_cli.sync.purge_phantom_history_entries"):
                                            result = sync_pull([])
    assert result == 0
    assert "WARNING" in capsys.readouterr().err


def test_sync_pull_when_init_staging_raises_then_returns_1(tmp_path, capsys):
    """Covers lines 1472-1474: init_staging_repo raises → error, return 1."""
    from ai_cli.sync import SyncConfig

    cfg = SyncConfig(
        remote_host="host",
        remote_url="ssh://host/repo.git",
        staging_dir=tmp_path / "staging",
        source_machine="server",
        local_prefix=_SERVER_PREFIX,
    )

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.is_cc_active_locally", return_value=False):
            with patch("ai_cli.sync.init_staging_repo", side_effect=Exception("no connection")):
                result = sync_pull([])
    assert result == 1
    assert "Error initializing staging repo" in capsys.readouterr().err


def test_sync_pull_when_fetch_fails_then_returns_1(tmp_path, capsys):
    """Covers lines 1490-1491: git fetch fails → error, return 1."""
    from ai_cli.sync import SyncConfig

    cfg = SyncConfig(
        remote_host="host",
        remote_url="ssh://host/repo.git",
        staging_dir=tmp_path / "staging",
        source_machine="server",
        local_prefix=_SERVER_PREFIX,
    )

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.is_cc_active_locally", return_value=False):
            with patch("ai_cli.sync.init_staging_repo"):
                with patch("ai_cli.sync._pre_pull_push_memories"):
                    with patch("subprocess.run", return_value=MagicMock(returncode=1, stderr="fetch error")):
                        result = sync_pull([])
    assert result == 1
    assert "Error fetching" in capsys.readouterr().err


def test_sync_pull_when_verbose_then_prints_applied_count(tmp_path, capsys):
    """Covers lines 1514, 1524, 1533: verbose output for applied counts."""
    from ai_cli.sync import SyncConfig

    cfg = SyncConfig(
        remote_host="host",
        remote_url="ssh://host/repo.git",
        staging_dir=tmp_path / "staging",
        source_machine="server",
        local_prefix=_SERVER_PREFIX,
    )

    cc_dir = tmp_path / ".claude" / "projects"
    cc_dir.mkdir(parents=True)

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.is_cc_active_locally", return_value=False):
            with patch("ai_cli.sync.init_staging_repo"):
                with patch("ai_cli.sync._pre_pull_push_memories"):
                    with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
                        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
                            with patch(
                                "ai_cli.sync.apply_pull_files", return_value={"conflicts": [], "applied_count": 3}
                            ):
                                with patch("ai_cli.sync.translate_history_jsonl"):
                                    with patch("ai_cli.sync.retranslate_project_jsonls"):
                                        with patch("ai_cli.sync.purge_phantom_history_entries"):
                                            result = sync_pull(["--verbose"])
    assert result == 0
    output = capsys.readouterr().out
    assert "Applied" in output


def test_sync_pull_when_conflicts_then_notifies_and_returns_2(tmp_path):
    """Covers lines 1541-1543: conflicts present → notify_conflicts, return 2."""
    from ai_cli.sync import SyncConfig

    cfg = SyncConfig(
        remote_host="host",
        remote_url="ssh://host/repo.git",
        staging_dir=tmp_path / "staging",
        source_machine="server",
        local_prefix=_SERVER_PREFIX,
    )

    cc_dir = tmp_path / ".claude" / "projects"
    cc_dir.mkdir(parents=True)

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.is_cc_active_locally", return_value=False):
            with patch("ai_cli.sync.init_staging_repo"):
                with patch("ai_cli.sync._pre_pull_push_memories"):
                    with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
                        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
                            with patch(
                                "ai_cli.sync.apply_pull_files",
                                return_value={
                                    "conflicts": ["memory /proj/x.md — .conflict file written"],
                                    "applied_count": 0,
                                },
                            ):
                                with patch("ai_cli.sync.translate_history_jsonl"):
                                    with patch("ai_cli.sync.retranslate_project_jsonls"):
                                        with patch("ai_cli.sync.purge_phantom_history_entries"):
                                            with patch("ai_cli.sync.notify_conflicts") as mock_notify:
                                                result = sync_pull([])
    assert result == 2
    mock_notify.assert_called_once()


# ---------------------------------------------------------------------------
# sync_watch — already-running, result variants, verbose, KeyboardInterrupt
# ---------------------------------------------------------------------------


def test_sync_watch_when_already_running_then_returns_2(capsys):
    """Covers lines 1621-1622: PID guard prevents duplicate instance."""
    with patch("ai_cli.sync._acquire_pid_file", return_value=False):
        result = sync_watch([])
    assert result == 2
    assert "already running" in capsys.readouterr().err


def test_sync_watch_when_pull_returns_2_then_prints_conflict_message(tmp_path, capsys):
    """Covers lines 1632-1633: sync_pull returns 2 → conflict preserved message."""
    import asyncio
    from unittest.mock import AsyncMock as AM

    mock_nc = MagicMock()
    received_cb = {}

    async def fake_subscribe(subject, cb):
        received_cb["cb"] = cb
        msg = MagicMock()
        msg.data = b'{"machine": "mac"}'
        await cb(msg)

    mock_nc.subscribe = fake_subscribe

    with patch("ai_cli.sync._acquire_pid_file", return_value=True):
        with patch("ai_cli.sync._release_pid_file"):
            with patch("nats.connect", new=AM(return_value=mock_nc)):
                with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
                    with patch("ai_cli.sync.sync_pull", return_value=2):
                        result = sync_watch([])

    assert result == 0
    assert "conflicts preserved" in capsys.readouterr().out


def test_sync_watch_when_pull_fails_then_prints_failure_message(tmp_path, capsys):
    """Covers lines 1634-1635: sync_pull returns non-0/2 → failure message."""
    import asyncio
    from unittest.mock import AsyncMock as AM

    mock_nc = MagicMock()

    async def fake_subscribe(subject, cb):
        msg = MagicMock()
        msg.data = b'{"machine": "mac"}'
        await cb(msg)

    mock_nc.subscribe = fake_subscribe

    with patch("ai_cli.sync._acquire_pid_file", return_value=True):
        with patch("ai_cli.sync._release_pid_file"):
            with patch("nats.connect", new=AM(return_value=mock_nc)):
                with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
                    with patch("ai_cli.sync.sync_pull", return_value=1):
                        result = sync_watch([])

    assert result == 0
    assert "pull failed" in capsys.readouterr().err


def test_sync_watch_when_verbose_then_prints_connected(tmp_path, capsys):
    """Covers line 1644: verbose print when connected to NATS."""
    import asyncio
    from unittest.mock import AsyncMock as AM

    mock_nc = MagicMock()

    async def fake_subscribe(subject, cb):
        pass  # Don't trigger any messages

    mock_nc.subscribe = fake_subscribe

    with patch("ai_cli.sync._acquire_pid_file", return_value=True):
        with patch("ai_cli.sync._release_pid_file"):
            with patch("nats.connect", new=AM(return_value=mock_nc)):
                with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
                    result = sync_watch(["--verbose"])

    assert result == 0
    assert "connected to NATS" in capsys.readouterr().out


def test_sync_watch_when_keyboard_interrupt_during_run_then_returns_0(capsys):
    """Covers lines 1651-1652: KeyboardInterrupt during asyncio.run → ok=True, return 0."""
    with patch("ai_cli.sync._acquire_pid_file", return_value=True):
        with patch("ai_cli.sync._release_pid_file"):
            with patch("asyncio.run", side_effect=KeyboardInterrupt):
                result = sync_watch([])
    assert result == 0


# ---------------------------------------------------------------------------
# Group C: sync.py verbose/edge branches (592, 658, 831, 892, 1014, 1134-1136, 1244)
# ---------------------------------------------------------------------------


def test_replicate_history_to_worktrees_when_blank_lines_then_skips(tmp_path):
    """Covers line 592: continue for empty line in history."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    history_path = claude_dir / "history.jsonl"
    history_path.write_text('{"project": "/home/u/projects/app"}\n\n{"project": "/home/u/projects/app"}\n')
    projects_base = tmp_path / "projects"
    app = projects_base / "app"
    app.mkdir(parents=True)

    with patch("ai_cli.config._get_projects_dir", return_value=projects_base):
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = replicate_history_to_worktrees()
    assert result == 0  # no worktrees → nothing replicated, but blank line branch was exercised


def test_purge_phantom_history_entries_when_no_history_then_returns_zero():
    """Early exit when history.jsonl doesn't exist."""
    with patch("pathlib.Path.home", return_value=Path("/tmp/nonexistent_purge_test")):
        result = purge_phantom_history_entries()
    assert result == 0


def test_purge_phantom_history_entries_when_no_phantoms_then_returns_zero(tmp_path):
    """Genuine worktree entries (UUID only in worktree) are kept."""
    import json as _json

    history = tmp_path / ".claude" / "history.jsonl"
    history.parent.mkdir(parents=True)
    # A genuine worktree conversation — UUID not in any main-project entry
    wt_cwd = str(tmp_path / "projects" / "myapp" / ".worktrees" / "wt-1")
    history.write_text(_json.dumps({"project": wt_cwd, "sessionId": "genuine-uuid"}) + "\n")

    with patch("pathlib.Path.home", return_value=tmp_path):
        result = purge_phantom_history_entries()

    assert result == 0
    assert "genuine-uuid" in history.read_text()


def test_purge_phantom_history_entries_when_phantoms_exist_then_removes_them(tmp_path):
    """Phantom entries (worktree path, UUID also in main-project) are removed."""
    import json as _json

    history = tmp_path / ".claude" / "history.jsonl"
    history.parent.mkdir(parents=True)

    main_cwd = str(tmp_path / "projects" / "myapp")
    wt_cwd = str(tmp_path / "projects" / "myapp" / ".worktrees" / "wt-1")

    main_entry = _json.dumps({"project": main_cwd, "sessionId": "shared-uuid"})
    phantom_entry = _json.dumps({"project": wt_cwd, "sessionId": "shared-uuid"})
    genuine_wt_entry = _json.dumps({"project": wt_cwd, "sessionId": "wt-only-uuid"})
    history.write_text("\n".join([main_entry, phantom_entry, genuine_wt_entry]) + "\n")

    with patch("pathlib.Path.home", return_value=tmp_path):
        result = purge_phantom_history_entries()

    assert result == 1
    remaining = history.read_text()
    assert "shared-uuid" in remaining  # main-project entry kept
    assert main_cwd in remaining
    assert "wt-only-uuid" in remaining  # genuine worktree entry kept
    # Phantom (worktree path + shared UUID) is gone
    lines = [_json.loads(l) for l in remaining.strip().split("\n") if l]
    wt_shared = [l for l in lines if l["project"] == wt_cwd and l["sessionId"] == "shared-uuid"]
    assert wt_shared == []


def test_purge_phantom_history_entries_when_verbose_then_prints(tmp_path, capsys):
    """Verbose mode prints removal count."""
    import json as _json

    history = tmp_path / ".claude" / "history.jsonl"
    history.parent.mkdir(parents=True)

    main_cwd = str(tmp_path / "projects" / "myapp")
    wt_cwd = str(tmp_path / "projects" / "myapp" / ".worktrees" / "wt-1")
    history.write_text(
        _json.dumps({"project": main_cwd, "sessionId": "s1"})
        + "\n"
        + _json.dumps({"project": wt_cwd, "sessionId": "s1"})
        + "\n"
    )

    with patch("pathlib.Path.home", return_value=tmp_path):
        result = purge_phantom_history_entries(verbose=True)

    assert result == 1
    assert "purge phantom history" in capsys.readouterr().out


def test_purge_phantom_history_entries_when_malformed_json_then_keeps_line(tmp_path):
    """Malformed JSON lines are kept (exception path)."""
    history = tmp_path / ".claude" / "history.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text('{"project": "ok", "sessionId": "s1"}\nnot-json\n')

    with patch("pathlib.Path.home", return_value=tmp_path):
        result = purge_phantom_history_entries()

    assert result == 0
    assert "not-json" in history.read_text()


def test_retranslate_project_jsonls_when_jsonl_is_dir_then_skips(tmp_path):
    """Covers line 658: continue when jsonl_path.is_file() is False (it's a directory)."""
    cc_projects = tmp_path / "cc"
    proj_dir = cc_projects / "myproj"
    proj_dir.mkdir(parents=True)
    # Create a directory with a .jsonl name — rglob finds it, is_file() returns False
    fake_jsonl_dir = proj_dir / "thing.jsonl"
    fake_jsonl_dir.mkdir()

    with patch("ai_cli.sync._cc_projects_dir", return_value=cc_projects):
        result = retranslate_project_jsonls(verbose=False)
    assert result == 0


def test_replicate_to_worktrees_when_project_has_no_worktrees_then_continues(tmp_path):
    """Covers line 831: continue when _find_project_worktrees returns []."""
    projects_base = tmp_path / "projects"
    myapp = projects_base / "myapp"
    myapp.mkdir(parents=True)
    # No .worktrees dir — _find_project_worktrees returns []

    cc_projects = tmp_path / "cc"
    cc_dir = cc_projects / f"{_SERVER_PREFIX}myapp"
    cc_dir.mkdir(parents=True)

    with patch("ai_cli.config._get_projects_dir", return_value=projects_base):
        count = _replicate_to_worktrees(cc_projects, _SERVER_PREFIX, verbose=False)
    assert count == 0


def test_replicate_to_worktrees_when_unreadable_jsonl_then_continues(tmp_path):
    """Covers lines 868-869: except Exception: continue when open() raises on JSONL file."""
    projects_base = tmp_path / "projects"
    myapp = projects_base / "myapp"
    myapp.mkdir(parents=True)
    wt_dir = myapp / ".worktrees" / "myapp-1"
    wt_dir.mkdir(parents=True)
    (wt_dir / ".git").write_text("gitdir")

    cc_projects = tmp_path / "cc"
    cc_dir = cc_projects / f"{_SERVER_PREFIX}myapp"
    cc_dir.mkdir(parents=True)
    unreadable = cc_dir / "abc123.jsonl"
    unreadable.write_text('{"cwd":"/home/user/projects/myapp"}')
    unreadable.chmod(0o000)

    try:
        with patch("ai_cli.config._get_projects_dir", return_value=projects_base):
            count = _replicate_to_worktrees(cc_projects, _SERVER_PREFIX, verbose=False)
        assert count == 0  # skipped unreadable file
    finally:
        unreadable.chmod(0o644)  # restore so tmp cleanup works


def test_replicate_to_worktrees_when_lock_dir_not_in_replicated_then_skips(tmp_path):
    """Covers line 892: continue when lock dir UUID not in replicated_uuids."""
    projects_base = tmp_path / "projects"
    myapp = projects_base / "myapp"
    myapp.mkdir(parents=True)
    wt_dir = myapp / ".worktrees" / "myapp-1"
    wt_dir.mkdir(parents=True)
    (wt_dir / ".git").write_text("gitdir")

    cc_projects = tmp_path / "cc"
    cc_dir = cc_projects / f"{_SERVER_PREFIX}myapp"
    cc_dir.mkdir(parents=True)

    # JSONL that doesn't match worktree (no title/cwd match) → not replicated
    conv = cc_dir / "abc123.jsonl"
    conv.write_text('{"cwd":"/somewhere/else"}\n')

    # Lock dir for that UUID — will NOT be in replicated_uuids since conv wasn't copied
    lock_dir = cc_dir / "abc123"
    lock_dir.mkdir()

    with patch("ai_cli.config._get_projects_dir", return_value=projects_base):
        _replicate_to_worktrees(cc_projects, _SERVER_PREFIX, verbose=False)
    # Lock dir was skipped (UUID not in replicated set)
    wt_cc_dir = cc_projects / f"{_SERVER_PREFIX}myapp--worktrees-myapp-1"
    assert not (wt_cc_dir / "abc123").exists()


def test_pre_pull_push_memories_when_committed_and_verbose_then_prints(tmp_path, capsys):
    """Covers line 1244: verbose print after pre-pull memory push."""
    cfg = SyncConfig(
        remote_host="user@host",
        staging_dir=tmp_path / "staging",
        remote_url="ssh://user@host/repo.git",
        local_prefix=_SERVER_PREFIX,
        source_machine="hetzner",
    )
    cc_projects = tmp_path / "cc"
    cc_projects.mkdir()

    with (
        patch("ai_cli.sync.stage_project_files", return_value={"memory_count": 2, "project_names": ["myapp"]}),
        patch("ai_cli.sync.git_commit_staged", return_value=True),
        patch("ai_cli.sync._push_to_remote"),
    ):
        _pre_pull_push_memories(cfg, cc_projects, verbose=True)

    assert "pre-pull: pushed 2" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Group D: dream safety guard (sync.py:1285-1292, 1298-1319, 1322-1323)
# ---------------------------------------------------------------------------


def test_wait_for_dream_completion_when_no_pid_file_then_returns_immediately(tmp_path):
    """Pid file missing → returns immediately without connecting to NATS."""
    with patch("ai_cli.sync._pid_file_path", return_value=tmp_path / "nonexistent.pid"):
        with patch("ai_cli.messaging.NATSClient") as mock_cls:
            _wait_for_dream_completion(verbose=False)
    mock_cls.assert_not_called()


def test_wait_for_dream_completion_when_no_recent_write_then_closes_and_returns(tmp_path):
    """Covers lines 1285-1292, 1294-1296: MEMORY.md mtime is old → close and return."""
    pid_file = tmp_path / "memory-watch.pid"
    pid_file.write_text("12345")

    mock_client = MagicMock()
    mock_client.nc = MagicMock()

    async def fake_connect():
        pass

    async def fake_close():
        pass

    mock_client.connect = fake_connect
    mock_client.close = AsyncMock()

    # cc_projects with a MEMORY.md that's old (mtime 60s ago)
    cc_projects = tmp_path / ".claude" / "projects"
    cc_projects.mkdir(parents=True)
    old_mem = cc_projects / "myproj" / "MEMORY.md"
    old_mem.parent.mkdir()
    old_mem.write_text("old content")
    import os as _os

    _os.utime(old_mem, (0, 0))  # epoch — definitely old

    with (
        patch("ai_cli.sync._pid_file_path", return_value=pid_file),
        patch("ai_cli.messaging.NATSClient", return_value=mock_client),
        patch("pathlib.Path.home", return_value=tmp_path),
    ):
        _wait_for_dream_completion(verbose=False)

    mock_client.close.assert_awaited_once()


def test_wait_for_dream_completion_when_recent_write_and_completes_then_returns(tmp_path):
    """Covers lines 1298-1319, 1322-1323: recent write → subscribe, wait, complete."""

    pid_file = tmp_path / "memory-watch.pid"
    pid_file.write_text("12345")

    mock_sub = AsyncMock()
    mock_nc = MagicMock()
    mock_nc.subscribe = AsyncMock(return_value=mock_sub)

    mock_client = MagicMock()
    mock_client.nc = mock_nc

    async def fake_connect():
        pass

    mock_client.connect = fake_connect
    mock_client.close = AsyncMock()

    # MEMORY.md with current mtime (recent write)
    cc_projects = tmp_path / ".claude" / "projects"
    cc_projects.mkdir(parents=True)
    recent_mem = cc_projects / "myproj" / "MEMORY.md"
    recent_mem.parent.mkdir()
    recent_mem.write_text("fresh")
    # mtime is now (default) — within 5s window

    # wait_for completes immediately (simulate dream done)
    async def fake_wait_for(coro, timeout):
        pass  # returns without raising TimeoutError

    with (
        patch("ai_cli.sync._pid_file_path", return_value=pid_file),
        patch("ai_cli.messaging.NATSClient", return_value=mock_client),
        patch("pathlib.Path.home", return_value=tmp_path),
        patch("asyncio.wait_for", fake_wait_for),
    ):
        _wait_for_dream_completion(verbose=True)

    mock_sub.unsubscribe.assert_awaited()
    mock_client.close.assert_awaited()


def test_wait_for_dream_completion_when_recent_write_and_timeout_then_proceeds(tmp_path):
    """Covers lines 1314-1316: asyncio.TimeoutError path in dream guard."""
    import asyncio

    pid_file = tmp_path / "memory-watch.pid"
    pid_file.write_text("12345")

    mock_sub = AsyncMock()
    mock_nc = MagicMock()
    mock_nc.subscribe = AsyncMock(return_value=mock_sub)

    mock_client = MagicMock()
    mock_client.nc = mock_nc

    async def fake_connect():
        pass

    mock_client.connect = fake_connect
    mock_client.close = AsyncMock()

    cc_projects = tmp_path / ".claude" / "projects"
    cc_projects.mkdir(parents=True)
    recent_mem = cc_projects / "myproj" / "MEMORY.md"
    recent_mem.parent.mkdir()
    recent_mem.write_text("fresh")

    async def fake_wait_for_timeout(coro, timeout):
        raise asyncio.TimeoutError

    with (
        patch("ai_cli.sync._pid_file_path", return_value=pid_file),
        patch("ai_cli.messaging.NATSClient", return_value=mock_client),
        patch("pathlib.Path.home", return_value=tmp_path),
        patch("asyncio.wait_for", fake_wait_for_timeout),
    ):
        _wait_for_dream_completion(verbose=True)

    mock_sub.unsubscribe.assert_awaited()
    mock_client.close.assert_awaited()


def test_wait_for_dream_completion_when_stat_raises_then_continues(tmp_path):
    """Line 1293: stat() raises in rglob loop — recent_write stays False, returns early."""
    pid_file = tmp_path / "memory-watch.pid"
    pid_file.write_text("12345")

    mock_client = MagicMock()
    mock_client.nc = MagicMock()

    async def fake_connect():
        pass

    mock_client.connect = fake_connect
    mock_client.close = AsyncMock()

    # Create a MEMORY.md so rglob finds it, then make stat() raise
    cc_projects = tmp_path / ".claude" / "projects"
    cc_projects.mkdir(parents=True)
    mem = cc_projects / "myproj" / "MEMORY.md"
    mem.parent.mkdir()
    mem.write_text("content")

    # Only break stat on MEMORY.md files, not on directories (needed for .exists())
    _real_stat = Path.stat.__wrapped__ if hasattr(Path.stat, "__wrapped__") else None

    def broken_stat(self):
        if self.name == "MEMORY.md":
            raise PermissionError("no access")
        return type(self).stat.__wrapped__(self) if _real_stat else Path.stat(self)

    # Use a simpler approach: patch at module level within the async function
    import os as _os

    original_os_stat = _os.stat

    def broken_os_stat(path, *args, **kwargs):
        if str(path).endswith("MEMORY.md"):
            raise PermissionError("no access")
        return original_os_stat(path, *args, **kwargs)

    with (
        patch("ai_cli.sync._pid_file_path", return_value=pid_file),
        patch("ai_cli.messaging.NATSClient", return_value=mock_client),
        patch("pathlib.Path.home", return_value=tmp_path),
        patch("os.stat", broken_os_stat),
    ):
        _wait_for_dream_completion(verbose=False)

    # recent_write stays False → closes and returns
    mock_client.close.assert_awaited_once()


def test_wait_for_dream_completion_when_on_completed_fires_then_event_set(tmp_path):
    """Line 1305-1306: on_completed callback fires via subscribe, sets completed event."""
    pid_file = tmp_path / "memory-watch.pid"
    pid_file.write_text("12345")

    mock_sub = AsyncMock()

    # Capture the subscribe callback and invoke it immediately
    captured_cb = None

    async def fake_subscribe(subject, cb):
        nonlocal captured_cb
        captured_cb = cb
        # Immediately invoke the callback to simulate a NATS message arriving
        msg = MagicMock()
        msg.data = b"{}"
        await cb(msg)
        return mock_sub

    mock_nc = MagicMock()
    mock_nc.subscribe = fake_subscribe

    mock_client = MagicMock()
    mock_client.nc = mock_nc

    async def fake_connect():
        pass

    mock_client.connect = fake_connect
    mock_client.close = AsyncMock()

    # MEMORY.md with current mtime (recent write)
    cc_projects = tmp_path / ".claude" / "projects"
    cc_projects.mkdir(parents=True)
    recent_mem = cc_projects / "myproj" / "MEMORY.md"
    recent_mem.parent.mkdir()
    recent_mem.write_text("fresh")

    with (
        patch("ai_cli.sync._pid_file_path", return_value=pid_file),
        patch("ai_cli.messaging.NATSClient", return_value=mock_client),
        patch("pathlib.Path.home", return_value=tmp_path),
    ):
        _wait_for_dream_completion(verbose=True)

    mock_sub.unsubscribe.assert_awaited()
    mock_client.close.assert_awaited()


def test_wait_for_dream_completion_when_asyncio_run_raises_then_nonfatal():
    """Line 1324: outermost except catches asyncio.run failure."""
    with patch("ai_cli.sync._pid_file_path", return_value=Path("/tmp/fake.pid")):
        with patch("pathlib.Path.exists", return_value=True):
            with patch("asyncio.run", side_effect=RuntimeError("event loop broken")):
                _wait_for_dream_completion(verbose=False)  # must not raise


# ---------------------------------------------------------------------------
# clean_worktree_cc_dirs
# ---------------------------------------------------------------------------


def test_clean_worktree_cc_dirs_when_no_cc_dir_then_returns_zeros(tmp_path):
    cc_projects = tmp_path / "cc"
    result = clean_worktree_cc_dirs(cc_projects, _SERVER_PREFIX)
    assert result == (0, 0)


def test_clean_worktree_cc_dirs_when_duplicate_untitled_then_removes(tmp_path):
    """Removes a JSONL copy whose UUID exists in the main CC dir, has no matching title,
    and is not larger than the main copy (i.e. not extended by a resumed conversation)."""
    cc_projects = tmp_path / "cc"
    main_dir = cc_projects / f"{_SERVER_PREFIX}myapp"
    wt_dir = cc_projects / f"{_SERVER_PREFIX}myapp--worktrees-myapp-1"
    main_dir.mkdir(parents=True)
    wt_dir.mkdir(parents=True)

    # File exists in BOTH dirs with identical content — unmodified stale copy
    content = '{"cwd": "/projects/myapp", "msg": "hello"}\n'
    (main_dir / "abc123.jsonl").write_text(content)
    wt_copy = wt_dir / "abc123.jsonl"
    wt_copy.write_text(content)

    removed_jsonl, removed_lock = clean_worktree_cc_dirs(cc_projects, _SERVER_PREFIX)
    assert removed_jsonl == 1
    assert removed_lock == 0
    assert not wt_copy.exists()
    # Original in main dir untouched
    assert (main_dir / "abc123.jsonl").exists()


def test_clean_worktree_cc_dirs_when_worktree_copy_extended_then_keeps(tmp_path):
    """Keeps a JSONL copy in the worktree if it is larger than the main copy
    (conversation was resumed and extended in the worktree — in-progress work)."""
    cc_projects = tmp_path / "cc"
    main_dir = cc_projects / f"{_SERVER_PREFIX}myapp"
    wt_dir = cc_projects / f"{_SERVER_PREFIX}myapp--worktrees-myapp-1"
    main_dir.mkdir(parents=True)
    wt_dir.mkdir(parents=True)

    # Main copy is shorter; worktree copy has more messages appended
    (main_dir / "abc123.jsonl").write_text('{"cwd": "/projects/myapp"}\n')
    wt_copy = wt_dir / "abc123.jsonl"
    wt_copy.write_text('{"cwd": "/projects/myapp"}\n{"role":"user","content":"hello"}\n')

    removed_jsonl, removed_lock = clean_worktree_cc_dirs(cc_projects, _SERVER_PREFIX)
    assert removed_jsonl == 0
    assert wt_copy.exists()


def test_clean_worktree_cc_dirs_when_correct_title_then_keeps(tmp_path):
    """Keeps a JSONL file with the correct customTitle for this worktree."""
    cc_projects = tmp_path / "cc"
    main_dir = cc_projects / f"{_SERVER_PREFIX}myapp"
    wt_dir = cc_projects / f"{_SERVER_PREFIX}myapp--worktrees-myapp-1"
    main_dir.mkdir(parents=True)
    wt_dir.mkdir(parents=True)

    (main_dir / "abc123.jsonl").write_text('{"customTitle": "myapp-1", "cwd": "/projects/myapp"}\n')
    wt_file = wt_dir / "abc123.jsonl"
    wt_file.write_text('{"customTitle": "myapp-1", "cwd": "/projects/myapp/.worktrees/myapp-1"}\n')

    removed_jsonl, removed_lock = clean_worktree_cc_dirs(cc_projects, _SERVER_PREFIX)
    assert removed_jsonl == 0
    assert wt_file.exists()


def test_clean_worktree_cc_dirs_when_unique_to_worktree_then_keeps(tmp_path):
    """Keeps a JSONL file that only exists in the worktree (not a copy of main)."""
    cc_projects = tmp_path / "cc"
    main_dir = cc_projects / f"{_SERVER_PREFIX}myapp"
    wt_dir = cc_projects / f"{_SERVER_PREFIX}myapp--worktrees-myapp-1"
    main_dir.mkdir(parents=True)
    wt_dir.mkdir(parents=True)

    # Only in worktree, not in main — not a duplicate, keep it
    wt_file = wt_dir / "unique99.jsonl"
    wt_file.write_text('{"cwd": "/projects/myapp/.worktrees/myapp-1"}\n')

    removed_jsonl, removed_lock = clean_worktree_cc_dirs(cc_projects, _SERVER_PREFIX)
    assert removed_jsonl == 0
    assert wt_file.exists()


def test_clean_worktree_cc_dirs_when_orphan_lock_dir_then_removes(tmp_path):
    """Removes a UUID lock directory that has no corresponding .jsonl file."""
    cc_projects = tmp_path / "cc"
    wt_dir = cc_projects / f"{_SERVER_PREFIX}myapp--worktrees-myapp-1"
    wt_dir.mkdir(parents=True)

    # Orphan lock dir — no matching .jsonl
    lock = wt_dir / "deadbeef-0000"
    lock.mkdir()
    (lock / "lock").write_text("lock data")

    removed_jsonl, removed_lock = clean_worktree_cc_dirs(cc_projects, _SERVER_PREFIX)
    assert removed_lock == 1
    assert not lock.exists()


def test_clean_worktree_cc_dirs_when_lock_has_jsonl_then_keeps_lock(tmp_path):
    """Keeps a lock dir when a matching .jsonl exists in the same CC dir."""
    cc_projects = tmp_path / "cc"
    wt_dir = cc_projects / f"{_SERVER_PREFIX}myapp--worktrees-myapp-1"
    wt_dir.mkdir(parents=True)

    jsonl = wt_dir / "abc123.jsonl"
    jsonl.write_text('{"customTitle": "myapp-1"}\n')
    lock = wt_dir / "abc123"
    lock.mkdir()

    removed_jsonl, removed_lock = clean_worktree_cc_dirs(cc_projects, _SERVER_PREFIX)
    assert removed_lock == 0
    assert lock.exists()


def test_clean_worktree_cc_dirs_when_dry_run_then_no_deletions(tmp_path):
    """dry_run=True counts removals but does not delete files."""
    cc_projects = tmp_path / "cc"
    main_dir = cc_projects / f"{_SERVER_PREFIX}myapp"
    wt_dir = cc_projects / f"{_SERVER_PREFIX}myapp--worktrees-myapp-1"
    main_dir.mkdir(parents=True)
    wt_dir.mkdir(parents=True)

    # Identical content — stale unmodified copy
    content = '{"cwd": "/projects/myapp", "x": 1}\n'
    (main_dir / "abc123.jsonl").write_text(content)
    wt_copy = wt_dir / "abc123.jsonl"
    wt_copy.write_text(content)
    orphan = wt_dir / "deadbeef"
    orphan.mkdir()

    removed_jsonl, removed_lock = clean_worktree_cc_dirs(cc_projects, _SERVER_PREFIX, dry_run=True)
    assert removed_jsonl == 1
    assert removed_lock == 1
    # Files still exist — dry run did not delete
    assert wt_copy.exists()
    assert orphan.exists()


def test_clean_worktree_cc_dirs_when_verbose_then_prints(tmp_path, capsys):
    cc_projects = tmp_path / "cc"
    main_dir = cc_projects / f"{_SERVER_PREFIX}myapp"
    wt_dir = cc_projects / f"{_SERVER_PREFIX}myapp--worktrees-myapp-1"
    main_dir.mkdir(parents=True)
    wt_dir.mkdir(parents=True)

    # Identical content — stale unmodified copy
    content = '{"cwd": "/projects/myapp", "x": 1}\n'
    (main_dir / "abc123.jsonl").write_text(content)
    (wt_dir / "abc123.jsonl").write_text(content)
    orphan = wt_dir / "deadbeef"
    orphan.mkdir()

    clean_worktree_cc_dirs(cc_projects, _SERVER_PREFIX, verbose=True)
    out = capsys.readouterr().out
    assert "stale copy" in out
    assert "orphan lock" in out


def test_clean_worktree_cc_dirs_when_main_dir_missing_then_no_error(tmp_path):
    """Worktree CC dir exists but there is no main project CC dir — skip gracefully."""
    cc_projects = tmp_path / "cc"
    wt_dir = cc_projects / f"{_SERVER_PREFIX}myapp--worktrees-myapp-1"
    wt_dir.mkdir(parents=True)

    # File that looks like a duplicate but main dir doesn't exist
    wt_file = wt_dir / "abc123.jsonl"
    wt_file.write_text('{"cwd": "/projects/myapp/.worktrees/myapp-1"}\n')

    removed_jsonl, removed_lock = clean_worktree_cc_dirs(cc_projects, _SERVER_PREFIX)
    # No main dir → UUID not in main_uuids → file is unique to worktree → keep
    assert removed_jsonl == 0
    assert wt_file.exists()


# ---------------------------------------------------------------------------
# repair_worktree_cc_dir
# ---------------------------------------------------------------------------


def test_repair_worktree_cc_dir_when_project_missing_then_returns_zero(tmp_path, capsys):
    cc_projects = tmp_path / "cc"
    cc_projects.mkdir(parents=True)
    projects_base = tmp_path / "projects"
    projects_base.mkdir()

    with patch("ai_cli.config._get_projects_dir", return_value=projects_base):
        result = repair_worktree_cc_dir("missing-project", "session-1", cc_projects, _SERVER_PREFIX)
    assert result == 0
    assert "not found" in capsys.readouterr().err


def test_repair_worktree_cc_dir_when_worktree_missing_then_returns_zero(tmp_path, capsys):
    cc_projects = tmp_path / "cc"
    cc_projects.mkdir(parents=True)
    projects_base = tmp_path / "projects"
    myapp = projects_base / "myapp"
    myapp.mkdir(parents=True)

    with patch("ai_cli.config._get_projects_dir", return_value=projects_base):
        result = repair_worktree_cc_dir("myapp", "no-such-wt", cc_projects, _SERVER_PREFIX)
    assert result == 0
    assert "not found" in capsys.readouterr().err


def test_repair_worktree_cc_dir_when_no_main_cc_dir_then_returns_zero(tmp_path, capsys):
    cc_projects = tmp_path / "cc"
    cc_projects.mkdir(parents=True)
    projects_base = tmp_path / "projects"
    myapp = projects_base / "myapp"
    wt_dir = myapp / ".worktrees" / "myapp-1"
    wt_dir.mkdir(parents=True)

    with patch("ai_cli.config._get_projects_dir", return_value=projects_base):
        result = repair_worktree_cc_dir("myapp", "myapp-1", cc_projects, _SERVER_PREFIX)
    assert result == 0
    assert "no CC dir" in capsys.readouterr().err


def test_repair_worktree_cc_dir_when_conversations_exist_then_copies(tmp_path, capsys):
    """Copies all main project JSONL files to the worktree CC dir with cwd translation."""
    cc_projects = tmp_path / "cc"
    projects_base = tmp_path / "projects"
    myapp = projects_base / "myapp"
    wt_path = myapp / ".worktrees" / "myapp-1"
    wt_path.mkdir(parents=True)

    main_cc = cc_projects / f"{_SERVER_PREFIX}myapp"
    main_cc.mkdir(parents=True)

    main_cwd = str(myapp)
    wt_cwd = str(wt_path)
    conv = main_cc / "abc123.jsonl"
    conv.write_text(f'{{"cwd":"{main_cwd}", "msg":"hello"}}\n')

    with patch("ai_cli.config._get_projects_dir", return_value=projects_base):
        result = repair_worktree_cc_dir("myapp", "myapp-1", cc_projects, _SERVER_PREFIX, verbose=True)

    assert result == 1
    wt_cc = cc_projects / f"{_SERVER_PREFIX}myapp--worktrees-myapp-1"
    dst = wt_cc / "abc123.jsonl"
    assert dst.exists()
    content = dst.read_text()
    # cwd should be translated to worktree path; original standalone cwd value replaced
    assert f'"cwd":"{wt_cwd}"' in content
    assert f'"cwd":"{main_cwd}"' not in content
    out = capsys.readouterr().out
    assert "copy" in out


def test_repair_worktree_cc_dir_when_file_already_exists_then_skips(tmp_path):
    """Does not overwrite a file that already exists in the worktree CC dir."""
    cc_projects = tmp_path / "cc"
    projects_base = tmp_path / "projects"
    myapp = projects_base / "myapp"
    wt_path = myapp / ".worktrees" / "myapp-1"
    wt_path.mkdir(parents=True)

    main_cc = cc_projects / f"{_SERVER_PREFIX}myapp"
    main_cc.mkdir(parents=True)
    wt_cc = cc_projects / f"{_SERVER_PREFIX}myapp--worktrees-myapp-1"
    wt_cc.mkdir(parents=True)

    (main_cc / "abc123.jsonl").write_text('{"cwd": "/projects/myapp"}\n')
    existing = wt_cc / "abc123.jsonl"
    existing.write_text("already here\n")

    with patch("ai_cli.config._get_projects_dir", return_value=projects_base):
        result = repair_worktree_cc_dir("myapp", "myapp-1", cc_projects, _SERVER_PREFIX)

    assert result == 0
    assert existing.read_text() == "already here\n"


def test_repair_worktree_cc_dir_when_orphan_lock_exists_then_removes(tmp_path):
    """Removes orphan lock dirs before copying conversations."""
    cc_projects = tmp_path / "cc"
    projects_base = tmp_path / "projects"
    myapp = projects_base / "myapp"
    wt_path = myapp / ".worktrees" / "myapp-1"
    wt_path.mkdir(parents=True)

    main_cc = cc_projects / f"{_SERVER_PREFIX}myapp"
    main_cc.mkdir(parents=True)
    wt_cc = cc_projects / f"{_SERVER_PREFIX}myapp--worktrees-myapp-1"
    wt_cc.mkdir(parents=True)

    (main_cc / "abc123.jsonl").write_text('{"cwd": "/projects/myapp"}\n')
    # Orphan lock dir — no matching .jsonl in worktree
    orphan = wt_cc / "deadbeef-0000"
    orphan.mkdir()

    with patch("ai_cli.config._get_projects_dir", return_value=projects_base):
        repair_worktree_cc_dir("myapp", "myapp-1", cc_projects, _SERVER_PREFIX)

    assert not orphan.exists()


def test_repair_worktree_cc_dir_when_dry_run_then_no_writes(tmp_path, capsys):
    cc_projects = tmp_path / "cc"
    projects_base = tmp_path / "projects"
    myapp = projects_base / "myapp"
    wt_path = myapp / ".worktrees" / "myapp-1"
    wt_path.mkdir(parents=True)

    main_cc = cc_projects / f"{_SERVER_PREFIX}myapp"
    main_cc.mkdir(parents=True)
    (main_cc / "abc123.jsonl").write_text('{"cwd": "/projects/myapp"}\n')

    with patch("ai_cli.config._get_projects_dir", return_value=projects_base):
        result = repair_worktree_cc_dir("myapp", "myapp-1", cc_projects, _SERVER_PREFIX, dry_run=True)

    assert result == 1
    wt_cc = cc_projects / f"{_SERVER_PREFIX}myapp--worktrees-myapp-1"
    assert not wt_cc.exists()
    assert "dry-run" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _llm_merge_memory_conflict
# ---------------------------------------------------------------------------


def test_llm_merge_memory_conflict_when_no_api_key_then_returns_none(monkeypatch):
    from ai_cli.sync import _llm_merge_memory_conflict

    monkeypatch.delenv("GOOGLE_API_KEY_TIER_1", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    result = _llm_merge_memory_conflict("<<<<<<< HEAD\nlocal\n=======\nremote\n>>>>>>> main\n", "test.md")
    assert result is None


def test_llm_merge_memory_conflict_when_api_exception_then_returns_none(monkeypatch):
    from ai_cli.sync import _llm_merge_memory_conflict

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = RuntimeError("network error")

    with patch("google.genai.Client", return_value=mock_client):
        result = _llm_merge_memory_conflict("<<<<<<< HEAD\nlocal\n=======\nremote\n>>>>>>> main\n", "test.md")

    assert result is None


def test_llm_merge_memory_conflict_when_llm_leaves_markers_then_returns_none(monkeypatch):
    """If the LLM output still contains conflict markers, we must reject it — applying it would corrupt the file."""
    from ai_cli.sync import _llm_merge_memory_conflict

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

    mock_response = MagicMock()
    mock_response.text = "<<<<<<< HEAD\nstill has markers\n>>>>>>> main\n"
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("google.genai.Client", return_value=mock_client):
        result = _llm_merge_memory_conflict("<<<<<<< HEAD\nlocal\n=======\nremote\n>>>>>>> main\n", "test.md")

    assert result is None


def test_llm_merge_memory_conflict_when_llm_succeeds_then_returns_merged_content(monkeypatch):
    """Successful merge: both sides preserved, no conflict markers in output."""
    from ai_cli.sync import _llm_merge_memory_conflict

    monkeypatch.setenv("GOOGLE_API_KEY_TIER_1", "fake-key")

    merged_text = "# Memory\n- local entry\n- remote entry\n"
    mock_response = MagicMock()
    mock_response.text = merged_text
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    conflict_content = "<<<<<<< HEAD\n- local entry\n=======\n- remote entry\n>>>>>>> main\n"

    with patch("google.genai.Client", return_value=mock_client):
        result = _llm_merge_memory_conflict(conflict_content, "memory.md")

    assert result == merged_text
    # Verify the prompt included the conflict content and filename
    call_args = mock_client.models.generate_content.call_args
    prompt = call_args[1]["contents"] if "contents" in call_args[1] else call_args[0][1]
    assert "memory.md" in prompt
    assert conflict_content in prompt


def test_llm_merge_memory_conflict_uses_tier1_key_over_gemini_key(monkeypatch):
    """GOOGLE_API_KEY_TIER_1 should be preferred over GEMINI_API_KEY when both are set."""
    from ai_cli.sync import _llm_merge_memory_conflict

    monkeypatch.setenv("GOOGLE_API_KEY_TIER_1", "tier1-key")
    monkeypatch.setenv("GEMINI_API_KEY", "fallback-key")

    mock_response = MagicMock()
    mock_response.text = "merged clean"
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    captured_key = []

    def capture_client(api_key):
        captured_key.append(api_key)
        return mock_client

    with patch("google.genai.Client", side_effect=capture_client):
        _llm_merge_memory_conflict("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> main\n", "f.md")

    assert captured_key == ["tier1-key"]


# ---------------------------------------------------------------------------
# apply_pull_files — LLM auto-merge path for conflict markers
# ---------------------------------------------------------------------------


def test_apply_pull_files_when_conflict_markers_and_llm_succeeds_then_file_written_no_conflict_file(tmp_path):
    """When LLM successfully merges conflict markers, the merged content goes to dst and staging is updated.
    No .conflict file should be created. applied_count should reflect the merge."""
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"
    conflict_dir = tmp_path / "conflicts"

    (staging_dir / "myproject" / "memory").mkdir(parents=True)
    conflict_content = "<<<<<<< HEAD\n- local note\n=======\n- remote note\n>>>>>>> main\n"
    staging_mem = staging_dir / "myproject" / "memory" / "project_notes.md"
    staging_mem.write_text(conflict_content)

    local_project_dir = cc_projects_dir / "-Users-user-projects-myproject"
    local_project_dir.mkdir(parents=True)

    merged_content = "- local note\n- remote note\n"
    mock_response = MagicMock()
    mock_response.text = merged_content
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("ai_cli.sync.CONFLICT_DIR", conflict_dir):
        with patch("google.genai.Client", return_value=mock_client):
            with patch.dict("os.environ", {"GEMINI_API_KEY": "fake-key"}):
                result = apply_pull_files(
                    staging_dir=staging_dir,
                    cc_projects_dir=cc_projects_dir,
                    local_prefix=_MAC_PREFIX,
                    memories_only=False,
                    verbose=False,
                    dry_run=False,
                )

    # No conflicts — the LLM resolved it
    assert result["conflicts"] == []
    # Merged content written to the local CC file
    local_mem = local_project_dir / "memory" / "project_notes.md"
    assert local_mem.exists()
    assert local_mem.read_text() == merged_content
    # Staging file also updated so future pulls don't re-detect
    assert staging_mem.read_text() == merged_content
    # No conflict file created
    assert not conflict_dir.exists() or not list(conflict_dir.rglob("*.conflict"))


def test_apply_pull_files_when_conflict_markers_and_llm_fails_then_conflict_file_written(tmp_path):
    """When LLM fails (no key), conflict file is written and local CC file is NOT modified."""
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"
    conflict_dir = tmp_path / "conflicts"

    (staging_dir / "myproject" / "memory").mkdir(parents=True)
    conflict_content = "<<<<<<< HEAD\nlocal content\n=======\nremote content\n>>>>>>> origin/main\n"
    (staging_dir / "myproject" / "memory" / "project_current_work.md").write_text(conflict_content)

    local_project_dir = cc_projects_dir / "-Users-user-projects-myproject"
    local_mem_dir = local_project_dir / "memory"
    local_mem_dir.mkdir(parents=True)
    local_mem = local_mem_dir / "project_current_work.md"
    local_mem.write_text("original local content\n")

    with patch("ai_cli.sync.CONFLICT_DIR", conflict_dir):
        with patch.dict("os.environ", {}, clear=True):  # No API key → LLM returns None
            result = apply_pull_files(
                staging_dir=staging_dir,
                cc_projects_dir=cc_projects_dir,
                local_prefix=_MAC_PREFIX,
                memories_only=False,
                verbose=False,
                dry_run=False,
            )

    assert len(result["conflicts"]) == 1
    # Conflict file written
    conflict_path = conflict_dir / "myproject" / "memory" / "project_current_work.md.conflict"
    assert conflict_path.exists()
    # Local CC file untouched — conflict markers must not overwrite good data
    assert local_mem.read_text() == "original local content\n"


def test_apply_pull_files_when_conflict_markers_and_dry_run_then_llm_not_called(tmp_path):
    """In dry-run mode, LLM is never called and nothing is written."""
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"
    conflict_dir = tmp_path / "conflicts"

    (staging_dir / "myproject" / "memory").mkdir(parents=True)
    conflict_content = "<<<<<<< HEAD\nlocal\n=======\nremote\n>>>>>>> main\n"
    (staging_dir / "myproject" / "memory" / "note.md").write_text(conflict_content)
    cc_projects_dir.mkdir()

    mock_llm = MagicMock()

    with patch("ai_cli.sync.CONFLICT_DIR", conflict_dir):
        with patch("ai_cli.sync._llm_merge_memory_conflict", mock_llm):
            apply_pull_files(
                staging_dir=staging_dir,
                cc_projects_dir=cc_projects_dir,
                local_prefix=_MAC_PREFIX,
                memories_only=False,
                verbose=False,
                dry_run=True,
            )

    mock_llm.assert_not_called()
    assert not conflict_dir.exists()


def test_apply_pull_files_when_conflict_markers_and_llm_succeeds_verbose_then_prints_auto_merged(tmp_path, capsys):
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"
    conflict_dir = tmp_path / "conflicts"

    (staging_dir / "myproject" / "memory").mkdir(parents=True)
    conflict_content = "<<<<<<< HEAD\na\n=======\nb\n>>>>>>> main\n"
    (staging_dir / "myproject" / "memory" / "info.md").write_text(conflict_content)
    (cc_projects_dir / "-Users-user-projects-myproject").mkdir(parents=True)

    merged = "a\nb\n"
    with patch("ai_cli.sync.CONFLICT_DIR", conflict_dir):
        with patch("ai_cli.sync._llm_merge_memory_conflict", return_value=merged):
            apply_pull_files(
                staging_dir=staging_dir,
                cc_projects_dir=cc_projects_dir,
                local_prefix=_MAC_PREFIX,
                memories_only=False,
                verbose=True,
                dry_run=False,
            )

    out = capsys.readouterr().out
    assert "auto-merged" in out
    assert "memory/info.md" in out


# ---------------------------------------------------------------------------
# sync_resolve
# ---------------------------------------------------------------------------


def test_sync_resolve_when_no_conflict_files_then_returns_0_and_prints_clean(tmp_path, capsys):
    from ai_cli.sync import sync_resolve

    conflict_dir = tmp_path / "conflicts"
    cc_dir = tmp_path / "cc"
    cc_dir.mkdir()

    with patch("ai_cli.sync.CONFLICT_DIR", conflict_dir):
        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
            with patch("ai_cli.sync.load_sync_config", return_value=MagicMock(local_prefix=_MAC_PREFIX)):
                result = sync_resolve([])

    assert result == 0
    assert "No conflict files found" in capsys.readouterr().out


def test_sync_resolve_removes_jsonl_backup_files(tmp_path, capsys):
    """JSONL conflict backups (conflict-*.jsonl) should be deleted — local already won."""
    from ai_cli.sync import sync_resolve

    conflict_dir = tmp_path / "conflicts"
    project_dir = conflict_dir / "myproject"
    project_dir.mkdir(parents=True)
    backup = project_dir / "conflict-abc123.jsonl"
    backup.write_text('{"type":"human"}\n')

    cc_dir = tmp_path / "cc"
    cc_dir.mkdir()

    with patch("ai_cli.sync.CONFLICT_DIR", conflict_dir):
        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
            with patch("ai_cli.sync.load_sync_config", return_value=MagicMock(local_prefix=_MAC_PREFIX)):
                result = sync_resolve([])

    assert result == 0
    assert not backup.exists()


def test_sync_resolve_removes_cascading_conflict_artifacts(tmp_path):
    """Files with .conflict.conflict in the name are bug artifacts — delete unconditionally."""
    from ai_cli.sync import sync_resolve

    conflict_dir = tmp_path / "conflicts"
    project_dir = conflict_dir / "myproject"
    project_dir.mkdir(parents=True)
    artifact = project_dir / "MEMORY.md.conflict.conflict"
    artifact.write_text("cascading junk")

    cc_dir = tmp_path / "cc"
    cc_dir.mkdir()

    with patch("ai_cli.sync.CONFLICT_DIR", conflict_dir):
        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
            with patch("ai_cli.sync.load_sync_config", return_value=MagicMock(local_prefix=_MAC_PREFIX)):
                sync_resolve([])

    assert not artifact.exists()


def test_sync_resolve_when_memory_conflict_file_and_llm_succeeds_then_local_file_updated(tmp_path):
    """LLM merge writes merged content to the local CC file and removes the .conflict file."""
    from ai_cli.sync import sync_resolve

    conflict_dir = tmp_path / "conflicts"
    project_conflict_dir = conflict_dir / "myproject" / "memory"
    project_conflict_dir.mkdir(parents=True)
    conflict_file = project_conflict_dir / "MEMORY.md.conflict"
    conflict_content = "<<<<<<< HEAD\n- local\n=======\n- remote\n>>>>>>> main\n"
    conflict_file.write_text(conflict_content)

    cc_dir = tmp_path / "cc"
    local_project = cc_dir / "-Users-user-projects-myproject" / "memory"
    local_project.mkdir(parents=True)
    local_mem = local_project / "MEMORY.md"
    local_mem.write_text("old local content")

    merged = "- local\n- remote\n"

    with patch("ai_cli.sync.CONFLICT_DIR", conflict_dir):
        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
            with patch("ai_cli.sync.load_sync_config", return_value=MagicMock(local_prefix=_MAC_PREFIX)):
                with patch("ai_cli.sync._llm_merge_memory_conflict", return_value=merged):
                    result = sync_resolve([])

    assert result == 0
    # Local CC file updated with merged content
    assert local_mem.read_text() == merged
    # Conflict file removed after successful merge
    assert not conflict_file.exists()


def test_sync_resolve_when_memory_conflict_file_and_llm_fails_then_returns_1(tmp_path, capsys):
    """When LLM can't merge (no key), mem_failed is counted and exit code is 1."""
    from ai_cli.sync import sync_resolve

    conflict_dir = tmp_path / "conflicts"
    project_conflict_dir = conflict_dir / "myproject" / "memory"
    project_conflict_dir.mkdir(parents=True)
    conflict_file = project_conflict_dir / "notes.md.conflict"
    conflict_file.write_text("<<<<<<< HEAD\nlocal\n=======\nremote\n>>>>>>> main\n")

    cc_dir = tmp_path / "cc"
    cc_dir.mkdir()

    with patch("ai_cli.sync.CONFLICT_DIR", conflict_dir):
        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
            with patch("ai_cli.sync.load_sync_config", return_value=MagicMock(local_prefix=_MAC_PREFIX)):
                with patch("ai_cli.sync._llm_merge_memory_conflict", return_value=None):
                    result = sync_resolve([])

    # Exit code 1 when unresolved memory files remain
    assert result == 1
    # Conflict file preserved so user can resolve manually
    assert conflict_file.exists()
    err = capsys.readouterr().err
    assert "Could not auto-merge" in err


def test_sync_resolve_when_conflict_file_has_no_markers_then_treated_as_artifact(tmp_path):
    """.conflict files without actual conflict markers are stale artifacts — delete them."""
    from ai_cli.sync import sync_resolve

    conflict_dir = tmp_path / "conflicts"
    project_conflict_dir = conflict_dir / "myproject" / "memory"
    project_conflict_dir.mkdir(parents=True)
    stale = project_conflict_dir / "old_note.md.conflict"
    stale.write_text("no markers here — just old content")

    cc_dir = tmp_path / "cc"
    cc_dir.mkdir()

    with patch("ai_cli.sync.CONFLICT_DIR", conflict_dir):
        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
            with patch("ai_cli.sync.load_sync_config", return_value=MagicMock(local_prefix=_MAC_PREFIX)):
                result = sync_resolve([])

    assert result == 0
    assert not stale.exists()


def test_sync_resolve_when_dry_run_then_nothing_deleted(tmp_path, capsys):
    """Dry run must not delete or modify any files."""
    from ai_cli.sync import sync_resolve

    conflict_dir = tmp_path / "conflicts"
    project_dir = conflict_dir / "myproject"
    project_dir.mkdir(parents=True)
    backup = project_dir / "conflict-abc.jsonl"
    backup.write_text('{"type":"human"}\n')
    artifact = project_dir / "MEMORY.md.conflict.conflict"
    artifact.write_text("junk")

    cc_dir = tmp_path / "cc"
    cc_dir.mkdir()

    with patch("ai_cli.sync.CONFLICT_DIR", conflict_dir):
        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
            with patch("ai_cli.sync.load_sync_config", return_value=MagicMock(local_prefix=_MAC_PREFIX)):
                result = sync_resolve(["--dry-run"])

    assert result == 0
    assert backup.exists()
    assert artifact.exists()
    out = capsys.readouterr().out
    assert "[dry-run]" in out


def test_sync_resolve_verbose_prints_each_action(tmp_path, capsys):
    from ai_cli.sync import sync_resolve

    conflict_dir = tmp_path / "conflicts"
    project_dir = conflict_dir / "myproject"
    project_dir.mkdir(parents=True)
    (project_dir / "conflict-abc.jsonl").write_text('{"type":"human"}\n')
    (project_dir / "MEMORY.md.conflict.conflict").write_text("junk")

    cc_dir = tmp_path / "cc"
    cc_dir.mkdir()

    with patch("ai_cli.sync.CONFLICT_DIR", conflict_dir):
        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
            with patch("ai_cli.sync.load_sync_config", return_value=MagicMock(local_prefix=_MAC_PREFIX)):
                sync_resolve(["--verbose"])

    out = capsys.readouterr().out
    assert "jsonl backup" in out
    assert "cascading artifact" in out


def test_sync_resolve_removes_empty_conflict_subdirectories_after_cleanup(tmp_path):
    """After deleting all files in a conflict subdir, the empty dir itself should be removed."""
    from ai_cli.sync import sync_resolve

    conflict_dir = tmp_path / "conflicts"
    project_dir = conflict_dir / "myproject"
    project_dir.mkdir(parents=True)
    backup = project_dir / "conflict-abc.jsonl"
    backup.write_text('{"type":"human"}\n')

    cc_dir = tmp_path / "cc"
    cc_dir.mkdir()

    with patch("ai_cli.sync.CONFLICT_DIR", conflict_dir):
        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
            with patch("ai_cli.sync.load_sync_config", return_value=MagicMock(local_prefix=_MAC_PREFIX)):
                sync_resolve([])

    # The now-empty project subdir should be removed
    assert not project_dir.exists()


def test_sync_resolve_handles_legacy_conflict_files_in_cc_projects_dir(tmp_path):
    """Legacy .conflict files inside the CC projects dir (old location) are also cleaned up."""
    from ai_cli.sync import sync_resolve

    conflict_dir = tmp_path / "conflicts"  # new location — empty

    cc_dir = tmp_path / "cc"
    legacy_project = cc_dir / "-Users-user-projects-myproject" / "memory"
    legacy_project.mkdir(parents=True)
    legacy_conflict = legacy_project / "MEMORY.md.conflict"
    legacy_conflict.write_text("no markers here")

    with patch("ai_cli.sync.CONFLICT_DIR", conflict_dir):
        with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
            with patch("ai_cli.sync.load_sync_config", return_value=MagicMock(local_prefix=_MAC_PREFIX)):
                result = sync_resolve([])

    assert result == 0
    # Legacy conflict file removed (no markers → artifact)
    assert not legacy_conflict.exists()


# ---------------------------------------------------------------------------
# apply_pull_files — staging_to_commit populated on LLM merge success
# ---------------------------------------------------------------------------


def test_apply_pull_files_when_llm_merge_succeeds_then_staging_to_commit_populated(tmp_path):
    """LLM merge success must populate staging_to_commit so sync_pull can commit+push it.
    Without this, the staging file is written on disk but never committed, and the next
    git fetch+merge re-introduces the conflict-marker content from the remote."""
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"
    conflict_dir = tmp_path / "conflicts"

    (staging_dir / "myproject" / "memory").mkdir(parents=True)
    conflict_content = "<<<<<<< HEAD\n- local\n=======\n- remote\n>>>>>>> main\n"
    staging_mem = staging_dir / "myproject" / "memory" / "notes.md"
    staging_mem.write_text(conflict_content)

    (cc_projects_dir / "-Users-user-projects-myproject").mkdir(parents=True)

    merged = "- local\n- remote\n"

    with patch("ai_cli.sync.CONFLICT_DIR", conflict_dir):
        with patch("ai_cli.sync._llm_merge_memory_conflict", return_value=merged):
            result = apply_pull_files(
                staging_dir=staging_dir,
                cc_projects_dir=cc_projects_dir,
                local_prefix=_MAC_PREFIX,
                memories_only=False,
                verbose=False,
                dry_run=False,
            )

    # staging_to_commit must contain the staging file so sync_pull can git-add+commit+push it
    assert staging_mem in result["staging_to_commit"]
    # No conflicts — LLM resolved it
    assert result["conflicts"] == []


def test_apply_pull_files_when_llm_merge_fails_then_staging_to_overwrite_populated(tmp_path):
    """LLM merge failure must populate staging_to_overwrite (copy local→staging then commit).
    This overwrites the conflict-marker content in staging with the clean local file, so
    future pulls see 'identical' instead of re-detecting the same conflict."""
    staging_dir = tmp_path / "staging"
    cc_projects_dir = tmp_path / "cc_projects"
    conflict_dir = tmp_path / "conflicts"

    (staging_dir / "myproject" / "memory").mkdir(parents=True)
    conflict_content = "<<<<<<< HEAD\nlocal\n=======\nremote\n>>>>>>> main\n"
    (staging_dir / "myproject" / "memory" / "notes.md").write_text(conflict_content)

    local_project = cc_projects_dir / "-Users-user-projects-myproject" / "memory"
    local_project.mkdir(parents=True)
    (local_project / "notes.md").write_text("clean local content\n")

    with patch("ai_cli.sync.CONFLICT_DIR", conflict_dir):
        with patch("ai_cli.sync._llm_merge_memory_conflict", return_value=None):
            result = apply_pull_files(
                staging_dir=staging_dir,
                cc_projects_dir=cc_projects_dir,
                local_prefix=_MAC_PREFIX,
                memories_only=False,
                verbose=False,
                dry_run=False,
            )

    # staging_to_overwrite holds (local_cc_file, staging_file) so sync_pull can copy+commit+push
    overwrite_staging_files = [staging_dst for _, staging_dst in result["staging_to_overwrite"]]
    staging_mem = staging_dir / "myproject" / "memory" / "notes.md"
    assert staging_mem in overwrite_staging_files
    assert len(result["conflicts"]) == 1


# ---------------------------------------------------------------------------
# sync_pull — staging commit+push after conflict resolution
# ---------------------------------------------------------------------------


def _make_pull_cfg(tmp_path):
    from ai_cli.sync import SyncConfig

    return SyncConfig(
        remote_host="host",
        remote_url="ssh://host/repo.git",
        staging_dir=tmp_path / "staging",
        source_machine="mac",
        local_prefix=_MAC_PREFIX,
    )


def test_sync_pull_when_jsonl_conflict_then_staging_commit_and_push(tmp_path):
    """After a JSONL divergence, sync_pull must commit the staging overwrite AND push it
    to remote. Without the push the next pull re-fetches the diverged content and re-fires
    the notification — the double-fire bug observed in the April 20 conflict log."""
    cfg = _make_pull_cfg(tmp_path)
    cc_dir = tmp_path / ".claude" / "projects"
    cc_dir.mkdir(parents=True)

    local_file = cc_dir / "conflict.jsonl"
    local_file.write_text('{"type":"human"}\n')
    staging_file = cfg.staging_dir / "conflict.jsonl"
    (cfg.staging_dir).mkdir(parents=True)
    staging_file.write_text('{"type":"human"}\n{"type":"ai"}\n')

    push_called = []

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.is_cc_active_locally", return_value=False):
            with patch("ai_cli.sync.init_staging_repo"):
                with patch("ai_cli.sync._pre_pull_push_memories"):
                    with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
                        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
                            with patch(
                                "ai_cli.sync.apply_pull_files",
                                return_value={
                                    "conflicts": ["jsonl myproject/conv.jsonl — remote saved as ..."],
                                    "applied_count": 0,
                                    "staging_to_overwrite": [(local_file, staging_file)],
                                    "staging_to_commit": [],
                                },
                            ):
                                with patch("ai_cli.sync.translate_history_jsonl"):
                                    with patch("ai_cli.sync.retranslate_project_jsonls"):
                                        with patch("ai_cli.sync.purge_phantom_history_entries"):
                                            with patch("ai_cli.sync.notify_conflicts"):
                                                with patch(
                                                    "ai_cli.sync._push_to_remote",
                                                    side_effect=lambda *a, **kw: push_called.append(True),
                                                ):
                                                    result = sync_pull([])

    assert result == 2
    # Push must be called so the remote staging repo reflects "take local"
    assert push_called, "staging remote push was not called after JSONL conflict resolution"


def test_sync_pull_when_llm_merge_succeeded_then_staging_commit_and_push(tmp_path):
    """When LLM merge succeeded (no conflicts in result), sync_pull must still commit
    and push the staging_to_commit files. Without this the merged content lives only in
    the staging working directory; the next git fetch+merge overwrites it from the remote,
    re-introducing the conflict markers."""
    cfg = _make_pull_cfg(tmp_path)
    cc_dir = tmp_path / ".claude" / "projects"
    cc_dir.mkdir(parents=True)
    (cfg.staging_dir).mkdir(parents=True)
    staging_file = cfg.staging_dir / "merged.md"
    staging_file.write_text("merged content\n")

    push_called = []

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.is_cc_active_locally", return_value=False):
            with patch("ai_cli.sync.init_staging_repo"):
                with patch("ai_cli.sync._pre_pull_push_memories"):
                    with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
                        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
                            with patch(
                                "ai_cli.sync.apply_pull_files",
                                return_value={
                                    "conflicts": [],
                                    "applied_count": 1,
                                    "staging_to_overwrite": [],
                                    "staging_to_commit": [staging_file],
                                },
                            ):
                                with patch("ai_cli.sync.translate_history_jsonl"):
                                    with patch("ai_cli.sync.retranslate_project_jsonls"):
                                        with patch("ai_cli.sync.purge_phantom_history_entries"):
                                            with patch(
                                                "ai_cli.sync._push_to_remote",
                                                side_effect=lambda *a, **kw: push_called.append(True),
                                            ):
                                                result = sync_pull([])

    assert result == 0  # No unresolved conflicts
    assert push_called, "staging remote push was not called after LLM merge commit"


def test_sync_pull_when_no_staging_changes_then_no_push(tmp_path):
    """When apply_pull_files reports no staging changes, sync_pull must not call push.
    Pushing on every clean pull would add unnecessary latency."""
    cfg = _make_pull_cfg(tmp_path)
    cc_dir = tmp_path / ".claude" / "projects"
    cc_dir.mkdir(parents=True)

    push_called = []

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.is_cc_active_locally", return_value=False):
            with patch("ai_cli.sync.init_staging_repo"):
                with patch("ai_cli.sync._pre_pull_push_memories"):
                    with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
                        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
                            with patch(
                                "ai_cli.sync.apply_pull_files",
                                return_value={
                                    "conflicts": [],
                                    "applied_count": 2,
                                    "staging_to_overwrite": [],
                                    "staging_to_commit": [],
                                },
                            ):
                                with patch("ai_cli.sync.translate_history_jsonl"):
                                    with patch("ai_cli.sync.retranslate_project_jsonls"):
                                        with patch("ai_cli.sync.purge_phantom_history_entries"):
                                            with patch(
                                                "ai_cli.sync._push_to_remote",
                                                side_effect=lambda *a, **kw: push_called.append(True),
                                            ):
                                                result = sync_pull([])

    assert result == 0
    assert not push_called, "push was called on a clean pull with no staging changes"


def test_sync_pull_when_dry_run_with_conflicts_then_no_staging_commit_or_push(tmp_path):
    """Dry-run must never write to staging or push, even when conflicts are reported."""
    cfg = _make_pull_cfg(tmp_path)
    cc_dir = tmp_path / ".claude" / "projects"
    cc_dir.mkdir(parents=True)

    push_called = []

    with patch("ai_cli.sync.load_sync_config", return_value=cfg):
        with patch("ai_cli.sync.is_cc_active_locally", return_value=False):
            with patch("ai_cli.sync._cc_projects_dir", return_value=cc_dir):
                with patch(
                    "ai_cli.sync.apply_pull_files",
                    return_value={
                        "conflicts": ["memory proj/notes.md — .conflict file written"],
                        "applied_count": 0,
                        "staging_to_overwrite": [],
                        "staging_to_commit": [],
                    },
                ):
                    with patch(
                        "ai_cli.sync._push_to_remote",
                        side_effect=lambda *a, **kw: push_called.append(True),
                    ):
                        result = sync_pull(["--dry-run"])

    assert result == 2
    assert not push_called, "push was called in dry-run mode"
