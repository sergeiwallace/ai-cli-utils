"""VPN-aware transport switching for remote sessions.

Depends on: config.py, messaging.py, process_manager.py (lazy).
"""

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .config import get_xdg_state_home

# Module-level alias so tests can patch _monotonic without affecting asyncio internals
_monotonic = time.monotonic

#: Terminal modes a remote ``tmux`` or agent turns ON for the duration of a session and
#: turns back OFF only on its own clean exit. A dropped connection never gives it that
#: chance, so the LOCAL terminal keeps them and starts reading ordinary input as protocol:
#: mouse motion arrives as a typed ``CSI < 35 ; 66 ; 6 M``, a paste arrives wrapped in
#: ``200~``/``201~``, and switching windows emits a stray ``I`` or ``O``. The operator's
#: only recourse was killing the terminal (AI-CLI-w679).
#:
#: The whole set goes out unconditionally rather than being tracked, because the disable
#: form is idempotent and harmless for a mode that was never set -- and tracking would
#: require knowing what the remote did, which is exactly what a dropped connection denies.
#: Tracking modes are disabled BEFORE their coordinate encodings: clearing the encoding
#: first can leave a report already in flight to be decoded the old way.
_TERMINAL_RESTORE = (
    "\x1b[?1000l"  # mouse: X10/normal tracking
    "\x1b[?1002l"  # mouse: button-event tracking
    "\x1b[?1003l"  # mouse: any-event (motion) tracking
    "\x1b[?1005l"  # mouse: UTF-8 coordinate encoding
    "\x1b[?1006l"  # mouse: SGR coordinate encoding -- the one in the report
    "\x1b[?1015l"  # mouse: urxvt coordinate encoding
    "\x1b[?1004l"  # focus in/out reporting
    "\x1b[?2004l"  # bracketed paste
    "\x1b[?1049l"  # leave the alternate screen
    "\x1b[?7h"  # re-enable autowrap
    "\x1b[?25h"  # show the cursor
    "\x1b[>4;0m"  # xterm modifyOtherKeys off
    "\x1b[<u"  # pop the kitty keyboard-protocol stack
    "\x1b[0m"  # reset colour and attribute state
)


def restore_terminal(stream=None) -> None:
    """Undo the terminal modes a remote session left set, on the way out.

    Writes nothing unless *stream* is a real terminal. These are control sequences, so a
    redirected or piped run would otherwise corrupt its own output with them -- and a run
    that is not attached to a terminal has no terminal state to repair in the first place.
    """
    stream = sys.stdout if stream is None else stream
    try:
        if not stream.isatty():
            return
        stream.write(_TERMINAL_RESTORE)
        stream.flush()
    except (OSError, ValueError):
        # A stream already closed or detached by the time we unwind is not worth raising
        # over: the session has ended and there is no longer anything to protect.
        pass


#: ssh exit codes that mean the OPERATOR ended the session, so reconnecting would fight the
#: user rather than help. 0 is a clean detach or a finished remote command; 130 is SIGINT.
#: Everything else -- 255 above all, which is ssh's own "connection failed or was lost" --
#: is a transport failure, and the remote tmux session is still sitting there detached.
_SSH_OPERATOR_EXIT_CODES = frozenset({0, 130})


def run_ssh_with_reconnect(
    ssh_args: list[str],
    cleanup_cmd: list[str],
    max_attempts: int = 10,
    backoff_seconds: float = 2.0,
    max_backoff_seconds: float = 30.0,
    on_clean_exit: Callable[[], None] | None = None,
) -> int:
    """Run the interactive SSH session, reattaching if the connection drops.

    The pure-SSH transport previously ``execvp``'d a single ``ssh`` and therefore ended the
    moment that connection died, even though **nothing on the remote side had ended**: the
    session runs under ``tmux`` there, so a dropped link leaves it detached and intact and
    reattaching costs nothing and loses nothing. The mosh path already loops; this path did
    not, which is why a transport blip ended the operator's session outright (AI-CLI-w679).

    This is deliberately indifferent to *why* the link dropped. The measured environment
    reaches its host through a ``ProxyCommand`` with ``ControlPersist`` in play, and an
    intermediary's idle timeout, its absolute session cap and a genuine network drop are
    not distinguishable from this side -- so reattaching covers all three, where tuning a
    keepalive covers only the first.

    Returns ssh's last exit code. Reconnects are bounded and backed off so a host that is
    genuinely gone produces a handful of attempts rather than an infinite loop.

    ``on_clean_exit`` runs once when ssh exited 0, the one status that means the remote
    session itself ended. Every other way out (130, a dropped link, giving up) leaves the
    remote session presumed alive and does not call it.
    """
    attempt = 0
    delay = backoff_seconds
    returncode = 0
    try:
        while True:
            returncode = subprocess.call(ssh_args)
            if returncode in _SSH_OPERATOR_EXIT_CODES:
                break
            attempt += 1
            if attempt >= max_attempts:
                print(
                    f"\nConnection lost (ssh exit {returncode}) and {max_attempts} reconnect "
                    "attempts did not restore it — giving up. The remote session is most "
                    "likely still running; reattach with the same command.",
                    file=sys.stderr,
                )
                break
            # Restore before printing, so the notice is readable even when the drop left
            # the terminal in a remote application's input modes.
            restore_terminal()
            print(
                f"\nConnection lost (ssh exit {returncode}) — reattaching in {delay:.0f}s "
                f"(attempt {attempt}/{max_attempts})...",
                file=sys.stderr,
            )
            time.sleep(delay)
            delay = min(delay * 2, max_backoff_seconds)
        if returncode == 0 and on_clean_exit is not None:
            on_clean_exit()
    finally:
        restore_terminal()
        subprocess.run(cleanup_cmd, capture_output=True, check=False)
    return returncode


def _is_vpn_active() -> bool:
    """Return True if a VPN (Mullvad or any tunnel interface) is currently active.

    Checks Mullvad CLI first (fast, authoritative). Falls back to scanning
    network interfaces for active tunnel devices (utun*, tun*) which covers
    other VPN clients.  Returns False on any error so mosh is never
    blocked by a detection failure.
    """
    try:
        mullvad = shutil.which("mullvad")
        if mullvad:
            result = subprocess.run(
                ["mullvad", "status"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            return "Connected" in result.stdout
        # Fallback: check for active tunnel interfaces via ifconfig
        result = subprocess.run(
            ["ifconfig"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        import re as _re

        # Look for utun/tun interfaces that have an inet address (i.e. are up)
        iface_blocks = _re.split(r"^(\S+)", result.stdout, flags=_re.MULTILINE)
        for i in range(1, len(iface_blocks) - 1, 2):
            name = iface_blocks[i]
            body = iface_blocks[i + 1]
            if name.startswith(("utun", "tun")) and "inet " in body:
                return True
        return False
    except Exception:
        return False


def _write_transport_state(
    path: Path,
    session: str,
    parent_pid: int,
    child_pid: int,
    transport: str,
) -> None:
    """Write transport state JSON for this session. Cleaned up in finally block."""
    state = {
        "parent_pid": parent_pid,
        "child_pid": child_pid,
        "transport": transport,
        "session": session,
        "started_at": datetime.now(UTC).isoformat(),
    }
    path.write_text(json.dumps(state))


def _ensure_vpn_watcher(config: dict) -> None:
    """Start the vpn-watch Circus watcher if not already running.

    Called by the first ``ai c -R`` session before entering the transport loop.
    Subsequent sessions see existing transport-*.json files and skip this.
    """
    state_dir = get_xdg_state_home()
    # If other transport sessions exist they already started the watcher
    if list(state_dir.glob("transport-*.json")):
        return
    try:
        from .process_manager import _ensure_circusd

        endpoint = _ensure_circusd()
        from circus.client import CircusClient

        client = CircusClient(endpoint=endpoint, timeout=5.0)
        # Check if already running
        try:
            result = client.send_message("status")
            statuses = result.get("statuses", {}) if isinstance(result, dict) else {}
            if "vpn-watch" in statuses:
                return
        except Exception:
            pass  # Not covered: requires CircusClient.send_message to raise mid-call
        ai_bin = shutil.which("ai") or "ai"
        client.send_message(
            "add",
            name="vpn-watch",
            cmd=f"{ai_bin} vpn-watch",
            options={
                "copy_env": True,
                "respawn": True,
                "singleton": True,
                "autostart": True,
            },
            start=True,
        )
    except Exception:
        pass  # Non-fatal — transport loop still works without watcher


def _maybe_stop_vpn_watcher() -> None:
    """Stop the vpn-watch Circus watcher if no transport sessions remain."""
    state_dir = get_xdg_state_home()
    if list(state_dir.glob("transport-*.json")):
        return  # Other sessions still active
    try:
        endpoint = f"ipc://{state_dir}/circus.endpoint"
        from circus.client import CircusClient

        CircusClient(endpoint=endpoint, timeout=2.0).send_message("rm", name="vpn-watch")
    except Exception:
        pass


async def _ensure_tailscale_up(host: str, timeout: int = 20) -> bool:
    """Try to start Tailscale and wait for *host* to become TCP-reachable.

    On macOS, checks whether Tailscale.app is already running:
    - If running but host unreachable: waits without relaunching (routes may still be settling).
    - If not running: starts Tailscale in the background (no GUI window) via ``open -gj``.

    Polls until *host*:22 is reachable or *timeout* seconds elapse.
    Returns True if *host*:22 becomes reachable before the deadline.
    """
    import socket as _socket

    def _reachable() -> bool:
        try:
            s = _socket.create_connection((host, 22), timeout=3.0)
            s.close()
            return True
        except OSError:
            return False

    def _tailscale_running() -> bool:
        result = subprocess.run(["pgrep", "-f", "Tailscale.app"], capture_output=True, check=False)
        return result.returncode == 0

    if await asyncio.to_thread(_reachable):
        return True

    if sys.platform != "darwin":
        return False  # auto-start only implemented for macOS

    if await asyncio.to_thread(_tailscale_running):
        print("\nTailscale running but host not yet reachable — waiting...", file=sys.stderr)
    else:
        print("\nTailscale not running — starting in background...", file=sys.stderr)
        # -g: don't bring to foreground; -j: launch hidden (no window)
        await asyncio.to_thread(subprocess.run, ["open", "-gj", "-a", "Tailscale"], capture_output=True)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(1)
        if await asyncio.to_thread(_reachable):
            return True

    return False


def _print_remote_diagnostic(diagnostic_ssh_args: list[str], remote_diagnostic_file: str) -> None:
    """Print and remove stderr saved by the failed mosh command, if available."""
    diagnostic_command = (
        f'diagnostic_file="$HOME/{remote_diagnostic_file}"; '
        'if test -f "$diagnostic_file"; then '
        'tail -n 50 "$diagnostic_file" && rm -f "$diagnostic_file"; fi'
    )
    try:
        result = subprocess.run(
            [*diagnostic_ssh_args, diagnostic_command],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if diagnostic := result.stdout.strip():
        print(f"\nRemote command error:\n{diagnostic}", file=sys.stderr)


async def _run_transport_loop(
    ssh_args: list[str],
    mosh_args: list[str],
    cleanup_cmd: list[str],
    session_name: str,
    config: dict,
    tailscale_host: str = "",
    diagnostic_ssh_args: list[str] | None = None,
    remote_diagnostic_file: str = "",
) -> None:
    """Run the mosh/SSH transport loop with VPN-aware switching.

    Subscribes to ``vpn.state.changed`` on NATS. When a message arrives the
    active transport child is terminated and the loop restarts with the correct
    transport for the current VPN state. Falls back gracefully when NATS is
    unavailable — transport still works, just without live switching.

    *tailscale_host* — when set, the loop will attempt to start Tailscale
    automatically before falling back to SSH if mosh fails fast without VPN.
    """
    from .messaging import NATSClient

    state_dir = get_xdg_state_home()
    transport_file = state_dir / f"transport-{session_name}.json"

    servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
    nc = NATSClient(servers)
    await nc.connect()

    vpn_changed = asyncio.Event()

    async def _on_vpn_change(msg):
        vpn_changed.set()

    if nc.nc:
        # Not covered: requires NATS subscribe to raise after connect succeeds
        with contextlib.suppress(Exception):
            await nc.nc.subscribe("vpn.state.changed", cb=_on_vpn_change)

    force_ssh = False
    # Bounds the "Tailscale might just be starting up" retry below to a single
    # attempt. _ensure_tailscale_up only proves the SSH/TCP control path is
    # reachable -- it says nothing about mosh's separate UDP data channel, so a
    # host where that channel is blocked (e.g. firewalld not opening
    # 60000-61000/udp for the tailscale0 interface) always reports "reachable"
    # and mosh always fails again immediately, which retried unboundedly here
    # before this cap (AI-CLI-gg9s).
    tailscale_retries = 0
    try:
        while True:
            vpn_active = _is_vpn_active()
            vpn_changed.clear()

            args = ssh_args if (vpn_active or force_ssh) else mosh_args
            transport_type = "ssh" if (vpn_active or force_ssh) else "mosh"
            force_ssh = False
            print(
                f"\n{'VPN active' if vpn_active else 'No VPN'} — connecting via {transport_type}...",
                file=sys.stderr,
            )

            proc = subprocess.Popen(args)
            _write_transport_state(transport_file, session_name, os.getpid(), proc.pid, transport_type)

            start_time = _monotonic()
            _vpn_poll_ticks = 0
            from .config import get_remote_machine

            _vpn_poll_interval = get_remote_machine(config).get("vpn_poll_interval", 3)
            _vpn_poll_every = max(1, int(_vpn_poll_interval / 0.5))
            # Poll process while watching for NATS VPN signal.
            # Also poll VPN state directly every vpn_poll_interval seconds as a
            # fallback — mosh never exits when UDP is blocked by VPN, so NATS alone
            # is insufficient; the direct poll catches the case where the NATS
            # connection drops when VPN changes routing.
            while proc.poll() is None:
                if vpn_changed.is_set():
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()  # Not covered: requires proc to ignore SIGTERM
                        proc.wait()
                    break
                _vpn_poll_ticks += 1
                if transport_type == "mosh" and _vpn_poll_ticks % _vpn_poll_every == 0 and _is_vpn_active():
                    print("\nVPN detected — switching from mosh to SSH...", file=sys.stderr)
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    vpn_changed.set()
                    break
                await asyncio.sleep(0.5)
            else:
                proc.wait()

            elapsed = _monotonic() - start_time
            transport_file.unlink(missing_ok=True)

            if vpn_changed.is_set():
                print("\nVPN state changed — switching transport...", file=sys.stderr)
                continue

            # Mosh failed before establishing a session — check for VPN or unreachable host.
            # Threshold of 60s covers both fast TCP failures (~10s with ConnectTimeout=10)
            # and SSH banner exchange timeouts (~30s when host is reachable but SSH hangs).
            if transport_type == "mosh" and proc.returncode not in (0, None) and elapsed < 60:
                if diagnostic_ssh_args and remote_diagnostic_file:
                    _print_remote_diagnostic(diagnostic_ssh_args, remote_diagnostic_file)
                if _is_vpn_active():
                    print(
                        f"\nmosh failed ({elapsed:.1f}s), VPN detected — switching to SSH...",
                        file=sys.stderr,
                    )
                    continue
                # Mosh failed fast without VPN — try to bring Tailscale up first.
                # Only fall back to SSH if Tailscale can't be recovered, and only
                # retry once: a second fast failure right after "Tailscale up"
                # means the SSH/TCP control path is fine but mosh's UDP data
                # channel specifically is blocked, not that Tailscale was down.
                if tailscale_host and tailscale_retries < 1 and await _ensure_tailscale_up(tailscale_host):
                    tailscale_retries += 1
                    print("\nTailscale up — retrying mosh...", file=sys.stderr)
                    continue  # retry mosh with Tailscale now reachable
                if tailscale_retries >= 1:
                    print(
                        f"\nmosh failed again quickly ({elapsed:.1f}s) even though the host is "
                        "reachable — this usually means mosh's UDP data channel (default ports "
                        "60000-61000) is blocked (e.g. by the remote host's firewall), not that "
                        "Tailscale is down. Falling back to SSH; to restore mosh, allow that UDP "
                        "range to the remote host (firewalld: `firewall-cmd --add-port="
                        "60000-61000/udp` or trust the tailscale0 interface).",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"\nmosh failed ({elapsed:.1f}s), host unreachable — falling back to SSH...",
                        file=sys.stderr,
                    )
                force_ssh = True
                continue

            # SSH retry with backoff when VPN is active
            if transport_type == "ssh" and elapsed < 3 and _is_vpn_active():
                for delay in (1, 2, 4):
                    print(f"\nSSH failed — retrying in {delay}s...", file=sys.stderr)
                    time.sleep(delay)
                    proc2 = subprocess.Popen(args)
                    _write_transport_state(transport_file, session_name, os.getpid(), proc2.pid, transport_type)
                    while proc2.poll() is None:
                        if vpn_changed.is_set():
                            proc2.terminate()
                            proc2.wait()
                            break
                        await asyncio.sleep(0.5)
                    else:
                        proc2.wait()
                    transport_file.unlink(missing_ok=True)
                    if proc2.returncode == 0:
                        return  # SSH succeeded
                    if vpn_changed.is_set():
                        print("\nVPN state changed — switching transport...", file=sys.stderr)
                        break  # Back to outer loop
                if vpn_changed.is_set():
                    continue
                print("\nSSH failed after retries — giving up.", file=sys.stderr)
                break

            if elapsed < 3:
                if transport_type == "mosh" and diagnostic_ssh_args and remote_diagnostic_file:
                    _print_remote_diagnostic(diagnostic_ssh_args, remote_diagnostic_file)
                print(
                    f"\nTransport exited too quickly ({elapsed:.1f}s) — giving up.",
                    file=sys.stderr,
                )
                break

            # A mosh session that ends quickly with a "successful" exit code is
            # ambiguous: mosh propagates whatever the remote command exited with,
            # so a fast, clean exit can be a real user detach OR the remote-side
            # command (tmux/session setup) silently no-op'ing on a transient
            # condition (e.g. a stale session-name collision) and exiting 0 without
            # ever actually starting a session. The mosh-fail branch above only
            # catches a non-zero return code, so this case previously fell straight
            # through to a bare, silent exit (AI-CLI-jbyo) — surface it instead.
            if transport_type == "mosh" and elapsed < 15:
                if diagnostic_ssh_args and remote_diagnostic_file:
                    _print_remote_diagnostic(diagnostic_ssh_args, remote_diagnostic_file)
                print(
                    f"\nmosh session ended after {elapsed:.1f}s (exit code "
                    f"{proc.returncode}) — if you didn't intentionally detach this "
                    "quickly, the remote session likely failed to start (e.g. a "
                    "transient session-name collision); try the command again.",
                    file=sys.stderr,
                )

            # A non-zero mosh exit outside the fast-failure windows is neither
            # a normal detach nor explained by one of the named diagnostics
            # above. Preserve its exact values so a transient recurrence is
            # actionable.
            elif transport_type == "mosh" and proc.returncode not in (0, None):
                if diagnostic_ssh_args and remote_diagnostic_file:
                    _print_remote_diagnostic(diagnostic_ssh_args, remote_diagnostic_file)
                print(
                    "\nmosh exited without a recognized diagnostic branch "
                    f"(returncode={proc.returncode}, elapsed={elapsed:.1f}s) — this is an "
                    "unexplained transient failure; please retry and report if it recurs "
                    "with these exact numbers.",
                    file=sys.stderr,
                )

            break  # Normal exit (user detached or session ended)
    finally:
        transport_file.unlink(missing_ok=True)
        # Before anything else in this block, and unconditionally rather than only on a
        # clean exit: every way out of the loop above -- normal detach, a killed child, a
        # give-up branch, an exception -- leaves the terminal in whatever modes the remote
        # set, and the paths that skipped a graceful shutdown are precisely the ones where
        # the remote never sent its own disable sequences. Restoring first also means the
        # messages below are printed to a sane terminal.
        restore_terminal()
        # Not covered: requires NATS close to raise after connect succeeds
        with contextlib.suppress(Exception):
            await nc.close()
        subprocess.run(cleanup_cmd, capture_output=True, check=False)
