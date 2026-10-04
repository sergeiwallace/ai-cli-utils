"""Every `git worktree add` this tool runs attests who is creating the worktree.

ai-harness installs a `post-checkout` backstop (`scripts/worktree_creation_guard.py`) that
undoes a worktree created flat under `<repo>/.worktrees/`, because that root is reserved for
a Claude Code session's own checkout. Both worktrees this package creates are flat there, so
without the attestation the backstop would remove a session home at the moment `ai c <n>`
created it, and `ai copier-update` would lose its temp worktree the same way.

The variable NAMES are a cross-repo contract, so they are spelled as literals here rather
than imported from the code under test: importing them would make a typo in the source pass
its own test. The guard spells them as literals on its side for the same reason.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_cli.copier_update import _update_one_isolated
from ai_cli.git_repair import _creator_env
from ai_cli.session import create_worktree

CREATOR_VAR = "AIH_WORKTREE_CREATOR"
CANONICAL_LEAF_VAR = "AIH_WORKTREE_CANONICAL_LEAF"


def _is_worktree_add(cmd: list[str]) -> bool:
    return "worktree" in cmd and "add" in cmd


class _GitRecorder:
    """Records every git argv with the environment it was actually given."""

    def __init__(self, wt_dir: Path, first_add_returncode: int = 0):
        self.wt_dir = wt_dir
        self.first_add_returncode = first_add_returncode
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), dict(kwargs.get("env") or {})))
        result = MagicMock(returncode=0)
        result.stdout = ""
        result.stderr = ""
        if _is_worktree_add(cmd):
            adds = [call for call, _ in self.calls if _is_worktree_add(call)]
            if len(adds) == 1 and self.first_add_returncode != 0:
                result.returncode = self.first_add_returncode
                return result
            self.wt_dir.mkdir(parents=True, exist_ok=True)
        return result

    def envs_for_adds(self) -> list[dict[str, str]]:
        return [env for cmd, env in self.calls if _is_worktree_add(cmd)]

    def envs_for_non_adds(self) -> list[tuple[list[str], dict[str, str]]]:
        return [(cmd, env) for cmd, env in self.calls if not _is_worktree_add(cmd)]


def _stub_base():
    return patch(
        "ai_cli.session._resolve_worktree_target",
        return_value=("refs/remotes/origin/main", "main"),
    )


def test_create_worktree_when_the_add_runs_then_it_attests_ai_c_and_the_slot_name(tmp_path):
    wt_dir = tmp_path / ".worktrees" / "session-2"
    recorder = _GitRecorder(wt_dir)

    with patch("ai_cli.session.detect_repo_root", return_value=tmp_path), _stub_base():
        with patch("subprocess.run", side_effect=recorder):
            assert create_worktree("session-2") == wt_dir

    adds = recorder.envs_for_adds()
    assert len(adds) == 1
    assert adds[0][CREATOR_VAR] == "ai-c"
    # The declared leaf is the destination's own directory name: the backstop admits the ONE
    # path the marker names, not every canonical-looking leaf.
    assert adds[0][CANONICAL_LEAF_VAR] == wt_dir.name == "session-2"


def test_create_worktree_when_the_first_add_fails_then_the_fallback_add_attests_too(tmp_path):
    """The retry against an existing branch is just as flat, so it needs the marker too."""
    wt_dir = tmp_path / ".worktrees" / "session-3"
    recorder = _GitRecorder(wt_dir, first_add_returncode=128)

    with patch("ai_cli.session.detect_repo_root", return_value=tmp_path), _stub_base():
        with patch("subprocess.run", side_effect=recorder):
            assert create_worktree("session-3") == wt_dir

    adds = recorder.envs_for_adds()
    assert len(adds) == 2
    for env in adds:
        assert env[CREATOR_VAR] == "ai-c"
        assert env[CANONICAL_LEAF_VAR] == "session-3"


def test_create_worktree_when_a_git_call_creates_nothing_then_it_carries_no_attestation(tmp_path):
    """Only the creating call attests.

    The marker is an authorisation to create a path that is otherwise refused, so spreading
    it across every git call would hand it to `prune`, `rev-parse` and `branch`, none of
    which create anything — and any of which could be the one that leaks it onward.
    """
    wt_dir = tmp_path / ".worktrees" / "session-4"
    recorder = _GitRecorder(wt_dir)

    with patch("ai_cli.session.detect_repo_root", return_value=tmp_path), _stub_base():
        with patch("subprocess.run", side_effect=recorder):
            assert create_worktree("session-4") == wt_dir

    non_adds = recorder.envs_for_non_adds()
    # A positive control: without a non-add git call in the log this would assert nothing.
    assert any(cmd[:1] == ["git"] for cmd, _ in non_adds)
    for cmd, env in non_adds:
        assert CREATOR_VAR not in env, cmd
        assert CANONICAL_LEAF_VAR not in env, cmd


def test_update_one_isolated_when_the_add_runs_then_it_attests_ai_copier_update(tmp_path):
    (tmp_path / ".copier-answers.yml").write_text("_src_path: /projects/project-template\n_commit: previous\n")
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), dict(kwargs.get("env") or {})))
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("ai_cli.copier_update._repo_root", return_value=tmp_path):
        with patch("ai_cli.copier_update.subprocess.run", side_effect=fake_run):
            with patch("ai_cli.copier_update._cleanup_worktree"):
                with patch("ai_cli.copier_update._do_update_in_worktree", return_value=("ok", "")):
                    status, _ = _update_one_isolated(tmp_path, "/usr/bin/copier")

    assert status == "ok"
    adds = [env for cmd, env in calls if _is_worktree_add(cmd)]
    assert len(adds) == 1
    assert adds[0][CREATOR_VAR] == "ai-copier-update"
    # No slot name: the temp worktree is not a session home, and the backstop compares a
    # declared leaf only for `ai-c`.
    assert CANONICAL_LEAF_VAR not in adds[0]
    non_adds = [(cmd, env) for cmd, env in calls if not _is_worktree_add(cmd)]
    assert any(cmd[:1] == ["git"] for cmd, _ in non_adds)
    for cmd, env in non_adds:
        assert CREATOR_VAR not in env, cmd


def test_creator_env_when_built_then_it_keeps_the_git_env_containment():
    """The attestation rides on the scrubbed environment, it does not replace it."""
    base = {
        "GIT_DIR": "/elsewhere/.git",
        "GIT_WORK_TREE": "/elsewhere",
        "PATH": "/usr/bin",
    }
    with patch.dict("os.environ", base, clear=True):
        env = _creator_env("ai-c", "session-1")

    assert "GIT_DIR" not in env
    assert "GIT_WORK_TREE" not in env
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["PATH"] == "/usr/bin"


def test_creator_env_when_no_leaf_is_declared_then_an_inherited_leaf_is_dropped():
    """The attestation describes THIS call.

    A launcher started from inside a marked process would otherwise pass that process's slot
    name along, so a creator carrying no leaf of its own could present a stale one.
    """
    inherited = {CREATOR_VAR: "ai-c", CANONICAL_LEAF_VAR: "someone-elses-slot"}
    with patch.dict("os.environ", inherited, clear=True):
        env = _creator_env("ai-copier-update")

    assert env[CREATOR_VAR] == "ai-copier-update"
    assert CANONICAL_LEAF_VAR not in env
