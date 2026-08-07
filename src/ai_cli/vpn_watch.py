"""VPN state watcher — Circus-managed process that publishes vpn.state.changed to NATS.

Started lazily by the first ``ai c -R`` session and torn down when the last session exits.
All active transport loops subscribe to ``vpn.state.changed`` and switch transports on receipt.
"""

import asyncio
import json
import sys
from datetime import UTC, datetime

from .config import get_xdg_state_home
from .messaging import NATSClient
from .transport import _is_vpn_active


def run_vpn_watch(config: dict) -> None:
    """Entry point for ``ai vpn-watch``. Runs the watcher loop until interrupted."""
    try:
        asyncio.run(_vpn_watch_loop(config))
    except KeyboardInterrupt:
        pass


async def _vpn_watch_loop(config: dict) -> None:
    poll_interval = int(config.get("remote", {}).get("vpn_poll_interval", 3))
    state_dir = get_xdg_state_home()
    state_dir.mkdir(parents=True, exist_ok=True)
    log_file = state_dir / "vpn-transitions.log"

    servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
    nc = NATSClient(servers)
    await nc.connect()

    last_vpn = _is_vpn_active()
    print(f"vpn-watch: started (VPN {'active' if last_vpn else 'inactive'})", file=sys.stderr)

    try:
        while True:
            await asyncio.sleep(poll_interval)
            current_vpn = _is_vpn_active()
            if current_vpn == last_vpn:
                continue

            # Debounce: wait 2s and re-check before committing to the transition
            await asyncio.sleep(2)
            confirmed_vpn = _is_vpn_active()
            if confirmed_vpn == last_vpn:
                # State reverted within debounce window — suppress (VPN flap)
                continue

            last_vpn = confirmed_vpn
            state_str = "active" if confirmed_vpn else "inactive"
            ts = datetime.now(UTC).isoformat()
            payload = {"vpn": confirmed_vpn, "ts": ts}

            # Log the transition
            try:
                with open(log_file, "a") as f:
                    f.write(json.dumps(payload) + "\n")
            except OSError:
                pass  # Not covered: requires filesystem write failure

            print(f"vpn-watch: VPN {state_str} at {ts}", file=sys.stderr)

            # Publish to NATS (core publish — ephemeral notification, no durability needed)
            if nc.nc:
                try:
                    await nc.nc.publish("vpn.state.changed", json.dumps(payload).encode())
                except Exception:
                    pass  # Not covered: requires NATS publish to raise after subscribe
    finally:
        try:
            await nc.close()
        except Exception:
            pass  # Not covered: requires NATS close to raise after connect
