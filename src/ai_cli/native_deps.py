"""Shared machinery for the native dependencies pip/uv can never supply.

``direnv`` and ``tmux`` are both C binaries: declaring them in ``[dependencies]``
is impossible, so each one is detected at run time and, where a package manager
can do it unattended, installed. The two bootstrappers differ in what they probe
and what they print, but the install attempt itself is the same loop, so it lives
here rather than being written twice.

Everything in this module is non-raising by contract. Its callers run mid-launch,
and a bootstrap helper that throws would take down the session it exists to keep
running.
"""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

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
    """

    installed: bool
    tool: str | None = None
    detail: str = ""


def needs_root(argv: list[str]) -> bool:
    """True when ``argv`` is a system package manager and we are not root."""
    if argv[0] not in _ROOT_MANAGERS:
        return False
    return getattr(os, "geteuid", lambda: 0)() != 0


def attempt_installs(
    candidates: list[Candidate],
    verify: Callable[[], bool],
    timeout: int = 300,
    before_verify: Callable[[], object] | None = None,
) -> InstallResult:
    """Try each candidate for this platform in order; stop at the first success.

    ``verify`` re-probes for the tool itself, because a manager's exit status is
    not proof: some exit 0 having only staged a pending install. ``before_verify``
    runs between a zero exit and that re-probe, for the Windows PATH refresh —
    the installer wrote its directory to the registry, not to this already
    running process, so without it a perfectly good install looks like a failure.
    """
    if not candidates:
        return InstallResult(False, detail=f"no unattended installer is known for platform {sys.platform!r}")

    skipped: list[str] = []
    attempted: list[str] = []
    for probe, argv in candidates:
        if shutil.which(probe) is None:
            continue
        if needs_root(argv):
            skipped.append(f"{probe} (needs root; re-run with sudo or install manually)")
            continue
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
