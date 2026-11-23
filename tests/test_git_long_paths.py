"""Every git call that writes a working tree runs with ``core.longpaths=true``.

Git for Windows refuses to create a working-tree path longer than 260 characters
("Filename too long") unless ``core.longpaths`` is on. A session worktree nests every
tracked file under ``<repo>/.worktrees/<name>/``, so a file whose path fits the main
checkout can fail the session's checkout, and ``git worktree add`` then aborts with
"Could not reset index file to revision 'HEAD'". Git on Linux and macOS ignores the key.

A real over-limit checkout cannot fail on Linux, so these tests ask real git what value
of ``core.longpaths`` it would see under the exact environment the launcher handed to each
git call. The key and value are spelled as literals rather than imported, so a typo in the
source cannot pass its own test.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_cli import session as session_mod
from ai_cli import sync as sync_mod
from ai_cli import workspace as workspace_mod
from ai_cli.copier_update import _update_one_isolated
from ai_cli.git_repair import pull_rebase_autostash
from ai_cli.session import create_worktree

_REAL_RUN = subprocess.run


@pytest.fixture(autouse=True)
def _no_ambient_git_config(tmp_path, monkeypatch):
    """Keep this host's own global and system git config out of the reading."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in ("GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS"):
        monkeypatch.delenv(var, raising=False)


def _longpaths_seen_by_git(env: dict[str, str], cwd: Path) -> str:
    probe = _REAL_RUN(
        ["git", "config", "--get", "core.longpaths"], capture_output=True, text=True, env=env, cwd=cwd, check=False
    )
    return probe.stdout.strip()


def _is(cmd: list[str], *words: str) -> bool:
    return all(word in cmd for word in words)


class _GitRecorder:
    """Records each git argv with its environment, answering like a cooperative git."""

    def __init__(self, wt_dir: Path, *, first_add_returncode: int = 0, longpaths_config: str | None = None):
        self.wt_dir = wt_dir
        self.first_add_returncode = first_add_returncode
        self.longpaths_config = longpaths_config
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, cmd, **kwargs):
        cmd = list(cmd)
        self.calls.append((cmd, dict(kwargs.get("env") or {})))
        result = MagicMock(returncode=0, stdout="", stderr="")
        if _is(cmd, "config", "--get-all", "core.longpaths"):
            if self.longpaths_config is None:
                result.returncode = 1
            else:
                result.stdout = f"global\t{self.longpaths_config}\n"
        if _is(cmd, "worktree", "add"):
            adds = [call for call, _ in self.calls if _is(call, "worktree", "add")]
            if len(adds) == 1 and self.first_add_returncode:
                result.returncode = self.first_add_returncode
                return result
            self.wt_dir.mkdir(parents=True, exist_ok=True)
        return result

    def envs(self, *words: str) -> list[dict[str, str]]:
        return [env for cmd, env in self.calls if _is(cmd, *words)]

    def config_writes(self) -> list[list[str]]:
        return [cmd for cmd, _ in self.calls if _is(cmd, "config", "core.longpaths", "true") and "--get-all" not in cmd]


def _session_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[tool.ai-cli]\ntask_prefix = "myrepo"\n')
    return repo


def _stub_base():
    return patch("ai_cli.session._resolve_worktree_target", return_value=("refs/remotes/origin/main", "main"))


def test_session_launch_when_the_worktree_add_runs_then_git_sees_core_longpaths_true(tmp_path):
    repo = _session_repo(tmp_path)
    recorder = _GitRecorder(repo / ".worktrees" / "myrepo-tst-1")

    with patch("ai_cli.session.detect_repo_root", return_value=repo), _stub_base():
        with patch("subprocess.run", side_effect=recorder):
            create_worktree("myrepo-tst-1")

    adds = recorder.envs("worktree", "add")
    assert len(adds) == 1
    assert _longpaths_seen_by_git(adds[0], tmp_path) == "true"


def test_session_launch_when_the_first_add_fails_then_the_retry_add_also_sees_core_longpaths_true(tmp_path):
    """A failed ``-b`` add leaves its branch behind, so the retry checks that branch out; it needs the key too."""
    repo = _session_repo(tmp_path)
    recorder = _GitRecorder(repo / ".worktrees" / "myrepo-tst-2", first_add_returncode=128)

    with patch("ai_cli.session.detect_repo_root", return_value=repo), _stub_base():
        with patch("subprocess.run", side_effect=recorder):
            create_worktree("myrepo-tst-2")

    adds = recorder.envs("worktree", "add")
    assert len(adds) == 2
    assert [_longpaths_seen_by_git(env, tmp_path) for env in adds] == ["true", "true"]


def test_session_launch_on_windows_when_longpaths_is_unset_then_the_repo_config_turns_it_on(tmp_path):
    """The session's own later git commands (status, pull, checkout) run without our environment.

    Once the add has written over-limit paths, a plain ``git status`` inside the worktree
    cannot stat them on Windows unless the repository itself enables the key.
    """
    repo = _session_repo(tmp_path)
    recorder = _GitRecorder(repo / ".worktrees" / "myrepo-tst-3")

    with patch("ai_cli.session.detect_repo_root", return_value=repo), _stub_base():
        with patch("subprocess.run", side_effect=recorder), patch.object(session_mod.sys, "platform", "win32"):
            create_worktree("myrepo-tst-3")

    writes = recorder.config_writes()
    assert len(writes) == 1
    assert str(repo) in writes[0]


def test_session_launch_on_windows_when_longpaths_is_explicitly_false_then_the_repo_config_is_left_alone(tmp_path):
    repo = _session_repo(tmp_path)
    recorder = _GitRecorder(repo / ".worktrees" / "myrepo-tst-4", longpaths_config="false")

    with patch("ai_cli.session.detect_repo_root", return_value=repo), _stub_base():
        with patch("subprocess.run", side_effect=recorder), patch.object(session_mod.sys, "platform", "win32"):
            create_worktree("myrepo-tst-4")

    assert recorder.envs("config", "--get-all", "core.longpaths")
    assert recorder.config_writes() == []


def test_session_launch_off_windows_when_the_worktree_is_created_then_the_repo_config_is_not_written(tmp_path):
    repo = _session_repo(tmp_path)
    recorder = _GitRecorder(repo / ".worktrees" / "myrepo-tst-5")

    with patch("ai_cli.session.detect_repo_root", return_value=repo), _stub_base():
        with patch("subprocess.run", side_effect=recorder), patch.object(session_mod.sys, "platform", "linux"):
            create_worktree("myrepo-tst-5")

    assert recorder.config_writes() == []


def test_session_launch_when_the_worktree_syncs_then_the_pull_sees_core_longpaths_true(tmp_path):
    recorder = _GitRecorder(tmp_path / "unused")

    with patch("ai_cli.git_repair.subprocess.run", side_effect=recorder):
        pull_rebase_autostash(tmp_path)

    pulls = recorder.envs("pull", "--rebase")
    assert len(pulls) == 1
    assert _longpaths_seen_by_git(pulls[0], tmp_path) == "true"


def test_copier_update_when_its_worktree_add_runs_then_git_sees_core_longpaths_true(tmp_path):
    (tmp_path / ".copier-answers.yml").write_text("_src_path: /projects/project-template\n_commit: previous\n")
    recorder = _GitRecorder(tmp_path / "unused")

    with patch("ai_cli.copier_update._repo_root", return_value=tmp_path):
        with patch("ai_cli.copier_update.subprocess.run", side_effect=recorder):
            with patch("ai_cli.copier_update._cleanup_worktree"):
                with patch("ai_cli.copier_update._do_update_in_worktree", return_value=("ok", "")):
                    _update_one_isolated(tmp_path, "/usr/bin/copier")

    adds = recorder.envs("worktree", "add")
    assert len(adds) == 1
    assert _longpaths_seen_by_git(adds[0], tmp_path) == "true"


def test_workspace_pull_when_a_dirty_repo_is_stashed_and_pulled_then_each_write_sees_core_longpaths_true(tmp_path):
    folder = tmp_path / "myproject"
    folder.mkdir()
    workspace = tmp_path / "my.code-workspace"
    workspace.write_text('{"folders": [{"path": "myproject"}]}')
    recorder = _GitRecorder(tmp_path / "unused")

    def dirty(cmd, **kwargs):
        result = recorder(cmd, **kwargs)
        if _is(list(cmd), "status", "--porcelain"):
            result.stdout = " M file.txt\n"
        return result

    with patch.object(workspace_mod.subprocess, "run", side_effect=dirty):
        workspace_mod.ws_pull(workspace)

    writes = recorder.envs("stash", "push") + recorder.envs("pull", "--rebase") + recorder.envs("stash", "pop")
    assert len(writes) == 3
    assert {_longpaths_seen_by_git(env, tmp_path) for env in writes} == {"true"}


def test_sync_library_pull_when_a_repo_is_stashed_and_pulled_then_each_write_sees_core_longpaths_true(tmp_path):
    recorder = _GitRecorder(tmp_path / "unused")

    with patch.object(sync_mod.subprocess, "run", side_effect=recorder):
        sync_mod._git_stash_pull_pop(tmp_path)
        sync_mod._git_pull_rebase(tmp_path)

    writes = recorder.envs("stash", "push") + recorder.envs("pull", "--rebase") + recorder.envs("stash", "pop")
    assert len(writes) == 4
    assert {_longpaths_seen_by_git(env, tmp_path) for env in writes} == {"true"}
