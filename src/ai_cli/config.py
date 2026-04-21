"""XDG paths, config loading, session map, and project registry.

Foundation module — must not import from any other ai_cli submodule.
All other modules depend on this one.
"""

import json
import os
import sys
import tomllib
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

DEFAULT_CONFIG = """## ai-cli-utils configuration

[gemini]
## Projects that should NOT be sandboxed by default
## (Matches the project prefix in your project registry TOML)
# sandbox_whitelist = ["sw"]
## Set true only after AI-CLI-43 confirms billing credit status.
## When false (default), the ai_studio_paid tier is excluded from all fallback chains.
# paid_fallback_enabled = false
## Command used to launch gemini-cli. Override if the binary is not on PATH
## (e.g. "npx @google/gemini-cli" for npx-only installs).
# command = "gemini"

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
## VPN poll interval in seconds for the vpn-watch daemon (default: 3)
# vpn_poll_interval = 3

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
## Set AI_CLI_HOST in your shell environment (~/.zshenv or ~/.bashrc) so it is available to all processes.
## Example values: "mac", "hetzner", "work-laptop"
# host_id = ""

[update]
## Additional venv paths to install ai-cli-utils into after 'ai update'
## Useful if you have tools or virtual environments that depend on ai-cli-utils
# extra_venvs = ["/home/user/projects/mytool/.venv"]

[cdp]
## Chrome/Chromium binary path for CDP debug server (auto-detected if not set)
# binary_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

[gemini_billing]
## GCP project that holds the BigQuery billing export dataset.
## Required for `ai spend gemini` to show actual billed amounts.
# gcp_project_id = "my-gcp-project"
## Fully-qualified BigQuery table ID for the GCP detailed billing export.
## Enable in Cloud Console → Billing → Billing export → Detailed usage cost.
# billing_export_table = "my-project.billing_export.gcp_billing_export_v1_XXXXXX"
## Default port for the Chrome DevTools Protocol endpoint
# port = 9222

[humanware]
## REST API base URL and key for pushing CC usage events.
## Obtain from your humanware backend instance.
# api_url = "https://your-humanware-host"
# api_key = "hw-api-..."
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

    # Scan for project directories (skip hidden dirs, .worktrees, bare git repos, etc.)
    unregistered = []
    for d in sorted(projects_dir.iterdir()):
        if not d.is_dir() or d.name.startswith(".") or d.name.endswith(".git"):
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
            return p.get("task_prefix", project_name[:3]).lower().strip("-")
    return project_name[:3].lower().strip("-")


def get_project_aliases() -> dict:
    """Build project alias map: task_prefix.lower() -> project name from project registry."""
    aliases = {}
    for p in load_project_registry():
        prefix = p.get("task_prefix", "").lower()
        name = p.get("name", "")
        if prefix and name and prefix != name:
            aliases[prefix] = name
    return aliases
