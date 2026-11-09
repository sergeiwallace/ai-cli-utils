"""Finding a session by its name: which host holds it, whether it is active, how to reach it.

A name is unique on every machine that shares the registry, so its registry row says
where the session lives. ``ai locate-session`` reads four things:

1. the row: title, host, client, repo, kind and worktree;
2. on the host that holds it, the tmux session, the worktree and the client transcript;
3. on another host, the heartbeat reading of ``fleet-liveness status``: its word and rung;
4. the messaging rung ``message-transports resolve`` picks for ``title@host``.

The last two are separate tools an environment may install. They are run only when
they are on ``PATH`` and never imported, and their absence is said, not guessed around.
A tool that runs past its bound reads as a timeout, never as an answer.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import name_registry as reg
from . import session_clients

ENGINES = ("c", "g", "p", "cx")
LIVENESS_TOOL = "fleet-liveness"
TRANSPORTS_TOOL = "message-transports"
TOOL_TIMEOUT_S = 20


@dataclass
class Located:
    row: dict | None
    local: dict | None
    liveness: dict | None
    reach: dict | None
    notes: list[str]
    stale_tip: str | None = None

    def as_json(self) -> dict:
        return {"row": self.row, "local": self.local, "liveness": self.liveness, "reach": self.reach}


def _run_tool(tool: str, *args: str) -> dict | None:
    """``tool``'s JSON answer, or None when it is not on PATH or did not answer."""
    path = shutil.which(tool)
    if path is None:
        return None
    try:
        result = subprocess.run([path, *args], capture_output=True, text=True, timeout=TOOL_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        return {"error": f"{tool} timed out after {TOOL_TIMEOUT_S} s", "timed_out": True}
    except OSError as exc:
        return {"error": f"{tool} did not answer: {exc}"}
    try:
        value = json.loads(result.stdout)
    except ValueError:
        return {"error": f"{tool} exited {result.returncode} without JSON: {(result.stderr or result.stdout).strip()}"}
    return value if isinstance(value, dict) else {"answer": value}


def _row_for(snapshot: reg.Snapshot, query: str) -> dict | None:
    if query in snapshot.rows:
        return snapshot.rows[query]
    return next((row for row in snapshot.rows.values() if row.get("title") == query), None)


def _tmux_sessions() -> list[str]:
    from .session import _tmux_session_names

    return _tmux_session_names()


def local_reading(row: dict, projects_root: Path) -> dict:
    """The tmux session, the worktree and the transcript of a row this host holds."""
    title = str(row.get("title") or "")
    live = set(_tmux_sessions())
    tmux = next(
        (name for engine in ENGINES for name in (f"{engine}-{title}", f"{engine}-r-{title}") if name in live), None
    )
    worktree = reg.worktree_path(row, projects_root)
    client = session_clients.ADAPTERS.get(str(row.get("client")))
    transcript = None
    if client is not None:
        probe = client().probe_transcript(worktree.resolve(strict=False) if worktree else None, title)
        transcript = {"state": probe.state, "reason": probe.reason}
    exists = bool(worktree and worktree.exists())
    state = (
        "active" if tmux else ("inactive" if exists or (transcript and transcript["state"] == "present") else "gone")
    )
    return {
        "state": state,
        "tmux": tmux,
        "worktree": str(worktree) if worktree else None,
        "worktree_exists": exists,
        "transcript": transcript,
    }


def locate(registry: reg.Registry, query: str, host: str, *, projects_root: Path) -> Located:
    """All four readings for a name or a full title; ``row`` is None when nothing holds it."""
    notes: list[str] = []
    stale_tip = None
    try:
        snapshot = registry.snapshot()
    except reg.RegistryUnreachableError as exc:
        snapshot = registry.snapshot(fetch=False)
        stale_tip = f"the registry is unreachable ({exc}); showing the last fetched tip"
        notes.append(stale_tip)
    row = _row_for(snapshot, query)
    if row is None:
        return Located(None, None, None, None, notes, stale_tip)
    title, machine = str(row.get("title") or row.get("token")), str(row.get("machine"))
    local = liveness = None
    if machine == host:
        local = local_reading(row, projects_root)
    else:
        liveness = _run_tool(LIVENESS_TOOL, "status", "--session", f"{title}@{machine}", "--json")
        if liveness is None:
            notes.append(f"{LIVENESS_TOOL} is not installed, so whether the session is active on {machine} is unknown")
    reach = _run_tool(TRANSPORTS_TOOL, "resolve", "--target", f"{title}@{machine}", "--json")
    if reach is None:
        notes.append(f"{TRANSPORTS_TOOL} is not installed, so no messaging rung was resolved")
    return Located(row, local, liveness, reach, notes, stale_tip)


def liveness_reading(liveness: dict, target: str) -> str:
    """The located session's record as ``<word> (via <rung>)``, or ``unknown: <why>``.

    ``fleet-liveness status --json`` answers ``{"identities": [record, ...]}``; a record's
    ``status`` is its word as the tool prints it (``live``, ``stale(<age>)``, ``gone``,
    ``undetermined``), and ``rung`` is the channel that carried the heartbeat.
    """
    if "error" in liveness:
        return f"unknown: {liveness['error']}"
    records = liveness.get("identities")
    if isinstance(records, list):
        if not records:
            return f"unknown: {LIVENESS_TOOL} has no heartbeat record for {target}"
        dicts = [record for record in records if isinstance(record, dict)]
        record = next((r for r in dicts if r.get("identity") == target), dicts[0] if dicts else {})
        word = record.get("status") or record.get("liveness")
        if isinstance(word, str) and word:
            rung = record.get("rung")
            return f"{word} (via {rung})" if isinstance(rung, str) and rung else word
    return f"unknown: unreadable {LIVENESS_TOOL} record: {json.dumps(liveness)}"


def describe(located: Located) -> list[str]:
    row = located.row
    if row is None:
        return []
    title, machine = row.get("title") or row.get("token"), row.get("machine")
    lines = [f"{row['token']}: {title} on {machine} ({row.get('client') or 'no client'}, {row['kind']})"]
    if located.local is not None:
        local = located.local
        lines.append(f"state      {local['state']}" + (f" (tmux {local['tmux']})" if local["tmux"] else ""))
        lines.append(f"worktree   {local['worktree'] or 'none'}" + ("" if local["worktree_exists"] else " (gone)"))
        if local["transcript"]:
            lines.append(f"transcript {local['transcript']['state']}: {local['transcript']['reason']}")
    if located.liveness is not None:
        lines.append(f"liveness   {liveness_reading(located.liveness, f'{title}@{machine}')}")
    if located.reach is not None:
        rung = located.reach.get("rung") or located.reach.get("transport") or located.reach.get("primary")
        lines.append(f"reach      {rung or json.dumps(located.reach)}")
        command = located.reach.get("send") or located.reach.get("command")
        if command:
            lines.append(f"send with  {command}")
    lines += [f"note       {note}" for note in located.notes]
    return lines


def tip_age(registry: reg.Registry) -> str | None:
    if not isinstance(registry, reg.GitRegistry):
        return None
    result = registry._git("log", "-1", "--format=%ct", reg.PRIVATE_REF, check=False)
    try:
        stamp = int(result.stdout.decode().strip())
    except ValueError:
        return None
    age = datetime.now(UTC) - datetime.fromtimestamp(stamp, UTC)
    return f"{int(age.total_seconds() // 60)} min"
