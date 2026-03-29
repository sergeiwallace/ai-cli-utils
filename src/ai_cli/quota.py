"""Quota tracking — monitors Claude usage and publishes threshold events.

Polls Claude usage data and publishes quota.threshold.{50,75,90} events
when thresholds are crossed. Uses JetStream deduplication to avoid
re-alerting the same threshold within a calendar day.
"""

import asyncio
import json
import subprocess
import sys
import time
from datetime import date
from pathlib import Path


def _get_claude_usage_percent() -> float | None:
    """Attempt to read Claude usage percentage.

    Checks ~/.claude/usage.json or runs `claude usage` to get current usage.
    Returns a float 0-100 or None if unavailable.
    """
    # Check for usage file first
    usage_file = Path.home() / ".claude" / "usage.json"
    if usage_file.exists():
        try:
            data = json.loads(usage_file.read_text())
            used = data.get("used", 0)
            limit = data.get("limit", 0)
            if limit > 0:
                return (used / limit) * 100
        except Exception:
            pass

    # Try `claude usage` command
    try:
        result = subprocess.run(
            ["claude", "usage"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            # Parse percentage from output
            import re

            match = re.search(r"(\d+(?:\.\d+)?)%", result.stdout)
            if match:
                return float(match.group(1))
    except Exception:
        pass

    return None


def _dedup_key(threshold: int) -> str:
    """Generate a deduplication key for a threshold on today's date."""
    return f"quota-{threshold}-{date.today().isoformat()}"


def quota_watch(poll_interval: int = 300) -> int:
    """Run the quota watch daemon.

    Polls Claude usage and publishes threshold events when crossed.
    Uses JetStream deduplication to avoid re-alerting same threshold in same day.
    PID file guard prevents duplicate instances.
    Exit codes: 0 = clean stop, 1 = error
    """
    from .sync import _acquire_pid_file, _release_pid_file
    from .messaging import NATSClient

    if not _acquire_pid_file("quota-watch"):
        print("ai quota watch is already running.", file=sys.stderr)
        return 2

    client = NATSClient()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(client.connect())
    except Exception:
        pass

    if not client.nc:
        print("NATS unavailable — cannot start quota watcher.", file=sys.stderr)
        _release_pid_file("quota-watch")
        return 1

    thresholds = [50, 75, 90]
    alerted_today: dict[int, str] = {}  # threshold -> date string

    print(f"ai quota watch — polling every {poll_interval}s (Ctrl+C to stop)")

    try:
        while True:
            usage = _get_claude_usage_percent()
            if usage is not None:
                today = date.today().isoformat()
                for threshold in thresholds:
                    if usage >= threshold and alerted_today.get(threshold) != today:
                        subject = f"quota.threshold.{threshold}"
                        payload = {
                            "threshold": threshold,
                            "usage_percent": round(usage, 1),
                            "ts": time.time(),
                        }
                        try:
                            loop.run_until_complete(client.publish(subject, payload))
                            alerted_today[threshold] = today
                            print(f"[quota-watch] threshold {threshold}% crossed (usage: {usage:.1f}%)")

                            # Fire OS notification
                            _send_notification(threshold, usage)
                        except Exception as e:
                            print(f"[quota-watch] failed to publish: {e}", file=sys.stderr)

            time.sleep(poll_interval)
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(client.close())
        loop.close()
        _release_pid_file("quota-watch")

    return 0


def _send_notification(threshold: int, usage: float) -> None:
    """Send OS notification for quota threshold."""
    msg = f"Claude usage at {usage:.0f}% (threshold: {threshold}%)"
    if threshold >= 90:
        msg += " — slow down!"
    try:
        subprocess.run(
            ["notify-send", "ai-cli quota", msg],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass
