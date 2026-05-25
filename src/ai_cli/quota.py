"""Quota tracking — monitors Claude usage and publishes threshold events.

Polls Claude usage data via a hidden tmux window and publishes
quota.threshold.{50,75,90} events when thresholds are crossed.
Uses JetStream deduplication to avoid re-alerting the same threshold
within a calendar day. Stores snapshots and usage records in local SQLite
at ~/.local/state/ai-cli/quota.db (no external server dependency).
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

# Windows cp1252 cannot encode the emoji used in statusline output (📊, ✅, etc.).
# Reconfigure stdout to UTF-8 with replacement on errors so emoji never crashes the process.
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = ["QuotaSnapshot", "read_latest_snapshot"]


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
        r"week\s*\(all\s*models\)[^·\n]*[·\u00b7\u2019·]\s*[Rr]eset[s]?\s+" r"(\d{1,2})(?::(\d{2}))?\s*([AP]M?)\b",
        text,
        re.IGNORECASE,
    )

    if not m:
        # CC v2.1.112 format: "Resets Apr 23 at 3pm (America/New_York)"
        # Month + day + hour-only time (no colon) + IANA timezone in parens.
        iana_fmt = re.search(
            r"[Rr]eset[s]?\s+"
            r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?"
            r"|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?"
            r"|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
            r"\s+(\d{1,2})"
            r"[^(]*?"
            r"(\d{1,2})(?::(\d{2}))?\s*([AP]M?)"
            r"[^(]*\(([A-Za-z/_]+)\)",
            text,
            re.IGNORECASE,
        )
        if iana_fmt:
            from zoneinfo import ZoneInfo

            month_str, day_str, h, mi, ampm, iana_name = iana_fmt.groups()
            month = _MONTH_MAP.get(month_str.lower())
            if not month:
                return None
            now_utc = _dt.now(_tz.utc)
            day = int(day_str)
            hour = int(h)
            minute = int(mi) if mi else 0
            ap = ampm.upper().rstrip(".")
            if ap == "PM" and hour < 12:
                hour += 12
            elif ap == "AM" and hour == 12:
                hour = 0
            try:
                tz_info = ZoneInfo(iana_name)
                # Use current or next year so the date is always in the future.
                for year in (now_utc.year, now_utc.year + 1):
                    candidate = _dt(year, month, day, hour, minute, 0, tzinfo=tz_info)
                    if candidate.astimezone(_tz.utc) > now_utc:
                        return candidate.astimezone(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                pass
            return None

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
    # that time (weekly period). If the time has already passed today, advance
    # by one week (this only affects CC versions pre-dating the IANA format).
    now_utc = _dt.now(_tz.utc)
    local_now = _dt.now().astimezone()
    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate.astimezone(_tz.utc) <= now_utc:
        candidate += _td(weeks=1)
    return candidate.astimezone(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_usage_output(output: str) -> QuotaSnapshot | None:
    """Parse the text output of /usage into a QuotaSnapshot.

    CC v2.1.114+ format (progress bar on separate line):

        Current week (all models)
        █████████████                                      26% used
        Resets Apr 23 at 3pm (America/New_York)

    CC v2.1.112 format (one label per line, progress bar below, then "N% used"):

        Current week (all models)
        Resets Apr 23 at 3pm (America/New_York)      3% used

    Older format (inline):

        Current week (all models) · Resets 6:59am
        Current week (all models): 86% used

    ``strict`` / ``strict=False`` distinction is preserved for the scrape
    loop's two-phase fallback logic but no longer rejects valid data based on
    the "does not include other devices" disclaimer.  In CC v2.1.114+ that
    disclaimer appears in the contributing-factors section regardless of
    whether the main weekly figure is API data — filtering on it caused every
    scrape to return None.  "Scanning local sessions" is similarly scoped to
    the details section; it is safe to parse the headline numbers while the
    detail section is still loading.
    """
    # Reject only while the MAIN quota section itself is absent — i.e. before
    # the "Current week (all models)" line has rendered at all.  Once it's
    # present the percentage is reliable.  "Scanning local sessions" and
    # "does not include other devices" refer only to the contributing-factors
    # detail section below and must not block parsing of the headline figure.
    if not re.search(r"Current week \(all models\)", output, re.IGNORECASE):
        return None

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
    """Scrape /usage from a hidden tmux session running a bare CC session.

    Always creates a fully isolated detached tmux session (never new-window in the
    user's session). Uses the session name as the target throughout — unambiguous,
    never accidentally targets the user's active session.
    """
    window_name = "ai-quota-scrape"
    try:
        # Kill any stale scrape session left by a previous failed run
        subprocess.run(
            ["tmux", "kill-session", "-t", window_name],
            capture_output=True,
            timeout=3,
        )
        # Always use a standalone detached session — never new-window inside the user's
        # session, which would cause `:N` targeting to hit the wrong session.
        result = subprocess.run(
            ["tmux", "new-session", "-d", "-s", window_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        target = window_name  # session-name target — unambiguous across all tmux contexts

        # Resize to a generous size so the full /usage dialog fits without scrolling.
        # The dialog spans ~25 lines; a small default pane causes the label lines to
        # scroll off before capture-pane can read them.
        # NOTE: resize-window sets window-size=manual on the session as a side effect;
        # scoped to the isolated scrape session, so it never affects the user's session.
        subprocess.run(
            ["tmux", "resize-window", "-t", target, "-x", "220", "-y", "60"],
            capture_output=True,
            timeout=2,
        )
        subprocess.run(
            ["tmux", "set-option", "-t", target, "window-size", "latest"],
            capture_output=True,
            timeout=2,
        )

        # Start CC with no-op permissions (read-only scraping, never runs tools)
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "claude --dangerously-skip-permissions", "Enter"],
            capture_output=True,
            timeout=2,
        )

        # Poll for the CC prompt indicator (❯) — startup takes ~4s.
        # CC may show a "trust this folder" dialog first; dismiss it with Enter,
        # then continue polling for the real interactive prompt.
        # Total budget: 150 × 0.2s = 30s (trust dialog + post-dismiss startup).
        ready = False
        trust_dismissed = False
        for _ in range(150):  # up to 30s
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
                if not trust_dismissed and (
                    "trust this folder" in output.lower()
                    or "yes, i trust" in output.lower()
                    or "enter to confirm" in output.lower()
                ):
                    subprocess.run(
                        ["tmux", "send-keys", "-t", target, "Enter"],
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

        # Poll for usage output, max 40s. Accept as soon as "Current week (all models)"
        # and "% used" are both present, then give up to 2s grace for Sonnet data to
        # render — CC renders the all-models line before the Sonnet-only line, so
        # exiting on first valid parse silently drops weekly_sonnet_pct.
        snapshot = None
        for _ in range(200):
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
                    if snapshot.weekly_sonnet_pct is None:
                        # All-models data present but Sonnet not yet rendered — wait
                        # up to 2s for the Sonnet line to appear before accepting.
                        for _ in range(10):
                            time.sleep(0.2)
                            extra_cap = subprocess.run(
                                ["tmux", "capture-pane", "-p", "-t", target, "-J"],
                                capture_output=True,
                                text=True,
                                timeout=3,
                            )
                            if extra_cap.returncode == 0:
                                extra_snap = _parse_usage_output(extra_cap.stdout)
                                if extra_snap and extra_snap.weekly_sonnet_pct is not None:
                                    snapshot = extra_snap
                                    break
                    _clear_scrape_format_mismatch()
                    break
                else:
                    _record_scrape_format_mismatch(cap.stdout)

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
        subprocess.run(
            ["tmux", "kill-session", "-t", window_name],
            capture_output=True,
            timeout=3,
        )


def _get_claude_usage_snapshot() -> QuotaSnapshot | None:
    """Return a QuotaSnapshot from a hidden tmux window, or None if unavailable."""
    return _scrape_usage_hidden_pane()


def read_latest_snapshot() -> QuotaSnapshot | None:
    """Return the most recent stored quota snapshot from local SQLite.

    Reads from the local DB without scraping — fast and safe to call from
    library code. Returns None if no snapshots have been recorded yet.
    """
    from .quota_db import get_current_status

    status = get_current_status()
    snap = status.get("latest_snapshot")
    if snap is None:
        return None
    return QuotaSnapshot(
        weekly_all_models_pct=float(snap["usage_percent"]),
        reset_at=status.get("reset_at"),
    )


def quota_watch(poll_interval: int = 300) -> int:
    """Run the quota-watch daemon (raw entry point — no PID guard).

    Polls Claude usage and fires threshold alerts via Notifier when thresholds
    are crossed. Also publishes to NATS when available. Circus owns restart.
    Exit codes: 0 = clean stop (KeyboardInterrupt), 1 = unrecoverable error.
    """
    import os
    import threading

    from .messaging import NATSClient
    from .notifications import Notifier

    machine = os.environ.get("AI_HOST", "")

    client = NATSClient()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(client.connect())
    except Exception:
        pass

    notifier = Notifier()
    thresholds = [50, 75, 90]
    alerted_today: dict[int, str] = {}

    print(f"ai quota watch — polling every {poll_interval}s (Ctrl+C to stop)")

    # Start background NATS listener for on-demand scrape requests + heartbeat.
    stop_event = threading.Event()
    if machine:
        listener_thread = threading.Thread(
            target=_run_nats_quota_listener,
            args=(machine,),
            kwargs={"stop_event": stop_event},
            daemon=True,
            name="nats-quota-listener",
        )
        listener_thread.start()

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
                        if client.nc:
                            try:
                                loop.run_until_complete(client.publish(subject, payload))
                            except Exception as e:
                                print(f"[quota-watch] failed to publish: {e}", file=sys.stderr)
                        alerted_today[threshold] = today
                        print(f"[quota-watch] threshold {threshold}% crossed (usage: {usage:.1f}%)")
                        _notify_threshold(notifier, threshold, snapshot)

            time.sleep(poll_interval)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        if client.nc:
            loop.run_until_complete(client.close())
        loop.close()

    return 0


def _run_nats_quota_listener(machine: str, *, stop_event: "Any | None" = None) -> None:
    """NATS listener for on-demand scrape requests and periodic heartbeat.

    Runs in a daemon thread started by quota_watch. Subscribes to
    ``quota.scrape.request.{machine}`` and publishes a heartbeat to
    ``hw_state[quota_watch.heartbeat.{machine}]`` every 60 seconds.
    """
    import threading as _threading

    _stop = stop_event if stop_event is not None else _threading.Event()

    async def _loop() -> None:
        from .messaging import NATSClient

        try:
            from .config import load_config

            cfg = load_config()
            nats_servers = cfg.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
        except Exception:
            nats_servers = ["nats://localhost:4222"]

        client = NATSClient(servers=nats_servers)
        try:
            await asyncio.wait_for(client.connect(), timeout=5.0)
        except Exception:
            return

        if not client.nc:
            return

        async def _on_scrape_request(msg: Any) -> None:
            # Dedup: if scrape lock exists a scrape is already in-flight.
            if not _SCRAPE_LOCK_PATH.exists():
                _launch_background_scrape()

        sub = None
        try:
            sub = await client.nc.subscribe(f"quota.scrape.request.{machine}", cb=_on_scrape_request)
        except Exception:
            pass

        heartbeat_interval = 60.0
        last_heartbeat = 0.0

        try:
            while not _stop.is_set():
                now = time.time()
                if client.js and now - last_heartbeat >= heartbeat_interval:
                    try:
                        kv = await client.js.key_value("hw_state")
                        await kv.put(
                            f"quota_watch.heartbeat.{machine}",
                            json.dumps({"ts": now}).encode(),
                        )
                        last_heartbeat = now
                    except Exception:
                        pass
                await asyncio.sleep(2.0)
        finally:
            if sub is not None:
                try:
                    await sub.unsubscribe()
                except Exception:
                    pass
            try:
                await client.close()
            except Exception:
                pass

    event_loop = asyncio.new_event_loop()
    try:
        event_loop.run_until_complete(_loop())
    except Exception:
        pass
    finally:
        event_loop.close()


def _notify_threshold(notifier: Any, threshold: int, snapshot: QuotaSnapshot) -> None:
    """Send quota threshold alert via Notifier."""
    usage = snapshot.weekly_all_models_pct
    title = f"Claude quota {threshold}% threshold crossed"
    body = f"Weekly (all models): {usage:.1f}%"
    if snapshot.weekly_sonnet_pct is not None:
        body += f" | Sonnet: {snapshot.weekly_sonnet_pct:.1f}%"
    if snapshot.session_pct is not None:
        body += f" | Session: {snapshot.session_pct:.1f}%"
    if threshold >= 90:
        body += " — slow down!"
    priority = "urgent" if threshold >= 90 else "high" if threshold >= 75 else "default"
    tags = ["rotating_light"] if threshold >= 90 else ["warning"]
    try:
        notifier.send(title, body, priority=priority, tags=tags, source="quota-watch")
    except Exception as exc:
        print(f"[quota-watch] notification failed: {exc}", file=sys.stderr)


def _print_mismatch_warning() -> None:
    """Print a warning if the scrape format mismatch flag is set. Silent on error."""
    try:
        from .quota_db import _get_quota_meta

        count = _get_quota_meta("scrape_format_mismatch_count")
        at = _get_quota_meta("scrape_format_mismatch_at")
        if count and int(count) > 0:
            print(
                f"\n⚠  Scrape parse failure — CC /usage format may have changed.\n"
                f"   Raw output saved to: {_SCRAPE_DEBUG_PATH}\n"
                f"   Last failure: {at or 'unknown'}",
                file=sys.stderr,
            )
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

    _print_mismatch_warning()
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
_SCRAPER_BROKEN_PREFIX = "🚨 BROKEN 🚨 "
_SCRAPE_DEBUG_PATH = Path.home() / ".local" / "state" / "ai-cli" / "quota-scrape-debug.txt"


def _record_scrape_format_mismatch(raw: str) -> None:
    """Write raw scrape output to debug file and increment the mismatch counter."""
    from datetime import datetime, timezone
    from .quota_db import _get_quota_meta, _set_quota_meta

    try:
        _SCRAPE_DEBUG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SCRAPE_DEBUG_PATH.write_text(raw, encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        _set_quota_meta("scrape_format_mismatch_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        current = _get_quota_meta("scrape_format_mismatch_count")
        count = int(current) + 1 if current and current.isdigit() else 1
        _set_quota_meta("scrape_format_mismatch_count", str(count))
    except Exception:
        pass


def _clear_scrape_format_mismatch() -> None:
    """Reset the mismatch counter to 0 after a successful parse."""
    from .quota_db import _set_quota_meta

    try:
        _set_quota_meta("scrape_format_mismatch_count", "0")
    except Exception:
        pass


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
        _print_mismatch_warning()
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
        from .config import load_config

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
    import os
    import uuid as _uuid

    from .messaging import NATSClient

    try:
        from .config import load_config

        cfg = load_config()
        nats_servers = cfg.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
    except Exception:
        nats_servers = ["nats://localhost:4222"]

    payload: dict = {
        "usage_percent": snapshot.weekly_all_models_pct,
        "session_pct": snapshot.session_pct,
        "weekly_sonnet_pct": snapshot.weekly_sonnet_pct,
        "extra_pct": snapshot.extra_pct,
        "reset_at": snapshot.reset_at,
        "ts": time.time(),
    }

    machine = os.environ.get("AI_HOST", "")

    async def _do_publish() -> None:
        client = NATSClient(servers=nats_servers)
        try:
            await client.connect()
            if client.nc:
                await client.publish("quota.snapshot", payload)
                # Publish to ai-core subject for UsageConsumer ingest
                hw_payload = {
                    "id": str(_uuid.uuid4()),
                    "machine": machine,
                    "used_pct": snapshot.weekly_all_models_pct,
                    "tokens_used": None,
                    "tokens_limit": None,
                    "reset_at": snapshot.reset_at,
                    "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "raw": json.dumps(payload),
                }
                await client.publish("hw.events.usage.claude.snapshot", hw_payload)
                # Write latest snapshot to NATS KV so aido and other services can read
                # current quota without SSHing to the local DB. Key is
                # machine-suffixed (AI_HOST) for multi-machine disambiguation.
                if client.js:
                    try:
                        kv = await client.js.key_value("hw_state")
                        kv_key = f"quota.claude.current.{machine}" if machine else "quota.claude.current"
                        await kv.put(kv_key, json.dumps(payload).encode())
                        # Ack for aido mid-run quota monitor (AI-CLI-57 / AIDO-48 T-04).
                        if machine:
                            await kv.put(
                                f"quota.scrape.ack.{machine}",
                                json.dumps({"scraped_at": time.time()}).encode(),
                            )
                    except Exception:
                        pass
        finally:
            await client.close()

    try:
        asyncio.run(_do_publish())
    except Exception:
        pass


def _try_read_kv_snapshot() -> dict | None:
    """Read the latest quota snapshot from NATS KV (shared across machines).

    Returns the raw payload dict or None if NATS is unreachable or the key
    doesn't exist. Capped at 300ms total via a daemon thread join — never
    blocks the statusline for longer than that.

    Reads from ``quota.claude.current.{AI_HOST}`` when ``AI_HOST`` is
    set, falling back to the legacy ``quota.claude.current`` key otherwise.
    """
    import os
    import threading

    machine = os.environ.get("AI_HOST", "")
    kv_key = f"quota.claude.current.{machine}" if machine else "quota.claude.current"
    result: list[dict | None] = [None]

    def _read() -> None:
        try:
            from .config import load_config as _load_config

            cfg = _load_config()
            nats_servers = cfg.get("messaging", {}).get("nats_servers")
        except Exception:
            return
        if not nats_servers:
            return

        from .messaging import NATSClient

        async def _do() -> None:
            client = NATSClient(servers=nats_servers)
            try:
                await asyncio.wait_for(client.connect(), timeout=0.25)
                if client.js:
                    kv = await asyncio.wait_for(client.js.key_value("hw_state"), timeout=0.1)
                    entry = await asyncio.wait_for(kv.get(kv_key), timeout=0.1)
                    if entry.value is not None:
                        result[0] = json.loads(entry.value)
            except Exception:
                pass
            finally:
                try:
                    await client.close()
                except Exception:
                    pass

        try:
            asyncio.run(_do())
        except Exception:
            pass

    t = threading.Thread(target=_read, daemon=True)
    t.start()
    t.join(timeout=0.3)
    return result[0]


def _check_scrape_mismatch_prefix() -> None:
    """Write the broken-scraper prefix to stdout if mismatch count > 0. Silent on error."""
    try:
        from .quota_db import _get_quota_meta

        count = _get_quota_meta("scrape_format_mismatch_count")
        if count and int(count) > 0:
            sys.stdout.write(_SCRAPER_BROKEN_PREFIX)
    except Exception:
        pass


def quota_statusline_part() -> int:
    """Print a compact quota indicator for use in the statusline.

    Primary source: NATS KV (shared across machines) when local data is stale.
    Fast path: local SQLite when data is fresh (no network call).
    Outputs: {pace_icon} {usage_pct:.0f}% {direction}{delta:.0f}%
    where delta = usage_pct - week_elapsed_pct.
    """
    import sqlite3

    from .quota_db import (
        _get_current_week_start,
        _get_quota_db_path,
        _init_db,
        record_quota_snapshot,
    )

    _check_scrape_mismatch_prefix()

    try:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        week_start_str = _get_current_week_start(now)

        db_path = _get_quota_db_path()
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        _init_db(conn)
        rows = conn.execute(
            "SELECT usage_percent, snapshotted_at, weekly_sonnet_pct FROM quota_snapshots"
            " WHERE week_start = ? ORDER BY snapshotted_at DESC LIMIT 3",
            (week_start_str,),
        ).fetchall()
        conn.close()

        # If local data is stale (>TTL) or absent, try NATS KV for the shared value.
        # This keeps Mac and Hetzner statuslines aligned without requiring a local scrape.
        local_stale = (
            not rows
            or (now - datetime.fromisoformat(rows[0]["snapshotted_at"].replace("Z", "+00:00"))).total_seconds() / 60
            >= _SCRAPE_TTL_MINUTES
        )
        if local_stale:
            kv = _try_read_kv_snapshot()
            if kv is not None:
                kv_ts = kv.get("ts", 0.0)
                local_ts = (
                    datetime.fromisoformat(rows[0]["snapshotted_at"].replace("Z", "+00:00")).timestamp()
                    if rows
                    else 0.0
                )
                if kv_ts > local_ts:
                    # KV has fresher data — persist it locally and use it
                    try:
                        record_quota_snapshot(
                            usage_percent=kv["usage_percent"],
                            session_pct=kv.get("session_pct"),
                            weekly_sonnet_pct=kv.get("weekly_sonnet_pct"),
                            extra_pct=kv.get("extra_pct"),
                            reset_at=kv.get("reset_at"),
                        )
                        conn2 = sqlite3.connect(str(db_path))
                        conn2.row_factory = sqlite3.Row
                        _init_db(conn2)
                        rows = conn2.execute(
                            "SELECT usage_percent, snapshotted_at, weekly_sonnet_pct FROM quota_snapshots"
                            " WHERE week_start = ? ORDER BY snapshotted_at DESC LIMIT 3",
                            (week_start_str,),
                        ).fetchall()
                        conn2.close()
                    except Exception:
                        pass

        if not rows:
            # No data anywhere — show placeholder and kick off a scrape
            _launch_background_scrape()
            DIM = "\033[2m"
            RESET = "\033[0m"
            print(f"\U0001f4ca {DIM}-{RESET}")  # 📊 -
            return 0

        _maybe_trigger_background_scrape(rows[0]["snapshotted_at"])
        usage_pct = rows[0]["usage_percent"]
        # Use the most recent non-None Sonnet value — a single scrape that fails to parse
        # the Sonnet breakdown produces None, which would otherwise mask valid older data.
        sonnet_pct = next(
            (r["weekly_sonnet_pct"] for r in rows if r["weekly_sonnet_pct"] is not None),
            None,
        )
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
        DIM = "\033[2m"
        RESET = "\033[0m"

        # Quota % color: absolute level
        if usage_pct < 50:
            pct_color = GREEN
        elif usage_pct < 75:
            pct_color = YELLOW
        else:
            pct_color = RED

        # Terminal width — passed by statusline-command.sh via AI_CLI_STATUSLINE_COLS
        _cols = int(os.environ.get("AI_CLI_STATUSLINE_COLS", "0"))
        BOLD_CYAN = "\033[1;36m"
        BOLD_MAG = "\033[1;35m"
        week_label = "Week" if _cols >= 80 else "W"
        son_label = "Son" if _cols >= 80 else "S"

        # Sonnet part — fire scrape if absent so field populates on next refresh
        if sonnet_pct is None:
            _launch_background_scrape()
            sonnet_part = f"{BOLD_MAG}{son_label}{RESET} 🤖 {DIM}-% →-%{RESET}"
        else:
            if sonnet_pct < 50:
                s_color = GREEN
            elif sonnet_pct < 75:
                s_color = YELLOW
            else:
                s_color = RED
            sonnet_delta = sonnet_pct - week_elapsed_pct
            if sonnet_delta > 25:
                s_delta_color = RED
                s_icon = "\U0001f6a8"  # 🚨
            elif sonnet_delta > 10:
                s_delta_color = YELLOW
                s_icon = "⚠️"
            else:
                s_delta_color = GREEN
                s_icon = "✅"
            s_sign = "+" if sonnet_delta > 0 else ""
            sonnet_part = (
                f"{BOLD_MAG}{son_label}{RESET} 🤖 {s_color}{sonnet_pct:.0f}%{RESET}"
                f" {s_delta_color}→{s_sign}{sonnet_delta:.0f}%{RESET}"
            )

        # Arrow: acceleration direction (requires \u22653 snapshots)
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

        stale_suffix = " \033[2m\u23f1\033[0m" if stale else ""  # ⏱ dimmed
        sign = "+" if delta > 0 else ""

        if elapsed_secs < 24 * 3600:
            # Seedling phase (first 24h): 🌱 always shown; informational only, no alarms.
            delta_color = BLUE
            print(
                f"{BOLD_CYAN}{week_label}{RESET} \U0001f4ca {pct_color}{usage_pct:.0f}%{RESET}"
                f" {delta_color}{arrow_char}{sign}{abs(delta):.0f}%{RESET} \U0001f331{stale_suffix}"
                f" | {sonnet_part}"
            )  # W 📊 N% →X% 🌱 [⏱] | Son M% →Y%
        else:
            # Normal phase: ≤10% over = on track, 10-25% = running hot, >25% = significantly over
            if delta <= 10:
                icon = "\u2705"  # ✅
                delta_color = GREEN
            elif delta <= 25:
                icon = "\u26a0\ufe0f"  # ⚠️
                delta_color = YELLOW
            else:
                icon = "\U0001f6a8"  # 🚨
                delta_color = RED
            s_pace = f" {s_icon}" if sonnet_pct is not None else ""
            print(
                f"{BOLD_CYAN}{week_label}{RESET} \U0001f4ca {pct_color}{usage_pct:.0f}%{RESET}"
                f" {delta_color}{arrow_char}{sign}{abs(delta):.0f}%{RESET} {icon}{stale_suffix}"
                f" | {sonnet_part}{s_pace}"
            )  # W 📊 N% →X% ✅/⚠️/🚨 [⏱] | Son 🤖 M% →Y% ✅/⚠️/🚨
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
