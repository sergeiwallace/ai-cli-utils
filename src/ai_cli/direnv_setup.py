"""Cross-platform direnv bootstrap: detect, install, and report remediation.

direnv is an *enhancement*, never a launch precondition. Two upstream facts drive
the whole design here:

- ``.envrc`` is always evaluated in a **bash sub-process** (direnv's own FAQ), so
  bash is required even when the interactive shell is PowerShell or fish.
- direnv's stated prerequisite is a Unix-like OS. A Windows build is published
  (winget), and ``direnv hook pwsh`` exists, but a Windows host still needs a
  bash on PATH — Git for Windows supplies one.

That chain has several independent ways to fail, none of which may take a session
down with it. So every failure path here is *loud* (a block naming the exact
per-OS install command, the bash requirement, and the bypass) while callers keep
running degraded. The bypass exists so a broken dependency can never brick the
tool: ``AI_CLI_SKIP_DIRENV=1``, ``-D/--no-direnv``, or ``[direnv] enabled =
false`` in config.toml.
"""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

BYPASS_ENV = "AI_CLI_SKIP_DIRENV"

# Install candidates per sys.platform, in attempt order: (probe, argv).
# ``probe`` is the executable that must exist on PATH for the entry to apply.
# Only non-interactive invocations belong here — an installer that can block on a
# password or a UAC prompt would hang a session launch instead of failing it.
_INSTALLERS: dict[str, list[tuple[str, list[str]]]] = {
    "win32": [
        # scoop first: it is per-user and never raises UAC. winget is direnv's
        # documented Windows route but can prompt for elevation.
        ("scoop", ["scoop", "install", "direnv"]),
        (
            "winget",
            [
                "winget",
                "install",
                "--exact",
                "--id",
                "direnv.direnv",
                "--accept-package-agreements",
                "--accept-source-agreements",
                "--disable-interactivity",
            ],
        ),
        ("choco", ["choco", "install", "direnv", "-y"]),
    ],
    "darwin": [
        ("brew", ["brew", "install", "direnv"]),
    ],
    "linux": [
        ("apt-get", ["apt-get", "install", "-y", "direnv"]),
        ("dnf", ["dnf", "install", "-y", "direnv"]),
        ("pacman", ["pacman", "-S", "--noconfirm", "direnv"]),
        ("zypper", ["zypper", "--non-interactive", "install", "direnv"]),
    ],
}

# Manual commands to print when nothing could run unattended. Unlike
# _INSTALLERS these may need sudo/elevation, because a human runs them.
_MANUAL_HINTS: dict[str, tuple[str, ...]] = {
    "win32": (
        "winget install --exact --id direnv.direnv",
        "scoop install direnv",
        "choco install direnv",
    ),
    "darwin": (
        "brew install direnv",
        "sudo port install direnv",
    ),
    "linux": (
        "sudo apt install direnv        # Debian/Ubuntu",
        "sudo dnf install direnv        # Fedora/RHEL",
        "sudo pacman -S direnv          # Arch",
        "sudo zypper install direnv     # openSUSE",
        "curl -sfL https://direnv.net/install.sh | bash    # any Unix, no root",
    ),
}

# Shell hook lines, keyed by the shell name direnv itself uses.
_HOOKS: tuple[tuple[str, str, str], ...] = (
    ("bash", "~/.bashrc", 'eval "$(direnv hook bash)"'),
    ("zsh", "~/.zshrc", 'eval "$(direnv hook zsh)"'),
    ("fish", "~/.config/fish/config.fish", "direnv hook fish | source"),
    ("pwsh", "$PROFILE", 'Invoke-Expression "$(direnv hook pwsh)"'),
)


@dataclass(frozen=True)
class InstallResult:
    """Outcome of one bootstrap attempt.

    ``installed`` is the only success signal. ``tool`` names the package manager
    that ran (None when none applied), and ``detail`` carries the failure text
    worth surfacing — a non-zero installer's stderr, or why nothing ran.
    """

    installed: bool
    tool: str | None = None
    detail: str = ""


def is_bypassed(config: dict | None = None) -> bool:
    """True when the operator has explicitly opted out of direnv handling.

    Checked before any probe or install so a bypass also silences the warning,
    which is the point of it: an operator who has said "not on this host" should
    not keep being told.
    """
    if os.environ.get(BYPASS_ENV, "").strip().lower() not in ("", "0", "false", "no"):
        return True
    return (config or {}).get("direnv", {}).get("enabled", True) is False


def direnv_available() -> bool:
    """True when a direnv executable is on PATH."""
    return shutil.which("direnv") is not None


def bash_available() -> bool:
    """True when a bash is on PATH to evaluate ``.envrc``.

    Separate from :func:`direnv_available` because on Windows the two fail
    independently: winget can install direnv onto a host with no bash at all,
    and direnv would then be present but unable to evaluate anything.
    """
    return shutil.which("bash") is not None


def envrc_loads(directory: Path, timeout: int = 60) -> bool:
    """True when direnv can actually evaluate the ``.envrc`` applying to ``directory``.

    Probes with ``direnv export json`` run *in* ``directory``, which loads the
    .envrc and prints the resulting environment diff. It exits non-zero when the
    file is blocked or errors, which is exactly the question every caller asks.

    Replaces ``direnv exec <dir> true``, which is **broken on Windows and always
    reports failure there**: ``true`` is a shell builtin, Git for Windows ships no
    ``true.exe``, so direnv resolves the command against PATH, finds nothing, and
    exits 1 -- with the .envrc having loaded perfectly. Measured on a Windows
    host, on a directory whose .envrc was approved and loading cleanly::

        direnv: ai-cli-utils environment loaded
        direnv: error command 'true' not found on PATH '...'

    That false negative is the root cause of the endless "run direnv allow"
    nagging: it made every trust probe fail on Windows regardless of the actual
    approval state, so the worktree auto-approval refused to act and the launcher
    warned anyway.

    ``direnv status`` is not a substitute -- it exits 0 even for a blocked
    .envrc (measured: rc=0 on a deliberately unapproved file), so it cannot
    answer this question. ``export json`` returns rc=1 there, as required.
    """
    try:
        probe = subprocess.run(
            ["direnv", "export", "json"],
            capture_output=True,
            text=True,
            cwd=directory,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def find_envrc(start: Path) -> Path | None:
    """Return the nearest ``.envrc`` at or above ``start``, else None.

    direnv searches upward, so a repo without its own ``.envrc`` can still
    inherit one; checking only ``start`` would miss a usable environment.
    """
    try:
        resolved = start.resolve()
    except OSError:
        resolved = start
    for directory in (resolved, *resolved.parents):
        candidate = directory / ".envrc"
        if candidate.is_file():
            return candidate
    return None


def refresh_windows_path() -> bool:
    """Re-read the persisted Windows PATH into this process. True if it changed.

    A package manager writes the new directory to the registry, but a process
    that is already running keeps the environment block it started with -- which
    is why a freshly installed direnv is invisible until the shell is restarted,
    and the symptom that prompted this module ("direnv: not a command" when
    direnv was in fact installed).

    This process does not have to wait for a restart: the persisted value can be
    read back and merged in. A parent shell cannot be fixed from here -- no
    process can mutate another's environment -- so :func:`remediation` prints the
    per-shell one-liner for that instead.

    Machine PATH is placed before user PATH, matching how Windows itself
    composes them. Entries already present are preserved and not reordered, so a
    deliberately prepended directory keeps its precedence.
    """
    if sys.platform != "win32":
        return False
    try:
        import winreg
    except ImportError:
        return False

    persisted: list[str] = []
    for root, subkey in (
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER, "Environment"),
    ):
        try:
            with winreg.OpenKey(root, subkey) as key:
                value, _ = winreg.QueryValueEx(key, "Path")
        except OSError:
            continue
        persisted.extend(part for part in str(value).split(os.pathsep) if part)

    current = [part for part in os.environ.get("PATH", "").split(os.pathsep) if part]
    seen = {part.rstrip("\\").lower() for part in current}
    added = [part for part in persisted if part.rstrip("\\").lower() not in seen]
    if not added:
        return False
    os.environ["PATH"] = os.pathsep.join([*current, *added])
    return True


def _needs_root(argv: list[str]) -> bool:
    """True when ``argv`` is a system package manager and we are not root.

    Attempting one of these unprivileged either prompts for a password (hanging
    a launch) or fails on permissions, so they are skipped rather than tried.
    """
    if argv[0] not in ("apt-get", "dnf", "pacman", "zypper"):
        return False
    return getattr(os, "geteuid", lambda: 0)() != 0


def install_direnv(timeout: int = 300) -> InstallResult:
    """Attempt one unattended direnv install, returning the outcome.

    Never raises: a bootstrap helper that throws would take down the caller it
    exists to keep running. Tries each candidate for this platform in order and
    stops at the first that reports success.
    """
    candidates = _INSTALLERS.get(sys.platform, [])
    if not candidates:
        return InstallResult(False, detail=f"no unattended installer is known for platform {sys.platform!r}")

    skipped: list[str] = []
    attempted: list[str] = []
    for probe, argv in candidates:
        if shutil.which(probe) is None:
            continue
        if _needs_root(argv):
            skipped.append(f"{probe} (needs root; re-run with sudo or install manually)")
            continue
        attempted.append(probe)
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            skipped.append(f"{probe} ({type(exc).__name__})")
            continue
        # Trust the executable's own verdict, then confirm the binary really
        # landed: some managers exit 0 having only staged a pending install.
        # The PATH refresh must come first -- the installer wrote its directory
        # to the registry, not to this already-running process, so without it a
        # perfectly good install looks like a failure.
        if proc.returncode == 0:
            refresh_windows_path()
            if direnv_available():
                return InstallResult(True, tool=probe)
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        skipped.append(f"{probe} (exit {proc.returncode}: {detail[-1] if detail else 'no output'})")

    if not attempted and not skipped:
        managers = ", ".join(probe for probe, _ in candidates)
        return InstallResult(False, detail=f"none of these package managers are on PATH: {managers}")
    return InstallResult(False, detail="; ".join(skipped))


def remediation(envrc: Path | None = None, result: InstallResult | None = None) -> str:
    """Build the loud, actionable block shown when direnv is unusable.

    Deliberately verbose: this text is the entire remediation path for an
    operator who does not know what direnv is, so it names the failure, the
    exact command for *this* OS, the bash requirement, how to hook the shell,
    and how to carry on without direnv.
    """
    lines = ["", "=" * 72, "ai-cli-utils: direnv is not usable on this machine."]
    if envrc is not None:
        lines.append(f"  A project environment exists at {envrc}, so it will NOT be loaded.")
    if result is not None and result.detail:
        lines.append(f"  Auto-install did not succeed: {result.detail}")

    lines += ["", "  Install it with one of:"]
    lines += [
        f"      {hint}" for hint in _MANUAL_HINTS.get(sys.platform, ("see https://direnv.net/docs/installation.html",))
    ]
    # The single most common "it didn't work" report: the install succeeded but
    # the shell that runs `direnv` was started before it and kept the old PATH.
    # ai-cli-utils refreshes its OWN PATH after installing, so this is only about
    # the operator's interactive shell, which no other process can fix for it.
    if sys.platform == "win32":
        lines += [
            "",
            "  An already-running shell keeps the PATH it started with, so it can still",
            "  say 'direnv: command not found' after a good install. Refresh it in place:",
            "      PowerShell:  $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine')"
            + " + ';' + [Environment]::GetEnvironmentVariable('Path','User')",
            '      Git Bash:    export PATH="$PATH:/c/ProgramData/chocolatey/bin:$LOCALAPPDATA/Microsoft/WinGet/Links"',
            "  Or just open a new shell.",
        ]
    else:
        lines += [
            "",
            "  Then open a new shell (or re-source your shell profile) so PATH picks it up.",
        ]

    if sys.platform == "win32" and not bash_available():
        lines += [
            "",
            "  Windows also needs bash: direnv always evaluates .envrc in a bash",
            "  sub-process, and no bash was found on PATH. Install Git for Windows",
            "  (https://git-scm.com/download/win) to supply the bash direnv needs.",
        ]

    lines += ["", "  Then hook it into your shell (one line, in the file shown):"]
    lines += [f"      {shell:<5} {path:<32} {line}" for shell, path, line in _HOOKS]

    lines += [
        "",
        "  To keep using ai-cli-utils without direnv, bypass it with any of:",
        f"      {BYPASS_ENV}=1        (env var; PowerShell: $env:{BYPASS_ENV}='1')",
        "      ai c <n> -D                (per-launch flag)",
        "      [direnv] enabled = false   (config.toml, permanent)",
        "=" * 72,
        "",
    ]
    return "\n".join(lines)


def ensure_direnv(
    project_root: Path,
    config: dict | None = None,
    auto_install: bool = True,
) -> InstallResult:
    """Make direnv usable for ``project_root`` if it is wanted and missing.

    Returns the attempt's outcome; ``installed=True`` also covers "was already
    there". Prints the remediation block on any failure but never raises and
    never exits, so a caller mid-launch degrades instead of dying — the
    regression this module's whole contract exists to prevent.
    """
    if is_bypassed(config):
        return InstallResult(True, detail="bypassed")

    envrc = find_envrc(project_root)
    if envrc is None:
        # Nothing to load: do not install a tool that would have no work to do.
        return InstallResult(True, detail="no .envrc")

    have_direnv = direnv_available()
    if have_direnv and bash_available():
        return InstallResult(True, tool="already-present")

    result = install_direnv() if auto_install and not have_direnv else InstallResult(False, detail="")
    if result.installed and bash_available():
        print(f"ai-cli-utils: installed direnv via {result.tool}.", file=sys.stderr)
        return result

    print(remediation(envrc, result), file=sys.stderr)
    return InstallResult(False, tool=result.tool, detail=result.detail)
