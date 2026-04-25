"""
ai sync push/pull — bidirectional CC session data sync via git staging repo.

Scope: CC session data only — conversation JSONL files, memory files
(MEMORY.md + memory/*.md), and history.jsonl. All files live in
~/.claude/projects/ which is not a git repo. Sync is the only mechanism
that moves this data between machines.

What sync does NOT touch:
  - Git-tracked files (config, hooks, statusline, handoff queue) — use git.
  - ~/.claude/settings.json or other machine-specific CC config.

Architecture:
  Mac: ~/.claude-sync-staging/  (working git repo, remote = ssh://{user}@{host}/{home}/.claude-sync-staging.git)
  Server: ~/.claude-sync-staging.git  (bare repo, receives pushes from both machines)
  Server: ~/.claude-sync-staging/     (working checkout, remote = file://{home}/.claude-sync-staging.git)

Push flow: stage local ~/.claude/projects/ files → git commit → git push to bare repo
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
CONFLICT_DIR = Path.home() / ".claude-sync-conflicts"

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
    from .config import load_config

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


def _parse_flags(flags: list[str]) -> tuple[bool, bool, bool, bool, bool]:
    """Parse common sync flags. Returns (memories_only, dry_run, verbose, force, prefer_remote)."""
    return (
        "--memories-only" in flags or "-m" in flags,
        "--dry-run" in flags or "-n" in flags,
        "--verbose" in flags or "-v" in flags,
        "--force" in flags or "-f" in flags,
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


def _wt_name_from_bare_name(bare_name: str) -> Optional[str]:
    """Return the worktree session name from a bare CC dir name, or None if not a worktree dir.

    E.g. "sergei--worktrees-sw-5" → "sw-5", "sergei" → None.
    """
    marker = "--worktrees-"
    idx = bare_name.find(marker)
    if idx == -1:
        return None
    return bare_name[idx + len(marker) :]


def _jsonl_custom_title(path: Path) -> Optional[str]:
    """Return the customTitle field from a JSONL file, or None if absent or unreadable.

    Reads the full file line-by-line so compacted JSONL files (which start with
    file-history-snapshot records before any customTitle line) are handled correctly.
    """
    import json as _json

    try:
        with path.open("rb") as fh:
            for raw_line in fh:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    obj = _json.loads(raw_line)
                    title = obj.get("customTitle", "")
                    if title:
                        return title
                except (_json.JSONDecodeError, ValueError):
                    continue
    except Exception:
        pass
    return None


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
    # Conflict files go to CONFLICT_DIR, never the CC project dirs or staging repo.
    if path.name.startswith("conflict-") and path.suffix == ".jsonl":
        return False
    if path.suffix == ".conflict":
        return False
    # Non-markdown files in memory/ subdirs (e.g. .consolidate-lock) are machine-local
    # state and must never cross machines — syncing them triggers spurious conflict
    # detections and can block consolidation on the remote.
    if "memory" in path.parts and path.suffix != ".md":
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

        # If this is a worktree CC dir, only stage JSONL files whose customTitle
        # matches the worktree session name. Staging foreign JSONL files would
        # replicate session contamination to the other machine via sync.
        wt_name = _wt_name_from_bare_name(bare_name)

        for src in cc_dir.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(cc_dir)
            if not should_sync_file(rel, memories_only):
                continue

            # Worktree-dir JSONL guard: skip files that don't belong here.
            if wt_name and is_jsonl_file(rel) and len(rel.parts) == 1:
                title = _jsonl_custom_title(src)
                if title != wt_name:
                    continue  # Foreign or untitled session — belongs in main project dir

            dst = staging_project_dir / rel

            if not dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                # Skip copy if content is identical — avoids creating new git blobs
                # for unchanged files, which is the primary driver of pack bloat.
                if not dst.exists() or file_hash(src) != file_hash(dst):
                    shutil.copy2(src, dst)
                else:
                    continue  # unchanged — exclude from staged_files too

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
    # Repack loose objects periodically to keep pack files lean.
    # --auto only runs when git's internal heuristics decide it's needed
    # (loose objects > gc.auto threshold, typically 6700).
    _git(["gc", "--auto", "--quiet"], staging_dir)
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

    from .config import _get_projects_dir

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


def purge_phantom_history_entries(verbose: bool = False) -> int:
    """Remove phantom worktree entries from history.jsonl.

    ``replicate_history_to_worktrees`` (now removed from the sync pipeline)
    used to copy every main-project history entry into worktree-path variants
    on each pull.  These phantom entries cause the conversation picker to show
    main-project conversations inside worktree sessions, then fail to load them
    because the JSONL files live in the main CC directory, not the worktree CC
    directory.

    A phantom entry is a history.jsonl line whose ``project`` field is a
    worktree path AND whose ``sessionId`` also appears under a main-project
    path.  Genuine worktree-initiated conversations (created by CC with the
    worktree CWD) are not phantoms — their UUIDs only exist under the worktree
    path.

    Returns the number of entries removed.
    """
    import json as _json

    history_path = Path.home() / ".claude" / "history.jsonl"
    if not history_path.exists():
        return 0

    lines = history_path.read_text().strip().split("\n")

    # Collect UUIDs that appear in *main-project* entries (not worktrees).
    main_project_uuids: set[str] = set()
    for line in lines:
        if not line:
            continue
        try:
            d = _json.loads(line)
            proj = d.get("project", "")
            if proj and ".worktrees/" not in proj and "--worktrees-" not in proj:
                session_id = d.get("sessionId", "")
                if session_id:
                    main_project_uuids.add(session_id)
        except Exception:
            pass

    kept: list[str] = []
    removed = 0
    for line in lines:
        if not line:
            continue
        try:
            d = _json.loads(line)
            proj = d.get("project", "")
            session_id = d.get("sessionId", "")
            if (".worktrees/" in proj or "--worktrees-" in proj) and session_id in main_project_uuids:
                removed += 1
                continue
        except Exception:
            pass
        kept.append(line)

    if removed:
        history_path.write_text("\n".join(kept) + "\n" if kept else "")
        if verbose:
            print(f"  purge phantom history: {removed} phantom worktree entries removed")

    return removed


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
    # Files where staging should be overwritten with local to prevent re-detection.
    # Populated during divergence handling; committed to staging after apply loop.
    staging_to_overwrite: list[tuple[Path, Path]] = []  # (local_src, staging_dst)
    # Staging files already written directly (e.g. LLM merge) that need git commit.
    staging_to_commit: list[Path] = []

    for staging_project_dir in sorted(staging_dir.iterdir()):
        if not staging_project_dir.is_dir() or staging_project_dir.name.startswith("."):
            continue

        bare_name = staging_project_dir.name
        cc_dir_name = denormalize_project_name(bare_name, local_prefix)
        cc_project_dir = cc_projects_dir / cc_dir_name

        # If this is a worktree CC dir, only apply JSONL files whose customTitle
        # matches the worktree session name. This prevents contamination from
        # spreading via the staging repo if the push side ever stages foreign files.
        wt_name = _wt_name_from_bare_name(bare_name)

        for src in staging_project_dir.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(staging_project_dir)
            if not should_sync_file(rel, memories_only):
                continue

            # Worktree-dir JSONL guard: skip files that don't belong here.
            if wt_name and is_jsonl_file(src) and len(rel.parts) == 1:
                title = _jsonl_custom_title(src)
                if title != wt_name:
                    continue  # Foreign or untitled session — skip

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
                        conflict_path = CONFLICT_DIR / bare_name / conflict_name
                        if not dry_run:
                            conflict_path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src, conflict_path)
                        conflict_str = f"jsonl  {bare_name}/{rel.name} — remote saved as {conflict_path}"
                        conflicts.append(conflict_str)
                        print(
                            f"JSONL CONFLICT: {bare_name}/{rel.name} — remote version saved as {conflict_name}",
                            file=sys.stderr,
                        )
                        # Queue staging update: overwrite staging with local so future
                        # pulls don't re-detect the same divergence and fire again.
                        if dst.exists():
                            staging_to_overwrite.append((dst, src))
            else:
                # Memory or other text file — skip if identical, then check for git conflict markers
                if dst.exists() and file_hash(src) == file_hash(dst):
                    continue

                content = src.read_text(errors="replace")
                has_conflict_markers = "<<<<<<<" in content and ">>>>>>>" in content

                if has_conflict_markers:
                    # Attempt LLM auto-merge before saving a conflict file.
                    merged = _llm_merge_memory_conflict(content, src.name) if not dry_run else None
                    if merged is not None:
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        dst.write_text(merged, encoding="utf-8")
                        src.write_text(merged, encoding="utf-8")  # Update staging file
                        staging_to_commit.append(src)  # Track for git commit+push
                        applied_count += 1
                        if verbose:
                            print(f"  auto-merged: {bare_name}/{rel}")
                    else:
                        conflict_path = CONFLICT_DIR / bare_name / rel.with_suffix(rel.suffix + ".conflict")
                        if not dry_run:
                            conflict_path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src, conflict_path)
                        conflict_str = f"memory {bare_name}/{rel} — .conflict file written"
                        conflicts.append(conflict_str)
                        print(
                            f"CONFLICT: {bare_name}/{rel} — resolve manually with: ai sync resolve",
                            file=sys.stderr,
                        )
                        # Queue staging update: overwrite the marker-laden staging file with
                        # the local version so future pulls don't re-detect the same conflict.
                        if dst.exists():
                            staging_to_overwrite.append((dst, src))
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
        # Note: clean_worktree_cc_dirs is not called here automatically.
        # It is a one-time cleanup tool exposed as `ai sync cleanup`.
        # Running it on every pull would remove files placed by repair_worktree_cc_dir
        # before the user has had a chance to extend them by resuming the conversation.

    return {
        "conflicts": conflicts,
        "applied_count": applied_count,
        "staging_to_overwrite": staging_to_overwrite,
        "staging_to_commit": staging_to_commit,
    }


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

    from .config import _get_projects_dir

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

            # Worktree name is the directory name (e.g. "sw-5" from .worktrees/sw-5)
            wt_session_name = wt_path.name

            # Copy and translate JSONL files — only those belonging to this worktree's session.
            # Match criterion: customTitle field must equal the worktree session name.
            # This is set by `ai c N` via --name flag and is the authoritative identifier.
            # cwd-based matching is intentionally removed: translated cwd copies could
            # false-positive match, and titles are a more reliable discriminator.
            for src in cc_dir.glob("*.jsonl"):
                dst = wt_cc_dir / src.name
                if dst.exists() and not dst.is_symlink():
                    # Don't overwrite the worktree's own conversations
                    continue

                # Only replicate sessions explicitly named for this worktree
                if _jsonl_custom_title(src) != wt_session_name:
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

            # Copy session lock directories only for conversations we replicated
            replicated_uuids = {f.stem for f in wt_cc_dir.glob("*.jsonl")}
            for d in cc_dir.iterdir():
                if d.is_dir() and d.name != "memory" and not d.name.endswith(".jsonl"):
                    if d.name not in replicated_uuids:
                        continue  # Only copy lock dirs for replicated conversations
                    wt_d = wt_cc_dir / d.name
                    if not wt_d.exists():
                        shutil.copytree(d, wt_d)
                        if verbose:
                            print(f"  replicate dir: {bare_name}/{d.name} → {wt_path.name}")

    return count


def clean_worktree_cc_dirs(
    cc_projects_dir: Path,
    local_prefix: str,
    dry_run: bool = False,
    verbose: bool = False,
) -> tuple[int, int]:
    """Remove stale JSONL copies and orphan lock dirs from worktree CC directories.

    Two categories of stale files are removed:

    1. JSONL duplicates — a JSONL file in a worktree CC dir whose UUID also exists
       in the corresponding main project CC dir AND whose customTitle does not match
       the worktree session name.  These are leftover copies created by old versions
       of ``_replicate_to_worktrees`` before the customTitle filter was introduced.
       Removing them is safe: the originals remain in the main project CC dir.

    2. Orphan lock directories — UUID-named directories in a worktree CC dir with no
       corresponding ``{uuid}.jsonl`` file in the same dir.  CC treats these as
       active sessions and hides the conversation from the /resume picker.

    Returns (removed_jsonl_count, removed_lock_count).
    """
    removed_jsonl = 0
    removed_lock = 0

    if not cc_projects_dir.exists():
        return 0, 0

    for cc_dir in sorted(cc_projects_dir.iterdir()):
        if not cc_dir.is_dir():
            continue
        bare_name = normalize_project_path(cc_dir.name, local_prefix)
        if bare_name is None:
            continue
        wt_name = _wt_name_from_bare_name(bare_name)
        if not wt_name:
            continue  # Not a worktree CC dir

        # Locate the corresponding main project CC dir
        main_bare = bare_name.split("--worktrees-")[0]
        main_cc_dir = cc_projects_dir / (local_prefix + main_bare)
        main_uuids = {f.stem for f in main_cc_dir.glob("*.jsonl")} if main_cc_dir.is_dir() else set()

        # Track which UUIDs have a valid .jsonl in the worktree CC dir (after cleanup)
        valid_uuids: set[str] = set()

        for jsonl_path in sorted(cc_dir.glob("*.jsonl")):
            title = _jsonl_custom_title(jsonl_path)
            if title == wt_name:
                valid_uuids.add(jsonl_path.stem)
                continue  # Correct session — keep

            # Foreign or untitled: remove only if the UUID also exists in the main CC dir
            # (i.e. it is a copy, not a standalone worktree-native conversation) AND
            # the worktree file is not larger than the main copy (which would mean the
            # conversation was resumed and extended in the worktree — keep those).
            if jsonl_path.stem in main_uuids:
                main_copy = (main_cc_dir / jsonl_path.name) if main_cc_dir.is_dir() else None
                wt_size = jsonl_path.stat().st_size
                main_size = main_copy.stat().st_size if (main_copy and main_copy.exists()) else 0
                if wt_size > main_size:
                    # Worktree copy is larger — conversation was continued here, keep it
                    valid_uuids.add(jsonl_path.stem)
                else:
                    if not dry_run:
                        jsonl_path.unlink()
                    removed_jsonl += 1
                    if verbose:
                        print(f"  rm stale copy: {bare_name}/{jsonl_path.name} (title={title!r}, exists in main)")
            else:
                # Unique to this worktree — keep it
                valid_uuids.add(jsonl_path.stem)

        # Remove orphan lock directories (UUID dirs with no matching .jsonl)
        for item in sorted(cc_dir.iterdir()):
            if not item.is_dir() or item.name == "memory":
                continue
            # Only consider UUID-looking names (32+ hex chars with dashes)
            if item.name in valid_uuids:
                continue  # Has a matching .jsonl — keep
            if not dry_run:
                shutil.rmtree(item)
            removed_lock += 1
            if verbose:
                print(f"  rm orphan lock: {bare_name}/{item.name}/")

    return removed_jsonl, removed_lock


def repair_worktree_cc_dir(
    project_name: str,
    wt_name: str,
    cc_projects_dir: Path,
    local_prefix: str,
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """Copy all JSONL conversations from a main project CC dir to a worktree CC dir.

    Used when conversations created in the main project context (e.g. on another
    machine without a worktree) need to be accessible from a worktree CC session.

    Translates the ``cwd`` field from the main project path to the worktree path.
    Skips files that already exist in the worktree CC dir (non-destructive).
    Removes orphan lock directories before copying.

    Returns the number of files copied.
    """
    from .config import _get_projects_dir

    projects_base = _get_projects_dir()
    project_path = projects_base / project_name
    if not project_path.is_dir():
        print(f"Error: project '{project_name}' not found at {project_path}", file=sys.stderr)
        return 0

    wt_path = project_path / ".worktrees" / wt_name
    if not wt_path.is_dir():
        print(f"Error: worktree '{wt_name}' not found at {wt_path}", file=sys.stderr)
        return 0

    main_cc_name = local_prefix + project_name
    main_cc_dir = cc_projects_dir / main_cc_name
    if not main_cc_dir.is_dir():
        print(f"Error: no CC dir for '{project_name}' at {main_cc_dir}", file=sys.stderr)
        return 0

    wt_cc_name = local_prefix + project_name + "--worktrees-" + wt_name
    wt_cc_dir = cc_projects_dir / wt_cc_name
    if not dry_run:
        wt_cc_dir.mkdir(parents=True, exist_ok=True)

    main_cwd = str(project_path)
    wt_cwd = str(wt_path)

    # Remove orphan lock dirs first so they don't block resumed conversations
    existing_jsonl = {f.stem for f in wt_cc_dir.glob("*.jsonl")} if wt_cc_dir.is_dir() else set()
    for item in sorted(wt_cc_dir.iterdir()) if wt_cc_dir.is_dir() else []:
        if not item.is_dir() or item.name == "memory":
            continue
        if item.name not in existing_jsonl:
            if not dry_run:
                shutil.rmtree(item)
            if verbose:
                print(f"  rm orphan lock: {wt_cc_name}/{item.name}/")

    copied = 0
    for src in sorted(main_cc_dir.glob("*.jsonl")):
        dst = wt_cc_dir / src.name
        if dst.exists() and not dst.is_symlink():
            if verbose:
                print(f"  skip (exists): {src.name}")
            continue

        content = src.read_bytes()
        translated = content.replace(
            f'"cwd":"{main_cwd}"'.encode(),
            f'"cwd":"{wt_cwd}"'.encode(),
        )
        translated = translated.replace(
            f'"cwd": "{main_cwd}"'.encode(),
            f'"cwd": "{wt_cwd}"'.encode(),
        )
        if not dry_run:
            dst.write_bytes(translated)
        copied += 1
        if verbose:
            print(f"  copy: {src.name} → {wt_cc_name}/")

    if not dry_run:
        print(f"repair-worktree: {copied} conversations copied to {wt_cc_name}")
    else:
        print(f"repair-worktree (dry-run): would copy {copied} conversations to {wt_cc_name}")
    return copied


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
# LLM memory merge
# ---------------------------------------------------------------------------


def _llm_merge_memory_conflict(conflict_content: str, filename: str) -> Optional[str]:
    """Use Gemini Flash to resolve git conflict markers in a memory markdown file.

    Returns merged content without conflict markers, or None if merge fails
    (no API key, network error, or LLM left markers in the output).
    """
    import os

    api_key = os.environ.get("GOOGLE_API_KEY_TIER_1") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None

    try:
        from google import genai  # type: ignore

        client = genai.Client(api_key=api_key)
        prompt = (
            f"You are resolving a git merge conflict in a memory file named '{filename}'.\n"
            "The file has git conflict markers (<<<<<<, =======, >>>>>>>).\n"
            "Merge both versions, preserving ALL information from both sides.\n"
            "Rules:\n"
            "- Keep ALL entries from both the HEAD (<<<<<<) and incoming (>>>>>>>) sections.\n"
            "- For duplicate keys with different values, prefer the HEAD version (local machine).\n"
            "- Remove ALL conflict markers from the output.\n"
            "- Preserve original structure, headings, and markdown formatting.\n"
            "- Output ONLY the merged file content. No explanation, no preamble.\n\n"
            f"{conflict_content}"
        )
        response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
        merged = response.text
        if not merged or "<<<<<<<" in merged or ">>>>>>>" in merged:
            return None  # LLM failed to resolve all markers
        return merged
    except Exception:
        return None


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


def _remote_newer_files(staging_dir: Path) -> list[str]:
    """Return relative paths of tracked files where remote has a newer commit than local HEAD.

    Fetches origin first. Files that are new (untracked) are skipped — they have
    no remote version to conflict with. Returns an empty list if remote cannot
    be reached (guard is non-fatal on network error).
    """
    fetch = _git(["fetch", "origin"], staging_dir, check=False)
    if fetch.returncode != 0:
        return []  # Can't reach remote — skip guard

    # Get files that are modified relative to HEAD (M = modified tracked file)
    status = _git(["status", "--porcelain"], staging_dir, check=False)
    if status.returncode != 0:
        return []

    modified: list[str] = []
    for line in status.stdout.decode().splitlines():
        if len(line) < 4:
            continue
        xy = line[:2]
        # Working-tree modified (unstaged) — "staged" shows in index col, but
        # stage_project_files uses shutil.copy2 without git-add, so changes
        # appear as unstaged modifications or new untracked files.
        # ' M' = modified in working tree; 'MM' = modified in both.
        # 'AM' / ' M' etc. — skip '??' (untracked/new).
        if xy == "??":
            continue  # New file — no remote version to conflict with
        path = line[3:].strip()
        if path:
            modified.append(path)

    if not modified:
        return []

    newer: list[str] = []
    for rel in modified:
        remote_ts_raw = (
            _git(
                ["log", "-1", "--format=%ct", "origin/main", "--", rel],
                staging_dir,
                check=False,
            )
            .stdout.decode()
            .strip()
        )

        if not remote_ts_raw:
            continue  # File not on remote yet — no conflict

        local_ts_raw = (
            _git(
                ["log", "-1", "--format=%ct", "HEAD", "--", rel],
                staging_dir,
                check=False,
            )
            .stdout.decode()
            .strip()
        )

        remote_ts = int(remote_ts_raw)
        local_ts = int(local_ts_raw) if local_ts_raw else 0

        if remote_ts > local_ts:
            newer.append(rel)

    return newer


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

    if dry_run:
        staged = result["staged_files"]
        project_names = result["project_names"]
        print(f"Would sync {len(staged)} files across {len(project_names)} projects:")
        for src, dst in staged:
            print(f"  {src}")
        return 0

    if not force:
        newer = _remote_newer_files(cfg.staging_dir)
        if newer:
            files_list = "\n".join(f"  {f}" for f in newer)
            print(
                f"Error: remote has newer content for {len(newer)} file(s):\n"
                f"{files_list}\n"
                "Run 'ai sync pull' first, or use --force / -f to overwrite.",
                file=sys.stderr,
            )
            return 1

    committed = git_commit_staged(
        staging_dir=cfg.staging_dir,
        source_machine=cfg.source_machine,
        project_names=result["project_names"],
        memory_count=result["memory_count"],
        jsonl_count=result["jsonl_count"],
        total_count=len(result["staged_files"]),
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

    if not dry_run and not memories_only:
        translate_history_jsonl(verbose=verbose)
        retranslate_project_jsonls(verbose=verbose)
        purge_phantom_history_entries(verbose=verbose)

    if not dry_run:
        # Commit and push any staging files that were modified during apply:
        # - staging_to_overwrite: local won a divergence, copy local → staging then commit
        # - staging_to_commit: LLM-merged content already written to staging file directly
        # Without this push, the remote staging repo still has the old content, so the
        # next pull re-fetches it and re-detects the same conflict, firing again.
        staging_to_commit: list[Path] = list(result.get("staging_to_commit", []))
        overwrites = result.get("staging_to_overwrite", [])
        if overwrites:
            for local_src, staging_dst in overwrites:
                shutil.copy2(local_src, staging_dst)
            staging_to_commit.extend(staging_dst for _, staging_dst in overwrites)
        if staging_to_commit:
            git_add = subprocess.run(
                ["git", "add"] + [str(p) for p in staging_to_commit],
                cwd=cfg.staging_dir,
                capture_output=True,
                env=_GIT_ENV,
            )
            if git_add.returncode == 0:
                commit = subprocess.run(
                    ["git", "commit", "-m", "sync: update staging after conflict resolution"],
                    cwd=cfg.staging_dir,
                    capture_output=True,
                    env=_GIT_ENV,
                )
                if commit.returncode == 0:
                    _push_to_remote(cfg.staging_dir, verbose=False)

    if result["conflicts"]:
        if not dry_run:
            notify_conflicts(result["conflicts"])
        return 2

    return 0


def sync_conflicts(flags: list[str]) -> int:
    """List unresolved .conflict files and recent log entries."""
    conflict_files: list[str] = []
    # New location: ~/.claude-sync-conflicts/
    if CONFLICT_DIR.exists():
        conflict_files = sorted(
            str(f)
            for f in CONFLICT_DIR.rglob("*")
            if f.is_file() and (f.suffix == ".conflict" or (f.name.startswith("conflict-") and f.suffix == ".jsonl"))
        )
    # Legacy location: CC project dirs (pre-fix)
    cc_projects_dir = _cc_projects_dir()
    if cc_projects_dir.exists():
        legacy = sorted(
            str(f)
            for f in cc_projects_dir.rglob("*")
            if f.is_file() and (f.suffix == ".conflict" or (f.name.startswith("conflict-") and f.suffix == ".jsonl"))
        )
        if legacy:
            conflict_files.extend(legacy)

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


def sync_resolve(flags: list[str]) -> int:
    """Scan and resolve accumulated conflict files.

    - JSONL conflict backups: delete them (local already won via staging-overwrite).
    - Memory .conflict files: attempt LLM merge, apply result, clean up.
    - Cascading .conflict.conflict files: delete (bug artifacts from old code).

    Exit codes: 0 = all resolved, 1 = some memory files could not be auto-merged
    """
    dry_run = "--dry-run" in flags or "-n" in flags
    verbose = "--verbose" in flags or "-v" in flags

    try:
        cfg = load_sync_config()
    except Exception as e:
        print(f"Error loading sync config: {e}", file=sys.stderr)
        return 1

    cc_projects_dir = _cc_projects_dir()
    jsonl_deleted = 0
    mem_merged = 0
    mem_failed = 0
    artifacts_deleted = 0

    def _resolve_memory_conflict_file(f: Path, local_cc_file: Optional[Path]) -> str:
        """Attempt to resolve one .conflict file. Returns 'merged', 'failed', or 'no_markers'."""
        content = f.read_text(errors="replace")
        if "<<<<<<<" not in content or ">>>>>>>" not in content:
            if not dry_run:
                f.unlink()
            return "no_markers"
        merged = _llm_merge_memory_conflict(content, f.stem)
        if merged:
            if not dry_run:
                if local_cc_file and local_cc_file.exists():
                    local_cc_file.write_text(merged, encoding="utf-8")
                f.unlink()
            return "merged"
        return "failed"

    # Process new location: ~/.claude-sync-conflicts/
    if CONFLICT_DIR.exists():
        for f in sorted(CONFLICT_DIR.rglob("*")):
            if not f.is_file() or f.name == ".DS_Store":
                continue

            if f.name.startswith("conflict-") and f.suffix == ".jsonl":
                if verbose:
                    print(f"  rm (jsonl backup): {f.relative_to(CONFLICT_DIR)}")
                if not dry_run:
                    f.unlink()
                jsonl_deleted += 1

            elif ".conflict.conflict" in f.name:
                if verbose:
                    print(f"  rm (cascading artifact): {f.relative_to(CONFLICT_DIR)}")
                if not dry_run:
                    f.unlink()
                artifacts_deleted += 1

            elif f.suffix == ".conflict":
                rel = f.relative_to(CONFLICT_DIR)
                parts = rel.parts
                if len(parts) < 2:
                    continue
                bare_name = parts[0]
                rel_in_project = Path(*parts[1:]).with_suffix("")
                cc_dir_name = denormalize_project_name(bare_name, cfg.local_prefix)
                local_cc_file = cc_projects_dir / cc_dir_name / rel_in_project
                outcome = _resolve_memory_conflict_file(f, local_cc_file)
                if verbose:
                    print(f"  {outcome}: {rel}")
                if outcome == "merged":
                    mem_merged += 1
                elif outcome == "failed":
                    mem_failed += 1
                else:
                    artifacts_deleted += 1

    # Process legacy location: .conflict files inside CC projects dir
    if cc_projects_dir.exists():
        for f in sorted(cc_projects_dir.rglob("*")):
            if not f.is_file() or f.name == ".DS_Store":
                continue

            if f.name.startswith("conflict-") and f.suffix == ".jsonl":
                if verbose:
                    print(f"  rm (legacy jsonl backup): {f.name}")
                if not dry_run:
                    f.unlink()
                jsonl_deleted += 1

            elif ".conflict.conflict" in f.name:
                if verbose:
                    print(f"  rm (legacy cascading): {f.name}")
                if not dry_run:
                    f.unlink()
                artifacts_deleted += 1

            elif f.suffix == ".conflict":
                local_cc_file = f.with_suffix("")
                outcome = _resolve_memory_conflict_file(f, local_cc_file)
                if verbose:
                    print(f"  {outcome}: {f.name}")
                if outcome == "merged":
                    mem_merged += 1
                elif outcome == "failed":
                    mem_failed += 1
                else:
                    artifacts_deleted += 1

    # Remove empty conflict subdirectories
    if not dry_run and CONFLICT_DIR.exists():
        for d in sorted(CONFLICT_DIR.iterdir(), reverse=True):
            if d.is_dir():
                try:
                    d.rmdir()
                except OSError:
                    pass

    prefix = "[dry-run] " if dry_run else ""
    total = jsonl_deleted + artifacts_deleted + mem_merged + mem_failed
    if total == 0:
        print(f"{prefix}No conflict files found.")
    else:
        if jsonl_deleted:
            print(f"{prefix}Removed {jsonl_deleted} stale JSONL conflict backup(s).")
        if artifacts_deleted:
            print(f"{prefix}Removed {artifacts_deleted} cascading conflict artifact(s).")
        if mem_merged:
            print(f"{prefix}LLM-merged {mem_merged} memory conflict file(s).")
        if mem_failed:
            print(
                f"{prefix}Could not auto-merge {mem_failed} memory file(s) — "
                "no API key or LLM error. Resolve manually: ai sync conflicts",
                file=sys.stderr,
            )

    return 1 if mem_failed > 0 else 0


def _pid_file_path(name: str) -> Path:
    """Return path for a daemon PID file under XDG state dir."""
    from .config import get_xdg_state_home

    return get_xdg_state_home() / f"{name}.pid"


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
