"""Tests for ai_cli.trust — Claude Code workspace trust registration (GH #72896)."""

import json
import subprocess
from pathlib import Path

import pytest

from ai_cli import trust


@pytest.fixture
def claude_json(tmp_path, monkeypatch):
    """Point trust._claude_json_path() at a temp file and return its Path."""
    cfg = tmp_path / ".claude.json"
    monkeypatch.setattr(trust, "_claude_json_path", lambda: cfg)
    return cfg


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    return path


def test_ensure_trusted_creates_key_for_git_root(claude_json, tmp_path):
    repo = _init_repo(tmp_path / "repo")
    claude_json.write_text(json.dumps({"projects": {}}, indent=2))

    added = trust.ensure_workspace_trusted([repo])

    data = json.loads(claude_json.read_text())
    key = str(repo.resolve())
    assert added == [key]
    assert data["projects"][key]["hasTrustDialogAccepted"] is True


def test_ensure_trusted_is_idempotent(claude_json, tmp_path):
    repo = _init_repo(tmp_path / "repo")
    key = str(repo.resolve())
    claude_json.write_text(json.dumps({"projects": {key: {"hasTrustDialogAccepted": True}}}, indent=2))

    added = trust.ensure_workspace_trusted([repo])

    assert added == []  # already trusted → no change


def test_ensure_trusted_preserves_other_keys_and_fields(claude_json, tmp_path):
    repo = _init_repo(tmp_path / "repo")
    claude_json.write_text(
        json.dumps(
            {
                "numStartups": 42,
                "projects": {
                    "/other/proj": {"hasTrustDialogAccepted": True, "history": ["x"]},
                },
            },
            indent=2,
        )
    )

    trust.ensure_workspace_trusted([repo])

    data = json.loads(claude_json.read_text())
    assert data["numStartups"] == 42
    assert data["projects"]["/other/proj"] == {"hasTrustDialogAccepted": True, "history": ["x"]}
    assert data["projects"][str(repo.resolve())]["hasTrustDialogAccepted"] is True


def test_ensure_trusted_uses_git_toplevel_for_subdir(claude_json, tmp_path):
    repo = _init_repo(tmp_path / "repo")
    sub = repo / "a" / "b"
    sub.mkdir(parents=True)
    claude_json.write_text(json.dumps({"projects": {}}, indent=2))

    added = trust.ensure_workspace_trusted([sub])

    # Key is the git root, not the subdir.
    assert added == [str(repo.resolve())]


def test_ensure_trusted_missing_file_is_noop(claude_json, tmp_path):
    repo = _init_repo(tmp_path / "repo")
    assert not claude_json.exists()

    added = trust.ensure_workspace_trusted([repo])

    # Best-effort: no file to update → no crash, nothing registered.
    assert added == []


def test_ensure_trusted_unparseable_file_is_noop(claude_json, tmp_path):
    repo = _init_repo(tmp_path / "repo")
    claude_json.write_text("{ not valid json ")

    added = trust.ensure_workspace_trusted([repo])

    assert added == []
    assert claude_json.read_text() == "{ not valid json "  # untouched


def test_ensure_trusted_creates_projects_map_if_absent(claude_json, tmp_path):
    repo = _init_repo(tmp_path / "repo")
    claude_json.write_text(json.dumps({"numStartups": 1}, indent=2))

    trust.ensure_workspace_trusted([repo])

    data = json.loads(claude_json.read_text())
    assert data["projects"][str(repo.resolve())]["hasTrustDialogAccepted"] is True


def test_backfill_registers_all_repos_under_root(claude_json, tmp_path):
    root = tmp_path / "projects"
    r1 = _init_repo(root / "alpha")
    r2 = _init_repo(root / "beta")
    (root / "not_a_repo").mkdir(parents=True)  # skipped
    claude_json.write_text(json.dumps({"projects": {}}, indent=2))

    added = trust.backfill_projects_trust(root)

    data = json.loads(claude_json.read_text())
    assert str(r1.resolve()) in data["projects"]
    assert str(r2.resolve()) in data["projects"]
    assert str((root / "not_a_repo").resolve()) not in data["projects"]
    assert set(added) == {str(r1.resolve()), str(r2.resolve())}


def test_backfill_includes_linked_worktrees(claude_json, tmp_path):
    root = tmp_path / "projects"
    repo = _init_repo(root / "main")
    # a commit is required before adding a worktree
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        check=True,
    )
    wt = root / "main" / ".worktrees" / "w1"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(wt)], check=True)
    claude_json.write_text(json.dumps({"projects": {}}, indent=2))

    added = trust.backfill_projects_trust(root)

    assert str(repo.resolve()) in added
    assert str(wt.resolve()) in added


def test_backfill_nonexistent_root_is_noop(claude_json, tmp_path):
    added = trust.backfill_projects_trust(tmp_path / "nope")
    assert added == []
