"""Session naming, index management, worktree operations, and Gemini UUID lookup.

Depends on: config.py
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import (
    WORKTREE_DIR,
    _get_main_project_name,
    _get_projects_dir,
    get_current_project_name,
    load_project_registry,
)
from .git_repair import _git_env, repair_bare_worktree_config


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
        chk_dt = datetime.fromtimestamp(chk_mtime, tz=timezone.utc)
        ts_str = chk_dt.strftime("%Y-%m-%dT%H-%M")
        chat_path = chats_dir / f"session-{ts_str}-{session_uuid[:8]}.json"

        # Skip if already converted and up to date
        if chat_path.exists() and chat_path.stat().st_mtime >= chk_mtime:
            return session_uuid

        # Compute projectHash = sha256(projectRoot)
        project_root_file = gemini_tmp / ".project_root"
        if project_root_file.exists():
            project_root = project_root_file.read_text().strip()
        else:
            project_root = str(Path.cwd())
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
                with open(p, "rb") as f:
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

    e.g. ``c-ai-cli-2`` → ``ai-cli``, ``c-r-myproj-1`` → ``myproj``.
    Returns empty string if the format is not recognised.
    """
    parts = session_name.split("-")
    if len(parts) >= 3 and parts[0] in ("c", "g"):
        start = 2 if parts[1] == "r" else 1
        end = len(parts) - 1
        if end > start:
            return "-".join(parts[start:end]).lower()
        if start < len(parts):
            return parts[start].lower()
    return ""


def get_project_prefix() -> str:
    project_name = get_current_project_name()
    for p in load_project_registry():
        if p.get("name") == project_name:
            return p.get("task_prefix", project_name[:3]).lower().strip("-")
    # Fallback 1: parse AI_TMUX_SESSION set by the session script — reliable when
    # the user runs `ai c -R` from a pane whose cwd has drifted away from the
    # project root.
    ai_session = os.environ.get("AI_TMUX_SESSION", "")
    if ai_session:
        prefix = _prefix_from_session_name(ai_session)
        if prefix:
            return prefix
    # Fallback 2: derive from cwd name — strip trailing hyphens so e.g.
    # "ai-cli-utils"[:3] → "ai-" doesn't produce a double-dash in session names.
    return project_name[:3].lower().strip("-")


def is_current_project_resolved() -> bool:
    """True when the session can be tied to a real project.

    Confident sources: running inside an existing ai session (``AI_TMUX_SESSION``
    set), cwd is a registered project, or cwd is physically under the projects
    directory (a real, possibly-unregistered project). ``False`` means the
    launcher would otherwise fabricate a session prefix from an unrelated cwd —
    the old silent "myproject"-style fallback — and should fail loudly instead.
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


def find_next_index(prefix: str, use_tmux: bool = True) -> int:
    """Return the lowest unused session index for ``prefix``.

    With tmux, occupancy is authoritative: a live ``tmux has-session`` means the
    slot is taken.  In bare mode there is no server to ask (and tmux may not be
    installed at all), so fall back to the on-disk worktree directories, which
    are the only durable record a bare session leaves behind.
    """
    if not use_tmux:
        return _find_next_index_from_worktrees(prefix)
    i = 1
    while True:
        res = subprocess.run(["tmux", "has-session", "-t", f"{prefix}{i}"], capture_output=True)
        if res.returncode != 0:
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
    ai_prefix = re.sub(r"^[cg](-r)?-", "", prefix)
    try:
        repo_root = detect_repo_root()
    except RuntimeError:
        repo_root = None
    if not repo_root:
        return 1
    wt_base = repo_root / WORKTREE_DIR
    i = 1
    while True:
        candidate = wt_base / f"{ai_prefix}{i}"
        if not candidate.exists() or not _worktree_has_live_session(candidate):
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
        ["tmux", "list-sessions", "-F", "#{session_name} #{session_activity}"], capture_output=True, text=True
    )
    if res.returncode != 0:
        return ""
    sessions = []
    for line in res.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith(prefix):
            try:
                sessions.append((parts[0], int(parts[1])))
            except ValueError:
                pass
    if not sessions:
        return ""
    sessions.sort(key=lambda x: x[1], reverse=True)
    return sessions[0][0]


# Matches ai-cli session names: c-session-1, c-r-session-1, g-project-2, etc.
_AI_SESSION_RE = re.compile(r"^[cg](-r)?-[a-zA-Z0-9]+-\d+$")


def cleanup_stale_sessions(config: dict) -> None:
    """Kill stale ai-cli tmux sessions on each launch.

    Two cases:
    - Dead shell: AI exited, pane shows bash/zsh (auto-resume loop stopped).
    - Abandoned: AI still running but session unattached for > stale_session_timeout minutes.
    """
    if sys.platform == "win32":
        return
    session_cfg = config.get("session", {})
    timeout_seconds = session_cfg.get("stale_session_timeout", 15) * 60
    now = int(time.time())

    res = subprocess.run(
        [
            "tmux",
            "list-panes",
            "-a",
            "-F",
            "#{session_name}|#{session_last_attached}|#{session_attached}|#{pane_current_command}",
        ],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return

    # Group pane commands by session name
    sessions = {}
    for line in res.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        session_name, last_attached_str, attached_str, pane_cmd = parts
        if not _AI_SESSION_RE.match(session_name):
            continue
        try:
            last_attached = int(last_attached_str)
        except ValueError:
            continue
        currently_attached = attached_str.strip() != "0"
        if session_name not in sessions:
            sessions[session_name] = (last_attached, currently_attached, [])
        sessions[session_name][2].append(pane_cmd.lower())

    shell_cmds = {"bash", "zsh", "sh", "fish"}
    dead_shell_grace = 60  # seconds — don't kill shell-only sessions that were recently active
    for session_name, (last_attached, currently_attached, pane_cmds) in sessions.items():
        all_shells = all(cmd in shell_cmds for cmd in pane_cmds)
        # Never kill a session that currently has a client attached
        if currently_attached:
            continue
        abandoned = (now - last_attached) > timeout_seconds
        # Dead shell: all panes show a shell prompt, but grant a 60s grace period so sessions
        # starting up (CC not yet launched) aren't killed by a concurrent session launch.
        dead_shell = all_shells and last_attached > 0 and (now - last_attached) > dead_shell_grace
        if dead_shell or abandoned:
            subprocess.run(["tmux", "kill-session", "-t", session_name], capture_output=True)

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
        res = subprocess.run(["tmux", "display-message", "-p", "#{session_name}"], capture_output=True, text=True)
        current_session = res.stdout.strip() if res.returncode == 0 else ""
        if current_session and current_session.startswith(prefix):
            return current_session
        return find_recent_session(prefix)
    res = subprocess.run(["tmux", "has-session", "-t", f"{prefix}{name}"], capture_output=True)
    if res.returncode == 0:
        return f"{prefix}{name}"
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


def build_session_name(
    engine_type: str,
    project_prefix: str,
    name: str,
    config: dict | None = None,
    is_remote: bool = False,
    use_tmux: bool = True,
) -> tuple[str, str]:
    """Build tmux session name and ai_name.

    Session name format: {c|g}[-r]-{project}-{index}
      e.g. c-myproject-1, c-r-myproject-1, g-myproject-2
    ai_name (used for --name, worktrees, session map): {project}-{index}
      e.g. myproject-1, myproject-2

    ``use_tmux=False`` (bare mode) switches auto-index discovery from live tmux
    sessions to on-disk worktrees.  The returned session name is still built in
    the same format so it remains a stable key for the session map and logs even
    though no tmux session will exist.
    """
    engine_short = "c" if engine_type == "c" else "g"
    remote_seg = "-r" if is_remote else ""
    tmux_base = f"{engine_short}{remote_seg}-{project_prefix}-"
    ai_base = f"{project_prefix}-"

    clean_name = name
    prefixes_to_strip = [
        f"c-r-{project_prefix}-",
        f"c-{project_prefix}-",
        f"g-r-{project_prefix}-",
        f"g-{project_prefix}-",
        f"claude-{project_prefix}-",
        f"gemini-{project_prefix}-",
        f"{project_prefix}-",
    ]
    for p in sorted(prefixes_to_strip, key=len, reverse=True):
        if clean_name.startswith(p):
            clean_name = clean_name[len(p) :]
            break
    clean_name = re.sub(r"[^a-zA-Z0-9_-]", "-", clean_name)
    clean_name = re.sub(r"-+", "-", clean_name)
    clean_name = clean_name.strip("-")

    if clean_name.isdigit():
        return f"{tmux_base}{clean_name}", f"{ai_base}{clean_name}"
    if not clean_name:
        idx = find_next_index(tmux_base, use_tmux=use_tmux)
        return f"{tmux_base}{idx}", f"{ai_base}{idx}"
    tmux_named = f"{tmux_base}{clean_name}-"
    ai_named = f"{ai_base}{clean_name}-"
    idx = find_next_index(tmux_named, use_tmux=use_tmux)
    return f"{tmux_named}{idx}", f"{ai_named}{idx}"


# --- Git Worktree Logic ---


def detect_repo_root():
    # Use --git-common-dir so we get the main repo root even when called from
    # inside a git worktree (--show-toplevel would return the worktree path instead,
    # causing create_worktree to nest worktrees and create circular .direnv symlinks).
    res = subprocess.run(["git", "rev-parse", "--git-common-dir"], capture_output=True, text=True, env=_git_env())
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


def create_worktree(ai_name: str) -> Path | None:
    repo_root = detect_repo_root()
    if not repo_root:
        return None

    # Deterministic repair backstop (AI-CLI-99): repo_root is a normal working
    # tree and must never be core.bare=true / carry a stale core.worktree.
    # Check + repair BEFORE any worktree op, regardless of what corrupted it.
    repair_bare_worktree_config(repo_root)

    wt_dir = repo_root / WORKTREE_DIR / ai_name
    if wt_dir.exists():
        # Verify it's still registered as a valid worktree; prune stale ones first
        subprocess.run(["git", "worktree", "prune"], capture_output=True, cwd=repo_root, env=_git_env())
        res = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            env=_git_env(),
        )
        if str(wt_dir) in res.stdout:
            _allow_trusted_worktree_envrc(repo_root, wt_dir)
            return wt_dir
        # Stale directory not in git's index — remove and recreate
        import shutil

        shutil.rmtree(wt_dir, ignore_errors=True)

    branch = f"wt-{ai_name}"
    wt_dir.parent.mkdir(parents=True, exist_ok=True)

    # Try creating new branch, fallback to existing
    res = subprocess.run(["git", "worktree", "add", str(wt_dir), "-b", branch], capture_output=True, env=_git_env())
    if res.returncode != 0:
        subprocess.run(["git", "worktree", "add", str(wt_dir), branch], capture_output=True, env=_git_env())

    # Repair again after the add — the backstop for whatever just ran (defense
    # in depth alongside the env scrub above).
    repair_bare_worktree_config(repo_root)

    if wt_dir.exists():
        # Track origin/main so git push ships to main, git pull --rebase syncs
        # from main. This must not fail silently (AI-CLI-128): a worktree
        # branch left without an upstream is one `git push` away from git
        # suggesting `--set-upstream origin wt-X`, which publishes a
        # same-named remote branch instead of shipping to main — the exact
        # drift that stranded ai-ide-mobile/mobile-1 46 commits behind main
        # for months. Retry once, then raise loudly rather than returning a
        # worktree that looks fine but is one push away from that state.
        upstream_res = subprocess.run(
            ["git", "branch", "--set-upstream-to=origin/main", branch],
            capture_output=True,
            cwd=repo_root,
            env=_git_env(),
        )
        if upstream_res.returncode != 0:
            upstream_res = subprocess.run(
                ["git", "branch", "--set-upstream-to=origin/main", branch],
                capture_output=True,
                cwd=repo_root,
                env=_git_env(),
            )
        if upstream_res.returncode != 0:
            stderr = upstream_res.stderr.decode(errors="replace").strip()
            raise RuntimeError(
                f"create_worktree: failed to set upstream=origin/main on branch "
                f"{branch!r} after retry (AI-CLI-128 — a worktree branch with no "
                f"upstream is one `git push` away from publishing a same-named "
                f"remote branch). git stderr: {stderr}"
            )

        # Symlink critical environment files
        for item in [".venv", ".claude", ".gemini", ".direnv"]:
            src = repo_root / item
            dst = wt_dir / item
            if src.exists() and not dst.exists():
                os.symlink(src, dst)
        # Register workspace trust so Claude Code loads the symlinked
        # .claude/settings.json permissions instead of dropping them with a
        # "workspace has not been trusted" warning (GH #72896). Worktrees
        # resolve to the main gitRoot, but register both for safety.
        from .trust import ensure_workspace_trusted

        ensure_workspace_trusted([repo_root, wt_dir])
        _allow_trusted_worktree_envrc(repo_root, wt_dir)
        return wt_dir
    return None


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

    try:
        # An existing worktree should not re-evaluate the root .envrc on every
        # launch.  Besides avoiding unnecessary work, that file may load
        # credentials from a network-backed provider.
        worktree_usable = subprocess.run(
            ["direnv", "exec", str(worktree_dir), "true"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if worktree_usable.returncode == 0:
            return

        # ``direnv status --json`` does not reliably report whether an .envrc
        # can actually be executed.  Use the same command as the launch path
        # as the authoritative root trust check instead.
        root_usable = subprocess.run(
            ["direnv", "exec", str(repo_root), "true"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return

    if root_usable.returncode != 0:
        return

    subprocess.run(
        ["direnv", "allow", str(worktree_dir)],
        cwd=repo_root,
        capture_output=True,
        text=True,
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
    diff = subprocess.run(["git", "-C", str(wt_dir), "diff", "--quiet"], env=_git_env())
    cached = subprocess.run(["git", "-C", str(wt_dir), "diff", "--cached", "--quiet"], env=_git_env())
    if diff.returncode == 0 and cached.returncode == 0:
        subprocess.run(["git", "worktree", "remove", str(wt_dir)], capture_output=True, env=_git_env())
        # Backstop repair after teardown — worktree remove is the other
        # documented trigger for the core.bare/core.worktree corruption class.
        repair_bare_worktree_config(repo_root)
