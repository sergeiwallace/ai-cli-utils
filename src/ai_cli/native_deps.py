"""Shared machinery for the native dependencies pip/uv can never supply.

``direnv`` and ``tmux`` are both C binaries: declaring them in ``[dependencies]``
is impossible, so each one is detected at run time and, where a package manager
can do it unattended, installed. The two bootstrappers differ in what they probe
and what they print, but the install attempt itself is the same loop, so it lives
here rather than being written twice.

A native binary has a second failure mode a package manager cannot describe: it
is *there* and cannot run, because the dynamic loader can no longer find a
shared library it was linked against. That is not hypothetical — an ephemeral
container filesystem rebuilt underneath a persistent per-user prefix produces it
on every restart (AI-CLI-i2ih). The loader-repair half of this module handles it
by *discovery*: the library is usually still on the box, in a directory the
loader was never told to look in, and pointing it there costs nothing and
installs nothing. Vendoring copies of system libraries was the alternative and
was rejected — it would make this package the owner of their staleness.

Everything in this module is non-raising by contract. Its callers run mid-launch,
and a bootstrap helper that throws would take down the session it exists to keep
running.
"""

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, MutableMapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

# One install candidate: (probe, argv). ``probe`` is the executable that must be
# on PATH for the entry to apply. Only non-interactive invocations belong in a
# candidate list -- an installer that can block on a password or a UAC prompt
# would hang a session launch instead of failing it.
Candidate = tuple[str, list[str]]

# Package managers that mutate system paths, so they need root. Attempting one
# unprivileged either prompts for a password (hanging a launch) or fails on
# permissions, so they are skipped rather than tried.
_ROOT_MANAGERS = ("apt-get", "dnf", "pacman", "zypper")


@dataclass(frozen=True)
class InstallResult:
    """Outcome of one bootstrap attempt.

    ``installed`` is the only success signal. ``tool`` names the package manager
    that ran (None when none applied), and ``detail`` carries the failure text
    worth surfacing — a non-zero installer's stderr, or why nothing ran.

    ``unusable`` is a stronger claim than ``not installed``, and the distinction
    is load-bearing: it means the tool was POSITIVELY established to be
    unavailable — absent from PATH, or present and demonstrably unable to load a
    library it names. ``not installed`` with ``unusable=False`` means "could not
    be confirmed either way", which is not grounds for degrading a launch. A
    binary that runs but answers a version query in an unexpected shape is the
    common case there, and downgrading the session over it would trade a working
    feature for a parsing quirk.
    """

    installed: bool
    tool: str | None = None
    detail: str = ""
    unusable: bool = False


def needs_root(argv: list[str]) -> bool:
    """True when ``argv`` is a system package manager and we are not root."""
    if argv[0] not in _ROOT_MANAGERS:
        return False
    return getattr(os, "geteuid", lambda: 0)() != 0


def can_prompt_for_root() -> bool:
    """True when it is safe to let a package manager ask for a password.

    Two conditions, and both are required. ``sudo`` must exist, and **stdin must
    be a terminal** — because a password prompt with nobody watching does not
    fail, it blocks forever. That distinction is the whole reason escalation is
    opt-in per call site rather than a global setting: an explicit ``ai setup`` or
    ``ai doctor`` has a human in front of it, while a launch fired from a shell
    hook, a background job or ``tmux new-session`` does not, and hanging one of
    those is strictly worse than degrading it.
    """
    if sys.platform == "win32":
        return False  # no sudo; elevation is a UAC prompt we must never trigger
    if shutil.which("sudo") is None:
        return False
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError, OSError):
        # A detached or closed stdin raises rather than answering False.
        return False


def _authenticate_root(timeout: int = 120) -> bool:
    """Refresh sudo's credential cache with the prompt VISIBLE. Never raises.

    Deliberately a separate step from running the installer. ``sudo <manager>``
    under ``capture_output=True`` swallows the password prompt, so the user sees a
    silent hang and the call eventually times out — the exact failure mode this
    whole module exists to avoid. ``sudo -v`` inherits the terminal so the prompt
    is seen and answered; every later call can then use ``sudo -n`` and keep its
    output captured.
    """
    print("ai-cli-utils: this install needs administrator rights.", file=sys.stderr)
    try:
        proc = subprocess.run(["sudo", "-v"], timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


# --- Provisioning into a prefix the host cannot take away --------------------
#
# The lesson behind this section (AI-CLI-i2ih): a binary is only as durable as
# the least durable thing it depends on. A conda/micromamba base prefix is a
# perfectly good place to install a tool, right up until that prefix lives on a
# filesystem the platform rebuilds -- measured on a managed notebook host, where
# `conda info` reports /opt/conda and `df` puts /opt/conda on an ephemeral overlay
# while only $HOME persists. Installing there produces a tool that vanishes on
# every restart, which is the same bug class the tmux loader repair exists for.
#
# So anything this package provisions goes into a prefix under the user's own
# data directory, and nowhere else.


def native_prefix() -> Path:
    """The prefix this package installs native binaries into.

    Under the user's XDG data directory (``%LOCALAPPDATA%`` on Windows), which is
    the one location on any supported platform that a host rebuild leaves alone.
    """
    from .config import get_xdg_data_home

    return get_xdg_data_home() / "native"


def prefix_bin(prefix: Path | None = None) -> Path:
    """The executable directory inside a conda-family prefix, per platform."""
    prefix = native_prefix() if prefix is None else prefix
    return prefix / ("Scripts" if sys.platform == "win32" else "bin")


def adopt_prefix_bin(env: MutableMapping[str, str] | None = None, prefix: Path | None = None) -> bool:
    """Put our provisioned bin directory first on PATH. Cheap and idempotent.

    This is the *launch-time* half of provisioning: a stat and maybe a string
    join, with no subprocess and no installer. It exists so a launch can pick up
    an already-provisioned tool without ever risking a package manager's timeout
    mid-session — installing is reserved for the explicit commands.

    Returns True when the directory exists (whether or not PATH needed changing),
    because that is the question a caller actually has: is our prefix usable.

    Non-raising, like everything else here, and that is load-bearing rather than
    defensive: this runs from ``resolve_session_shell`` on the launch path, so any
    exception would take down the session it exists to improve. Resolving the
    prefix can genuinely fail — it consults ``sys.platform``, and building a
    ``WindowsPath`` on a POSIX host raises ``NotImplementedError`` — so "cannot
    work out where our prefix would be" has to mean "there is no prefix to adopt".
    """
    env = os.environ if env is None else env
    try:
        binary_dir = prefix_bin(prefix)
        if not binary_dir.is_dir():
            return False
    except Exception:
        return False
    current = env.get("PATH", "")
    entries = current.split(os.pathsep) if current else []
    if str(binary_dir) not in entries:
        env["PATH"] = os.pathsep.join([str(binary_dir), *entries]) if entries else str(binary_dir)
    return True


def prefix_installers(package: str, prefix: Path | None = None) -> list[Candidate]:
    """Rootless conda-family candidates that install ``package`` into our prefix.

    ``create`` versus ``install`` is decided by whether the prefix already holds a
    conda environment, because the two are not interchangeable: ``install`` needs
    an existing environment and ``create`` refuses a populated one. Probing for
    ``conda-meta`` answers it deterministically instead of relying on whichever
    error message the current version emits.

    No system managers here on purpose. These candidates need no privileges at
    all, so they are the ones a launch could in principle survive; escalation is a
    separate, explicitly requested path.
    """
    prefix = native_prefix() if prefix is None else prefix
    verb = "install" if (prefix / "conda-meta").is_dir() else "create"
    common = [verb, "-y", "-q", "--prefix", str(prefix), "-c", "conda-forge", package]
    return [("micromamba", ["micromamba", *common]), ("conda", ["conda", *common])]


def attempt_installs(
    candidates: list[Candidate],
    verify: Callable[[], bool],
    timeout: int = 300,
    before_verify: Callable[[], object] | None = None,
    allow_root: bool = False,
) -> InstallResult:
    """Try each candidate for this platform in order; stop at the first success.

    ``verify`` re-probes for the tool itself, because a manager's exit status is
    not proof: some exit 0 having only staged a pending install. ``before_verify``
    runs between a zero exit and that re-probe, for the Windows PATH refresh —
    the installer wrote its directory to the registry, not to this already
    running process, so without it a perfectly good install looks like a failure.

    ``allow_root`` opts *this call* into escalating a system package manager
    through ``sudo``, prompting for a password. It defaults to False and must stay
    that way for anything on the launch path: a prompt nobody is watching blocks
    forever, which is a worse outcome than the degradation this function exists to
    report. Even when opted in, escalation happens only if ``sudo`` exists and
    stdin is a terminal, and the password is collected by a separate visible
    ``sudo -v`` so the prompt cannot be swallowed by output capture.
    """
    if not candidates:
        return InstallResult(False, detail=f"no unattended installer is known for platform {sys.platform!r}")

    skipped: list[str] = []
    attempted: list[str] = []
    authenticated = False
    for probe, argv in candidates:
        if shutil.which(probe) is None:
            continue
        if needs_root(argv):
            if not allow_root:
                skipped.append(f"{probe} (needs root; re-run `ai doctor` in a terminal, or install manually)")
                continue
            if not can_prompt_for_root():
                skipped.append(f"{probe} (needs root and there is no terminal to ask on)")
                continue
            if not authenticated:
                # Show the exact command before asking for a password. Nobody
                # should be typing one for an action they have not seen.
                print(f"ai-cli-utils: about to run: sudo {' '.join(argv)}", file=sys.stderr)
                authenticated = _authenticate_root()
            if not authenticated:
                skipped.append(f"{probe} (root authentication declined or failed)")
                continue
            # -n so this cannot re-prompt under capture_output; the credential was
            # just cached by the visible `sudo -v` above.
            argv = ["sudo", "-n", *argv]
        attempted.append(probe)
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            skipped.append(f"{probe} ({type(exc).__name__})")
            continue
        if proc.returncode == 0:
            if before_verify is not None:
                before_verify()
            if verify():
                return InstallResult(True, tool=probe)
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        skipped.append(f"{probe} (exit {proc.returncode}: {detail[-1] if detail else 'no output'})")

    if not attempted and not skipped:
        managers = ", ".join(probe for probe, _ in candidates)
        return InstallResult(False, detail=f"none of these package managers are on PATH: {managers}")
    return InstallResult(False, detail="; ".join(skipped))


# ---------------------------------------------------------------------------
# Dynamic-loader repair
# ---------------------------------------------------------------------------

# Env var an operator can set to name a library directory this module's own
# heuristic would not find. Colon/semicolon separated like any other path list.
LIBRARY_PATH_OVERRIDE = "AI_CLI_LIBRARY_PATH"

# How each loader says "I could not find this library". Parsed from the failed
# process's own stderr rather than from `ldd`/`otool`, because the process has
# already named exactly what it wanted and those tools are not everywhere:
# `ldd` is absent on musl and on macOS.
_MISSING_LIB_PATTERNS = (
    # glibc: "error while loading shared libraries: libfoo.so.1: cannot open..."
    re.compile(r"error while loading shared libraries:\s*([^\s:]+)"),
    # musl: "Error loading shared library libfoo.so.1: No such file or directory"
    re.compile(r"Error (?:loading shared library|relocating)\s+([^\s:]+)"),
    # macOS dyld: "Library not loaded: /opt/homebrew/lib/libfoo.1.dylib"
    re.compile(r"Library not loaded:\s*(\S+)"),
)

# How deep to look for a self-contained payload's own library directory. An
# extracted AppImage/AppDir, and several "unpack a tarball into ~/.local" style
# installs, keep their libraries at <prefix>/lib/<payload>/usr/lib -- exactly
# one directory below the sibling lib dir. Nothing here recurses: an unbounded
# walk of a home directory is a launch-time cost nobody asked for.
_PAYLOAD_SUBDIRS = (("usr", "lib"), ("usr", "lib64"), ("lib",), ("lib64",))


@dataclass(frozen=True)
class LoaderRepair:
    """Outcome of one attempt to make an unrunnable binary run.

    ``repaired`` is the only success signal, and it means the binary was
    re-executed and exited zero afterwards -- never merely that a directory was
    found. ``missing`` is what the loader asked for, ``unresolved`` the subset
    that could not be located anywhere, and ``added_dirs`` what was prepended to
    ``variable``. ``detail`` carries the text worth showing a human.
    """

    repaired: bool
    missing: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    added_dirs: tuple[str, ...] = ()
    variable: str | None = None
    searched: tuple[str, ...] = field(default=())
    detail: str = ""


def loader_path_var() -> str | None:
    """The environment variable this platform's dynamic loader searches.

    None on Windows, which resolves DLLs through ``PATH`` and has no separate
    search variable — so there is nothing to repair there, and saying so is
    better than pretending ``LD_LIBRARY_PATH`` means something on win32.

    Note for macOS: ``DYLD_LIBRARY_PATH`` is stripped by System Integrity
    Protection for protected binaries. A tmux from Homebrew or MacPorts is not
    protected, so the repair can work there; one shipped inside a system path
    cannot be repaired this way and will report an honest failure.
    """
    if sys.platform == "win32":
        return None
    return "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"


def _as_text(value: object) -> str:
    """A process stream as text, or "" for anything unreadable. Never raises.

    ``text=True`` yields ``str``, but a caller's ``subprocess.run`` may not be
    the real one, and a stream can legitimately be ``bytes`` or ``None``. This
    module's non-raising contract has to hold at that boundary too: handing a
    non-string to a regex is a ``TypeError`` mid-launch.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return ""


def missing_shared_libraries(stderr: str) -> tuple[str, ...]:
    """Library names a loader error names, in order, deduplicated.

    Returns empty for anything that is not a loader error. That matters more
    than it looks: a wrong diagnosis would send the repair hunting for a library
    that was never the problem and then report a remedy that cannot work.
    """
    found: list[str] = []
    for pattern in _MISSING_LIB_PATTERNS:
        for match in pattern.finditer(_as_text(stderr)):
            # dyld names a full install path; only the filename is searchable.
            name = match.group(1).rsplit("/", 1)[-1].rstrip(":")
            if name and name not in found:
                found.append(name)
    return tuple(found)


def candidate_library_dirs(exe: str | Path) -> tuple[Path, ...]:
    """Directories that plausibly hold a library for ``exe``, best first.

    Derived from the binary's own install prefix, so it stays correct for a
    conda env, a Homebrew cellar, a hand-unpacked ``~/.local`` tree or anything
    else, without this module knowing about any of them. Symlinks are followed
    too: a launcher symlink in ``~/.local/bin`` pointing into an unpacked tree
    is a normal shape, and the libraries live by the real file.

    Only directories that exist are returned. A nonexistent entry in the
    loader's search variable is silently ignored by the loader, which would make
    a doomed repair look like a real one.
    """
    override = os.environ.get(LIBRARY_PATH_OVERRIDE, "")
    roots: list[Path] = [Path(part) for part in override.split(os.pathsep) if part]

    paths = [Path(exe)]
    try:
        resolved = Path(exe).resolve()
    except OSError:  # a broken symlink still has a usable literal path
        resolved = paths[0]
    if resolved != paths[0]:
        paths.append(resolved)

    for path in paths:
        prefix = path.parent.parent
        for libname in ("lib", "lib64"):
            lib = prefix / libname
            roots.append(lib)
            if not lib.is_dir():
                continue
            try:
                children = sorted(child for child in lib.iterdir() if child.is_dir())
            except OSError:
                continue
            roots += [child.joinpath(*parts) for child in children for parts in _PAYLOAD_SUBDIRS]

    seen: list[Path] = []
    for root in roots:
        if root not in seen and root.is_dir():
            seen.append(root)
    return tuple(seen)


def find_library_dirs(exe: str | Path, sonames: Sequence[str]) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    """Locate each of ``sonames`` near ``exe``.

    Returns ``(dirs that supply at least one, names found nowhere)``. The second
    half is why this does not just return dirs: handing back a partial answer
    silently would produce a loader path that cannot work, plus a success report.
    """
    candidates = candidate_library_dirs(exe)
    dirs: list[Path] = []
    unresolved: list[str] = []
    for soname in sonames:
        for candidate in candidates:
            if (candidate / soname).exists():
                if candidate not in dirs:
                    dirs.append(candidate)
                break
        else:
            unresolved.append(soname)
    return tuple(dirs), tuple(unresolved)


def _run_quietly(argv: list[str], timeout: int, env: MutableMapping[str, str]) -> tuple[int, str]:
    """Run ``argv``, returning ``(returncode, stderr)``. Never raises.

    A failure to spawn at all is reported as a non-zero code with the exception
    text as its stderr, so callers have one shape to handle.
    """
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=dict(env),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 126, f"{type(exc).__name__}: {exc}"
    return proc.returncode, _as_text(proc.stderr) or _as_text(proc.stdout)


def repair_loader_path(
    argv: list[str],
    *,
    env: MutableMapping[str, str] | None = None,
    timeout: int = 15,
) -> LoaderRepair:
    """Make ``argv`` runnable by pointing the loader at libraries already present.

    Runs ``argv``; if it already works, changes nothing. Otherwise reads the
    loader's complaint, looks for those libraries near the binary's own prefix,
    prepends what it finds to this platform's loader search variable in ``env``,
    and **re-runs the command to prove it worked**. A repair that cannot be
    demonstrated is rolled back: a speculative search path left behind would
    change how every later child process resolves its libraries.

    ``env`` defaults to ``os.environ``, which is what makes a successful repair
    apply to every subprocess and ``exec`` this process goes on to make.

    ``argv`` must be a read-only invocation — this runs it up to twice.
    """
    env = os.environ if env is None else env
    variable = loader_path_var()
    if variable is None:
        return LoaderRepair(False, detail=f"no dynamic-loader search path on platform {sys.platform!r}")

    exe = shutil.which(argv[0], path=env.get("PATH"))
    if exe is None:
        return LoaderRepair(False, detail=f"{argv[0]} is not on PATH")

    code, stderr = _run_quietly(argv, timeout, env)
    if code == 0:
        return LoaderRepair(False, detail=f"{argv[0]} already runs; nothing to repair")

    missing = missing_shared_libraries(stderr)
    if not missing:
        first = next((line for line in stderr.splitlines() if line.strip()), f"exit {code}")
        return LoaderRepair(False, detail=first.strip())

    dirs, unresolved = find_library_dirs(exe, missing)
    searched = tuple(str(d) for d in candidate_library_dirs(exe))
    if unresolved or not dirs:
        return LoaderRepair(
            False,
            missing=missing,
            unresolved=unresolved or missing,
            searched=searched,
            detail=(
                f"{', '.join(unresolved or missing)} not found in any of: "
                f"{', '.join(searched) or '(no library directory near the binary)'}"
            ),
        )

    previous = env.get(variable)
    env[variable] = os.pathsep.join([*(str(d) for d in dirs), *([previous] if previous else [])])
    code, stderr = _run_quietly(argv, timeout, env)
    if code == 0:
        return LoaderRepair(
            True,
            missing=missing,
            added_dirs=tuple(str(d) for d in dirs),
            variable=variable,
            searched=searched,
            detail=f"{', '.join(missing)} resolved from {', '.join(str(d) for d in dirs)}",
        )

    # Not demonstrably better, so put the environment back exactly as it was.
    if previous is None:
        env.pop(variable, None)
    else:
        env[variable] = previous
    first = next((line for line in stderr.splitlines() if line.strip()), f"exit {code}")
    return LoaderRepair(
        False,
        missing=missing,
        searched=searched,
        detail=f"found {', '.join(missing)} but {argv[0]} still fails: {first.strip()}",
    )
