"""Quota tracking — monitors Claude usage and publishes threshold events.

Polls Claude usage data via a hidden tmux window and publishes
quota.threshold.{50,75,90} events when thresholds are crossed.
Uses JetStream deduplication to avoid re-alerting the same threshold
within a calendar day. Stores snapshots and usage records in local SQLite
at ~/.local/state/ai-cli/quota.db (no external server dependency).
"""

import asyncio
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

# Windows cp1252 cannot encode the emoji used in statusline output (📊, ✅, etc.).
# Reconfigure stdout to UTF-8 with replacement on errors so emoji never crashes the process.
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

__all__ = ["QuotaSnapshot", "read_latest_snapshot"]


_STATUSLINE_RESET = "\033[0m"


@dataclass(frozen=True)
class QuotaProviderAdapter:
    """Provider-specific statusline identity and quota-window configuration."""

    label: str
    identity_rgb: tuple[int, int, int]
    window_seconds: int

    @property
    def identity_color(self) -> str:
        red, green, blue = self.identity_rgb
        return f"\033[1;38;2;{red};{green};{blue}m"


class ClaudeQuotaAdapter(QuotaProviderAdapter):
    """Claude Code quota windows use Anthropic's established statusline identity."""

    def __init__(self, label: str, window_seconds: int) -> None:
        super().__init__(label, (217, 119, 87), window_seconds)


class CodexQuotaAdapter(QuotaProviderAdapter):
    """Codex quota windows use OpenAI's established statusline identity."""

    def __init__(self, label: str, window_seconds: int) -> None:
        super().__init__(label, (16, 163, 127), window_seconds)


CLAUDE_FIVE_HOUR_QUOTA = ClaudeQuotaAdapter("cc5h", 5 * 60 * 60)
CLAUDE_WEEKLY_QUOTA = ClaudeQuotaAdapter("ccWk", 7 * 24 * 60 * 60)
CODEX_WEEKLY_QUOTA = CodexQuotaAdapter("cxWk", 7 * 24 * 60 * 60)


class QuotaStatuslineSegment:
    """Render one quota segment using a shared pace calculation and truecolor gradient."""

    # Green well behind pace, blue just behind, yellow at pace, then orange and red.
    _PACE_COLOR_ANCHORS = (
        (-10.0, (34, 197, 94)),
        (-1.0, (59, 130, 246)),
        (0.0, (234, 179, 8)),
        (5.0, (249, 115, 22)),
        (10.0, (220, 38, 38)),
    )

    def __init__(self, adapter: QuotaProviderAdapter) -> None:
        self.adapter = adapter

    def pace_delta(self, usage_pct: float, reset_epoch: float, now_epoch: float) -> float:
        """Return usage minus elapsed-cycle percentage points, clamped to the cycle."""
        window_start = reset_epoch - self.adapter.window_seconds
        elapsed_seconds = min(max(now_epoch - window_start, 0.0), self.adapter.window_seconds)
        elapsed_pct = elapsed_seconds / self.adapter.window_seconds * 100.0
        return usage_pct - elapsed_pct

    def color_for_delta(self, delta: float) -> str:
        """Interpolate the pace palette into a 24-bit foreground escape sequence."""
        anchors = self._PACE_COLOR_ANCHORS
        if delta <= anchors[0][0]:
            rgb = anchors[0][1]
        elif delta >= anchors[-1][0]:
            rgb = anchors[-1][1]
        else:
            for (left_delta, left_rgb), (right_delta, right_rgb) in pairwise(anchors):
                if left_delta <= delta <= right_delta:
                    fraction = (delta - left_delta) / (right_delta - left_delta)
                    rgb = tuple(
                        round(start + (end - start) * fraction) for start, end in zip(left_rgb, right_rgb, strict=True)
                    )
                    break
            else:  # The bounded branches above make this unreachable, but keep type checkers satisfied.
                rgb = anchors[-1][1]
        red, green, blue = rgb
        return f"\033[38;2;{red};{green};{blue}m"

    @staticmethod
    def format_signed_delta(delta: float) -> str:
        """Format a signed pace difference without hiding sub-1% direction."""
        if delta == 0:
            return "±0%"
        sign = "+" if delta > 0 else "-"
        magnitude = abs(delta)
        if magnitude < 1:
            return f"{sign}<1%"
        return f"{sign}{magnitude:.0f}%"

    def render(self, usage_pct: float, reset_epoch: float, now_epoch: float, arrow: str = "→") -> str:
        """Render provider label, pace-colored usage, and signed pace beside the acceleration arrow."""
        delta = self.pace_delta(usage_pct, reset_epoch, now_epoch)
        pace_color = self.color_for_delta(delta)
        return (
            f"{self.adapter.identity_color}{self.adapter.label}{_STATUSLINE_RESET} "
            f"{pace_color}{usage_pct:.0f}%{_STATUSLINE_RESET} "
            f"{arrow} {pace_color}{self.format_signed_delta(delta)}{_STATUSLINE_RESET}"
        )

    def render_without_pace(self, usage_pct: float) -> str:
        """Render the neutral fallback used when an upstream source omits a reset time."""
        neutral_color = self.color_for_delta(0.0)
        return (
            f"{self.adapter.identity_color}{self.adapter.label}{_STATUSLINE_RESET} "
            f"{neutral_color}{usage_pct:.0f}%{_STATUSLINE_RESET}"
        )


@dataclass
class QuotaSnapshot:
    """All metrics from a single /usage scrape."""

    weekly_all_models_pct: float  # primary quota metric — "Current week (all models)"
    session_pct: float | None = None  # "Current session"
    # Secondary per-model weekly limit — the "Current week (<model>)" line that is NOT
    # "all models". Its label is a MODEL NAME that changes over time (was "Sonnet only",
    # now "Fable", AIH-120). The pct keeps the historical field name for DB/KV back-compat;
    # weekly_model_name carries the label so the statusline can name it correctly.
    weekly_sonnet_pct: float | None = None
    weekly_model_name: str | None = None  # e.g. "Fable", "Sonnet only", "Opus"
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
    from datetime import datetime as _dt
    from datetime import timedelta as _td

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
            now_utc = _dt.now(UTC)
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
                    if candidate.astimezone(UTC) > now_utc:
                        return candidate.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
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
        now_utc = _dt.now(UTC)
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
            # The parsed wall-clock time belongs to the tz named in the text, whose
            # offset is tz_offset_h. Attaching UTC up front and then subtracting
            # that offset yields the same instant as building a naive datetime and
            # subtracting, without an intermediate naive value.
            local = _dt(year, month, (day_str and int(day_str)) or 1, hour, minute, second, tzinfo=UTC)
            return (local - _td(hours=tz_offset_h)).strftime("%Y-%m-%dT%H:%M:%SZ")
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
    now_utc = _dt.now(UTC)
    local_now = _dt.now().astimezone()
    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate.astimezone(UTC) <= now_utc:
        candidate += _td(weeks=1)
    return candidate.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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

    # Secondary per-model weekly limit (AIH-120): CC used to label this "Current week
    # (Sonnet only)"; it is now a model name ("Current week (Fable)") and will keep
    # changing as model tiers shift. Match every "Current week (<label>)" line generically
    # and take the first one that is NOT the "all models" aggregate. re.DOTALL + non-greedy
    # pairs each label with its own following "N% used" (the Fable line has no progress bar).
    weekly_lines = re.findall(
        r"Current week \(([^)]+)\).*?(\d+(?:\.\d+)?)\s*%\s*used",
        output,
        re.DOTALL | re.IGNORECASE,
    )
    weekly_model_name: str | None = None
    weekly_secondary_pct: float | None = None
    for label, pct in weekly_lines:
        if label.strip().lower() != "all models":
            weekly_model_name = label.strip()
            weekly_secondary_pct = float(pct)
            break

    extra_match = re.search(r"Extra usage.*?(\d+(?:\.\d+)?)\s*%", output, re.DOTALL | re.IGNORECASE)
    extra_not_enabled = bool(re.search(r"Extra usage not enabled", output, re.IGNORECASE))

    return QuotaSnapshot(
        weekly_all_models_pct=float(weekly_all_match.group(1)),
        session_pct=float(session_match.group(1)) if session_match else None,
        weekly_sonnet_pct=weekly_secondary_pct,
        weekly_model_name=weekly_model_name,
        extra_pct=float(extra_match.group(1)) if extra_match else (0.0 if extra_not_enabled else None),
        reset_at=_parse_reset_datetime(output),
    )


# --- CC auto-update staging reaper (AI-CLI-131) ---

#: Entries CC creates under its staging dir, named "<major>.<minor>.<patch>.<pid>.<epoch_ms>".
#: Matched strictly so the reaper can only ever delete things it positively recognises.
_CC_STAGING_ENTRY_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+\.\d+$")

#: An in-flight CC update download completes in minutes at most, so an entry untouched for
#: an hour is definitively dead and safe to remove even if some other CC session created it.
CC_STAGING_MAX_AGE_S = 3600


def _cc_staging_dir() -> Path:
    """Directory CC downloads pending updates into before promoting them."""
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "claude" / "staging"


def reap_cc_update_staging(max_age_s: int = CC_STAGING_MAX_AGE_S) -> int:
    """Delete CC update-staging entries left behind by a killed scrape session.

    The bound on AI-CLI-131: the scrape kills its CC session while CC may still be
    downloading an update, orphaning a partial binary that nothing else reaps. Running this
    on every scrape caps the directory at roughly one ``max_age_s`` window of debris instead
    of letting it grow without limit.

    Deliberately conservative — only well-formed entry directories older than ``max_age_s``
    are removed, so a download in flight for another CC session is never touched. Returns
    the number of entries removed; never raises, since bounding disk is not worth failing a
    scrape over.
    """
    staging = _cc_staging_dir()
    cutoff = time.time() - max_age_s
    removed = 0
    try:
        entries = list(staging.iterdir())
    except OSError:
        return 0

    for entry in entries:
        try:
            if not _CC_STAGING_ENTRY_RE.match(entry.name) or not entry.is_dir():
                continue
            if entry.stat().st_mtime > cutoff:
                continue
            shutil.rmtree(entry)
            removed += 1
        except OSError:
            continue

    if removed:
        # Surfaced, not silent: the */10 cron redirects to ~/.local/log/quota-scrape.log, so
        # a recurring leak shows up there rather than only as a full disk.
        print(
            f"ai quota: reaped {removed} orphaned CC update-staging entries from {staging}",
            file=sys.stderr,
        )
    return removed


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
            check=False,
        )
        # Always use a standalone detached session — never new-window inside the user's
        # session, which would cause `:N` targeting to hit the wrong session.
        result = subprocess.run(
            ["tmux", "new-session", "-d", "-s", window_name],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
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
            check=False,
        )
        subprocess.run(
            ["tmux", "set-option", "-t", target, "window-size", "latest"],
            capture_output=True,
            timeout=2,
            check=False,
        )

        # Start CC with no-op permissions (read-only scraping, never runs tools).
        #
        # DISABLE_AUTOUPDATER=1 (AI-CLI-131): CC starts a background download of any newer
        # version into ~/.cache/claude/staging/<ver>.<pid>.<ms>/ on startup. This session is
        # killed unconditionally ~15s later by the finally block below, which truncates that
        # download mid-write and orphans the partial file — nothing ever reaps it. Under the
        # */10 cron that is ~144 partials/day (~8 GB/day; the Hetzner box reached 23 GB and
        # 0 bytes free). An ephemeral read-only scrape has no business updating anything, so
        # the download must never start. Shell-prefix form rather than `new-session -e` so it
        # does not depend on tmux >= 3.2.
        subprocess.run(
            [
                "tmux",
                "send-keys",
                "-t",
                target,
                "DISABLE_AUTOUPDATER=1 claude --dangerously-skip-permissions",
                "Enter",
            ],
            capture_output=True,
            timeout=2,
            check=False,
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
                check=False,
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
                        check=False,
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
            check=False,
        )

        # Poll for usage output, max 40s. Accept as soon as "Current week (all models)"
        # and "% used" are both present, then give a grace window for the secondary
        # per-model line to render — CC renders the all-models line before the per-model
        # line ("Current week (Fable)", AIH-120), which can lag several seconds, so exiting
        # on the first valid parse silently drops weekly_sonnet_pct/weekly_model_name.
        snapshot = None
        for _ in range(200):
            time.sleep(0.2)
            cap = subprocess.run(
                ["tmux", "capture-pane", "-p", "-t", target, "-J"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if cap.returncode == 0 and "% used" in cap.stdout:
                snapshot = _parse_usage_output(cap.stdout)
                if snapshot:
                    if snapshot.weekly_sonnet_pct is None:
                        # All-models present but the per-model line not yet rendered — wait
                        # up to 8s for it to appear before accepting (it lags all-models).
                        for _ in range(40):
                            time.sleep(0.2)
                            extra_cap = subprocess.run(
                                ["tmux", "capture-pane", "-p", "-t", target, "-J"],
                                capture_output=True,
                                text=True,
                                timeout=3,
                                check=False,
                            )
                            if extra_cap.returncode == 0:
                                extra_snap = _parse_usage_output(extra_cap.stdout)
                                if extra_snap and extra_snap.weekly_sonnet_pct is not None:
                                    snapshot = extra_snap
                                    break
                    _clear_scrape_format_mismatch()
                    break
                _record_scrape_format_mismatch(cap.stdout)

        # Dismiss the dialog
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "Escape", ""],
            capture_output=True,
            timeout=2,
            check=False,
        )
        return snapshot

    except Exception:
        return None
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", window_name],
            capture_output=True,
            timeout=3,
            check=False,
        )
        # Second line of defence behind DISABLE_AUTOUPDATER=1 above: sweep anything a
        # previous (or otherwise-configured) scrape orphaned, so staging stays bounded.
        reap_cc_update_staging()


def _get_usage_via_print_mode() -> QuotaSnapshot | None:
    """Fetch /usage non-interactively via ``claude -p /usage``.

    Robust primary path (AIH-120 follow-up / AI-CLI-94). Print mode runs the slash
    command, waits for the data, prints the complete result to stdout, and exits —
    deterministic, ~1-2s, and $0 (measured: num_turns=0, zero tokens, total_cost_usd=0;
    /usage is metadata, not a model turn). It has none of the interactive-TUI hidden-pane
    scrape's nondeterminism: no readiness race (print mode blocks until the command
    completes), no async mid-render sampling (the per-model "Current week (<model>)" line
    is always present), no terminal-size/animation dependence.

    Returns None if print mode is unavailable or the output can't be parsed, so the caller
    falls back to the legacy hidden-pane scrape (belt-and-suspenders for CC versions where
    slash commands aren't yet supported under -p).
    """
    try:
        result = subprocess.run(
            ["claude", "-p", "/usage"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0 or "% used" not in (result.stdout or ""):
        return None
    return _parse_usage_output(result.stdout)


def _get_claude_usage_snapshot() -> QuotaSnapshot | None:
    """Return a QuotaSnapshot via the hidden-pane ``/usage`` scrape.

    AIH-164: print mode (``claude -p /usage``) is **retired** from this capture path — on CC
    2.1.207 it emits an insights-only view with no quota bars, so it always returned ``None``.
    The all-models weekly + 5-hour numbers now come from the official statusLine ``rate_limits``
    stdin (see :func:`quota_statusline_part`); this scrape remains the capture fallback and the
    sole source of the secondary ``Current week (<model>)`` (Fable) cap (T-06). The
    :func:`_get_usage_via_print_mode` helper is kept (dormant, unit-tested) in case a future CC
    restores quota bars under ``-p``, but is intentionally no longer called here.
    """
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
    with contextlib.suppress(Exception):
        loop.run_until_complete(client.connect())

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
                # UTC day, not local: this is only ever compared against itself as
                # a once-per-day dedupe key, and the quota window it tracks is
                # itself UTC-based.
                today = datetime.now(UTC).date().isoformat()
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
        with contextlib.suppress(Exception):
            sub = await client.nc.subscribe(f"quota.scrape.request.{machine}", cb=_on_scrape_request)

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
                with contextlib.suppress(Exception):
                    await sub.unsubscribe()
            with contextlib.suppress(Exception):
                await client.close()

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
        body += f" | {snapshot.weekly_model_name or 'Sonnet'}: {snapshot.weekly_sonnet_pct:.1f}%"
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

# AIH-164 T-06: rate-limit-aware Fable (secondary per-model cap) scrape scheduling. The Fable
# `Current week (<model>)` line is the ONLY per-model datum /usage exposes and is NOT in the
# stdin rate_limits — so it still needs the TUI scrape. Its "Per-model breakdown" is frequently
# server-side "rate limited"; back off progressively (10→20→40→80→120 min) while it stays
# unavailable so we never hammer it, and reset on a fresh capture.
_FABLE_SCRAPE_TTL_MINUTES = 30
_FABLE_BACKOFF_BASE_MINUTES = 10
_FABLE_BACKOFF_MAX_MINUTES = 120
_FABLE_BACKOFF_MAX_MISSES = 4  # 10*2^4 = 160 → capped at 120
_FABLE_BACKOFF_STATE = Path.home() / ".local" / "state" / "ai-cli" / "fable-scrape-backoff.json"


def _save_fable_backoff(state: dict) -> None:
    try:
        import json

        _FABLE_BACKOFF_STATE.parent.mkdir(parents=True, exist_ok=True)
        _FABLE_BACKOFF_STATE.write_text(json.dumps(state))
    except Exception:
        pass


def _maybe_trigger_fable_scrape(now, fable_ts: str | None) -> None:
    """Trigger the /usage scrape on a FABLE-specific cadence with progressive backoff.

    Decoupled from all-models snapshot freshness (AIH-164 T-06): T-02's rate_limits env path
    keeps the all-models snapshot fresh, so the old snapshot-age trigger would never fire and the
    Fable cap would go stale forever. Fires when the last non-null Fable snapshot is older than
    ``_FABLE_SCRAPE_TTL_MINUTES``; while the breakdown stays rate-limited (Fable not refreshing)
    the interval doubles up to ``_FABLE_BACKOFF_MAX_MINUTES``; a fresh capture resets it. Never
    raises — the statusline path must be silent.
    """
    try:
        import json
        from datetime import datetime

        state = {"last_attempt": 0.0, "misses": 0}
        if _FABLE_BACKOFF_STATE.exists():
            with contextlib.suppress(Exception):
                state.update(json.loads(_FABLE_BACKOFF_STATE.read_text()))

        fable_age_min = float("inf")
        if fable_ts is not None:
            fable_dt = datetime.fromisoformat(fable_ts.replace("Z", "+00:00"))
            fable_age_min = (now - fable_dt).total_seconds() / 60

        # Fresh Fable → reset backoff, nothing to scrape.
        if fable_age_min < _FABLE_SCRAPE_TTL_MINUTES:
            if state.get("misses"):
                state["misses"] = 0
                _save_fable_backoff(state)
            return

        # Stale/missing Fable → scrape only once the (growing) backoff interval has elapsed.
        misses = int(state.get("misses", 0))
        interval = min(_FABLE_BACKOFF_BASE_MINUTES * (2**misses), _FABLE_BACKOFF_MAX_MINUTES)
        elapsed_min = (now.timestamp() - float(state.get("last_attempt", 0.0))) / 60
        if elapsed_min >= interval:
            _launch_background_scrape()
            state["misses"] = min(misses + 1, _FABLE_BACKOFF_MAX_MISSES)
            state["last_attempt"] = now.timestamp()
            _save_fable_backoff(state)
    except Exception:
        pass


def _get_last_fable_snapshot(week_start: str):
    """Return (weekly_sonnet_pct, weekly_model_name, snapshotted_at) for the most recent snapshot
    this week whose Fable value is non-null, or (None, None, None). Unbounded (not LIMIT 3) so a
    last-good Fable value survives even after the T-02 env snapshots push it past the 3 rows the
    render reads (AIH-164 T-06 / audit F-04 interaction). Never raises."""
    import sqlite3

    from .quota_db import _get_quota_db_path, _init_db

    try:
        conn = sqlite3.connect(str(_get_quota_db_path()))
        conn.row_factory = sqlite3.Row
        _init_db(conn)
        row = conn.execute(
            "SELECT weekly_sonnet_pct, weekly_model_name, snapshotted_at FROM quota_snapshots"
            " WHERE week_start = ? AND weekly_sonnet_pct IS NOT NULL ORDER BY snapshotted_at DESC LIMIT 1",
            (week_start,),
        ).fetchone()
        conn.close()
        if row is None:
            return (None, None, None)
        return (row["weekly_sonnet_pct"], row["weekly_model_name"], row["snapshotted_at"])
    except Exception:
        return (None, None, None)


_SCRAPE_DEBUG_PATH = Path.home() / ".local" / "state" / "ai-cli" / "quota-scrape-debug.txt"


def _record_scrape_format_mismatch(raw: str) -> None:
    """Write raw scrape output to debug file and increment the mismatch counter."""
    from datetime import datetime

    from .quota_db import _get_quota_meta, _set_quota_meta

    try:
        _SCRAPE_DEBUG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SCRAPE_DEBUG_PATH.write_text(raw, encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        _set_quota_meta("scrape_format_mismatch_at", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
        current = _get_quota_meta("scrape_format_mismatch_count")
        count = int(current) + 1 if current and current.isdigit() else 1
        _set_quota_meta("scrape_format_mismatch_count", str(count))
    except Exception:
        pass


def _clear_scrape_format_mismatch() -> None:
    """Reset the mismatch counter to 0 after a successful parse."""
    from .quota_db import _set_quota_meta

    with contextlib.suppress(Exception):
        _set_quota_meta("scrape_format_mismatch_count", "0")


def _launch_background_scrape() -> None:
    """Launch `ai quota scrape` in the background if no scrape is already running.

    Must never raise — the statusline path must be silent on errors.
    """
    try:
        from datetime import datetime

        if _SCRAPE_LOCK_PATH.exists():
            lock_age_minutes = (datetime.now(UTC).timestamp() - _SCRAPE_LOCK_PATH.stat().st_mtime) / 60
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
        from datetime import datetime

        now = datetime.now(UTC)
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
            print(f", {snapshot.weekly_model_name or 'Sonnet'} {snapshot.weekly_sonnet_pct:.1f}%", end="")
        if snapshot.session_pct is not None:
            print(f", session {snapshot.session_pct:.1f}%", end="")
        print()

        record_quota_snapshot(
            usage_percent=snapshot.weekly_all_models_pct,
            session_pct=snapshot.session_pct,
            weekly_sonnet_pct=snapshot.weekly_sonnet_pct,
            weekly_model_name=snapshot.weekly_model_name,
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
        from .config import get_remote_machine

        remote = get_remote_machine(cfg)
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
        ssh_cmd += ["-i", str(Path(identity).expanduser())]
    ssh_cmd += [
        f"{user}@{host}",
        f'sqlite3 ~/.local/state/ai-cli/quota.db "{sql}"',
    ]

    try:
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=15, check=False)
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
            with contextlib.suppress(ValueError):
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
        "weekly_model_name": snapshot.weekly_model_name,
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
                # Publish to the usage-events subject for UsageConsumer ingest
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
                # Write latest snapshot to NATS KV so downstream services can read
                # current quota without SSHing to the local DB. Key is
                # machine-suffixed (AI_HOST) for multi-machine disambiguation.
                if client.js:
                    try:
                        kv = await client.js.key_value("hw_state")
                        kv_key = f"quota.claude.current.{machine}" if machine else "quota.claude.current"
                        await kv.put(kv_key, json.dumps(payload).encode())
                        # Ack for a downstream mid-run quota monitor.
                        if machine:
                            await kv.put(
                                f"quota.scrape.ack.{machine}",
                                json.dumps({"scraped_at": time.time()}).encode(),
                            )
                    except Exception:
                        pass
        finally:
            await client.close()

    with contextlib.suppress(Exception):
        asyncio.run(_do_publish())


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
                with contextlib.suppress(Exception):
                    await client.close()

        with contextlib.suppress(Exception):
            asyncio.run(_do())

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


# AIH-164: throttle env-sourced snapshot writes so the acceleration arrow keeps its ~10-min
# cadence — an un-throttled per-render write would pin the arrow to "steady" (audit F-04/AD-1).
_QUOTA_ENV_SNAPSHOT_THROTTLE_SECONDS = 600


def _record_rate_limits_env_snapshot(now) -> None:
    """AIH-164 T-02: persist CC's ``rate_limits`` (exported by the statusline as env vars) as a
    THROTTLED quota snapshot, so the official all-models weekly % (+ 5h session %) flows through
    the existing render + history path as the authoritative source.

    Records only when there is no snapshot this week, the pct changed, or the last snapshot is
    older than ``_QUOTA_ENV_SNAPSHOT_THROTTLE_SECONDS`` — preserving the sparse snapshot cadence
    the acceleration arrow depends on. The seven-day reset epoch is routed through
    ``record_quota_snapshot(reset_at=…)`` so the week anchor stays consistent across the
    statusline and ``ai quota status`` (audit F-08). No-op when the env vars are absent
    (enterprise/non-Pro-Max seat, or before the first API response).
    """
    import os
    import sqlite3
    from datetime import datetime as _dt

    pct_raw = os.environ.get("AI_CLI_QUOTA_SEVEN_DAY_PCT")
    if not pct_raw:
        return
    try:
        seven_day_pct = float(pct_raw)
    except ValueError:
        return

    five_hour_pct: float | None = None
    fh_raw = os.environ.get("AI_CLI_QUOTA_FIVE_HOUR_PCT")
    if fh_raw:
        try:
            five_hour_pct = float(fh_raw)
        except ValueError:
            five_hour_pct = None

    reset_iso: str | None = None
    reset_raw = os.environ.get("AI_CLI_QUOTA_SEVEN_DAY_RESET")
    if reset_raw:
        try:
            reset_iso = _dt.fromtimestamp(int(reset_raw), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, OverflowError, OSError):
            reset_iso = None

    from .quota_db import _get_current_week_start, _get_quota_db_path, _init_db, record_quota_snapshot

    # Throttle check against the most recent snapshot this week.
    last = None
    try:
        week_start = _get_current_week_start(now)
        conn = sqlite3.connect(str(_get_quota_db_path()))
        conn.row_factory = sqlite3.Row
        _init_db(conn)
        last = conn.execute(
            "SELECT usage_percent, snapshotted_at FROM quota_snapshots"
            " WHERE week_start = ? ORDER BY snapshotted_at DESC LIMIT 1",
            (week_start,),
        ).fetchone()
        conn.close()
    except Exception:
        last = None

    should_record = True
    if last is not None:
        try:
            last_ts = _dt.fromisoformat(last["snapshotted_at"].replace("Z", "+00:00"))
            age = (now - last_ts).total_seconds()
        except Exception:
            age = _QUOTA_ENV_SNAPSHOT_THROTTLE_SECONDS + 1
        if last["usage_percent"] == seven_day_pct and age < _QUOTA_ENV_SNAPSHOT_THROTTLE_SECONDS:
            should_record = False

    if should_record:
        record_quota_snapshot(usage_percent=seven_day_pct, session_pct=five_hour_pct, reset_at=reset_iso)


def _render_env_statusline_segment(segment_name: str) -> str:
    """Render a non-database quota segment supplied by the shell statusline."""
    if segment_name == "claude-five-hour":
        adapter = CLAUDE_FIVE_HOUR_QUOTA
        pct_name = "AI_CLI_QUOTA_FIVE_HOUR_PCT"
        reset_name = "AI_CLI_QUOTA_FIVE_HOUR_RESET"
    elif segment_name == "codex-weekly":
        try:
            window_seconds = int(os.environ["AI_CLI_QUOTA_STATUSLINE_WINDOW_SECONDS"])
        except (KeyError, ValueError):
            return ""
        if window_seconds <= 0:
            return ""
        adapter = CodexQuotaAdapter("cxWk", window_seconds)
        pct_name = "AI_CLI_QUOTA_STATUSLINE_PCT"
        reset_name = "AI_CLI_QUOTA_STATUSLINE_RESET"
    else:
        return ""

    try:
        usage_pct = float(os.environ[pct_name])
    except (KeyError, ValueError):
        return ""
    renderer = QuotaStatuslineSegment(adapter)
    try:
        reset_epoch = float(os.environ[reset_name])
    except (KeyError, ValueError):
        return renderer.render_without_pace(usage_pct) if segment_name == "claude-five-hour" else ""
    arrow = os.environ.get("AI_CLI_QUOTA_STATUSLINE_ARROW", "→")
    return renderer.render(usage_pct, reset_epoch, time.time(), arrow)


def quota_statusline_part() -> int:
    """Print a compact quota indicator for use in the statusline.

    Authoritative source (AIH-164): CC's official ``rate_limits`` stdin, exported by the
    statusline as ``AI_CLI_QUOTA_*`` env vars and persisted here as a throttled snapshot.
    Then: NATS KV (shared across machines) when local data is stale; local SQLite fast path
    when fresh. The three provider windows share ``QuotaStatuslineSegment`` for their
    pace calculation, signed display, and truecolor gradient.
    """
    import sqlite3

    from .quota_db import (
        _get_current_week_start,
        _get_quota_db_path,
        _get_reset_at,
        _init_db,
        record_quota_snapshot,
    )

    segment_name = os.environ.get("AI_CLI_QUOTA_STATUSLINE_SEGMENT")
    if segment_name:
        rendered = _render_env_statusline_segment(segment_name)
        if rendered:
            print(rendered)
        return 0

    _check_scrape_mismatch_prefix()

    try:
        from datetime import datetime

        now = datetime.now(UTC)
        # AIH-164 T-02: consume the official rate_limits env vars (throttled) BEFORE reading rows,
        # so the fresh all-models value is the newest snapshot the render below picks up.
        _record_rate_limits_env_snapshot(now)
        week_start_str = _get_current_week_start(now)

        db_path = _get_quota_db_path()
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        _init_db(conn)
        rows = conn.execute(
            "SELECT usage_percent, snapshotted_at FROM quota_snapshots"
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
                            weekly_model_name=kv.get("weekly_model_name"),
                            extra_pct=kv.get("extra_pct"),
                            reset_at=kv.get("reset_at"),
                        )
                        conn2 = sqlite3.connect(str(db_path))
                        conn2.row_factory = sqlite3.Row
                        _init_db(conn2)
                        rows = conn2.execute(
                            "SELECT usage_percent, snapshotted_at FROM quota_snapshots"
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

        usage_pct = rows[0]["usage_percent"]
        # Without the official rate_limits input, retain the legacy all-models refresh cadence.
        if not os.environ.get("AI_CLI_QUOTA_SEVEN_DAY_PCT"):
            _maybe_trigger_background_scrape(rows[0]["snapshotted_at"])
        snapshot_age_hours = (
            now - datetime.fromisoformat(rows[0]["snapshotted_at"].replace("Z", "+00:00"))
        ).total_seconds() / 3600
        stale = snapshot_age_hours > 2.0  # scrape has been failing for >2h
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
        reset_at = _get_reset_at(now)
        reset_epoch = datetime.fromisoformat(reset_at.replace("Z", "+00:00")).timestamp()
        segment = QuotaStatuslineSegment(CLAUDE_WEEKLY_QUOTA)
        elapsed_secs = max(now.timestamp() - (reset_epoch - CLAUDE_WEEKLY_QUOTA.window_seconds), 0.0)
        seedling_suffix = " \U0001f331" if elapsed_secs < 24 * 3600 else ""
        print(segment.render(usage_pct, reset_epoch, now.timestamp(), arrow_char) + seedling_suffix + stale_suffix)
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
