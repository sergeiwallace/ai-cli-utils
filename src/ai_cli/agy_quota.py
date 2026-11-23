"""Antigravity (agy) quota ingestion, freshness tracking, NATS sync, and statusline rendering.

Antigravity pipes a JSON state object on stdin to its configured ``statusLine`` command.
When the user runs on a subscription plan, the payload includes a ``quota`` map keyed by
bucket ID (for example ``gemini-weekly`` or five-hour buckets) with ``remaining_fraction``
or ``remaining_amount``, ``reset_time``, ``reset_in_seconds``, and optional ``disabled``.

This module ingests those readings into an atomic local state file
(``$XDG_STATE_HOME/ai-cli-utils/agy-quota.json`` by default), preserves ``unknown`` whenever a
route is absent, disabled, stale, expired, or incomparable (such as ``remaining_amount``
without ``remaining_fraction``), publishes fresh snapshots to NATS for cross-machine
consumers, and renders compact statusline output for both Antigravity and Claude Code.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import sys
import tempfile
import threading
import time
import uuid as _uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import output as _out
from . import quota as _quota
from .output import Untagged

_RESET = _quota._STATUSLINE_RESET
_DIM = _quota._STATUSLINE_DIM
_BOLD = _quota._STATUSLINE_BOLD
_GREEN = _quota._STATUSLINE_GREEN
_YELLOW = _quota._STATUSLINE_YELLOW
_RED = _quota._STATUSLINE_RED
_CYAN = _quota._STATUSLINE_CYAN
_MAGENTA = _quota._STATUSLINE_MAGENTA
_GOOGLE_BLUE = _quota._STATUSLINE_GOOGLE_BLUE

_AGY_QUOTA_STATE_FILENAME = "agy-quota.json"
_AGY_QUOTA_MAX_AGE_SECONDS_DEFAULT = 1800
_AGY_PUBLISH_THROTTLE_SECONDS = 60
_FIVE_HOUR_SECONDS = 5 * 60 * 60
_WEEKLY_SECONDS = 7 * 24 * 60 * 60


def default_agy_quota_state_path() -> Path:
    """Return the default XDG state path for Antigravity quota readings."""
    from .config import get_xdg_state_home

    return get_xdg_state_home() / _AGY_QUOTA_STATE_FILENAME


def agy_quota_settings() -> tuple[Path, float]:
    """Return ``(state_file_path, max_age_seconds)`` from ``[quota]`` in ``config.toml``."""
    try:
        from .config import load_config

        section = load_config().get("quota", {})
    except Exception:
        section = {}
    if not isinstance(section, dict):
        section = {}
    raw_path = section.get("agy_state_file")
    if isinstance(raw_path, str) and raw_path.strip():
        path = Path(raw_path.strip()).expanduser()
    else:
        path = default_agy_quota_state_path()
    try:
        max_age = float(section.get("max_age_seconds", _AGY_QUOTA_MAX_AGE_SECONDS_DEFAULT))
    except (TypeError, ValueError):
        max_age = float(_AGY_QUOTA_MAX_AGE_SECONDS_DEFAULT)
    return path, max_age


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _parse_iso_epoch(raw: Any) -> tuple[str | None, float | None]:
    if not isinstance(raw, str) or not raw.strip():
        return None, None
    text = raw.strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None, None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt_utc = dt.astimezone(UTC)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), dt_utc.timestamp()


def _classify_window(
    bucket_id: str, reset_in_seconds: float | None, reset_epoch: float | None, now_ts: float
) -> tuple[str, int]:
    lower = bucket_id.lower()
    if any(token in lower for token in ("week", "7d", "weekly", "seven_day")):
        return "seven_day", _WEEKLY_SECONDS
    if any(token in lower for token in ("5h", "five", "hour", "session", "short")):
        return "five_hour", _FIVE_HOUR_SECONDS
    remaining = reset_in_seconds
    if remaining is None and reset_epoch is not None:
        remaining = reset_epoch - now_ts
    if remaining is not None and remaining > 24 * 3600:
        return "seven_day", _WEEKLY_SECONDS
    if remaining is not None and remaining > 0:
        return "five_hour", _FIVE_HOUR_SECONDS
    return "other", _WEEKLY_SECONDS


def parse_statusline_payload(payload: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any] | None:
    """Extract normalized, non-PII Antigravity quota readings from a statusLine JSON payload.

    Returns ``None`` when the payload carries no usable ``quota`` mapping. Buckets with
    ``disabled=True`` or incomparable ``remaining_amount`` (without ``remaining_fraction``)
    are preserved with ``comparable=False`` so downstream consumers treat them as ``unknown``.
    """
    if not isinstance(payload, dict):
        return None
    raw_quota = payload.get("quota")
    if not isinstance(raw_quota, dict) or not raw_quota:
        return None

    observed_dt = (now or datetime.now(UTC)).astimezone(UTC)
    observed_iso = observed_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    now_ts = observed_dt.timestamp()

    buckets: dict[str, dict[str, Any]] = {}
    for bucket_id, raw_bucket in raw_quota.items():
        if not isinstance(bucket_id, str) or not bucket_id.strip() or not isinstance(raw_bucket, dict):
            continue
        disabled = bool(raw_bucket.get("disabled", False))
        reset_at, reset_epoch = _parse_iso_epoch(raw_bucket.get("reset_time"))
        reset_in = _finite_number(raw_bucket.get("reset_in_seconds"))
        if reset_epoch is None and reset_in is not None and reset_in >= 0:
            reset_dt = observed_dt + timedelta(seconds=reset_in)
            reset_at = reset_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            reset_epoch = reset_dt.timestamp()

        window_kind, window_seconds = _classify_window(bucket_id, reset_in, reset_epoch, now_ts)
        rem_frac = _finite_number(raw_bucket.get("remaining_fraction"))
        rem_amount = _finite_number(raw_bucket.get("remaining_amount"))

        if disabled:
            buckets[bucket_id] = {
                "bucket_id": bucket_id,
                "disabled": True,
                "comparable": False,
                "window_kind": window_kind,
                "window_seconds": window_seconds,
                "remaining_fraction": None,
                "used_percent": None,
                "remaining_amount": rem_amount,
                "reset_at": reset_at,
                "reset_epoch": reset_epoch,
                "updated_at": observed_iso,
            }
            continue

        if rem_frac is not None and 0.0 <= rem_frac <= 1.0:
            used_pct = round(max(0.0, min(100.0, (1.0 - rem_frac) * 100.0)), 2)
            buckets[bucket_id] = {
                "bucket_id": bucket_id,
                "disabled": False,
                "comparable": True,
                "window_kind": window_kind,
                "window_seconds": window_seconds,
                "remaining_fraction": round(rem_frac, 6),
                "used_percent": used_pct,
                "remaining_amount": rem_amount,
                "reset_at": reset_at,
                "reset_epoch": reset_epoch,
                "updated_at": observed_iso,
            }
        else:
            # Preserve incomparable or malformed bucket measurements explicitly as unknown.
            buckets[bucket_id] = {
                "bucket_id": bucket_id,
                "disabled": False,
                "comparable": False,
                "window_kind": window_kind,
                "window_seconds": window_seconds,
                "remaining_fraction": None,
                "used_percent": None,
                "remaining_amount": rem_amount,
                "reset_at": reset_at,
                "reset_epoch": reset_epoch,
                "updated_at": observed_iso,
            }

    if not buckets:
        return None

    model = payload.get("model")
    model_id: str | None = None
    model_display: str | None = None
    if isinstance(model, dict):
        raw_id = model.get("id")
        raw_display = model.get("display_name")
        model_id = raw_id if isinstance(raw_id, str) and raw_id else None
        model_display = raw_display if isinstance(raw_display, str) and raw_display else None
    elif isinstance(model, str) and model:
        model_id = model
        model_display = model

    plan_tier = payload.get("plan_tier")
    version = payload.get("version")

    return {
        "schema_version": 1,
        "provider": "agy",
        "source": "agy_statusline",
        "plan_tier": plan_tier if isinstance(plan_tier, str) and plan_tier else None,
        "model_id": model_id,
        "model_display": model_display,
        "client_version": version if isinstance(version, str) and version else None,
        "updated_at": observed_iso,
        "buckets": buckets,
    }


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, sort_keys=True)
            fh.write("\n")
        Path(tmp_name).replace(path)
    finally:
        Path(tmp_name).unlink(missing_ok=True)


def _read_raw_state(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def record_agy_statusline_payload(
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
    state_path: Path | None = None,
) -> dict[str, Any] | None:
    """Parse and atomically persist an Antigravity statusLine quota payload.

    Unmentioned prior buckets retain their original ``updated_at`` timestamps so an old
    bucket reading is never relabeled fresh.
    """
    parsed = parse_statusline_payload(payload, now=now)
    if parsed is None:
        return None
    target = state_path or agy_quota_settings()[0]
    existing = _read_raw_state(target)
    merged_buckets: dict[str, Any] = {}
    if existing and isinstance(existing.get("buckets"), dict):
        for key, value in existing["buckets"].items():
            if isinstance(key, str) and isinstance(value, dict):
                merged_buckets[key] = value
    merged_buckets.update(parsed["buckets"])
    merged = {
        **parsed,
        "plan_tier": parsed["plan_tier"] or (existing.get("plan_tier") if existing else None),
        "buckets": merged_buckets,
    }
    _atomic_write_json(target, merged)
    return merged


def _evaluate_bucket(bucket: dict[str, Any], now_ts: float, max_age: float) -> dict[str, Any]:
    bucket_id = str(bucket.get("bucket_id", ""))
    disabled = bool(bucket.get("disabled", False))
    comparable = bool(bucket.get("comparable", False))
    used_pct = _finite_number(bucket.get("used_percent"))
    rem_frac = _finite_number(bucket.get("remaining_fraction"))
    reset_at, reset_epoch = _parse_iso_epoch(bucket.get("reset_at"))
    if reset_epoch is None:
        reset_epoch = _finite_number(bucket.get("reset_epoch"))
    updated_at, updated_epoch = _parse_iso_epoch(bucket.get("updated_at"))
    window_kind = str(bucket.get("window_kind", "other"))
    window_seconds_raw = _finite_number(bucket.get("window_seconds"))
    window_seconds = int(window_seconds_raw) if window_seconds_raw and window_seconds_raw > 0 else _WEEKLY_SECONDS

    status = "unknown"
    reason = ""
    if disabled:
        status = "unknown"
        reason = "bucket disabled"
    elif not comparable or used_pct is None or rem_frac is None or not (0.0 <= used_pct <= 100.0):
        status = "unknown"
        reason = "no comparable remaining_fraction"
    elif updated_epoch is None:
        status = "unknown"
        reason = "missing observation timestamp"
    elif reset_epoch is not None and reset_epoch < now_ts:
        status = "stale"
        reason = "reset time has passed"
    elif now_ts - updated_epoch > max_age:
        status = "stale"
        reason = "observation older than max_age_seconds"
    else:
        status = "ok"

    return {
        "bucket_id": bucket_id,
        "status": status,
        "reason": reason,
        "disabled": disabled,
        "comparable": comparable,
        "window_kind": window_kind,
        "window_seconds": window_seconds,
        "used_percent": used_pct if status != "unknown" else None,
        "remaining_fraction": rem_frac if status != "unknown" else None,
        "remaining_amount": _finite_number(bucket.get("remaining_amount")),
        "reset_at": reset_at,
        "reset_epoch": reset_epoch,
        "updated_at": updated_at,
    }


def _select_primary_bucket(buckets: dict[str, dict[str, Any]]) -> str | None:
    """Choose the primary bucket: prefer fresh seven-day, then fresh five-hour, then highest used."""
    ok_items = [item for item in buckets.values() if item["status"] == "ok"]
    candidates = ok_items or [item for item in buckets.values() if item["status"] == "stale"]
    if not candidates:
        return None
    rank = {"seven_day": 0, "five_hour": 1, "other": 2}
    candidates.sort(
        key=lambda item: (
            rank.get(item["window_kind"], 2),
            -(item["used_percent"] if item["used_percent"] is not None else -1.0),
            item["bucket_id"],
        )
    )
    return candidates[0]["bucket_id"]


def read_agy_quota_kv() -> dict[str, Any] | None:
    """Read the latest Antigravity quota state from NATS KV (300ms cap)."""
    machine = os.environ.get("AI_HOST", "")
    kv_key = f"quota.agy.current.{machine}" if machine else "quota.agy.current"
    result: list[dict[str, Any] | None] = [None]

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
                    try:
                        entry = await asyncio.wait_for(kv.get(kv_key), timeout=0.1)
                    except Exception:
                        if machine:
                            entry = await asyncio.wait_for(kv.get("quota.agy.current"), timeout=0.1)
                        else:
                            raise
                    if entry.value is not None:
                        loaded = json.loads(entry.value)
                        if isinstance(loaded, dict):
                            result[0] = loaded
            except Exception:
                pass
            finally:
                with contextlib.suppress(Exception):
                    await client.close()

        with contextlib.suppress(Exception):
            asyncio.run(_do())

    thread = threading.Thread(target=_read, daemon=True)
    thread.start()
    thread.join(timeout=0.3)
    return result[0]


def _evaluate_raw_state(raw: dict[str, Any] | None, now_ts: float, max_age: float) -> dict[str, Any]:
    if not raw or not isinstance(raw.get("buckets"), dict) or not raw["buckets"]:
        return {
            "provider": "agy",
            "status": "unknown",
            "plan_tier": raw.get("plan_tier") if isinstance(raw, dict) else None,
            "updated_at": raw.get("updated_at") if isinstance(raw, dict) else None,
            "primary_bucket": None,
            "used_percent": None,
            "remaining_fraction": None,
            "reset_at": None,
            "reset_epoch": None,
            "window_seconds": None,
            "buckets": {},
        }

    evaluated: dict[str, dict[str, Any]] = {}
    for key, val in raw["buckets"].items():
        if isinstance(key, str) and isinstance(val, dict):
            evaluated[key] = _evaluate_bucket({**val, "bucket_id": val.get("bucket_id", key)}, now_ts, max_age)

    primary_id = _select_primary_bucket(evaluated)
    primary = evaluated[primary_id] if primary_id else None
    overall_status = primary["status"] if primary else "unknown"
    return {
        "provider": "agy",
        "status": overall_status,
        "plan_tier": raw.get("plan_tier") if isinstance(raw.get("plan_tier"), str) else None,
        "updated_at": raw.get("updated_at") if isinstance(raw.get("updated_at"), str) else None,
        "primary_bucket": primary_id,
        "used_percent": primary["used_percent"] if primary and primary["status"] != "unknown" else None,
        "remaining_fraction": primary["remaining_fraction"] if primary and primary["status"] != "unknown" else None,
        "reset_at": primary["reset_at"] if primary else None,
        "reset_epoch": primary["reset_epoch"] if primary else None,
        "window_seconds": primary["window_seconds"] if primary else None,
        "buckets": evaluated,
    }


def read_agy_quota_status(
    *,
    now: datetime | None = None,
    state_path: Path | None = None,
    allow_kv_fallback: bool = True,
) -> dict[str, Any]:
    """Return the current Antigravity quota status (local state file, then optional NATS KV)."""
    default_path, max_age = agy_quota_settings()
    target = state_path or default_path
    now_ts = (now or datetime.now(UTC)).astimezone(UTC).timestamp()

    local_raw = _read_raw_state(target)
    local_status = _evaluate_raw_state(local_raw, now_ts, max_age)
    if local_status["status"] == "ok" or not allow_kv_fallback:
        return local_status

    kv_raw = read_agy_quota_kv()
    if kv_raw is not None:
        kv_status = _evaluate_raw_state(kv_raw, now_ts, max_age)
        if kv_status["status"] == "ok":
            return kv_status
    return local_status


def publish_agy_quota(
    *,
    now: datetime | None = None,
    state_path: Path | None = None,
) -> bool:
    """Publish fresh Antigravity quota state to NATS when current; returns True if sent."""
    default_path, max_age = agy_quota_settings()
    target = state_path or default_path
    now_dt = (now or datetime.now(UTC)).astimezone(UTC)
    raw = _read_raw_state(target)
    status = _evaluate_raw_state(raw, now_dt.timestamp(), max_age)
    if status["status"] != "ok" or raw is None:
        return False

    try:
        from .config import load_config

        cfg = load_config()
        nats_servers = cfg.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
    except Exception:
        nats_servers = ["nats://localhost:4222"]
    if not nats_servers:
        return False

    machine = os.environ.get("AI_HOST", "")
    snapshot_payload = {
        **raw,
        "machine": machine,
        "used_percent": status["used_percent"],
        "remaining_fraction": status["remaining_fraction"],
        "primary_bucket": status["primary_bucket"],
        "reset_at": status["reset_at"],
        "ts": now_dt.timestamp(),
    }

    from .messaging import NATSClient

    sent = False

    async def _do_publish() -> None:
        nonlocal sent
        client = NATSClient(servers=nats_servers)
        try:
            await client.connect()
            if client.nc:
                await client.publish("quota.agy.snapshot", snapshot_payload)
                hw_payload = {
                    "id": str(_uuid.uuid4()),
                    "machine": machine,
                    "used_pct": status["used_percent"],
                    "remaining_fraction": status["remaining_fraction"],
                    "plan_tier": status["plan_tier"],
                    "reset_at": status["reset_at"],
                    "scraped_at": now_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "raw": json.dumps(snapshot_payload, sort_keys=True),
                }
                await client.publish("hw.events.usage.agy.snapshot", hw_payload)
                if client.js:
                    with contextlib.suppress(Exception):
                        kv = await client.js.key_value("hw_state")
                        encoded = json.dumps(snapshot_payload, sort_keys=True).encode()
                        if machine:
                            await kv.put(f"quota.agy.current.{machine}", encoded)
                        await kv.put("quota.agy.current", encoded)
                sent = True
        finally:
            with contextlib.suppress(Exception):
                await client.close()

    with contextlib.suppress(Exception):
        asyncio.run(_do_publish())
    return sent


def _format_reset_short(reset_epoch: float | None) -> str:
    if reset_epoch is None:
        return ""
    try:
        dt = datetime.fromtimestamp(reset_epoch).astimezone()
    except (OSError, OverflowError, ValueError):
        return ""
    hour_12 = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt.month}/{dt.day} {hour_12} {ampm}"


def render_agy_statusline_segment(
    *,
    now: datetime | None = None,
    state_path: Path | None = None,
    allow_kv_fallback: bool = True,
    include_reset: bool = False,
) -> str:
    """Render a compact single-line Antigravity quota segment for CC, Codex, or agy statuslines."""
    from .quota import AGY_FIVE_HOUR_QUOTA, AGY_WEEKLY_QUOTA, AntigravityQuotaAdapter, QuotaStatuslineSegment

    now_dt = (now or datetime.now(UTC)).astimezone(UTC)
    now_ts = now_dt.timestamp()
    status = read_agy_quota_status(now=now_dt, state_path=state_path, allow_kv_fallback=allow_kv_fallback)
    if status["status"] != "ok":
        return ""

    parts: list[str] = []
    buckets = status["buckets"]
    ok_buckets = [b for b in buckets.values() if b["status"] == "ok"]
    rank = {"seven_day": 0, "five_hour": 1, "other": 2}
    ok_buckets.sort(key=lambda b: (rank.get(b["window_kind"], 2), b["bucket_id"]))

    seen_kinds: set[str] = set()
    primary_reset_epoch: float | None = None
    for bucket in ok_buckets:
        kind = bucket["window_kind"]
        if kind in seen_kinds:
            continue
        seen_kinds.add(kind)
        used_pct = bucket["used_percent"]
        if used_pct is None:
            continue
        if kind == "seven_day":
            adapter = AGY_WEEKLY_QUOTA
        elif kind == "five_hour":
            adapter = AGY_FIVE_HOUR_QUOTA
        else:
            adapter = AntigravityQuotaAdapter("agy", bucket["window_seconds"])
        segment = QuotaStatuslineSegment(adapter)
        reset_epoch = bucket["reset_epoch"]
        if primary_reset_epoch is None and reset_epoch is not None:
            primary_reset_epoch = reset_epoch
        if reset_epoch is not None:
            parts.append(segment.render(used_pct, reset_epoch, now_ts))
        else:
            parts.append(segment.render_without_pace(used_pct))
        if len(parts) >= 2:
            break

    if not parts:
        return ""
    rendered = " ".join(parts)
    if include_reset and primary_reset_epoch is not None:
        reset_str = _format_reset_short(primary_reset_epoch)
        if reset_str:
            rendered = f"{rendered} {_GOOGLE_BLUE}agy{_RESET} \U0001f504 {_CYAN}{reset_str}{_RESET}"
    return rendered


def _short_model_label(payload: dict[str, Any]) -> str:
    model = payload.get("model")
    if isinstance(model, dict):
        display = str(model.get("display_name") or model.get("id") or "Antigravity")
    elif isinstance(model, str) and model:
        display = model
    else:
        display = "Antigravity"
    return display[:28] + ("…" if len(display) > 28 else "")


def _project_and_worktree(payload: dict[str, Any]) -> tuple[str, str]:
    workspace = payload.get("workspace")
    raw_dir = ""
    if isinstance(workspace, dict):
        raw_dir = str(workspace.get("current_dir") or workspace.get("project_dir") or "")
    if not raw_dir:
        raw_dir = str(payload.get("cwd") or Path.cwd())
    normalized = raw_dir.replace("\\", "/").rstrip("/")
    for marker in ("/.worktrees/", "/.claude/worktrees/"):
        if marker in normalized:
            repo_part, wt_part = normalized.split(marker, 1)
            return Path(repo_part).name or repo_part, wt_part.split("/", 1)[0]
    return Path(normalized).name or normalized, ""


def _format_context_part(payload: dict[str, Any]) -> str:
    ctx = payload.get("context_window")
    used = _finite_number(ctx.get("used_percentage")) if isinstance(ctx, dict) else None
    if used is None:
        return f"\U0001fa9f {_DIM}--%{_RESET}"
    clamped = max(0, min(100, round(used)))
    color = _GREEN if clamped < 50 else (_YELLOW if clamped < 80 else _RED)
    return f"\U0001fa9f {color}{clamped}%{_RESET}"


def _read_claude_segment_for_agy() -> str:
    """Read Claude weekly quota without invoking slow external subprocesses."""
    from .quota import CLAUDE_WEEKLY_QUOTA, QuotaStatuslineSegment, read_latest_snapshot

    try:
        snap = read_latest_snapshot()
    except Exception:
        return ""
    if snap is None:
        return ""
    segment = QuotaStatuslineSegment(CLAUDE_WEEKLY_QUOTA)
    _, reset_epoch = _parse_iso_epoch(snap.reset_at)
    if reset_epoch is not None:
        return segment.render(snap.weekly_all_models_pct, reset_epoch, time.time())
    return segment.render_without_pace(snap.weekly_all_models_pct)


def _should_publish_throttled(state_path: Path, now_ts: float) -> bool:
    stamp_path = state_path.parent / ".agy-quota-publish-ts"
    try:
        prev = float(stamp_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        prev = 0.0
    if now_ts - prev < _AGY_PUBLISH_THROTTLE_SECONDS:
        return False
    with contextlib.suppress(OSError):
        stamp_path.parent.mkdir(parents=True, exist_ok=True)
        stamp_path.write_text(f"{int(now_ts)}\n", encoding="utf-8")
    return True


def render_agy_statusline(
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
    state_path: Path | None = None,
    publish: bool = True,
) -> str:
    """Ingest an Antigravity statusLine JSON payload and render a compact single-line TUI bar."""
    now_dt = (now or datetime.now(UTC)).astimezone(UTC)
    target = state_path or agy_quota_settings()[0]

    recorded = record_agy_statusline_payload(payload, now=now_dt, state_path=target)
    if publish and recorded is not None and _should_publish_throttled(target, now_dt.timestamp()):
        with contextlib.suppress(Exception):
            publish_agy_quota(now=now_dt, state_path=target)

    clock = now_dt.astimezone().strftime("%H:%M")
    model = _short_model_label(payload)
    project, wt = _project_and_worktree(payload)
    ctx_part = _format_context_part(payload)
    agy_part = render_agy_statusline_segment(
        now=now_dt,
        state_path=target,
        allow_kv_fallback=False,
        include_reset=True,
    )
    cc_part = _read_claude_segment_for_agy()

    sep = f"{_DIM}│{_RESET}"
    if wt:
        wt_disp = wt if len(wt) <= 18 else f"{wt[:17]}…"
        loc = f"{_BOLD}{project}{_RESET} {_MAGENTA}⎇{_RESET} {_DIM}{wt_disp}{_RESET}"
    else:
        loc = f"{_BOLD}{project}{_RESET}"

    segments = [f"{_DIM}{clock}{_RESET}", model, loc, ctx_part]
    if agy_part:
        segments.append(agy_part)
    if cc_part:
        segments.append(cc_part)
    plan_tier = payload.get("plan_tier")
    if isinstance(plan_tier, str) and plan_tier.strip():
        segments.append(f"{_GOOGLE_BLUE}{plan_tier.strip()}{_RESET}")
    line = f" {sep} ".join(segments)
    return line.replace("\r", " ").replace("\n", " ") + _RESET


def cmd_render_agy_statusline() -> int:
    """CLI entrypoint for ``ai quota render-statusline``: read stdin JSON and emit one line."""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (OSError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    _out.raw(Untagged.STATUSLINE, render_agy_statusline(payload))
    return 0
