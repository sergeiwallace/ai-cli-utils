"""The CHANGELOG rule for ``feat:`` commits, run the way git runs it.

Each test builds a throwaway repository that carries this repo's real
``.githooks/commit-msg`` and the real ``.pre-commit-config.yaml`` entry that
registers it, installs the commit-msg stage with ``pre-commit install``, and
then makes an ordinary ``git commit``. A test that called the script directly
would pass while the hook was registered nowhere, which is how the rule went
unenforced before.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = ".githooks/commit-msg"

pytestmark = [
    pytest.mark.skipif(shutil.which("pre-commit") is None, reason="requires the pre-commit framework"),
    pytest.mark.skipif(os.name == "nt", reason="the hook is a bash script run through git's sh shim"),
]


def _registered_hook() -> dict[str, object]:
    config = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    for repo in config["repos"]:
        if repo["repo"] != "local":
            continue
        for hook in repo["hooks"]:
            if hook["entry"] == HOOK_SCRIPT:
                return hook
    raise AssertionError(f"no local hook in .pre-commit-config.yaml runs {HOOK_SCRIPT}")


def _git(repo: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=False)


@pytest.fixture
def hooked_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR"}
    }
    env["PRE_COMMIT_HOME"] = str(tmp_path / "pre-commit-home")
    env.pop("SKIP_CHANGELOG", None)
    assert _git(repo, "init", "-q", env=env).returncode == 0
    _git(repo, "config", "user.email", "test@example.com", env=env)
    _git(repo, "config", "user.name", "Test", env=env)
    _git(repo, "config", "commit.gpgsign", "false", env=env)

    (repo / ".githooks").mkdir()
    shutil.copy2(REPO_ROOT / HOOK_SCRIPT, repo / HOOK_SCRIPT)
    config = {"repos": [{"repo": "local", "hooks": [_registered_hook()]}]}
    (repo / ".pre-commit-config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (repo / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    _git(repo, "add", "-A", env=env)
    seeded = _git(repo, "commit", "-q", "-m", "chore: seed", env=env)
    assert seeded.returncode == 0, seeded.stderr

    installed = subprocess.run(
        ["pre-commit", "install", "--hook-type", "commit-msg"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        check=False,
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
