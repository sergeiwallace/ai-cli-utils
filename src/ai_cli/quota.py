"""Quota tracking — renders, stores and publishes Claude usage.

The seven-day and five-hour figures come from Claude Code's native ``rate_limits``
statusline payload: the statusline exports them to ``quota statusline-part`` and writes
them to a state file that ``quota publish`` reads. Nothing here scrapes ``/usage``.
The payload has no per-model (Fable) window, so that weekly value has no source until
Claude Code reports one. Snapshots and usage records live in local SQLite at
~/.local/state/ai-cli/quota.db (no external server dependency).
"""

import asyncio
import contextlib
import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from . import output as _out
from .output import Tag, Untagged

# Windows cp1252 cannot encode the emoji used in statusline output (📊, ✅, etc.).
# Reconfigure stdout to UTF-8 with replacement on errors so emoji never crashes the process.
_out.utf8_stdout_on_windows()

__all__ = ["QuotaSnapshot", "read_latest_snapshot"]


_STATUSLINE_RESET = "\033[0m"

EX_CONFIG = 78
EX_TEMPFAIL = 75


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
    """One quota reading, as stored in SQLite and published to NATS."""

    weekly_all_models_pct: float  # primary quota metric — "Current week (all models)"
    session_pct: float | None = None  # "Current session"
    # Secondary per-model weekly limit — the "Current week (<model>)" line that is NOT
    # "all models". Its label is a MODEL NAME that changes over time (was "Sonnet only",
    # now "Fable"). The pct keeps the historical field name for DB/KV back-compat;
    # weekly_model_name carries the label so the statusline can name it correctly.
    weekly_sonnet_pct: float | None = None
    weekly_model_name: str | None = None  # e.g. "Fable", "Sonnet only", "Opus"
    extra_pct: float | None = None  # "Extra usage: X%" or 0.0 if "not enabled"
    reset_at: str | None = None  # next reset as UTC ISO string, e.g. "2026-04-18T11:59:00Z"


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


def quota_status() -> int:
    """Print current quota status from local SQLite."""
    from .quota_db import get_current_status

    data = get_current_status()
    snap = data.get("latest_snapshot")
    if snap:
        pct = snap.get("usage_percent", 0)
        ts = snap.get("snapshotted_at", "?")
        _out.emit(Tag.QUOTA, "status", f"Quota: {pct:.1f}% used  (last snapshot: {ts})")
    else:
        _out.emit(Tag.QUOTA, "status", "Quota: no snapshots yet")

    burn = data.get("burn_rate", {})
    if burn and burn.get("expected_pct_per_day", 0) > 0:
        actual = burn.get("actual_pct_per_day", 0)
        expected = burn.get("expected_pct_per_day", 0)
        mult = burn.get("multiplier", 0)
        _out.emit(
            Tag.QUOTA, "status", f"Burn rate: {actual:.2f}%/day actual vs {expected:.2f}%/day expected ({mult:.1f}x)"
        )

    days = data.get("days_remaining")
    if days is not None:
        _out.emit(Tag.QUOTA, "status", f"Days to reset: {days:.1f}")

    alerts = data.get("alerts", [])
    for alert in alerts:
        _out.emit(Tag.QUOTA, "status", f"  {alert}")

    return 0


def quota_history() -> int:
    """Print weekly quota usage history from local SQLite."""
    from .quota_db import get_weekly_history

    history = get_weekly_history()
    if not history:
        _out.emit(Tag.QUOTA, "history", "No history yet.")
        return 0

    _out.emit(Tag.QUOTA, "history", f"{'Week':24} {'Peak %':8} {'Tokens':>12} {'Snapshots':>10}")
    _out.emit(Tag.QUOTA, "history", "-" * 58)
    for week in history:
        _out.emit(
            Tag.QUOTA,
            "history",
            f"{week['week_start']:24} {week.get('peak_percent', 0):7.1f}% "
            f"{week.get('total_consumed', 0):12,} {week.get('snapshot_count', 0):10}",
        )
    return 0


_LOCAL_SNAPSHOT_TTL_MINUTES = 30


def _remote_quota_query_command(sql: str) -> str:
    """Build the shell command that runs ``sql`` against the remote quota DB.

    Uses Python's standard library ``sqlite3`` module on the remote side, so the
    remote host needs ``python3`` but not the ``sqlite3`` command line tool, which
    several platforms (for example Fedora) do not install by default.  The DB is
    opened read-only so a missing file fails instead of being created.  Rows print
    pipe-separated with NULL as an empty field, the format the caller parses.
    """
    script = (
        "import os, pathlib, sqlite3\n"
        'db = pathlib.Path(os.path.expanduser("~/.local/state/ai-cli/quota.db"))\n'
        'conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)\n'
        f"for row in conn.execute({sql!r}):\n"
        '    print("|".join("" if v is None else str(v) for v in row))\n'
    )
    return f"python3 -c {shlex.quote(script)}"


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
        _out.emit(Tag.QUOTA, "sync", f"quota sync: could not load config: {exc}", err=True)
        return EX_CONFIG

    if not host or not user:
        _out.emit(Tag.QUOTA, "sync", "quota sync: no remote host configured in [remote]", err=True)
        return EX_CONFIG

    sql = (
        "SELECT usage_percent, session_pct, weekly_sonnet_pct, extra_pct,"
        " week_start, snapshotted_at"
        " FROM quota_snapshots"
        " ORDER BY snapshotted_at DESC LIMIT 20;"
    )
    ssh_cmd = ["ssh", "-p", port, "-o", "ConnectTimeout=5", "-o", "BatchMode=yes"]
    if identity:
        ssh_cmd += ["-i", str(Path(identity).expanduser())]
    ssh_cmd += [f"{user}@{host}", _remote_quota_query_command(sql)]

    try:
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=15, check=False)
    except Exception as exc:
        _out.emit(Tag.QUOTA, "sync", f"quota sync: SSH failed: {exc}", err=True)
        return EX_TEMPFAIL

    if result.returncode != 0:
        _out.emit(Tag.QUOTA, "sync", f"quota sync: remote command failed: {result.stderr.strip()}", err=True)
        return EX_TEMPFAIL

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
        _out.emit(Tag.QUOTA, "sync", "quota sync: no snapshots on remote (or remote DB empty).")
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
        _out.emit(Tag.QUOTA, "sync", f"quota sync: pulled {len(new_rows)} new snapshot(s) from remote.")
    else:
        _out.emit(Tag.QUOTA, "sync", "quota sync: already up to date.")

    conn.close()
    return 0


def _publish_quota_snapshot(snapshot: QuotaSnapshot) -> None:
    """Publish a quota snapshot to NATS for cross-machine sync.

    Publishes to subject ``quota.snapshot`` so quota subscribers on other
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
                    except Exception:
                        pass
        finally:
            await client.close()

    with contextlib.suppress(Exception):
        asyncio.run(_do_publish())


_QUOTA_STATE_FILE_DEFAULT = "~/.claude/state/quota.json"
_QUOTA_STATE_MAX_AGE_SECONDS_DEFAULT = 1800


def _quota_state_settings() -> tuple[Path, float]:
    """Return the statusline quota state file and its freshness limit from ``[quota]`` in config.toml."""
    try:
        from .config import load_config

        section = load_config().get("quota", {})
    except Exception:
        section = {}
    if not isinstance(section, dict):
        section = {}
    path = Path(str(section.get("state_file", _QUOTA_STATE_FILE_DEFAULT))).expanduser()
    try:
        max_age = float(section.get("max_age_seconds", _QUOTA_STATE_MAX_AGE_SECONDS_DEFAULT))
    except (TypeError, ValueError):
        max_age = float(_QUOTA_STATE_MAX_AGE_SECONDS_DEFAULT)
    return path, max_age


def _current_window(state: dict, window: str, now: float, max_age: float) -> tuple[float, float] | None:
    """Return ``(pct, reset_epoch)`` for a window whose reading is current, else None.

    Current means a percentage in 0-100, a reset time that has not passed and lies within one
    window length, and an observation
    (``<window>_updated_at``) no older than ``max_age`` seconds: the rule ``quota-pct`` reports
    as ``<window>_status == "ok"``, applied here to the state file directly.
    """

    def _number(value: Any) -> float | None:
        return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None

    pct = _number(state.get(f"{window}_pct"))
    reset = _number(state.get(f"{window}_reset"))
    observed_raw = state.get(f"{window}_updated_at")
    if pct is None or reset is None or not isinstance(observed_raw, str) or not 0 <= pct <= 100:
        return None
    try:
        observed = datetime.fromisoformat(observed_raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
    window_seconds = (
        CLAUDE_WEEKLY_QUOTA.window_seconds if window == "seven_day" else CLAUDE_FIVE_HOUR_QUOTA.window_seconds
    )
    if reset < now or reset - now > window_seconds or now - observed > max_age:
        return None
    return pct, reset


def quota_publish() -> int:
    """Publish the statusline's native seven-day quota reading when it is current.

    Reads the state file the statusline writes from Claude Code's ``rate_limits`` payload. When
    the seven-day window is current it publishes the same three items a quota snapshot always
    has (``quota.snapshot``, ``hw.events.usage.claude.snapshot`` and the ``hw_state`` KV key
    ``quota.claude.current.<host>``). A missing, unreadable, stale or reset state file publishes
    nothing and still exits 0: an idle host has no fresh reading, which is not a failure.
    """
    path, max_age = _quota_state_settings()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _out.emit(Tag.QUOTA, "publish", f"quota publish: no readable state file at {path} ({type(exc).__name__})")
        return 0
    if not isinstance(state, dict):
        _out.emit(Tag.QUOTA, "publish", f"quota publish: state file {path} is not a JSON object")
        return 0

    now = time.time()
    seven_day = _current_window(state, "seven_day", now, max_age)
    if seven_day is None:
        _out.emit(Tag.QUOTA, "publish", "quota publish: no current seven-day reading; nothing published")
        return 0
    five_hour = _current_window(state, "five_hour", now, max_age)

    pct, reset_epoch = seven_day
    snapshot = QuotaSnapshot(
        weekly_all_models_pct=pct,
        session_pct=five_hour[0] if five_hour is not None else None,
        reset_at=datetime.fromtimestamp(reset_epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    _publish_quota_snapshot(snapshot)
    _out.emit(Tag.QUOTA, "publish", f"quota publish: seven-day {pct:.1f}% sent")
    return 0


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


# Throttle env-sourced snapshot writes so the acceleration arrow keeps its ~10-min
# cadence — an un-throttled per-render write would pin the arrow to "steady" (audit F-04/AD-1).
_QUOTA_ENV_SNAPSHOT_THROTTLE_SECONDS = 600


def _record_rate_limits_env_snapshot(now) -> None:
    """Persist CC's ``rate_limits`` (exported by the statusline as env vars) as a
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

    Authoritative source: CC's official ``rate_limits`` stdin, exported by the
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
            _out.raw(Untagged.STATUSLINE, rendered)
        return 0

    try:
        from datetime import datetime

        now = datetime.now(UTC)
        # Consume the official rate_limits env vars (throttled) BEFORE reading rows,
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
        # This keeps statuslines on different hosts aligned with what this host last published.
        local_stale = (
            not rows
            or (now - datetime.fromisoformat(rows[0]["snapshotted_at"].replace("Z", "+00:00"))).total_seconds() / 60
            >= _LOCAL_SNAPSHOT_TTL_MINUTES
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
            # No seven-day value from any source: render nothing. The statusline never
            # starts a scrape; the value arrives with Claude Code's next rate_limits payload.
            return 0

        usage_pct = rows[0]["usage_percent"]
        snapshot_age_hours = (
            now - datetime.fromisoformat(rows[0]["snapshotted_at"].replace("Z", "+00:00"))
        ).total_seconds() / 3600
        stale = snapshot_age_hours > 2.0  # no fresh value for >2h
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
        _out.raw(
            Untagged.STATUSLINE,
            segment.render(usage_pct, reset_epoch, now.timestamp(), arrow_char) + seedling_suffix + stale_suffix,
        )
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
