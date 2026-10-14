"""Tests for scripts/check_live_file_deletions.py (AI-CLI-9sy).

Both directions are exercised against real git repositories, never mocks: a
staged deletion of a file that is still live upstream must be REJECTED, and an
acknowledged one must PASS. A guard nobody has watched refuse is not known to be
a guard at all, so the rejection path is proven first and the pass path second.

Two tests go further and run the script as a real `pre-commit` git hook over a
real `git commit`, asserting that HEAD does not move. "The guard refused" and
"nothing was committed" are different claims, and only the second one is the
property this guard exists to provide.

The comparison ref is created with `git update-ref refs/remotes/origin/main`,
which is exactly what `git fetch` writes -- a remote-tracking ref is an ordinary
ref -- so the fixtures need no network and no second clone. One test does clone
from a bare repo, because `origin/HEAD` is written only by `git clone` and its
fallback cannot be exercised any other way.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_live_file_deletions import (
    ACK_ENV,
    HOOK_ID,
    REF_ENV,
    check,
    repo_root,
    resolve_ref,
    staged_deletions,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_live_file_deletions.py"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)


def _hook_env() -> dict[str, str]:
    """The ambient environment with this guard's own env vars removed.

    `subprocess.run(env=...)` replaces the environment wholesale, so the vars the
    `_no_inherited_acknowledgement` fixture unsets for in-process calls have to be
    removed here too for the subprocess ones.
    """
    return {k: v for k, v in os.environ.items() if k not in {ACK_ENV, REF_ENV}}


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    result = _git(repo, "commit", "-q", "--no-verify", "-m", message)
    assert result.returncode == 0, result.stderr


@pytest.fixture(autouse=True)
def _no_inherited_acknowledgement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's own bypass out of the suite.

    Both env vars are read from the ambient environment, so a shell that exported
    one would turn every rejection test green for the wrong reason.
    """
    monkeypatch.delenv(ACK_ENV, raising=False)
    monkeypatch.delenv(REF_ENV, raising=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo whose `origin/main` carries `docs/plan.md` and `keep.txt`."""
    repo = tmp_path / "myproject"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "user@example.com")
    _git(repo, "config", "user.name", "user")
    (repo / "docs").mkdir()
    (repo / "docs" / "plan.md").write_text("plan content\n")
    (repo / "keep.txt").write_text("untouched\n")
    _commit_all(repo, "init")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", head)
    return repo


def _stage_deletion(repo: Path, *paths: str) -> None:
    for path in paths:
        (repo / path).unlink()
    _git(repo, "add", "-A")


# --- the rejection direction: the case the guard exists to catch ---


def test_given_staged_deletion_of_file_live_upstream_when_checked_then_rejects(repo: Path):
    _stage_deletion(repo, "docs/plan.md")

    errors = check(repo)

    assert errors, "a deletion of a file still live on origin/main must be rejected"
    joined = "\n".join(errors)
    assert "REJECTED" in joined
    assert "docs/plan.md" in joined
    assert "origin/main" in joined


def test_given_two_staged_deletions_when_checked_then_names_every_offending_path(repo: Path):
    _stage_deletion(repo, "docs/plan.md", "keep.txt")

    joined = "\n".join(check(repo))

    assert "docs/plan.md" in joined
    assert "keep.txt" in joined


def test_given_rejection_when_message_built_then_offers_restore_and_acknowledge_commands(repo: Path):
    _stage_deletion(repo, "docs/plan.md")

    joined = "\n".join(check(repo))

    assert "git restore --source=HEAD --staged --worktree -- docs/plan.md" in joined
    assert f"{ACK_ENV}=docs/plan.md git commit" in joined
    assert f"SKIP={HOOK_ID} git commit" in joined


def test_given_deleted_path_containing_a_space_when_rejected_then_message_quotes_it(repo: Path):
    (repo / "a file.txt").write_text("content\n")
    _commit_all(repo, "add a path with a space")
    _git(repo, "update-ref", "refs/remotes/origin/main", _git(repo, "rev-parse", "HEAD").stdout.strip())
    _stage_deletion(repo, "a file.txt")

    joined = "\n".join(check(repo))

    assert 'git restore --source=HEAD --staged --worktree -- "a file.txt"' in joined


# --- the pass direction: everything that must NOT be blocked ---


def test_given_clean_index_when_checked_then_passes(repo: Path):
    assert check(repo) == []


def test_given_modified_not_deleted_file_when_checked_then_passes(repo: Path):
    (repo / "docs" / "plan.md").write_text("edited\n")
    _git(repo, "add", "-A")

    assert check(repo) == []


def test_given_renamed_file_when_checked_then_passes(repo: Path):
    (repo / "docs" / "plan.md").rename(repo / "docs" / "design.md")
    _git(repo, "add", "-A")

    assert check(repo) == [], "a rename stages a delete+add and must not read as a deletion"


def test_given_deletion_of_file_absent_upstream_when_checked_then_passes(repo: Path):
    (repo / "scratch.txt").write_text("local only\n")
    _commit_all(repo, "add a file that never reached origin/main")
    _stage_deletion(repo, "scratch.txt")

    assert check(repo) == [], "deleting a file that was never published harms nobody"


def test_given_deletion_committed_in_an_earlier_commit_when_next_commit_checked_then_passes(repo: Path):
    _stage_deletion(repo, "docs/plan.md")
    _commit_all(repo, "deliberately remove the plan")
    (repo / "keep.txt").write_text("a later, unrelated edit\n")
    _git(repo, "add", "-A")

    assert check(repo) == [], "only what THIS commit deletes may block it"


def test_given_staged_deletion_of_a_tracked_symlink_when_checked_then_rejects(repo: Path):
    """A tracked symlink counts. One of the established loss shapes is exactly this.

    A checkout that failed to materialise 21 git-tracked symlinks, with every
    regular file arriving correctly, is a confirmed cause of this class of loss --
    so a guard blind to mode 120000 would miss it entirely. This is a real risk
    rather than a theoretical one: `git_repair.detect_phantom_deleted_files`
    deliberately EXCLUDES symlinks, to avoid double-reporting against its sibling
    detector, and a later refactor that made this guard mirror that exclusion
    would silently reopen the shape. Committing a deleted symlink loses just as
    much as committing a deleted file, so the exclusion must not be copied here.

    The symlink is created through git plumbing (`update-index --cacheinfo`)
    rather than `os.symlink`: that writes the mode-120000 index entry with no
    filesystem symlink at all, which IS the shape's signature -- and it needs
    none of the privilege Windows requires to create a real symlink.
    """
    blob = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
        input="plan.md",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    _git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},docs/link.md")
    assert _git(repo, "commit", "-q", "--no-verify", "-m", "add a tracked symlink").returncode == 0
    _git(repo, "update-ref", "refs/remotes/origin/main", _git(repo, "rev-parse", "HEAD").stdout.strip())
    assert "120000" in _git(repo, "ls-files", "-s", "--", "docs/link.md").stdout, "fixture must stage a symlink"

    # What `git add -A` produces once the symlink has vanished from the checkout.
    _git(repo, "update-index", "--force-remove", "docs/link.md")

    assert "docs/link.md" in "\n".join(check(repo))


# --- the acknowledgement path (it has to stay usable or it gets bypassed) ---


def test_given_deletion_acknowledged_by_path_when_checked_then_passes(repo: Path, monkeypatch: pytest.MonkeyPatch):
    _stage_deletion(repo, "docs/plan.md")
    monkeypatch.setenv(ACK_ENV, "docs/plan.md")

    assert check(repo) == []


def test_given_deletion_acknowledged_by_value_1_when_checked_then_passes(repo: Path, monkeypatch: pytest.MonkeyPatch):
    _stage_deletion(repo, "docs/plan.md")
    monkeypatch.setenv(ACK_ENV, "1")

    assert check(repo) == []


def test_given_only_one_of_two_deletions_acknowledged_when_checked_then_still_rejects_the_other(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    _stage_deletion(repo, "docs/plan.md", "keep.txt")
    monkeypatch.setenv(ACK_ENV, "docs/plan.md")

    joined = "\n".join(check(repo))

    assert "keep.txt" in joined
    assert "docs/plan.md" not in joined, "per-path acknowledgement must not wave through the others"


def test_given_acknowledgement_listing_several_paths_when_checked_then_passes(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    _stage_deletion(repo, "docs/plan.md", "keep.txt")
    monkeypatch.setenv(ACK_ENV, "docs/plan.md, keep.txt")

    assert check(repo) == []


def test_given_acknowledgement_using_backslash_separators_when_checked_then_passes(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    _stage_deletion(repo, "docs/plan.md")
    monkeypatch.setenv(ACK_ENV, r"docs\plan.md")

    assert check(repo) == [], "a Windows shell's own path separator must acknowledge the same file"


# --- the unresolvable-ref case: fails closed on purpose ---


def test_given_no_remote_tracking_ref_and_a_staged_deletion_when_checked_then_fails_closed(repo: Path):
    _git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    _stage_deletion(repo, "docs/plan.md")

    joined = "\n".join(check(repo))

    assert "REJECTED" in joined
    assert "fails CLOSED" in joined
    assert "git fetch origin" in joined


def test_given_no_remote_tracking_ref_and_no_staged_deletion_when_checked_then_passes(repo: Path):
    _git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    (repo / "docs" / "plan.md").write_text("edited\n")
    _git(repo, "add", "-A")

    assert check(repo) == [], "a repo with no fetched upstream must still be committable"


def test_given_explicit_ref_override_that_does_not_resolve_when_checked_then_fails_closed(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(REF_ENV, "refs/remotes/origin/typo")
    _stage_deletion(repo, "docs/plan.md")

    joined = "\n".join(check(repo))

    assert "fails CLOSED" in joined
    assert "refs/remotes/origin/typo" in joined, "the ref that failed must be named"


def test_given_explicit_ref_override_when_checked_then_compares_against_it(repo: Path, monkeypatch: pytest.MonkeyPatch):
    _git(repo, "branch", "release")
    _stage_deletion(repo, "docs/plan.md")
    monkeypatch.setenv(REF_ENV, "release")

    joined = "\n".join(check(repo))

    assert "docs/plan.md" in joined
    assert "release" in joined


def test_given_origin_main_missing_but_origin_head_present_when_ref_resolved_then_uses_origin_head(tmp_path: Path):
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "trunk", str(origin))
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "trunk")
    _git(seed, "config", "user.email", "user@example.com")
    _git(seed, "config", "user.name", "user")
    (seed / "published.txt").write_text("live upstream\n")
    _commit_all(seed, "init")
    _git(seed, "remote", "add", "origin", str(origin))
    assert _git(seed, "push", "-q", "origin", "trunk").returncode == 0

    clone = tmp_path / "clone"
    assert _git(tmp_path, "clone", "-q", str(origin), str(clone)).returncode == 0
    _git(clone, "config", "user.email", "user@example.com")
    _git(clone, "config", "user.name", "user")

    ref, _tried = resolve_ref(clone)
    assert ref == "refs/remotes/origin/HEAD", "a repo whose default branch is neither main nor master is covered"

    _stage_deletion(clone, "published.txt")
    assert "published.txt" in "\n".join(check(clone))


# --- repo selection must come from the path we pass, not from the environment ---


def test_given_leaked_git_dir_env_when_checked_then_measures_the_repo_it_was_given(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """git exports GIT_DIR into every hook process and pre-commit forwards it.

    Left unstripped it would redirect these probes at whatever repo launched the
    commit -- producing a confident verdict about the wrong tree.
    """
    other = tmp_path / "otherproject"
    other.mkdir()
    _git(other, "init", "-q", "-b", "main")
    _git(other, "config", "user.email", "user@example.com")
    _git(other, "config", "user.name", "user")
    (other / "unrelated.txt").write_text("unrelated\n")
    _commit_all(other, "init")
    # Stage before injecting the leak: this file's own `_git` helper does not strip
    # the targeting vars, so a `git add -A` run afterwards would land in `other`.
    _stage_deletion(repo, "docs/plan.md")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))

    assert "docs/plan.md" in "\n".join(check(repo))


def test_given_a_path_outside_any_repo_when_root_resolved_then_returns_none(tmp_path: Path):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()

    assert repo_root(outside) is None


def test_given_repo_with_no_commits_when_deletions_read_then_returns_empty(tmp_path: Path):
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    _git(fresh, "init", "-q", "-b", "main")

    assert staged_deletions(fresh) == []


# --- the whole thing, as a real hook over a real commit ---


def _install_as_pre_commit_hook(repo: Path) -> None:
    """Install the guard as the repo's own `pre-commit` hook.

    A `sh` wrapper naming this interpreter by absolute posix path, rather than a
    `#!/usr/bin/env python3` shebang: `python3` is frequently absent on Windows,
    where git runs hooks through the bundled `sh`.
    """
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    _git(repo, "config", "core.hooksPath", hooks.as_posix())
    hook = hooks / "pre-commit"
    hook.write_text(f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" "{SCRIPT.as_posix()}"\n')
    hook.chmod(0o755)


def test_given_the_guard_installed_as_a_hook_when_a_live_deletion_is_committed_then_head_does_not_move(repo: Path):
    _install_as_pre_commit_hook(repo)
    _stage_deletion(repo, "docs/plan.md")
    before = _git(repo, "rev-parse", "HEAD").stdout.strip()

    result = _git(repo, "commit", "-m", "remove the plan")

    assert result.returncode != 0, result.stdout + result.stderr
    assert "docs/plan.md" in result.stdout + result.stderr
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == before, "the commit must not have been created"


def test_given_the_guard_installed_as_a_hook_when_the_deletion_is_acknowledged_then_the_commit_lands(repo: Path):
    _install_as_pre_commit_hook(repo)
    _stage_deletion(repo, "docs/plan.md")
    before = _git(repo, "rev-parse", "HEAD").stdout.strip()

    result = subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "remove the plan on purpose"],
        capture_output=True,
        text=True,
        check=False,
        env={**_hook_env(), ACK_ENV: "docs/plan.md"},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() != before


def test_given_the_script_run_as_a_subprocess_when_a_live_deletion_is_staged_then_exits_one(repo: Path):
    _stage_deletion(repo, "docs/plan.md")

    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=_hook_env(),
    )

    assert result.returncode == 1
    assert "docs/plan.md" in result.stderr


def test_given_a_clean_index_when_the_script_runs_as_a_subprocess_then_exits_zero(repo: Path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=_hook_env(),
    )

    assert result.returncode == 0, result.stderr


# --- the guard must stay wired in ---


def test_given_the_real_precommit_config_when_read_then_the_guard_is_registered_at_commit_stage():
    """A guard that is not registered enforces nothing, however good its body is.

    This repo has had one such file: `.githooks/commit-msg` sat in the tree for
    weeks with no `.pre-commit-config.yaml` entry naming it, so its CHANGELOG
    rule checked nothing. Asserting the registration here means this guard cannot
    go the same way silently.
    """
    config = (REPO_ROOT / ".pre-commit-config.yaml").read_text()

    assert f"id: {HOOK_ID}" in config
    assert "scripts/check_live_file_deletions.py" in config
    entry = config[config.index(f"id: {HOOK_ID}") :]
    next_hook = entry.find("- id: ", 1)
    entry = entry if next_hook == -1 else entry[:next_hook]
    assert "stages:" not in entry, "the guard must run at the default pre-commit stage, not only on push"
