"""Quota tracking — monitors Claude usage and publishes threshold events.

Polls Claude usage data via a hidden tmux window and publishes
quota.threshold.{50,75,90} events when thresholds are crossed.
Uses JetStream deduplication to avoid re-alerting the same threshold
within a calendar day. Stores snapshots and usage records in local SQLite
at ~/.local/state/ai-cli/quota.db (no external server dependency).
"""

import asyncio
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date


@dataclass
class QuotaSnapshot:
    """All metrics from a single /usage scrape."""

    weekly_all_models_pct: float  # primary quota metric — "Current week (all models)"
    session_pct: float | None = None  # "Current session"
    weekly_sonnet_pct: float | None = None  # "Current week (Sonnet only)"
    extra_pct: float | None = None  # "Extra usage: X%" or 0.0 if "not enabled"


def _parse_usage_output(output: str) -> QuotaSnapshot | None:
    """Parse the text output of /usage into a QuotaSnapshot.

    Expected output contains lines like:
      Current session: 12% used
      Current week (all models): 86% used
      Current week (Sonnet only): 49% used
      Extra usage not enabled
    """
    # re.DOTALL required: /usage output puts the percentage on a separate line from
    # the label, with a block-character progress bar in between.
    weekly_all_match = re.search(
        r"Current week \(all models\).*?(\d+(?:\.\d+)?)\s*%\s*used", output, re.DOTALL | re.IGNORECASE
    )
    if not weekly_all_match:
        return None

    session_match = re.search(r"Current session.*?(\d+(?:\.\d+)?)\s*%\s*used", output, re.DOTALL | re.IGNORECASE)
    sonnet_match = re.search(
        r"Current week \(Sonnet only\).*?(\d+(?:\.\d+)?)\s*%\s*used", output, re.DOTALL | re.IGNORECASE
    )
    extra_match = re.search(r"Extra usage.*?(\d+(?:\.\d+)?)\s*%", output, re.DOTALL | re.IGNORECASE)
    extra_not_enabled = bool(re.search(r"Extra usage not enabled", output, re.IGNORECASE))

    return QuotaSnapshot(
        weekly_all_models_pct=float(weekly_all_match.group(1)),
        session_pct=float(session_match.group(1)) if session_match else None,
        weekly_sonnet_pct=float(sonnet_match.group(1)) if sonnet_match else None,
        extra_pct=float(extra_match.group(1)) if extra_match else (0.0 if extra_not_enabled else None),
    )


def _scrape_usage_hidden_pane() -> QuotaSnapshot | None:
    """Scrape /usage from a hidden tmux window running a bare CC session.

    Spins up a detached tmux window, starts claude --dangerously-skip-permissions,
    waits for the prompt, injects /usage, captures the output, and kills the window.
    The user never sees it.
    """
    window_name = "ai-quota-scrape"
    created_session = False
    try:
        # Prefer new-window (inside tmux) for reliability; fall back to new-session
        # when running outside tmux (e.g. from cron). Use index-based targeting (:N)
        # rather than =name syntax — capture-pane requires a pane target, and index
        # targeting works universally across tmux versions.
        result = subprocess.run(
            ["tmux", "new-window", "-d", "-n", window_name, "-P", "-F", "#{window_index}"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode != 0:
            # Outside tmux (e.g. cron) — create a standalone detached session instead
            result = subprocess.run(
                ["tmux", "new-session", "-d", "-s", window_name, "-P", "-F", "#{window_index}"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode != 0:
                return None
            created_session = True
        target = f":{result.stdout.strip()}"

        # Start CC with no-op permissions (read-only scraping, never runs tools)
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "claude --dangerously-skip-permissions", "Enter"],
            capture_output=True,
            timeout=2,
        )

        # Poll for the CC prompt indicator (❯) — startup takes ~4s
        ready = False
        for _ in range(75):  # up to 15s
            time.sleep(0.2)
            cap = subprocess.run(
                ["tmux", "capture-pane", "-p", "-t", target, "-J"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if cap.returncode == 0 and "❯" in cap.stdout:
                ready = True
                break

        if not ready:
            return None

        # Inject /usage
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "/usage", "Enter"],
            capture_output=True,
            timeout=2,
        )

        # Poll for the usage output (max 5s)
        snapshot = None
        for _ in range(25):
            time.sleep(0.2)
            cap = subprocess.run(
                ["tmux", "capture-pane", "-p", "-t", target, "-J"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if cap.returncode == 0 and "% used" in cap.stdout:
                snapshot = _parse_usage_output(cap.stdout)
                if snapshot:
                    break

        # Dismiss the dialog
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "Escape", ""],
            capture_output=True,
            timeout=2,
        )
        return snapshot

    except Exception:
        return None
    finally:
        # Always clean up — kill session if we created one, otherwise kill just the window
        if created_session:
            subprocess.run(
                ["tmux", "kill-session", "-t", window_name],
                capture_output=True,
                timeout=3,
            )
        else:
            subprocess.run(
                ["tmux", "kill-window", "-t", f"={window_name}"],
                capture_output=True,
                timeout=3,
            )


def _get_claude_usage_snapshot() -> QuotaSnapshot | None:
    """Return a QuotaSnapshot from a hidden tmux window, or None if unavailable."""
    return _scrape_usage_hidden_pane()


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
            snapshot = _get_claude_usage_snapshot()
            if snapshot is not None:
                usage = snapshot.weekly_all_models_pct
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
                            _send_notification(threshold, snapshot)
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


def _send_notification(threshold: int, snapshot: QuotaSnapshot) -> None:
    """Send alert for quota threshold via Slack webhook (if configured) or notify-send."""
    usage = snapshot.weekly_all_models_pct
    msg = f"Claude usage at {usage:.0f}% (threshold: {threshold}%)"
    if threshold >= 90:
        msg += " — slow down!"

    # Try Slack webhook first if configured
    try:
        from .main import load_config

        cfg = load_config().get("quota", {})
        webhook_url = cfg.get("slack_webhook_url", "")
    except Exception:
        webhook_url = ""

    if webhook_url:
        _send_slack_notification(webhook_url, threshold, snapshot)
        return

    # Fallback: OS notification via notify-send
    try:
        subprocess.run(
            ["notify-send", "ai-cli quota", msg],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass


def _send_slack_notification(webhook_url: str, threshold: int, snapshot: QuotaSnapshot) -> None:
    """POST a Slack webhook message for a quota threshold crossing."""
    import urllib.request

    usage = snapshot.weekly_all_models_pct
    emoji = ":rotating_light:" if threshold >= 90 else ":warning:" if threshold >= 75 else ":information_source:"
    text = f"{emoji} *Claude quota {threshold}% threshold crossed*\nWeekly (all models): {usage:.1f}%"
    if snapshot.weekly_sonnet_pct is not None:
        text += f" | Sonnet: {snapshot.weekly_sonnet_pct:.1f}%"
    if snapshot.session_pct is not None:
        text += f" | Session: {snapshot.session_pct:.1f}%"
    if threshold >= 90:
        text += "\n*Slow down — quota nearly exhausted.*"

    payload = json.dumps({"text": text}).encode()
    try:
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def quota_status() -> int:
    """Print current quota status from local SQLite."""
    from .quota_db import get_current_status

    data = get_current_status()
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


def quota_history() -> int:
    """Print weekly quota usage history from local SQLite."""
    from .quota_db import get_weekly_history

    history = get_weekly_history()
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


def quota_scrape() -> int:
    """Scrape /usage from a hidden CC session and store in local SQLite."""
    from .quota_db import record_quota_snapshot

    print("Scraping /usage from Claude Code session (hidden tmux window)...")
    snapshot = _scrape_usage_hidden_pane()
    if snapshot is None:
        print("Could not extract usage percentage.", file=sys.stderr)
        return 1

    print(f"Scraped: weekly all-models {snapshot.weekly_all_models_pct:.1f}%", end="")
    if snapshot.weekly_sonnet_pct is not None:
        print(f", Sonnet {snapshot.weekly_sonnet_pct:.1f}%", end="")
    if snapshot.session_pct is not None:
        print(f", session {snapshot.session_pct:.1f}%", end="")
    print()

    record_quota_snapshot(
        usage_percent=snapshot.weekly_all_models_pct,
        session_pct=snapshot.session_pct,
        weekly_sonnet_pct=snapshot.weekly_sonnet_pct,
        extra_pct=snapshot.extra_pct,
    )
    print("Stored snapshot in local quota DB.")
    _publish_quota_snapshot(snapshot)
    return 0


def _publish_quota_snapshot(snapshot: QuotaSnapshot) -> None:
    """Publish a quota snapshot to NATS for cross-machine sync.

    Publishes to subject ``quota.snapshot`` so signal-watch daemons on other
    machines can receive and persist the latest usage data into their local
    SQLite DB. Fire-and-forget — silently no-ops if NATS is unavailable.
    """
    import asyncio

    from .messaging import NATSClient

    try:
        from .main import load_config

        cfg = load_config()
        nats_servers = cfg.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
    except Exception:
        nats_servers = ["nats://localhost:4222"]

    payload: dict = {
        "usage_percent": snapshot.weekly_all_models_pct,
        "session_pct": snapshot.session_pct,
        "weekly_sonnet_pct": snapshot.weekly_sonnet_pct,
        "extra_pct": snapshot.extra_pct,
        "ts": time.time(),
    }

    async def _do_publish() -> None:
        client = NATSClient(servers=nats_servers)
        try:
            await client.connect()
            if client.nc:
                await client.publish("quota.snapshot", payload)
        finally:
            await client.close()

    try:
        asyncio.run(_do_publish())
    except Exception:
        pass


def quota_statusline_part() -> int:
    """Print a compact quota indicator for use in the statusline.

    Reads the latest snapshot from local SQLite (fast, no scraping).
    Outputs: {pace_icon} {usage_pct:.0f}% {direction}{delta:.0f}%
    where delta = usage_pct - week_elapsed_pct.
    """
    import sqlite3

    from .quota_db import _get_current_week_start, _get_quota_db_path

    try:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        week_start_str = _get_current_week_start(now)

        db_path = _get_quota_db_path()
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT usage_percent, snapshotted_at FROM quota_snapshots"
            " WHERE week_start = ? ORDER BY snapshotted_at DESC LIMIT 3",
            (week_start_str,),
        ).fetchall()
        conn.close()

        if not rows:
            return 0

        usage_pct = rows[0]["usage_percent"]

        ws_dt = datetime.strptime(week_start_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        elapsed_secs = (now - ws_dt).total_seconds()
        week_elapsed_pct = min(elapsed_secs / (7 * 24 * 3600) * 100.0, 100.0)

        delta = usage_pct - week_elapsed_pct

        GREEN = "\033[32m"
        YELLOW = "\033[33m"
        RED = "\033[31m"
        RESET = "\033[0m"

        # Quota % color: absolute level
        if usage_pct < 50:
            pct_color = GREEN
        elif usage_pct < 75:
            pct_color = YELLOW
        else:
            pct_color = RED

        # Pace icon and delta color (match each other)
        if delta < -5:
            icon = "\u2705"  # ✅
            delta_color = GREEN
        elif delta <= 5:
            icon = "\u26a0\ufe0f"  # ⚠️
            delta_color = YELLOW
        else:
            icon = "\U0001f6a8"  # 🚨
            delta_color = RED

        # Arrow: acceleration direction (requires ≥3 snapshots)
        arrow_char = "\u2192"  # → steady (default / insufficient data)
        if len(rows) >= 3:
            t0 = datetime.fromisoformat(rows[0]["snapshotted_at"].replace("Z", "+00:00")).timestamp()
            t1 = datetime.fromisoformat(rows[1]["snapshotted_at"].replace("Z", "+00:00")).timestamp()
            t2 = datetime.fromisoformat(rows[2]["snapshotted_at"].replace("Z", "+00:00")).timestamp()
            dt01 = (t0 - t1) / 3600
            dt12 = (t1 - t2) / 3600
            rate_recent = (rows[0]["usage_percent"] - rows[1]["usage_percent"]) / dt01 if dt01 > 0 else 0.0
            rate_prev = (rows[1]["usage_percent"] - rows[2]["usage_percent"]) / dt12 if dt12 > 0 else 0.0
            accel = rate_recent - rate_prev
            if accel > 1.0:
                arrow_char = "\u2191"  # ↑ accelerating
            elif accel < -1.0:
                arrow_char = "\u2193"  # ↓ decelerating

        print(
            f"\U0001f4ca {pct_color}{usage_pct:.0f}%{RESET} {icon} {delta_color}{arrow_char}{abs(delta):.0f}%{RESET}"
        )  # 📊
    except Exception:
        pass
    return 0


def quota_record(
    session_id: str,
    machine_id: str,
    model: str,
    total_tokens: int,
    cost_usd: float | None = None,
) -> int:
    """Record a session usage event into local SQLite.

    Called by the statusLine hook (ai quota record SESSION MACHINE MODEL TOKENS [COST]).
    """
    from .quota_db import record_usage

    record_usage(
        session_id=session_id,
        machine_id=machine_id,
        model=model,
        total_tokens=total_tokens,
        cost_usd=cost_usd,
    )
    return 0
