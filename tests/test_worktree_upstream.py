"""A session worktree must track the branch its repository actually integrates through.

``create_worktree`` used to point every new worktree branch at ``origin/main``
unconditionally. In a repository whose own checkout sits on a long-running workspace
branch, that aims routine session work at a branch the repository does not integrate
through — and in a shared repository on a pull-request workflow, ``main`` is the branch
nobody may push to directly. ``git push origin HEAD:main`` from such a worktree goes
straight through.

Two upstream writers exist in this path, which is why "attach the right one" is not a
one-line change:

* the explicit ``git branch --set-upstream-to=...`` call, and
* ``git worktree add -b <branch> <start-point>`` itself, which sets an upstream via
  git's default ``branch.autoSetupMerge`` whenever the start-point is a
  remote-tracking ref. So declining to attach an upstream must actively clear one.

Resolution is config-first (``[worktree_upstream]``), then the branch the main working
tree has checked out. When the resolved branch does not exist on ``origin``, the
worktree gets NO upstream rather than ``origin/main``: a missing upstream makes the
first push stop and ask, which is the safe direction.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_cli.session import create_worktree


def _git(*args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=str(cwd), check=check, capture_output=True, text=True)


def _out(*args, cwd):
    return _git(*args, cwd=cwd).stdout.strip()


def _upstream_of(branch, *, cwd):
    """Return the branch's upstream, or None when it tracks nothing."""
    res = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", f"{branch}@{{u}}", cwd=cwd, check=False)
    return res.stdout.strip() if res.returncode == 0 else None


def _init_seed(seed: Path) -> None:
    _git("init", "-q", "-b", "main", ".", cwd=seed)
    _git("config", "user.email", "t@example.com", cwd=seed)
    _git("config", "user.name", "T", cwd=seed)


@pytest.fixture
def repo_on_workspace_branch(tmp_path):
    """A clone named ``myproject`` whose checkout is on a pushed ``workspace`` branch."""
    remote = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)

    seed = tmp_path / "seed"
    seed.mkdir()
    _init_seed(seed)
    (seed / "README.md").write_text("base\n")
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "shared base", cwd=seed)
    _git("push", "-q", str(remote), "main", cwd=seed)

    _git("checkout", "-q", "-b", "workspace", cwd=seed)
    (seed / "workspace-notes.md").write_text("workspace only\n")
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "workspace only commit", cwd=seed)
    _git("push", "-q", str(remote), "workspace", cwd=seed)

    # main advances separately, so the two are genuinely divergent.
    _git("checkout", "-q", "main", cwd=seed)
    (seed / "shipped.md").write_text("landed on main\n")
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "landed on main", cwd=seed)
    _git("push", "-q", str(remote), "main", cwd=seed)

    repo = tmp_path / "myproject"
    _git("clone", "-q", str(remote), str(repo), cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "T", cwd=repo)
    _git("checkout", "-q", "-b", "workspace", "origin/workspace", cwd=repo)
    return repo


@pytest.fixture
def repo_on_main(tmp_path):
    """The ordinary case: a clone named ``myproject`` whose checkout is on ``main``."""
    remote = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp_path)

    seed = tmp_path / "seed"
    seed.mkdir()
    _init_seed(seed)
    (seed / "README.md").write_text("base\n")
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "shared base", cwd=seed)
    _git("push", "-q", str(remote), "main", cwd=seed)

    repo = tmp_path / "myproject"
    _git("clone", "-q", str(remote), str(repo), cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "T", cwd=repo)
    return repo


def _config(**worktree_upstream):
    """Patch the config file's contents rather than any resolver function.

    Deliberately patches ``ai_cli.config.load_config``, which exists independently of
    this fix, so these tests exercise the real resolution path end to end and fail on
    a behavioural assertion rather than on a missing patch target.
    """
    return patch("ai_cli.config.load_config", return_value={"worktree_upstream": dict(worktree_upstream)})


def _no_config():
    """No repository declares an integration branch, so resolution uses the checkout."""
    return _config()


# --- AC-1: a repo on a non-main branch tracks that branch ---


def test_given_checkout_on_workspace_branch_when_worktree_created_then_tracks_that_branch(
    repo_on_workspace_branch, monkeypatch
):
    """THE REGRESSION. The old code attached ``origin/main`` here."""
    repo = repo_on_workspace_branch
    monkeypatch.chdir(repo)
    assert _out("rev-parse", "--abbrev-ref", "HEAD", cwd=repo) == "workspace"

    with patch("ai_cli.trust.ensure_workspace_trusted"), _no_config():
        worktree = create_worktree("session-1")

    assert worktree is not None and worktree.is_dir()
    assert _upstream_of("wt-session-1", cwd=repo) == "origin/workspace"
    # The point of the issue: not main, however main has moved.
    assert _upstream_of("wt-session-1", cwd=repo) != "origin/main"

    # Base agrees with upstream — a worktree based on main while tracking the
    # workspace branch would make its first pull --rebase replay unrelated commits.
    assert _out("rev-parse", "wt-session-1", cwd=repo) == _out("rev-parse", "origin/workspace", cwd=repo)
    assert (worktree / "workspace-notes.md").exists(), "the worktree must contain the workspace branch's work"


def test_given_checkout_on_workspace_branch_when_worktree_created_then_that_branch_is_untouched(
    repo_on_workspace_branch, monkeypatch
):
    """Resolving an upstream must not move the branch the main tree is on."""
    repo = repo_on_workspace_branch
    before = _out("rev-parse", "workspace", cwd=repo)
    monkeypatch.chdir(repo)

    with patch("ai_cli.trust.ensure_workspace_trusted"), _no_config():
        create_worktree("session-1")

    assert _out("rev-parse", "workspace", cwd=repo) == before
    assert _out("rev-parse", "--abbrev-ref", "HEAD", cwd=repo) == "workspace"


# --- AC-2: no regression for a repo on main ---


def test_given_checkout_on_main_when_worktree_created_then_still_tracks_origin_main(repo_on_main, monkeypatch):
    """The common personal-repo case must behave exactly as before."""
    repo = repo_on_main
    monkeypatch.chdir(repo)
    assert _out("rev-parse", "--abbrev-ref", "HEAD", cwd=repo) == "main"

    with patch("ai_cli.trust.ensure_workspace_trusted"), _no_config():
        worktree = create_worktree("session-1")

    assert worktree is not None and worktree.is_dir()
    assert _upstream_of("wt-session-1", cwd=repo) == "origin/main"
    assert _out("rev-parse", "wt-session-1", cwd=repo) == _out("rev-parse", "origin/main", cwd=repo)


# --- AC-3: resolution failure attaches NO upstream, never origin/main ---


def test_given_integration_branch_absent_from_origin_when_worktree_created_then_no_upstream(
    repo_on_main, monkeypatch, capsys
):
    """A never-pushed workspace branch must yield NO upstream, not ``origin/main``.

    ``origin/main`` is present and resolvable throughout, so a fallback would
    silently succeed — which is exactly what this asserts cannot happen.
    """
    repo = repo_on_main
    _git("checkout", "-q", "-b", "local-only-workspace", cwd=repo)
    monkeypatch.chdir(repo)
    assert _out("rev-parse", "--verify", "--quiet", "refs/remotes/origin/main", cwd=repo), "origin/main must exist"

    with patch("ai_cli.trust.ensure_workspace_trusted"), _no_config():
        worktree = create_worktree("session-1")

    assert worktree is not None and worktree.is_dir()
    upstream = _upstream_of("wt-session-1", cwd=repo)
    assert upstream is None, f"expected NO upstream, got {upstream!r}"
    assert "NO upstream" in capsys.readouterr().err, "the user must be told the worktree tracks nothing"


def test_given_configured_branch_exists_only_locally_when_worktree_created_then_no_upstream(repo_on_main, monkeypatch):
    """A declared branch present locally but not on origin must not degrade to main.

    ``origin/main`` exists and resolves throughout, so a fallback would silently
    succeed. The worktree is still created — the local branch is an honest base —
    but it tracks nothing.
    """
    repo = repo_on_main
    _git("branch", "local-only-workspace", cwd=repo)
    monkeypatch.chdir(repo)

    with (
        patch("ai_cli.trust.ensure_workspace_trusted"),
        _config(myproject="local-only-workspace"),
    ):
        worktree = create_worktree("session-1")

    assert worktree is not None and worktree.is_dir()
    upstream = _upstream_of("wt-session-1", cwd=repo)
    assert upstream is None, f"expected NO upstream, got {upstream!r}"


def test_given_configured_branch_exists_nowhere_when_worktree_created_then_raises(repo_on_main, monkeypatch):
    """A declared branch that exists neither on origin nor locally is a hard failure."""
    repo = repo_on_main
    monkeypatch.chdir(repo)

    with (
        patch("ai_cli.trust.ensure_workspace_trusted"),
        _config(myproject="never-created"),
        pytest.raises(RuntimeError, match="neither on"),
    ):
        create_worktree("session-1")

    assert not (repo / ".worktrees" / "session-1").exists(), "no worktree may be left behind"


def test_given_detached_head_when_worktree_created_then_raises_instead_of_guessing(repo_on_main, monkeypatch):
    """Nothing anchors a session branch in a detached HEAD, so refuse rather than guess."""
    repo = repo_on_main
    _git("checkout", "-q", "--detach", cwd=repo)
    monkeypatch.chdir(repo)

    with (
        patch("ai_cli.trust.ensure_workspace_trusted"),
        _no_config(),
        pytest.raises(RuntimeError, match="detached"),
    ):
        create_worktree("session-1")

    assert not (repo / ".worktrees" / "session-1").exists(), "no worktree may be left behind"


def test_given_no_origin_remote_when_worktree_created_then_raises(tmp_path, monkeypatch):
    """No remote at all is a hard failure, never a silent fallback to HEAD."""
    repo = tmp_path / "myproject"
    repo.mkdir()
    _init_seed(repo)
    (repo / "README.md").write_text("hi\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "init", cwd=repo)
    monkeypatch.chdir(repo)

    with (
        patch("ai_cli.trust.ensure_workspace_trusted"),
        _no_config(),
        pytest.raises(RuntimeError, match="no `origin` remote"),
    ):
        create_worktree("session-1")

    assert not (repo / ".worktrees" / "session-1").exists()


# --- AC-5: the workspace branch is configured, never hardcoded ---


def test_given_configured_integration_branch_when_worktree_created_then_config_wins_over_checkout(
    repo_on_workspace_branch, monkeypatch
):
    """Config takes precedence over the checked-out branch, keyed by repo name.

    Proves the branch is not hardcoded anywhere: the same repository resolves to a
    different upstream purely by changing configuration, with no code change and no
    repository name special-cased in source.
    """
    repo = repo_on_workspace_branch
    monkeypatch.chdir(repo)
    assert _out("rev-parse", "--abbrev-ref", "HEAD", cwd=repo) == "workspace"

    with (
        patch("ai_cli.trust.ensure_workspace_trusted"),
        _config(myproject="main"),
    ):
        create_worktree("session-1")

    assert _upstream_of("wt-session-1", cwd=repo) == "origin/main"


def test_given_config_table_when_read_then_blank_branches_are_ignored(tmp_path):
    """A declared-but-empty branch must not resolve to an empty ref."""
    from ai_cli.config import get_worktree_upstream_branches

    with patch("ai_cli.config.load_config", return_value={"worktree_upstream": {"myproject": "  ", "myapp": "trunk"}}):
        assert get_worktree_upstream_branches() == {"myapp": "trunk"}


def test_given_config_table_of_wrong_type_when_read_then_returns_empty(tmp_path):
    from ai_cli.config import get_worktree_upstream_branches

    with patch("ai_cli.config.load_config", return_value={"worktree_upstream": "not-a-table"}):
        assert get_worktree_upstream_branches() == {}


# --- the drift check and the creation path must agree ---


def test_given_worktree_tracking_its_integration_branch_when_drift_checked_then_no_drift(
    repo_on_workspace_branch, monkeypatch
):
    """A worktree `ai c` just created correctly must not be reported as drifted.

    The drift check used to expect ``origin/main`` unconditionally, so in exactly the
    repositories this fix targets it flagged every correct worktree and stayed silent
    about the wrong one. Checking creation and detection together is what catches that.
    """
    from ai_cli.workspace import _expected_upstream, _upstream_drift

    repo = repo_on_workspace_branch
    monkeypatch.chdir(repo)

    with patch("ai_cli.trust.ensure_workspace_trusted"), _no_config():
        worktree = create_worktree("session-1")

    with _no_config():
        assert _expected_upstream(worktree) == "origin/workspace"
        assert _upstream_drift(worktree) is None

    # Negative control: repoint it at main and the same check must now report drift.
    _git("branch", "--set-upstream-to=origin/main", "wt-session-1", cwd=repo)
    with _no_config():
        drift = _upstream_drift(worktree)
    assert drift is not None, "a worktree repointed at origin/main must be reported as drifted"
    assert "origin/main" in drift


def test_given_path_outside_any_worktree_when_expected_upstream_resolved_then_none(tmp_path):
    """A path that is not inside a worktree directory yields no expectation."""
    from ai_cli.workspace import _expected_upstream

    assert _expected_upstream(tmp_path) is None


# --- existing-branch resume path is unaffected ---


def test_given_existing_worktree_branch_when_recreated_then_history_is_preserved(repo_on_workspace_branch, monkeypatch):
    """Re-creating a removed worktree reuses the branch rather than rebasing it."""
    repo = repo_on_workspace_branch
    monkeypatch.chdir(repo)

    with patch("ai_cli.trust.ensure_workspace_trusted"), _no_config():
        worktree = create_worktree("session-1")

    (worktree / "session-work.md").write_text("work from the session\n")
    _git("add", "-A", cwd=worktree)
    _git("commit", "-q", "-m", "session work", cwd=worktree)
    session_tip = _out("rev-parse", "wt-session-1", cwd=repo)

    _git("worktree", "remove", "--force", str(worktree), cwd=repo)

    with patch("ai_cli.trust.ensure_workspace_trusted"), _no_config():
        again = create_worktree("session-1")

    assert again == worktree
    assert _out("rev-parse", "wt-session-1", cwd=repo) == session_tip
    assert (Path(again) / "session-work.md").exists(), "the session's own commit must survive re-creation"
