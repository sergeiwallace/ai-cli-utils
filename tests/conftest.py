import contextlib
import http.server
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import ai_cli.config as _config_module
import ai_cli.session as _session_module
import ai_cli.trust as _trust_module
from ai_cli.git_repair import _GIT_TARGETING_VARS
from ai_cli.main import _REMOTE_SHELL_PROBE_CMD

_TEST_TMUX_PREFIX = "pytest-leak-guard-"

# Anything that puts a banner on the operator's screen or scripts their live
# terminal. Guarded at the process boundary rather than per call site because per-call
# discipline has now failed twice here, in two different subsystems: AI-CLI-jk7v (one
# test of seven siblings patched the log path but not ``subprocess.run``, so EVERY
# pytest run in this repo raised a real desktop notification, for weeks) and
# AI-CLI-tevy (tests writing the real iTerm2 profile directory).
#
# ``osascript`` covers both, and on purpose: ``notifications.py`` uses it for the
# banner and ``iterm2.py:313`` uses it to script the running terminal, which is the
# same kind of escape. ``notify-send`` is the non-macOS branch of the same function,
# so it is reached on Linux AND Windows and is guarded everywhere rather than only
# where it happens to exist.
_DESKTOP_ESCAPE_BINARIES = frozenset({"osascript", "notify-send"})

_PROTECTED_TEST_BINARIES = frozenset({"tmux", "claude", "gemini", "direnv", "ssh", "mosh"}) | _DESKTOP_ESCAPE_BINARIES

# Programs that RUN a command string handed to them, which is what makes a guard on the
# program name alone insufficient (AI-CLI-013v). Measured: a test drove
# ``os.execvp("zsh", ["zsh", "-c", "<real ssh command>; ..."])``, the program was ``zsh``
# and so not protected, ``ssh`` was only a substring of the payload the guard never read,
# and the exec REPLACED the pytest process -- the run ended mid-collection with exit 0, no
# summary and no results, while a real outbound SSH connection was attempted.
#
# Banning the shells themselves was rejected: many tests here legitimately run one (the
# generated engine script, the supervisor, the lease-redirection probes), and a blanket
# ban is the kind of rule that gets switched off or worked around. So a shell stays
# allowed and its command string is inspected instead.
#
# Both the bare and ``.exe`` spellings, because ``_command_program`` returns a basename and
# Windows argv carries the suffix.
_SHELL_PROGRAMS = frozenset(
    {
        "sh",
        "bash",
        "zsh",
        "dash",
        "ash",
        "ksh",
        "mksh",
        "fish",
        "csh",
        "tcsh",
        "cmd",
        "cmd.exe",
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
    }
)

# A POSIX shell takes its command STRING after an option cluster ending in ``c`` -- ``-c``,
# but also ``-lc`` and ``-ic``, which production uses (``main.py`` builds a mosh remote
# command as ``<shell> -l -c <cmd>``). Anything else after the options is a script PATH,
# which is not a command string and is deliberately not scanned: a path is what the
# supervisor and engine-script tests pass.
_POSIX_SHELL_COMMAND_FLAG = re.compile(r"^-[A-Za-z]*c$")

# The Windows equivalents, compared lowercased. ``-encodedcommand`` takes base64 and so
# cannot be inspected; it is listed anyway so the payload is at least consumed as a
# command rather than mistaken for a script path.
_WINDOWS_SHELL_COMMAND_FLAGS = frozenset({"/c", "/k", "-command", "-encodedcommand"})

# Words inside a shell command string, split on the shell metacharacters that can abut a
# program name. A regex rather than ``shlex.split`` on purpose: shlex leaves ``(ssh`` and
# ``&&ssh`` as single tokens and raises on an unbalanced quote, and a guard that stops
# seeing a hazard because the payload quoting is odd is not a guard.
_SHELL_PAYLOAD_WORD = re.compile(r"[^\s;&|()<>'\"`$={}]+")

# Shell-laundered spawn attempts, recorded as well as refused for the reason the desktop
# escapes are: refusing alone does not report. A caller that swallows the exception --
# ``cli()`` is driven under ``except SystemExit`` and ``except Exception`` in several
# tests -- would otherwise be silently protected and never told.
_protected_spawn_attempts: list[str] = []

# The Windows branch of the same function, which the binary interception above cannot
# see at all (AI-CLI-e9nm). ``notifications._send_os_notification`` raises a toast there
# by calling ``plyer.notification.notify`` IN-PROCESS -- there is no subprocess to
# intercept, so a test that reaches that branch on Windows puts a real toast on the
# operator's screen while the guard reports nothing. Recorded under this name rather
# than a binary name because it is not a binary.
_DESKTOP_ESCAPE_IMPORT = "plyer"

# Distinguishes "``plyer`` was absent from ``sys.modules``" from "it was present and
# bound to ``None``", which is a real state a failed import can leave behind. ``None``
# would restore as a module that cannot be imported from.
_MISSING = object()

# The two registry keys that hold a persisted ``Path``, lowercased for comparison. Exactly
# the pair ``direnv_setup.refresh_windows_path`` reads and merges into this process.
_WINDOWS_ENVIRONMENT_SUBKEYS = frozenset(
    {
        r"system\currentcontrolset\control\session manager\environment",
        "environment",
    }
)

# Attempts are RECORDED as well as refused, because refusing alone does not report.
# ``notifications._send_os_notification`` wraps its spawn in ``except Exception`` and
# returns a failed result, so the guard's RuntimeError never reaches the test that
# caused it. Prevention without a report is how this class survived for weeks: the
# notification was visible on screen and invisible in the run.
_desktop_escape_attempts: list[str] = []

# Resolved at IMPORT time, before any fixture has redirected HOME, so the guard below
# always names the operator's own directory rather than a redirected one. Re-reading
# ``Path.home()`` from inside a fixture would return the redirect and guard nothing.
_REAL_HOME = Path.home()

# iTerm2 is macOS-only, but ``icon_generator._dynamic_profile_dir`` and
# ``layout._dynamic_profile_dir`` both build this path from ``Path.home()`` with no
# platform branch, so an unredirected test creates a stray ``~/Library/...`` tree on
# Windows and Linux too. Guarding it everywhere is therefore correct rather than a
# macOS special case.
_REAL_ITERM2_PROFILE_DIR = _REAL_HOME / "Library" / "Application Support" / "iTerm2" / "DynamicProfiles"

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


def _tmux_argv_is_read_only(command) -> bool:
    """True for a tmux invocation that cannot create a session, server or window.

    The guard below matches on program NAME, which cannot tell `tmux new-session`
    from `tmux -V`. That coarseness is fine until production code legitimately
    needs to ASK tmux something during a launch: the launcher now reports the
    client and the running server's version so an operator can see which tmux a
    session is under, and neither query creates anything at all -- `-V` never
    contacts a server, and `display-message` fails cleanly when none is running.
    Rejecting those made a read-only diagnostic look like the hazard the guard
    exists for, which is a live session leaking out of a test.

    Deliberately an allowlist of exact subcommands, not a denylist: an unknown
    tmux subcommand stays rejected, so this cannot silently widen.
    """
    if isinstance(command, str):
        try:
            argv = shlex.split(command)
        except ValueError:
            return False
    elif isinstance(command, (list, tuple)):
        argv = [str(part) for part in command]
    else:
        return False
    if len(argv) < 2:
        return False
    return argv[1] in {"-V", "display-message"}


def filesystem_is_case_insensitive(path: Path) -> bool:
    """Does ``path``'s filesystem resolve two spellings to one directory?

    Answered by asking the filesystem, never by guessing from ``sys.platform``: a
    case-insensitive volume can be mounted on Linux and a case-sensitive one on
    macOS, so a platform guess would be wrong in both directions. The probe
    creates one directory and removes it, so it leaves nothing behind.
    """
    probe = path / "aicli-case-probe"
    try:
        probe.mkdir()
    except OSError:
        return False
    try:
        return (path / "AICLI-CASE-PROBE").exists()
    finally:
        with contextlib.suppress(OSError):
            probe.rmdir()


def _case_insensitive_filesystem_impl(path: Path):
    """Fixture body, exposed so its skip contract can be tested directly."""
    if not filesystem_is_case_insensitive(path):
        pytest.skip(f"filesystem at {path} is case-sensitive, so it has no case-alias paths to test")
    yield path


@pytest.fixture
def case_insensitive_filesystem(tmp_path: Path):
    """Skip at SETUP unless this filesystem aliases case, so nothing gets built.

    A case-alias test used to create a worktree and *then* discover it could not
    run, throwing the checkout away — seconds of I/O on a throttled filesystem,
    every run, on every case-sensitive host (AI-CLI-bug-tests-skip-capability-probe-bfqy).
    Deciding at fixture setup means the test body is never entered.
    """
    yield from _case_insensitive_filesystem_impl(tmp_path)


class _AlwaysUnauthorized(http.server.BaseHTTPRequestHandler):
    """Smallest remote that makes git ask for a credential: answer every request 401."""

    def do_GET(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="git"')
        self.end_headers()

    def log_message(self, *args):
        pass  # keep the captured test output readable


@pytest.fixture
def remote_demanding_credentials():
    """Yield the URL of a loopback remote that answers every request with 401.

    This is the real protocol boundary for the credential-prompt containment:
    git reaches its credential step for real, so a test driven through here
    cannot pass by stubbing the behaviour under test. Shared because every
    module that runs unattended git subprocesses against a remote needs it.
    """
    server = http.server.HTTPServer(("127.0.0.1", 0), _AlwaysUnauthorized)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/repo.git"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


#: Prefix on every skip reason that means "tmux itself could not be used here", so the
#: session-level check below can recognise one without pattern-matching English.
#:
#: A plain substring search for "tmux" is NOT good enough, and that is the whole reason
#: this marker exists. Several deliberate skips mention tmux while saying nothing about
#: whether it is installed -- "real tmux server behavior unverified under MSYS2 CI", which
#: fires on Windows where tmux IS provisioned, and "the real-tmux tests need POSIX process
#: semantics, not just a tmux binary". Both would match a naive search and neither is a
#: provisioning problem, so a naive check would fail the Windows job on every run.
TMUX_UNUSABLE_SKIP = "tmux unusable"

#: What to do about it, shared so every tmux-provisioning failure says the same thing.
#: tmux is a HARD requirement of this suite, not an optional one (AI-CLI-qzf2): the tests
#: that drive a real tmux server fail rather than skip without it, and every CI platform
#: installs it explicitly.
TMUX_REQUIRED_REMEDY = (
    "tmux is a hard requirement of this test suite, not an optional one: install tmux and "
    "re-run. Every CI platform provisions it explicitly -- see .github/workflows/ci.yml."
)


def tmux_unusable_skip_reason(detail: str) -> str:
    """Format ``detail`` as a skip reason the session-level tmux check recognises."""
    return f"{TMUX_UNUSABLE_SKIP}: {detail}"


def is_tmux_unusable_skip(reason: str) -> bool:
    """Was this skip caused by tmux being unusable, rather than merely about tmux?"""
    return TMUX_UNUSABLE_SKIP in reason


def tmux_runnable() -> tuple[bool, str]:
    """Can ``tmux`` actually be executed here? Returns ``(runnable, reason)``.

    Presence on ``PATH`` is not the same question. A ``tmux`` that resolves but
    cannot start — an extracted bundle whose shared libraries are not on the
    loader path, a binary built against a different libc — makes every real-tmux
    test fail on an empty session list, which reads as a defect in the launch
    path rather than as a broken tool. ``shutil.which`` cannot see that: it stats
    the file and checks the executable bit, and the failure happens later, in the
    dynamic loader. So the probe is to run the thing.

    When it does not run, the PRODUCTION loader repair is applied before giving
    up, for two reasons. It is what `ai c` itself does on the next launch, so
    skipping here while the launcher succeeds would report the suite as unable to
    test a path that in fact works; and it is the only way these tests observe
    the repair against a real tmux server rather than a fixture (AI-CLI-i2ih
    AC-5). The repair installs nothing and only edits this process's environment,
    which real-tmux tests then inherit.

    Shared by every module that gates on a live tmux. It used to be copied per
    module, and the copies had already drifted in what they reported.

    Every failure reason carries :data:`TMUX_UNUSABLE_SKIP`, so a skip taken on one
    is machine-recognisable by :func:`pytest_sessionfinish` below.
    """
    if shutil.which("tmux") is None:
        return False, tmux_unusable_skip_reason("tmux binary not available on PATH")

    def _probe() -> tuple[bool, str]:
        try:
            probe = subprocess.run(["tmux", "-V"], capture_output=True, text=True, timeout=30, check=False)
        except OSError as exc:
            return False, tmux_unusable_skip_reason(f"tmux could not be executed: {exc}")
        except subprocess.TimeoutExpired:
            return False, tmux_unusable_skip_reason("tmux -V timed out")
        if probe.returncode != 0:
            detail = (probe.stderr or probe.stdout or "").strip().splitlines()
            return False, tmux_unusable_skip_reason(
                f"tmux is on PATH but does not run: {detail[0] if detail else f'exit {probe.returncode}'}"
            )
        return True, ""

    runnable, reason = _probe()
    if runnable:
        return True, ""

    from ai_cli.tmux_setup import repair_tmux_loader_path

    repair = repair_tmux_loader_path()
    if not repair.repaired:
        return False, f"{reason} (loader repair did not help: {repair.detail})"
    return _probe()


#: Every skip this session took because tmux was unusable, as ``(nodeid, reason)``.
_tmux_unusable_skips: list[tuple[str, str]] = []


def _report_skip_reason(report) -> str:
    """The human reason from a skip report, or ``""`` if this is not a skip.

    Shape-tolerant on purpose. pytest represents a skip's ``longrepr`` as a
    ``(path, lineno, "Skipped: <reason>")`` triple, but this suite runs under
    ``-n auto`` and xdist round-trips every report through a serializer, so the
    triple can arrive at the controller as a list. An xfail's ``longrepr`` is
    neither shape.
    """
    longrepr = report.longrepr
    if not report.skipped or not isinstance(longrepr, (tuple, list)) or len(longrepr) != 3:
        return ""
    return str(longrepr[2])


def pytest_runtest_logreport(report):
    """Collect skips caused by an unusable tmux, for :func:`pytest_sessionfinish`."""
    reason = _report_skip_reason(report)
    if reason and is_tmux_unusable_skip(reason):
        _tmux_unusable_skips.append((report.nodeid, reason))


def tmux_skip_regression_message(skips, tmux_is_runnable: bool) -> str | None:
    """Why this run's tmux skips are a coverage regression, or ``None`` if they are not.

    Separated from the hook so the verdict is testable without driving a whole pytest
    session: the interesting part is the pairing of the two inputs, not the plumbing.

    A tmux skip on a host with no usable tmux is correct and reports nothing. The same
    skip on a host where tmux runs fine means the suite quietly covered less than it
    could, and that is the case worth failing on.
    """
    if not skips or not tmux_is_runnable:
        return None
    listed = "\n".join(f"  {nodeid}: {reason}" for nodeid, reason in skips)
    return (
        f"{len(skips)} test(s) skipped for lack of a usable tmux, on a host where tmux DOES run. "
        "That means this run covered less than the last one while looking just as green. It is "
        "the failure mode that hid a twelve-skip divergence until three unrelated tests happened "
        "to fail beside it (AI-CLI-qzf2): either tmux stopped working mid-run, or one of these "
        f"skips is misreporting its cause.\n{listed}"
    )


def pytest_sessionfinish(session, exitstatus):
    """Fail a run that skipped tmux tests despite tmux being usable (AI-CLI-qzf2).

    Session-scoped rather than per test because the signal is a COUNT: each individual
    skip looks reasonable in isolation, and it is only the total moving between two runs
    of the same commit that reveals coverage was lost.

    Under ``-n auto`` this must decide on the controller alone. Workers each see only
    their own shard, and the controller receives every worker's reports, so a worker
    deciding would report a fraction of the truth and set an exit status xdist does not
    use for the run's verdict.

    ``tmux_runnable`` is probed here rather than at import so the cost -- a real
    subprocess -- is paid only by a run that has something to decide.
    """
    if hasattr(session.config, "workerinput"):
        return
    message = tmux_skip_regression_message(_tmux_unusable_skips, tmux_runnable()[0])
    if message is None:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_sep("=", "tmux coverage regression", red=True, bold=True)
        reporter.write_line(message)
    session.exitstatus = pytest.ExitCode.TESTS_FAILED


def _command_argv(command) -> list[str]:
    """Return ``command`` as an argv list, however subprocess was handed it."""
    if isinstance(command, (list, tuple)):
        return [str(part) for part in command]
    if isinstance(command, str):
        try:
            return shlex.split(command)
        except ValueError:
            return command.split()
    return []


def shell_command_payloads(argv) -> list[str]:
    """Command STRINGS this argv hands a shell to run, in argv order.

    Only the argument of a command-string option is returned. A script path is not a
    command string and is excluded on purpose -- ``zsh -o NO_BG_NICE /tmp/supervisor`` and
    ``bash script.sh`` are how several tests here run a generated script, and scanning a
    path would charge them for the directory they happen to sit in.

    Exposed (not underscore-private) so the extraction can be tested directly rather than
    only observed through a refusal.
    """
    tokens = _command_argv(argv)
    payloads = []
    for index, token in enumerate(tokens[1:], start=1):
        is_command_flag = _POSIX_SHELL_COMMAND_FLAG.match(token) or token.lower() in _WINDOWS_SHELL_COMMAND_FLAGS
        if is_command_flag and index + 1 < len(tokens):
            payloads.append(tokens[index + 1])
    return payloads


def _test_temp_roots() -> tuple[str, ...]:
    """Every root a pytest temp directory can live under, as real paths.

    ``PYTEST_DEBUG_TEMPROOT`` is included because :func:`pytest_configure` relocates the
    Windows root to a short path on the drive anchor, which is NOT under
    ``tempfile.gettempdir()``.
    """
    roots = [tempfile.gettempdir()]
    relocated = os.environ.get("PYTEST_DEBUG_TEMPROOT")
    if relocated:
        roots.append(relocated)
    return tuple(os.path.realpath(root) for root in roots)


def payload_word_reaches_a_real_binary(word: str) -> bool:
    """Would a shell running ``word`` reach a binary outside this run's temp tree?

    The question the guard actually needs, and the reason it is not simply "does the payload
    mention a protected name". Tests here legitimately run a protected NAME through a shell
    after putting their own stub on a clean PATH -- ``test_session_launch_shell_resolution``
    writes a ``claude`` and a ``direnv`` into a temp bin directory and drives the generated
    launch command through the interpreter it chose, which is the only way that path is
    observed end to end. Refusing those would be a regression wearing a guard's clothes, the
    same trap :func:`_tmux_argv_is_read_only` exists to avoid for ``tmux -V``.

    Resolution mirrors what the shell itself would do: a word carrying a separator is used as
    the path it is, a bare name goes through ``PATH`` as the test has set it. An unresolvable
    name is not a hazard, because the shell could not run it either.
    """
    looks_like_path = os.sep in word or (os.altsep is not None and os.altsep in word)
    resolved = word if looks_like_path else shutil.which(word)
    if resolved is None:
        return False
    try:
        real = os.path.realpath(resolved)
    except OSError:
        return True  # cannot prove it is a stub, so treat it as the operator's own
    return not any(real.startswith(root + os.sep) for root in _test_temp_roots())


def shell_payload_protected_binary(program, argv, allowed_binaries=frozenset()) -> str | None:
    """The protected binary a shell's command string would really run, or ``None``.

    This is the AI-CLI-013v half of the guard: the program is allowed, the string it is
    told to run is not. Matching is on each word's BASENAME, so ``/usr/bin/ssh`` and a
    bare ``ssh`` are the same finding, and each candidate is then checked against
    :func:`payload_word_reaches_a_real_binary` so a test's own stub stays usable.

    ``allowed_binaries`` is honoured exactly as it is for a direct spawn -- a ``real_tmux``
    test may drive tmux through a shell for the same reason it may drive it directly.
    """
    if program not in _SHELL_PROGRAMS:
        return None
    protected = _PROTECTED_TEST_BINARIES - allowed_binaries
    for payload in shell_command_payloads(argv):
        for word in _SHELL_PAYLOAD_WORD.findall(payload):
            name = os.path.basename(word)  # noqa: PTH119 -- a shell word, not a filesystem path
            if name in protected and payload_word_reaches_a_real_binary(word):
                return name
    return None


def _reject_real_agent_process(command, allowed_binaries=frozenset(), argv=None):
    """Fail loudly when a test reaches a real agent, transport, or tmux boundary.

    ``argv`` carries the full argument vector when the caller cannot pass it as
    ``command``: ``os.execvp`` takes the program and the argv as two separate arguments, so
    the guard would otherwise see only ``zsh`` and never the ``-c`` string that is the
    whole hazard in AI-CLI-013v.
    """
    program = _command_program(command)
    if program == "tmux" and _tmux_argv_is_read_only(command):
        return
    if program in _DESKTOP_ESCAPE_BINARIES - allowed_binaries:
        _desktop_escape_attempts.append(program)
        raise RuntimeError(
            f"test attempted to reach the operator's desktop through a real `{program}` process. "
            "Patch subprocess.run (or the notifier itself), not only the paths it writes: "
            "redirecting the log while leaving the spawn live is exactly how AI-CLI-jk7v put a "
            "banner on the screen on every run for weeks. Asserting a notification WOULD be sent "
            "is fine -- do it against a mock."
        )
    if program in _PROTECTED_TEST_BINARIES - allowed_binaries:
        raise RuntimeError(
            f"test attempted to spawn a real `{program}` process — "
            "mock subprocess.run, subprocess.Popen, or os.execvp explicitly in this test"
        )
    laundered = shell_payload_protected_binary(program, argv if argv is not None else command, allowed_binaries)
    if laundered:
        _protected_spawn_attempts.append(laundered)
        raise RuntimeError(
            f"test attempted to run a real `{laundered}` through a `{program}` command string. "
            "A shell is allowed here; using one as a laundering route to a protected binary is "
            "not. Measured (AI-CLI-013v): an os.execvp of a shell whose -c payload ran ssh "
            "REPLACED the pytest process, so the run ended mid-collection with exit 0, no "
            "summary and no results, and a real outbound connection was attempted. Mock "
            "subprocess.run, subprocess.Popen or os.execvp explicitly in this test."
        )


class _RefusingPlyerNotification:
    """Stands in for ``plyer.notification`` so a Windows toast is refused and recorded."""

    @staticmethod
    def notify(**kwargs):
        """Refuse the toast the way :func:`_reject_real_agent_process` refuses a spawn.

        ``RuntimeError`` rather than ``ImportError`` on purpose, and that choice is the
        whole mechanism: ``_send_os_notification`` wraps the plyer call in
        ``except ImportError: pass`` to degrade silently when the optional
        ``[notify-win]`` extra is absent, so an ``ImportError`` here would be swallowed
        into a SUCCESS result with nothing recorded -- the exact blindness being closed.
        A ``RuntimeError`` falls through to the outer ``except Exception``, which
        reports failure, and the record below is what the teardown check turns into a
        test failure.
        """
        del kwargs
        _desktop_escape_attempts.append(_DESKTOP_ESCAPE_IMPORT)
        raise RuntimeError(
            "test attempted to reach the operator's desktop through a real plyer toast. "
            "This branch takes no subprocess, so patch `ai_cli.notifications` itself (or "
            "the plyer import) rather than a spawn boundary. Asserting a notification "
            "WOULD be sent is fine -- do it against a mock."
        )


@pytest.fixture(scope="session", autouse=True)
def _refuse_real_plyer_toasts():
    """Shadow ``plyer`` for the whole run so the Windows toast branch cannot fire.

    Installed unconditionally, on every platform, for the same reason the binary guard
    is: the branch is selected by ``sys.platform``, and tests force that value in order
    to exercise the Windows path from a Mac or Linux host. Gating this on the host OS
    would leave the branch unguarded on the one platform where it is the live one.

    Shadowing rather than ``setdefault``: where the optional ``[notify-win]`` extra IS
    installed, the real module would otherwise win and raise a real toast. Where it is
    absent, the import would raise ``ImportError`` and be swallowed -- so without this,
    neither case is observable.
    """
    stub = types.ModuleType(_DESKTOP_ESCAPE_IMPORT)
    stub.notification = _RefusingPlyerNotification  # type: ignore[attr-defined]
    saved = sys.modules.get(_DESKTOP_ESCAPE_IMPORT, _MISSING)
    sys.modules[_DESKTOP_ESCAPE_IMPORT] = stub
    try:
        yield stub
    finally:
        if saved is _MISSING:
            del sys.modules[_DESKTOP_ESCAPE_IMPORT]
        else:
            sys.modules[_DESKTOP_ESCAPE_IMPORT] = saved


@pytest.fixture(scope="session", autouse=True)
def _refuse_real_windows_registry_reads():
    """Shadow ``winreg`` for the whole run so no test can reread the real PATH (AI-CLI-8elu).

    ``direnv_setup.refresh_windows_path`` is the sole consumer. It exists to merge the
    machine's persisted ``Path`` into THIS process after an installer wrote the registry
    rather than the environment -- so it mutates ``os.environ["PATH"]`` as its whole
    purpose. On the launch path that runs inside ``ensure_direnv``, and a launch test
    that blanket-mocks ``subprocess.run`` to succeed makes every package manager "exit
    0", which is what carries the real registry into a test process' own PATH.

    That produced an order-dependent, Windows-only failure. The mutation is not undone
    unless the test happens to wrap ``os.environ`` in a restoring ``patch.dict``, so the
    FIRST launch test to run in an xdist worker changed PATH for every later test in it.
    A test asserting that the environment it set is the environment forwarded to ``tmux
    new-session`` then passed or failed purely on whether it drew that first slot --
    green on the same commit one run later.

    Refusing the read, rather than the mutation, is what makes this total: with both
    registry roots unreadable the function takes its own documented "unreadable
    registry" branch and returns False without touching PATH. Refusing quietly, unlike
    the desktop-escape guards, because reading a registry key harms nobody -- there is
    no escape to report, only a shared mutable to keep out of the suite.

    Only the two environment subkeys are refused, and every other key is handed to the
    real module. ``winreg`` is shared with the standard library and with dependencies --
    ``mimetypes`` and ``webbrowser`` both reach for it on Windows -- and any of them
    importing it late enough to see this stub would get an unrelated, hard-to-place
    failure from a blanket refusal. Narrowing to the keys that carry PATH leaves the
    guard total for the hazard and invisible to everything else.

    Installed on every platform, for the reason the plyer guard is: the branch is
    selected by ``sys.platform`` and tests force that value to exercise it from a Mac or
    Linux host, so gating the guard on the host OS would leave it off exactly where the
    branch is live. Tests that want the behaviour inject their own fake over this one
    (``monkeypatch.setitem(sys.modules, "winreg", ...)``), which still wins.
    """
    try:
        import winreg as real
    except ImportError:
        real = None  # type: ignore[assignment]

    def _open_key(root, subkey, *args, **kwargs):
        if str(subkey).lower() in _WINDOWS_ENVIRONMENT_SUBKEYS:
            raise OSError(f"{subkey!r} is shadowed in tests; inject a fake winreg to exercise this branch")
        if real is None:
            raise OSError("no winreg on this platform")
        return real.OpenKey(root, subkey, *args, **kwargs)

    stub = types.ModuleType("winreg")
    if real is None:
        # Enough surface for the Windows branch to be reachable from a POSIX host.
        stub.HKEY_LOCAL_MACHINE = 0  # type: ignore[attr-defined]
        stub.HKEY_CURRENT_USER = 1  # type: ignore[attr-defined]
    else:
        for name in dir(real):
            if not name.startswith("__"):
                setattr(stub, name, getattr(real, name))
    stub.OpenKey = _open_key  # type: ignore[attr-defined]
    stub.OpenKeyEx = _open_key  # type: ignore[attr-defined]
    saved = sys.modules.get("winreg", _MISSING)
    sys.modules["winreg"] = stub
    try:
        yield stub
    finally:
        if saved is _MISSING:
            del sys.modules["winreg"]
        else:
            sys.modules["winreg"] = saved


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
        # execvp's argv is its SECOND argument, and it carries the -c payload the
        # program name alone cannot reveal (AI-CLI-013v).
        argv = args[1] if len(args) > 1 else kwargs.get("args")
        _reject_real_agent_process(command, argv=argv)
        return real_execvp(*args, **kwargs)

    with (
        patch("subprocess.run", side_effect=guarded_run),
        patch("subprocess.Popen", side_effect=guarded_popen),
        patch("os.execvp", side_effect=guarded_execvp),
    ):
        yield


@pytest.fixture
def expect_desktop_escape():
    """Opt out of the teardown check below, for tests that DRIVE the guard on purpose.

    Returns the record so a test can assert what was attempted. Requesting this fixture
    is what suppresses the failure, checked by name rather than by fixture finalisation
    order, because that order is not something to bet a guard on -- which is also why
    this fixture has no teardown of its own to sequence.
    """
    return _desktop_escape_attempts


@pytest.fixture(autouse=True)
def _fail_on_desktop_escape(request):
    """Fail any test that tried to put a banner on the operator's screen (AI-CLI-8n4h).

    The refusal in :func:`_reject_real_agent_process` is the prevention; this is the
    report. Both are needed: ``notifications._send_os_notification`` catches
    ``Exception`` and returns a failed result, so a test that reaches it would be
    silently protected and never told. A guard that prevents without reporting leaves
    the next author free to write the same mistake.
    """
    _desktop_escape_attempts.clear()
    yield
    attempted = list(_desktop_escape_attempts)
    _desktop_escape_attempts.clear()
    if "expect_desktop_escape" in request.fixturenames:
        return
    assert not attempted, (
        f"this test tried to spawn {sorted(set(attempted))} and reach the operator's desktop. "
        "The spawn was refused, so nothing appeared on screen -- but patch the notifier or "
        "subprocess.run in the test rather than relying on this guard. Patching only the paths "
        "the notifier writes is not enough: that is exactly AI-CLI-jk7v."
    )


@pytest.fixture
def expect_protected_spawn():
    """Opt out of the laundered-spawn report below, for tests that DRIVE that guard.

    Returns the record so a test can assert what was attempted. Requesting the fixture is
    what suppresses the failure, checked by name rather than by finalisation order, for the
    reason :func:`expect_desktop_escape` is.
    """
    return _protected_spawn_attempts


@pytest.fixture(autouse=True)
def _fail_on_laundered_protected_spawn(request):
    """Fail any test that ran a protected binary through a shell command string (AI-CLI-013v).

    The refusal in :func:`_reject_real_agent_process` is the prevention; this is the report,
    and both are needed for the same reason they are for the desktop escapes: the refusal
    travels as a ``RuntimeError`` through code that catches broadly, so a test can be
    silently protected and never told it wrote the mistake.
    """
    _protected_spawn_attempts.clear()
    yield
    attempted = list(_protected_spawn_attempts)
    _protected_spawn_attempts.clear()
    if "expect_protected_spawn" in request.fixturenames:
        return
    assert not attempted, (
        f"this test asked a shell to run {sorted(set(attempted))}, which is a protected binary. "
        "The spawn was refused, so nothing real was reached -- but mock subprocess.run, "
        "subprocess.Popen or os.execvp in the test rather than relying on this guard. An "
        "unrefused one replaces the pytest process and the run reports success with no results."
    )


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_tmux_sessions_after_suite():
    """Backstop cleanup for test-only tmux names if an explicit mock is bypassed."""
    yield
    _cleanup_test_tmux_sessions(subprocess.run)


def snapshot_tree(path: Path) -> dict[str, tuple[int, int]] | None:
    """Fingerprint every entry under ``path``, or return None when it does not exist.

    Returns a mapping of POSIX-relative name to ``(size, mtime_ns)``. ``None`` is a
    distinct answer from ``{}`` on purpose: "the directory is absent" and "the
    directory exists and is empty" are different states, and a test that merely
    *creates* the real iTerm2 profile directory has already polluted the machine
    even though it wrote no profile into it.

    Exposed (not underscore-private) so the guard's detection logic can be tested
    directly rather than only observed in passing.
    """
    if not path.is_dir():
        return None
    snapshot: dict[str, tuple[int, int]] = {}
    for entry in sorted(path.rglob("*")):
        try:
            stat = entry.stat()
        except OSError:
            # Vanished mid-walk: record a sentinel so it still counts as a difference.
            snapshot[entry.relative_to(path).as_posix()] = (-1, -1)
            continue
        snapshot[entry.relative_to(path).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def describe_tree_change(
    before: dict[str, tuple[int, int]] | None,
    after: dict[str, tuple[int, int]] | None,
) -> str:
    """Describe how two :func:`snapshot_tree` results differ; "" when identical."""
    if before == after:
        return ""
    if before is None:
        return "directory did not exist before the test and does now"
    if after is None:
        return "directory existed before the test and was removed"
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    modified = sorted(name for name in set(before) & set(after) if before[name] != after[name])
    parts = []
    if added:
        parts.append(f"added {added}")
    if removed:
        parts.append(f"removed {removed}")
    if modified:
        parts.append(f"modified {modified}")
    return "; ".join(parts)


@pytest.fixture(autouse=True)
def _redirect_home_away_from_the_operator(monkeypatch, tmp_path_factory):
    """Point ``Path.home()`` at a per-test temp dir so no test can write the real home.

    Installed at the PROCESS BOUNDARY rather than per call site, because per-call
    discipline demonstrably failed here (AI-CLI-tevy). ``test_icon_generator.py``
    patches ``_dynamic_profile_dir`` at every one of its ~30 call sites and is not the
    leak; the profiles that reached the operator's real
    ``~/Library/Application Support/iTerm2/DynamicProfiles/`` came from session-launch
    tests that drive ``iterm2.py``'s generator indirectly and had no reason to know a
    profile write was involved. Patching one function could not have covered them all
    either: there are three independent real-directory paths --
    ``icon_generator._dynamic_profile_dir`` (writes a profile),
    ``layout._dynamic_profile_dir`` (a SECOND, separately-defined copy that writes
    layout profiles), and ``session._sweep_stale_iterm2_profiles`` (which ``unlink()``s every
    ``ai-cli-session-*.json`` whose tmux session is not currently live, so a test with a
    mocked-empty session list deletes the operator's LIVE profiles). All three derive
    from ``Path.home()``, which is the one lever that covers them by construction.

    A newly written test inherits this without opting in -- the property step 1 of the
    task asked for -- and iTerm2 hot-reloads that directory on any filesystem event, so
    a stray write there re-parses and re-enumerates the live terminal's profile set.

    ``HOME`` alone is not portable: ``ntpath.expanduser`` consults ``USERPROFILE``
    first and never reads ``HOME``, so Windows needs it set too, and the
    ``HOMEDRIVE``/``HOMEPATH`` pair is cleared so it cannot serve as a third route.
    """
    fake_home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.delenv("HOMEDRIVE", raising=False)
    monkeypatch.delenv("HOMEPATH", raising=False)
    return fake_home


def home_redirect_breach() -> str:
    """Describe how the operator's real home is still reachable; "" when it is not.

    The property enforced is "home is not the REAL home", deliberately NOT "home is
    exactly this fixture's redirect target". Plenty of tests legitimately re-point
    ``HOME`` at a ``tmp_path`` of their own; that is correct isolation reaching the same
    end by its own route, and demanding they match this fixture's directory failed 72 of
    them for doing the right thing. What must never happen is ``Path.home()`` resolving
    back to the directory iTerm2 is watching.

    Checks only THIS process's own environment, which is what makes it usable as a gate:
    the answer cannot be changed by another process on the machine. Every route
    ``Path.home()`` can take is covered -- POSIX ``expanduser`` reads ``HOME``,
    ``ntpath.expanduser`` reads ``USERPROFILE`` and then ``HOMEDRIVE`` + ``HOMEPATH``.
    The last two are checked as a pair because neither alone reconstructs a home.
    """
    real_home = str(_REAL_HOME)
    breaches = []
    resolved = _pathlib_home()
    if resolved is not None and resolved == real_home:
        breaches.append(f"Path.home() resolved to the operator's real home {real_home}")
    breaches += [
        f"{var} points at the real home" for var in ("HOME", "USERPROFILE") if os.environ.get(var) == real_home
    ]
    drive, tail = os.environ.get("HOMEDRIVE"), os.environ.get("HOMEPATH")
    # Compared in string space, not by constructing a Path: see _pathlib_home for why
    # pathlib cannot be relied on to answer here.
    if drive and tail and os.path.normpath(drive + tail) == os.path.normpath(real_home):
        breaches.append("HOMEDRIVE + HOMEPATH reconstruct the real home")
    return "; ".join(breaches)


def _pathlib_home() -> str | None:
    """``Path.home()`` as a string, or None when pathlib refuses to answer.

    ``pathlib.Path`` dispatches on ``os.name``, and a test that patches ``os.name``
    to ``"nt"`` to exercise a Windows branch -- ``test_runaway_loop_guards.py`` does --
    makes every subsequent ``Path(...)`` a ``WindowsPath``, which refuses to
    instantiate on POSIX below Python 3.14 (``NotImplementedError`` to 3.12,
    ``pathlib.UnsupportedOperation`` in 3.13).

    The guard below runs during teardown, and its teardown runs BEFORE monkeypatch's
    undo -- it depends on the redirect fixture, which depends on ``monkeypatch``, so
    ``monkeypatch`` finalises last. The patch is therefore still in force here, and
    raising would turn a PASSING test into a teardown ERROR. That is exactly how this
    first reached CI: three Linux jobs and the macOS job went red reporting
    ``3125 passed ... 1 error`` while a local run on 3.14, where the instantiation is
    allowed, was clean.

    Returning None loses no enforcement. On POSIX ``Path.home()`` reads ``HOME``, and
    on Windows ``USERPROFILE`` then ``HOMEDRIVE``+``HOMEPATH``; the caller checks all
    four directly, in string space, unaffected by ``os.name``.
    """
    try:
        return str(Path.home())
    except Exception:
        return None


@pytest.fixture(autouse=True)
def _guard_real_iterm2_profile_dir(_redirect_home_away_from_the_operator):
    """Fail any test that can reach the operator's real iTerm2 Dynamic Profiles dir.

    The redirect above is the prevention; this is the proof that it held.

    Depending on that fixture is what orders the TEARDOWNS: pytest finalises a fixture
    before the ones it depends on, so this check runs while the redirect is still in
    place. Dropping the argument as unused made every test fail here, because
    monkeypatch had already restored the real ``HOME`` by the time the check ran.

    The ENFORCED check is that the real home is unreachable at teardown, because that is
    a property of this process alone and so is deterministic. Watching the real directory
    for changes and failing on any of them was tried first and rejected: that directory
    has other legitimate writers -- a concurrent ``ai c`` launch, or another checkout of
    this repo running its own suite -- and a watch cannot tell their writes from a test's.
    Measured, it charged 53 innocent tests with strays that peer runs of this suite had
    created, and since the operator launches sessions while tests run, it would have
    stayed flaky permanently rather than only until peers picked up the fix. A guard that
    cries wolf gets deleted, so it enforces the attributable half and reports the rest.

    A change to the real directory is therefore still surfaced, as a warning naming the
    likely external writer. With the real home unreachable a home-derived write is
    impossible by construction, so such a change is provably not this test's doing; the
    one route that would bypass it is a hardcoded absolute path, and static checking
    closes that deterministically instead (see ``test_iterm2_profile_isolation.py``).
    """
    before = snapshot_tree(_REAL_ITERM2_PROFILE_DIR)
    yield
    breach = home_redirect_breach()
    assert not breach, (
        f"the operator's real home became reachable during this test, exposing "
        f"{_REAL_ITERM2_PROFILE_DIR}: {breach}. iTerm2 watches that directory and "
        "re-enumerates every entry on any filesystem event, so a write there mutates the "
        "operator's live terminal, and session._sweep_stale_iterm2_profiles deletes from "
        "it. Re-pointing HOME at a tmp_path of your own is fine; pointing it back at the "
        "real home is not, and neither is a bare monkeypatch.undo(), which reverts this "
        "fixture's redirect along with the test's own patches."
    )
    change = describe_tree_change(before, snapshot_tree(_REAL_ITERM2_PROFILE_DIR))
    if change:
        warnings.warn(
            f"the real iTerm2 Dynamic Profiles directory {_REAL_ITERM2_PROFILE_DIR} "
            f"changed while this test ran: {change}. The HOME redirect was intact, so "
            "this test cannot have caused it via Path.home() -- the likely writer is a "
            "concurrent `ai` session launch or another checkout of this repo running "
            "its suite without this redirect.",
            stacklevel=1,
        )


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
def _isolate_workspace_trust_registry(monkeypatch, tmp_path_factory):
    """Keep workspace-trust registration out of the real home directory.

    ``create_worktree()`` registers its newly-created checkout in Claude Code's
    home-level trust registry. Under xdist, otherwise-isolated test repositories
    concurrently read and replace that one file. Seed a distinct registry for
    each test so the real integration path stays exercised without sharing
    state across workers or with the user.
    """
    trust_registry = tmp_path_factory.mktemp("claude_trust") / ".claude.json"
    trust_registry.write_text('{"projects": {}}\n')
    monkeypatch.setattr(_trust_module, "_claude_json_path", lambda: trust_registry)


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

    Fleet event publishers are fire-and-forget: they build a ``NATSClient`` and
    ``asyncio.run(...)`` a publish, wrapped in ``except Exception: pass``. That
    swallows a *failure* but cannot shorten a *hang*, so tests must not exercise
    the network instead of their intended behavior.

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
def _restore_process_working_directory():
    """Keep one test's temporary CLI directory from leaking to the next test."""
    checkout_root = Path(__file__).resolve().parent.parent
    try:
        original_cwd = Path.cwd()
    except OSError:
        original_cwd = checkout_root
        os.chdir(original_cwd)

    yield

    try:
        os.chdir(original_cwd)
    except OSError:
        os.chdir(checkout_root)


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


def _run_cli_with_args(argv, config_override=None, capture_ssh_runner=False):
    """Helper: invoke cli() with argv, capturing execvp calls.

    os.execvp replaces the process in real usage, so we raise SystemExit
    to simulate that — otherwise execution falls through to later exec calls.

    ``capture_ssh_runner=True`` returns the mock for
    ``transport.run_ssh_with_reconnect`` instead of the ``execvp`` mock. The
    pure-SSH remote path stopped ``execvp``-ing a shell in AI-CLI-w679 -- it runs
    in-process now so a dropped link can be reattached and the terminal handed
    back -- so tests of THAT path assert against the ssh argv list passed to the
    runner, rather than against a joined shell string. Every other path (mosh,
    ``ai ssh``, tmux attach) still execs and still uses the default.
    """
    config = config_override or {}

    def remote_preflight(command, **_kwargs):
        if isinstance(command, (list, tuple)) and any("dolt_server.py" in str(part) for part in command):
            # Not a remote-SSH command at all -- _ensure_dolt_server's local advisory
            # probe runs before every session launch and shares this same mocked
            # subprocess.run. Its argv has no shell string to parse below.
            return make_subprocess_result(stdout='{"status": "healthy"}')
        if _command_program(command) == "tmux":
            return make_subprocess_result(returncode=1)
        if command[-1] == _REMOTE_SHELL_PROBE_CMD:
            return make_subprocess_result(stdout="zsh\n")
        remote_command = shlex.split(command[-1])[-1]
        tokens = shlex.split(remote_command)
        if "update" in tokens and "ai" in tokens:
            return make_subprocess_result(stdout="current")
        allocation_index = tokens.index("allocate-session-name")
        engine, project_prefix, name = tokens[allocation_index + 1 : allocation_index + 4]
        session_id, ai_name = _session_module.build_session_name(engine, project_prefix, name, is_remote=True)
        return make_subprocess_result(stdout=json.dumps({"session_id": session_id, "ai_name": ai_name}))

    with (
        patch("sys.argv", argv),
        patch("ai_cli.config.load_config", return_value=config),
        patch("ai_cli.session.is_current_project_resolved", return_value=True),
        patch("ai_cli.session.get_project_prefix", return_value="test-project"),
        patch("os.execvp", side_effect=SystemExit(0)) as mock_exec,
        patch("ai_cli.transport.run_ssh_with_reconnect", return_value=0) as mock_ssh_runner,
        patch("ai_cli.main.trigger_background_update"),
        patch("ai_cli.main._auto_update_if_stale"),
        patch("ai_cli.main.subprocess.run", side_effect=remote_preflight),
    ):
        from ai_cli.main import cli

        try:
            cli()
        except SystemExit:
            pass
        return mock_ssh_runner if capture_ssh_runner else mock_exec


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

    Such repositories can accumulate on Windows before the mechanism is traced.

    Deliberately unconditional rather than a ``sys.platform`` branch -- POSIX
    ``rmtree`` only needs write on the parent directory, so this is a no-op cost
    on Linux and macOS instead of a platform special case.

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
