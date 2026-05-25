"""CC CLI per-call token tracking — scans ~/.claude/projects/ JSONL files and pushes events.

Reads all CC session JSONL files, extracts per-call token data from assistant
messages, and POSTs new entries to the ai-core REST API. Cursor-tracked so
only entries since the last push are sent.

Cursor file: ~/.local/state/ai-cli-utils/cc-usage-cursor.json
  Format: {"session-uuid": "2026-04-17T10:00:00+00:00", ...}

Config (config.toml [ai-core] section):
  api_url = "https://your-ai-core-host"
  api_key  = "ac-api-..."

Usage::

    from ai_cli.cc_usage import scan_and_push
    result = scan_and_push(config={"ai-core": {"api_url": ..., "api_key": ...}})
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
_STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "ai-cli-utils"
_CURSOR_FILE = _STATE_DIR / "cc-usage-cursor.json"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class CCTokenEvent:
    """Token usage data extracted from a single CC JSONL assistant entry."""

    id: str
    session_id: str
    project_path: Optional[str]
    machine: str
    model: str
    input_tokens: Optional[int]
    cache_creation_tokens: Optional[int]
    cache_read_tokens: Optional[int]
    output_tokens: Optional[int]
    cc_version: Optional[str]
    git_branch: Optional[str]
    occurred_at: str


@dataclass
class PushResult:
    """Result of a scan-and-push run."""

    scanned_sessions: int = 0
    new_events: int = 0
    inserted: int = 0
    skipped: int = 0
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# JSONL parsing
# ---------------------------------------------------------------------------


def _parse_iso(ts: str) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp string to a timezone-aware datetime."""
    try:
        dt = datetime.fromisoformat(ts.rstrip("Z"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError, AttributeError):
        return None


def _extract_event(entry: dict, session_id: str, project_path: Optional[str], machine: str) -> Optional[CCTokenEvent]:
    """Extract a CCTokenEvent from a CC JSONL entry, or return None if not applicable."""
    if entry.get("type") != "assistant":
        return None
    msg = entry.get("message", {})
    usage = msg.get("usage")
    if not usage:
        return None
    entry_id = entry.get("uuid")
    if not entry_id:
        return None
    ts_raw = entry.get("timestamp", "")
    occurred_at_dt = _parse_iso(ts_raw)
    if occurred_at_dt is None:
        return None
    return CCTokenEvent(
        id=entry_id,
        session_id=session_id,
        project_path=project_path or entry.get("cwd"),
        machine=machine,
        model=msg.get("model", "unknown"),
        input_tokens=usage.get("input_tokens"),
        cache_creation_tokens=usage.get("cache_creation_input_tokens"),
        cache_read_tokens=usage.get("cache_read_input_tokens"),
        output_tokens=usage.get("output_tokens"),
        cc_version=entry.get("version"),
        git_branch=entry.get("gitBranch"),
        occurred_at=occurred_at_dt.isoformat(),
    )


def _read_jsonl(path: Path) -> list[dict]:
    """Read all valid JSON lines from a JSONL file."""
    entries: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return entries


def _decode_project_path(encoded_dir_name: str) -> str:
    """Decode a CC project directory name back to a filesystem path.

    CC encodes paths by replacing '/' with '-' and leading '~' with
    '-Users-<name>'. We return a best-effort human-readable string.
    """
    if encoded_dir_name.startswith("-"):
        return "/" + encoded_dir_name[1:].replace("-", "/")
    return encoded_dir_name


# ---------------------------------------------------------------------------
# Cursor management
# ---------------------------------------------------------------------------


def _load_cursor() -> dict[str, str]:
    """Load the push cursor file. Returns {} if not found or corrupt."""
    try:
        return json.loads(_CURSOR_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cursor(cursor: dict[str, str]) -> None:
    """Persist the push cursor file."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _CURSOR_FILE.write_text(json.dumps(cursor, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Session scanning
# ---------------------------------------------------------------------------


def scan_new_events(
    claude_dir: Optional[Path] = None,
    machine: Optional[str] = None,
    cursor: Optional[dict[str, str]] = None,
) -> tuple[list[CCTokenEvent], dict[str, str]]:
    """Scan all CC session JSONL files and return events not yet pushed.

    Args:
        claude_dir: Override for ~/.claude/projects (for testing).
        machine: Machine identifier (defaults to AI_HOST env var or 'unknown').
        cursor: Existing cursor dict. If None, loaded from disk.

    Returns:
        (new_events, updated_cursor) where updated_cursor maps session_id →
        latest occurred_at ISO string for all sessions with new events.
    """
    projects_dir = claude_dir or _CLAUDE_PROJECTS_DIR
    if not projects_dir.exists():
        return [], cursor or {}

    _machine = machine or os.environ.get("AI_HOST", "unknown")
    _cursor = cursor if cursor is not None else _load_cursor()
    new_cursor = dict(_cursor)
    events: list[CCTokenEvent] = []

    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        project_path = _decode_project_path(project_dir.name)

        for jsonl_file in sorted(project_dir.glob("*.jsonl")):
            session_id = jsonl_file.stem
            if session_id == "memory":
                continue

            cursor_ts_str = _cursor.get(session_id)
            cursor_dt: Optional[datetime] = _parse_iso(cursor_ts_str) if cursor_ts_str else None

            session_max_dt: Optional[datetime] = None

            for entry in _read_jsonl(jsonl_file):
                event = _extract_event(entry, session_id, project_path, _machine)
                if event is None:
                    continue

                event_dt = _parse_iso(event.occurred_at)
                if event_dt is None:
                    continue

                if cursor_dt is not None and event_dt <= cursor_dt:
                    continue

                events.append(event)
                if session_max_dt is None or event_dt > session_max_dt:
                    session_max_dt = event_dt

            if session_max_dt is not None:
                new_cursor[session_id] = session_max_dt.isoformat()

    return events, new_cursor


# ---------------------------------------------------------------------------
# HTTP push
# ---------------------------------------------------------------------------


def _push_to_api(events: list[CCTokenEvent], api_url: str, api_key: str) -> tuple[int, int]:
    """POST a batch of events to POST /api/v1/usage/cc/ingest.

    Returns (inserted, skipped). Raises urllib.error.URLError on network failure.
    """
    payload = {
        "events": [
            {
                "id": e.id,
                "session_id": e.session_id,
                "project_path": e.project_path,
                "machine": e.machine,
                "model": e.model,
                "input_tokens": e.input_tokens,
                "cache_creation_tokens": e.cache_creation_tokens,
                "cache_read_tokens": e.cache_read_tokens,
                "output_tokens": e.output_tokens,
                "cc_version": e.cc_version,
                "git_branch": e.git_branch,
                "occurred_at": e.occurred_at,
            }
            for e in events
        ]
    }
    data = json.dumps(payload).encode("utf-8")
    url = api_url.rstrip("/") + "/api/v1/usage/cc/ingest"
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("inserted", 0), body.get("skipped", 0)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


_BATCH_SIZE = 500


def scan_and_push(
    config: dict,
    claude_dir: Optional[Path] = None,
    machine: Optional[str] = None,
    dry_run: bool = False,
) -> PushResult:
    """Scan CC session files and push new events to ai-core.

    Args:
        config: Full ai-cli config dict (reads config["ai-core"]).
        claude_dir: Override for ~/.claude/projects (for testing).
        machine: Override AI_HOST (for testing).
        dry_run: If True, scan and count but do not push or update cursor.

    Returns:
        PushResult with counts and any error message.
    """
    result = PushResult()
    hw_config = config.get("ai-core", {})
    api_url = hw_config.get("api_url", "").strip()
    api_key = hw_config.get("api_key", "").strip()

    if not api_url or not api_key:
        result.error = (
            "ai-core.api_url and ai-core.api_key must be set in config.toml under [ai-core] to push CC usage events."
        )
        return result

    cursor = _load_cursor()
    events, new_cursor = scan_new_events(claude_dir=claude_dir, machine=machine, cursor=cursor)

    result.scanned_sessions = len({e.session_id for e in events} | set(cursor.keys()))
    result.new_events = len(events)

    if not events or dry_run:
        return result

    try:
        for i in range(0, len(events), _BATCH_SIZE):
            batch = events[i : i + _BATCH_SIZE]
            inserted, skipped = _push_to_api(batch, api_url, api_key)
            result.inserted += inserted
            result.skipped += skipped
    except Exception as exc:
        result.error = str(exc)
        return result

    _save_cursor(new_cursor)
    return result


def get_cursor_summary() -> dict:
    """Return a summary of the current cursor state."""
    cursor = _load_cursor()
    if not cursor:
        return {"sessions_tracked": 0, "last_push": None}
    return {
        "sessions_tracked": len(cursor),
        "last_push": max(cursor.values()),
    }
