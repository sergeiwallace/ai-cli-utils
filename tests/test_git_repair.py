"""Tests for the core.bare=true / stale core.worktree corruption fix (AI-CLI-99),
plus the AIH-443 phantom-deletion detection guards."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from ai_cli.git_repair import (
    _GIT_TARGETING_VARS,
    GitProbeError,
    _git_env,
    detect_missing_tracked_symlinks,
    detect_phantom_deleted_files,
    operation_in_progress,
    pull_rebase_autostash,
    repair_bare_worktree_config,
    stash_entries,
    unmerged_paths,
)

# --- _git_env ---


def test_git_env_when_targeting_vars_present_then_strips_them():
    base = {
        "GIT_DIR": "/tmp/somewhere/.git",
        "GIT_WORK_TREE": "/tmp/somewhere",
        "GIT_INDEX_FILE": "/tmp/somewhere/.git/index",
        "GIT_OBJECT_DIRECTORY": "/tmp/somewhere/.git/objects",
        "GIT_COMMON_DIR": "/tmp/somewhere/.git",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/tmp/other/objects",
        "GIT_PREFIX": "sub/dir/",
        "GIT_CONFIG": "/tmp/somewhere/.git/config",
        "GIT_CONFIG_GLOBAL": "/tmp/home/.gitconfig",
        "PATH": "/usr/bin",
    }

    result = _git_env(base)

    for var in _GIT_TARGETING_VARS:
        assert var not in result
    assert result["PATH"] == "/usr/bin"


def test_git_env_when_innocuous_git_vars_present_then_keeps_them():
    base = {
        "GIT_DIR": "/tmp/somewhere/.git",
        "GIT_SSH": "/usr/bin/ssh",
        "GIT_SSH_COMMAND": "ssh -o foo=bar",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    result = _git_env(base)

    assert "GIT_DIR" not in result
    assert result["GIT_SSH"] == "/usr/bin/ssh"
    assert result["GIT_SSH_COMMAND"] == "ssh -o foo=bar"
    assert result["GIT_TERMINAL_PROMPT"] == "0"
    assert result["GIT_AUTHOR_NAME"] == "Test"
    assert result["GIT_AUTHOR_EMAIL"] == "t@t"
    assert result["GIT_COMMITTER_NAME"] == "Test"
    assert result["GIT_COMMITTER_EMAIL"] == "t@t"


def test_git_env_when_no_base_given_then_scrubs_os_environ():
    with patch.dict("os.environ", {"GIT_DIR": "/leaked", "SOME_VAR": "1"}, clear=False):
        result = _git_env()
    assert "GIT_DIR" not in result
    assert result.get("SOME_VAR") == "1"


# --- repair_bare_worktree_config ---


def test_repair_when_bare_true_then_resets_false_and_reports_repaired(tmp_path):
    def mock_run(cmd, **kwargs):
        if "core.bare" in cmd and "--get" in cmd:
            return MagicMock(returncode=0, stdout="true\n")
        if "core.worktree" in cmd and "--get" in cmd:
            return MagicMock(returncode=1, stdout="")
        return MagicMock(returncode=0, stdout="")

    with patch("subprocess.run", side_effect=mock_run) as mock_subprocess:
        result = repair_bare_worktree_config(tmp_path)

    assert result is True
    set_calls = [c.args[0] for c in mock_subprocess.call_args_list if "config" in c.args[0] and "false" in c.args[0]]
    assert any("core.bare" in c for c in set_calls)


def test_repair_when_stale_worktree_config_then_unsets_and_reports_repaired(tmp_path):
    def mock_run(cmd, **kwargs):
        if "core.bare" in cmd and "--get" in cmd:
            return MagicMock(returncode=0, stdout="false\n")
        if "core.worktree" in cmd and "--get" in cmd:
            return MagicMock(returncode=0, stdout="/some/sibling/.worktrees/aih-37\n")
        return MagicMock(returncode=0, stdout="")

    with patch("subprocess.run", side_effect=mock_run) as mock_subprocess:
        result = repair_bare_worktree_config(tmp_path)

    assert result is True
    unset_calls = [c.args[0] for c in mock_subprocess.call_args_list if "--unset" in c.args[0]]
    assert any("core.worktree" in c for c in unset_calls)


def test_repair_when_both_bare_true_and_stale_worktree_then_repairs_both(tmp_path):
    def mock_run(cmd, **kwargs):
        if "core.bare" in cmd and "--get" in cmd:
            return MagicMock(returncode=0, stdout="true\n")
        if "core.worktree" in cmd and "--get" in cmd:
            return MagicMock(returncode=0, stdout="/some/sibling/.worktrees/aih-37\n")
        return MagicMock(returncode=0, stdout="")

    with patch("subprocess.run", side_effect=mock_run) as mock_subprocess:
        result = repair_bare_worktree_config(tmp_path)

    assert result is True
    calls = [c.args[0] for c in mock_subprocess.call_args_list]
    assert any("core.bare" in c and "false" in c for c in calls)
    assert any("--unset" in c and "core.worktree" in c for c in calls)


def test_repair_when_healthy_repo_then_noop_and_returns_false(tmp_path):
    def mock_run(cmd, **kwargs):
        if "core.bare" in cmd and "--get" in cmd:
            return MagicMock(returncode=0, stdout="false\n")
        if "core.worktree" in cmd and "--get" in cmd:
            return MagicMock(returncode=1, stdout="")
        return MagicMock(returncode=0, stdout="")

    with patch("subprocess.run", side_effect=mock_run) as mock_subprocess:
        result = repair_bare_worktree_config(tmp_path)

    assert result is False
    set_calls = [c.args[0] for c in mock_subprocess.call_args_list if "false" in c.args[0] or "--unset" in c.args[0]]
    assert set_calls == []


def test_repair_when_real_repo_corrupted_then_fixes_it_end_to_end(tmp_path):
    """Integration-style repro: a real git repo flipped to core.bare=true (± a
    stale core.worktree) is restored to a normal working tree by the guard."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        ],
        check=True,
    )

    # Simulate the corruption directly (the documented failure mode): flip
    # core.bare=true and point core.worktree at a sibling worktree directory
    # (the sibling dir must exist — git rejects config ops that reference a
    # nonexistent core.worktree path with "fatal: Invalid path").
    sibling_worktree = tmp_path / ".worktrees" / "aih-37"
    sibling_worktree.mkdir(parents=True)
    subprocess.run(["git", "-C", str(repo), "config", "--local", "core.bare", "true"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "core.worktree", str(sibling_worktree)],
        check=True,
    )

    result = repair_bare_worktree_config(repo)

    assert result is True
    bare = subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "--get", "core.bare"], capture_output=True, text=True
    )
    assert bare.stdout.strip() == "false"
    worktree_cfg = subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "--get", "core.worktree"], capture_output=True
    )
    assert worktree_cfg.returncode != 0  # unset


# --- pull_rebase_autostash / unmerged_paths / stash_entries (AIH-443 Shape B) ---
#
# These drive real `git` subprocesses on real repos on purpose. The defect IS
# git's exit-code behaviour, so a mocked subprocess would assert only what the
# mock was told to say and would pass just as happily against the broken code.


def _git(repo, *args, **kwargs):
    return subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True, text=True, check=False, **kwargs)


def _make_remote(path):
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "f.txt").write_text("line1\n")
    _git(path, "add", "f.txt")
    _git(path, "commit", "-q", "-m", "init")
    return path


def _clone(remote, local):
    subprocess.run(["git", "clone", "-q", str(remote), str(local)], check=True)
    _git(local, "config", "user.email", "t@t")
    _git(local, "config", "user.name", "t")
    return local


def _remote_edits_same_line(remote):
    (remote / "f.txt").write_text("remote-change\n")
    _git(remote, "commit", "-q", "-am", "remote edits same line")


def test_pull_rebase_autostash_when_pop_conflicts_then_reports_strand_despite_exit_zero(tmp_path):
    """The defect itself: a same-line local-vs-remote edit makes the autostash
    pop conflict, yet `git pull --rebase --autostash` still exits 0. Measured on
    git 2.43.0 and 2.55.0. A caller gating on `returncode != 0` sees success and
    walks into a conflicted index — which stranded a remote build host 50 commits
    behind for five days."""
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")
    (local / "f.txt").write_text("local-change\n")  # uncommitted, never staged
    _remote_edits_same_line(remote)

    pull, stranded = pull_rebase_autostash(local)

    assert pull.returncode == 0, "if this ever becomes non-zero, git fixed the defect and the guard can be simplified"
    assert stranded is not None, "the guard must see the strand the exit code hides"
    assert "f.txt" in stranded
    # And the repo really is stranded, exactly as the live specimen was:
    assert unmerged_paths(local) == {"f.txt"}
    assert len(stash_entries(local)) == 1
    assert operation_in_progress(local) is None  # nothing "in progress" to signal it


def test_pull_rebase_autostash_when_clean_pull_then_no_strand(tmp_path):
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")
    (remote / "g.txt").write_text("unrelated\n")
    _git(remote, "add", "g.txt")
    _git(remote, "commit", "-q", "-m", "unrelated remote change")

    pull, stranded = pull_rebase_autostash(local)

    assert pull.returncode == 0
    assert stranded is None
    assert unmerged_paths(local) == set()


def test_pull_rebase_autostash_when_repo_holds_unrelated_wip_stashes_then_clean_pull_is_not_flagged(tmp_path):
    """Regression for the false positive that made the previous guard useless:
    it reported ANY stash entry as a strand. The stranded host's main tree held
    three unrelated WIP stashes, so that guard would have cried wolf on every
    clean launch — and a warning that always fires is a warning nobody reads."""
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")
    (local / "f.txt").write_text("my real work in progress\n")
    _git(local, "stash", "push", "-q", "-m", "WIP on main: real work")
    before = stash_entries(local)
    assert len(before) == 1

    (remote / "g.txt").write_text("unrelated\n")
    _git(remote, "add", "g.txt")
    _git(remote, "commit", "-q", "-m", "unrelated remote change")

    pull, stranded = pull_rebase_autostash(local)

    assert pull.returncode == 0
    assert stranded is None, "a pre-existing WIP stash is not this pull's doing"
    assert stash_entries(local) == before, "and it must be left untouched"


def test_pull_rebase_autostash_when_index_already_conflicted_then_strand_not_attributed_to_this_pull(tmp_path):
    """An already-conflicted repo is not re-blamed on the next pull. Git refuses
    to pull at all here (non-zero), and the caller decides what that means —
    `ai update` treats any conflicted index as fatal, `ai c N` only refuses when
    the launch itself caused it."""
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")
    (local / "f.txt").write_text("local-change\n")
    _remote_edits_same_line(remote)
    _, first = pull_rebase_autostash(local)
    assert first is not None  # strand it

    (remote / "g.txt").write_text("more upstream work\n")
    _git(remote, "add", "g.txt")
    _git(remote, "commit", "-q", "-m", "more upstream")

    pull, stranded = pull_rebase_autostash(local)

    assert pull.returncode != 0, "git refuses to pull onto an unmerged index"
    assert stranded is None, "the conflict predates this pull"
    assert unmerged_paths(local) == {"f.txt"}, "caller can still see the repo is unusable"


def test_pull_rebase_autostash_when_remote_unreachable_then_fails_without_stranding(tmp_path):
    """A pull that merely fails leaves the tree intact — that is not a strand,
    and callers must stay usable offline."""
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")
    _git(local, "remote", "set-url", "origin", str(tmp_path / "does-not-exist"))

    pull, stranded = pull_rebase_autostash(local)

    assert pull.returncode != 0
    assert stranded is None
    assert unmerged_paths(local) == set()


def test_unmerged_paths_when_index_has_conflict_stages_then_returns_those_paths(tmp_path):
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")
    (local / "f.txt").write_text("local-change\n")
    _remote_edits_same_line(remote)
    assert unmerged_paths(local) == set()

    pull_rebase_autostash(local)

    assert unmerged_paths(local) == {"f.txt"}


def test_stash_entries_when_entries_pushed_then_returns_their_ids(tmp_path):
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")
    assert stash_entries(local) == set()

    for i in range(3):
        (local / "f.txt").write_text(f"wip {i}\n")
        _git(local, "stash", "push", "-q", "-m", f"wip {i}")

    entries = stash_entries(local)
    assert len(entries) == 3
    assert all(len(sha) == 40 for sha in entries)


def test_stash_entries_when_one_dropped_and_one_added_then_identity_not_count_decides(tmp_path):
    """Identities rather than a count: a concurrent drop can hold the count flat
    while a genuinely new entry appeared. A count-based guard would see 0 growth
    and report nothing."""
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")
    (local / "f.txt").write_text("old wip\n")
    _git(local, "stash", "push", "-q", "-m", "old")
    before = stash_entries(local)

    _git(local, "stash", "drop", "-q", "stash@{0}")
    (local / "f.txt").write_text("new wip\n")
    _git(local, "stash", "push", "-q", "-m", "new")
    after = stash_entries(local)

    assert len(after) == len(before), "count is unchanged — this is what fools a counter"
    assert after != before
    assert after - before, "identity comparison still sees the new entry"


def test_pull_rebase_autostash_when_rebase_body_conflicts_then_not_reported_as_strand(tmp_path):
    """A plain rebase conflict is NOT this defect: git exits non-zero and leaves
    `rebase-merge` behind, so it already told the user. Reporting it here would
    refuse the session that could resolve it and give stash advice for a pull
    that never stashed anything."""
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")
    (local / "f.txt").write_text("local-committed\n")
    _git(local, "commit", "-q", "-am", "local commit same line")  # committed, so no autostash
    _remote_edits_same_line(remote)

    pull, stranded = pull_rebase_autostash(local)

    assert pull.returncode != 0, "git reports this one honestly"
    assert unmerged_paths(local), "and it does leave conflict stages"
    assert operation_in_progress(local) == "rebase-merge"
    assert stranded is None, "an in-progress rebase is visible by ordinary means"


def test_operation_in_progress_when_repo_clean_then_none(tmp_path):
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")

    assert operation_in_progress(local) is None


def test_unmerged_paths_when_path_has_spaces_and_non_ascii_then_decoded_intact(tmp_path):
    """The probe backs a fatal gate, so a parsing miss is a silent pass."""
    awkward = "dir with spaces/файл 'quoted'.txt"
    remote = tmp_path / "remote"
    remote.mkdir()
    _git(remote, "init", "-q", "-b", "main")
    _git(remote, "config", "user.email", "t@t")
    _git(remote, "config", "user.name", "t")
    (remote / "dir with spaces").mkdir()
    (remote / awkward).write_text("line1\n")
    _git(remote, "add", "-A")
    _git(remote, "commit", "-q", "-m", "init")

    local = _clone(remote, tmp_path / "local")
    (local / awkward).write_text("local-change\n")
    (remote / awkward).write_text("remote-change\n")
    _git(remote, "commit", "-q", "-am", "remote edits same line")

    _, stranded = pull_rebase_autostash(local)

    assert unmerged_paths(local) == {awkward}
    assert stranded is not None and awkward in stranded


def test_unmerged_paths_when_probe_fails_then_raises_rather_than_reporting_clean(tmp_path):
    """Fail closed. If this returned an empty set on error, "could not look" and
    "looked, it was clean" would be the same value — which is how a gate goes
    inert."""
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    with pytest.raises(GitProbeError):
        unmerged_paths(not_a_repo)

    with pytest.raises(GitProbeError):
        stash_entries(not_a_repo)


def test_pull_rebase_autostash_when_state_unverifiable_then_reports_strand(tmp_path):
    """An unverifiable repo is treated as unsafe rather than assumed clean."""
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    _, stranded = pull_rebase_autostash(not_a_repo)

    assert stranded is not None
    assert "could not be verified" in stranded


# --- detect_missing_tracked_symlinks (AIH-443 Shape A) ---


def test_detect_missing_tracked_symlinks_when_symlink_missing_from_disk_then_reports_it(tmp_path):
    """Reproduces AIH-443 Shape A's exact signature: a tracked symlink HEAD still
    lists is absent from disk (verified `os.path.lexists` failure), with no git
    error anywhere — the same shape a Claude Code `isolation: worktree` checkout
    produced for 21 real symlinks in one sub-agent worktree."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "docs").mkdir()
    (repo / "docs" / "real.md").write_text("real content\n")
    link = repo / "docs" / "link.md"
    link.symlink_to("real.md")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init with a tracked symlink")

    assert detect_missing_tracked_symlinks(repo) == []  # sanity: present and tracked

    # Simulate the checkout dropping the symlink from disk (Shape A) — the index/
    # HEAD are untouched, exactly matching `git status` reporting a deletion
    # nobody made while `git show HEAD:<path>` still succeeds.
    link.unlink()

    missing = detect_missing_tracked_symlinks(repo)

    assert missing == ["docs/link.md"]
    show = _git(repo, "show", "HEAD:docs/link.md")
    assert show.returncode == 0  # still present in HEAD — a real "phantom" deletion


def test_detect_missing_tracked_symlinks_when_regular_file_missing_then_ignored(tmp_path):
    """A missing REGULAR file (Shape B's signature, not Shape A's) must not be
    reported here — the two shapes are confirmed distinct root causes and this
    detector is scoped to the symlink-specific mechanism only."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("content\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    (repo / "f.txt").unlink()

    assert detect_missing_tracked_symlinks(repo) == []


# --- detect_phantom_deleted_files (AIH-443 Shape C) ---


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "docs").mkdir()
    (repo / "docs" / "plan.md").write_text("plan content\n" * 40)
    (repo / "keep.txt").write_text("untouched\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _precommit_staged_files_only_cycle(repo):
    """Replay pre-commit's `staged_files_only` exactly, without needing pre-commit.

    Mirrors `pre_commit/staged_files_only.py::_unstaged_changes_cleared`: capture
    the unstaged diff as a binary patch, `git checkout -- .` to clear the working
    tree, then re-apply the patch in the `finally:` block. Returns the patch bytes
    so a test can assert what pre-commit would have persisted to its cache.
    """
    tree = _git(repo, "write-tree").stdout.strip()
    diff = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "diff-index",
            "--ignore-submodules",
            "--binary",
            "--exit-code",
            "--no-color",
            "--no-ext-diff",
            tree,
            "--",
        ],
        capture_output=True,
        check=False,
    )
    if diff.returncode == 0:
        return b""
    patch_file = repo.parent / "patch-capture"
    patch_file.write_bytes(diff.stdout)
    _git(repo, "-c", "submodule.recurse=0", "checkout", "--", ".")
    _git(repo, "apply", "--whitespace=nowarn", str(patch_file))
    return diff.stdout


def test_detect_phantom_deleted_files_when_tracked_regular_file_missing_then_reports_it(tmp_path):
    """Shape C's exact signature: the index still holds the blob, HEAD still has it,
    but the file is gone from disk — so `git status` shows a deletion nobody made and
    committing would remove content still live on the remote."""
    repo = _init_repo(tmp_path)

    assert detect_phantom_deleted_files(repo) == []  # sanity: healthy tree is silent

    (repo / "docs" / "plan.md").unlink()

    assert detect_phantom_deleted_files(repo) == ["docs/plan.md"]
    assert _git(repo, "show", "HEAD:docs/plan.md").returncode == 0  # content still safe


def test_detect_phantom_deleted_files_when_worktree_clean_then_empty(tmp_path):
    """Negative control: the detector must be silent on a healthy tree, otherwise
    firing on the sick one proves nothing."""
    repo = _init_repo(tmp_path)

    assert detect_phantom_deleted_files(repo) == []


def test_detect_phantom_deleted_files_when_file_modified_not_deleted_then_ignored(tmp_path):
    """An ordinary unstaged edit is not a phantom deletion and must not warn."""
    repo = _init_repo(tmp_path)

    (repo / "docs" / "plan.md").write_text("edited\n")

    assert detect_phantom_deleted_files(repo) == []


def test_detect_phantom_deleted_files_when_missing_entry_is_symlink_then_ignored(tmp_path):
    """Shape A stays with its own detector: a missing tracked symlink has a different
    cause and a different fix, so reporting it here would double-warn."""
    repo = _init_repo(tmp_path)
    link = repo / "docs" / "link.md"
    link.symlink_to("plan.md")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add tracked symlink")

    link.unlink()

    assert detect_phantom_deleted_files(repo) == []
    assert detect_missing_tracked_symlinks(repo) == ["docs/link.md"]


def test_shape_b_guard_when_phantom_deletion_present_then_blind_to_it(tmp_path):
    """Why Shape C needed a new detector at all: pre-commit stashes to its OWN patch
    file under ~/.cache/pre-commit, never `git stash`, so the Shape B guard sees an
    empty stash list and an unconflicted index, and stays silent on a genuinely
    broken worktree."""
    repo = _init_repo(tmp_path)
    (repo / "docs" / "plan.md").unlink()

    # The blindness, reproduced against the Shape B guard's actual inputs.
    assert stash_entries(repo) == set()
    assert unmerged_paths(repo) == set()
    assert detect_phantom_deleted_files(repo) == ["docs/plan.md"]


def test_detect_phantom_deleted_files_when_precommit_cycle_runs_then_deletion_survives(tmp_path):
    """The perpetuation mechanism, reproduced end to end.

    pre-commit's `staged_files_only` captures the lone deletion as a patch, runs
    `git checkout -- .` (which RESTORES the file — the very fix), then re-applies the
    patch, deleting it again. The tree ends byte-identical to how it started and
    nothing errors, which is why this shape survived every pull and every hook run on
    `aido/.worktrees/aido-1` for over 24 hours.
    """
    repo = _init_repo(tmp_path)
    (repo / "docs" / "plan.md").unlink()

    patch_bytes = _precommit_staged_files_only_cycle(repo)

    assert b"deleted file mode 100644" in patch_bytes  # what lands in the patch cache
    assert not (repo / "docs" / "plan.md").exists()  # restored, then re-deleted
    assert detect_phantom_deleted_files(repo) == ["docs/plan.md"]
    assert _git(repo, "stash", "list").stdout.strip() == ""  # no stash, ever


def test_detect_phantom_deleted_files_when_restored_then_precommit_cycle_is_a_noop(tmp_path):
    """`git checkout -- <path>` is a durable fix: with a clean tree pre-commit's
    `git diff-index` returns 0, so no patch is captured and nothing gets replayed."""
    repo = _init_repo(tmp_path)
    (repo / "docs" / "plan.md").unlink()

    _git(repo, "checkout", "--", "docs/plan.md")
    patch_bytes = _precommit_staged_files_only_cycle(repo)

    assert patch_bytes == b""
    assert (repo / "docs" / "plan.md").exists()
    assert detect_phantom_deleted_files(repo) == []
