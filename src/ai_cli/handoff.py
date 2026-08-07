"""Handoff queue: post, check, claim, complete.

Depends on: config.py, messaging.py (lazy).
"""

import json
import os
import sys
import time
from pathlib import Path

from .config import _get_handoff_queue_dir, get_xdg_state_home, load_config


def _log_handoff_event(event_type: str, **fields) -> None:
    """Append a JSON event to handoff-events.jsonl for observability."""
    log_path = get_xdg_state_home() / "handoff-events.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"event": event_type, "ts": time.time(), **fields}
    try:
        with log_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def post_handoff(title, priority, project, message, for_machine=None):
    if not for_machine:
        print("Error: --for-machine is required (e.g. --for-machine mac)", file=sys.stderr)
        sys.exit(1)
    handoff_dir = _get_handoff_queue_dir()
    if handoff_dir is None:
        print("Error: [project] main_project not set in ~/.config/ai-cli-utils/config.toml", file=sys.stderr)
        sys.exit(1)
    queue_dir = handoff_dir / "pending"
    created_by = os.environ.get("AI_TMUX_SESSION", "unknown")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    max_id = 0
    for subdir in ["pending", "claimed", "completed"]:
        d = handoff_dir / subdir
        if not d.exists():
            continue
        for f in d.glob("*.md"):
            try:
                fid = int(f.name.split("-")[0])
                if fid > max_id:
                    max_id = fid
            except ValueError:
                pass
    next_id = max_id + 1
    slug = "".join(c if c.isalnum() or c == "-" else "-" for c in title.lower().replace(" ", "-"))[:40]
    filename = f"{next_id:03d}-{slug}.md"
    queue_dir.mkdir(parents=True, exist_ok=True)
    out = f'---\nid: "{next_id}"\ntitle: "{title}"\npriority: {priority}\nproject: {project}\ncreated_by: {created_by}\ncreated_at: "{now}"\nfor_machine: {for_machine}\nclaimed_by: null\nclaimed_at: null\n---\n\n{message}\n'
    (queue_dir / filename).write_text(out)
    print(queue_dir / filename)
    _log_handoff_event("handoff.posted", handoff_id=next_id, project=project, title=title, priority=priority)
    # Publish to NATS for real-time delivery (non-fatal — file queue is the durable record)
    try:
        import asyncio as _asyncio

        from .messaging import NATSClient as _NATSClient

        _cfg = load_config()
        _servers = _cfg.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
        _client = _NATSClient(servers=_servers)
        _payload = {
            "id": next_id,
            "title": title,
            "project": project,
            "priority": priority,
            "message": message,
            "created_by": created_by,
            "for_machine": for_machine,
            "content": out,
            "filename": filename,
            "ts": time.time(),
        }
        _asyncio.run(_client.publish(f"handoff.{project}", _payload))
    except Exception:
        pass


def _find_best_handoff(queue_dir: Path, project_filter: str | None = None) -> "Path | None":
    """Return the highest-priority pending handoff file, optionally filtered by project name."""
    if not queue_dir.exists():
        return None
    best_file, best_prio = None, 9
    for f in queue_dir.glob("*.md"):
        text = f.read_text()
        if project_filter is not None:
            project_match = False
            for line in text.splitlines():
                if line.startswith("project:"):
                    val = line.split(":", 1)[1].strip()
                    if val == project_filter:
                        project_match = True
                    break
            if not project_match:
                continue
        prio = 9
        for line in text.splitlines():
            if line.startswith("priority:"):
                try:
                    prio = int(line.split(":", 1)[1].strip().replace("P", ""))
                except ValueError:
                    pass
                break
        if prio < best_prio:
            best_prio, best_file = prio, f
        elif prio == best_prio and best_file is None:
            best_file = f
    return best_file


def _format_handoff_summary(path: Path) -> str:
    """Render a human-readable summary of a handoff file."""
    text = path.read_text()
    meta: dict[str, str] = {}
    body_lines: list[str] = []
    in_front = False
    front_done = False
    for line in text.splitlines():
        if line.strip() == "---":
            if not in_front and not front_done:
                in_front = True
                continue
            if in_front:
                in_front = False
                front_done = True
                continue
        if in_front:
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip('"')
        elif front_done:
            body_lines.append(line)
    title = meta.get("title", "(untitled)")
    priority = meta.get("priority", "?")
    project = meta.get("project", "?")
    created_at = meta.get("created_at", "")
    age = ""
    if created_at:
        try:
            created_ts = time.mktime(time.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ"))
            secs = max(0, int(time.time() - created_ts))
            if secs < 60:
                age = f"{secs}s ago"
            elif secs < 3600:
                age = f"{secs // 60}m ago"
            elif secs < 86400:
                age = f"{secs // 3600}h ago"
            else:
                age = f"{secs // 86400}d ago"
        except (ValueError, OverflowError):
            age = created_at
    body_preview = [ln for ln in body_lines if ln.strip()][:3]
    lines = [
        f"{path}",
        f"  [{priority}] {title}",
        f"  project: {project}   age: {age}",
    ]
    if body_preview:
        lines.append("  ---")
        for ln in body_preview:
            lines.append(f"  {ln}")
    return "\n".join(lines)


def check_handoff():
    handoff_dir = _get_handoff_queue_dir()
    if handoff_dir is None:
        print("No pending handoffs (main_project not configured).")
        return
    best_file = _find_best_handoff(handoff_dir / "pending")
    if best_file:
        print(_format_handoff_summary(best_file))
    else:
        print("No pending handoffs.")


def check_handoff_project(project_name: str):
    """Like check_handoff but filtered to a specific project directory name."""
    handoff_dir = _get_handoff_queue_dir()
    if handoff_dir is None:
        print("No pending handoffs (main_project not configured).")
        return
    best_file = _find_best_handoff(handoff_dir / "pending", project_filter=project_name)
    if best_file:
        print(_format_handoff_summary(best_file))
    else:
        print(f"No pending handoffs for project '{project_name}'.")


def claim_handoff(file_path, claimer=None):
    if claimer is None:
        claimer = os.environ.get("AI_TMUX_SESSION", "unknown")
    handoff_dir = _get_handoff_queue_dir()
    if handoff_dir is None:
        print("Error: [project] main_project not set in ~/.config/ai-cli-utils/config.toml", file=sys.stderr)
        sys.exit(1)
    claimed_dir = handoff_dir / "claimed"
    claimed_dir.mkdir(parents=True, exist_ok=True)
    src, dst = Path(file_path), claimed_dir / Path(file_path).name
    if not src.exists():
        print(f"Error: handoff file not found: {file_path}", file=sys.stderr)
        sys.exit(1)
    if dst.exists():
        print(f"Error: handoff already claimed: {dst}", file=sys.stderr)
        sys.exit(1)
    try:
        src.rename(dst)
    except OSError as e:
        print(f"Error: could not claim handoff: {e}", file=sys.stderr)
        sys.exit(1)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    text = (
        dst.read_text()
        .replace("claimed_by: null", f"claimed_by: {claimer}")
        .replace("claimed_at: null", f'claimed_at: "{now}"')
    )
    dst.write_text(text)
    print(dst)


def complete_handoff(file_path):
    handoff_dir = _get_handoff_queue_dir()
    if handoff_dir is None:
        print("Error: [project] main_project not set in ~/.config/ai-cli-utils/config.toml", file=sys.stderr)
        sys.exit(1)
    completed_dir = handoff_dir / "completed"
    completed_dir.mkdir(parents=True, exist_ok=True)
    src, dst = Path(file_path), completed_dir / Path(file_path).name
    if not src.exists():
        print(f"Error: handoff file not found: {file_path}", file=sys.stderr)
        sys.exit(1)
    try:
        src.rename(dst)
    except OSError as e:
        print(f"Error: could not complete handoff: {e}", file=sys.stderr)
        sys.exit(1)
    print(dst)


def _claim_handoff_for_signal(handoff_dir: Path, handoff_id: int, claimer: str) -> "Path | None":
    """Atomically claim a pending handoff by id. Returns claimed path on success, None if already taken."""
    pending_dir = handoff_dir / "pending"
    claimed_dir = handoff_dir / "claimed"
    claimed_dir.mkdir(parents=True, exist_ok=True)
    matches = list(pending_dir.glob(f"{handoff_id:03d}-*.md"))
    if not matches:
        return None
    src = matches[0]
    dst = claimed_dir / src.name
    try:
        src.rename(dst)  # atomic on Linux — first session wins, others get OSError
    except OSError:
        return None
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    text = (
        dst.read_text()
        .replace("claimed_by: null", f"claimed_by: {claimer}")
        .replace("claimed_at: null", f'claimed_at: "{now}"')
    )
    dst.write_text(text)
    return dst
