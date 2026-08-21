import contextlib
import io
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import ai_cli.config as _config_module
import ai_cli.session as _session_module
from ai_cli.git_repair import _GIT_TARGETING_VARS

_TEST_TMUX_PREFIX = "pytest-leak-guard-"
_PROTECTED_TEST_BINARIES = frozenset({"tmux", "claude", "gemini", "direnv"})

# Short directory name for the relocated Windows temp root -- see
# _windows_temproot for why the length itself is the point.
_WIN_TEMPROOT_NAME = "aipt"


def _windows_temproot() -> Path:
    """Return a deliberately SHORT temp root on the same drive as the default one.

    Windows still enforces MAX_PATH (260) unless ``LongPathsEnabled`` is set,
    which needs admin and is therefore not available here. Two things make the
    suite's paths unusually long: pytest nests
    ``<temp>/pytest-of-<user>/pytest-N/<test-name>/``, and several tests build a
    Claude Code project directory whose *name* is an entire absolute path with
    every non-alphanumeric byte replaced by ``-`` (see
    ``ai_cli.main._cc_project_dir``, which must keep matching Claude Code's own
    slugify and so cannot be shortened).

    Because that slug embeds the temp path, shortening the root shortens the
    path *twice over* -- once in the real path and again inside the slug. The
    default root here is 40 characters; this one is ~7, which measured a
    reduction from 270 characters (winerror 3) to comfortably under the limit.

    The drive is taken from the default temp dir rather than hardcoded so this
    does not assume ``C:``.
    """
    return Path(Path(tempfile.gettempdir()).anchor) / _WIN_TEMPROOT_NAME


def _repair_inherited_acl(target: Path, recurse: bool = False) -> None:
    """Re-enable ACL inheritance on ``target`` so Git for Windows can write under it.

    pytest creates ``pytest-of-<user>`` with ``mode=0o700``. CPython implements
    that on Windows as a *protected* DACL: inheritance disabled, with ACEs for
    SYSTEM, Administrators and ``OWNER RIGHTS`` only and NO explicit ACE for the
    current user. Directories git then creates underneath inherit no usable ACE,
    so Git for Windows' own access check refuses them -- ``git init`` and
    ``git clone`` both fail rc=128 with "could not create leading directories
    ... Permission denied". On a host whose domain trust is broken (seen here:
    ``icacls`` prints "The trust relationship between this workstation and the
    primary domain failed") ``OWNER RIGHTS`` does not rescue it.

    Measured remedies, both rc=0 and neither needing admin: ``/inheritance:e``
    and ``/reset``. ``chmod(0o777)`` from Python does NOT work -- on Windows it
    only toggles FILE_ATTRIBUTE_READONLY and cannot rewrite a DACL -- and a
    ``/grant`` of the user SID on a leaf directory alone also left the clone
    failing. Order matters: the repair has to land before any repo is created
    inside the directory, or git has already failed.
    """
    argv = ["icacls", str(target), "/inheritance:e", "/C", "/Q"]
    if recurse:
        argv.insert(3, "/T")
    # Best effort: a failure here leaves the pre-existing breakage in place and
    # the affected tests fail loudly, which is the honest outcome. Never fatal --
    # this runs for every session, and icacls may be absent on a stripped host.
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False)


def pytest_configure(config):
    """Relocate the Windows temp root before pytest first resolves ``basetemp``.

    This is the MAX_PATH half of the Windows fix; the DACL half is handled
    lazily in :func:`_reject_real_agent_processes`.

    ``PYTEST_DEBUG_TEMPROOT`` is pytest's own hook for the root and is read
    lazily when the base temp dir is first needed, so setting it here is early
    enough. It is only set when the operator has not chosen a root themselves.

    Patching pytest to stop requesting ``mode=0o700`` was tried and rejected:
    the mode is hardcoded at five separate call sites in ``_pytest.tmpdir``, and
    ``getbasetemp`` additionally *validates* that the root is not group/world
    accessible (``if (rootdir_stat.st_mode & 0o077) != 0``), so forcing it wide
    fights pytest's own security check.
    """
    if sys.platform != "win32" or os.environ.get("PYTEST_DEBUG_TEMPROOT"):
        return
    root = _windows_temproot()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return  # fall back to the default root; the ACL repair still applies
    os.environ["PYTEST_DEBUG_TEMPROOT"] = str(root)


@pytest.fixture(scope="session", autouse=True)
def _repair_windows_temp_tree(tmp_path_factory):
    """Repair the session's temp roots once, before any test uses them.

    Covers ``pytest-of-<user>`` (the protected parent) and this session's base
    dir. Per-test directories need their own repair -- pytest applies
    ``mode=0o700`` to each of those too, so they do not exist yet here. That is
    handled per test in :func:`_make_tmp_path_deletable`.
    """
    if sys.platform != "win32":
        return
    base = tmp_path_factory.getbasetemp()
    _repair_inherited_acl(base.parent)
    _repair_inherited_acl(base)


def _command_program(command):
    """Return the executable name from a subprocess argument, if present."""
    if isinstance(command, (list, tuple)):
        command = command[0] if command else None
    elif isinstance(command, str):
        command = shlex.split(command)[0] if command else None
    if not command:
        return None
    # PTH119 is suppressed below deliberately: this extracts a program name from
    # an arbitrary subprocess argument, not from a filesystem path, and the two
    # are not interchangeable here. os.fspath may yield bytes, which Path()
    # rejects with TypeError, and this helper backstops every subprocess call in
    # the suite -- raising there would be worse than the lint finding.
    # os.path.basename also returns "" for a trailing-slash argument, where
    # Path().name returns the parent directory name instead.
    return os.path.basename(os.fspath(command))  # noqa: PTH119


def _reject_real_agent_process(command, allowed_binaries=frozenset()):
    """Fail loudly when a test reaches a real agent or tmux process boundary."""
    program = _command_program(command)
    if program in _PROTECTED_TEST_BINARIES - allowed_binaries:
        raise RuntimeError(
            f"test attempted to spawn a real `{program}` process — "
            "mock subprocess.run, subprocess.Popen, or os.execvp explicitly in this test"
        )


def _cleanup_test_tmux_sessions(run):
    """Kill only sessions created with the test-only leak-guard prefix."""
    try:
        sessions = run(["tmux", "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True)
    except OSError:
        return
    if sessions.returncode != 0:
        return
    for session_name in sessions.stdout.splitlines():
        if session_name.startswith(_TEST_TMUX_PREFIX):
            try:
                run(["tmux", "kill-session", "-t", session_name], capture_output=True)
            except OSError:
                return


@pytest.fixture(autouse=True)
def _reject_real_agent_processes(request):
    """Prevent test launches from creating live tmux/agent processes (AI-CLI-117).

    Session-launch tests that patch ``os.execvp`` must also patch ``subprocess.run``
    when execution can reach the detached tmux creation step.  These outer patches
    allow explicit test-level ``patch(...)`` calls to replace them, while turning a
    missed mock into a clear failure instead of an orphaned live session.
    """
    # subprocess.run and subprocess.Popen both need the real_tmux allowance —
    # the isolated-socket integration tests use `run` for the tmux_server
    # fixture's own teardown cleanup (`tmux -S <sock> kill-server`, which runs
    # after each test's own tighter-scoped mocks have already exited) and
    # libtmux itself uses `Popen` internally for every tmux command it issues
    # against that same isolated socket. execvp is deliberately excluded even
    # for real_tmux tests: every test in that suite already self-mocks
    # os.execvp (a real execvp("tmux", ...) call never returns on success, so
    # letting one slip through would silently replace the pytest worker
    # process instead of failing loudly), and libtmux never calls it.
    allowed_binaries = frozenset({"tmux"}) if request.node.get_closest_marker("real_tmux") else frozenset()
    real_run = subprocess.run
    real_popen = subprocess.Popen
    real_execvp = os.execvp
    repaired: list[bool] = []

    def repair_temp_acl_for_git(command) -> None:
        """On Windows, fix this test's temp-dir DACL the first time it runs git.

        Deliberately lazy and keyed on git, because the repair costs an
        ``icacls`` process and only git needs it: measured unconditionally per
        test it roughly doubled a fast test file's runtime (26s -> 56s), while
        the tests that actually shell out to git are a small minority.

        Why it is needed at all: pytest creates every temp directory with
        ``mode=0o700``, which CPython implements on Windows as a protected DACL
        naming no ACE for the current user, so Git for Windows cannot create
        anything underneath it (see :func:`_repair_inherited_acl`). Repairing the
        session roots is not sufficient -- each per-test directory is protected
        in its own right.
        """
        if sys.platform != "win32" or repaired or _command_program(command) != "git":
            return
        repaired.append(True)
        if "tmp_path" in request.fixturenames:
            _repair_inherited_acl(request.getfixturevalue("tmp_path"), recurse=True)

    def guarded_run(*args, **kwargs):
        command = args[0] if args else kwargs.get("args")
        _reject_real_agent_process(command, allowed_binaries)
        repair_temp_acl_for_git(command)
        return real_run(*args, **kwargs)

    def guarded_popen(*args, **kwargs):
        command = args[0] if args else kwargs.get("args")
        _reject_real_agent_process(command, allowed_binaries)
        repair_temp_acl_for_git(command)
        return real_popen(*args, **kwargs)

    def guarded_execvp(*args, **kwargs):
        command = args[0] if args else kwargs.get("file")
        _reject_real_agent_process(command)
        return real_execvp(*args, **kwargs)

    with (
        patch("subprocess.run", side_effect=guarded_run),
        patch("subprocess.Popen", side_effect=guarded_popen),
        patch("os.execvp", side_effect=guarded_execvp),
    ):
        yield


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_tmux_sessions_after_suite():
    """Backstop cleanup for test-only tmux names if an explicit mock is bypassed."""
    yield
    _cleanup_test_tmux_sessions(subprocess.run)


@pytest.fixture(autouse=True)
def _isolate_xdg_state_home(monkeypatch, tmp_path_factory):
    """Hermetic XDG state dir — never touch the real ~/.local/state/ai-cli-utils (AI-CLI-121).

    `config.get_xdg_state_home()`/`process_hygiene._get_state_dir()` both fall back to the
    real ``~/.local/state`` when unset. Several git tests create ephemeral temp git
    repos and expect a clean, uncontended state directory; without isolation they race against
    whatever else (other test runs, live `ai` CLI processes) is concurrently reading/writing the
    real one, producing exactly the `git commit`/`git init` failures this task fixed by proving
    the isolation empirically (`XDG_STATE_HOME=$(mktemp -d)` made the full suite deterministic).
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("xdg_state_home")))


@pytest.fixture(autouse=True)
def _strip_git_targeting_env_vars(monkeypatch):
    """Never inherit GIT_DIR/GIT_WORK_TREE/etc. into test subprocesses (AI-CLI-121).

    `git_repair._git_env()` already strips these before every git subprocess the app's own
    code issues, specifically because they redirect git's repo/worktree targeting rather than
    honoring `-C`/`cwd` — see that module's docstring for the full AI-CLI-99 rationale. Tests
    that shell out to `git` directly in their own ephemeral fixture repos (`test_sync.py`,
    `test_trust.py`, `test_setup.py`, `test_git_repair.py`) never got the
    same protection. Confirmed live: invoking the full suite through this repo's own pre-commit
    pre-push hook — which manipulates the index/work-tree via these exact variables while
    staging unstaged changes for the hook run — reproduced 17 failures + 9 errors that were
    100% absent running the identical suite/command directly (bypassing pre-commit). Stripping
    them for the whole test session removes the leak at its source, matching the app's own
    established pattern instead of requiring every git-shelling test to defend itself.
    """
    for var in _GIT_TARGETING_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _no_real_nats_connections(request, monkeypatch):
    """Never let a test open a real NATS connection (AI-CLI-121 class).

    ``post_handoff`` and the fleet event publishers are fire-and-forget: they build a
    ``NATSClient`` and ``asyncio.run(...)`` a publish, wrapped in ``except Exception:
    pass``.  That swallows a *failure* but cannot shorten a *hang*, so on any machine with
    no NATS server six handoff tests blocked until the suite's timeout killed them —
    exercising the network instead of the file-writing behaviour they assert.

    The block is applied to ``nats.connect`` -- the actual network boundary -- rather than
    to ``NATSClient.connect``. Overriding the latter would also neutralise the many tests
    that legitimately drive ``connect()`` with their own ``nats.connect`` mock (retry
    behaviour, JetStream handle setup); those tests patch this same name inside their own
    ``with`` block, which nests inside this fixture and therefore still wins.

    Tests that genuinely want a live server opt out with the ``real_nats`` marker.
    """
    if request.node.get_closest_marker("real_nats"):
        yield
        return

    import nats

    from ai_cli.messaging import NoServersError

    async def _refuse(*args, **kwargs):
        raise NoServersError("NATS connections are blocked in tests")

    monkeypatch.setattr(nats, "connect", _refuse)
    yield


@pytest.fixture(autouse=True)
def _projects_dir_contains_the_checkout(monkeypatch):
    """Make ``projects_dir`` resolve to this checkout's own parent directory.

    ``is_current_project_resolved()`` reads the real filesystem: it answers True
    when cwd is physically under ``projects_dir``.  Session-launch tests that
    patch ``get_project_prefix`` intend to bypass project resolution entirely,
    but that later-added guard runs before the prefix is ever used, so they only
    passed on a machine where the checkout happened to sit at
    ``~/projects/<name>`` — the default. On any other layout (a work machine
    where repos live on a mounted volume, CI checking out to ``/build``, a
    plain ``git clone`` into ``~/src``) the guard short-circuits the launch with
    "no project resolved" and 22 launch tests fail for a reason that has nothing
    to do with what they assert.

    Pointing ``projects_dir`` at the checkout's parent restores that implicit
    assumption for every layout, rather than requiring each launch test to
    defend itself — the same "fix it once at the process boundary" approach the
    ``GIT_*`` scrub above takes. Tests that specifically exercise resolution
    (both the True and False cases) patch ``_get_projects_dir`` or
    ``is_current_project_resolved`` themselves, and those inner patches still
    win.
    """
    checkout_parent = Path(__file__).resolve().parent.parent.parent
    monkeypatch.setattr(_config_module, "_get_projects_dir", lambda: checkout_parent)
    monkeypatch.setattr(_session_module, "_get_projects_dir", lambda: checkout_parent)


@pytest.fixture(autouse=True)
def _reset_registry_cache():
    """Reset the project registry cache before each test."""
    _config_module._registry_cache = None
    yield
    _config_module._registry_cache = None


@pytest.fixture(autouse=True)
def _isolate_quota_state(request, tmp_path_factory, monkeypatch):
    """Hermetic quota/statusline tests — never touch real user quota state (AI-CLI-97).

    Four independent breaches this closes:

    1. **Real quota DB.** ``_get_quota_db_path()`` falls back to the real
       ``~/.local/state/ai-cli/quota.db`` when no override is set, so an unisolated
       test reads/writes real quota history (which carries live ``Fable`` model data).
       Redirect it to a per-test tmp file.
    2. **Real background scrape subprocess.** ``quota_statusline_part()`` fires
       ``_launch_background_scrape`` / ``_maybe_trigger_background_scrape``, which
       ``subprocess.Popen(["ai","quota","scrape"], start_new_session=True)`` — a real
       detached process that scrapes live usage and writes the real DB + NATS KV.
       Under xdist these race across workers and inject real ``Fable`` data into a
       test expecting isolated state (the intermittent ``F 🤖`` vs ``S 🤖`` flake).
       No-op both spawners.
    3. **Real reset-anchor file (AI-CLI-180).** ``_get_reset_anchor_path()`` was never
       redirected, so any test reaching ``record_quota_snapshot(reset_at=...)`` wrote the
       real ``~/.local/state/ai-cli/quota-reset-anchor.txt``. That file *defines* the quota
       week boundary, and ``_get_current_week_start()`` re-reads it on every call — so one
       xdist worker's write moved another worker's boundary mid-test. The row a test just
       recorded then carried a different ``week_start`` than the one the statusline queried,
       no rows came back, and the render fell through to its ``📊 -`` placeholder. Redirect
       it to a per-test tmp path so the anchor resolves to the deterministic built-in default.
    4. **Real scrape lock file.** ``_launch_background_scrape()`` skips spawning when
       its process-global lock exists. Redirect it to a per-test path so a live
       statusline or another xdist worker cannot make a test's launch assertion
       depend on shared user state.

    Tests that exercise the scrape spawners themselves (they mock ``subprocess.Popen``
    locally and assert the real functions' behavior) opt out of the no-op via the
    ``real_quota_scrape`` marker. ``set_db_path`` tests likewise re-set the path, and the
    anchor-persistence tests patch ``_get_reset_anchor_path`` with their own path — an inner
    patch that still wins over this one.
    """
    import ai_cli.quota_db as _qdb

    tmp_db = tmp_path_factory.mktemp("quota_state") / "quota.db"
    _qdb.set_db_path(tmp_db)
    tmp_anchor = tmp_path_factory.mktemp("quota_anchor") / "quota-reset-anchor.txt"
    monkeypatch.setattr(_qdb, "_get_reset_anchor_path", lambda: tmp_anchor)
    # AIH-164 T-06: redirect the Fable backoff-state file to a per-test tmp path so tests never
    # read/write the real ~/.local/state/ai-cli/fable-scrape-backoff.json (which would leak
    # scrape-scheduling state across tests / into the real user state).
    import ai_cli.quota as _q

    _orig_scrape_lock_path = _q._SCRAPE_LOCK_PATH
    _q._SCRAPE_LOCK_PATH = tmp_path_factory.mktemp("scrape_lock") / "quota-scrape.lock"
    _orig_fable_state = _q._FABLE_BACKOFF_STATE
    _q._FABLE_BACKOFF_STATE = tmp_path_factory.mktemp("fable_state") / "fable-scrape-backoff.json"
    try:
        if request.node.get_closest_marker("real_quota_scrape"):
            yield  # test drives the real scrape functions (with its own Popen mock)
        else:
            with (
                patch("ai_cli.quota._launch_background_scrape"),
                patch("ai_cli.quota._maybe_trigger_background_scrape"),
            ):
                yield
    finally:
        _qdb.set_db_path(None)
        _q._SCRAPE_LOCK_PATH = _orig_scrape_lock_path
        _q._FABLE_BACKOFF_STATE = _orig_fable_state


@pytest.fixture(autouse=True)
def _suppress_auto_update():
    """Suppress _auto_update_if_stale for all tests.

    Without this, tests that call cli() trigger a real subprocess.run(["ai", ...])
    when the git HEAD doesn't match the last-update stamp file, causing
    FileNotFoundError in environments where 'ai' isn't on PATH (e.g. pre-push hook).
    """
    with patch("ai_cli.main._auto_update_if_stale"):
        yield


def _run_cli_with_args(argv, config_override=None):
    """Helper: invoke cli() with argv, capturing execvp calls.

    os.execvp replaces the process in real usage, so we raise SystemExit
    to simulate that — otherwise execution falls through to later exec calls.
    """
    config = config_override or {}
    with (
        patch("sys.argv", argv),
        patch("ai_cli.config.load_config", return_value=config),
        patch("ai_cli.session.is_current_project_resolved", return_value=True),
        patch("ai_cli.session.get_project_prefix", return_value="test-project"),
        patch("os.execvp", side_effect=SystemExit(0)) as mock_exec,
        patch("ai_cli.main.trigger_background_update"),
        patch("ai_cli.main._auto_update_if_stale"),
    ):
        from ai_cli.main import cli

        try:
            cli()
        except SystemExit:
            pass
        return mock_exec


def run_cli(argv, config=None, env=None):
    """Invoke cli() with argv. Returns (exit_code, stdout, stderr)."""
    from ai_cli.main import cli

    _config = config or {}
    _env = env or {}
    with (
        patch("sys.argv", argv),
        patch("ai_cli.config.load_config", return_value=_config),
        patch("ai_cli.main.trigger_background_update"),
        patch("ai_cli.main._auto_update_if_stale"),
        patch.dict(os.environ, _env),
    ):
        stdout_cap = io.StringIO()
        stderr_cap = io.StringIO()
        try:
            with patch("sys.stdout", stdout_cap), patch("sys.stderr", stderr_cap):
                cli()
            exit_code = 0
        except SystemExit as e:
            exit_code = e.code if isinstance(e.code, int) else 0
        return exit_code, stdout_cap.getvalue(), stderr_cap.getvalue()


def make_subprocess_result(returncode=0, stdout="", stderr=""):
    """Factory for subprocess.run/check_output return value."""
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def make_iterm2_config(
    palette=None,
    enabled=True,
    color_enabled=True,
    collision_avoidance=True,
    projects=None,
    sessions=None,
    defaults=None,
):
    """Factory for iterm2 config dicts.

    ``projects``: dict mapping project_name → {tab_color, icon_color, ...}
    ``sessions``: dict mapping ai_name → {tab_color, icon_color, ...}
    ``defaults``: dict with default settings applied to all sessions
    """
    palette = palette or {"red": "#e74c3c", "blue": "#1e88e5", "green": "#2ecc71"}
    cfg = {
        "iterm2": {
            "enabled": enabled,
            "color": {"enabled": color_enabled, "collision_avoidance": collision_avoidance},
            "palette": palette,
        }
    }
    if defaults:
        cfg["iterm2"]["defaults"] = defaults
    if projects:
        cfg["iterm2"]["projects"] = projects
    if sessions:
        cfg["iterm2"]["sessions"] = sessions
    return cfg


def _make_list_panes_output(*entries):
    """Build mock tmux list-panes -a output.

    Each entry: (session_name, last_attached, pane_cmd) or
                (session_name, last_attached, pane_cmd, attached_count).
    attached_count defaults to 0 (no clients attached).
    """
    rows = []
    for entry in entries:
        if len(entry) == 3:
            name, last, cmd = entry
            attached = 0
        else:
            name, last, cmd, attached = entry
        rows.append(f"{name}|{last}|{attached}|{cmd}")
    lines = "\n".join(rows)
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = lines
    return mock


@pytest.fixture(autouse=True)
def _make_tmp_path_deletable(tmp_path: Path):
    """Clear read-only bits under ``tmp_path`` so pytest can actually delete it.

    Tests here build real git repos in ``tmp_path``. Git writes loose objects
    mode 0444, and on Windows ``shutil.rmtree`` cannot unlink a read-only file
    (``DeleteFile`` returns ACCESS_DENIED). pytest's tmp_path cleanup passes no
    ``onexc`` handler, so the failure is swallowed and the directory survives as
    a live git repo named after the test function -- which any editor that adopts
    nearby repositories then lists as a project.

    Eighty such repos accumulated on bms-windows-sem-kg before the mechanism was
    traced (CORE-196; same defect as ai-harness AIH-612).

    Deliberately unconditional rather than a ``sys.platform`` branch -- POSIX
    ``rmtree`` only needs write on the parent directory, so this is a no-op cost
    on Linux and macOS instead of a platform special case (AIH-824).

    The Windows DACL problem that used to need a repair here is prevented
    upstream instead -- see :func:`_stop_pytest_protecting_temp_dirs`, which stops
    pytest requesting ``mode=0o700`` at all. Repairing per test also worked but
    cost an ``icacls`` process per test, roughly doubling a fast file's runtime.
    """
    yield
    if not tmp_path.exists():
        return
    for path in tmp_path.rglob("*"):
        try:
            if path.is_file() or path.is_dir():
                path.chmod(0o700)
        except OSError:
            continue
