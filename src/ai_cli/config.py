"""XDG paths, config loading, session map, and project registry.

Foundation module — must not import from any other ai_cli submodule.
All other modules depend on this one.
"""

import json
import os
import socket
import sys
import tomllib
from pathlib import Path

_OS_TYPE_MAP = {
    "win32": "windows",
    "darwin": "macos",
    "linux": "linux",
}


# --- XDG Directory Support ---


def _pid_alive(pid: int) -> bool:
    """Return True if a process with the given PID exists, False otherwise.

    Uses psutil for cross-platform correctness (Windows, macOS, Linux).
    """
    import psutil

    return psutil.pid_exists(pid)


def _migrate_xdg_dir(old: Path, new: Path) -> Path:
    """Rename old XDG dir to new name if old exists and new does not."""
    if old.exists() and not new.exists():
        old.rename(new)
    return new


def detect_machine_profile() -> dict[str, str]:
    """Return detected host_id and os_type for this machine.

    host_id: AI_HOST env var → socket.gethostname() fallback
    os_type: sys.platform mapped to 'windows' / 'macos' / 'linux'
    """
    host_id = os.environ.get("AI_HOST") or socket.gethostname()
    os_type = _OS_TYPE_MAP.get(sys.platform, sys.platform)
    return {"host_id": host_id, "os_type": os_type}


def ensure_machine_profile_registered(config_path: Path, config: dict) -> bool:
    """Write detected host_id / os_type into config.toml if not already set.

    Returns True if the file was modified (caller should reload config).
    Prints a one-time status message when a new profile is written.
    """
    machine = config.get("machine", {})
    missing = [k for k in ("host_id", "os_type") if not machine.get(k)]
    if not missing:
        return False

    profile = detect_machine_profile()
    text = config_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    insert_after: int | None = None
    for i, line in enumerate(lines):
        if line.strip() == "[machine]":
            insert_after = i
            break

    additions = [f'{k} = "{profile[k]}"\n' for k in missing]

    if insert_after is not None:
        lines = lines[: insert_after + 1] + additions + lines[insert_after + 1 :]
    else:
        lines.append("\n[machine]\n")
        lines.extend(additions)

    config_path.write_text("".join(lines), encoding="utf-8")
    print(
        f"Machine profile registered: {profile['host_id']} ({profile['os_type']})",
        file=sys.stderr,
    )
    return True


def get_xdg_config_home() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "ai-cli-utils"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return _migrate_xdg_dir(base / "ai-cli", base / "ai-cli-utils")


def get_xdg_state_home() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ai-cli-utils"
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return _migrate_xdg_dir(base / "ai-cli", base / "ai-cli-utils")


def get_xdg_cache_home() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ai-cli-utils" / "cache"
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return _migrate_xdg_dir(base / "ai-cli", base / "ai-cli-utils")


def get_xdg_data_home() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ai-cli-utils"
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "ai-cli-utils"


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

[project_prefixes]
## Task-prefix overrides, keyed by project directory name. Consulted before the
## project registry and before the default 3-character truncation. Use these when
## two repositories would otherwise collapse to the same prefix (for example
## "myapp-frontend" and "myapp-backend" both truncate to "mya").
# myapp-frontend = "mfe"
# myapp-backend = "mbe"

[project_registry]
## Repository-root → task-prefix registry. Add entries with `ai register`;
## do not derive prefixes from directory names. Example:
## "/home/user/projects/myproject" = { prefix = "MYPROJECT", type = "tool" }

[behavior]
## Enable system notifications on task completion
notify_on_exit = true

[quota_watch]
## Auto-register the quota-watch background daemon on every 'ai c'/'ai g' session
## launch. quota-watch polls Claude weekly usage and fires ntfy/discord alerts at
## 50/75/90% thresholds. Off by default -- the CC statusline already surfaces
## weekly usage, so this is redundant unless you specifically want push alerts on
## top of it. `ai quota watch start` (typed explicitly) always works regardless
## of this flag; this only gates the automatic per-session registration.
# auto_start = false

[worktree]
## Enable automatic git worktree isolation for new sessions
enabled = true

[session]
## Session names: c-{project}-{n} or c-r-{project}-{n} for remote sessions
stale_session_timeout = 15
## Wrap sessions in tmux? Default true. Set false to make bare mode the default
## (equivalent to always passing -b/--bare). tmux is a C binary and cannot be
## installed by pip/uv, so machines without it should set this false.
## Trade-off: without tmux you lose detach/reattach (`ai ls`, `ai attach`),
## sessions surviving a dropped SSH connection, and remote access from another
## device. If you only run sessions in a local terminal, false is fine.
# use_tmux = true

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
## Auto-detected from AI_HOST env var, then hostname; set manually to override.
## Example values: "mac", "hetzner", "work-laptop", "acn-windows"
# host_id = ""
## OS type for this machine. Auto-detected from sys.platform on first run.
## Values: "windows", "macos", "linux"
# os_type = ""

[update]
## Additional venv paths to install ai-cli-utils into after 'ai update'
## Useful if you have tools or virtual environments that depend on ai-cli-utils
# extra_venvs = ["/home/user/projects/mytool/.venv"]

[cdp]
## Chrome/Chromium binary path for CDP debug server (auto-detected if not set)
# binary_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
## Persistent Chrome profile directory for CDP sessions (survives restarts)
## Defaults to ~/.local/share/ai-cli-utils/chrome-profiles/automation
# profile_dir = "~/.local/share/ai-cli-utils/chrome-profiles/automation"

[gemini_billing]
## GCP project that holds the BigQuery billing export dataset.
## Required for `ai spend gemini` to show actual billed amounts.
# gcp_project_id = "my-gcp-project"
## Fully-qualified BigQuery table ID for the GCP detailed billing export.
## Enable in Cloud Console → Billing → Billing export → Detailed usage cost.
# billing_export_table = "my-project.billing_export.gcp_billing_export_v1_XXXXXX"
## Default port for the Chrome DevTools Protocol endpoint
# port = 9222

[workspace]
## VS Code .code-workspace file paths for 'ai ws pull'
## local_path is the default; --remote uses remote_path; --workspace PATH overrides both
# local_path = "~/projects/myorg/local.code-workspace"
# remote_path = "~/projects/myorg/remote.code-workspace"

[ai-core]
## REST API base URL and key for pushing CC usage events.
## Obtain from your ai-core backend instance.
# api_url = "https://your-ai-core-host"
# api_key = "ac-api-..."
"""


def load_config():
    config_dir = get_xdg_config_home()
    config_path = config_dir / "config.toml"

    if not config_path.exists():
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")

    try:
        with config_path.open("rb") as f:
            cfg = tomllib.load(f)
    except Exception as e:
        print(f"Warning: Failed to load config from {config_path}: {e}", file=sys.stderr)
        return {}

    if ensure_machine_profile_registered(config_path, cfg):
        try:
            with config_path.open("rb") as f:
                cfg = tomllib.load(f)
        except Exception:
            pass

    return cfg


# --- Prefix registry ---


class ProjectPrefixError(ValueError):
    """Raised when a repository has no unambiguous registered task prefix."""


def _config_path() -> Path:
    return get_xdg_config_home() / "config.toml"


def _project_root(path: Path) -> Path:
    """Return the main repository root for ``path`` without importing session.py."""
    resolved = path.expanduser().resolve()
    if WORKTREE_DIR in resolved.parts:
        return Path(*resolved.parts[: resolved.parts.index(WORKTREE_DIR)])
    for candidate in (resolved, *resolved.parents):
        if (candidate / ".git").exists():
            return candidate
    return resolved


def _registry_entries(config: dict | None = None) -> dict[str, dict[str, str]]:
    """Return validated config-backed prefix entries keyed by normalized root."""
    raw = (config if config is not None else load_config()).get("project_registry", {})
    if not isinstance(raw, dict):
        raise ProjectPrefixError("Invalid [project_registry] configuration. Run: ai register -p . -x PREFIX")

    entries: dict[str, dict[str, str]] = {}
    for root, value in raw.items():
        if not isinstance(value, dict) or not isinstance(value.get("prefix"), str) or not value["prefix"].strip():
            raise ProjectPrefixError(
                f"Invalid prefix registry entry for {root!r}. Run: ai register -p {root!s} -x PREFIX"
            )
        normalized = os.path.normcase(str(_project_root(Path(root))))
        if normalized in entries:
            raise ProjectPrefixError(f"Ambiguous prefix registry entry for {root!s}. Remove the duplicate entry.")
        entries[normalized] = {"prefix": value["prefix"].strip(), "type": str(value.get("type", "tool"))}
    return entries


def _read_local_project_metadata(root: Path) -> tuple[str, str] | None:
    """Read optional task-prefix metadata from a repository's pyproject.toml."""
    path = root / "pyproject.toml"
    if not path.is_file():
        return None
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        metadata = data.get("tool", {}).get("ai-cli", {})
        prefix = metadata.get("task_prefix")
        if isinstance(prefix, str) and prefix.strip():
            return prefix.strip(), str(metadata.get("project_type", "tool"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return None


def _write_registry_entry(config_path: Path, root: Path, prefix: str, project_type: str) -> None:
    """Upsert one inline-table entry in the config registry while preserving other settings."""
    text = config_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    header = "[project_registry]"
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == header)
    except StopIteration:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.extend(["\n", f"{header}\n"])
        start = len(lines) - 1

    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    key = json.dumps(str(root))
    replacement = f"{key} = {{ prefix = {json.dumps(prefix)}, type = {json.dumps(project_type)} }}\n"
    for i in range(start + 1, end):
        if lines[i].lstrip().startswith(f"{key} ="):
            lines[i] = replacement
            break
    else:
        lines.insert(end, replacement)
    config_path.write_text("".join(lines), encoding="utf-8")


def _set_beads_prefix(root: Path, prefix: str) -> None:
    """Project the registered prefix into Beads when that project uses Beads."""
    path = root / ".beads" / "config.yaml"
    if not path.is_file():
        return
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    replacement = f'issue-prefix: "{prefix}"\n'
    for i, line in enumerate(lines):
        if line.lstrip("# ").startswith("issue-prefix:"):
            lines[i] = replacement
            break
    else:
        lines.insert(0, replacement)
    path.write_text("".join(lines), encoding="utf-8")


def register_project(project: str | Path, prefix: str, project_type: str = "tool") -> Path:
    """Register a repository root and synchronize its optional Beads prefix."""
    candidate = Path(project).expanduser()
    if not candidate.exists() and not candidate.is_absolute():
        candidate = _find_project_dir(str(project))
    if not candidate.is_dir():
        raise ProjectPrefixError(f"Project path does not exist: {project}")
    if not prefix.strip():
        raise ProjectPrefixError("Prefix must not be empty. Run: ai register -p . -x PREFIX")

    root = _project_root(candidate)
    config_path = _config_path()
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    _write_registry_entry(config_path, root, prefix.strip(), project_type.strip() or "tool")
    _set_beads_prefix(root, prefix.strip())
    return root


def resolve_project_prefix(path: Path | None = None) -> str:
    """Return the registered prefix for a repository root or explain how to register it."""
    root = _project_root(path or Path.cwd())
    key = os.path.normcase(str(root))
    entries = _registry_entries()
    entry = entries.get(key)
    if entry:
        return entry["prefix"]

    metadata = _read_local_project_metadata(root)
    if metadata:
        prefix, project_type = metadata
        register_project(root, prefix, project_type)
        return prefix

    raise ProjectPrefixError(
        f"No task prefix is registered for repository {root}. Register it with: ai register -p {root} -x PREFIX"
    )


def resolve_project_prefix_by_name(project_name: str) -> str:
    """Return one registered prefix for a project directory name, rejecting ambiguity."""
    matches = [entry["prefix"] for root, entry in _registry_entries().items() if Path(root).name == project_name]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ProjectPrefixError(
            f"Project name {project_name!r} matches multiple registered roots. Use a unique repository name."
        )
    candidate = _find_project_dir(project_name)
    return resolve_project_prefix(candidate)


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
        with registry_path.open("rb") as f:
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
    Returns False (and prints error) if user declines registration or stdin ends.
    A keyboard interrupt propagates so the caller can abort immediately; no
    registry changes are written unless every prompt completes successfully.
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
    entries: list[tuple[str, str]] = []
    for name in unregistered:
        suggested_prefix = name.upper().replace("-", "_")[:8]
        try:
            answer = input(
                f'Unregistered project: "{name}" (~/{projects_dir.name}/{name})\n'
                f"Suggested task_prefix: {suggested_prefix}\n"
                f"Add to registry? [Y/n, or enter custom prefix]: "
            ).strip()
        except EOFError:
            print("\nRegistry incomplete — exiting.", file=sys.stderr)
            return False

        if answer.lower() == "n":
            print("Registry incomplete — exiting. All projects must be registered.", file=sys.stderr)
            return False

        prefix = answer if answer and answer.lower() != "y" else suggested_prefix

        entries.append((name, prefix))

    # Commit all prompted registrations at once so cancellation or rejection
    # cannot leave a partially updated registry behind.
    with registry_path.open("a") as f:
        for name, prefix in entries:
            f.write(f'\n[[projects]]\nname = "{name}"\ntask_prefix = "{prefix}"\ntype = "tool"\nactive = true\n')
    for name, prefix in entries:
        print(f'Registered "{name}" with prefix "{prefix}"')

    # Force reload after registration
    global _registry_cache
    _registry_cache = None
    load_project_registry(_force=True)
    return True


def get_project_prefix_overrides() -> dict[str, str]:
    """Return the user's project-name → task-prefix override map.

    Read from the ``[project_prefixes]`` table in ``config.toml``. Overrides exist
    to resolve collisions when several repositories share the same 3-character
    truncation (e.g. ``myapp-frontend`` and ``myapp-backend`` both → ``"mya"``).
    Project names are inherently user-specific, so they belong in configuration
    rather than in this package's source.
    """
    table = load_config().get("project_prefixes", {})
    if not isinstance(table, dict):
        return {}
    return {str(name): str(prefix) for name, prefix in table.items()}


def _get_project_prefix_by_name(project_name: str) -> str:
    """Resolve an explicit legacy project name without fabricating a prefix.

    Session, worktree, and Beads prefix consumers use :func:`resolve_project_prefix`.
    This compatibility helper remains for integrations that still use the older
    name-only registry.
    """
    overrides = get_project_prefix_overrides()
    if project_name in overrides:
        return overrides[project_name]
    for project in load_project_registry():
        if project.get("name") == project_name and project.get("task_prefix"):
            return str(project["task_prefix"])
    raise ProjectPrefixError(
        f"No task prefix is registered for project {project_name!r}. "
        f"Register its repository with: ai register -p {project_name} -x PREFIX"
    )


def get_project_aliases() -> dict:
    """Build project alias map: task_prefix.lower() -> project name from project registry."""
    aliases = {}
    for p in load_project_registry():
        prefix = p.get("task_prefix", "").lower()
        name = p.get("name", "")
        if prefix and name and prefix != name:
            aliases[prefix] = name
    return aliases
