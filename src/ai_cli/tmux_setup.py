"""Cross-platform tmux bootstrap: detect, install, else fall back to bare mode.

tmux buys detach/reattach (``ai ls``, ``ai attach``), sessions that survive a
dropped SSH connection, and remote access from another device. It is the default
session mode for exactly those reasons — but it is an *enhancement*, never a
launch precondition, so nothing here may abort a launch. The resolution order the
launcher implements on top of this module is:

1. ``[session] use_tmux`` in config.toml wins outright, on or off.
2. With no setting, tmux is the default rather than something to opt into.
3. A missing tmux is installed unattended where a package manager can do it.
4. Still missing: fall back to bare mode with a notice. Never fatal.

Step 4 is the reason this module exists. tmux is a C binary, so ``libtmux`` in
``[dependencies]`` supplies only the client library and pip/uv can never install
the binary itself; before this, a non-Windows host without tmux was told to
install it and exited 1, which made a missing enhancement block every launch.

Windows has no native tmux at all — the real thing runs under WSL, MSYS2, or
Cygwin — so there are deliberately no unattended candidates for ``win32`` and
bare mode is the correct permanent answer there.
"""

import shutil
import subprocess
import sys

from .native_deps import Candidate, InstallResult, attempt_installs

# Unattended install candidates per sys.platform, in attempt order. Rootless
# managers come first: an unprivileged host is the common case for the machines
# that need this most (a SageMaker space, a locked-down corporate box), and
# native_deps.needs_root skips the system managers there rather than hanging on
# a password prompt.
_INSTALLERS: dict[str, list[Candidate]] = {
    # No entry for "win32" on purpose: no package manager ships a native tmux,
    # so an empty candidate list produces the honest "no unattended installer"
    # result and the launcher degrades to bare.
    "darwin": [
        ("brew", ["brew", "install", "tmux"]),
        ("conda", ["conda", "install", "-y", "-c", "conda-forge", "tmux"]),
    ],
    "linux": [
        ("micromamba", ["micromamba", "install", "-y", "-c", "conda-forge", "tmux"]),
        ("conda", ["conda", "install", "-y", "-c", "conda-forge", "tmux"]),
        ("brew", ["brew", "install", "tmux"]),
        ("apt-get", ["apt-get", "install", "-y", "tmux"]),
        ("dnf", ["dnf", "install", "-y", "tmux"]),
        ("pacman", ["pacman", "-S", "--noconfirm", "tmux"]),
        ("zypper", ["zypper", "--non-interactive", "install", "tmux"]),
    ],
}

# Manual commands to print when nothing could run unattended. Unlike
# _INSTALLERS these may need sudo/elevation, because a human runs them.
_MANUAL_HINTS: dict[str, tuple[str, ...]] = {
    "win32": (
        "wsl --install                       # then run tmux inside WSL",
        "pacman -S tmux                      # inside MSYS2",
        "(there is no native Windows tmux; bare mode is the right answer here)",
    ),
    "darwin": ("brew install tmux",),
    "linux": (
        "sudo apt install tmux               # Debian/Ubuntu",
        "sudo dnf install tmux               # Fedora/RHEL",
        "sudo pacman -S tmux                 # Arch",
        "sudo zypper install tmux            # openSUSE",
        "conda install -c conda-forge tmux   # any Linux, no root",
    ),
}


def tmux_present() -> bool:
    """True when a tmux executable is on PATH.

    Presence only. A tmux that resolves but cannot run is a distinct state —
    see :func:`tmux_runs`.
    """
    return shutil.which("tmux") is not None


def tmux_runs(timeout: int = 10) -> bool:
    """True when ``tmux -V`` actually executes.

    Presence on PATH is not the same as working. A hand-placed or half-installed
    build can resolve and then die on a missing shared library (measured on a
    SageMaker space: ``tmux`` on PATH, ``tmux -V`` exiting 127 for want of
    ``libutempter.so.0``), which is why ``ai doctor`` reported ``OK tmux`` for a
    tmux that could not start a single session.
    """
    if not tmux_present():
        return False
    try:
        proc = subprocess.run(["tmux", "-V"], capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def install_tmux(timeout: int = 300) -> InstallResult:
    """Attempt one unattended tmux install, returning the outcome.

    Never raises. Verification re-probes for the binary rather than trusting the
    manager's exit status, because some exit 0 having only staged an install.
    """
    return attempt_installs(_INSTALLERS.get(sys.platform, []), verify=tmux_present, timeout=timeout)


def remediation(result: InstallResult | None = None) -> str:
    """Build the notice shown when tmux is unusable and bare mode takes over.

    Loud but not alarming: bare mode is a working session, so this states what
    was lost, the exact command for *this* OS, and how to make bare permanent —
    the last line matters, because an operator who has chosen bare should not
    keep being told.
    """
    lines = ["", "=" * 72, "ai-cli-utils: tmux is not usable here — launching in bare mode instead."]
    if result is not None and result.detail:
        lines.append(f"  Auto-install did not succeed: {result.detail}")
    lines += [
        "",
        "  Bare mode runs the engine directly. What it costs you:",
        "    - detach/reattach (ai ls, ai attach)",
        "    - a session that survives a dropped SSH connection",
        "    - reaching the session from another device",
        "",
        "  Install tmux with one of:",
    ]
    lines += [f"      {hint}" for hint in _MANUAL_HINTS.get(sys.platform, ("install tmux from your package manager",))]
    lines += [
        "",
        "  Then open a new shell so PATH picks it up.",
        "",
        "  Or make bare mode permanent on this machine and silence this notice:",
        "      [session] use_tmux = false   in ~/.config/ai-cli-utils/config.toml",
        "=" * 72,
        "",
    ]
    return "\n".join(lines)


def ensure_tmux(auto_install: bool = True, quiet: bool = False) -> InstallResult:
    """Make tmux usable if it is missing, and report whether it now is.

    ``installed=True`` also covers "was already there". Prints the remediation
    notice on failure but never raises and never exits: the caller is mid-launch
    and must be free to continue in bare mode, which is what a False result
    tells it to do.
    """
    if tmux_present():
        return InstallResult(True, tool="already-present")

    result = install_tmux() if auto_install else InstallResult(False, detail="auto-install not attempted")
    if result.installed:
        if not quiet:
            print(f"ai-cli-utils: installed tmux via {result.tool}.", file=sys.stderr)
        return result

    if not quiet:
        print(remediation(result), file=sys.stderr)
    return InstallResult(False, tool=result.tool, detail=result.detail)


def config_opts_out(config: dict | None = None) -> bool:
    """True when ``[session] use_tmux = false`` opts this machine out of tmux.

    The config setting is checked before any probe or install, so a machine that
    has said "bare here" is never made to wait on a package manager.
    """
    return (config or {}).get("session", {}).get("use_tmux", True) is False
