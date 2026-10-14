"""The CHANGELOG rule for ``feat:`` commits, run the way git runs it.

Each test builds a throwaway repository that carries this repo's real hook
scripts, the real ``.pre-commit-config.yaml`` entries that register them, and
the real ``.githooks/install.sh``, then makes an ordinary ``git commit``. A
test that called the script directly would pass while the hook was registered
nowhere, which is how the rule went unenforced before.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPTS = (".githooks/commit-msg",)

pytestmark = [
    pytest.mark.skipif(shutil.which("pre-commit") is None, reason="requires the pre-commit framework"),
    pytest.mark.skipif(os.name == "nt", reason="the hook is a bash script run through git's sh shim"),
]


def _registered_hooks() -> list[dict[str, object]]:
    config = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hooks = [
        hook
        for repo in config["repos"]
        if repo["repo"] == "local"
        for hook in repo["hooks"]
        if hook["entry"] in HOOK_SCRIPTS
    ]
    assert sorted(hook["entry"] for hook in hooks) == sorted(HOOK_SCRIPTS)
    return hooks


def _clean_env(pre_commit_home: Path) -> dict[str, str]:
    """The caller's env without anything that steers git, pre-commit or the hook.

    A developer's or CI runner's ``SKIP``/``SKIP_CHANGELOG``, an inherited
    ``GIT_DIR``, or a pinned ``GIT_AUTHOR_DATE`` would otherwise decide the result.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GIT_", "PRE_COMMIT")) and key not in {"SKIP", "SKIP_CHANGELOG"}
    }
    env["PRE_COMMIT_HOME"] = str(pre_commit_home)
    return env


def _git(repo: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=False)


@pytest.fixture
def hooked_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = _clean_env(tmp_path / "pre-commit-home")
    assert _git(repo, "init", "-q", env=env).returncode == 0
    _git(repo, "config", "user.email", "test@example.com", env=env)
    _git(repo, "config", "user.name", "Test", env=env)
    _git(repo, "config", "commit.gpgsign", "false", env=env)

    (repo / ".githooks").mkdir()
    for script in (*HOOK_SCRIPTS, ".githooks/install.sh"):
        shutil.copy2(REPO_ROOT / script, repo / script)
    config = {"repos": [{"repo": "local", "hooks": _registered_hooks()}]}
    (repo / ".pre-commit-config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (repo / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    _git(repo, "add", "-A", env=env)
    seeded = _git(repo, "commit", "-q", "-m", "chore: seed", env=env)
    assert seeded.returncode == 0, seeded.stderr

    installed = subprocess.run(
        ["bash", ".githooks/install.sh"], cwd=repo, capture_output=True, text=True, env=env, check=False
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    return repo, env


def _commit(repo: Path, env: dict[str, str], message: str, *, changelog: bool) -> subprocess.CompletedProcess[str]:
    (repo / "feature.txt").write_text(message, encoding="utf-8")
    _git(repo, "add", "feature.txt", env=env)
    if changelog:
        with (repo / "CHANGELOG.md").open("a", encoding="utf-8") as handle:
            handle.write(f"- {message}\n")
        _git(repo, "add", "CHANGELOG.md", env=env)
    return _git(repo, "commit", "-m", message, env=env)


def test_given_a_feat_commit_without_changelog_when_committed_then_the_hook_refuses_it(hooked_repo):
    repo, env = hooked_repo

    result = _commit(repo, env, "feat: add a thing", changelog=False)

    assert result.returncode != 0
    assert "feat: commits require a CHANGELOG.md [Unreleased] entry" in result.stdout + result.stderr
    assert _git(repo, "log", "-1", "--format=%s", env=env).stdout.strip() == "chore: seed"


def test_given_a_scoped_feat_commit_without_changelog_when_committed_then_the_hook_refuses_it(hooked_repo):
    repo, env = hooked_repo

    result = _commit(repo, env, "feat(cli): add a flag", changelog=False)

    assert result.returncode != 0
    assert "feat: commits require a CHANGELOG.md [Unreleased] entry" in result.stdout + result.stderr


def test_given_a_feat_commit_staging_changelog_when_committed_then_it_lands(hooked_repo):
    repo, env = hooked_repo

    result = _commit(repo, env, "feat: add a thing", changelog=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(repo, "log", "-1", "--format=%s", env=env).stdout.strip() == "feat: add a thing"


def test_given_a_fix_commit_without_changelog_when_committed_then_it_lands(hooked_repo):
    repo, env = hooked_repo

    result = _commit(repo, env, "fix: correct a thing", changelog=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(repo, "log", "-1", "--format=%s", env=env).stdout.strip() == "fix: correct a thing"


def test_given_a_feat_commit_carrying_changelog_when_reworded_then_the_hook_names_the_amend_bypass(hooked_repo):
    """No hook stage can tell an amend from a new commit, so the check stays against HEAD."""
    repo, env = hooked_repo
    assert _commit(repo, env, "feat: add a thing", changelog=True).returncode == 0

    result = _git(repo, "commit", "--amend", "-m", "feat: add a better thing", env=env)

    assert result.returncode != 0
    assert "SKIP_CHANGELOG=1 git commit --amend" in result.stdout + result.stderr


def test_given_skip_changelog_when_a_feat_commit_is_reworded_then_it_lands(hooked_repo):
    repo, env = hooked_repo
    assert _commit(repo, env, "feat: add a thing", changelog=True).returncode == 0

    result = _git(repo, "commit", "--amend", "-m", "feat: add a better thing", env={**env, "SKIP_CHANGELOG": "1"})

    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(repo, "log", "-1", "--format=%s", env=env).stdout.strip() == "feat: add a better thing"


def test_given_a_commit_without_changelog_when_amended_to_feat_then_the_hook_refuses_it(hooked_repo):
    repo, env = hooked_repo
    assert _commit(repo, env, "fix: correct a thing", changelog=False).returncode == 0

    result = _git(repo, "commit", "--amend", "-m", "feat: add a thing", env=env)

    assert result.returncode != 0
    assert "feat: commits require a CHANGELOG.md [Unreleased] entry" in result.stdout + result.stderr


def test_given_a_new_feat_commit_in_the_same_second_as_head_when_committed_then_the_hook_refuses_it(hooked_repo):
    """A new commit can share HEAD's author date on a fast machine, as on CI; HEAD's entry is not its own."""
    repo, env = hooked_repo
    head_date = _git(repo, "log", "-1", "--format=@%at %ai", env=env).stdout.split()
    same_second = {**env, "GIT_AUTHOR_DATE": f"{head_date[0]} {head_date[-1]}"}

    result = _commit(repo, same_second, "feat(cli): add a flag", changelog=False)

    assert result.returncode != 0
    assert "feat: commits require a CHANGELOG.md [Unreleased] entry" in result.stdout + result.stderr
