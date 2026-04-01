import argparse
import sys
import os
import json
import shutil
import time
import subprocess
import tomllib
import re
import shlex
from pathlib import Path

# --- XDG Directory Support ---


def _migrate_xdg_dir(old: Path, new: Path) -> Path:
    """Rename old XDG dir to new name if old exists and new does not."""
    if old.exists() and not new.exists():
        old.rename(new)
    return new


def get_xdg_config_home():
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return _migrate_xdg_dir(base / "ai-cli", base / "ai-cli-utils")


def get_xdg_state_home():
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return _migrate_xdg_dir(base / "ai-cli", base / "ai-cli-utils")


def get_xdg_cache_home():
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return _migrate_xdg_dir(base / "ai-cli", base / "ai-cli-utils")


# --- Configuration Management ---
# MAINTENANCE: when editing ai-cli, also update:
#   - docs/tools/cc-cli-design.md (usage reference, session naming, transport, auto-resume)
#   - README.md (if CLI interface changes)
#   - Code comments in this file (especially around session naming, resume logic, mosh/transport)
#   - CLAUDE.md ai-cli deploy note (reinstall in 3 places: Mac uv tool, server uv tool, extra_venvs)

DEFAULT_CONFIG = """## ai-cli-utils configuration

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

[machine]
## Identifier for this machine. Used to target handoffs to a specific host.
## Set AI_CLI_HOST in your shell environment (~/.bashrc or ~/.zshrc) above the interactive guard.
## Example values: "mac", "hetzner", "work-laptop"
# host_id = ""

[update]
## Additional venv paths to install ai-cli-utils into after 'ai update'
## Useful if you have tools or virtual environments that depend on ai-cli-utils
# extra_venvs = ["/home/user/projects/mytool/.venv"]
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


def _find_project_dir(name: str, _home: Path | None = None) -> Path:
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


# --- Project Registry (cached, validated) ---

_registry_cache: list[dict] | None = None


def load_project_registry(*, _force: bool = False) -> list[dict]:
    """Load and validate the project registry. Caches result after first call.

    Returns list of project dicts. Raises SystemExit on schema violations.
    Pass _force=True to bypass cache (used in tests).
    """
    global _registry_cache
    if _registry_cache is not None and not _force:
        return _registry_cache

    registry_path = _get_project_registry_path()
    if registry_path is None:
        _registry_cache = []
        return _registry_cache

    try:
        with open(registry_path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        _registry_cache = []
        return _registry_cache

    projects = data.get("projects", [])

    # Schema validation: name and task_prefix required, both unique
    names_seen: set[str] = set()
    prefixes_seen: set[str] = set()
    for p in projects:
        name = p.get("name", "")
        prefix = p.get("task_prefix", "")
        if not name or not prefix:
            print(f"Error: project registry entry missing name or task_prefix: {p}", file=sys.stderr)
            sys.exit(1)
        name_lower = name.lower()
        prefix_lower = prefix.lower()
        if name_lower in names_seen:
            print(f"Error: duplicate project name in registry: {name}", file=sys.stderr)
            sys.exit(1)
        if prefix_lower in prefixes_seen:
            print(f"Error: duplicate task_prefix in registry: {prefix}", file=sys.stderr)
            sys.exit(1)
        names_seen.add(name_lower)
        prefixes_seen.add(prefix_lower)

    _registry_cache = projects
    return projects


def validate_registry_completeness(*, interactive: bool = True) -> bool:
    """Check that all ~/projects/* directories are registered. Returns True if complete.

    If interactive=True and unregistered dirs found, prompts user to register.
    Returns False (and prints error) if user declines registration.
    """
    # Skip if no registry configured
    if _get_project_registry_path() is None:
        return True

    projects_dir = _get_projects_dir()
    if not projects_dir.exists():
        return True

    registry = load_project_registry()
    registered_names = {p.get("name", "").lower() for p in registry}

    # Scan for project directories (skip hidden dirs, .worktrees, etc.)
    unregistered = []
    for d in sorted(projects_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if d.name.lower() not in registered_names:
            unregistered.append(d.name)

    if not unregistered:
        return True

    if not interactive:
        print(f"Error: unregistered project directories: {', '.join(unregistered)}", file=sys.stderr)
        return False

    registry_path = _get_project_registry_path()
    assert registry_path is not None
    for name in unregistered:
        suggested_prefix = name.upper().replace("-", "_")[:8]
        try:
            answer = input(
                f'Unregistered project: "{name}" (~/{projects_dir.name}/{name})\n'
                f"Suggested task_prefix: {suggested_prefix}\n"
                f"Add to registry? [Y/n, or enter custom prefix]: "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nRegistry incomplete — exiting.", file=sys.stderr)
            return False

        if answer.lower() == "n":
            print("Registry incomplete — exiting. All projects must be registered.", file=sys.stderr)
            return False

        prefix = answer if answer and answer.lower() != "y" else suggested_prefix

        # Append to registry TOML
        entry = f'\n[[projects]]\nname = "{name}"\ntask_prefix = "{prefix}"\ntype = "tool"\nactive = true\n'
        with open(registry_path, "a") as f:
            f.write(entry)
        print(f'Registered "{name}" with prefix "{prefix}"')

    # Force reload after registration
    global _registry_cache
    _registry_cache = None
    load_project_registry(_force=True)
    return True


def _get_project_prefix_by_name(project_name: str) -> str:
    """Look up a project's task_prefix from the project registry by directory name."""
    for p in load_project_registry():
        if p.get("name") == project_name:
            return p.get("task_prefix", project_name[:3]).lower()
    return project_name[:3].lower()


def get_project_aliases() -> dict:
    """Build project alias map: task_prefix.lower() -> project name from project registry."""
    aliases = {}
    for p in load_project_registry():
        prefix = p.get("task_prefix", "").lower()
        name = p.get("name", "")
        if prefix and name and prefix != name:
            aliases[prefix] = name
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
    for p in load_project_registry():
        if p.get("name") == project_name:
            return p.get("task_prefix", project_name[:3]).lower()
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
    engine_type: str, project_prefix: str, name: str, config: dict | None = None, is_remote: bool = False
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


# --- iTerm2 Pre-Launch Setup ---

# Rolling tab colors (12 slots, one per session number mod 12)
_ITERM2_TAB_COLORS = [
    "e74c3c",
    "e67e22",
    "f0b429",
    "2ecc71",
    "1abc9c",
    "039be5",
    "1e88e5",
    "5e35b1",
    "d81b60",
    "00acc1",
    "ff5722",
    "7cb342",
]

# Icon color profile per tab color slot, chosen by contrast + complementary harmony:
#   ClaudeCode   = coral icon  → cool/dark backgrounds (sky blue, blue, purple, pink)
#   ClaudeCode-W = white icon  → warm/saturated backgrounds (red, orange, deep orange, cyan)
#   ClaudeCode-D = dark navy icon → bright/light backgrounds (yellow, green, teal, lime)
_ITERM2_PROFILE_MAP = [
    "ClaudeCode-W",  # 1: e74c3c  red         warm
    "ClaudeCode-W",  # 2: e67e22  orange       warm
    "ClaudeCode-D",  # 3: f0b429  yellow       bright
    "ClaudeCode-D",  # 4: 2ecc71  green        bright
    "ClaudeCode-D",  # 5: 1abc9c  teal         bright
    "ClaudeCode",  # 6: 039be5  sky blue     cool
    "ClaudeCode",  # 7: 1e88e5  blue         cool
    "ClaudeCode",  # 8: 5e35b1  purple       dark
    "ClaudeCode",  # 9: d81b60  pink         dark
    "ClaudeCode-W",  # 10: 00acc1 cyan         medium
    "ClaudeCode-W",  # 11: ff5722 deep orange  warm
    "ClaudeCode-D",  # 12: 7cb342 lime         bright
]


def _iterm2_state_dir() -> Path:
    """Return the XDG state dir for iTerm2 session-tracking files, creating it if needed."""
    d = get_xdg_state_home() / "iterm2"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _iterm2_pane_ids(iterm_session_id: str) -> tuple[str, str, str]:
    """Extract (tab_key, pane_idx, win_key) from ITERM_SESSION_ID.
    Format: "w{W}t{T}p{P}:UUID" -> tab_key="w{W}t{T}", pane_idx="{P}", win_key="w{W}"
    """
    if not iterm_session_id or "p" not in iterm_session_id:
        return "", "0", ""
    tab_key = iterm_session_id.split("p")[0]
    pane_idx = iterm_session_id.split("p", 1)[1].split(":")[0]
    win_key = iterm_session_id.split("t")[0]
    return tab_key, pane_idx, win_key


def _iterm2_register_session(tab_key: str, pane_idx: str, session_name: str, stype: str, status: str) -> None:
    """Write or update session entry in the XDG state iterm2 dir.
    Line format: {pane_idx}:{session_name}:{type}:{status}
    """
    path = _iterm2_state_dir() / f"cc-names-{tab_key}"
    new_line = f"{pane_idx}:{session_name}:{stype}:{status}"
    lines: list[str] = []
    try:
        with open(path) as f:
            lines = [l.rstrip("\n") for l in f if l.strip()]
    except OSError:
        pass
    updated = False
    new_lines = []
    for l in lines:
        if l.startswith(f"{pane_idx}:"):
            new_lines.append(new_line)
            updated = True
        else:
            new_lines.append(l)
    if not updated:
        new_lines.append(new_line)
    try:
        with open(path, "w") as f:
            f.write("\n".join(new_lines) + "\n")
    except OSError:
        pass


_ITERM2_TYPE_SYMBOLS: dict[str, str] = {"cc": "*", "gemini": "✦"}
_ITERM2_STATUS_SYMBOLS: dict[str, str] = {
    "running": "▶",
    "waiting": "⏸",
    "done": "✓",
    "error": "✗",
    "resuming": "↻",
    "init": "▶",
}


def _iterm2_compute_tab_title(tab_key: str) -> str:
    """Read names file and return abbreviated tab title string."""
    path = _iterm2_state_dir() / f"cc-names-{tab_key}"
    entries: list[list[str]] = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(":", 3)
                if len(parts) == 4:
                    entries.append(parts)  # [pane_idx, name, type, status]
    except OSError:
        return ""
    if not entries:
        return ""
    entries.sort(key=lambda e: int(e[0]) if e[0].isdigit() else 0)
    symbols = "".join(_ITERM2_TYPE_SYMBOLS.get(e[2], "·") for e in entries)
    if len(entries) == 1:
        e = entries[0]
        st = _ITERM2_STATUS_SYMBOLS.get(e[3], "▶")
        return f"{symbols} {st} {e[1]}"
    names = [e[1] for e in entries]
    prefix = os.path.commonprefix(names)
    if len(prefix) >= 4:
        parts = []
        for e in entries:
            suffix = e[1][len(prefix) :]
            st = _ITERM2_STATUS_SYMBOLS.get(e[3], "")
            parts.append(f"{st}{suffix}")
        return f"{symbols} {prefix}{{{'|'.join(parts)}}}"
    parts = []
    for e in entries:
        st = _ITERM2_STATUS_SYMBOLS.get(e[3], "")
        parts.append(f"{st}{e[1]}")
    return f"{symbols} {'  '.join(parts)}"


def _iterm2_heuristic_window_title(sessions: list[str]) -> str:
    """Fast heuristic window title from tmux session names."""
    if not sessions:
        return "CC Sessions"
    projects: set[str] = set()
    for s in sessions:
        m = re.match(r"^[cg](?:-r)?-(.+)-\d+$", s)
        if m:
            projects.add(m.group(1))
    project_str = "+".join(sorted(projects)) if projects else "CC"
    has_remote = any(re.match(r"^[cg]-r-", s) for s in sessions)
    has_local = any(re.match(r"^[cg]-(?!r-)", s) for s in sessions)
    if has_remote and not has_local:
        loc = " Remote"
    elif has_local and not has_remote:
        loc = " Local"
    else:
        loc = ""
    return f"{project_str}{loc} CC"


def _iterm2_update_window_title(win_key: str, tab_key: str, session_name: str, stype: str) -> None:
    """Update window registry and spawn async Claude Haiku for window title."""
    if not win_key:
        return
    win_file = _iterm2_state_dir() / f"win-{win_key}"
    title_file = _iterm2_state_dir() / f"win-title-{win_key}"
    lines: list[str] = []
    try:
        with open(win_file) as f:
            lines = [l.strip() for l in f if l.strip()]
    except OSError:
        pass
    new_line = f"{tab_key}:{session_name}:{stype}"
    new_lines: list[str] = []
    updated = False
    for l in lines:
        if l.startswith(f"{tab_key}:"):
            new_lines.append(new_line)
            updated = True
        else:
            new_lines.append(l)
    if not updated:
        new_lines.append(new_line)
    try:
        with open(win_file, "w") as f:
            f.write("\n".join(new_lines) + "\n")
    except OSError:
        return
    all_sessions = [l.split(":")[1] for l in new_lines if l.count(":") >= 2]
    heuristic = _iterm2_heuristic_window_title(all_sessions)
    sys.stdout.write(f"\033]2;{heuristic}\007")
    sys.stdout.flush()
    try:
        with open(title_file, "w") as f:
            f.write(heuristic)
    except OSError:
        pass


def _emit_iterm2_profile_setup(ai_name: str, engine: str, session: str = "") -> None:
    """Emit iTerm2 profile/color/title escape sequences directly to stdout.

    Called before os.execvp so sequences reach iTerm2 before tmux takes over.
    No DCS wrapping needed — we're not inside tmux yet at this point.
    """
    lc_term = os.environ.get("LC_TERMINAL", "")
    term_prog = os.environ.get("TERM_PROGRAM", "")
    if lc_term != "iTerm2" and term_prog != "iTerm.app":
        return

    iterm_session_id = os.environ.get("ITERM_SESSION_ID", "")
    tab_key, pane_idx, win_key = _iterm2_pane_ids(iterm_session_id)
    session_name = session or ai_name

    if engine == "c":
        m = re.search(r"\d+$", ai_name)
        num = int(m.group()) if m else 1
        idx = (num - 1) % len(_ITERM2_TAB_COLORS)
        color = _ITERM2_TAB_COLORS[idx]
        profile = _ITERM2_PROFILE_MAP[idx]
        sys.stdout.write(f"\033]1337;SetProfile={profile}\007")
        sys.stdout.write(f"\033]1337;SetColors=tab={color}\007")
        sys.stdout.flush()
        if tab_key:
            try:
                with open(_iterm2_state_dir() / f"cc-color-{tab_key}", "w") as f:
                    f.write(f"{color}:{profile}")
            except OSError:
                pass
            _iterm2_register_session(tab_key, pane_idx, session_name, "cc", "init")
            title = _iterm2_compute_tab_title(tab_key)
            if title:
                sys.stdout.write(f"\033]0; {title}\007")
                sys.stdout.flush()
        _iterm2_update_window_title(win_key, tab_key, session_name, "cc")

    elif engine == "g":
        sys.stdout.write("\033]1337;SetProfile=GeminiCLI\007")
        sys.stdout.flush()
        if tab_key:
            _iterm2_register_session(tab_key, pane_idx, session_name, "gemini", "init")
            title = _iterm2_compute_tab_title(tab_key)
            if title:
                sys.stdout.write(f"\033]0; {title}\007")
                sys.stdout.flush()
        _iterm2_update_window_title(win_key, tab_key, session_name, "gemini")


# --- Script Generation ---


def get_engine_script(
    engine: str,
    ai_name: str,
    session: str,
    prefix: str,
    project_prefix: str,
    session_id_uuid: str | None = None,
    sandbox: bool = True,
    worktree_dir: str | None = None,
    notify: bool = False,
    is_remote: bool = False,
    project_name: str = "",
) -> str:
    # Validate UUID before interpolating into bash script (defense-in-depth)
    if session_id_uuid and not re.fullmatch(r"[0-9a-f-]{36}", session_id_uuid):
        session_id_uuid = ""
    env_var_prefix = "CC" if engine == "c" else "GG"
    sandbox_flag = "-s" if sandbox else ""
    cd_cmd = f"cd {worktree_dir}" if worktree_dir else ":"
    notify_cmd = 'ai internal notify "$tmux_session" "Agent Finished Task" 2>/dev/null || true' if notify else "true"
    try:
        from importlib.metadata import version as _pkg_version

        _template_version = _pkg_version("ai-cli-utils")
    except Exception:
        _template_version = "unknown"

    script = f"""
    {cd_cmd}
    first_run=true
    ai_name="{ai_name}"
    engine="{engine}"
    tmux_session="{session}"
    _template_version="{_template_version}"
    uuid="{session_id_uuid or ""}"
    project_prefix="{project_prefix}"
    project_name="{project_name}"
    _ai_state_dir="$HOME/.local/state/ai-cli-utils"
    mkdir -p "$_ai_state_dir/iterm2"

    # --dangerously-skip-permissions is blocked when running as root
    if [[ $(id -u) -eq 0 ]]; then
      claude_perms_flag=""
    else
      claude_perms_flag="--dangerously-skip-permissions"
    fi

    if [[ "$engine" == "c" ]]; then
      signal_file="$_ai_state_dir/cc-exit-$tmux_session"
      prompt_file="$_ai_state_dir/cc-resume-prompt-$tmux_session"
    else
      signal_file="$_ai_state_dir/gg-exit-$tmux_session"
      reload_file="$_ai_state_dir/gg-reload-$tmux_session"
      restart_file="$_ai_state_dir/gg-restart-$tmux_session"
      prompt_file="$_ai_state_dir/gg-resume-prompt-$tmux_session"
    fi
    lock_file="$_ai_state_dir/ai-watcher-lock-$tmux_session"
    handoff_pending_file="$_ai_state_dir/handoff-pending-$tmux_session"

    export AI_TMUX_SESSION="$tmux_session"
    export {env_var_prefix}_TMUX_SESSION="$tmux_session"
    watcher_pid=""
    signal_watch_pid=""

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

    # Auto-start signal-watch for handoff auto-pickup (only for cc engine)
    if [[ "$engine" == "c" && -n "$project_name" ]]; then
      ai signal-watch start "$project_name" "$tmux_session" &>/dev/null
      signal_watch_pid=""
    fi

    # iTerm2 fleet management: set profile, rolling tab color, tab title
    # Only runs under iTerm2 (check LC_TERMINAL which survives tmux, unlike TERM_PROGRAM)
    # _it2: wraps OSC sequences in DCS passthrough when inside tmux
    _it2() {{
      if [[ -n "$TMUX" ]]; then
        printf '\\033Ptmux;\\033%b\\033\\\\' "$1"
      else
        printf '%b' "$1"
      fi
    }}

    _iterm2_fleet_setup() {{
      [[ "$LC_TERMINAL" != "iTerm2" && "$TERM_PROGRAM" != "iTerm.app" ]] && return 0
      local num="$1" stype="$2" sname="$3"
      local _tab_key="" _pane_idx="0"
      if [[ -n "$ITERM_SESSION_ID" ]]; then
        _tab_key="${{ITERM_SESSION_ID%%p*}}"
        local _pp="${{ITERM_SESSION_ID#*p}}"; _pane_idx="${{_pp%%:*}}"
      fi

      case "$stype" in
        cc)
          # Rolling tab colors + icon profile chosen by contrast/complementary harmony:
          #   ClaudeCode   = coral icon  → cool/dark (sky blue, blue, purple, pink)
          #   ClaudeCode-W = white icon  → warm/saturated (red, orange, deep orange, cyan)
          #   ClaudeCode-D = dark icon   → bright/light (yellow, green, teal, lime)
          local colors=("e74c3c" "e67e22" "f0b429" "2ecc71" "1abc9c"
                        "039be5" "1e88e5" "5e35b1" "d81b60" "00acc1"
                        "ff5722" "7cb342")
          local profiles=("ClaudeCode-W" "ClaudeCode-W" "ClaudeCode-D" "ClaudeCode-D" "ClaudeCode-D"
                          "ClaudeCode" "ClaudeCode" "ClaudeCode" "ClaudeCode" "ClaudeCode-W"
                          "ClaudeCode-W" "ClaudeCode-D")
          local idx=$(( (num - 1) % ${{#colors[@]}} ))
          _it2 "\\033]1337;SetProfile=${{profiles[$idx]}}\\007"
          _it2 "\\033]1337;SetColors=tab=${{colors[$idx]}}\\007"
          if [[ -n "$_tab_key" ]]; then
            printf '%s' "${{colors[$idx]}}:${{profiles[$idx]}}" > "$_ai_state_dir/iterm2/cc-color-${{_tab_key}}"
          fi
          ;;
        gemini)
          _it2 '\\033]1337;SetProfile=GeminiCLI\\007'
          ;;
        shell)
          _it2 '\\033]1337;SetProfile=ShellUtility\\007'
          ;;
        *)
          return 0
          ;;
      esac

      # Update names file status and emit abbreviated tab title
      if [[ -n "$_tab_key" && ("$stype" == "cc" || "$stype" == "gemini") ]]; then
        local _title
        _title=$(ai internal iterm2-update-status "$_tab_key" "$_pane_idx" "$sname" "$stype" "running" 2>/dev/null)
        [[ -n "$_title" ]] && _it2 "\\033]0; $_title\\007"
      fi
    }}

    # iTerm2 status updates: update names file + re-emit abbreviated tab title
    _iterm2_status() {{
      [[ "$LC_TERMINAL" != "iTerm2" && "$TERM_PROGRAM" != "iTerm.app" ]] && return 0
      local status="$1" num="$2" stype="$3" sname="${{4:-}}"
      [[ -z "$ITERM_SESSION_ID" ]] && return 0
      local _tab_key="${{ITERM_SESSION_ID%%p*}}"
      local _pp="${{ITERM_SESSION_ID#*p}}"; local _pane_idx="${{_pp%%:*}}"
      local _title
      _title=$(ai internal iterm2-update-status "$_tab_key" "$_pane_idx" "$sname" "$stype" "$status" 2>/dev/null)
      [[ -n "$_title" ]] && _it2 "\\033]0; $_title\\007"
    }}

    # iTerm2 cleanup on exit: deregister pane, re-emit remaining title if tab still has sessions
    _iterm2_exit_cleanup() {{
      [[ -z "$ITERM_SESSION_ID" ]] && return 0
      local _tab_key="${{ITERM_SESSION_ID%%p*}}"
      local _pp="${{ITERM_SESSION_ID#*p}}"; local _pane_idx="${{_pp%%:*}}"
      local _win_key="${{ITERM_SESSION_ID%%t*}}"
      local _remaining_title
      _remaining_title=$(ai internal iterm2-exit-cleanup "$_tab_key" "$_pane_idx" "$_win_key" 2>/dev/null)
      [[ -n "$_remaining_title" ]] && _it2 "\\033]0; $_remaining_title\\007"
    }}

    # Extract session number from ai_name (e.g., "sw-3" → "3")
    _session_num=$(echo "$ai_name" | grep -oE '[0-9]+$' || echo "1")
    _session_type="cc"
    [[ "$engine" == "g" ]] && _session_type="gemini"
    _iterm2_fleet_setup "$_session_num" "$_session_type" "$tmux_session"

    # Export for CC Notification hook to use
    export ITERM2_SESSION_NUM="$_session_num"
    export ITERM2_SESSION_TYPE="$_session_type"

    _iterm2_color_file=""
    if [[ -n "$ITERM_SESSION_ID" ]]; then
      _iterm2_color_file="$_ai_state_dir/iterm2/cc-color-${{ITERM_SESSION_ID%%p*}}"
    fi
    trap 'kill "$watcher_pid" 2>/dev/null; ai signal-watch stop "$tmux_session" &>/dev/null; rm -f "$lock_file" "$_iterm2_color_file" "$_ai_state_dir/handoff-caught-$tmux_session"; _iterm2_exit_cleanup; ai internal cleanup-worktree "$ai_name" 2>/dev/null' EXIT

    while true; do
      start_watcher
      start_ts=$(date +%s)
      # Re-emit iTerm2 setup + set status to running
      _iterm2_fleet_setup "$_session_num" "$_session_type" "$tmux_session"
      _iterm2_status "running" "$_session_num" "$_session_type" "$tmux_session"
      (ai internal publish-event "$tmux_session" "START" 2>/dev/null || true) &
      (ai internal publish-session-event "$tmux_session" "started" 2>/dev/null || true) &

      if [[ -f "scripts/session-broker.py" ]] && $first_run; then
        python3 scripts/session-broker.py --engine "$engine" 2>/dev/null || true
      fi

      # On first run: synchronously drain local queue + NATS before launching CC.
      # Writes prompt_file if a pending handoff exists (local or cross-machine via NATS).
      # CC then launches with --continue on the task — zero user input required.
      if $first_run && [[ "$engine" == "c" && -n "$project_name" && ! -f "$prompt_file" ]]; then
        ai internal handoff-drain "$project_name" "$tmux_session" 2>/dev/null || true
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
        _iterm2_status "error" "$_session_num" "$_session_type" "$tmux_session"
        (ai internal publish-session-event "$tmux_session" "error" 2>/dev/null || true) &
      else
        _iterm2_status "done" "$_session_num" "$_session_type" "$tmux_session"
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
        echo "AI CLI exited too quickly ($elapsed s) — stopping. Run 'ai c' to retry."
        break
      fi
      _iterm2_status "resuming" "$_session_num" "$_session_type" "$tmux_session"
      if [[ -f "$handoff_pending_file" ]]; then
        pending_msg=$(cat "$handoff_pending_file")
        rm -f "$handoff_pending_file"
        echo "$pending_msg" > "$prompt_file"
        printf '{{"event":"handoff.while_loop_pickup","session":"%s","ts":%s}}\n' \
          "$tmux_session" "$(date +%s)" >> "$_ai_state_dir/handoff-events.jsonl" 2>/dev/null || true
      fi
      # Self-update: if ai-cli was reinstalled, regenerate template and restart
      _current_ver=$(ai internal get-version 2>/dev/null || echo "unknown")
      if [[ "$_current_ver" != "unknown" && "$_current_ver" != "$_template_version" ]]; then
        echo "ai-cli updated ($_template_version → $_current_ver) — restarting session..."
        exec ai "$engine" "$ai_name"
      fi
      echo "Resuming... (Ctrl-C to exit)"
      sleep 0.5 || break
    done
    (ai internal publish-event "$tmux_session" "STOP" 2>/dev/null || true) &
    (ai internal publish-session-event "$tmux_session" "stopped" 2>/dev/null || true) &
    {('echo "Session ended. Exit shell to close tmux session."; exec $SHELL') if is_remote else "exit 0"}
    """
    return script


# --- Subcommands ---


def _log_handoff_event(event_type: str, **fields) -> None:
    """Append a JSON event to handoff-events.jsonl for observability."""
    log_path = get_xdg_state_home() / "handoff-events.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"event": event_type, "ts": time.time(), **fields}
    try:
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def post_handoff(title, priority, project, message, for_machine=None):
    if for_machine is None:
        for_machine = os.environ.get("AI_CLI_HOST", "")
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


def check_handoff():
    handoff_dir = _get_handoff_queue_dir()
    if handoff_dir is None:
        return
    best_file = _find_best_handoff(handoff_dir / "pending")
    if best_file:
        print(best_file)


def check_handoff_project(project_name: str):
    """Like check_handoff but filtered to a specific project directory name."""
    handoff_dir = _get_handoff_queue_dir()
    if handoff_dir is None:
        return
    best_file = _find_best_handoff(handoff_dir / "pending", project_filter=project_name)
    if best_file:
        print(best_file)


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
        ["uv", "tool", "upgrade", "ai-cli-utils"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


# --- SSH tunnel management (autossh-backed) ---


def _cmd_tunnel_start(local_port: int, remote_port: int, *, forward: bool = False, config: dict) -> None:
    autossh_bin = shutil.which("autossh")
    if not autossh_bin:
        print(
            "autossh not found. Install it first:\n  macOS:  brew install autossh\n  Linux:  apt install autossh",
            file=sys.stderr,
        )
        sys.exit(1)

    remote_cfg = config.get("remote", {})
    host = remote_cfg.get("host", "")
    user = remote_cfg.get("user", "ubuntu")
    if not host:
        print("Error: [remote] host not set in ~/.config/ai-cli-utils/config.toml", file=sys.stderr)
        sys.exit(1)

    direction = "-L" if forward else "-R"
    cmd = [
        autossh_bin,
        "-M",
        "0",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "ExitOnForwardFailure=yes",
        "-N",
        direction,
        f"{remote_port}:localhost:{local_port}",
        f"{user}@{host}",
    ]
    proc = subprocess.Popen(cmd, start_new_session=True)
    state_dir = get_xdg_state_home()
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"tunnel-{local_port}.pid").write_text(str(proc.pid))
    print(f"Tunnel started: localhost:{local_port} -> {host}:{remote_port} (PID {proc.pid})")


def _cmd_tunnel_stop(local_port: int) -> None:
    state_dir = get_xdg_state_home()
    pid_file = state_dir / f"tunnel-{local_port}.pid"
    if not pid_file.exists():
        return
    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, 15)  # SIGTERM
    except ProcessLookupError:
        pass
    pid_file.unlink(missing_ok=True)
    print(f"Tunnel stopped: port {local_port}")


def _cmd_tunnel_status() -> None:
    state_dir = get_xdg_state_home()
    pid_files = sorted(state_dir.glob("tunnel-*.pid"))
    if not pid_files:
        print("No tunnels registered.")
        return
    for pid_file in pid_files:
        port = pid_file.stem[len("tunnel-") :]
        pid = int(pid_file.read_text().strip())
        try:
            os.kill(pid, 0)
            status = "alive"
        except ProcessLookupError:
            status = "dead"
            pid_file.unlink(missing_ok=True)
        print(f"port {port}: PID {pid} ({status})")


# --- Circus / signal-watch process management ---


def _ensure_circusd() -> str:
    """Start circusd if not already running. Returns the endpoint URI."""
    import shutil as _shutil
    import time as _time

    state_dir = get_xdg_state_home()
    state_dir.mkdir(parents=True, exist_ok=True)
    endpoint = f"ipc://{state_dir}/circus.endpoint"

    # Try existing daemon first
    try:
        from circus.client import CircusClient

        CircusClient(endpoint, timeout=1.0).send_message("status")
        return endpoint
    except Exception:
        pass

    # Write circus.ini
    ini_path = state_dir / "circus.ini"
    ini_path.write_text(
        f"[circus]\n"
        f"endpoint        = {endpoint}\n"
        f"pubsub_endpoint = ipc://{state_dir}/circus.pubsub\n"
        f"logoutput       = {state_dir}/circus.log\n"
        f"umask           = 0o022\n"
    )

    circusd_bin = _shutil.which("circusd") or str(Path.home() / ".local" / "bin" / "circusd")
    pidfile = str(state_dir / "circusd.pid")
    subprocess.Popen(
        [circusd_bin, "--daemon", "--pidfile", pidfile, str(ini_path)],
    )

    # Poll until ready
    from circus.client import CircusClient

    for _ in range(10):
        _time.sleep(0.3)
        try:
            CircusClient(endpoint, timeout=1.0).send_message("status")
            return endpoint
        except Exception:
            pass

    raise RuntimeError("circusd did not start in time")


def _cmd_signal_watch_start(project: str, session: str) -> None:
    endpoint = _ensure_circusd()
    from circus.client import CircusClient

    client = CircusClient(endpoint, timeout=5.0)
    watcher_name = f"sw-{session}"
    ai_bin = shutil.which("ai") or "ai"
    cmd = f"{ai_bin} internal signal-watch {project} {session}"

    # Remove existing watcher idempotently
    try:
        client.send_message("rm", name=watcher_name)
    except Exception:
        pass

    client.send_message(
        "add",
        name=watcher_name,
        cmd=cmd,
        options={
            "copy_env": True,
            "respawn": False,
            "singleton": True,
            "autostart": True,
        },
        start=True,
    )


def _cmd_signal_watch_stop(session: str) -> None:
    state_dir = get_xdg_state_home()
    endpoint = f"ipc://{state_dir}/circus.endpoint"
    try:
        from circus.client import CircusClient

        CircusClient(endpoint, timeout=2.0).send_message("rm", name=f"sw-{session}")
    except Exception:
        pass


def _cmd_signal_watch_status() -> None:
    state_dir = get_xdg_state_home()
    endpoint = f"ipc://{state_dir}/circus.endpoint"
    try:
        from circus.client import CircusClient

        result = CircusClient(endpoint, timeout=2.0).send_message("status")
        statuses = result.get("statuses", {}) if isinstance(result, dict) else {}
        sw_watchers = {k: v for k, v in statuses.items() if k.startswith("sw-")}
        if not sw_watchers:
            print("No signal-watch processes running.")
            return
        for name, status in sorted(sw_watchers.items()):
            session = name[len("sw-") :]
            print(f"{session}: {status}")
    except Exception:
        print("circusd not running.")


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
        elif action == "get-version":
            try:
                from importlib.metadata import version as _pkg_version

                print(_pkg_version("ai-cli-utils"))
            except Exception:
                print("unknown")
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
        elif action == "signal-watch":
            # ai internal signal-watch <project> <session_id>
            if len(sys.argv) < 5:
                print("Usage: ai internal signal-watch <project> <session_id>", file=sys.stderr)
                sys.exit(1)
            import asyncio
            from .messaging import NATSClient

            sw_project = sys.argv[3]
            sw_session_id = sys.argv[4]
            sw_handoff_dir = _get_handoff_queue_dir()
            sw_pending_file = get_xdg_state_home() / f"handoff-pending-{sw_session_id}"
            nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
            sw_client = NATSClient(servers=nats_servers)

            async def _on_handoff(data):
                handoff_id = data.get("id")
                title = data.get("title", "")
                priority = data.get("priority", "")
                message = data.get("message", "")
                for_machine = data.get("for_machine", "")
                if for_machine and for_machine != os.environ.get("AI_CLI_HOST", ""):
                    return  # not intended for this machine
                print(f"\n[HANDOFF] {priority} #{handoff_id}: {title}", flush=True)
                if sw_handoff_dir is None or not handoff_id:
                    return
                # Cross-machine delivery: if file doesn't exist locally but payload carries content, write it
                content = data.get("content")
                filename = data.get("filename")
                if content and filename:
                    pending_dir = sw_handoff_dir / "pending"
                    local_file = pending_dir / filename
                    if not local_file.exists():
                        pending_dir.mkdir(parents=True, exist_ok=True)
                        try:
                            local_file.write_text(content)
                        except OSError:
                            pass
                claimed = _claim_handoff_for_signal(sw_handoff_dir, int(handoff_id), sw_session_id)
                if claimed is None:
                    return  # another session claimed it first
                _log_handoff_event(
                    "handoff.claimed",
                    handoff_id=handoff_id,
                    session=sw_session_id,
                    layer="nats_realtime" if data.get("_source") != "startup_scan" else "startup_scan",
                )
                resume_msg = f"Auto-pickup: {priority} handoff #{handoff_id} — {title}. File: {claimed}\n\n{message}"
                sw_pending_file.parent.mkdir(parents=True, exist_ok=True)
                sw_pending_file.write_text(resume_msg)
                # Only nudge via send-keys for real-time NATS delivery.
                # Startup scan fires before CC is ready — sending keys at that point
                # causes multiple text injections without submission.
                if data.get("_source") != "startup_scan":
                    pane_state = "unknown"
                    try:
                        result = subprocess.run(
                            ["tmux", "display-message", "-t", sw_session_id, "-p", "#{pane_current_command}"],
                            capture_output=True,
                            text=True,
                            timeout=2,
                        )
                        if result.returncode == 0:
                            pane_state = result.stdout.strip()
                            if pane_state not in ("claude",):
                                # Pane is idle — send actionable message via send-keys
                                nudge_msg = (
                                    f"Pick up handoff task #{handoff_id}: {title}. "
                                    f"Run: ai handoff check && ai handoff claim $(ai handoff check)"
                                )
                                subprocess.run(
                                    ["tmux", "send-keys", "-t", sw_session_id, nudge_msg, "Enter"],
                                    timeout=2,
                                    check=False,
                                )
                                _log_handoff_event(
                                    "handoff.nudge_sent",
                                    handoff_id=handoff_id,
                                    session=sw_session_id,
                                    pane_state=pane_state,
                                )
                    except Exception:
                        pass

            # Startup scan: pick up any unclaimed files already in the pending queue
            if sw_handoff_dir is not None:
                pending_dir = sw_handoff_dir / "pending"
                if pending_dir.exists():
                    for f in sorted(pending_dir.glob("*.md")):
                        try:
                            fid = int(f.name.split("-")[0])
                        except ValueError:
                            continue
                        try:
                            raw = f.read_text()
                            fm_title = re.search(r'^title:\s*"?([^"\n]+)"?', raw, re.MULTILINE)
                            fm_priority = re.search(r"^priority:\s*(\S+)", raw, re.MULTILINE)
                            fm_for_machine = re.search(r"^for_machine:\s*(\S+)", raw, re.MULTILINE)
                            body = raw.split("---", 2)[-1].strip() if raw.count("---") >= 2 else ""
                            scan_title = fm_title.group(1).strip() if fm_title else f.stem
                            scan_priority = fm_priority.group(1) if fm_priority else ""
                            scan_for_machine = fm_for_machine.group(1) if fm_for_machine else ""
                        except OSError:
                            scan_title, scan_priority, body, scan_for_machine = f.stem, "", "", ""
                        asyncio.run(
                            _on_handoff(
                                {
                                    "id": fid,
                                    "title": scan_title,
                                    "priority": scan_priority,
                                    "message": body,
                                    "for_machine": scan_for_machine,
                                    "_source": "startup_scan",
                                }
                            )
                        )

            consumer_name = f"{sw_session_id}-signal-watcher"
            try:
                asyncio.run(sw_client.subscribe_durable(f"handoff.{sw_project}", consumer_name, _on_handoff))
            except Exception:
                pass
            sys.exit(0)
        elif action == "handoff-drain":
            # ai internal handoff-drain <project> <session_id>
            # Synchronous: drain pending NATS messages + local file scan, then exit.
            # Called BEFORE CC launches so prompt_file is ready on first invocation.
            if len(sys.argv) < 5:
                sys.exit(0)
            import asyncio
            from .messaging import NATSClient

            hd_project = sys.argv[3]
            hd_session = sys.argv[4]
            hd_handoff_dir = _get_handoff_queue_dir()
            hd_prompt_file = get_xdg_state_home() / f"cc-resume-prompt-{hd_session}"
            nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
            hd_client = NATSClient(servers=nats_servers)

            def _write_pending_if_claimed(data):
                handoff_id = data.get("id")
                title = data.get("title", "")
                priority = data.get("priority", "")
                message = data.get("message", "")
                for_machine = data.get("for_machine", "")
                if for_machine and for_machine != os.environ.get("AI_CLI_HOST", ""):
                    return False  # not intended for this machine
                if hd_handoff_dir is None or not handoff_id:
                    return False
                # Cross-machine: write file locally from payload if missing
                content = data.get("content")
                filename = data.get("filename")
                if content and filename:
                    pending_dir = hd_handoff_dir / "pending"
                    local_file = pending_dir / filename
                    if not local_file.exists():
                        pending_dir.mkdir(parents=True, exist_ok=True)
                        try:
                            local_file.write_text(content)
                        except OSError:
                            return False
                claimed = _claim_handoff_for_signal(hd_handoff_dir, int(handoff_id), hd_session)
                if claimed is None:
                    return False
                _log_handoff_event(
                    "handoff.claimed",
                    handoff_id=handoff_id,
                    session=hd_session,
                    layer="pre_launch_drain",
                )
                resume_msg = f"Auto-pickup: {priority} handoff #{handoff_id} — {title}. File: {claimed}\n\n{message}"
                hd_prompt_file.parent.mkdir(parents=True, exist_ok=True)
                hd_prompt_file.write_text(resume_msg)
                return True

            # 1. Local file scan first (fast, no network)
            if hd_handoff_dir is not None:
                pending_dir = hd_handoff_dir / "pending"
                if pending_dir.exists():
                    best = _find_best_handoff(pending_dir, project_filter=hd_project)
                    if best is not None:
                        try:
                            fid = int(best.name.split("-")[0])
                            raw = best.read_text()
                            fm_title = re.search(r'^title:\s*"?([^"\n]+)"?', raw, re.MULTILINE)
                            fm_priority = re.search(r"^priority:\s*(\S+)", raw, re.MULTILINE)
                            fm_for_machine = re.search(r"^for_machine:\s*(\S+)", raw, re.MULTILINE)
                            body = raw.split("---", 2)[-1].strip() if raw.count("---") >= 2 else ""
                            _write_pending_if_claimed(
                                {
                                    "id": fid,
                                    "title": fm_title.group(1).strip() if fm_title else best.stem,
                                    "priority": fm_priority.group(1) if fm_priority else "",
                                    "message": body,
                                    "for_machine": fm_for_machine.group(1) if fm_for_machine else "",
                                }
                            )
                        except Exception:
                            pass

            # 2. NATS drain: pull pending JetStream messages (non-blocking, 2s timeout)
            if not hd_prompt_file.exists():

                async def _drain():
                    await hd_client.connect()
                    if not hd_client.js:
                        return
                    consumer_name = f"{hd_session}-pre-launch"
                    subject = f"handoff.{hd_project}"
                    try:
                        await hd_client._ensure_stream(subject)
                        sub = await hd_client.js.pull_subscribe(subject, durable=consumer_name)
                        while True:
                            try:
                                msgs = await sub.fetch(1, timeout=2)
                                for msg in msgs:
                                    try:
                                        data = json.loads(msg.data.decode())
                                    except Exception:
                                        data = {}
                                    await msg.ack()
                                    if _write_pending_if_claimed(data):
                                        return
                            except Exception:
                                break
                    except Exception:
                        pass
                    finally:
                        await hd_client.close()

                try:
                    asyncio.run(_drain())
                except Exception:
                    pass

            sys.exit(0)
        elif action == "iterm2-tab-title":
            # ai internal iterm2-tab-title <tab_key>
            if len(sys.argv) >= 4:
                title = _iterm2_compute_tab_title(sys.argv[3])
                if title:
                    print(title)
            sys.exit(0)
        elif action == "iterm2-update-status":
            # ai internal iterm2-update-status <tab_key> <pane_idx> <name> <type> <status>
            if len(sys.argv) >= 8:
                tab_key, pane_idx, name, stype, status = sys.argv[3:8]
                _iterm2_register_session(tab_key, pane_idx, name, stype, status)
                title = _iterm2_compute_tab_title(tab_key)
                if title:
                    print(title)
            sys.exit(0)
        elif action == "iterm2-exit-cleanup":
            # ai internal iterm2-exit-cleanup <tab_key> <pane_idx> <win_key>
            if len(sys.argv) >= 6:
                tab_key, pane_idx, win_key = sys.argv[3], sys.argv[4], sys.argv[5]
                names_file = _iterm2_state_dir() / f"cc-names-{tab_key}"
                # Remove this pane's entry
                try:
                    with open(names_file) as f:
                        lines = [l.rstrip("\n") for l in f if l.strip()]
                    remaining = [l for l in lines if not l.startswith(f"{pane_idx}:")]
                    if remaining:
                        with open(names_file, "w") as f:
                            f.write("\n".join(remaining) + "\n")
                        title = _iterm2_compute_tab_title(tab_key)
                        if title:
                            print(title)
                    else:
                        os.unlink(names_file)
                except OSError:
                    pass
                # Remove tab from window registry
                win_file = _iterm2_state_dir() / f"win-{win_key}"
                try:
                    with open(win_file) as f:
                        wlines = [l.strip() for l in f if l.strip()]
                    wlines = [l for l in wlines if not l.startswith(f"{tab_key}:")]
                    with open(win_file, "w") as f:
                        f.write("\n".join(wlines) + "\n")
                except OSError:
                    pass
            sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "upgrade":
        print("Upgrading ai-cli-utils...", file=sys.stderr)
        os.execvp("uv", ["uv", "tool", "upgrade", "ai-cli-utils"])

    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        from .setup import run_setup

        sys.exit(run_setup())

    if len(sys.argv) > 1 and sys.argv[1] == "handoff":
        if len(sys.argv) == 2:
            print("Usage: ai handoff [post|check|claim|complete]")
            sys.exit(1)
        action = sys.argv[2]
        if action == "post":
            post_args = sys.argv[3:]
            if "--remote" in post_args or "-R" in post_args:
                post_args = [a for a in post_args if a not in ("--remote", "-R")]
                remote_cfg = load_config().get("remote", {})
                remote_host = remote_cfg.get("host", "")
                remote_user = remote_cfg.get("user", "ubuntu")
                if not remote_host:
                    print("Error: [remote] host not set in config", file=sys.stderr)
                    sys.exit(1)
                os.execvp("ssh", ["ssh", f"{remote_user}@{remote_host}", "ai", "handoff", "post"] + post_args)
            for_machine = None
            for flag in ("--for-machine", "-m"):
                if flag in post_args:
                    idx = post_args.index(flag)
                    for_machine = post_args[idx + 1]
                    post_args = post_args[:idx] + post_args[idx + 2 :]
                    break
            post_handoff(post_args[0], post_args[1], post_args[2], post_args[3], for_machine=for_machine)
        elif action == "check":
            check_handoff()
        elif action == "check-project":
            if len(sys.argv) < 4:
                print("Usage: ai handoff check-project <project_name>", file=sys.stderr)
                sys.exit(1)
            check_handoff_project(sys.argv[3])
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

    if len(sys.argv) > 1 and sys.argv[1] == "gemini":
        from .gemini import gemini_cli

        gemini_cli(sys.argv[2:])
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "tunnel":
        if len(sys.argv) < 3:
            print(
                "Usage: ai tunnel [start <local-port> [remote-port] [-L|--forward] | stop <port> | status]",
                file=sys.stderr,
            )
            sys.exit(1)
        tn_action = sys.argv[2]
        if tn_action == "start":
            if len(sys.argv) < 4:
                print("Usage: ai tunnel start <local-port> [remote-port] [-L|--forward]", file=sys.stderr)
                sys.exit(1)
            local_port = int(sys.argv[3])
            args_rest = sys.argv[4:]
            forward = "--forward" in args_rest or "-L" in args_rest
            non_flag = [a for a in args_rest if not a.startswith("--")]
            remote_port = int(non_flag[0]) if non_flag else local_port
            _cmd_tunnel_start(local_port, remote_port, forward=forward, config=config)
            sys.exit(0)
        elif tn_action == "stop":
            if len(sys.argv) < 4:
                print("Usage: ai tunnel stop <port>", file=sys.stderr)
                sys.exit(1)
            _cmd_tunnel_stop(int(sys.argv[3]))
            sys.exit(0)
        elif tn_action == "status":
            _cmd_tunnel_status()
            sys.exit(0)
        else:
            print(f"Unknown tunnel action: {tn_action}. Use start, stop, or status.", file=sys.stderr)
            sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "signal-watch":
        if len(sys.argv) < 3:
            print("Usage: ai signal-watch [start <project> <session> | stop <session> | status]", file=sys.stderr)
            sys.exit(1)
        sw_action = sys.argv[2]
        if sw_action == "start":
            if len(sys.argv) < 5:
                print("Usage: ai signal-watch start <project> <session>", file=sys.stderr)
                sys.exit(1)
            _cmd_signal_watch_start(sys.argv[3], sys.argv[4])
            sys.exit(0)
        elif sw_action == "stop":
            if len(sys.argv) < 4:
                print("Usage: ai signal-watch stop <session>", file=sys.stderr)
                sys.exit(1)
            _cmd_signal_watch_stop(sys.argv[3])
            sys.exit(0)
        elif sw_action == "status":
            _cmd_signal_watch_status()
            sys.exit(0)
        else:
            print(f"Unknown signal-watch action: {sw_action}. Use start, stop, or status.", file=sys.stderr)
            sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "sync":
        if len(sys.argv) == 2:
            print(
                "Usage: ai sync [push|pull|conflicts|watch] [-m|--memories-only] [-n|--dry-run] [-v|--verbose] [-f|--force]"
            )
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
            print("Error: [remote] host not set in ~/.config/ai-cli-utils/config.toml", file=sys.stderr)
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

    if len(sys.argv) > 1 and sys.argv[1] in ("update", "deploy"):
        force_reinstall = "--force" in sys.argv or "-f" in sys.argv
        cfg_deploy = config.get("deploy", {})
        project_path_str = cfg_deploy.get("project_path", "")
        project_path = Path(project_path_str).expanduser() if project_path_str else Path.cwd()
        pyproject = project_path / "pyproject.toml"
        if not pyproject.exists():
            print(
                "Error: pyproject.toml not found. Run from project directory or set [deploy] project_path in config.",
                file=sys.stderr,
            )
            sys.exit(1)
        original = pyproject.read_text()
        m = re.search(r'^(version\s*=\s*")([^"]+)(")', original, re.MULTILINE)
        if not m:
            print("Error: could not find version in pyproject.toml", file=sys.stderr)
            sys.exit(1)
        base = re.sub(r"\.post\d+$", "", m.group(2))
        new_version = f"{base}.post{int(time.strftime('%Y%m%d%H%M%S'))}"
        is_mac = os.environ.get("AI_CLI_HOST") == "mac"
        if is_mac:
            print("Pulling latest from origin...")
            subprocess.run(["git", "pull", "--rebase"], cwd=project_path, check=False)
        print(f"Updating {m.group(2)} → {new_version}")
        exit_code = 0
        try:
            pyproject.write_text(original[: m.start(2)] + new_version + original[m.end(2) :])
            uv_cmd = ["uv", "tool", "install", str(project_path), "--force"]
            if force_reinstall:
                uv_cmd.append("--reinstall")
            result = subprocess.run(uv_cmd, cwd=project_path)
            exit_code = result.returncode
        finally:
            pyproject.write_text(original)
        if exit_code == 0:
            # Install into any configured extra venvs (e.g. tool venvs that depend on ai-cli-utils)
            extra_venvs = config.get("update", {}).get("extra_venvs", [])
            for venv_path_str in extra_venvs:
                venv_path = Path(venv_path_str).expanduser()
                if venv_path.exists():
                    pip_cmd = ["uv", "pip", "install", str(project_path)]
                    if force_reinstall:
                        pip_cmd.append("--force-reinstall")
                    subprocess.run(
                        pip_cmd,
                        env={**os.environ, "VIRTUAL_ENV": str(venv_path)},
                        check=False,
                    )
            # Clear pycache
            subprocess.run(
                ["find", str(project_path), "-name", "__pycache__", "-exec", "rm", "-rf", "{}", "+"],
                check=False,
            )
        sys.exit(exit_code)

    if len(sys.argv) > 1 and sys.argv[1] == "attach":
        if len(sys.argv) < 3:
            print("Usage: ai attach <session-name>", file=sys.stderr)
            sys.exit(1)
        session_name = sys.argv[2]
        check = subprocess.run(["tmux", "has-session", "-t", session_name], capture_output=True)
        if check.returncode != 0:
            print(f"No tmux session named '{session_name}'", file=sys.stderr)
            sys.exit(1)
        os.execvp("tmux", ["tmux", "attach-session", "-t", session_name])

    if len(sys.argv) > 1 and sys.argv[1] == "ls":
        show_all = "--all" in sys.argv or "-a" in sys.argv

        res = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name} #{session_activity}"],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            print("No tmux sessions found (is tmux running?)", file=sys.stderr)
            sys.exit(0)

        now = int(time.time())
        sessions = []
        for line in res.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split()
            name = parts[0]
            try:
                activity = int(parts[1]) if len(parts) > 1 else 0
            except ValueError:
                activity = 0
            if not show_all and not _AI_SESSION_RE.match(name):
                continue
            sessions.append((name, activity))

        if not sessions:
            msg = (
                "No tmux sessions found."
                if show_all
                else "No ai-cli sessions found. Use --all to show all tmux sessions."
            )
            print(msg)
            sys.exit(0)

        sessions.sort(key=lambda x: x[1], reverse=True)

        def _human_age(ts: int) -> str:
            delta = now - ts
            if delta < 60:
                return f"{delta}s"
            if delta < 3600:
                return f"{delta // 60}m"
            if delta < 86400:
                return f"{delta // 3600}h"
            return f"{delta // 86400}d"

        def _project_from_session(name: str) -> str:
            """Extract project prefix from session name: c-sw-1 → sw, c-r-sw-1 → sw."""
            parts = name.split("-")
            # Format: {c|g}[-r]-{project}-{index}
            if len(parts) >= 3 and parts[0] in ("c", "g"):
                start = 2 if parts[1] == "r" else 1
                # project is everything between start and last segment
                return "-".join(parts[start:-1]) if len(parts) > start + 1 else parts[start]
            return name

        fzf = shutil.which("fzf")
        if fzf is None:
            # Try to install fzf
            apt = shutil.which("apt")
            if apt:
                print("fzf not found — installing with apt...")
                subprocess.run(["apt", "install", "-y", "fzf"], check=False)
                fzf = shutil.which("fzf")

        if fzf:
            lines = [f"{name}\t{_project_from_session(name)}\t{_human_age(activity)}" for name, activity in sessions]
            result = subprocess.run(
                [
                    fzf,
                    "--ansi",
                    "--reverse",
                    "--prompt=session> ",
                    "--delimiter=\t",
                    "--with-nth=1,3",
                    "--preview-window=hidden",
                ],
                input="\n".join(lines),
                capture_output=True,
                text=True,
            )
            if result.returncode != 0 or not result.stdout.strip():
                sys.exit(0)
            selected = result.stdout.strip().split("\t")[0]
            os.execvp("tmux", ["tmux", "attach-session", "-t", selected])
        else:
            # Plain list fallback
            for i, (name, activity) in enumerate(sessions, 1):
                project = _project_from_session(name)
                print(f"  {i}. {name}  ({project})  {_human_age(activity)} ago")
            print("\nTo attach: ai attach <name>")
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
    parser.add_argument("-S", "--no-sandbox", action="store_true", help="Explicitly disable sandboxing")
    parser.add_argument("-W", "--no-worktree", action="store_true", help="Disable git worktree isolation")
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
            print("Error: [remote] host not set in ~/.config/ai-cli-utils/config.toml", file=sys.stderr)
            sys.exit(1)
        user = remote_cfg.get("user", "ubuntu")
        port = str(remote_cfg.get("port", 22))
        id_file = remote_cfg.get("identity_file", "")
        transport = remote_cfg.get("transport", "mosh")
        aliases = get_project_aliases()
        raw_project = args.project or get_current_project_name()
        if args.project and ("/" in args.project or "\\" in args.project):
            print("Error: --project name must not contain path separators", file=sys.stderr)
            sys.exit(1)
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
            remote_cmd += f" {shlex.quote(name)}"
        if transport == "mosh":
            mosh_args = ["mosh"]
            if port != "22":
                mosh_args += [
                    "--ssh",
                    f"ssh -p {port}" + (f" -i {shlex.quote(os.path.expanduser(id_file))}" if id_file else ""),
                ]
            elif id_file:
                mosh_args += ["--ssh", f"ssh -i {shlex.quote(os.path.expanduser(id_file))}"]
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

    # Registry validation: ensure all projects are registered before launching session
    if not validate_registry_completeness(interactive=sys.stdin.isatty()):
        sys.exit(1)

    cleanup_stale_sessions(config)
    current_project_name = get_current_project_name()
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
        project_name=current_project_name,
    )
    # Emit iTerm2 profile/color/title now, before tmux takes over the pane.
    # This fires in the current shell (no DCS wrapping needed) so it works
    # for new tabs, split panes, and re-attaches alike.
    _emit_iterm2_profile_setup(ai_name, engine, session_id)

    # Check if session already exists (e.g., re-attaching after disconnect)
    existing = subprocess.run(["tmux", "has-session", "-t", session_id], capture_output=True)
    if existing.returncode == 0:
        # Session exists — attach and detach any stale clients (e.g., closed tabs)
        os.execvp("tmux", ["tmux", "attach-session", "-d", "-t", session_id])
    elif args.is_remote:
        # Create session attached (not -d) so Claude gets a proper PTY.
        # The script's tmux detach-client at loop end drops us back to the SSH/mosh shell.
        os.execvp("tmux", ["tmux", "new-session", "-s", session_id, "--", "bash", "-c", script])
    else:
        os.execvp("tmux", ["tmux", "new-session", "-s", session_id, "--", "bash", "-c", script])


if __name__ == "__main__":  # pragma: no cover
    cli()
