"""A local session must stay in the repo it was launched from.

Two independent defects made ``ai c`` unusable on any Linux/Windows host that
sets ``AI_HOST``:

1. ``_resolve_is_remote()`` treated *every* non-Mac ``AI_HOST`` value as "this is
   a remote session", which is only true for the SSH-injected ``--is-remote``
   case.  A plain local launch on such a host therefore took the ``if
   is_remote:`` branch in ``_do_session_launch`` and ``chdir``-ed to the
   configured main project before creating the worktree -- so ``ai c`` inside
   repo A created its worktree, branch, and session inside repo B.

2. ``_exec_with_direnv`` exec'd ``direnv exec`` unconditionally.  When the target
   ``.envrc`` is not approved, direnv exits 1 **without running the command at
   all**, so the launch died with a bare "is blocked" message and no engine ever
   started.
"""

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_cli.main import _do_session_launch, _exec_with_direnv
from ai_cli.session import _resolve_is_remote

# --- _resolve_is_remote --------------------------------------------------------


def test_given_is_remote_flag_when_resolved_then_true(monkeypatch):
    """The SSH-injected --is-remote flag is the authoritative signal."""
    monkeypatch.delenv("AI_HOST", raising=False)
    assert _resolve_is_remote(True) is True


def test_given_linux_ai_host_without_flag_when_resolved_then_local(monkeypatch):
    """The regression: a local launch on a named non-Mac host is NOT remote.

    Previously any AI_HOST not equal to "mac" forced remote mode, which
    redirected the worktree into the configured main project.
    """
    monkeypatch.setenv("AI_HOST", "my-linux-box")
    assert _resolve_is_remote(False) is False


def test_given_mac_ai_host_without_flag_when_resolved_then_local(monkeypatch):
    monkeypatch.setenv("AI_HOST", "mac")
    assert _resolve_is_remote(False) is False


def test_given_no_ai_host_without_flag_when_resolved_then_local(monkeypatch):
    monkeypatch.delenv("AI_HOST", raising=False)
    assert _resolve_is_remote(False) is False


# --- direnv fallback -----------------------------------------------------------


def test_given_blocked_envrc_when_exec_then_command_still_runs(tmp_path):
    """A blocked .envrc must not swallow the launch.

    ``direnv exec`` on an unapproved .envrc exits non-zero and never runs the
    command, so the wrapper has to detect that and exec the command directly.
    """
    (tmp_path / ".envrc").write_text("export NEVER_APPROVED=1\n")
    execs: list[list[str]] = []

    def fake_execvp(file, args):
        execs.append([file, *list(args)[1:]])
        raise SystemExit(0)

    def fake_run(cmd, *a, **kw):
        # Emulate direnv refusing an unapproved .envrc.
        return subprocess.CompletedProcess(cmd, 1, "", "direnv: error .envrc is blocked")

    with (
        patch("ai_cli.main.os.execvp", side_effect=fake_execvp),
        patch("ai_cli.main.subprocess.run", side_effect=fake_run),
    ):
        with pytest.raises(SystemExit):
            _exec_with_direnv(tmp_path, ["claude", "--name", "kg-1"])

    assert execs, "the engine must still be exec'd when direnv refuses the .envrc"
    assert execs[0][0] == "claude", f"expected a direct claude exec, got {execs[0]!r}"


def test_given_usable_envrc_when_exec_then_goes_through_direnv(tmp_path):
    """The happy path must still load the project environment via direnv."""
    (tmp_path / ".envrc").write_text("export APPROVED=1\n")
    execs: list[list[str]] = []

    def fake_execvp(file, args):
        execs.append([file, *list(args)[1:]])
        raise SystemExit(0)

    with (
        patch("ai_cli.main.os.execvp", side_effect=fake_execvp),
        patch(
            "ai_cli.main.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ),
    ):
        with pytest.raises(SystemExit):
            _exec_with_direnv(tmp_path, ["claude", "--name", "kg-1"])

    assert execs[0][0] == "direnv"
    assert "claude" in execs[0]


def test_given_no_envrc_when_exec_then_runs_command_directly(tmp_path):
    """With no .envrc there is nothing for direnv to load -- don't require it.

    Repos without an .envrc (a plain clone) must launch on a host where direnv
    is not installed at all.
    """
    execs: list[list[str]] = []

    def fake_execvp(file, args):
        execs.append([file, *list(args)[1:]])
        raise SystemExit(0)

    with patch("ai_cli.main.os.execvp", side_effect=fake_execvp):
        with pytest.raises(SystemExit):
            _exec_with_direnv(tmp_path, ["claude", "--name", "kg-1"])

    assert execs[0][0] == "claude"


def test_given_direnv_missing_when_envrc_present_then_falls_back_to_direct_exec(tmp_path):
    """direnv absent is not fatal: run the engine without the project env."""
    (tmp_path / ".envrc").write_text("export X=1\n")
    execs: list[list[str]] = []

    def fake_execvp(file, args):
        if file == "direnv":
            raise FileNotFoundError("no direnv")
        execs.append([file, *list(args)[1:]])
        raise SystemExit(0)

    with (
        patch("ai_cli.main.os.execvp", side_effect=fake_execvp),
        patch("ai_cli.main.subprocess.run", side_effect=FileNotFoundError("no direnv")),
    ):
        with pytest.raises(SystemExit):
            _exec_with_direnv(tmp_path, ["claude", "--name", "kg-1"])

    assert execs and execs[0][0] == "claude"


# --- end-to-end: the worktree lands in the launching repo ----------------------


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _make_repo(path: Path) -> Path:
    """A real repo cloned from a bare remote, so ``origin/main`` exists.

    ``create_worktree`` hard-fails when it cannot set a worktree branch's upstream to
    ``origin/main`` (AI-CLI-128), so a plain ``git init`` fixture is not a repo the
    launcher will accept.
    """
    remote = path.parent / f"{path.name}-origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
    )

    seed = path.parent / f"{path.name}-seed"
    seed.mkdir(parents=True)
    _git("init", "-b", "main", cwd=seed)
    _git("config", "user.email", "t@example.com", cwd=seed)
    _git("config", "user.name", "T", cwd=seed)
    (seed / "README.md").write_text("hi\n")
    _git("add", "-A", cwd=seed)
    _git("commit", "-m", "init", cwd=seed)
    _git("push", "-q", str(remote), "main", cwd=seed)

    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(path)], check=True, capture_output=True)
    _git("config", "user.email", "t@example.com", cwd=path)
    _git("config", "user.name", "T", cwd=path)
    return path


def test_given_named_ai_host_when_launched_in_repo_then_worktree_stays_in_that_repo(tmp_path, monkeypatch):
    """The end-to-end regression: launching in repo A must not touch repo B.

    Reproduces the reported failure -- ``ai c`` inside the knowledge-graph repo
    created ``kg-1`` under the *main* project's ``.worktrees/`` instead.
    """
    projects = tmp_path / "projects"
    target = _make_repo(projects / "myproject")
    other = _make_repo(projects / "mainproject")

    monkeypatch.chdir(target)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    # The exact condition that used to force remote mode.
    monkeypatch.setenv("AI_HOST", "my-linux-box")

    with (
        patch("ai_cli.main.os.execvp", side_effect=SystemExit(0)),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.config._get_main_project_name", return_value="mainproject"),
        patch("ai_cli.config._find_project_dir", return_value=other),
        patch("ai_cli.trust.ensure_workspace_trusted"),
    ):
        with pytest.raises(SystemExit):
            _do_session_launch(
                engine="c",
                name="1",
                resume=False,
                once=False,
                bare=True,
                notify=False,
                sandbox=False,
                no_worktree=False,
                remote=False,
                project="",
                is_remote=False,
                project_prefix_override="kg",
                extra_args=[],
                config={
                    "worktree": {"enabled": True},
                    "session": {"use_tmux": False},
                    "project": {"main_project": "mainproject"},
                },
            )

    assert (target / ".worktrees" / "kg-1").is_dir(), "worktree must be created in the launching repo"
    assert not (other / ".worktrees").exists(), "worktree must NOT be created in the main project"


def test_given_unreachable_remote_when_pull_fails_then_session_still_starts_with_gits_reason(
    tmp_path, monkeypatch, capsys
):
    """A failed sync must not block the launch, and must report git's own reason.

    The warning used to assert "(autostash pop conflict?)" for every failure,
    which pointed debugging at the wrong cause for the common ones -- no network,
    or no credentials for the remote -- and dropped the message naming the real
    one.  The worktree here has an ``origin`` that cannot be reached.
    """
    repo = _make_repo(tmp_path / "projects" / "myproject")
    # Point origin at a path that does not exist, *after* cloning — so origin/main still
    # exists locally (create_worktree requires it) but the fetch itself must fail.
    subprocess.run(
        ["git", "remote", "set-url", "origin", str(tmp_path / "nope.git")],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    monkeypatch.chdir(repo)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))

    with (
        patch("ai_cli.main.os.execvp", side_effect=SystemExit(0)),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.trust.ensure_workspace_trusted"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            _do_session_launch(
                engine="c",
                name="1",
                resume=False,
                once=False,
                bare=True,
                notify=False,
                sandbox=False,
                no_worktree=False,
                remote=False,
                project="",
                is_remote=False,
                project_prefix_override="kg",
                extra_args=[],
                config={"worktree": {"enabled": True}, "session": {"use_tmux": False}},
            )

    # The launch must proceed to the engine exec, not abort on the failed sync.
    assert exc_info.value.code == 0
    assert (repo / ".worktrees" / "kg-1").is_dir()

    err = capsys.readouterr().err
    assert "git pull --rebase failed" in err
    assert "autostash pop conflict" not in err, "must not assert a cause it did not verify"
    assert "Last git error:" in err, f"git's own reason must be surfaced; got: {err!r}"


def test_given_local_launch_on_named_host_when_named_then_session_has_no_remote_segment(tmp_path, monkeypatch):
    """A local session on a named host must be ``kg-1``, not ``c-r-kg-1``.

    The ``-r`` segment marks a session reached over SSH; applying it to a local
    launch mislabels it in the session map and in ``ai ls``.
    """
    repo = _make_repo(tmp_path / "projects" / "myproject")
    monkeypatch.chdir(repo)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.setenv("AI_HOST", "my-linux-box")
    monkeypatch.delenv("CLAUDE_CODE_TASK_LIST_ID", raising=False)

    with (
        patch("ai_cli.main.os.execvp", side_effect=SystemExit(0)),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.trust.ensure_workspace_trusted"),
    ):
        with pytest.raises(SystemExit):
            _do_session_launch(
                engine="c",
                name="",
                resume=False,
                once=False,
                bare=True,
                notify=False,
                sandbox=False,
                no_worktree=False,
                remote=False,
                project="",
                is_remote=False,
                project_prefix_override="kg",
                extra_args=[],
                config={"worktree": {"enabled": True}, "session": {"use_tmux": False}},
            )

    assert os.environ.get("CLAUDE_CODE_TASK_LIST_ID") == "kg-1"
    assert (repo / ".worktrees" / "kg-1").is_dir()


# --- worktree isolation is announced, not silent (AI-CLI-195 AC-7) --------------


def _launch_in(monkeypatch, tmp_path, *, engine="c", no_worktree=False, expected_exception=SystemExit):
    """Run one bare launch from the current directory, up to the engine exec."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.setenv("AI_HOST", "my-linux-box")

    with (
        patch("ai_cli.main.os.execvp", side_effect=SystemExit(0)),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.trust.ensure_workspace_trusted"),
    ):
        with pytest.raises(expected_exception) as exc_info:
            _do_session_launch(
                engine=engine,
                name="1",
                resume=False,
                once=False,
                bare=True,
                notify=False,
                sandbox=False,
                no_worktree=no_worktree,
                remote=False,
                project="",
                is_remote=False,
                project_prefix_override="kg",
                extra_args=[],
                config={"worktree": {"enabled": True}, "session": {"use_tmux": False}},
            )
    return exc_info.value


@pytest.mark.parametrize("engine", ["c", "g"])
def test_given_a_new_worktree_when_launched_then_its_creation_is_announced(tmp_path, monkeypatch, capsys, engine):
    """AC-7: creating a worktree per session is intended, but it must not be silent.

    The reported surprise was a ``.worktrees/<name>`` directory appearing with
    nothing in the output to say it would.
    """
    repo = _make_repo(tmp_path / "projects" / "myproject")
    monkeypatch.chdir(repo)

    _launch_in(monkeypatch, tmp_path, engine=engine)

    err = capsys.readouterr().err
    assert "Creating isolated worktree for this session" in err
    # The message must name WHERE, or it does not remove the surprise.
    assert str(repo / ".worktrees" / "kg-1") in err
    assert (repo / ".worktrees" / "kg-1").is_dir()


def test_given_a_launch_from_a_repo_root_when_announced_then_the_opt_out_is_named(tmp_path, monkeypatch, capsys):
    """An announcement the reader cannot act on still leaves them stuck."""
    repo = _make_repo(tmp_path / "projects" / "myproject")
    monkeypatch.chdir(repo)

    _launch_in(monkeypatch, tmp_path)

    assert "--no-worktree" in capsys.readouterr().err


def test_given_an_existing_worktree_when_announced_then_no_opt_out_hint_is_repeated(tmp_path, monkeypatch, capsys):
    """The opt-out hint only applies to creation; reuse has nothing to opt out of."""
    repo = _make_repo(tmp_path / "projects" / "myproject")
    monkeypatch.chdir(repo)
    _launch_in(monkeypatch, tmp_path)
    capsys.readouterr()

    monkeypatch.chdir(repo / ".worktrees" / "kg-1")
    _launch_in(monkeypatch, tmp_path)

    assert "--no-worktree" not in capsys.readouterr().err


def test_given_worktree_isolation_disabled_when_launched_then_nothing_is_announced(tmp_path, monkeypatch, capsys):
    """Negative control: the announcement must be tied to actually doing it.

    Without this, a message printed unconditionally would satisfy the assertions
    above while telling the reader something untrue.
    """
    repo = _make_repo(tmp_path / "projects" / "myproject")
    monkeypatch.chdir(repo)

    _launch_in(monkeypatch, tmp_path, no_worktree=True)

    err = capsys.readouterr().err
    assert "isolated worktree" not in err
    assert not (repo / ".worktrees").exists()


def test_given_worktree_creation_fails_when_launched_then_it_refuses_to_use_the_repository_root(
    tmp_path, monkeypatch, capsys
):
    """A RuntimeError from create_worktree() must become a clean refusal, not a raw traceback."""
    repo = _make_repo(tmp_path / "projects" / "myproject")
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        "ai_cli.session.create_worktree",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("git worktree add failed for slot. First attempt: branch exists. Retry: permissions denied")
        ),
    )

    _launch_in(monkeypatch, tmp_path)
    err = capsys.readouterr().err
    assert "refusing to launch in the repository root" in err
    assert "branch exists" in err
    assert "permissions denied" in err


@pytest.mark.parametrize("engine", ["c", "g"])
def test_given_an_existing_worktree_when_launched_then_its_reuse_is_announced(tmp_path, monkeypatch, capsys, engine):
    """Both engines report reuse after create_worktree makes that decision."""
    repo = _make_repo(tmp_path / "projects" / "myproject")
    monkeypatch.chdir(repo)
    _launch_in(monkeypatch, tmp_path, engine=engine)
    capsys.readouterr()

    monkeypatch.chdir(repo / ".worktrees" / "kg-1")
    _launch_in(monkeypatch, tmp_path, engine=engine)

    err = capsys.readouterr().err
    assert f"Using existing worktree: {repo / '.worktrees' / 'kg-1'}" in err
