"""VPN-aware transport switching for remote sessions.

Depends on: config.py, messaging.py, process_manager.py (lazy).
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from .config import get_xdg_state_home

# Module-level alias so tests can patch _monotonic without affecting asyncio internals
_monotonic = time.monotonic


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
            )
            return "Connected" in result.stdout
        # Fallback: check for active tunnel interfaces via ifconfig
        result = subprocess.run(
            ["ifconfig"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        import re as _re

        # Look for utun/tun interfaces that have an inet address (i.e. are up)
        iface_blocks = _re.split(r"^(\S+)", result.stdout, flags=_re.MULTILINE)
        for i in range(1, len(iface_blocks) - 1, 2):
            name = iface_blocks[i]
            body = iface_blocks[i + 1]
            if (name.startswith("utun") or name.startswith("tun")) and "inet " in body:
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
        result = subprocess.run(["pgrep", "-f", "Tailscale.app"], capture_output=True)
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


async def _run_transport_loop(
    ssh_args: list[str],
    mosh_args: list[str],
    cleanup_cmd: list[str],
    session_name: str,
    config: dict,
    tailscale_host: str = "",
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
        try:
            await nc.nc.subscribe("vpn.state.changed", cb=_on_vpn_change)
        except Exception:
            pass  # Not covered: requires NATS subscribe to raise after connect succeeds

    force_ssh = False
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
            _vpn_poll_interval = config.get("remote", {}).get("vpn_poll_interval", 3)
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
                if transport_type == "mosh" and _vpn_poll_ticks % _vpn_poll_every == 0:
                    if _is_vpn_active():
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
                if _is_vpn_active():
                    print(
                        f"\nmosh failed ({elapsed:.1f}s), VPN detected — switching to SSH...",
                        file=sys.stderr,
                    )
                    continue
                # Mosh failed fast without VPN — try to bring Tailscale up first.
                # Only fall back to SSH if Tailscale can't be recovered.
                if tailscale_host and await _ensure_tailscale_up(tailscale_host):
                    print("\nTailscale up — retrying mosh...", file=sys.stderr)
                    continue  # retry mosh with Tailscale now reachable
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
                print(
                    f"\nTransport exited too quickly ({elapsed:.1f}s) — giving up.",
                    file=sys.stderr,
                )
                break

            break  # Normal exit (user detached or session ended)
    finally:
        transport_file.unlink(missing_ok=True)
        try:
            await nc.close()
        except Exception:
            pass  # Not covered: requires NATS close to raise after connect succeeds
        subprocess.run(cleanup_cmd, capture_output=True)
