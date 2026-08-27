"""Session naming, index management, worktree operations, and Gemini UUID lookup.

Depends on: config.py
"""

import contextlib
import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import (
    WORKTREE_DIR,
    _get_main_project_name,
    _get_projects_dir,
    get_current_project_name,
    get_worktree_upstream_branches,
    load_project_registry,
    resolve_project_prefix,
)
from .direnv_setup import envrc_loads
from .git_repair import _git_env, repair_bare_worktree_config
from .process_probe import probe_for


def _checkpoint_to_chat_uuid(checkpoint_bytes: bytes) -> str:
    """Derive a stable UUID from checkpoint content (SHA-256 of first 64KB).

    Using a content-derived UUID ensures the same checkpoint always produces
    the same chat file — re-running conversion is idempotent.
    """
    h = hashlib.sha256(checkpoint_bytes[:65536]).hexdigest()
    return f"{h[0:8]}-{h[8:12]}-4{h[13:16]}-{h[16:20]}-{h[20:32]}"


def _convert_checkpoint_to_chat(ai_name: str, gemini_tmp: Path) -> str | None:
    """Convert a Gemini checkpoint JSON to a chat session file.

    Reads ``checkpoint-{ai_name}.json``, converts from the checkpoint
    ``history`` format (role/parts) to the chat file format (type/content),
    and writes a ``chats/session-*.json`` file with a stable content-derived
    UUID.  Idempotent: if a file with the same UUID already exists and is at
    least as recent as the checkpoint, it is left unchanged.

    Returns the sessionId UUID on success, or None on any error.
    """
    checkpoint_path = gemini_tmp / f"checkpoint-{ai_name}.json"
    if not checkpoint_path.exists():
        return None

    try:
        raw = checkpoint_path.read_bytes()
        checkpoint = json.loads(raw)
        history = checkpoint.get("history", [])
        if not history:
            return None

        session_uuid = _checkpoint_to_chat_uuid(raw)
        chats_dir = gemini_tmp / "chats"
        chats_dir.mkdir(parents=True, exist_ok=True)

        # Build filename from checkpoint mtime so file sorts correctly vs. native sessions
        chk_mtime = checkpoint_path.stat().st_mtime
        chk_dt = datetime.fromtimestamp(chk_mtime, tz=UTC)
        ts_str = chk_dt.strftime("%Y-%m-%dT%H-%M")
        chat_path = chats_dir / f"session-{ts_str}-{session_uuid[:8]}.json"

        # Skip if already converted and up to date
        if chat_path.exists() and chat_path.stat().st_mtime >= chk_mtime:
            return session_uuid

        # Compute projectHash = sha256(projectRoot)
        project_root_file = gemini_tmp / ".project_root"
        project_root = project_root_file.read_text().strip() if project_root_file.exists() else str(Path.cwd())
        project_hash = hashlib.sha256(project_root.encode()).hexdigest()

        # Convert history entries to chat messages
        base_time = chk_dt - timedelta(seconds=len(history))
        messages = []
        for i, entry in enumerate(history):
            role = entry.get("role", "user")
            parts = entry.get("parts", [])
            text = next((p.get("text", "") for p in parts if "text" in p), "")
            msg_time = base_time + timedelta(seconds=i)
            msg_uuid_raw = hashlib.sha256(f"{session_uuid}:{i}".encode()).hexdigest()
            msg_uuid = f"{msg_uuid_raw[0:8]}-{msg_uuid_raw[8:12]}-4{msg_uuid_raw[13:16]}-{msg_uuid_raw[16:20]}-{msg_uuid_raw[20:32]}"
            messages.append(
                {
                    "id": msg_uuid,
                    "timestamp": msg_time.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "type": "gemini" if role == "model" else role,
                    "content": [{"text": text}],
                }
            )

        chat_data = {
            "sessionId": session_uuid,
            "projectHash": project_hash,
            "startTime": messages[0]["timestamp"] if messages else chk_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "lastUpdated": messages[-1]["timestamp"] if messages else chk_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "messages": messages,
            "kind": "main",
        }
        chat_path.write_text(json.dumps(chat_data, ensure_ascii=False))
        # Set mtime to match checkpoint so ordering vs. native chat files is correct
        os.utime(chat_path, (chk_mtime, chk_mtime))
        return session_uuid

    except Exception:
        return None


def _get_chat_last_message_timestamp(chat_path: Path) -> float:
    """Return the timestamp of the last message in a chat file as a Unix timestamp.

    Falls back to 0.0 on any error so callers can compare safely.
    """
    try:
        data = json.loads(chat_path.read_bytes())
        messages = data.get("messages", [])
        if not messages:
            return 0.0
        last_ts = messages[-1].get("timestamp", "")
        return datetime.fromisoformat(last_ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _find_latest_gemini_uuid(ai_name: str) -> str | None:
    """Return the sessionId from the most recently modified chat file for ai_name.

    Gemini stores sessions in ~/.gemini/tmp/{ai_name}/chats/*.json.  Each file
    contains a top-level ``sessionId`` field with the full UUID needed for
    ``--resume``.

    If a checkpoint exists and its mtime is newer than the last message timestamp
    in the latest chat file (or no chat files exist), the checkpoint is
    automatically converted to a chat file so the session can be resumed via
    ``gemini -r`` without any ``/resume load`` injection.

    Comparing checkpoint mtime against the chat file's last message timestamp
    (rather than the chat file's mtime) correctly handles the case where
    ``/resume save`` is run mid-session: the chat file may have a stale mtime
    even though auto-save has written more recent messages to it.
    """
    gemini_tmp = Path.home() / ".gemini" / "tmp" / ai_name
    chats_dir = gemini_tmp / "chats"
    checkpoint_path = gemini_tmp / f"checkpoint-{ai_name}.json"

    # Find newest existing chat file (by mtime — for initial ordering only)
    latest_chat: Path | None = None
    if chats_dir.exists():
        candidates = sorted(chats_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        latest_chat = candidates[0] if candidates else None

    # Convert checkpoint if it exists and its save time is newer than the last
    # message in the chat file.  This correctly selects the chat file when
    # auto-save has written messages after a mid-session /resume save.
    if checkpoint_path.exists():
        chk_mtime = checkpoint_path.stat().st_mtime
        chat_last_ts = _get_chat_last_message_timestamp(latest_chat) if latest_chat else 0.0
        if chk_mtime > chat_last_ts:
            converted_uuid = _convert_checkpoint_to_chat(ai_name, gemini_tmp)
            if converted_uuid:
                return converted_uuid

    # Use latest native chat file
    if latest_chat:
        try:
            session_id = json.loads(latest_chat.read_text()).get("sessionId")
            if session_id:
                return session_id
        except Exception:
            pass

    return None


def get_latest_gemini_session_id(ai_name: str | None = None) -> str | None:
    """Return the most recent Gemini session ID.

    If ai_name is provided, scans ~/.gemini/tmp/{ai_name}/chats/ directly —
    the authoritative source regardless of current working directory.  Only
    sessions with a chat file on disk can be resumed with ``-r``; sessions
    started via ``/resume load`` (checkpoint restore) do not write a chat file,
    so their UUID in logs.json cannot be used for ``-r``.  Returning None in
    that case causes the caller to fall back to ``/resume load``, which is the
    correct recovery path.  Never fall back to logs.json when ai_name is known.
    """
    if ai_name:
        return _find_latest_gemini_uuid(ai_name)

    cwd = Path.cwd()
    project_name = cwd.name
    paths = [
        Path.home() / ".gemini" / "tmp" / project_name / "logs.json",
    ]
    main_name = _get_main_project_name()
    if main_name is not None:
        paths.append(Path.home() / ".gemini" / "tmp" / main_name / "logs.json")
    for p in paths:
        if p.exists():
            try:
                with p.open("rb") as f:
                    size = p.stat().st_size
                    if size > 4096:
                        f.seek(-4096, 2)
                    data = f.read().decode("utf-8", errors="ignore")
                    matches = re.findall(r'"sessionId":\s*"([^"]+)"', data)
                    if matches:
                        return matches[-1]
            except Exception:
                pass
    return None


def _prefix_from_session_name(session_name: str) -> str:
    """Extract the project prefix from a tmux session name.

    e.g. ``c-ai-cli-2`` → ``ai-cli``, ``p-r-myproj-1`` → ``myproj``.
    Returns empty string if the format is not recognised.
    """
    parts = session_name.split("-")
    if len(parts) >= 3 and parts[0] in ("c", "g", "p", "cx"):
        start = 2 if parts[1] == "r" else 1
        end = len(parts) - 1
        if end > start:
            return "-".join(parts[start:end]).lower()
        if start < len(parts):
            return parts[start].lower()
    return ""


def get_project_prefix() -> str:
    """Resolve the current repository's registered task prefix.

    This preserves the registry's canonical casing for task-ID consumers.
    Worktree names and custom session titles are normalized when session names
    are built, while an unregistered repository must still fail instead of
    inventing a directory-name prefix.
    """
    return resolve_project_prefix()


def is_current_project_resolved() -> bool:
    """True when the session can be tied to a real project.

    This remains a launch-location guard. Prefix lookup is deliberately stricter:
    ``get_project_prefix()`` requires a root registration and supplies the remedy.
    """
    if os.environ.get("AI_TMUX_SESSION"):
        return True
    name = get_current_project_name()
    if any(p.get("name") == name for p in load_project_registry()):
        return True
    try:
        return Path.cwd().resolve().is_relative_to(_get_projects_dir().resolve())
    except Exception:
        return False


class SessionSlotAmbiguityError(RuntimeError):
    """Raised when more than one live session could satisfy an explicit slot."""


def _tmux_session_names() -> list[str]:
    """Return the live tmux session names, or no names when tmux is unavailable."""
    res = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True, check=False
    )
    if res.returncode != 0:
        return []
    return [name for name in (res.stdout or "").splitlines() if name]


def _matching_tmux_sessions(engine_short: str, project_prefix: str, slot: str | int) -> list[str]:
    """Find local and remote tmux names for a slot, ignoring prefix casing."""
    targets = {
        f"{engine_short}-{project_prefix}-{slot}".casefold(),
        f"{engine_short}-r-{project_prefix}-{slot}".casefold(),
    }
    return [name for name in _tmux_session_names() if name.casefold() in targets]


def _session_slot_name(engine_short: str, session_name: str) -> str:
    remote_prefix = f"{engine_short}-r-"
    prefix_length = len(remote_prefix) if session_name.casefold().startswith(remote_prefix) else len(engine_short) + 1
    return session_name[prefix_length:]


def _resolve_explicit_tmux_slot(engine_short: str, project_prefix: str, slot: str) -> tuple[str, str] | None:
    candidates = _matching_tmux_sessions(engine_short, project_prefix, slot)
    if len(candidates) > 1:
        joined = ", ".join(candidates)
        raise SessionSlotAmbiguityError(f"ambiguous existing sessions for slot {project_prefix}-{slot}: {joined}")
    if candidates:
        session_name = candidates[0]
        return session_name, _session_slot_name(engine_short, session_name)
    return None


def _matching_worktrees(worktree_base: Path, ai_name: str) -> list[Path]:
    """Find existing worktrees whose name differs only by prefix casing."""
    try:
        return [path for path in worktree_base.iterdir() if path.name.casefold() == ai_name.casefold()]
    except OSError:
        return []


def _resolve_explicit_bare_slot(engine_short: str, project_prefix: str, slot: str) -> tuple[str, str] | None:
    try:
        repo_root = detect_repo_root()
    except RuntimeError:
        repo_root = None
    if not repo_root:
        return None
    candidates = _matching_worktrees(repo_root / WORKTREE_DIR, f"{project_prefix}-{slot}")
    if len(candidates) > 1:
        joined = ", ".join(str(path) for path in candidates)
        raise SessionSlotAmbiguityError(f"ambiguous existing worktrees for slot {project_prefix}-{slot}: {joined}")
    if candidates:
        ai_name = candidates[0].name
        return f"{engine_short}-{ai_name}", ai_name
    return None


def find_next_index(prefix: str, use_tmux: bool = True) -> int:
    """Return the lowest unused session index for ``prefix``.

    With tmux, occupancy is authoritative: a live tmux session using either
    local or remote naming for the slot means it is taken. In bare mode there is no server to ask (and tmux may not be
    installed at all), so fall back to the on-disk worktree directories, which
    are the only durable record a bare session leaves behind.
    """
    if not use_tmux:
        return _find_next_index_from_worktrees(prefix)
    match = re.fullmatch(r"([cgp])(?:-r)?-(.+)-", prefix)
    if not match:
        return 1
    engine_short, project_prefix = match.groups()
    i = 1
    while True:
        if not _matching_tmux_sessions(engine_short, project_prefix, i):
            return i
        i += 1


def _find_next_index_from_worktrees(prefix: str) -> int:
    """Return the lowest index with no live bare session, using worktrees on disk.

    ``prefix`` is a tmux-style session prefix (``c-myproject-``); worktrees are
    named with the ai_name form (``myproject-1``), so the leading engine segment
    is stripped before matching.  A worktree whose directory exists but whose
    engine process is gone is a *reusable* slot, not an occupied one — otherwise
    indexes would climb forever, since bare mode has no session-exit hook to
    remove the worktree (the tmux path's EXIT trap does that).
    """
    ai_prefix = re.sub(r"^[cgp](-r)?-", "", prefix)
    try:
        repo_root = detect_repo_root()
    except RuntimeError:
        repo_root = None
    if not repo_root:
        return 1
    wt_base = repo_root / WORKTREE_DIR
    i = 1
    while True:
        candidates = _matching_worktrees(wt_base, f"{ai_prefix}{i}")
        if not candidates or not any(_worktree_has_live_session(candidate) for candidate in candidates):
            return i
        i += 1


def _worktree_has_live_session(worktree_dir: Path) -> bool:
    """True when some engine process is currently running inside ``worktree_dir``.

    Used to decide whether a leftover worktree directory represents an active
    bare session or a reusable slot.  Falls back to treating the slot as free
    when process inspection is unavailable, so a launch is never blocked
    outright by a missing/denied psutil probe.
    """
    try:
        import psutil
    except Exception:
        return False

    target = str(worktree_dir)
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if "claude" not in name and "gemini" not in name and "node" not in name:
                cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                if "claude" not in cmdline and "gemini" not in cmdline:
                    continue
            if proc.cwd() == target:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            continue
    return False


def find_recent_session(prefix: str) -> str:
    res = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name} #{session_activity}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        return ""
    sessions = []
    for line in res.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0].casefold().startswith(prefix.casefold()):
            with contextlib.suppress(ValueError):
                sessions.append((parts[0], int(parts[1])))
    if not sessions:
        return ""
    sessions.sort(key=lambda x: x[1], reverse=True)
    return sessions[0][0]


# Matches ai-cli session names: c-session-1, c-r-session-1, g-project-2, etc.
_AI_SESSION_RE = re.compile(r"^[cgp](-r)?-[a-zA-Z0-9]+-\d+$")
_PROCESS_START_TIME_TOLERANCE_SECONDS = 5
_BG_SPARE_TERMINATE_TIMEOUT_SECONDS = 2


def _claude_sessions_dir() -> Path:
    """Return Claude Code's per-process session-state directory."""
    return Path.home() / ".claude" / "sessions"


def _is_claude_bg_spare(cmdline: list[str]) -> bool:
    """Return whether ``cmdline`` is a Claude Code bg-spare invocation."""
    return any(
        Path(command).name == "claude" and next_command == "bg-spare"
        for command, next_command in itertools.pairwise(cmdline)
    )


def _has_live_tmux_session(session_name: object, active_sessions: set[str]) -> bool:
    """Return whether a Claude session-state name is represented in tmux."""
    if not isinstance(session_name, str):
        return False
    possible_sessions = {session_name}
    if not _AI_SESSION_RE.fullmatch(session_name):
        possible_sessions.update({f"c-{session_name}", f"g-{session_name}"})
    return bool(possible_sessions & active_sessions)


def _sweep_orphaned_claude_bg_spares(active_sessions: set[str] | None, timeout_seconds: int, now: float) -> None:
    """Remove dead Claude state files and reap verified, old orphan bg-spares.

    A failed tmux query passes ``None`` for ``active_sessions``.  Dead state
    files are still safe to remove then, but a live process is never considered
    orphaned without a successful tmux liveness check.
    """
    try:
        import psutil
    except Exception:
        return

    sessions_dir = _claude_sessions_dir()
    if not sessions_dir.exists():
        return

    for state_file in sessions_dir.glob("*.json"):
        try:
            state = json.loads(state_file.read_text())
            pid = state.get("pid")
            started_at = state.get("startedAt")
            if not isinstance(pid, int) or not isinstance(started_at, (int, float)):
                continue
            process = psutil.Process(pid)
            if abs(process.create_time() - (started_at / 1000)) > _PROCESS_START_TIME_TOLERANCE_SECONDS:
                state_file.unlink(missing_ok=True)
                continue
        except (OSError, ValueError, TypeError, json.JSONDecodeError, psutil.NoSuchProcess):
            with contextlib.suppress(OSError):
                state_file.unlink(missing_ok=True)
            continue
        except (psutil.AccessDenied, psutil.ZombieProcess):
            continue

        last_activity = state.get("updatedAt", started_at)
        if not isinstance(last_activity, (int, float)) or now - (last_activity / 1000) <= timeout_seconds:
            continue
        if active_sessions is None or _has_live_tmux_session(state.get("name"), active_sessions):
            continue

        try:
            if not _is_claude_bg_spare(process.cmdline()):
                continue
            process.terminate()
            try:
                process.wait(timeout=_BG_SPARE_TERMINATE_TIMEOUT_SECONDS)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=_BG_SPARE_TERMINATE_TIMEOUT_SECONDS)
            state_file.unlink(missing_ok=True)
        except (OSError, psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, psutil.TimeoutExpired):
            continue


def cleanup_stale_sessions(config: dict) -> None:
    """Reap only tmux sessions whose every pane leader has provably ended.

    Client attachment and pane idle time say nothing about whether a remote
    agent process is still running, so neither is used to authorize a reap.
    A malformed or unreadable pane PID fails closed: that session is preserved.
    """
    if sys.platform == "win32":
        return
    session_cfg = config.get("session", {})
    timeout_seconds = session_cfg.get("stale_session_timeout", 15) * 60
    orphan_bg_spare_timeout_seconds = session_cfg.get("orphan_bg_spare_timeout", timeout_seconds // 60) * 60
    now = int(time.time())

    res = subprocess.run(
        [
            "tmux",
            "list-panes",
            "-a",
            "-F",
            "#{session_name}|#{pane_pid}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        _sweep_orphaned_claude_bg_spares(None, orphan_bg_spare_timeout_seconds, now)
        return

    # A session can be reaped only when every one of its pane leaders is known
    # to be gone or a zombie.  Any unknown PID preserves the whole session.
    sessions: dict[str, list[int | None]] = {}
    for line in res.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 1)
        if len(parts) != 2:
            continue
        session_name, pane_pid_str = parts
        if not _AI_SESSION_RE.match(session_name):
            continue
        try:
            pane_pid = int(pane_pid_str)
        except ValueError:
            pane_pid = None
        if pane_pid is not None and pane_pid <= 0:
            pane_pid = None
        sessions.setdefault(session_name, []).append(pane_pid)

    probe = probe_for()
    for session_name, pane_pids in sessions.items():
        if any(pane_pid is None for pane_pid in pane_pids):
            continue
        try:
            all_ended = all(probe.has_ended(pane_pid) for pane_pid in pane_pids)
        except OSError:
            continue
        if all_ended:
            subprocess.run(["tmux", "kill-session", "-t", session_name], capture_output=True, check=False)

    _sweep_orphaned_claude_bg_spares(set(sessions), orphan_bg_spare_timeout_seconds, now)
    _sweep_stale_iterm2_profiles()


def _sweep_stale_iterm2_profiles() -> None:
    """Remove Dynamic Profile files for sessions that no longer exist in tmux.

    Profiles accumulate when sessions are killed without running the EXIT trap
    (e.g. SIGKILL, mosh disconnect). Called at every session launch so stale
    profiles don't pollute iTerm2's profile list.
    """
    try:
        from .icon_generator import _DYNAMIC_PROFILE_PREFIX, _dynamic_profile_dir

        profile_dir = _dynamic_profile_dir()
        if not profile_dir.exists():
            return

        # Get current tmux sessions
        res = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            check=False,
        )
        active_sessions = set(res.stdout.strip().splitlines()) if res.returncode == 0 else set()

        for profile_file in profile_dir.glob(f"{_DYNAMIC_PROFILE_PREFIX}*.json"):
            session_name = profile_file.stem[len(_DYNAMIC_PROFILE_PREFIX) :]
            # Map ai_name back to possible tmux session names (c-{name} or g-{name})
            possible_sessions = {
                session_name,
                f"c-{session_name}",
                f"g-{session_name}",
            }
            if not possible_sessions & active_sessions:
                profile_file.unlink(missing_ok=True)
    except Exception:
        pass  # non-fatal


def resolve_session(prefix: str, name: str) -> str:
    if not name:
        res = subprocess.run(
            ["tmux", "display-message", "-p", "#{session_name}"], capture_output=True, text=True, check=False
        )
        current_session = res.stdout.strip() if res.returncode == 0 else ""
        if current_session and current_session.casefold().startswith(prefix.casefold()):
            return current_session
        return find_recent_session(prefix)
    res = subprocess.run(["tmux", "has-session", "-t", f"{prefix}{name}"], capture_output=True, check=False)
    if res.returncode == 0:
        return f"{prefix}{name}"
    if name.isdigit():
        match = re.fullmatch(r"([cgp])(?:-r)?-(.+)-", prefix)
        if match:
            engine_short, project_prefix = match.groups()
            expected_prefix = prefix.casefold()
            candidates = [
                session_name
                for session_name in _matching_tmux_sessions(engine_short, project_prefix, name)
                if session_name.casefold().startswith(expected_prefix)
            ]
            if len(candidates) == 1:
                return candidates[0]
    return find_recent_session(f"{prefix}{name}-")


def _resolve_is_remote(is_remote_flag: bool) -> bool:
    """Return True only for a session launched *from another machine* over SSH.

    ``--is-remote`` is injected into the remote command line by the local machine
    when it SSHes out to launch a session, and is the only trustworthy signal:
    it means "someone else drove this launch", which is what the ``c-r-`` /
    ``g-r-`` prefix and the chdir-to-configured-project behaviour exist for.

    This deliberately does **not** infer remoteness from ``AI_HOST``.  That
    heuristic ("any host not named mac is remote") treated every ordinary local
    launch on a Linux or Windows workstation as remote, which sent the launch
    down the ``is_remote`` branch and created the worktree inside the configured
    *main* project instead of the repo the user was actually in.  A host having a
    name says nothing about who initiated the session.
    """
    return is_remote_flag


def _new_session_display_name(engine_short: str, project_prefix: str, name: str, is_remote: bool) -> str:
    """Build a lowercase tmux name for a newly allocated session."""
    remote_seg = "-r" if is_remote else ""
    return f"{engine_short}{remote_seg}-{project_prefix.lower()}-{name.lower()}"


def build_session_name(
    engine_type: str,
    project_prefix: str,
    name: str,
    config: dict | None = None,
    is_remote: bool = False,
    use_tmux: bool = True,
) -> tuple[str, str]:
    """Build tmux session name and ai_name.

    Session name format: {c|g|p|cx}[-r]-{project}-{index}
      e.g. c-myproject-1, c-r-myproject-1, g-myproject-2, p-myproject-3, cx-myproject-4
    ai_name (used for --name, worktrees, session map): {project}-{index}
      e.g. myproject-1, myproject-2

    ``use_tmux=False`` (bare mode) switches auto-index discovery from live tmux
    sessions to on-disk worktrees.  The returned session name is still built in
    the same format so it remains a stable key for the session map and logs even
    though no tmux session will exist.
    """
    engine_short = engine_type
    naming_prefix = project_prefix.lower()
    tmux_base = f"{engine_short}{'-r' if is_remote else ''}-{naming_prefix}-"
    ai_base = f"{naming_prefix}-"

    clean_name = name
    prefixes_to_strip = [
        f"c-r-{project_prefix}-",
        f"c-{project_prefix}-",
        f"g-r-{project_prefix}-",
        f"g-{project_prefix}-",
        f"p-r-{project_prefix}-",
        f"p-{project_prefix}-",
        f"cx-r-{project_prefix}-",
        f"cx-{project_prefix}-",
        f"claude-{project_prefix}-",
        f"gemini-{project_prefix}-",
        f"pi-{project_prefix}-",
        f"codex-{project_prefix}-",
        f"{project_prefix}-",
    ]
    for p in sorted(prefixes_to_strip, key=len, reverse=True):
        if clean_name.casefold().startswith(p.casefold()):
            clean_name = clean_name[len(p) :]
            break
    clean_name = re.sub(r"[^a-zA-Z0-9_-]", "-", clean_name)
    clean_name = re.sub(r"-+", "-", clean_name)
    clean_name = clean_name.strip("-").lower()

    if clean_name.isdigit() or clean_name.rsplit("-", 1)[-1].isdigit():
        existing = (
            _resolve_explicit_tmux_slot(engine_short, project_prefix, clean_name)
            if use_tmux
            else _resolve_explicit_bare_slot(engine_short, project_prefix, clean_name)
        )
        if existing:
            return existing
        return _new_session_display_name(engine_short, project_prefix, clean_name, is_remote), f"{ai_base}{clean_name}"
    if not clean_name:
        idx = find_next_index(tmux_base, use_tmux=use_tmux)
        return _new_session_display_name(engine_short, project_prefix, str(idx), is_remote), f"{ai_base}{idx}"
    tmux_named = f"{tmux_base}{clean_name}-"
    ai_named = f"{ai_base}{clean_name}-"
    idx = find_next_index(tmux_named, use_tmux=use_tmux)
    return _new_session_display_name(engine_short, project_prefix, f"{clean_name}-{idx}", is_remote), f"{ai_named}{idx}"


# --- Git Worktree Logic ---


def detect_repo_root():
    # Use --git-common-dir so we get the main repo root even when called from
    # inside a git worktree (--show-toplevel would return the worktree path instead,
    # causing create_worktree to nest worktrees and create circular .direnv symlinks).
    res = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"], capture_output=True, text=True, env=_git_env(), check=False
    )
    if res.returncode != 0:
        return None
    git_common = Path(res.stdout.strip())
    if not git_common.is_absolute():
        git_common = Path(os.path.normpath(Path.cwd() / git_common))
    root = git_common.parent
    # Sanity guard: if WORKTREE_DIR appears in the resolved root, --git-common-dir
    # returned something unexpected and we'd nest worktrees inside a worktree.
    if WORKTREE_DIR in root.parts:
        raise RuntimeError(
            f"detect_repo_root() resolved to {root!r}, which contains '{WORKTREE_DIR}'. "
            "This would nest worktrees — aborting. Check git rev-parse --git-common-dir output."
        )
    return root


def _current_branch(repo_root: Path) -> str | None:
    """Return the branch the main working tree has checked out, or None if detached."""
    res = subprocess.run(
        ["git", "-C", str(repo_root), "symbolic-ref", "--quiet", "--short", "HEAD"],
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )
    branch = (res.stdout or "").strip()
    return branch if res.returncode == 0 and branch else None


def _remote_branch_exists(repo_root: Path, branch: str) -> bool:
    res = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"],
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )
    return res.returncode == 0 and bool((res.stdout or "").strip())


def _has_origin_remote(repo_root: Path) -> bool:
    res = subprocess.run(
        ["git", "-C", str(repo_root), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )
    return res.returncode == 0


def _local_branch_exists(repo_root: Path, branch: str) -> bool:
    res = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )
    return res.returncode == 0 and bool((res.stdout or "").strip())


def _resolve_worktree_target(repo_root: Path) -> tuple[str, str | None]:
    """Return ``(base_ref, upstream_branch)`` for a NEW session worktree branch.

    ``base_ref`` is the start-point the branch is created at; ``upstream_branch`` is
    the branch on ``origin`` it should track, or ``None`` for "attach no upstream".

    The integration branch — the branch this repository's work actually lands on —
    resolves in this order, first match wins:

    1. The ``[worktree_upstream]`` config table, keyed by repository directory name
       (AI-CLI-193 AC-5). A repository declares its own integration branch; no
       repository name is ever special-cased in this package's source.
    2. The branch the main working tree currently has checked out. A session should
       work on what the repository itself is working on. For a repository parked on
       ``main`` this resolves to ``main`` — the historical behaviour, unchanged.

    Once resolved, the *remote-tracking* ref is preferred as the base: the local tip
    of that branch may carry unpushed commits, and starting there would make the new
    worktree not review-clean (promoting it would open a pull request full of
    unrelated commits). The remote-tracking ref is used as-is rather than fetched
    here — the launch already runs ``git pull --rebase`` inside the new worktree,
    and resolving the base must not depend on the network, or an unreachable remote
    would stop the launch outright instead of merely leaving the worktree behind.

    When the integration branch exists locally but not on ``origin`` — a workspace
    branch that has not been pushed yet — the local branch is the only honest base,
    and the upstream is ``None``. Falling back to ``origin/main`` there is precisely
    the defect (AC-3): it points routine session work at a branch this repository
    does not integrate through, which in a shared repository on a pull-request
    workflow is the branch nobody may push directly. A missing upstream makes the
    first ``git push`` stop and ask, which is the safe direction.

    Raises ``RuntimeError`` when the repository has no ``origin`` remote or is in a
    detached HEAD, so nothing anchors a session branch at all. There is deliberately
    no fallback to ``HEAD`` for the unanchored case: it fails later and far more
    confusingly, at push or review time.
    """
    if not _has_origin_remote(repo_root):
        raise RuntimeError(
            f"create_worktree: {repo_root} has no `origin` remote — refusing to create a session "
            f"worktree based on the current HEAD instead. A worktree branch must be anchored to a "
            f"branch that exists on a remote so it stays review-clean and its sync rebases onto "
            f"that branch. Fix the repo first: add an `origin` remote and run `git fetch origin`."
        )

    configured = get_worktree_upstream_branches().get(repo_root.name)
    branch = configured or _current_branch(repo_root)
    if not branch:
        raise RuntimeError(
            f"create_worktree: cannot resolve an integration branch in {repo_root} — its HEAD is "
            f"detached, so there is no branch a session could be said to be working on, and "
            f"refusing to guess one. Check out the branch this repository integrates through, or "
            f"declare it under [worktree_upstream] in config.toml."
        )

    if _remote_branch_exists(repo_root, branch):
        return f"refs/remotes/origin/{branch}", branch
    if _local_branch_exists(repo_root, branch):
        return f"refs/heads/{branch}", None
    raise RuntimeError(
        f"create_worktree: integration branch {branch!r} for {repo_root.name} exists neither on "
        f"`origin` nor locally, so there is nothing to base a session worktree on — refusing to "
        f"fall back to the current HEAD. Either correct the [worktree_upstream] entry in "
        f"config.toml, or create and push that branch and run `git fetch origin`."
    )


def _resolve_worktree_base(repo_root: Path) -> str:
    """Return the commit a NEW session worktree branch must start at.

    Thin accessor over :func:`_resolve_worktree_target`; see it for the resolution
    order and the reasoning.
    """
    return _resolve_worktree_target(repo_root)[0]


def _set_upstream_or_raise(repo_root: Path, branch: str, upstream_branch: str) -> None:
    """Point ``branch`` at ``origin/<upstream_branch>``, retrying once, then raising.

    Must not fail silently (AI-CLI-128): a worktree branch left without an upstream
    is one ``git push`` away from git suggesting ``--set-upstream origin wt-X``,
    which publishes a same-named remote branch instead of shipping to the
    integration branch — the drift that stranded one session 46 commits behind for
    months.
    """
    target = f"--set-upstream-to=origin/{upstream_branch}"
    for _ in range(2):
        res = subprocess.run(
            ["git", "branch", target, branch],
            capture_output=True,
            cwd=repo_root,
            env=_git_env(),
            check=False,
        )
        if res.returncode == 0:
            return
    stderr = res.stderr.decode(errors="replace").strip()
    raise RuntimeError(
        f"create_worktree: failed to set upstream=origin/{upstream_branch} on branch "
        f"{branch!r} after retry (AI-CLI-128 — a worktree branch with no upstream is "
        f"one `git push` away from publishing a same-named remote branch). "
        f"git stderr: {stderr}"
    )


def _branch_upstream(worktree_path: Path, branch: str) -> str | None:
    """Return ``branch``'s configured upstream, if any."""
    res = subprocess.run(
        ["git", "-C", str(worktree_path), "for-each-ref", "--format=%(upstream:short)", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )
    upstream = (res.stdout or "").strip()
    return upstream if res.returncode == 0 and upstream else None


def _unset_upstream(repo_root: Path, branch: str) -> None:
    """Ensure ``branch`` tracks nothing (AI-CLI-193 AC-3).

    ``git worktree add -b <branch> <start-point>`` sets an upstream *by itself* when
    the start-point is a remote-tracking ref, via git's default
    ``branch.autoSetupMerge``. That implicit write is the second upstream writer in
    this path, so "do not attach an upstream" has to actively clear one rather than
    merely skip the explicit call. ``--unset-upstream`` exits non-zero when there is
    nothing to unset, which is the desired end state, so its status is ignored.
    """
    subprocess.run(
        ["git", "branch", "--unset-upstream", branch],
        capture_output=True,
        cwd=repo_root,
        env=_git_env(),
        check=False,
    )


def registered_worktrees(repo_root: Path) -> list[Path]:
    """Every path ``git worktree list --porcelain`` reports for ``repo_root``.

    Parsed line-exactly rather than searched as text. A substring test cannot
    distinguish a worktree from a *parent directory* of one: ``.worktrees/name``
    occurs inside the line ``worktree /…/.worktrees/name/leaf``, so a directory
    that merely contains worktrees answers "registered" and is then treated as a
    checkout it does not have.

    Paths are resolved so the comparison survives a symlinked or otherwise
    non-canonical spelling of the same directory — git prints its own canonical
    form, which need not match the one the caller built.
    """
    res = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=repo_root,
        env=_git_env(),
        check=False,
    )
    if res.returncode != 0:
        stderr = _git_stderr(res)
        if "not a git repository" in stderr:
            # Not an anomaly: callers probe arbitrary paths, and "no repo here"
            # degrades to "no worktrees here" so the checkout guard downstream
            # still gets a chance to run rather than the caller erroring out
            # before it can check whether there's real content to protect.
            return []
        raise RuntimeError(f"could not list registered git worktrees for {repo_root}: {stderr}")
    return [
        Path(line[len("worktree ") :].strip()).resolve()
        for line in (res.stdout or "").splitlines()
        if line.startswith("worktree ")
    ]


def _git_stderr(res: subprocess.CompletedProcess) -> str:
    """Return command stderr as safe, single-line diagnostic text."""
    stderr = res.stderr or ""
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    return " ".join(stderr.split()) or "(no stderr output)"


def _contains_git_checkout(directory: Path) -> Path | None:
    """Return the first git checkout at or under ``directory``, or None.

    A ``.git`` entry is a file in a linked worktree and a directory in a normal
    clone, so both forms count. This is the guard on deleting a directory: an
    unregistered clone is invisible to ``git worktree list`` yet still holds
    commits that exist nowhere else.
    """
    if (directory / ".git").exists():
        return directory
    for candidate in sorted(directory.rglob(".git")):
        return candidate.parent
    return None


def _is_empty_dir(directory: Path) -> bool:
    """Return whether ``directory`` is a directory holding no entries at all.

    Deliberately mirrors git's own ``is_empty_dir``, which ``git worktree add``
    uses to decide that an existing path may be written into: a single level, and
    every entry counts, so a dotfile or an empty subdirectory makes a directory
    non-empty. A looser reading here would admit a slot that git then rejects
    with ``already exists``, replacing an actionable refusal with a confusing one.

    Anything that is not a readable directory — a file at that path, a permission
    error, a race that removes it mid-check — answers False, so the caller falls
    through to refusing rather than to deleting.
    """
    try:
        with os.scandir(directory) as entries:
            return next(entries, None) is None
    except OSError:
        return False


def _same_worktree_path(first: Path, second: Path) -> bool:
    """Return whether two existing paths identify the same filesystem object."""
    try:
        return first.samefile(second)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise RuntimeError(
            f"could not determine whether worktree paths {first} and {second} identify the same directory: {exc}"
        ) from exc


def _registered_worktree_at(candidate: Path, registered: list[Path]) -> Path | None:
    """Return the registered worktree physically located at ``candidate``."""
    for worktree in registered:
        if candidate == worktree or _same_worktree_path(candidate, worktree):
            return worktree
    return None


def _initialize_worktree(
    repo_root: Path,
    worktree_path: Path,
    branch: str | None,
    upstream: str | None,
    *,
    reused: bool,
) -> None:
    """Finish the idempotent setup required before returning a worktree."""
    if branch is not None:
        if reused:
            configured_upstream = _branch_upstream(worktree_path, branch)
            if upstream is None:
                if configured_upstream is not None:
                    _unset_upstream(repo_root, branch)
            elif configured_upstream != f"origin/{upstream}":
                _set_upstream_or_raise(repo_root, branch, upstream)
        elif upstream is None:
            print(
                f"Warning: worktree branch {branch!r} was created with NO upstream — no integration "
                f"branch could be resolved for {repo_root.name}. `git push` will stop and ask rather "
                f"than guess. Declare the branch under [worktree_upstream] in config.toml, or push "
                f"the branch this repository is on and run `git fetch origin`.",
                file=sys.stderr,
            )
            _unset_upstream(repo_root, branch)
        else:
            _set_upstream_or_raise(repo_root, branch, upstream)

    # Symlink critical environment files
    for item in [".venv", ".claude", ".gemini", ".direnv"]:
        src = repo_root / item
        dst = worktree_path / item
        if src.exists() and not dst.exists():
            dst.symlink_to(src)
    # Register workspace trust so Claude Code loads the symlinked
    # .claude/settings.json permissions instead of dropping them with a
    # "workspace has not been trusted" warning (GH #72896). Worktrees
    # resolve to the main gitRoot, but register both for safety.
    from .trust import ensure_workspace_trusted

    ensure_workspace_trusted([repo_root, worktree_path])
    _allow_trusted_worktree_envrc(repo_root, worktree_path)


def create_worktree(
    ai_name: str, *, with_status: bool = False, repo_root: Path | None = None
) -> Path | tuple[Path, bool] | None:
    """Create or reuse a session worktree.

    With ``with_status=True``, return ``(path, created)`` so launch output can
    describe the operation that actually occurred.
    """
    repo_root = repo_root or detect_repo_root()
    if not repo_root:
        return None

    # Deterministic repair backstop (AI-CLI-99): repo_root is a normal working
    # tree and must never be core.bare=true / carry a stale core.worktree.
    # Check + repair BEFORE any worktree op, regardless of what corrupted it.
    repair_bare_worktree_config(repo_root)

    wt_dir = repo_root / WORKTREE_DIR / ai_name
    wt_dir.parent.mkdir(parents=True, exist_ok=True)
    lock_path = wt_dir.parent / f".{ai_name}.lock"
    import portalocker

    # Worktree creation is a repository/slot-wide decision. Serializing it means
    # a launcher can report "created" only for the git command it actually won.
    try:
        with portalocker.Lock(str(lock_path), mode="a", timeout=20):
            # Verify it is still registered as a valid worktree; prune stale entries first.
            # Compare filesystem identity too: on a case-insensitive filesystem, a
            # differently-cased prefix can spell the same live checkout.
            subprocess.run(
                ["git", "worktree", "prune"], capture_output=True, cwd=repo_root, env=_git_env(), check=False
            )
            registered = _registered_worktree_at(wt_dir, registered_worktrees(repo_root))
            if registered is not None:
                branch = _current_branch(registered)
                _, upstream = _resolve_worktree_target(repo_root) if branch is not None else (None, None)
                _initialize_worktree(repo_root, registered, branch, upstream, reused=True)
                result = (registered, False)
                return result if with_status else registered

            if wt_dir.exists() and not _is_empty_dir(wt_dir):
                # A non-registered directory might hold files or git data that the
                # launcher cannot safely evaluate. Never recycle it automatically.
                #
                # An *empty* one is excluded above rather than refused: it holds
                # nothing to protect, and `git worktree add` writes into an
                # existing empty directory by design, so the add below succeeds
                # without deleting anything. Windows produces this state routinely
                # — `git worktree remove` drops the registration before it has
                # verified the directory is gone, so a file still held open by a
                # live process leaves the slot present and deregistered, and every
                # later launch refused over a directory git would have accepted.
                holder = _contains_git_checkout(wt_dir)
                if holder is not None:
                    raise RuntimeError(
                        f"create_worktree: {wt_dir} exists but is not a worktree of {repo_root}, and it "
                        f"contains a git checkout at {holder} — refusing to delete it. This path has two "
                        f"meanings: this launcher wants it to BE the session's checkout, while per-task "
                        f"agent worktrees are nested INSIDE it as `<name>/<task>/<leaf>`. Move the nested "
                        f"checkout(s) out to a sibling container and re-run, e.g. "
                        f"`git worktree move {holder} {wt_dir.parent / (wt_dir.name + '-agents')}/{holder.name}` "
                        f"for each one (a plain `mv` would leave git's registration pointing at the old path)."
                    )
                raise RuntimeError(
                    f"create_worktree: {wt_dir} exists but is not a worktree of {repo_root} — refusing to delete it. "
                    f"Remove or relocate it only after verifying it contains no needed files; if it is empty, run "
                    f"`rmdir {wt_dir}` and re-run."
                )

            branch = f"wt-{ai_name}"
            # Resolve base AND upstream in one call BEFORE creating anything: an unresolvable
            # one leaves no half-made worktree directory behind, and a single resolution means
            # the two can never disagree about which branch the session integrates through.
            base, upstream = _resolve_worktree_target(repo_root)

            # Try creating the branch at the resolved base; fall back to checking out an
            # existing branch of that name. The fallback deliberately passes no start-point:
            # a `wt-<name>` that already exists carries a previous session's commits, and
            # forcing it back to the integration branch would discard them.
            # cwd=repo_root, like every other git call here: this is the one that
            # WRITES, and without it git resolves the repository from the process's
            # current directory and registers the worktree in whichever repository
            # the caller happened to be standing in. The checkout still appears at
            # the requested path, so the only symptom is a later, misleading
            # `fatal: branch 'wt-<name>' does not exist` from the upstream step.
            res = subprocess.run(
                ["git", "worktree", "add", str(wt_dir), "-b", branch, base],
                capture_output=True,
                cwd=repo_root,
                env=_git_env(),
                check=False,
            )
            created = res.returncode == 0
            if not created:
                first_add = res
                res = subprocess.run(
                    ["git", "worktree", "add", str(wt_dir), branch],
                    capture_output=True,
                    cwd=repo_root,
                    env=_git_env(),
                    check=False,
                )
                created = res.returncode == 0

            # A second probe is required even after a failed add: a non-cooperating
            # process may have created a valid worktree while this process was waiting.
            registered = _registered_worktree_at(wt_dir, registered_worktrees(repo_root))
            if not created:
                if registered is not None:
                    branch = _current_branch(registered)
                    _, upstream = _resolve_worktree_target(repo_root) if branch is not None else (None, None)
                    _initialize_worktree(repo_root, registered, branch, upstream, reused=True)
                    result = (registered, False)
                    return result if with_status else registered
                raise RuntimeError(
                    f"git worktree add failed for {wt_dir}. First attempt: {_git_stderr(first_add)}. "
                    f"Retry with existing branch: {_git_stderr(res)}"
                )
            worktree_path = registered or wt_dir
            # Repair again after the add — the backstop for whatever just ran (defense
            # in depth alongside the env scrub above).
            repair_bare_worktree_config(repo_root)

            if worktree_path.is_dir():
                _initialize_worktree(repo_root, worktree_path, branch, upstream, reused=False)
                result = (worktree_path, True)
                return result if with_status else worktree_path
            raise RuntimeError(
                f"git worktree add succeeded but did not create the expected worktree at {worktree_path}"
            )
    except portalocker.exceptions.LockException as exc:
        raise RuntimeError(
            f"timed out waiting for worktree launch lock {lock_path}; another launch may be stuck. "
            f"If no ai c or ai g process is running for this slot, remove {lock_path} and retry."
        ) from exc


def _allow_trusted_worktree_envrc(repo_root: Path, worktree_dir: Path) -> None:
    """Approve a worktree .envrc only when it exactly matches an approved root file.

    direnv approvals are path-specific.  Git creates the worktree's tracked
    ``.envrc`` during checkout, but that new path is not approved just because
    the repository root's identical file is.  Restrict automatic approval to
    an exact byte-for-byte copy of an already-approved root .envrc; a changed,
    missing, or unapproved file remains subject to direnv's normal prompt.
    """
    root_envrc = repo_root / ".envrc"
    worktree_envrc = worktree_dir / ".envrc"
    if not root_envrc.is_file() or not worktree_envrc.is_file():
        return

    try:
        if root_envrc.read_bytes() != worktree_envrc.read_bytes():
            return
    except OSError:
        return

    # An existing worktree should not re-evaluate the root .envrc on every
    # launch.  Besides avoiding unnecessary work, that file may load
    # credentials from a network-backed provider.
    #
    # Both probes go through the shared portable helper. They previously ran
    # ``direnv exec <dir> true``, which ALWAYS fails on Windows because ``true``
    # is a shell builtin with no ``true.exe`` to resolve -- so the root trust
    # check below returned False on healthy setups and this function silently
    # refused to approve anything, which is why the launcher nagged forever.
    if envrc_loads(worktree_dir):
        return
    if not envrc_loads(repo_root):
        return

    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            ["direnv", "allow", str(worktree_dir)],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )


def cleanup_worktree(ai_name: str):
    repo_root = detect_repo_root()
    if not repo_root:
        return
    wt_dir = repo_root / WORKTREE_DIR / ai_name
    if not wt_dir.exists():
        return

    # Only remove if clean
    diff = subprocess.run(["git", "-C", str(wt_dir), "diff", "--quiet"], env=_git_env(), check=False)
    cached = subprocess.run(["git", "-C", str(wt_dir), "diff", "--cached", "--quiet"], env=_git_env(), check=False)
    if diff.returncode == 0 and cached.returncode == 0:
        subprocess.run(["git", "worktree", "remove", str(wt_dir)], capture_output=True, env=_git_env(), check=False)
        # Backstop repair after teardown — worktree remove is the other
        # documented trigger for the core.bare/core.worktree corruption class.
        repair_bare_worktree_config(repo_root)
