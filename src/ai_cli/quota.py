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
from pathlib import Path


@dataclass
class QuotaSnapshot:
    """All metrics from a single /usage scrape."""

    weekly_all_models_pct: float  # primary quota metric — "Current week (all models)"
    session_pct: float | None = None  # "Current session"
    weekly_sonnet_pct: float | None = None  # "Current week (Sonnet only)"
    extra_pct: float | None = None  # "Extra usage: X%" or 0.0 if "not enabled"
    reset_at: str | None = None  # next reset as UTC ISO string, e.g. "2026-04-18T11:59:00Z"


# Timezone abbreviation → UTC offset in hours. Covers US zones; others fall back to UTC.
_TZ_OFFSETS_H: dict[str, int] = {
    "EST": -5,
    "EDT": -4,
    "CST": -6,
    "CDT": -5,
    "MST": -7,
    "MDT": -6,
    "PST": -8,
    "PDT": -7,
    "UTC": 0,
    "GMT": 0,
}

_MONTH_MAP: dict[str, int] = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}


def _parse_reset_datetime(text: str) -> str | None:
    """Extract the all-models weekly reset datetime from /usage output.

    Returns a UTC ISO string (e.g. "2026-04-18T11:59:00Z"), or None if not found.

    CC's /usage dialog embeds the reset time in the label line for the weekly
    all-models quota, using a time-only format without a date:

        Current week (all models) · Resets 6:59am
        Current week (all models) · Resets 11pm

    The full reset datetime is reconstructed using the system's local timezone:
    the next future occurrence of that time on a weekly boundary from now.
    """
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    # Primary format: "week (all models) ... Resets {time}"
    # Handles: "6:59am", "6:59 AM", "11pm", "11 PM", "11:00 PM"
    m = re.search(
        r"week\s*\(all\s*models\)[^·\n]*[·\u00b7\u2019·]\s*[Rr]eset[s]?\s+"
        r"(\d{1,2})(?::(\d{2}))?\s*([AP]M?)\b",
        text,
        re.IGNORECASE,
    )

    if not m:
        # Fallback: standalone "Resets {full date+time}" line (e.g. future CC format)
        full = re.search(
            r"[Rr]eset[^\n·]*?"
            r"(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*,?\s*)?"
            r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?"
            r"|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?"
            r"|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
            r"\s+(\d{1,2})(?:,?\s*(\d{4}))?"
            r"[^,\n]*?(\d{1,2}):(\d{2})(?::(\d{2}))?"
            r"\s*([AP]M?)?(?:\s+(EST|EDT|CST|CDT|MST|MDT|PST|PDT|UTC|GMT))?",
            text,
            re.IGNORECASE,
        )
        if not full:
            return None
        month_str, day_str, year_str, h, mi, sec_s, ampm, tz_s = full.groups()
        month = _MONTH_MAP.get(month_str.lower())
        if not month:
            return None
        now_utc = _dt.now(_tz.utc)
        year = int(year_str) if year_str else now_utc.year
        hour, minute, second = int(h), int(mi), int(sec_s) if sec_s else 0
        if ampm:
            ap = ampm.upper().rstrip(".")
            if ap == "PM" and hour < 12:
                hour += 12
            elif ap == "AM" and hour == 12:
                hour = 0
        tz_offset_h = _TZ_OFFSETS_H.get(tz_s.upper() if tz_s else "", 0)
        try:
            naive = _dt(year, month, day_str and int(day_str) or 1, hour, minute, second)
            return (naive - _td(hours=tz_offset_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return None

    hour_str, min_str, ampm = m.groups()
    hour = int(hour_str)
    minute = int(min_str) if min_str else 0

    ampm_upper = ampm.upper().rstrip(".")
    if ampm_upper == "PM" and hour < 12:
        hour += 12
    elif ampm_upper == "AM" and hour == 12:
        hour = 0

    # Reconstruct the full UTC datetime using the system's local timezone.
    # CC shows the next reset time, so we find the next future occurrence of
    # that time (weekly period).
    local_now = _dt.now().astimezone()
    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local_now:
        # Time already passed today — next occurrence is in 7 days
        candidate += _td(days=7)
    return candidate.astimezone(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_usage_output(output: str) -> QuotaSnapshot | None:
    """Parse the text output of /usage into a QuotaSnapshot.

    Expected output contains lines like:
      Current week (all models) · Resets 6:59am
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
        reset_at=_parse_reset_datetime(output),
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

        # Poll for the CC prompt indicator (❯) — startup takes ~4s.
        # CC may show a "trust this folder" dialog first; dismiss it with Enter,
        # then continue polling for the real interactive prompt.
        ready = False
        trust_dismissed = False
        for _ in range(75):  # up to 15s
            time.sleep(0.2)
            cap = subprocess.run(
                ["tmux", "capture-pane", "-p", "-t", target, "-J"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if cap.returncode != 0:
                continue
            output = cap.stdout
            if "❯" in output:
                # If the trust dialog is visible, confirm it and keep polling.
                if not trust_dismissed and ("trust this folder" in output.lower() or "yes, i trust" in output.lower()):
                    subprocess.run(
                        ["tmux", "send-keys", "-t", target, "", "Enter"],
                        capture_output=True,
                        timeout=2,
                    )
                    trust_dismissed = True
                    continue
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


_SCRAPE_LOCK_PATH = Path.home() / ".local" / "state" / "ai-cli" / "quota-scrape.lock"
_SCRAPE_TTL_MINUTES = 30
_SCRAPE_LOCK_STALE_MINUTES = 15


def _launch_background_scrape() -> None:
    """Launch `ai quota scrape` in the background if no scrape is already running.

    Must never raise — the statusline path must be silent on errors.
    """
    try:
        from datetime import datetime, timezone

        if _SCRAPE_LOCK_PATH.exists():
            lock_age_minutes = (datetime.now(timezone.utc).timestamp() - _SCRAPE_LOCK_PATH.stat().st_mtime) / 60
            if lock_age_minutes < _SCRAPE_LOCK_STALE_MINUTES:
                return  # scrape already running
            _SCRAPE_LOCK_PATH.unlink(missing_ok=True)

        _SCRAPE_LOCK_PATH.touch()
        subprocess.Popen(
            ["ai", "quota", "scrape"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def _maybe_trigger_background_scrape(snapshotted_at: str) -> None:
    """Fire a background `ai quota scrape` if the latest snapshot is stale.

    Must never raise — the statusline path must be silent on errors.
    """
    try:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        snapshot_dt = datetime.fromisoformat(snapshotted_at.replace("Z", "+00:00"))
        age_minutes = (now - snapshot_dt).total_seconds() / 60

        if age_minutes < _SCRAPE_TTL_MINUTES:
            return  # snapshot is fresh enough

        _launch_background_scrape()
    except Exception:
        pass


def quota_scrape() -> int:
    """Scrape /usage from a hidden CC session and store in local SQLite."""
    from .quota_db import record_quota_snapshot

    print("Scraping /usage from Claude Code session (hidden tmux window)...")
    try:
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
            reset_at=snapshot.reset_at,
        )
        print("Stored snapshot in local quota DB.")
        _publish_quota_snapshot(snapshot)
        return 0
    finally:
        _SCRAPE_LOCK_PATH.unlink(missing_ok=True)


def quota_sync_from_remote() -> int:
    """Pull quota snapshots from the remote server's SQLite DB into the local DB.

    SSHes to the configured remote host, queries the latest ``quota_snapshots``
    rows, and upserts any new rows into the local DB.  Rows are deduplicated by
    ``snapshotted_at`` — existing rows are never overwritten.

    Designed for cron use on Mac: run every 10 minutes so the local DB always
    reflects the primary server's quota state without needing a scraper or a
    running CC session.  Silently no-ops if SSH fails (e.g. host unreachable).
    """
    from .quota_db import _get_conn

    try:
        from .main import load_config

        cfg = load_config()
        remote = cfg.get("remote", {})
        host = remote.get("host", "")
        user = remote.get("user", "")
        port = str(remote.get("port", 22))
        identity = remote.get("identity_file", "")
    except Exception as exc:
        print(f"quota sync: could not load config: {exc}", file=sys.stderr)
        return 1

    if not host or not user:
        print("quota sync: no remote host configured in [remote]", file=sys.stderr)
        return 1

    sql = (
        "SELECT usage_percent, session_pct, weekly_sonnet_pct, extra_pct,"
        " week_start, snapshotted_at"
        " FROM quota_snapshots"
        " ORDER BY snapshotted_at DESC LIMIT 20;"
    )
    ssh_cmd = ["ssh", "-p", port, "-o", "ConnectTimeout=5", "-o", "BatchMode=yes"]
    if identity:
        import os

        ssh_cmd += ["-i", os.path.expanduser(identity)]
    ssh_cmd += [
        f"{user}@{host}",
        f'sqlite3 ~/.local/state/ai-cli/quota.db "{sql}"',
    ]

    try:
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=15)
    except Exception as exc:
        print(f"quota sync: SSH failed: {exc}", file=sys.stderr)
        return 1

    if result.returncode != 0:
        print(f"quota sync: remote command failed: {result.stderr.strip()}", file=sys.stderr)
        return 1

    rows = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("|")
        if len(parts) == 6:
            try:
                rows.append(
                    {
                        "usage_percent": float(parts[0]),
                        "session_pct": float(parts[1]) if parts[1] else None,
                        "weekly_sonnet_pct": float(parts[2]) if parts[2] else None,
                        "extra_pct": float(parts[3]) if parts[3] else None,
                        "week_start": parts[4],
                        "snapshotted_at": parts[5],
                    }
                )
            except ValueError:
                pass

    if not rows:
        print("quota sync: no snapshots on remote (or remote DB empty).")
        return 0

    conn = _get_conn()
    existing = {r[0] for r in conn.execute("SELECT snapshotted_at FROM quota_snapshots").fetchall()}
    new_rows = [r for r in rows if r["snapshotted_at"] not in existing]

    if new_rows:
        conn.executemany(
            "INSERT INTO quota_snapshots"
            " (usage_percent, session_pct, weekly_sonnet_pct, extra_pct,"
            "  week_start, snapshotted_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    r["usage_percent"],
                    r["session_pct"],
                    r["weekly_sonnet_pct"],
                    r["extra_pct"],
                    r["week_start"],
                    r["snapshotted_at"],
                )
                for r in new_rows
            ],
        )
        conn.commit()
        print(f"quota sync: pulled {len(new_rows)} new snapshot(s) from remote.")
    else:
        print("quota sync: already up to date.")

    conn.close()
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
                # Write latest snapshot to NATS KV so other services can read current
                # quota without SSHing to the local DB. Only the publisher (Hetzner)
                # writes this key — subscribers never re-publish.
                if client.js:
                    try:
                        kv = await client.js.key_value("hw_state")
                        await kv.put("quota.claude.weekly", json.dumps(payload).encode())
                    except Exception:
                        pass
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
            # No data for current week — show placeholder and kick off a scrape
            _launch_background_scrape()
            DIM = "\033[2m"
            RESET = "\033[0m"
            print(f"\U0001f4ca {DIM}-{RESET}")  # 📊 -
            return 0

        _maybe_trigger_background_scrape(rows[0]["snapshotted_at"])
        usage_pct = rows[0]["usage_percent"]
        snapshot_age_hours = (
            now - datetime.fromisoformat(rows[0]["snapshotted_at"].replace("Z", "+00:00"))
        ).total_seconds() / 3600
        stale = snapshot_age_hours > 2.0  # scrape has been failing for >2h

        ws_dt = datetime.strptime(week_start_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        elapsed_secs = (now - ws_dt).total_seconds()
        week_elapsed_pct = min(elapsed_secs / (7 * 24 * 3600) * 100.0, 100.0)

        delta = usage_pct - week_elapsed_pct

        GREEN = "\033[32m"
        YELLOW = "\033[33m"
        RED = "\033[31m"
        BLUE = "\033[34m"
        RESET = "\033[0m"

        # Quota % color: absolute level
        if usage_pct < 50:
            pct_color = GREEN
        elif usage_pct < 75:
            pct_color = YELLOW
        else:
            pct_color = RED

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

        if elapsed_secs < 24 * 3600:
            # Seedling phase (first 24h): 🌱 always shown; alert only for large positive deltas.
            # Negative delta (under pace) is never flagged early — it's noise, not signal.
            if delta < 10:
                alert = ""
                delta_color = BLUE
            elif delta < 20:
                alert = " \u26a0\ufe0f"  # ⚠️
                delta_color = YELLOW
            else:
                alert = " \U0001f6a8"  # 🚨
                delta_color = RED
            stale_suffix = " \033[2m\u23f1\033[0m" if stale else ""  # ⏱ dimmed
            print(
                f"\U0001f4ca {pct_color}{usage_pct:.0f}%{RESET}"
                f" \U0001f331{alert} {delta_color}{arrow_char}{abs(delta):.0f}%{RESET}{stale_suffix}"
            )  # 📊 N% 🌱 [alert] →X% [⏱]
        else:
            # Normal phase: standard ✅/⚠️/🚨 bands
            if delta < -5:
                icon = "\u2705"  # ✅
                delta_color = GREEN
            elif delta <= 5:
                icon = "\u26a0\ufe0f"  # ⚠️
                delta_color = YELLOW
            else:
                icon = "\U0001f6a8"  # 🚨
                delta_color = RED
            stale_suffix = " \033[2m\u23f1\033[0m" if stale else ""  # ⏱ dimmed
            print(
                f"\U0001f4ca {pct_color}{usage_pct:.0f}%{RESET} {icon} {delta_color}{arrow_char}{abs(delta):.0f}%{RESET}{stale_suffix}"
            )  # 📊 N% ✅/⚠️/🚨 →X% [⏱]
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
