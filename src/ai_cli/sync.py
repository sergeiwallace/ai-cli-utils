"""
ai sync push/pull — bidirectional CC memory and history sync via git staging repo.

Architecture:
  Mac: ~/.claude-sync-staging/  (working git repo, remote = ssh://{user}@{host}/{home}/.claude-sync-staging.git)
  Server: ~/.claude-sync-staging.git  (bare repo, receives pushes from both machines)
  Server: ~/.claude-sync-staging/     (working checkout, remote = file://{home}/.claude-sync-staging.git)

Push flow: stage local files → git commit → git push to bare repo
Pull flow: git fetch + merge from bare repo → apply merged files to ~/.claude/projects/
"""

import os
import re
import sys
import time
import shutil
import hashlib
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFLICT_LOG = Path.home() / ".claude-sync-conflicts.log"

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "ai-sync",
    "GIT_AUTHOR_EMAIL": "ai-sync@local",
    "GIT_COMMITTER_NAME": "ai-sync",
    "GIT_COMMITTER_EMAIL": "ai-sync@local",
}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class SyncConfig:
    staging_dir: Path
    remote_url: str
    local_prefix: str
    remote_host: str
    source_machine: str  # "mac" or "server"


def _is_mac() -> bool:
    return sys.platform == "darwin"


def get_local_prefix() -> str:
    """Derive the CC project directory prefix for this machine from the home path.

    Claude Code encodes project paths by replacing each non-alphanumeric character
    with '-'. The prefix for all projects on this machine is the encoded home path
    plus '-projects-'.

    Examples:
      Mac  /Users/username  -> -Users-username-projects-
      Linux /home/user      -> -home-user-projects-
    """
    home = str(Path.home())
    encoded = re.sub(r"[^a-zA-Z0-9]", "-", home)
    return f"{encoded}-projects-"


def get_source_machine() -> str:
    return "mac" if _is_mac() else "server"


def _default_remote_bare_url(remote_host: str) -> str:
    """Derive the default SSH URL for the bare repo from a user@host string.

    Assumes the bare repo lives at ~/.claude-sync-staging.git on the remote.
    root -> /root/.claude-sync-staging.git
    other users -> /home/{user}/.claude-sync-staging.git
    """
    if "@" in remote_host:
        user = remote_host.split("@")[0]
        repo_home = "/root" if user == "root" else f"/home/{user}"
    else:
        repo_home = "/root"
    return f"ssh://{remote_host}{repo_home}/.claude-sync-staging.git"


def load_sync_config() -> SyncConfig:
    """Load sync config, falling back to sensible defaults."""
    from .main import load_config

    config = load_config()
    sync_cfg = config.get("sync", {})

    local_prefix = get_local_prefix()
    source_machine = get_source_machine()
    remote_host = sync_cfg.get("remote_host")
    if not remote_host:
        # Derive from [remote] section if available
        remote_cfg = config.get("remote", {})
        r_host = remote_cfg.get("host")
        r_user = remote_cfg.get("user", "ubuntu")
        if r_host:
            remote_host = f"{r_user}@{r_host}"
        else:
            print(
                "Error: [sync] remote_host not set in ~/.config/ai-cli/config.toml.\n"
                'Set [sync] remote_host = "user@host" or configure [remote] host + user.',
                file=sys.stderr,
            )
            sys.exit(1)
    staging_dir = Path(sync_cfg.get("staging_dir", "~/.claude-sync-staging")).expanduser()

    if source_machine == "mac":
        remote_url = sync_cfg.get("remote_url", _default_remote_bare_url(remote_host))
    else:
        remote_url = sync_cfg.get("remote_url", f"file://{Path.home()}/.claude-sync-staging.git")

    return SyncConfig(
        staging_dir=staging_dir,
        remote_url=remote_url,
        local_prefix=local_prefix,
        remote_host=remote_host,
        source_machine=source_machine,
    )


def _cc_projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


def _handoff_queue_dir() -> Path:
    from .main import _get_main_project_dir

    return _get_main_project_dir() / ".handoff-queue"


# Namespace in the staging repo for handoff queue files.
# Double-dash prefix ensures it never collides with project names.
_HANDOFF_STAGING_NAMESPACE = "--handoff-queue"

# Namespace for user-level CC config files (hooks, statusline, keybindings).
# settings.json requires path translation between Mac and server.
_CONFIG_STAGING_NAMESPACE = "--config"

# Files to sync from ~/.claude/ (relative to ~/.claude/)
# Excludes: settings.json (needs path translation, handled separately),
#           settings.local.json (machine-specific by design),
#           security_warnings_state_*.json (session-specific),
#           stats-cache.json (local metrics),
#           ai-cli-update.json (local update state),
#           projects/ (handled by project sync),
#           plugins/ (managed by CC plugin system)
_CONFIG_SYNC_FILES = [
    "statusline-command.sh",
    "keybindings.json",
    "hooks/config-reload-check.sh",
    "hooks/config-reload-reset.sh",
    "hooks/notify.sh",
]

# settings.json is synced separately with path translation
_SETTINGS_FILE = "settings.json"


def _parse_flags(flags: list[str]) -> tuple[bool, bool, bool, bool, bool]:
    """Parse common sync flags. Returns (memories_only, dry_run, verbose, force, prefer_remote)."""
    return (
        "--memories-only" in flags,
        "--dry-run" in flags,
        "--verbose" in flags,
        "--force" in flags,
        "--prefer-remote" in flags,
    )


# ---------------------------------------------------------------------------
# Path normalization
# ---------------------------------------------------------------------------


def normalize_project_path(cc_dir_name: str, local_prefix: str) -> Optional[str]:
    """Convert a CC project dir name to a bare project name.

    E.g. '-Users-username-projects-myproject' -> 'myproject'
    '-Users-username-projects-myproject--worktrees-sw-1' -> 'myproject--worktrees-sw-1'
    Returns None if the dir name does not match the local prefix.
    """
    if cc_dir_name.startswith(local_prefix):
        return cc_dir_name[len(local_prefix) :]
    return None


def denormalize_project_name(bare_name: str, local_prefix: str) -> str:
    """Convert a bare project name to the CC project dir name for this machine.

    E.g. 'myproject' -> '-Users-username-projects-myproject'
    """
    return local_prefix + bare_name


# ---------------------------------------------------------------------------
# File classification
# ---------------------------------------------------------------------------


def is_memory_file(path: Path) -> bool:
    """Returns True if path is a memory file (MEMORY.md or memory/*.md)."""
    if path.name == "MEMORY.md":
        return True
    return "memory" in path.parts and path.suffix == ".md"


def is_jsonl_file(path: Path) -> bool:
    return path.suffix == ".jsonl"


def should_sync_file(path: Path, memories_only: bool) -> bool:
    """Returns True if the file should be synced given the memories_only flag."""
    if path.name == ".DS_Store":
        return False
    if "tool-results" in path.parts:
        return False
    # subagents/ lives inside session lock dirs ({uuid}/subagents/). Syncing it
    # recreates the lock directory on the remote machine, causing CC to treat the
    # session as "active" and hide it from the /resume picker.
    if "subagents" in path.parts:
        return False
    if memories_only:
        return is_memory_file(path)
    return True


# ---------------------------------------------------------------------------
# File hashing and JSONL divergence detection
# ---------------------------------------------------------------------------


def file_hash(path: Path) -> str:
    """Return SHA-256 hex digest of file content."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _detect_foreign_home(jsonl_path: Path) -> Optional[str]:
    """Read the first cwd or project field in a JSONL file. Return the home prefix if it differs from local."""
    import json as _json

    local_home = str(Path.home())
    try:
        with open(jsonl_path, "rb") as f:
            for raw in f:
                if b'"cwd"' not in raw and b'"project"' not in raw:
                    continue
                try:
                    entry = _json.loads(raw)
                except Exception:
                    continue
                for key in ("cwd", "project"):
                    val = entry.get(key, "")
                    if val and val.startswith("/") and not val.startswith(local_home):
                        # Extract the home prefix: the path starts with /home/user or /Users/user
                        parts = val.split("/")
                        # /home/user/... -> parts[0]="" parts[1]="home" parts[2]="user"
                        # /Users/username/... -> parts[1]="Users" parts[2]="username"
                        if len(parts) >= 3:
                            return "/" + "/".join(parts[1:3])
    except Exception:
        pass
    return None


def translate_cwd_paths(content: bytes, foreign_home: str) -> bytes:
    """Replace foreign home path prefix with local home in a JSONL file's bytes."""
    local_home = str(Path.home())
    return content.replace(foreign_home.encode(), local_home.encode())


def detect_jsonl_divergence(local_path: Path, staging_path: Path) -> str:
    """Classify the relationship between a local and staged JSONL file.

    Applies cwd path translation to staging content before comparing, so a
    locally-translated file is not falsely flagged as diverged from its
    untranslated staging counterpart.

    Returns one of:
    - "identical"           — files are the same (after translation)
    - "fast_forward_local"  — local is an extension of staging (local is newer)
    - "fast_forward_remote" — staging is an extension of local (remote is newer)
    - "diverged"            — both files grew independently
    """
    local_exists = local_path.exists()
    staging_exists = staging_path.exists()

    if not local_exists and not staging_exists:
        return "identical"
    if not local_exists:
        return "fast_forward_remote"
    if not staging_exists:
        return "fast_forward_local"

    local_bytes = local_path.read_bytes()
    staging_bytes = staging_path.read_bytes()

    # Translate staging content before comparing so cross-machine cwd differences
    # don't cause false divergence on files that are otherwise identical.
    foreign_home = _detect_foreign_home(staging_path)
    if foreign_home:
        staging_bytes = translate_cwd_paths(staging_bytes, foreign_home)

    if local_bytes == staging_bytes:
        return "identical"
    if staging_bytes.startswith(local_bytes):
        return "fast_forward_remote"
    if local_bytes.startswith(staging_bytes):
        return "fast_forward_local"

    return "diverged"


# ---------------------------------------------------------------------------
# Staging repo initialization
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["git"] + args, cwd=cwd, check=check, capture_output=True, env=_GIT_ENV, **kwargs)


def init_staging_repo(staging_dir: Path, remote_url: str) -> None:
    """Initialize the local staging repo if it does not exist.

    Tries to clone from remote first (preserves shared history with the bare repo).
    Falls back to git init + initial commit only if remote is empty/unreachable.
    """
    if staging_dir.exists() and (staging_dir / ".git").exists():
        # Already initialized — verify remote is set
        res = _git(["remote", "get-url", "origin"], staging_dir, check=False)
        if res.returncode != 0:
            _git(["remote", "add", "origin", remote_url], staging_dir)
        return

    # Try to clone from remote (handles case where remote already has commits)
    staging_dir.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(
        ["git", "clone", remote_url, str(staging_dir)],
        capture_output=True,
        text=True,
        timeout=120,
        env=_GIT_ENV,
    )
    if res.returncode == 0:
        return  # Successfully cloned — shared history established

    # Clone failed (bare repo empty or does not exist yet) — init + seed commit
    staging_dir.mkdir(parents=True, exist_ok=True)
    _git(["init"], staging_dir)
    _git(["remote", "add", "origin", remote_url], staging_dir)
    (staging_dir / ".gitkeep").write_text("")
    _git(["add", ".gitkeep"], staging_dir)
    _git(["commit", "-m", "init staging repo"], staging_dir)


def init_server_bare_repo(remote_host: str) -> None:
    """Initialize the bare repo on the server if it does not exist. Non-fatal."""
    subprocess.run(
        ["ssh", remote_host, "git init --bare ~/.claude-sync-staging.git 2>/dev/null || true"],
        capture_output=True,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Stage files (push direction)
# ---------------------------------------------------------------------------


def stage_project_files(
    staging_dir: Path,
    cc_projects_dir: Path,
    local_prefix: str,
    memories_only: bool,
    verbose: bool,
    dry_run: bool,
) -> dict:
    """Copy files from cc_projects_dir into staging_dir with normalized paths.

    Returns:
      staged_files: list of (src_path, dst_path) tuples
      project_names: list of bare project names processed
      memory_count: int
      jsonl_count: int
    """
    staged_files: list[tuple[Path, Path]] = []
    project_names: list[str] = []
    memory_count = 0
    jsonl_count = 0

    if not cc_projects_dir.exists():
        return {
            "staged_files": staged_files,
            "project_names": project_names,
            "memory_count": memory_count,
            "jsonl_count": jsonl_count,
        }

    for cc_dir in sorted(cc_projects_dir.iterdir()):
        if not cc_dir.is_dir():
            continue
        bare_name = normalize_project_path(cc_dir.name, local_prefix)
        if bare_name is None:
            continue

        project_names.append(bare_name)
        staging_project_dir = staging_dir / bare_name

        for src in cc_dir.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(cc_dir)
            if not should_sync_file(rel, memories_only):
                continue

            dst = staging_project_dir / rel

            if not dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

            staged_files.append((src, dst))
            if is_memory_file(rel):
                memory_count += 1
            elif is_jsonl_file(rel):
                jsonl_count += 1

            if verbose:
                print(f"  stage: {bare_name}/{rel}")

    return {
        "staged_files": staged_files,
        "project_names": project_names,
        "memory_count": memory_count,
        "jsonl_count": jsonl_count,
    }


# ---------------------------------------------------------------------------
# Git commit
# ---------------------------------------------------------------------------


def git_commit_staged(
    staging_dir: Path,
    source_machine: str,
    project_names: list[str],
    memory_count: int,
    jsonl_count: int,
    total_count: int,
) -> bool:
    """Stage all changes and commit. Returns True if a commit was made."""
    _git(["add", "-A"], staging_dir)

    # Check if there is anything to commit
    res = _git(["diff", "--cached", "--quiet"], staging_dir, check=False)
    if res.returncode == 0:
        return False  # Nothing to commit

    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    projects_str = ", ".join(sorted(project_names)) if project_names else "(none)"
    msg = (
        f"sync push from {source_machine} {ts}\n\n"
        f"projects: {projects_str}\n"
        f"files: {total_count} changed\n"
        f"memories: {memory_count} files\n"
        f"jsonl: {jsonl_count} files"
    )

    _git(["commit", "-m", msg], staging_dir)
    return True


# ---------------------------------------------------------------------------
# Apply files (pull direction)
# ---------------------------------------------------------------------------


def _write_jsonl_translated(src: Path, dst: Path) -> None:
    """Write src JSONL to dst, translating any foreign home paths to the local home."""
    foreign_home = _detect_foreign_home(src)
    if foreign_home:
        content = src.read_bytes()
        content = translate_cwd_paths(content, foreign_home)
        dst.write_bytes(content)
    else:
        shutil.copy2(src, dst)


def _detect_foreign_home_in_history(history_path: Path) -> Optional[str]:
    """Scan history.jsonl for a project field pointing to a foreign home directory."""
    import json as _json

    local_home = str(Path.home())
    try:
        with open(history_path, "rb") as f:
            for raw in f:
                if b'"project"' not in raw:
                    continue
                try:
                    entry = _json.loads(raw)
                    project = entry.get("project", "")
                    if project and not project.startswith(local_home):
                        parts = project.split("/")
                        if len(parts) >= 3:
                            return "/" + "/".join(parts[1:3])
                except Exception:
                    pass
    except Exception:
        pass
    return None


def translate_history_jsonl(verbose: bool = False) -> int:
    """Translate foreign home paths in ~/.claude/history.jsonl project fields.

    CC validates each history entry's project path against the filesystem.
    Entries with Mac paths on the server cause synced conversations to flash
    then disappear in the picker. This replaces foreign home prefixes in the
    project field with the local home path.

    Returns the number of byte-level replacements made (0 = nothing changed).
    """
    history_path = Path.home() / ".claude" / "history.jsonl"
    if not history_path.exists():
        return 0

    foreign_home = _detect_foreign_home_in_history(history_path)
    if not foreign_home:
        return 0

    local_home = str(Path.home())
    content = history_path.read_bytes()
    updated = content.replace(foreign_home.encode(), local_home.encode())

    if updated == content:
        return 0

    count = content.count(foreign_home.encode())
    history_path.write_bytes(updated)
    if verbose:
        print(f"  translate history.jsonl: {count} project paths updated ({foreign_home} → {local_home})")
    return count


def replicate_history_to_worktrees(verbose: bool = False) -> int:
    """Add worktree-translated entries to history.jsonl for projects with active worktrees.

    CC's /resume picker filters conversations by matching the `project` field in
    history.jsonl against the current session's working directory. When a session
    runs inside a worktree (e.g. /home/user/projects/myproject/.worktrees/myproject-1),
    history entries with project=/home/user/projects/myproject won't match.

    This function finds main-project history entries and creates copies with
    project paths rewritten to each active worktree path, so conversations
    appear in the /resume picker for worktree sessions.

    Only adds entries if they don't already exist. Safe to run multiple times.

    Returns the number of entries added.
    """
    import json as _json

    history_path = Path.home() / ".claude" / "history.jsonl"
    if not history_path.exists():
        return 0

    from .main import _get_projects_dir

    projects_base = _get_projects_dir()
    entries = history_path.read_text().strip().split("\n")
    existing_projects = set()
    main_entries_by_project: dict[str, list[str]] = {}

    for line in entries:
        if not line:
            continue
        try:
            d = _json.loads(line)
            proj = d.get("project", "")
            existing_projects.add(proj)
            # Collect main project entries (not worktree entries)
            if proj and "/projects/" in proj and "--worktrees-" not in proj and ".worktrees/" not in proj:
                main_entries_by_project.setdefault(proj, []).append(line)
        except Exception:
            pass

    new_entries = []
    for main_cwd, lines in main_entries_by_project.items():
        # Find the project name from the path
        project_name = main_cwd.rstrip("/").split("/")[-1]
        project_path = projects_base / project_name
        if not project_path.is_dir():
            continue

        worktrees_dir = project_path / ".worktrees"
        if not worktrees_dir.is_dir():
            continue

        for wt in worktrees_dir.iterdir():
            if not wt.is_dir() or not (wt / ".git").exists():
                continue
            wt_cwd = str(wt)
            if wt_cwd in existing_projects:
                continue  # Already have entries for this worktree

            for line in lines:
                translated = line.replace(f'"project":"{main_cwd}"', f'"project":"{wt_cwd}"')
                translated = translated.replace(f'"project": "{main_cwd}"', f'"project": "{wt_cwd}"')
                if translated != line:
                    new_entries.append(translated)

            existing_projects.add(wt_cwd)  # Prevent duplicates across iterations

    if new_entries:
        with open(history_path, "a") as f:
            for entry in new_entries:
                f.write(entry + "\n")
        if verbose:
            print(f"  replicate history: {len(new_entries)} worktree entries added")

    return len(new_entries)


def retranslate_project_jsonls(verbose: bool = False) -> int:
    """Translate foreign home paths in-place in all conversation JSONL files.

    Fixes files that were synced before path translation was implemented,
    or files that were skipped due to divergence detection (fast_forward_local).
    CC reads cwd/project fields from conversation JSONL files when building the
    picker — untranslated Mac paths cause entries to flash then disappear.

    Returns the number of files translated.
    """
    cc_projects_dir = _cc_projects_dir()
    if not cc_projects_dir.exists():
        return 0

    local_home = str(Path.home())
    count = 0
    for jsonl_path in cc_projects_dir.rglob("*.jsonl"):
        if not jsonl_path.is_file():
            continue
        foreign_home = _detect_foreign_home(jsonl_path)
        if not foreign_home:
            continue
        content = jsonl_path.read_bytes()
        updated = content.replace(foreign_home.encode(), local_home.encode())
        if updated != content:
            jsonl_path.write_bytes(updated)
            count += 1
            if verbose:
                print(f"  retranslate: {jsonl_path.relative_to(cc_projects_dir)}")
    return count


def apply_pull_files(
    staging_dir: Path,
    cc_projects_dir: Path,
    local_prefix: str,
    memories_only: bool,
    verbose: bool,
    dry_run: bool,
    prefer_remote: bool = False,
) -> dict:
    """Apply files from staging_dir to cc_projects_dir.

    Returns:
      conflicts: list of conflict description strings
      applied_count: int
    """
    conflicts: list[str] = []
    applied_count = 0

    for staging_project_dir in sorted(staging_dir.iterdir()):
        if not staging_project_dir.is_dir() or staging_project_dir.name.startswith("."):
            continue

        bare_name = staging_project_dir.name
        cc_dir_name = denormalize_project_name(bare_name, local_prefix)
        cc_project_dir = cc_projects_dir / cc_dir_name

        for src in staging_project_dir.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(staging_project_dir)
            if not should_sync_file(rel, memories_only):
                continue

            dst = cc_project_dir / rel

            if is_jsonl_file(src):
                divergence = detect_jsonl_divergence(dst, src)
                if divergence == "identical":
                    continue
                elif divergence == "fast_forward_remote":
                    if not dry_run:
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        _write_jsonl_translated(src, dst)
                    applied_count += 1
                    if verbose:
                        print(f"  apply (ff): {bare_name}/{rel}")
                elif divergence == "fast_forward_local":
                    if verbose:
                        print(f"  skip (local ahead): {bare_name}/{rel}")
                elif divergence == "diverged":
                    if prefer_remote:
                        if not dry_run:
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            _write_jsonl_translated(src, dst)
                        applied_count += 1
                        if verbose:
                            print(f"  apply (prefer-remote): {bare_name}/{rel}")
                    else:
                        ts = time.strftime("%Y-%m-%dT%H-%M-%S", time.gmtime())
                        conflict_name = f"conflict-{ts}.jsonl"
                        conflict_path = (dst.parent if dst.parent.exists() else cc_project_dir) / conflict_name
                        if not dry_run:
                            conflict_path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src, conflict_path)
                        conflict_str = f"jsonl  {bare_name}/{rel.name} — remote saved as {conflict_name}"
                        conflicts.append(conflict_str)
                        print(
                            f"JSONL CONFLICT: {bare_name}/{rel.name} — remote version saved as {conflict_name}",
                            file=sys.stderr,
                        )
            else:
                # Memory or other text file — skip if identical, then check for git conflict markers
                if dst.exists() and file_hash(src) == file_hash(dst):
                    continue

                content = src.read_text(errors="replace")
                has_conflict_markers = "<<<<<<<" in content and ">>>>>>>" in content

                if has_conflict_markers:
                    conflict_path = dst.with_suffix(dst.suffix + ".conflict")
                    if not dry_run:
                        conflict_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, conflict_path)
                    conflict_str = f"memory {bare_name}/{rel} — .conflict file written"
                    conflicts.append(conflict_str)
                    print(
                        f"CONFLICT: {bare_name}/{rel} — resolve manually or in next CC session",
                        file=sys.stderr,
                    )
                else:
                    if not dry_run:
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dst)
                    applied_count += 1
                    if verbose:
                        print(f"  apply: {bare_name}/{rel}")

    # After applying files to main CC project dirs, replicate JSONL files to
    # any active worktree CC dirs with translated cwd paths. This lets CC sessions
    # running inside worktrees see conversations that were synced from the other machine.
    if not memories_only and not dry_run:
        wt_count = _replicate_to_worktrees(cc_projects_dir, local_prefix, verbose)
        applied_count += wt_count

    return {"conflicts": conflicts, "applied_count": applied_count}


def _find_project_worktrees(project_path: Path) -> list[Path]:
    """Find active git worktree directories for a project.

    Returns a list of worktree absolute paths (e.g. /home/user/projects/myproject/.worktrees/myproject-1).
    """
    worktrees_dir = project_path / ".worktrees"
    if not worktrees_dir.is_dir():
        return []
    return [d for d in worktrees_dir.iterdir() if d.is_dir() and (d / ".git").exists()]


def _replicate_to_worktrees(
    cc_projects_dir: Path,
    local_prefix: str,
    verbose: bool,
) -> int:
    """Replicate JSONL conversation files from main CC project dirs to worktree CC dirs.

    For each project that has git worktrees, copies JSONL files from the main CC project
    directory to each worktree's CC directory, translating the `cwd` field inside the
    JSONL to match the worktree path. This is necessary because CC keys conversations
    to the exact cwd path, and worktrees have a different path than the main project.

    Also copies session lock directories (uuid dirs) needed for /resume metadata.

    Returns the number of files replicated.
    """

    from .main import _get_projects_dir

    projects_base = _get_projects_dir()
    count = 0

    for cc_dir in sorted(cc_projects_dir.iterdir()):
        if not cc_dir.is_dir():
            continue

        bare_name = normalize_project_path(cc_dir.name, local_prefix)
        if bare_name is None:
            continue

        # Skip worktree CC dirs themselves (they contain '--worktrees-' in the name)
        if "--worktrees-" in bare_name:
            continue

        # Find the actual project directory on disk
        project_path = projects_base / bare_name
        if not project_path.is_dir():
            continue

        worktrees = _find_project_worktrees(project_path)
        if not worktrees:
            continue

        main_cwd = str(project_path)

        for wt_path in worktrees:
            # Derive the CC dir name for this worktree
            wt_cc_name = local_prefix + bare_name + "--worktrees-" + wt_path.name
            wt_cc_dir = cc_projects_dir / wt_cc_name
            wt_cc_dir.mkdir(parents=True, exist_ok=True)
            wt_cwd = str(wt_path)

            # Copy and translate JSONL files
            for src in cc_dir.glob("*.jsonl"):
                dst = wt_cc_dir / src.name
                if dst.exists() and not dst.is_symlink():
                    # Don't overwrite the worktree's own conversations
                    continue

                # Read, translate cwd, write
                content = src.read_bytes()
                translated = content.replace(
                    f'"cwd":"{main_cwd}"'.encode(),
                    f'"cwd":"{wt_cwd}"'.encode(),
                )
                # Also translate cwd with trailing slash variants
                translated = translated.replace(
                    f'"cwd": "{main_cwd}"'.encode(),
                    f'"cwd": "{wt_cwd}"'.encode(),
                )
                dst.write_bytes(translated)
                count += 1
                if verbose:
                    print(f"  replicate to worktree: {bare_name}/{src.name} → {wt_path.name}")

            # Copy session lock directories (uuid dirs, not 'memory')
            for d in cc_dir.iterdir():
                if d.is_dir() and d.name != "memory" and not d.name.endswith(".jsonl"):
                    wt_d = wt_cc_dir / d.name
                    if not wt_d.exists():
                        shutil.copytree(d, wt_d)
                        if verbose:
                            print(f"  replicate dir: {bare_name}/{d.name} → {wt_path.name}")

    return count


# ---------------------------------------------------------------------------
# CC active detection
# ---------------------------------------------------------------------------


def is_cc_active_on_server(remote_host: str) -> bool:
    """Check if any Claude Code process is running on the server via SSH pgrep."""
    result = subprocess.run(
        ["ssh", remote_host, "pgrep", "-f", "claude"],
        capture_output=True,
        timeout=10,
    )
    return result.returncode == 0


def is_cc_active_locally() -> bool:
    """Check if Claude Code is active on this machine."""
    result = subprocess.run(["pgrep", "-f", "claude"], capture_output=True)
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Conflict notification
# ---------------------------------------------------------------------------


def notify_conflicts(conflicts: list[str]) -> None:
    """Fire a notification banner (macOS only) and append to the persistent conflict log."""
    summary = ", ".join(conflicts[:3])
    if len(conflicts) > 3:
        summary += f" (+{len(conflicts) - 3} more)"

    if _is_mac():
        subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "{summary}" with title "ai sync: conflict detected" '
                f'subtitle "Review .conflict files or check ~/.claude-sync-conflicts.log"',
            ],
            capture_output=True,
        )

    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    with open(CONFLICT_LOG, "a") as f:
        for conflict in conflicts:
            f.write(f"{ts} CONFLICT {conflict}\n")


# ---------------------------------------------------------------------------
# Handoff queue sync
# ---------------------------------------------------------------------------


def stage_handoff_files(
    staging_dir: Path,
    handoff_queue_dir: Path,
    verbose: bool,
    dry_run: bool,
) -> int:
    """Stage pending handoff files into the staging repo. Returns count of files staged."""
    pending_dir = handoff_queue_dir / "pending"
    if not pending_dir.exists():
        return 0

    count = 0
    staging_pending = staging_dir / _HANDOFF_STAGING_NAMESPACE / "pending"
    for src in sorted(pending_dir.glob("*.md")):
        dst = staging_pending / src.name
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        count += 1
        if verbose:
            print(f"  stage handoff: {src.name}")
    return count


def apply_handoff_files(
    staging_dir: Path,
    handoff_queue_dir: Path,
    verbose: bool,
    dry_run: bool,
) -> int:
    """Apply pending handoff files from staging repo to local queue. Returns count applied.

    Skips files already present in any state (pending / claimed / completed) to avoid
    re-adding handoffs that were already picked up locally.
    """
    staging_pending = staging_dir / _HANDOFF_STAGING_NAMESPACE / "pending"
    if not staging_pending.exists():
        return 0

    # Build a set of filenames already known in any state
    known: set[str] = set()
    for state in ("pending", "claimed", "completed"):
        state_dir = handoff_queue_dir / state
        if state_dir.exists():
            known.update(f.name for f in state_dir.glob("*.md"))

    count = 0
    local_pending = handoff_queue_dir / "pending"
    for src in sorted(staging_pending.glob("*.md")):
        if src.name in known:
            if verbose:
                print(f"  skip handoff (already known): {src.name}")
            continue
        if not dry_run:
            local_pending.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, local_pending / src.name)
        count += 1
        if verbose:
            print(f"  apply handoff: {src.name}")
    return count


# ---------------------------------------------------------------------------
# Config sync (hooks, statusline, settings.json with path translation)
# ---------------------------------------------------------------------------


def _get_remote_home(remote_host: str) -> str:
    """Derive the remote home directory from a user@host string.

    E.g. 'user@host' -> '/home/user', 'root@host' -> '/root'
    """
    if "@" in remote_host:
        user = remote_host.split("@")[0]
        return "/root" if user == "root" else f"/home/{user}"
    return "/root"


def _translate_settings_paths(content: str, direction: str, remote_host: str = "") -> str:
    """Translate absolute paths in settings.json between local and remote machines.

    direction: "to_local" -- replace remote paths with local
               "to_staging" -- normalize to remote (canonical) form in staging
    """
    local_home = str(Path.home())
    remote_home = _get_remote_home(remote_host) if remote_host else ""

    if not remote_home or local_home == remote_home:
        return content

    if direction == "to_staging":
        return content.replace(local_home, remote_home)
    elif direction == "to_local":
        return content.replace(remote_home, local_home)
    return content


def stage_config_files(
    staging_dir: Path,
    verbose: bool,
    dry_run: bool,
    remote_host: str = "",
) -> int:
    """Stage user-level CC config files into the staging repo. Returns count staged."""
    cc_dir = Path.home() / ".claude"
    staging_config = staging_dir / _CONFIG_STAGING_NAMESPACE
    count = 0

    # Portable files (no path translation needed)
    for rel_path in _CONFIG_SYNC_FILES:
        src = cc_dir / rel_path
        if not src.exists():
            continue
        dst = staging_config / rel_path
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        count += 1
        if verbose:
            print(f"  stage config: {rel_path}")

    # settings.json — translate paths to canonical (server) form
    settings_src = cc_dir / _SETTINGS_FILE
    if settings_src.exists():
        settings_dst = staging_config / _SETTINGS_FILE
        if not dry_run:
            settings_dst.parent.mkdir(parents=True, exist_ok=True)
            content = settings_src.read_text()
            translated = _translate_settings_paths(content, "to_staging", remote_host)
            settings_dst.write_text(translated)
        count += 1
        if verbose:
            print(f"  stage config: {_SETTINGS_FILE} (path-translated)")

    return count


def apply_config_files(
    staging_dir: Path,
    verbose: bool,
    dry_run: bool,
    remote_host: str = "",
) -> int:
    """Apply config files from staging repo to local ~/.claude/. Returns count applied."""
    cc_dir = Path.home() / ".claude"
    staging_config = staging_dir / _CONFIG_STAGING_NAMESPACE
    if not staging_config.exists():
        return 0

    count = 0

    # Portable files — copy if changed
    for rel_path in _CONFIG_SYNC_FILES:
        src = staging_config / rel_path
        if not src.exists():
            continue
        dst = cc_dir / rel_path
        if dst.exists() and file_hash(src) == file_hash(dst):
            continue
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            # Preserve executable permission for shell scripts
            if src.suffix == ".sh":
                dst.chmod(dst.stat().st_mode | 0o111)
        count += 1
        if verbose:
            print(f"  apply config: {rel_path}")

    # settings.json — translate paths from canonical to local
    settings_src = staging_config / _SETTINGS_FILE
    if settings_src.exists():
        settings_dst = cc_dir / _SETTINGS_FILE
        staged_content = settings_src.read_text()
        translated = _translate_settings_paths(staged_content, "to_local", remote_host)

        # Only write if content actually changed
        if settings_dst.exists():
            current = settings_dst.read_text()
            if current == translated:
                return count

        if not dry_run:
            settings_dst.parent.mkdir(parents=True, exist_ok=True)
            settings_dst.write_text(translated)
        count += 1
        if verbose:
            print(f"  apply config: {_SETTINGS_FILE} (path-translated)")

    return count


# ---------------------------------------------------------------------------
# git push helpers
# ---------------------------------------------------------------------------

_PUSH_TIMEOUT = 300  # 5 minutes — large first push can take time


def _push_to_remote(staging_dir: Path, verbose: bool) -> bool:
    """Push staging repo to remote. Retries after pull --rebase on rejection. Returns True on success."""
    res = subprocess.run(
        ["git", "push", "origin", "HEAD:main"],
        cwd=staging_dir,
        capture_output=True,
        text=True,
        timeout=_PUSH_TIMEOUT,
        env=_GIT_ENV,
    )
    if res.returncode == 0:
        return True

    # Non-fast-forward: pull --rebase then retry
    if "non-fast-forward" in res.stderr or "rejected" in res.stderr:
        # Abort any stale rebase left by a previous interrupted push
        rebase_merge = staging_dir / ".git" / "rebase-merge"
        rebase_apply = staging_dir / ".git" / "rebase-apply"
        if rebase_merge.exists() or rebase_apply.exists():
            subprocess.run(
                ["git", "rebase", "--abort"],
                cwd=staging_dir,
                capture_output=True,
                env=_GIT_ENV,
            )

        rebase = subprocess.run(
            ["git", "pull", "--rebase", "origin", "main"],
            cwd=staging_dir,
            capture_output=True,
            text=True,
            timeout=60,
            env=_GIT_ENV,
        )
        if rebase.returncode != 0:
            print(f"Error: git pull --rebase failed: {rebase.stderr}", file=sys.stderr)
            return False
        res2 = subprocess.run(
            ["git", "push", "origin", "HEAD:main"],
            cwd=staging_dir,
            capture_output=True,
            text=True,
            timeout=_PUSH_TIMEOUT,
            env=_GIT_ENV,
        )
        if res2.returncode == 0:
            return True
        print(f"Error pushing to remote after rebase: {res2.stderr}", file=sys.stderr)
        return False

    print(f"Error pushing to remote: {res.stderr}", file=sys.stderr)
    return False


# ---------------------------------------------------------------------------
# Pre-pull memory push
# ---------------------------------------------------------------------------


def _pre_pull_push_memories(cfg: SyncConfig, cc_projects_dir: Path, verbose: bool) -> None:
    """Stage and push local memory files before applying a pull.

    Without this, remote changes silently overwrite any local memory edits that
    haven't been pushed yet (the Stop-hook push only fires on session end). By
    pushing memories first, git can detect true conflicts and produce conflict
    markers instead of blindly overwriting.

    Non-fatal — if anything fails the pull still proceeds.
    """
    try:
        result = stage_project_files(
            staging_dir=cfg.staging_dir,
            cc_projects_dir=cc_projects_dir,
            local_prefix=cfg.local_prefix,
            memories_only=True,
            verbose=False,
            dry_run=False,
        )
        committed = git_commit_staged(
            staging_dir=cfg.staging_dir,
            source_machine=cfg.source_machine,
            project_names=result["project_names"],
            memory_count=result["memory_count"],
            jsonl_count=0,
            total_count=result["memory_count"],
        )
        if committed:
            _push_to_remote(cfg.staging_dir, verbose=False)
            if verbose:
                print(f"  pre-pull: pushed {result['memory_count']} local memory file(s)")
    except Exception:
        pass  # Non-fatal — pull proceeds without pre-push protection


# ---------------------------------------------------------------------------
# Dream safety guard
# ---------------------------------------------------------------------------


def _wait_for_dream_completion(verbose: bool) -> None:
    """Check if auto-dream is actively writing and wait for it to finish.

    Checks the memory-watch PID file to see if the daemon is running,
    then subscribes briefly to memory.dream.completed if a dream is active.
    Waits up to 30s, then proceeds regardless. Non-fatal if NATS unavailable.
    """
    import asyncio
    from .messaging import NATSClient

    try:
        pid_path = _pid_file_path("memory-watch")
        if not pid_path.exists():
            return  # No memory watcher running — no dream guard needed

        client = NATSClient()

        async def check():
            await client.connect()
            if not client.nc:
                return  # NATS unavailable — proceed without guard

            # Check for recent dream.started without a corresponding dream.completed
            # by publishing a probe and checking if memory watcher reports active dream
            # Simple approach: check if the memory watcher PID is alive and the
            # MEMORY.md mtime is recent (within last 5s = likely mid-dream)
            from pathlib import Path as _Path

            cc_projects = _Path.home() / ".claude" / "projects"
            recent_write = False
            if cc_projects.exists():
                now = time.time()
                for memory_file in cc_projects.rglob("MEMORY.md"):
                    try:
                        if now - memory_file.stat().st_mtime < 5.0:
                            recent_write = True
                            break
                    except Exception:
                        pass

            if not recent_write:
                await client.close()
                return

            if verbose:
                print("Dream write detected — waiting for completion (up to 30s)...")

            completed = asyncio.Event()

            async def on_completed(data):
                completed.set()

            # Subscribe briefly and wait
            sub = await client.nc.subscribe(
                "memory.dream.completed", cb=lambda msg: asyncio.ensure_future(on_completed({}))
            )
            try:
                await asyncio.wait_for(completed.wait(), timeout=30.0)
                if verbose:
                    print("Dream completed — proceeding with sync push.")
            except asyncio.TimeoutError:
                if verbose:
                    print("Dream wait timed out after 30s — proceeding anyway.")
            finally:
                await sub.unsubscribe()
                await client.close()

        asyncio.run(check())
    except Exception:
        pass  # Non-fatal — sync proceeds normally


# ---------------------------------------------------------------------------
# Public entry points: sync_push / sync_pull / sync_conflicts
# ---------------------------------------------------------------------------


def sync_push(flags: list[str]) -> int:
    """Push local CC data to the staging repo and server.

    Exit codes: 0 = success, 1 = fatal error, 2 = conflicts preserved
    """
    memories_only, dry_run, verbose, force, _ = _parse_flags(flags)

    try:
        cfg = load_sync_config()
    except Exception as e:
        print(f"Error loading sync config: {e}", file=sys.stderr)
        return 1

    if not dry_run:
        try:
            init_server_bare_repo(cfg.remote_host)
        except (subprocess.TimeoutExpired, OSError):
            pass  # Non-fatal — bare repo may already exist
        try:
            init_staging_repo(cfg.staging_dir, cfg.remote_url)
        except Exception as e:
            print(f"Error initializing staging repo: {e}", file=sys.stderr)
            return 1

    if not force and not dry_run:
        try:
            if is_cc_active_on_server(cfg.remote_host):
                print(
                    "WARNING: Claude Code is active on server. Sync aborted.\n"
                    "Exit the server CC session first, or use --force to sync anyway.",
                    file=sys.stderr,
                )
                return 1
        except subprocess.TimeoutExpired:
            print("WARNING: Could not reach server to check CC status. Proceeding.", file=sys.stderr)
        except Exception:
            pass  # Network unreachable — proceed silently

    # Dream safety guard — wait if auto-dream is actively writing MEMORY.md
    if not dry_run:
        _wait_for_dream_completion(verbose)

    cc_projects_dir = _cc_projects_dir()
    if not cc_projects_dir.exists():
        print(f"CC projects dir not found: {cc_projects_dir}", file=sys.stderr)
        return 1

    result = stage_project_files(
        staging_dir=cfg.staging_dir,
        cc_projects_dir=cc_projects_dir,
        local_prefix=cfg.local_prefix,
        memories_only=memories_only,
        verbose=verbose,
        dry_run=dry_run,
    )

    handoff_count = 0
    config_count = 0
    if not memories_only:
        handoff_count = stage_handoff_files(
            staging_dir=cfg.staging_dir,
            handoff_queue_dir=_handoff_queue_dir(),
            verbose=verbose,
            dry_run=dry_run,
        )
        config_count = stage_config_files(
            staging_dir=cfg.staging_dir,
            verbose=verbose,
            dry_run=dry_run,
            remote_host=cfg.remote_host,
        )

    if dry_run:
        staged = result["staged_files"]
        project_names = result["project_names"]
        print(f"Would sync {len(staged)} files across {len(project_names)} projects:")
        for src, dst in staged:
            print(f"  {src}")
        if handoff_count:
            print(f"Would sync {handoff_count} handoff file(s)")
        if config_count:
            print(f"Would sync {config_count} config file(s)")
        return 0

    committed = git_commit_staged(
        staging_dir=cfg.staging_dir,
        source_machine=cfg.source_machine,
        project_names=result["project_names"],
        memory_count=result["memory_count"],
        jsonl_count=result["jsonl_count"],
        total_count=len(result["staged_files"]) + handoff_count + config_count,
    )

    if not committed:
        if verbose:
            print("Nothing to commit — staging repo is up to date.")
        return 0

    if not _push_to_remote(cfg.staging_dir, verbose):
        return 1

    # Notify server to pull — fire-and-forget, non-fatal if NATS unavailable
    try:
        import asyncio as _asyncio
        from .messaging import NATSClient as _NATSClient

        _client = _NATSClient()
        _asyncio.run(_client.publish("sync.pull.requested", {"machine": cfg.source_machine}))
    except Exception:
        pass

    if verbose:
        print(
            f"Pushed {len(result['staged_files'])} files ({len(result['project_names'])} projects) to {cfg.remote_host}"
        )
    return 0


def sync_pull(flags: list[str]) -> int:
    """Pull from staging repo and apply to local CC projects dir.

    Exit codes: 0 = success, 1 = fatal error, 2 = conflicts preserved
    """
    memories_only, dry_run, verbose, force, prefer_remote = _parse_flags(flags)

    try:
        cfg = load_sync_config()
    except Exception as e:
        print(f"Error loading sync config: {e}", file=sys.stderr)
        return 1

    if not force and is_cc_active_locally():
        print(
            "WARNING: Claude Code is active locally. Sync will modify files CC may have loaded.\n"
            "Proceeding anyway — run `ai sync pull` after your session to be safe, or use --force.",
            file=sys.stderr,
        )

    if not dry_run:
        try:
            init_staging_repo(cfg.staging_dir, cfg.remote_url)
        except Exception as e:
            print(f"Error initializing staging repo: {e}", file=sys.stderr)
            return 1

        # Push local memory files first so git can detect conflicts on merge.
        # Without this, remote changes silently overwrite local edits that
        # haven't been pushed yet (Stop-hook push only fires on session end).
        _pre_pull_push_memories(cfg, _cc_projects_dir(), verbose)

        fetch = subprocess.run(
            ["git", "fetch", "origin"],
            cwd=cfg.staging_dir,
            capture_output=True,
            text=True,
            timeout=60,
            env=_GIT_ENV,
        )
        if fetch.returncode != 0:
            print(f"Error fetching from remote: {fetch.stderr}", file=sys.stderr)
            return 1

        # Merge — continue even if there are conflicts (they show as markers in files)
        subprocess.run(
            ["git", "merge", "origin/main", "--no-edit"],
            cwd=cfg.staging_dir,
            capture_output=True,
            text=True,
            env=_GIT_ENV,
        )

    cc_projects_dir = _cc_projects_dir()
    result = apply_pull_files(
        staging_dir=cfg.staging_dir,
        cc_projects_dir=cc_projects_dir,
        local_prefix=cfg.local_prefix,
        memories_only=memories_only,
        verbose=verbose,
        dry_run=dry_run,
        prefer_remote=prefer_remote,
    )

    if verbose and not dry_run:
        print(f"Applied {result['applied_count']} files to {cc_projects_dir}")

    if not memories_only:
        handoff_applied = apply_handoff_files(
            staging_dir=cfg.staging_dir,
            handoff_queue_dir=_handoff_queue_dir(),
            verbose=verbose,
            dry_run=dry_run,
        )
        if verbose and handoff_applied:
            print(f"Applied {handoff_applied} handoff file(s) to queue")

        config_applied = apply_config_files(
            staging_dir=cfg.staging_dir,
            verbose=verbose,
            dry_run=dry_run,
            remote_host=cfg.remote_host,
        )
        if verbose and config_applied:
            print(f"Applied {config_applied} config file(s) to ~/.claude/")

    if not dry_run and not memories_only:
        translate_history_jsonl(verbose=verbose)
        retranslate_project_jsonls(verbose=verbose)
        replicate_history_to_worktrees(verbose=verbose)

    if result["conflicts"]:
        if not dry_run:
            notify_conflicts(result["conflicts"])
        return 2

    return 0


def sync_conflicts(flags: list[str]) -> int:
    """List unresolved .conflict files and recent log entries."""
    cc_projects_dir = _cc_projects_dir()

    conflict_files: list[str] = []
    if cc_projects_dir.exists():
        conflict_files = sorted(
            str(f)
            for f in cc_projects_dir.rglob("*")
            if f.is_file() and (f.suffix == ".conflict" or (f.name.startswith("conflict-") and f.suffix == ".jsonl"))
        )

    if conflict_files:
        print("Unresolved conflict files:")
        for f in conflict_files:
            print(f"  {f}")
    else:
        print("No unresolved conflict files.")

    if CONFLICT_LOG.exists():
        lines = CONFLICT_LOG.read_text().splitlines()
        recent = lines[-20:]
        if recent:
            print("\nRecent conflict log entries:")
            for line in recent:
                print(f"  {line}")

    return 2 if conflict_files else 0


def _pid_file_path(name: str) -> Path:
    """Return path for a daemon PID file under ~/.ai-cli/."""
    return Path.home() / ".ai-cli" / f"{name}.pid"


def _acquire_pid_file(name: str) -> bool:
    """Write PID file if no other live process holds it. Returns True if acquired."""
    pid_path = _pid_file_path(name)
    if pid_path.exists():
        try:
            old_pid = int(pid_path.read_text().strip())
            # Check if process is alive
            os.kill(old_pid, 0)
            return False  # Another instance is running
        except (ValueError, ProcessLookupError, PermissionError):
            pass  # Stale PID file
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()))
    return True


def _release_pid_file(name: str) -> None:
    """Remove PID file."""
    pid_path = _pid_file_path(name)
    try:
        pid_path.unlink(missing_ok=True)
    except Exception:
        pass


def sync_watch(flags: list[str]) -> int:
    """Subscribe to sync.pull.requested events and auto-run ai sync pull.

    Intended to run as a long-lived background process (e.g. inside a tmux pane).
    Uses PID file guard to prevent duplicate instances.
    Exit codes: 0 = clean stop, 1 = NATS unavailable, 2 = already running
    """
    import asyncio
    from .messaging import NATSClient

    verbose = "--verbose" in flags

    if not _acquire_pid_file("sync-watch"):
        print("ai sync watch is already running.", file=sys.stderr)
        return 2

    client = NATSClient()

    async def on_pull_requested(data: dict):
        machine = data.get("machine", "unknown")
        print(f"[sync-watch] sync.pull.requested from {machine} — running ai sync pull --force")
        result = sync_pull(["--force"])
        if result == 0:
            print("[sync-watch] pull complete")
        elif result == 2:
            print("[sync-watch] pull complete (conflicts preserved — resolve with: ai sync conflicts)")
        else:
            print(f"[sync-watch] pull failed (exit {result})", file=sys.stderr)

    async def run():
        if not client.nc:
            await client.connect()
        if not client.nc:
            print("NATS unavailable — cannot start sync watcher.", file=sys.stderr)
            return False
        if verbose:
            print("[sync-watch] connected to NATS, watching sync.pull.requested")
        await client.subscribe_durable("sync.pull.requested", "sync-watch", on_pull_requested)
        return True

    print("ai sync watch — listening for sync.pull.requested (Ctrl+C to stop)")
    try:
        ok = asyncio.run(run())
    except KeyboardInterrupt:
        ok = True
    finally:
        _release_pid_file("sync-watch")
    return 0 if ok else 1
