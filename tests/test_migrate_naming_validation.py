"""Migration validates the target against the current owning repository and host."""

import subprocess

import pytest

from ai_cli import config, session


@pytest.fixture
def registered_repo(tmp_path, monkeypatch):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--allow-empty",
            "-q",
            "-m",
            "initial",
        ],
        check=True,
    )
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "--bare", "-q", str(repo), str(origin)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(origin)], check=True)
    subprocess.run(["git", "-C", str(repo), "fetch", "-q", "origin"], check=True)
    settings = {"project_registry": {str(repo): {"prefix": "myrepo"}}}
    monkeypatch.setattr(config, "load_config", lambda: settings)
    monkeypatch.setattr(session, "is_current_project_resolved", lambda: True)
    monkeypatch.setattr(session, "_tmux_session_names", list)
    monkeypatch.chdir(repo)
    return repo, settings


@pytest.mark.parametrize("target", ["wrong-box-3", "myrepo-other-3", "myrepo-box-0"])
def test_given_wrong_target_when_migrating_then_grammar_refuses_before_move(registered_repo, target):
    from ai_cli.session_migrate import migrate_session

    repo, _ = registered_repo
    old = "myrepo-tst-3"
    source = repo / ".worktrees" / old
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", "-b", f"wt-{old}", str(source), "main"], check=True
    )
    with pytest.raises(RuntimeError, match="rule"):
        migrate_session(repo, old, target, dry_run=True)
    assert source.is_dir()
    assert not (repo / ".worktrees" / target).exists()


def test_given_repo_alias_changed_when_migrating_then_only_current_alias_is_accepted(registered_repo):
    from ai_cli.session_migrate import migrate_session

    repo, settings = registered_repo
    old = "myrepo-tst-3"
    source = repo / ".worktrees" / old
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", "-b", f"wt-{old}", str(source), "main"], check=True
    )
    settings["project_registry"][str(repo)]["prefix"] = "new"
    with pytest.raises(RuntimeError, match="repo alias rule"):
        migrate_session(repo, old, "myrepo-tst-4", dry_run=True)
    plan = migrate_session(repo, old, "new-tst-4", dry_run=True)
    assert plan.new_worktree == repo / ".worktrees" / "new-tst-4"
    assert source.is_dir()
