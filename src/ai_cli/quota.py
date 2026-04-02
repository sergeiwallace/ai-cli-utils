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


def _find_claude_pane() -> str | None:
    """Find the tmux pane most likely running Claude Code."""
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{pane_id} #{pane_current_command} #{session_name}"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        for line in result.stdout.splitlines():
            parts = line.split(" ", 2)
            if len(parts) >= 2 and parts[1].lower() in ("claude", "node"):
                return parts[0]
        # Fall back: look for pane in a session named like cc/sw/c-*
        for line in result.stdout.splitlines():
            parts = line.split(" ", 2)
            if len(parts) >= 3:
                sess = parts[2]
                if any(sess.startswith(p) for p in ("sw-", "c-r-", "cc-")):
                    return parts[0]
    except Exception:
        pass
    return None


def _scrape_usage_tmux(target_pane: str | None = None) -> float | None:
    """Scrape /usage percentage from an active Claude Code tmux pane.

    If target_pane is None, searches for a pane likely running Claude Code
    by checking pane titles/commands. Returns float 0-100 or None.
    """
    import re

    if target_pane is None:
        target_pane = _find_claude_pane()
    if target_pane is None:
        return None

    try:
        # Clear the screen to avoid matching stale output
        subprocess.run(
            ["tmux", "send-keys", "-t", target_pane, "C-l", ""],
            capture_output=True,
            timeout=2,
        )
        time.sleep(0.3)

        # Inject /usage command
        subprocess.run(
            ["tmux", "send-keys", "-t", target_pane, "/usage", "Enter"],
            capture_output=True,
            timeout=2,
        )

        # Poll capture-pane until "Usage:" appears (max 10s)
        for _ in range(50):
            time.sleep(0.2)
            result = subprocess.run(
                ["tmux", "capture-pane", "-p", "-t", target_pane, "-J"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0:
                match = re.search(r"Usage[:\s]+(\d+(?:\.\d+)?)\s*%", result.stdout, re.IGNORECASE)
                if match:
                    return float(match.group(1))

        return None
    except Exception:
        return None


def _get_claude_usage_percent() -> float | None:
    """Attempt to read Claude usage percentage.

    Tries tmux scraping first, then falls back to usage file and CLI.
    Returns a float 0-100 or None if unavailable.
    """
    # Try tmux scraping first
    result = _scrape_usage_tmux()
    if result is not None:
        return result

    # Check for usage file
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
            import re

            match = re.search(r"(\d+(?:\.\d+)?)%", result.stdout)
            if match:
                return float(match.group(1))
    except Exception:
        pass

    return None


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
        loop.close()
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


def quota_status(receiver_url: str = "http://localhost:8080") -> int:
    """Print current quota status from the telemetry receiver."""
    import urllib.request
    import urllib.error

    try:
        req = urllib.request.urlopen(f"{receiver_url}/api/v1/quota/status", timeout=5)
        data = json.loads(req.read())
        snap = data.get("latest_snapshot")
        if snap:
            pct = snap.get("usage_percent", 0)
            ts = snap.get("snapshotted_at", "?")
            print(f"Quota: {pct:.1f}% used  (last snapshot: {ts})")
        else:
            print("Quota: no snapshots yet")
        burn = data.get("burn_rate", {})
        if burn and burn.get("expected_pct_per_day", 0) > 0:
            actual = burn.get("actual_pct_per_day", 0)
            expected = burn.get("expected_pct_per_day", 0)
            mult = burn.get("multiplier", 0)
            print(f"Burn rate: {actual:.2f}%/day actual vs {expected:.2f}%/day expected ({mult:.1f}x)")
        days = data.get("days_remaining")
        if days is not None:
            print(f"Days to reset: {days:.1f}")
        alerts = data.get("alerts", [])
        for alert in alerts:
            print(f"  {alert}")
        return 0
    except Exception as e:
        print(f"Could not reach telemetry receiver: {e}", file=sys.stderr)
        return 1


def quota_history(receiver_url: str = "http://localhost:8080") -> int:
    """Print weekly quota usage history."""
    import urllib.request
    import urllib.error

    try:
        req = urllib.request.urlopen(f"{receiver_url}/api/v1/quota/history", timeout=5)
        history = json.loads(req.read())
        if not history:
            print("No history yet.")
            return 0
        print(f"{'Week':24} {'Peak %':8} {'Tokens':>12} {'Snapshots':>10}")
        print("-" * 58)
        for week in history:
            print(
                f"{week['week_start']:24} {week.get('peak_percent', 0):7.1f}% "
                f"{week.get('total_consumed', 0):12,} {week.get('snapshot_count', 0):10}"
            )
        return 0
    except Exception as e:
        print(f"Could not reach telemetry receiver: {e}", file=sys.stderr)
        return 1


def quota_scrape(receiver_url: str = "http://localhost:8080", target_pane: str | None = None) -> int:
    """Scrape /usage from active CC session and POST to receiver."""
    import urllib.request

    print("Scraping /usage from Claude Code session...")
    pct = _scrape_usage_tmux(target_pane)
    if pct is None:
        print("Could not extract usage percentage.", file=sys.stderr)
        return 1
    print(f"Scraped: {pct:.1f}%")
    try:
        body = json.dumps({"usage_percent": pct}).encode()
        req = urllib.request.Request(
            f"{receiver_url}/api/v1/quota/snapshot",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
        print("Posted snapshot to receiver.")
        return 0
    except Exception as e:
        print(f"Could not post to receiver: {e}", file=sys.stderr)
        return 1
