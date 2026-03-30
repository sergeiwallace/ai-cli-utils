import argparse
import sys
import os
import json
import time
import subprocess
import tomllib
import re
import shlex
from pathlib import Path

# --- XDG Directory Support ---


def get_xdg_config_home():
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ai-cli"


def get_xdg_state_home():
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "ai-cli"


def get_xdg_cache_home():
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "ai-cli"


# --- Configuration Management ---
# MAINTENANCE: when editing ai-cli, also update:
#   - docs/tools/cc-cli-design.md (usage reference, session naming, transport, auto-resume)
#   - README.md (if CLI interface changes)
#   - Code comments in this file (especially around session naming, resume logic, mosh/transport)
#   - CLAUDE.md ai-cli deploy note (reinstall in 3 places: Mac uv tool, server uv tool, aido venv)

DEFAULT_CONFIG = """## ai-cli configuration

[gemini]
## Projects that should NOT be sandboxed by default
## (Matches the project prefix in your project registry TOML)
# sandbox_whitelist = ["sw"]

[behavior]
## Enable system notifications on task completion
notify_on_exit = true

[worktree]
## Enable automatic git worktree isolation for new sessions
enabled = true

[session]
## Session names: c-{project}-{n} or c-r-{project}-{n} for remote sessions
stale_session_timeout = 15

[remote]
## Remote server for AI sessions (ai c --remote)
# host = "1.2.3.4"
# user = "ubuntu"
# port = 22
# identity_file = ""
# transport = "mosh"     # "ssh" or "mosh"
# project = "my-project" # default project (directory name under ~/projects/)

[sync]
## Remote host for cc sync (SSH user@host format). Derived from [remote] host/user if not set.
# remote_host = "user@host"
# staging_dir = "~/.claude-sync-staging"
# remote_url = "ssh://user@host/home/user/.claude-sync-staging.git"

[project]
## Name of the main project directory
# main_project = "myproject"
## Base directory for all projects (default: ~/projects)
# projects_dir = "~/projects"

[messaging]
# NATS server URLs for fleet messaging (heartbeats, events)
nats_servers = ["nats://localhost:4222"]
"""


def load_config():
    config_dir = get_xdg_config_home()
    config_path = config_dir / "config.toml"

    if not config_path.exists():
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path.write_text(DEFAULT_CONFIG)

    try:
        with open(config_path, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        print(f"Warning: Failed to load config from {config_path}: {e}", file=sys.stderr)
        return {}


# --- State Management ---


def get_session_map_path(engine="c"):
    state_dir = get_xdg_state_home()
    state_dir.mkdir(parents=True, exist_ok=True)
    if engine == "c":
        return Path.home() / ".claude" / "cc-sessions.json"
    return state_dir / "gemini_sessions.json"


def get_session_map(engine="c"):
    path = get_session_map_path(engine)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def save_session_map(d, engine="c"):
    path = get_session_map_path(engine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, indent=2))


# --- Project & Session Logic ---

WORKTREE_DIR = ".worktrees"


def _get_projects_dir() -> Path:
    """Return the base directory for all projects. Configurable via [project] projects_dir."""
    try:
        cfg = load_config().get("project", {})
        custom = cfg.get("projects_dir")
        if custom:
            return Path(custom).expanduser()
    except Exception:
        pass
    return Path.home() / "projects"


def _find_project_dir(name: str, _home: Path = None) -> Path:
    """Find a project directory under the configured projects_dir (default: ~/projects)."""
    if _home is not None:
        return _home / "projects" / name
    return _get_projects_dir() / name


def get_current_project_name() -> str:
    """Return the project directory name from cwd, handling git worktrees."""
    parts = Path.cwd().parts
    if WORKTREE_DIR in parts:
        idx = list(parts).index(WORKTREE_DIR)
        return parts[idx - 1]
    return Path.cwd().name


def _get_main_project_name() -> str | None:
    """Return the main project name from config, or None if not configured."""
    try:
        return load_config().get("project", {}).get("main_project") or None
    except Exception:
        return None


def _get_main_project_dir() -> Path | None:
    name = _get_main_project_name()
    if name is None:
        return None
    return _find_project_dir(name)


def _get_project_registry_path() -> Path | None:
    """Return the path to the project registry TOML, or None if not configured."""
    main_dir = _get_main_project_dir()
    if main_dir is None:
        return None
    name = _get_main_project_name()
    path = main_dir / f"{name}.toml"
    return path if path.exists() else None


def _get_handoff_queue_dir() -> Path | None:
    main_dir = _get_main_project_dir()
    if main_dir is None:
        return None
    return main_dir / ".handoff-queue"


def _get_project_prefix_by_name(project_name: str) -> str:
    """Look up a project's task_prefix from the project registry TOML by directory name."""
    registry = _get_project_registry_path()
    if registry is not None:
        try:
            with open(registry, "rb") as f:
                toml = tomllib.load(f)
            for p in toml.get("projects", []):
                if p.get("name") == project_name:
                    return p.get("task_prefix", project_name[:3]).lower()
        except Exception:
            pass
    return project_name[:3].lower()


def get_project_aliases() -> dict:
    """Build project alias map: task_prefix.lower() -> project name from project registry TOML."""
    aliases = {}
    registry = _get_project_registry_path()
    if registry is not None:
        try:
            with open(registry, "rb") as f:
                toml = tomllib.load(f)
            for p in toml.get("projects", []):
                prefix = p.get("task_prefix", "").lower()
                name = p.get("name", "")
                if prefix and name and prefix != name:
                    aliases[prefix] = name
        except Exception:
            pass
    return aliases


def get_latest_gemini_session_id():
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


def get_project_prefix():
    project_name = get_current_project_name()
    registry = _get_project_registry_path()
    if registry is not None:
        try:
            with open(registry, "rb") as f:
                config = tomllib.load(f)
            for p in config.get("projects", []):
                if p.get("name") == project_name:
                    return p.get("task_prefix", project_name[:3]).lower()
        except Exception:
            pass
    return project_name[:3].lower()


def find_next_index(prefix: str) -> int:
    i = 1
    while True:
        res = subprocess.run(["tmux", "has-session", "-t", f"{prefix}{i}"], capture_output=True)
        if res.returncode != 0:
            return i
        i += 1


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


# Matches ai-cli session names: c-sw-1, c-r-sw-1, g-aido-2, etc.
_AI_SESSION_RE = re.compile(r"^[cg](-r)?-[a-zA-Z0-9]+-\d+$")


def cleanup_stale_sessions(config: dict) -> None:
    """Kill stale ai-cli tmux sessions on each launch.

    Two cases:
    - Dead shell: AI exited, pane shows bash/zsh (auto-resume loop stopped).
    - Abandoned: AI still running but session unattached for > stale_session_timeout minutes.
    """
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


def build_session_name(
    engine_type: str, project_prefix: str, name: str, config: dict = None, is_remote: bool = False
) -> tuple[str, str]:
    """Build tmux session name and ai_name.

    Session name format: {c|g}[-r]-{project}-{index}
      e.g. c-sw-1, c-r-sw-1, g-aido-2
    ai_name (used for --name, worktrees, session map): {project}-{index}
      e.g. sw-1, aido-2
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
        idx = find_next_index(tmux_base)
        return f"{tmux_base}{idx}", f"{ai_base}{idx}"
    tmux_named = f"{tmux_base}{clean_name}-"
    ai_named = f"{ai_base}{clean_name}-"
    idx = find_next_index(tmux_named)
    return f"{tmux_named}{idx}", f"{ai_named}{idx}"


# --- Git Worktree Logic ---


def detect_repo_root():
    res = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return Path(res.stdout.strip()) if res.returncode == 0 else None


def create_worktree(ai_name: str) -> Path | None:
    repo_root = detect_repo_root()
    if not repo_root:
        return None

    wt_dir = repo_root / WORKTREE_DIR / ai_name
    if wt_dir.exists():
        # Verify it's still registered as a valid worktree; prune stale ones first
        subprocess.run(["git", "worktree", "prune"], capture_output=True, cwd=repo_root)
        res = subprocess.run(["git", "worktree", "list", "--porcelain"], capture_output=True, text=True, cwd=repo_root)
        if str(wt_dir) in res.stdout:
            return wt_dir
        # Stale directory not in git's index — remove and recreate
        import shutil

        shutil.rmtree(wt_dir, ignore_errors=True)

    branch = f"wt-{ai_name}"
    wt_dir.parent.mkdir(parents=True, exist_ok=True)

    # Try creating new branch, fallback to existing
    res = subprocess.run(["git", "worktree", "add", str(wt_dir), "-b", branch], capture_output=True)
    if res.returncode != 0:
        subprocess.run(["git", "worktree", "add", str(wt_dir), branch], capture_output=True)

    # Track origin/main so git push ships to main, git pull --rebase syncs from main
    subprocess.run(["git", "branch", "--set-upstream-to=origin/main", branch], capture_output=True, cwd=repo_root)

    if wt_dir.exists():
        # Symlink critical environment files
        for item in [".venv", ".claude", ".gemini", ".direnv"]:
            src = repo_root / item
            dst = wt_dir / item
            if src.exists() and not dst.exists():
                os.symlink(src, dst)
        return wt_dir
    return None


def cleanup_worktree(ai_name: str):
    repo_root = detect_repo_root()
    if not repo_root:
        return
    wt_dir = repo_root / WORKTREE_DIR / ai_name
    if not wt_dir.exists():
        return

    # Only remove if clean
    diff = subprocess.run(["git", "-C", str(wt_dir), "diff", "--quiet"])
    cached = subprocess.run(["git", "-C", str(wt_dir), "diff", "--cached", "--quiet"])
    if diff.returncode == 0 and cached.returncode == 0:
        subprocess.run(["git", "worktree", "remove", str(wt_dir)], capture_output=True)


# --- Script Generation ---


def get_engine_script(
    engine: str,
    ai_name: str,
    session: str,
    prefix: str,
    project_prefix: str,
    session_id_uuid: str = None,
    sandbox: bool = True,
    worktree_dir: str = None,
    notify: bool = False,
    is_remote: bool = False,
) -> str:
    env_var_prefix = "CC" if engine == "c" else "GG"
    sandbox_flag = "-s" if sandbox else ""
    cd_cmd = f"cd {worktree_dir}" if worktree_dir else ":"
    notify_cmd = 'ai internal notify "$tmux_session" "Agent Finished Task" 2>/dev/null || true' if notify else "true"

    script = f"""
    {cd_cmd}
    first_run=true
    ai_name="{ai_name}"
    engine="{engine}"
    tmux_session="{session}"
    uuid="{session_id_uuid or ""}"
    project_prefix="{project_prefix}"

    # --dangerously-skip-permissions is blocked when running as root
    if [[ $(id -u) -eq 0 ]]; then
      claude_perms_flag=""
    else
      claude_perms_flag="--dangerously-skip-permissions"
    fi

    if [[ "$engine" == "c" ]]; then
      signal_file="/tmp/cc-exit-$tmux_session"
      prompt_file="/tmp/cc-resume-prompt-$tmux_session"
    else
      signal_file="/tmp/gg-exit-$tmux_session"
      reload_file="/tmp/gg-reload-$tmux_session"
      restart_file="/tmp/gg-restart-$tmux_session"
      prompt_file="/tmp/gg-resume-prompt-$tmux_session"
    fi
    lock_file="/tmp/ai-watcher-lock-$tmux_session"
    
    export AI_TMUX_SESSION="$tmux_session"
    export {env_var_prefix}_TMUX_SESSION="$tmux_session"
    watcher_pid=""

    start_watcher() {{
      if [[ -n "$watcher_pid" ]]; then
        kill "$watcher_pid" 2>/dev/null || true
        watcher_pid=""
      fi
      rm -f "$lock_file"

      (echo $$ > "$lock_file"
      trap 'rm -f "$lock_file"' EXIT
      counter=0
      while true; do
        if (( counter % 30 == 0 )); then
          heartbeat_json=$(printf '{{"status": "WORKING", "project": "%s", "ai_name": "%s"}}' "$project_prefix" "$ai_name")
          ai internal publish-heartbeat "$tmux_session" "$heartbeat_json" 2>/dev/null || true
        fi
        (( counter++ ))
        
        if [[ -f "$signal_file" ]]; then
          rm -f "$signal_file"
          sleep 1
          tmux send-keys -t "$tmux_session" Escape
          sleep 0.5
          tmux send-keys -t "$tmux_session" C-u
          sleep 0.2
          tmux send-keys -t "$tmux_session" Escape
          sleep 0.3
          if [[ "$engine" == "g" ]]; then
            tmux send-keys -t "$tmux_session" "/resume save $ai_name" C-m
            sleep 2
          fi
          tmux send-keys -t "$tmux_session" '/exit' C-m
          break
        fi
        if [[ "$engine" == "g" && -f "$reload_file" ]]; then
          rm -f "$reload_file"
          tmux send-keys -t "$tmux_session" Escape
          sleep 0.2
          tmux send-keys -t "$tmux_session" C-u
          tmux send-keys -t "$tmux_session" "/memory reload" C-m
        fi
        if [[ "$engine" == "g" && -f "$restart_file" ]]; then
          rm -f "$restart_file"
          tmux send-keys -t "$tmux_session" Escape
          sleep 0.2
          tmux send-keys -t "$tmux_session" C-u
          tmux send-keys -t "$tmux_session" "R"
        fi
        sleep 1
      done) &
      watcher_pid=$!
    }}

    # Auto-start sync watch and memory watch (PID files prevent duplicates)
    ai sync watch &>/dev/null &
    ai memory watch &>/dev/null &

    # iTerm2 fleet management: set profile, rolling tab color, badge, tab title
    # Only runs when TERM_PROGRAM is iTerm.app (skipped on Ghostty, Windows Terminal, etc.)
    _iterm2_fleet_setup() {{
      [[ "$TERM_PROGRAM" != "iTerm.app" ]] && return 0
      local num="$1" stype="$2" sname="$3"

      # Profile switch
      case "$stype" in
        cc)    printf '\\e]1337;SetProfile=ClaudeCode\\a' ;;
        shell) printf '\\e]1337;SetProfile=ShellUtility\\a' ;;
        *)     return 0 ;;
      esac

      # Rolling tab color (10 distinct colors, assigned by session number)
      if [[ "$stype" == "cc" ]]; then
        local colors=("6440dc" "4a90d9" "2ecc71" "e67e22" "e74c3c"
                      "1abc9c" "9b59b6" "f39c12" "3498db" "e91e63")
        local idx=$(( (num - 1) % ${{#colors[@]}} ))
        printf '\\e]1337;SetColors=tab=%s\\a' "${{colors[$idx]}}"
      fi

      # User variables for badge interpolation
      printf '\\e]1337;SetUserVar=%s=%s\\a' \
        "sessionType" "$(echo -n "$stype" | base64)"
      printf '\\e]1337;SetUserVar=%s=%s\\a' \
        "sessionNum" "$(echo -n "$num" | base64)"
      printf '\\e]1337;SetUserVar=%s=%s\\a' \
        "tmuxSession" "$(echo -n "$sname" | base64)"

      # Badge
      local badge_text="$stype sw-$num"
      printf '\\e]1337;SetBadgeFormat=%s\\a' \
        "$(echo -n "$badge_text" | base64)"

      # Tab title
      printf '\\e]0;%s sw-%s\\a' "$stype" "$num"
    }}

    # iTerm2 status updates: badge + tab title (NOT color — color is for identity)
    _iterm2_status() {{
      [[ "$TERM_PROGRAM" != "iTerm.app" ]] && return 0
      local status="$1" num="$2" stype="$3"
      local badge="" title=""
      case "$status" in
        running)   badge="▶ $stype sw-$num";   title="▶ $stype sw-$num" ;;
        waiting)   badge="⏸ WAIT sw-$num";     title="⏸ WAIT sw-$num" ;;
        done)      badge="✓ DONE sw-$num";     title="✓ DONE sw-$num" ;;
        error)     badge="✗ ERROR sw-$num";    title="✗ ERROR sw-$num" ;;
        resuming)  badge="↻ sw-$num";          title="↻ sw-$num" ;;
      esac
      [[ -n "$badge" ]] && printf '\\e]1337;SetBadgeFormat=%s\\a' "$(echo -n "$badge" | base64)"
      [[ -n "$title" ]] && printf '\\e]0;%s\\a' "$title"
    }}

    # Extract session number from ai_name (e.g., "sw-3" → "3")
    _session_num=$(echo "$ai_name" | grep -oE '[0-9]+$' || echo "1")
    _session_type="cc"
    [[ "$engine" == "g" ]] && _session_type="gemini"
    _iterm2_fleet_setup "$_session_num" "$_session_type" "$tmux_session"

    # Export for CC Notification hook to use
    export ITERM2_SESSION_NUM="$_session_num"
    export ITERM2_SESSION_TYPE="$_session_type"

    trap 'kill "$watcher_pid" 2>/dev/null; rm -f "$lock_file"; ai internal cleanup-worktree "$ai_name" 2>/dev/null' EXIT

    while true; do
      start_watcher
      start_ts=$(date +%s)
      # Re-emit iTerm2 setup + set status to running
      _iterm2_fleet_setup "$_session_num" "$_session_type" "$tmux_session"
      _iterm2_status "running" "$_session_num" "$_session_type"
      (ai internal publish-event "$tmux_session" "START" 2>/dev/null || true) &
      (ai internal publish-session-event "$tmux_session" "started" 2>/dev/null || true) &

      if [[ -f "scripts/session-broker.py" ]] && $first_run; then
        python3 scripts/session-broker.py --engine "$engine" 2>/dev/null || true
      fi

      if [[ -f "$prompt_file" ]]; then
        resume_msg=$(cat "$prompt_file")
        rm -f "$prompt_file"
        if [[ "$engine" == "c" ]]; then
          claude $claude_perms_flag --continue "$resume_msg" --name "$ai_name"
        else
          (sleep 4; tmux send-keys -t "$tmux_session" "$resume_msg" C-m) &
          if [[ -n "$uuid" ]]; then gemini -y {sandbox_flag} -r "$uuid"
          else gemini -y {sandbox_flag} -i "/resume load $ai_name"
          fi
        fi
      else
        if [[ "$engine" == "c" ]]; then
          # Check if any conversation exists in this worktree directory.
          # --continue resumes the most recent; if none exists, start fresh.
          cc_project_dir="$HOME/.claude/projects/$(echo "$PWD" | sed 's|[/.]|-|g')"
          if [[ -d "$cc_project_dir" ]] && ls "$cc_project_dir"/*.jsonl &>/dev/null; then
            claude $claude_perms_flag --continue --name "$ai_name"
          else
            claude $claude_perms_flag --name "$ai_name"
          fi
        else
          if [[ -n "$uuid" ]]; then gemini -y {sandbox_flag} -r "$uuid"
          else gemini -y {sandbox_flag} -i "/resume load $ai_name"
          fi
        fi
      fi
      
      # Set iTerm2 status based on how CC exited + publish NATS event for gateway
      _exit_elapsed=$(( $(date +%s) - start_ts ))
      if (( _exit_elapsed < 3 )); then
        _iterm2_status "error" "$_session_num" "$_session_type"
        (ai internal publish-session-event "$tmux_session" "error" 2>/dev/null || true) &
      else
        _iterm2_status "done" "$_session_num" "$_session_type"
        (ai internal publish-session-event "$tmux_session" "completed" 2>/dev/null || true) &
      fi

      {notify_cmd}

      new_uuid=$(ai internal get-latest-gemini-id 2>/dev/null)
      if [[ -n "$new_uuid" ]]; then
        uuid="$new_uuid"
        ai internal update-session-map g "$ai_name" "$uuid" 2>/dev/null
      fi

      first_run=false
      elapsed=$_exit_elapsed
      if (( elapsed < 3 )); then
        echo "AI CLI exited too quickly ($elapsed s) — stopping. Run 'ai' to retry."
        break
      fi
      _iterm2_status "resuming" "$_session_num" "$_session_type"
      echo "Resuming... (Ctrl-C to exit to shell)"
      sleep 0.5 || break
    done
    (ai internal publish-event "$tmux_session" "STOP" 2>/dev/null || true) &
    (ai internal publish-session-event "$tmux_session" "stopped" 2>/dev/null || true) &
    {('tmux detach-client -s "$tmux_session" 2>/dev/null || true') if is_remote else "exec $SHELL"}
    """
    return script


# --- Subcommands ---


def post_handoff(title, priority, project, message):
    handoff_dir = _get_handoff_queue_dir()
    if handoff_dir is None:
        print("Error: [project] main_project not set in ~/.config/ai-cli/config.toml", file=sys.stderr)
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
    out = f'---\nid: "{next_id}"\ntitle: "{title}"\npriority: {priority}\nproject: {project}\ncreated_by: {created_by}\ncreated_at: "{now}"\nclaimed_by: null\nclaimed_at: null\n---\n\n{message}\n'
    (queue_dir / filename).write_text(out)
    print(queue_dir / filename)


def check_handoff():
    handoff_dir = _get_handoff_queue_dir()
    if handoff_dir is None:
        return
    queue_dir = handoff_dir / "pending"
    if not queue_dir.exists():
        return
    best_file, best_prio = None, 9
    for f in queue_dir.glob("*.md"):
        prio = 9
        for line in f.read_text().splitlines():
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
    if best_file:
        print(best_file)


def claim_handoff(file_path, claimer=None):
    if claimer is None:
        claimer = os.environ.get("AI_TMUX_SESSION", "unknown")
    handoff_dir = _get_handoff_queue_dir()
    if handoff_dir is None:
        print("Error: [project] main_project not set in ~/.config/ai-cli/config.toml", file=sys.stderr)
        sys.exit(1)
    claimed_dir = handoff_dir / "claimed"
    claimed_dir.mkdir(parents=True, exist_ok=True)
    src, dst = Path(file_path), claimed_dir / Path(file_path).name
    try:
        src.rename(dst)
    except Exception:
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
        return
    completed_dir = handoff_dir / "completed"
    completed_dir.mkdir(parents=True, exist_ok=True)
    src, dst = Path(file_path), completed_dir / Path(file_path).name
    try:
        src.rename(dst)
    except Exception:
        pass


def trigger_background_update():
    state_file = get_xdg_state_home() / "update_check.json"
    now = time.time()
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
            if now - state.get("last_checked", 0) < 3600 * 24:
                return
        except Exception:
            pass
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"last_checked": now}))
    subprocess.Popen(
        ["uv", "tool", "upgrade", "ai-cli"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


# --- CLI Entry Point ---


def cli():
    config = load_config()

    if len(sys.argv) > 1 and sys.argv[1] == "internal":
        if len(sys.argv) < 3:
            print("Usage: ai internal <action> [args...]", file=sys.stderr)
            sys.exit(1)
        action = sys.argv[2]
        if action == "get-latest-gemini-id":
            res = get_latest_gemini_session_id()
            if res:
                print(res)
            sys.exit(0)
        elif action == "update-session-map":
            if len(sys.argv) < 6:
                print("Usage: ai internal update-session-map <engine> <ai_name> <uuid>", file=sys.stderr)
                sys.exit(1)
            engine, ai_name, uuid = sys.argv[3], sys.argv[4], sys.argv[5]
            d = get_session_map(engine)
            d[ai_name] = uuid
            save_session_map(d, engine)
            sys.exit(0)
        elif action == "cleanup-worktree":
            if len(sys.argv) < 4:
                print("Usage: ai internal cleanup-worktree <ai_name>", file=sys.stderr)
                sys.exit(1)
            cleanup_worktree(sys.argv[3])
            sys.exit(0)
        elif action == "notify":
            if len(sys.argv) < 5:
                print("Usage: ai internal notify <session_id> <message>", file=sys.stderr)
                sys.exit(1)
            from .notifications import NotificationManager

            session_id = sys.argv[3]
            msg = sys.argv[4]
            NotificationManager(session_id).notify(msg)
            sys.exit(0)
        elif action == "publish-event":
            if len(sys.argv) < 5:
                print("Usage: ai internal publish-event <session_id> <event_type>", file=sys.stderr)
                sys.exit(1)
            import asyncio
            from .messaging import NATSClient

            session_id = sys.argv[3]
            event_type = sys.argv[4]
            nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
            client = NATSClient(servers=nats_servers)
            try:
                asyncio.run(client.publish_event(session_id, event_type))
            except Exception:
                pass  # NATS unavailable — non-fatal
            sys.exit(0)
        elif action == "publish-heartbeat":
            if len(sys.argv) < 5:
                print("Usage: ai internal publish-heartbeat <session_id> <data_json>", file=sys.stderr)
                sys.exit(1)
            import asyncio
            from .messaging import NATSClient

            session_id = sys.argv[3]
            try:
                data = json.loads(sys.argv[4])
            except json.JSONDecodeError as e:
                print(f"Invalid JSON: {e}", file=sys.stderr)
                sys.exit(1)
            nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
            client = NATSClient(servers=nats_servers)
            try:
                asyncio.run(client.publish_heartbeat(session_id, data))
            except Exception:
                pass  # NATS unavailable — non-fatal
            sys.exit(0)
        elif action == "publish-session-event":
            if len(sys.argv) < 5:
                print("Usage: ai internal publish-session-event <session_id> <started|stopped>", file=sys.stderr)
                sys.exit(1)
            import asyncio
            from .messaging import NATSClient

            session_id = sys.argv[3]
            event_verb = sys.argv[4]  # "started" or "stopped"
            subject = f"session.{session_id}.{event_verb}"
            nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
            client = NATSClient(servers=nats_servers)
            try:
                asyncio.run(client.publish(subject, {"session_id": session_id, "event": event_verb, "ts": time.time()}))
            except Exception:
                pass  # NATS unavailable — non-fatal
            sys.exit(0)
        elif action == "publish":
            if len(sys.argv) < 5:
                print("Usage: ai internal publish <subject> <json_payload>", file=sys.stderr)
                sys.exit(1)
            import asyncio
            from .messaging import NATSClient

            subject = sys.argv[3]
            try:
                payload = json.loads(sys.argv[4])
            except (json.JSONDecodeError, IndexError):
                payload = {}
            nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
            client = NATSClient(servers=nats_servers)
            try:
                asyncio.run(client.publish(subject, payload))
            except Exception:
                pass  # NATS unavailable — non-fatal
            sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "upgrade":
        print("Upgrading ai-cli...", file=sys.stderr)
        os.execvp("uv", ["uv", "tool", "upgrade", "ai-cli"])

    if len(sys.argv) > 1 and sys.argv[1] == "handoff":
        if len(sys.argv) == 2:
            print("Usage: ai handoff [post|check|claim|complete]")
            sys.exit(1)
        action = sys.argv[2]
        if action == "post":
            post_handoff(sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6])
        elif action == "check":
            check_handoff()
        elif action == "claim":
            claim_handoff(sys.argv[3])
        elif action == "complete":
            complete_handoff(sys.argv[3])
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "memory":
        if len(sys.argv) < 3 or sys.argv[2] != "watch":
            print("Usage: ai memory watch", file=sys.stderr)
            sys.exit(1)
        from .memory import memory_watch

        sys.exit(memory_watch())

    if len(sys.argv) > 1 and sys.argv[1] == "quota":
        if len(sys.argv) < 3 or sys.argv[2] != "watch":
            print("Usage: ai quota watch", file=sys.stderr)
            sys.exit(1)
        from .quota import quota_watch

        sys.exit(quota_watch())

    if len(sys.argv) > 1 and sys.argv[1] == "telemetry":
        if len(sys.argv) < 3 or sys.argv[2] != "writer":
            print("Usage: ai telemetry writer", file=sys.stderr)
            sys.exit(1)
        from .telemetry import telemetry_writer

        sys.exit(telemetry_writer())

    if len(sys.argv) > 1 and sys.argv[1] == "sync":
        if len(sys.argv) == 2:
            print("Usage: ai sync [push|pull|conflicts|watch] [--memories-only] [--dry-run] [--verbose] [--force]")
            sys.exit(1)
        from .sync import sync_push, sync_pull, sync_conflicts, sync_watch

        action = sys.argv[2]
        flags = sys.argv[3:]
        if action == "push":
            sys.exit(sync_push(flags))
        elif action == "pull":
            sys.exit(sync_pull(flags))
        elif action == "conflicts":
            sys.exit(sync_conflicts(flags))
        elif action == "watch":
            sys.exit(sync_watch(flags))
        else:
            print(f"Unknown sync action: {action}. Use push, pull, conflicts, or watch.", file=sys.stderr)
            sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "reconnect":
        # List remote tmux sessions and print reconnect commands.
        # Usage: ai reconnect [session_numbers...]
        # Examples: ai reconnect (all), ai reconnect 1 3 (specific)
        remote_cfg = config.get("remote", {})
        host = remote_cfg.get("host", "")
        user = remote_cfg.get("user", "ubuntu")
        if not host:
            print("Error: [remote] host not set in ~/.config/ai-cli/config.toml", file=sys.stderr)
            sys.exit(1)

        requested = [int(x) for x in sys.argv[2:] if x.isdigit()] if len(sys.argv) > 2 else None

        # Find tmux sessions matching remote pattern on the server
        probe = subprocess.run(
            ["ssh", f"{user}@{host}", "tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            print("Error: could not list remote tmux sessions", file=sys.stderr)
            sys.exit(1)

        remote_sessions = [s.strip() for s in probe.stdout.splitlines() if s.strip().startswith("c-r-")]
        if not remote_sessions:
            print("No remote CC sessions found on server.")
            sys.exit(0)

        # Filter to requested sessions if specified
        if requested:
            remote_sessions = [s for s in remote_sessions if any(s.endswith(f"-{n}") for n in requested)]

        if not remote_sessions:
            print(f"No matching remote sessions for: {requested}")
            sys.exit(0)

        aliases = get_project_aliases()
        print(f"Found {len(remote_sessions)} remote session(s). Run each in a separate terminal:\n")
        for session_name in sorted(remote_sessions):
            parts = session_name.split("-")
            if len(parts) >= 4:
                num = parts[-1]
                proj_prefix = "-".join(parts[2:-1])
            else:
                continue
            project_name = aliases.get(proj_prefix, proj_prefix)
            if project_name == proj_prefix:
                print(f"  ai c {num} -R")
            else:
                print(f"  ai c {num} -R -p {proj_prefix}")
        print()
        sys.exit(0)

    trigger_background_update()

    parser = argparse.ArgumentParser(description="Unified AI CLI for Claude and Gemini")
    parser.add_argument("engine", choices=["c", "g"], help="c for Claude, g for Gemini")
    parser.add_argument("name", nargs="?", default="", help="Session name or index")
    parser.add_argument("-r", "--resume", action="store_true", help="Resume an existing session")
    parser.add_argument("-o", "--once", action="store_true", help="Run once without tmux auto-resume loop")
    parser.add_argument("-b", "--bare", action="store_true", help="Run bare tool without tmux at all")
    parser.add_argument("-n", "--notify", action="store_true", help="Fire system notifications on task completion")
    parser.add_argument("-s", "--sandbox", action="store_true", help="Explicitly enable sandboxing")
    parser.add_argument("--no-sandbox", action="store_true", help="Explicitly disable sandboxing")
    parser.add_argument("--no-worktree", action="store_true", help="Disable git worktree isolation")
    parser.add_argument(
        "-R", "--remote", action="store_true", help="Run session on remote server (configured in [remote])"
    )
    parser.add_argument(
        "-p",
        "--project",
        default="",
        help="Project to open on remote server (directory name, e.g. 'myproject', 'webapp')",
    )
    parser.add_argument("--is-remote", action="store_true", help=argparse.SUPPRESS)  # set by local machine when SSHing
    parser.add_argument("--project-prefix", default="", help=argparse.SUPPRESS)  # override auto-detected project prefix

    args, unknown = parser.parse_known_args()
    engine = args.engine
    project_prefix = args.project_prefix if args.project_prefix else get_project_prefix()
    engine_short = "c" if engine == "c" else "g"
    remote_seg = "-r" if args.is_remote else ""
    prefix = f"{engine_short}{remote_seg}-{project_prefix}-"

    whitelist = config.get("gemini", {}).get("sandbox_whitelist", ["sw"])
    if args.no_sandbox:
        use_sandbox = False
    elif args.sandbox:
        use_sandbox = True
    else:
        use_sandbox = project_prefix not in whitelist

    sandbox_flag = "-s" if use_sandbox else ""

    name = args.name
    if not name and unknown:
        name = unknown[0]

    if args.remote:
        remote_cfg = config.get("remote", {})
        host = remote_cfg.get("host", "")
        if not host:
            print("Error: [remote] host not set in ~/.config/ai-cli/config.toml", file=sys.stderr)
            sys.exit(1)
        user = remote_cfg.get("user", "ubuntu")
        port = str(remote_cfg.get("port", 22))
        id_file = remote_cfg.get("identity_file", "")
        transport = remote_cfg.get("transport", "mosh")
        aliases = get_project_aliases()
        raw_project = args.project or get_current_project_name()
        remote_project = aliases.get(raw_project, raw_project)
        # When -p is provided, derive prefix from the target project's task_prefix
        if args.project:
            remote_prefix = _get_project_prefix_by_name(remote_project)
        else:
            remote_prefix = project_prefix
        remote_cmd = f"ai {engine} --is-remote --project-prefix {remote_prefix} --project {shlex.quote(remote_project)}"
        if args.resume:
            remote_cmd += " --resume"
        if name:
            remote_cmd += f" {name}"
        if transport == "mosh":
            mosh_args = ["mosh"]
            if port != "22":
                mosh_args += ["--ssh", f"ssh -p {port}" + (f" -i {os.path.expanduser(id_file)}" if id_file else "")]
            elif id_file:
                mosh_args += ["--ssh", f"ssh -i {os.path.expanduser(id_file)}"]
            mosh_args.append(f"{user}@{host}")
            mosh_args += ["--", "bash", "-l", "-c", remote_cmd]
            os.execvp("mosh", mosh_args)
        else:
            ssh_args = ["ssh", "-t", "-p", port]
            if id_file:
                ssh_args += ["-i", os.path.expanduser(id_file)]
            ssh_args.append(f"{user}@{host}")
            ssh_args.append(f"bash -l -c {shlex.quote(remote_cmd)}")
            os.execvp("ssh", ssh_args)

    # When running as the remote side of an --remote session, cd into the project directory
    # before creating the worktree so git commands work correctly.
    if args.is_remote:
        aliases = get_project_aliases()
        raw_project = args.project or config.get("remote", {}).get("project") or _get_main_project_name()
        if raw_project:
            project_name = aliases.get(raw_project, raw_project)
            project_dir = _find_project_dir(project_name)
            if project_dir.exists():
                os.chdir(project_dir)

    if args.bare:
        if engine == "c":
            perms = [] if os.getuid() == 0 else ["--dangerously-skip-permissions"]
            os.execvp("claude", ["claude"] + perms + unknown)
        else:
            gemini_args = ["gemini", "-y"]
            if use_sandbox:
                gemini_args.append("-s")
            os.execvp("gemini", gemini_args + unknown)

    if args.resume:
        session = resolve_session(prefix, name)
        if not session:
            print(f"No matching session found for '{prefix}{name or '*'}'")
            sys.exit(1)
        os.execvp("tmux", ["tmux", "attach-session", "-t", session])

    cleanup_stale_sessions(config)
    session_id, ai_name = build_session_name(engine, project_prefix, name, config, is_remote=args.is_remote)

    # Worktree setup
    worktree_path = None
    if config.get("worktree", {}).get("enabled", True) and not args.no_worktree:
        worktree_path = create_worktree(ai_name)
        if worktree_path:
            # Sync worktree with any changes that landed on main from other sessions
            subprocess.run(["git", "pull", "--rebase", "--autostash"], capture_output=True, cwd=worktree_path)

    d = get_session_map(engine)
    uuid = d.get(ai_name)

    if args.once:
        cd_pref = f"cd {worktree_path} && " if worktree_path else ""
        if engine == "c":
            perms = "" if os.getuid() == 0 else "--dangerously-skip-permissions"
            os.execvp(
                "tmux",
                [
                    "tmux",
                    "new-session",
                    "-s",
                    session_id,
                    "--",
                    "bash",
                    "-c",
                    f"{cd_pref}claude {perms} --name {ai_name}".strip(),
                ],
            )
        else:
            if uuid:
                os.execvp(
                    "tmux",
                    [
                        "tmux",
                        "new-session",
                        "-s",
                        session_id,
                        "--",
                        "bash",
                        "-c",
                        f"{cd_pref}gemini -y {sandbox_flag} -r {uuid}",
                    ],
                )
            else:
                os.execvp(
                    "tmux",
                    [
                        "tmux",
                        "new-session",
                        "-s",
                        session_id,
                        "--",
                        "bash",
                        "-c",
                        f"{cd_pref}gemini -y {sandbox_flag} -i '/resume load {ai_name}'",
                    ],
                )

    script = get_engine_script(
        engine,
        ai_name,
        session_id,
        prefix,
        project_prefix,
        uuid,
        use_sandbox,
        str(worktree_path) if worktree_path else None,
        notify=args.notify,
        is_remote=args.is_remote,
    )
    # Check if session already exists (e.g., re-attaching after disconnect)
    existing = subprocess.run(["tmux", "has-session", "-t", session_id], capture_output=True)
    if existing.returncode == 0:
        # Session exists — just attach to it
        os.execvp("tmux", ["tmux", "attach-session", "-t", session_id])
    elif args.is_remote:
        # Create session attached (not -d) so Claude gets a proper PTY.
        # The script's tmux detach-client at loop end drops us back to the SSH/mosh shell.
        os.execvp("tmux", ["tmux", "new-session", "-s", session_id, "--", "bash", "-c", script])
    else:
        os.execvp("tmux", ["tmux", "new-session", "-s", session_id, "--", "bash", "-c", script])


if __name__ == "__main__":
    cli()
