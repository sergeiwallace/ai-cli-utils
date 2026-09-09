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
from dataclasses import dataclass

from . import native_deps
from .native_deps import Candidate, InstallResult, LoaderRepair, attempt_installs

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
    ``libevent_core-2.1.so.7`` after the container filesystem was rebuilt under
    it), which is why ``ai doctor`` reported ``OK tmux`` for a tmux that could
    not start a single session.

    This is THE predicate every tmux decision hangs on. Anything asking
    :func:`tmux_present` instead is asking whether a file exists, which the
    launcher already learned is a different question (AI-CLI-d89q).
    """
    if not tmux_present():
        return False
    return _probe_output(["tmux", "-V"], timeout) is not None


def repair_tmux_loader_path(timeout: int = 15) -> LoaderRepair:
    """Try to make a present-but-unrunnable tmux run, installing nothing.

    Delegates to the generic loader repair, which searches the directories
    around tmux's own install prefix for whatever shared library the loader
    said it could not find. On the machine this was written for, the library was
    sitting in a persistent directory one level below the same prefix the whole
    time — nothing needed installing, only pointing at.

    ``tmux -V`` creates no server, no session and no window, so running it twice
    (once to fail, once to prove the repair) mutates nothing.
    """
    return native_deps.repair_loader_path(["tmux", "-V"], timeout=timeout)


def install_tmux(timeout: int = 300) -> InstallResult:
    """Attempt one unattended tmux install, returning the outcome.

    Never raises. Verification re-probes by EXECUTING tmux rather than trusting
    the manager's exit status or the binary's mere presence: some managers exit 0
    having only staged an install, and a manager that lands a tmux which cannot
    load its own libraries has not given us a working tmux either (AI-CLI-d89q).
    """
    return attempt_installs(_INSTALLERS.get(sys.platform, []), verify=tmux_runs, timeout=timeout)


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
        "  If tmux is installed but cannot load a shared library, the library may",
        "  simply be somewhere the loader was not told to look. Name that directory",
        f"  once and every launch will use it:  export {native_deps.LIBRARY_PATH_OVERRIDE}=/path/to/lib",
        "",
        "  Or make bare mode permanent on this machine and silence this notice:",
        "      [session] use_tmux = false   in ~/.config/ai-cli-utils/config.toml",
        "=" * 72,
        "",
    ]
    return "\n".join(lines)


def ensure_tmux(auto_install: bool = True, quiet: bool = False) -> InstallResult:
    """Make tmux usable if it is not, and report whether it now is.

    Which route runs depends on WHY tmux is unusable, and the two are not
    interchangeable:

    * **Does tmux run?** Not "is it on PATH" — that conflation is the whole of
      AI-CLI-d89q, and it is what let a tmux missing a shared library report
      itself healthy through an entire launch.
    * **Absent: attempt one unattended install.** Unchanged; a package manager is
      the only thing that can produce a tmux that is not there.
    * **Present but unrunnable: repair the loader path, and nothing more.** Costs
      one extra ``tmux -V``, installs nothing, mutates nothing outside this
      process's environment, and is what actually fixes the measured failure.

    **A present-but-broken tmux is deliberately NOT sent to a package manager**
    (decided while fixing AI-CLI-i2ih). A manager cannot be verified to have
    fixed the tmux that will actually run: the broken binary keeps its place on
    PATH, so a freshly installed one elsewhere leaves ``tmux_runs()`` False and
    the launch has paid the manager's full timeout — up to five minutes, on every
    launch, forever — to change nothing. It is also a machine mutation nobody
    asked for, installing a second tmux over one the operator placed by hand. The
    honest answer for that case is the loud remediation below, which names the
    missing library and the directory override that fixes it.

    ``installed=True`` also covers "was already there". Prints the remediation
    notice on failure but never raises and never exits: the caller is mid-launch
    and must be free to continue in bare mode, which is what a False result
    tells it to do.
    """
    if tmux_runs():
        return InstallResult(True, tool="already-present")

    if tmux_present():
        # Repair runs even under ``auto_install=False``. That flag declines to
        # touch the machine's packages; a loader path installs nothing, so it is
        # not what was refused.
        repair = repair_tmux_loader_path()
        if repair.repaired:
            # Composed from the structured fields rather than passing
            # ``repair.detail`` through, so what a caller reports cannot go blank
            # on a repair that happened to leave its free-text field empty.
            summary = f"{', '.join(repair.missing)} resolved from {', '.join(repair.added_dirs)} via {repair.variable}"
            if not quiet:
                print(f"ai-cli-utils: repaired tmux — {summary}.", file=sys.stderr)
            return InstallResult(True, tool="loader-path", detail=summary)

        if not repair.missing:
            # tmux did not answer a version query, and also reported no loader
            # problem. That is NOT evidence it cannot host a session -- an
            # unexpected `-V` output shape produces exactly this state -- so the
            # caller is told "could not confirm", not "unusable", and no
            # remediation is printed for a fault nobody has established.
            return InstallResult(False, detail=repair.detail or "tmux did not report a version")

        failure = InstallResult(False, detail=repair.detail, unusable=True)
        if not quiet:
            print(remediation(failure), file=sys.stderr)
        return failure

    result = install_tmux() if auto_install else InstallResult(False, detail="auto-install not attempted")
    if result.installed:
        if not quiet:
            print(f"ai-cli-utils: installed tmux via {result.tool}.", file=sys.stderr)
        return result

    if not quiet:
        print(remediation(result), file=sys.stderr)
    # Absent from PATH and not installed: positively established, unlike the
    # ambiguous case above.
    return InstallResult(False, tool=result.tool, detail=result.detail, unusable=True)


@dataclass(frozen=True)
class TmuxReport:
    """What a launch actually established about tmux on this machine.

    ``client_version`` and ``server_version`` are deliberately separate fields
    rather than one "tmux version". They are two different processes: a running
    server keeps its own version until every session on it exits, so they
    disagree for the whole duration of an upgrade — and any consumer that parses
    tmux's output is answered by the SERVER. Collapsing them into one number is
    how a mixed install reads as a working one.
    """

    present: bool
    path: str | None = None
    client_version: str | None = None
    server_version: str | None = None
    # False when the caller asked for no version query at all, which is a
    # different state from "asked and got no answer": a bare launch must reach no
    # tmux process whatsoever, so it cannot report a version and must not imply
    # the binary is broken by printing that it has none.
    versions_probed: bool = True

    @property
    def runs(self) -> bool:
        """Present AND able to report its own version. Not the same as present."""
        return self.client_version is not None

    @property
    def versions_disagree(self) -> bool:
        """True only when both are known and differ.

        No running server is the normal state of a fresh machine, so an unknown
        server version is not a mismatch — claiming one would make the common
        case look broken.
        """
        return (
            self.client_version is not None
            and self.server_version is not None
            and self.client_version != self.server_version
        )


def _probe_output(argv: list[str], timeout: int) -> str | None:
    """Run one bounded probe, returning stripped stdout or None. Never raises."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    value = (proc.stdout or "").strip()
    return value or None


def probe(timeout: int = 10, *, query_versions: bool = True) -> TmuxReport:
    """Detect tmux without changing anything. A diagnostic must never block a launch.

    Every failure mode collapses to a missing field rather than an exception: a
    binary on PATH that dies on a missing shared library, a hung invocation, no
    running server. Each is a real state this fleet has hit.

    ``query_versions=False`` reports presence alone and spawns nothing. A bare
    launch must not invoke tmux at all — not even to ask its version — because
    bare mode is precisely the mode that has decided tmux is not involved.
    """
    # Presence through this module's own predicate, not a second `shutil.which`
    # call: one source for "is there a tmux" means the launcher and the report can
    # never disagree, and a caller that has already decided tmux is absent is not
    # made to spawn a probe anyway. A bare launch on a machine that happens to
    # have tmux must still reach no tmux process at all.
    if not tmux_present():
        return TmuxReport(present=False, versions_probed=query_versions)
    path = shutil.which("tmux")
    if not query_versions:
        return TmuxReport(present=True, path=path, versions_probed=False)

    client = _probe_output(["tmux", "-V"], timeout)
    if client is not None:
        # `tmux -V` prints "tmux 3.7c"; keep only the version token.
        parts = client.split()
        client = parts[-1] if parts else None

    server = _probe_output(["tmux", "display-message", "-p", "#{version}"], timeout)

    return TmuxReport(present=True, path=path, client_version=client, server_version=server)


def report_lines(
    *,
    report: TmuxReport,
    bare: bool,
    reason: str,
    auto_installed: str | None = None,
) -> list[str]:
    """The launch-time tmux block, as lines.

    Answers the operator's actual questions in order: is this session inside
    tmux, which tmux, and did anything get installed on my machine just now.
    """
    lines: list[str] = []
    if auto_installed:
        lines.append(f"ai-cli: tmux was auto-installed via {auto_installed}.")

    if not report.present:
        lines.append(f"ai-cli: tmux not found on PATH — launching bare ({reason}).")
        return lines

    if not report.versions_probed:
        # Presence only. Saying "version unavailable" here would report a broken
        # binary when nothing was ever asked.
        lines.append(f"ai-cli: tmux found at {report.path} (version not queried)")
        lines.append(f"ai-cli: launching bare, not under tmux ({reason}).")
        return lines

    detail = report.client_version or "version unavailable (binary does not run)"
    lines.append(f"ai-cli: tmux {detail} at {report.path}")

    if report.server_version:
        lines.append(f"ai-cli: running tmux server reports {report.server_version}")
    if report.versions_disagree:
        lines.append(
            f"ai-cli: WARNING — client {report.client_version} but the running "
            f"server is {report.server_version}. The server answers every "
            f"format query, so it decides compatibility; relaunch every session "
            f"to converge."
        )

    if bare:
        lines.append(f"ai-cli: launching bare, not under tmux ({reason}).")
    else:
        lines.append(f"ai-cli: launching inside tmux ({reason}).")
    return lines


def config_opts_out(config: dict | None = None) -> bool:
    """True when ``[session] use_tmux = false`` opts this machine out of tmux.

    The config setting is checked before any probe or install, so a machine that
    has said "bare here" is never made to wait on a package manager.
    """
    return (config or {}).get("session", {}).get("use_tmux", True) is False
