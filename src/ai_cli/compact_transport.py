"""Resolve the transport that can submit the auto-compact prompt into a session.

Depends on: nothing outside the stdlib.

A session's auto-compact prompt is submitted through the terminal that OWNS the
engine process, and ``/compact`` is a REPL-only built-in: no API call, hook return
value or SDK entry point starts a compaction. A running process's controlling
terminal cannot be relocated into a terminal it does not already own, so **the
transport is decided at launch or not at all** -- nothing a supervisor does
afterwards can add one.

Two transports satisfy that, and either is enough:

* **tmux**, the default and preferred one where tmux can host the session. It
  already carries the injection via ``send-keys`` and owns resize, signals,
  scrollback and detach/reattach correctly. Nothing here changes a tmux launch.
* **A harness-owned pty**, the fallback on the bare paths (``-b/--bare``,
  ``[session] use_tmux = false``, tmux unusable), for machines where tmux cannot be
  installed. Writing to the pty MASTER is an unprivileged submission path: measured
  on Darwin, ``TIOCSTI`` on a pty we own is ``errno=13 EACCES`` while a parent write
  arrived at the child's stdin intact. ``TIOCSTI`` is therefore never used.

THREE OUTCOMES, NOT TWO, and the third is the one that matters for portability.

1. A transport resolves -- the launch proceeds.
2. A transport is achievable on this platform but was WITHHELD: ``use_pty = false``
   with no tmux, or a pty that would not allocate. The operator either asked for
   tmux-or-nothing or has a real fault, so the launch is REFUSED at launch time,
   before anything is created.
3. No harness-owned transport is IMPLEMENTABLE on this platform at all. ``os.openpty``
   is POSIX-only, so on Windows the pty fallback cannot exist, and a Windows host
   without tmux can satisfy neither side however it is configured.

Case 3 must not refuse, and the reason is not politeness. A guard whose condition no
in-process action can satisfy is not a guard, it is a brick: refusing there would
strand the launch route on every Windows host, which reintroduces the resolved P1
``AI-CLI-vs8`` ("Windows: ai c hard-exits when tmux is absent instead of falling
back to bare mode"). It would also be a platform-parity regression -- the launcher's
contract is that a missing *enhancement* degrades a launch and never blocks it.

So case 3 proceeds and says so once, naming the upgrade path. That is not a silent
degradation, which is the failure this whole requirement exists to kill: what the
operator loses is harness-DRIVEN compaction (the harness choosing when to compact,
so it can checkpoint first), not compaction itself. The engine's own native
auto-compact needs no pane, no keystroke and no terminal, and still applies. tmux
runs on Windows under MSYS2/Cygwin/WSL, so the upgrade is real and reachable; a
native Windows pseudo-console (ConPTY, Windows 10 1809+) would also serve but needs
a dependency this package does not carry, tracked as ``AI-CLI-ysht``.

SCOPE, stated rather than implied. This module answers whether a transport CAN be
established -- a necessary condition, resolved before anything is created. It does
not host a session on a pty; that is ``AI-CLI-0nvi``. When it does, the pty is
allocated to CARRY THE SUBMISSION, not to host the session's UI: the operator's own
terminal stays the terminal, so SIGWINCH/resize and Ctrl-C keep reaching the engine
from there, and the pty path deliberately implements NO detach/reattach and NO
scrollback. Those are the reasons tmux exists and tmux remains the way to get them.
"""

import os
import select
from dataclasses import dataclass

#: Written to the pty master and expected back out of the slave. The probe asserts
#: what the slave READ rather than that the write returned a byte count, because a
#: successful write is not a successful delivery.
#:
#: The trailing newline is load-bearing, not formatting: a pty is in canonical mode,
#: so the terminal driver releases nothing to the slave until a line terminator
#: arrives. It is also the right shape for the real submission, which is a line.
_PROBE_MARKER = b"ai-cli-compact-transport-probe\n"


@dataclass(frozen=True)
class PtyProbe:
    """Whether this machine can give the harness a pty it owns, and why not.

    *platform_supported* separates "this platform has no pty API" from "it could
    have worked here and did not". Only the latter is the operator's problem to fix,
    and only the latter may refuse a launch.
    """

    usable: bool
    detail: str
    platform_supported: bool = True


def probe_pty(timeout: float = 2.0) -> PtyProbe:
    """Allocate a pty, carry the marker through it, and report what came back.

    Deliberately a round trip rather than a feature check. ``hasattr(os, "openpty")``
    says the function exists; it does not say the kernel will hand out a pty under
    the current sandbox, file-descriptor limit or container configuration, and a
    launch that degraded to a transport which then could not be allocated would
    reproduce the late silent failure this guard exists to prevent.

    Echo is turned off on the slave first: with echo on, the terminal driver writes
    the marker back to the master as well, so a probe that only checked "something
    came back" could pass on its own echo without the slave ever reading anything.
    """
    try:
        import termios
    except ImportError:  # pragma: no cover - POSIX-only module, absent on Windows
        return PtyProbe(
            False,
            "this platform has no termios module, so a pty cannot be configured",
            platform_supported=False,
        )

    if not hasattr(os, "openpty"):
        return PtyProbe(
            False,
            "this platform has no os.openpty(), so a pty cannot be allocated (it is POSIX-only)",
            platform_supported=False,
        )
    # The launched child has to become a session leader and claim the pty as its
    # controlling terminal, which is what makes the terminal ours to write into.
    # Without both of these the pty would be an ordinary pipe pair and the engine
    # would not treat it as a tty.
    if not hasattr(os, "setsid"):
        return PtyProbe(
            False,
            "this platform has no os.setsid(), so a child cannot claim a pty as its terminal",
            platform_supported=False,
        )
    if not hasattr(termios, "TIOCSCTTY"):
        return PtyProbe(
            False,
            "this platform has no termios.TIOCSCTTY, so a child cannot claim a pty as its terminal",
            platform_supported=False,
        )

    master = slave = -1
    try:
        master, slave = os.openpty()
        attrs = termios.tcgetattr(slave)
        attrs[3] &= ~termios.ECHO
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
        os.write(master, _PROBE_MARKER)
        readable, _, _ = select.select([slave], [], [], timeout)
        if not readable:
            return PtyProbe(
                False,
                f"a pty was allocated but nothing arrived at its slave within {timeout:g}s, "
                "so the submission path is not carrying input",
            )
        arrived = os.read(slave, len(_PROBE_MARKER))
        if arrived != _PROBE_MARKER:
            return PtyProbe(
                False,
                f"a pty was allocated but the slave read {arrived!r} instead of the marker written "
                "to the master, so the submission path is corrupting input",
            )
    except OSError as exc:
        return PtyProbe(False, f"allocating a pty failed: {exc}")
    finally:
        for fd in (master, slave):
            if fd >= 0:
                os.close(fd)
    return PtyProbe(True, "a harness-owned pty can be allocated and carries the injection")


def config_opts_out(config: dict | None = None) -> bool:
    """True when ``[session] use_pty = false`` opts this machine out of the pty fallback.

    The lever exists so the submission transport can be deliberately disabled -- the
    negative control for every test of this guard, and the way an operator who wants
    tmux-or-nothing says so. It is read before the probe, so a machine that has
    already refused the fallback never pays for allocating a pty to find out.
    """
    return (config or {}).get("session", {}).get("use_pty", True) is False


@dataclass(frozen=True)
class CompactTransport:
    """The resolved submission transport for one launch, and the reason for each.

    *kind* is ``"tmux"``, ``"pty"`` or ``None``. Both reasons are carried whichever
    way it resolves, because the message has to name what was unavailable and why --
    an operator told only "no transport" cannot tell a config opt-out from a missing
    binary from a platform that has no pty API.

    *platform_limited* is the case-3 split: no transport, and none was achievable
    here. It proceeds with an advisory instead of refusing.
    """

    kind: str | None
    tmux_detail: str
    pty_detail: str
    platform_limited: bool = False

    @property
    def usable(self) -> bool:
        return self.kind is not None

    @property
    def must_refuse(self) -> bool:
        """Refuse only when a transport was achievable here and is not available."""
        return self.kind is None and not self.platform_limited

    @property
    def detail(self) -> str:
        if self.kind == "tmux":
            return f"tmux ({self.tmux_detail})"
        if self.kind == "pty":
            return f"harness-owned pty ({self.pty_detail})"
        return "none"


def resolve(
    *,
    tmux_usable: bool,
    tmux_detail: str,
    config: dict | None = None,
    probe=None,
) -> CompactTransport:
    """Pick the transport for this launch, preferring tmux.

    *tmux_usable* is the launcher's already-resolved answer to "will tmux host this
    session" -- the end of its own preflight, after the config opt-out, the
    runnability probe, the unattended install and the format-expansion check. This
    function never re-derives it: asking tmux a second time could disagree with the
    answer the rest of the launch is built on.

    The pty is probed only when tmux cannot host the session, so a tmux launch
    allocates nothing.

    *probe* is resolved HERE rather than as a default argument. A default would bind
    this module's ``probe_pty`` at definition time, so patching the module attribute
    would be silently ignored -- the override would look applied and do nothing.
    """
    probe = probe_pty if probe is None else probe
    if tmux_usable:
        return CompactTransport("tmux", tmux_detail, "not probed: tmux hosts this session")
    if config_opts_out(config):
        # An explicit opt-out is a refusal even on a platform that has no pty: the
        # operator asked for tmux-or-nothing, and honouring that is not a platform
        # limitation. Probing first would also allocate a pty nobody asked for.
        return CompactTransport(None, tmux_detail, "[session] use_pty = false in config.toml")
    pty = probe()
    if pty.usable:
        return CompactTransport("pty", tmux_detail, pty.detail)
    return CompactTransport(
        None,
        tmux_detail,
        pty.detail,
        platform_limited=not pty.platform_supported,
    )


def refusal_message(transport: CompactTransport) -> str:
    """The launch-time refusal, naming both transports and what each failed on.

    Deliberately does not claim the session could never be compacted: the engine's
    own native auto-compact needs no terminal and still applies. What is unavailable
    is harness-DRIVEN compaction, which is what lets the harness checkpoint first
    and compact on its own schedule.
    """
    return (
        "this session has no way for the harness to submit its auto-compact prompt, "
        "so it is not being started.\n"
        f"  tmux: unavailable — {transport.tmux_detail}\n"
        f"  harness-owned pty: unavailable — {transport.pty_detail}\n"
        "  A session's submission transport is fixed by the terminal that owns it and "
        "cannot be added later, so\n"
        "  the harness could never compact this session on its own schedule or "
        "checkpoint it first.\n"
        "  Fix either side:\n"
        "    - install tmux (the preferred transport: it also gives you detach/reattach, "
        "`ai ls` and `ai attach`), or\n"
        "    - allow the pty fallback by removing `[session] use_pty = false` from "
        "~/.config/ai-cli-utils/config.toml."
    )


def advisory_message(transport: CompactTransport) -> str:
    """The one-line notice for a platform where no transport is implementable.

    Said once, at launch, rather than left silent: a session whose compaction the
    harness cannot drive is exactly the thing an operator should not discover hours
    later. It is not a refusal, because no in-process action could satisfy it.
    """
    return (
        "the harness cannot drive compaction for this session — "
        f"{transport.pty_detail}, and tmux is not hosting it ({transport.tmux_detail}). "
        "The engine's own auto-compact still applies, so the session is not uncompactable; "
        "the harness just cannot checkpoint first or choose when. Install tmux to get the "
        "managed path (on Windows it runs under MSYS2/Cygwin/WSL)."
    )
