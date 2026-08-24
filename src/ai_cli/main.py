import contextlib
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

import click

# Module aliases used by the call sites throughout this file. Tests can
# patch on the source module (``patch("ai_cli.config.load_config")``) and
# the dynamic attribute lookup here picks up the mock. The underscore
# prefix avoids clashing with the ``config: dict`` parameter name used by
# several helpers in this file.
from . import config as _config
from . import direnv_setup as _direnv_setup
from . import handoff as _handoff
from . import iterm2 as _iterm2
from . import process_manager as _process_manager
from . import session as _session
from . import session_script as _session_script
from . import transport as _transport
from . import tunnel as _tunnel

# Backwards-compat re-exports so historical ``patch("ai_cli.main.<name>")``
# call sites in the test suite keep working.
from .config import (  # noqa: F401
    DEFAULT_CONFIG,
    WORKTREE_DIR,
    ProjectPrefixError,
    _find_project_dir,
    _get_handoff_queue_dir,
    _get_main_project_dir,
    _get_main_project_name,
    _get_project_prefix_by_name,
    _get_project_registry_path,
    _get_projects_dir,
    _migrate_xdg_dir,
    get_current_project_name,
    get_project_aliases,
    get_session_map,
    get_session_map_path,
    get_xdg_cache_home,
    get_xdg_config_home,
    get_xdg_state_home,
    load_config,
    load_project_registry,
    register_project,
    resolve_project_prefix_by_name,
    save_session_map,
    validate_registry_completeness,
)
from .git_repair import (
    GitProbeError,
    _git_env,
    detect_missing_tracked_symlinks,
    detect_phantom_deleted_files,
    pull_rebase_autostash,
    repair_bare_worktree_config,
    unmerged_paths,
)
from .handoff import (  # noqa: F401
    _claim_handoff_for_signal,
    _find_best_handoff,
    _format_handoff_summary,
    _log_handoff_event,
    check_handoff,
    check_handoff_project,
    claim_handoff,
    complete_handoff,
    post_handoff,
)
from .iterm2 import (  # noqa: F401
    _DEFAULT_ITERM2_CONFIG,
    _assign_iterm2_color_slot,
    _configure_tmux_for_iterm2,
    _current_pane_tty,
    _emit_iterm2_profile_setup,
    _is_iterm2,
    _iterm2_palette,
    _iterm2_session_type,
    _iterm2_state_dir,
    _iterm_pane_tty_for_tmux_session,
    _load_iterm2_config,
    _release_iterm2_color_slot,
    _resolve_iterm2_config,
    _set_iterm2_name_by_tty,
)
from .process_manager import (  # noqa: F401
    _cmd_quota_watch_start,
    _cmd_quota_watch_status,
    _cmd_quota_watch_stop,
    _cmd_signal_watch_start,
    _cmd_signal_watch_status,
    _cmd_signal_watch_stop,
    _ensure_circusd,
)
from .process_probe import StartTimeMatch, probe_for
from .session import (  # noqa: F401
    _AI_SESSION_RE,
    _checkpoint_to_chat_uuid,
    _convert_checkpoint_to_chat,
    _find_latest_gemini_uuid,
    _get_chat_last_message_timestamp,
    _resolve_is_remote,
    _sweep_stale_iterm2_profiles,
    build_session_name,
    cleanup_stale_sessions,
    cleanup_worktree,
    create_worktree,
    detect_repo_root,
    find_next_index,
    find_recent_session,
    get_latest_gemini_session_id,
    get_project_prefix,
    resolve_session,
)
from .session_script import get_engine_script  # noqa: F401
from .transport import (  # noqa: F401
    _ensure_tailscale_up,
    _ensure_vpn_watcher,
    _is_vpn_active,
    _maybe_stop_vpn_watcher,
    _monotonic,
    _run_transport_loop,
    _write_transport_state,
)
from .tunnel import (  # noqa: F401
    _cmd_cdp_start,
    _cmd_cdp_status,
    _cmd_cdp_stop,
    _cmd_tunnel_start,
    _cmd_tunnel_status,
    _cmd_tunnel_stop,
    _ensure_nats_tunnel,
    _find_chrome_binary,
)

# MAINTENANCE: when editing ai-cli, also update:
#   - docs/tools/ai-cli-usage.md (usage reference, session naming, transport, auto-resume)
#   - README.md (if CLI interface changes)
#   - Code comments in this file (especially around session naming, resume logic, mosh/transport)
#   - CLAUDE.md ai-cli deploy note (reinstall in 3 places: Mac uv tool, server uv tool, extra_venvs)


# --- Helpers ---


def _exec_with_direnv(project_root: Path, command: list[str]) -> None:
    """Replace this process with ``command``, under direnv when that is possible.

    direnv is an enhancement, never a precondition for starting a session.  Three
    cases are handled explicitly, because ``direnv exec`` fails *closed*: on an
    unapproved (or erroring) ``.envrc`` it exits non-zero and never runs the
    command at all.  Exec'ing it unconditionally therefore turned a mere trust
    prompt into a launch that died with only "…/.envrc is blocked" on stderr and
    no engine ever started.

    - No ``.envrc`` anywhere above the target: nothing to load, exec directly.
      This also lets sessions start on hosts without direnv installed.
    - ``.envrc`` present and usable: exec through ``direnv exec`` as before.
    - ``.envrc`` present but blocked/erroring: warn with the approval command and
      exec the engine anyway, without the project environment.  Losing project
      env vars is a degraded session; losing the session entirely is not
      recoverable from inside the tool.
    - ``.envrc`` present but direnv not installed: skip the approval hint (it
      would fail anyway) and exec the engine directly.

    The decision itself lives in :func:`_direnv_prefix` so the tmux ``--once``
    launch path applies the identical policy instead of its own hardcode.
    """
    prefix = _direnv_prefix(project_root)
    if prefix:
        # FileNotFoundError falls through to a direct exec below
        with contextlib.suppress(FileNotFoundError):
            os.execvp(prefix[0], [*prefix, *command])

    try:
        os.execvp(command[0], command)
    except FileNotFoundError:
        print(f"Error: {command[0]} not found on PATH.", file=sys.stderr)
        sys.exit(1)


def _direnv_prefix(project_root: Path) -> list[str]:
    """``["direnv", "exec", <root>]`` when direnv can actually run there, else ``[]``.

    The single place the "direnv is an enhancement, never a precondition" policy
    described in :func:`_exec_with_direnv` is decided, so every launch path — bare
    exec and the tmux ``--once`` variants alike — makes the same call instead of
    hardcoding ``direnv exec`` and failing closed with exit 127 on a host that
    simply does not have direnv installed.
    """
    envrc = _find_envrc(project_root)
    if envrc is not None and _direnv_env_usable(project_root):
        return ["direnv", "exec", str(project_root)]
    if envrc is not None and _direnv_installed():
        print(
            f"Warning: direnv could not load {envrc} — starting without the project environment.\n"
            f"  Approve it with:  direnv allow {envrc.parent}",
            file=sys.stderr,
        )
    return []


def _find_envrc(start: Path) -> "Path | None":
    """Return the nearest ``.envrc`` at or above ``start``, or None if there is none.

    direnv searches parent directories, so a repo without its own ``.envrc`` can
    still inherit one; checking only ``start`` would skip a usable environment.
    """
    try:
        resolved = start.resolve()
    except OSError:
        resolved = start
    for directory in (resolved, *resolved.parents):
        candidate = directory / ".envrc"
        if candidate.is_file():
            return candidate
    return None


def _direnv_installed() -> bool:
    """True when direnv is available on PATH."""
    return shutil.which("direnv") is not None


def _direnv_env_usable(project_root: Path) -> bool:
    """True when direnv can actually load the environment for ``project_root``.

    Delegates to the one portable probe so this and the worktree auto-approval in
    ``session.py`` cannot disagree. The previous ``direnv exec <dir> true`` probe
    always failed on Windows -- ``true`` is not an executable there -- which made
    this report "unusable" on a perfectly healthy setup and produced the endless
    approval nagging. See :func:`ai_cli.direnv_setup.envrc_loads`.
    """
    return _direnv_setup.envrc_loads(project_root)


def _session_shell_or_exit() -> str:
    """The interpreter tmux should run the session script with, or exit(1).

    Delegates the preference order to :func:`session_script.resolve_session_shell`
    (zsh first, bash as fallback) so the launcher and the template it generates
    can never disagree about which shell the session runs under.

    Exiting here is the point of the helper. ``tmux new-session`` returns 0 even
    when the pane's interpreter does not exist: the pane dies on exec, tmux tears
    the session down, and the attach that follows prints a bare ``[exited]`` with
    nothing to act on. An explicit error names the missing dependency instead.
    """
    shell = _session_script.resolve_session_shell()
    if shell is None:
        print(
            "Error: no usable shell for the tmux session — neither "
            f"{' nor '.join(_session_script.SESSION_SHELL_PREFERENCE)} was found on PATH.\n"
            "  Install one of them and retry (e.g. `sudo dnf install zsh` / `sudo apt install zsh`).",
            file=sys.stderr,
        )
        sys.exit(1)
    return shell


def _cc_project_dir(cwd: Path) -> Path:
    """Return the ``~/.claude/projects`` directory Claude Code uses for ``cwd``.

    Claude Code slugifies the absolute cwd by replacing every **non-alphanumeric**
    character with ``-`` (``/home/me/my_proj`` -> ``-home-me-my-proj``).  Note the
    underscore: an earlier version of this logic used a ``sed 's|[/.]|-|g'``
    equivalent that replaced only ``/`` and ``.``, which silently computed the
    wrong directory for any path containing ``_`` and made session resume miss.
    """
    return Path.home() / ".claude" / "projects" / re.sub(r"[^a-zA-Z0-9]", "-", str(cwd))


def _find_cc_session_by_title(cwd: Path, title: str) -> "Path | None":
    """Return the newest CC transcript under ``cwd`` whose ``customTitle`` is ``title``.

    Claude Code's ``--continue`` picks the most recently modified conversation in
    the project directory, so callers ``touch`` the returned file to make the
    session named for this launch win.  ``--resume <uuid>`` is deliberately not
    used: it opens a search picker rather than resuming directly.
    """
    project_dir = _cc_project_dir(cwd)
    if not project_dir.is_dir():
        return None
    try:
        candidates = sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for path in candidates:
        try:
            with path.open("rb") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        record = json.loads(raw)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    found = record.get("customTitle", "")
                    if found:
                        # Only the first titled record matters — later ones repeat it.
                        if found == title:
                            return path
                        break
        except OSError:
            continue
    return None


def _cc_record_liveness(record: dict, proc_dir: Path | None = None) -> str:
    """Classify a session record as ``"live"``, ``"abandoned"``, ``"gone"``, or ``"unproven"``.

    Deliberately fails OPEN: a pid that cannot be verified counts as ``"unproven"``
    and does not block a launch, matching this registry's best-effort contract
    (see :func:`_cc_session_is_live`) -- a dead or unverifiable process is the same
    class of unreliability as a missing or malformed directory.  Note the
    asymmetry with the fleet's *worktree* liveness probe, which fails CLOSED
    (unreadable => live) because it guards a deletion; this one only decides
    whether a transcript may be reused, so the safe default is the opposite.  Do
    not "harmonize" the two.

    ``"abandoned"`` is the answer for a process that is *present but not running
    this session*, which pid existence alone cannot distinguish from a session
    the operator is using: exiting Claude Code does not reliably end its
    process, and one observed on a real host sat in state ``T`` (stopped) with
    ``/proc/<pid>`` fully populated after a ``SIGTERM`` that returned 0.  A
    stopped process never resumes on its own, so calling it live reserved the
    session name forever.  See :func:`_reclaim_abandoned_cc_session`.

    Every question about the process itself goes through
    :func:`ai_cli.process_probe.probe_for`, which is what makes this work off
    Linux: reading ``/proc`` here directly could answer neither the state nor the
    identity question on macOS or Windows, so no session was ever classified
    ``"abandoned"`` there and a recycled pid still blocked its name.

    The deferred import below is guarded for the same fail-open reason.  ``ai c``
    can replace this process's own installation mid-run (``ai update --force`` ->
    ``uv tool install`` rewrites ``site-packages/ai_cli``), so a lazily-imported
    module can vanish between interpreter start and this call.  A missing module
    is exactly the "cannot verify" case this function already promises to tolerate,
    so it must cost a liveness check rather than the whole command -- AI-CLI-205,
    where two launches died here while three siblings survived only because they
    never reached this branch.  The real defect is the mid-run reinstall, fixed
    separately in :func:`_auto_update_if_stale`; this guard keeps a future window
    from being fatal.
    """
    try:
        from .session_adopt import _pid_is_live
    except ImportError:
        return "unproven"

    try:
        pid = int(cast(Any, record.get("pid")))
    except (TypeError, ValueError):
        return "unproven"
    if pid <= 0:
        return "unproven"
    if not _pid_is_live(pid, proc_dir):
        return "gone"
    probe = probe_for(proc_dir)
    # Checked before ``procStart``: a stopped process is abandoned whether or not
    # the record carries enough to identify it.
    if probe.is_abandoned(pid):
        return "abandoned"
    match = probe.start_time_match(pid, record.get("procStart"))
    if match is StartTimeMatch.UNRECORDED:
        # The pid runs and nothing refutes its identity; keep the historical refusal.
        return "live"
    if match is StartTimeMatch.MATCH:
        return "live"
    # A recycled pid, or a start time this platform cannot read: the recorded process
    # may well be gone, but the record is NOT pruned -- a `procStart` in some unit
    # this code does not recognise would fail to match too, and deleting a live
    # session's record misleads every other tool that reads this registry.  Not
    # blocking the launch is enough to fix the bug.
    return "unproven"


def _prune_dead_cc_session_record(entry: Path) -> None:
    """Remove a session record whose process is provably gone.

    Claude Code does not reliably delete its own ``<pid>.json`` on kill or crash
    and nothing ages the file out, so without this every abandoned record made its
    session name unusable until a human found and deleted the file.  A losing race
    with another process, or a read-only file, must not affect the caller.
    """
    with contextlib.suppress(OSError):
        entry.unlink()


def _reclaim_abandoned_cc_session(entry: Path, record: dict) -> bool:
    """End a session process that stopped instead of exiting, and prune its record.

    What it found and what it did are always reported: silently launching a
    differently-named session is the defect this exists to fix, so silently
    reclaiming one would only move the surprise.

    The process is signalled only when the record can prove *which* process it
    is -- ``procStart`` must match the process now holding the pid.  Killing on a
    pid alone would eventually kill a stranger that inherited a recycled pid, and
    a record outliving its process is this registry's known failure mode.

    The probe is resolved with no injected ``/proc`` directory: the termination is
    real, so reading anything but the real system would only make the confirmation
    dishonest.
    """
    probe = probe_for()
    pid = int(record.get("pid", 0))
    state = probe.state(pid) or "?"
    name = str(record.get("name") or "")
    label = f"'{name}'" if name else (str(record.get("sessionId") or "")[:8] or "<unnamed>")
    identified = probe.start_time_match(pid, record.get("procStart")) is StartTimeMatch.MATCH
    if not identified:
        print(
            f"Claude Code session {label} recorded pid {pid}, which is present but not running "
            f"(state {state}). The record cannot prove that process is this session, so it is left "
            f"alone -- the name is not treated as in use.",
            file=sys.stderr,
        )
        return False
    if probe.end_process(pid):
        _prune_dead_cc_session_record(entry)
        print(
            f"Claude Code session {label} did not exit: pid {pid} was present in state {state}, "
            f"not gone. Ended it and everything it wrapped, pruned the stale record, and resuming "
            f"this session.",
            file=sys.stderr,
        )
        return True
    print(
        f"Claude Code session {label} did not exit: pid {pid} is present in state {state} and "
        f"could not be ended. Resuming the session anyway; end that process by hand with "
        f"{probe.manual_end_hint(pid)}.",
        file=sys.stderr,
    )
    return False


def _cc_session_is_live(transcript: Path, proc_dir: Path | None = None) -> tuple[bool, int | str | None]:
    """Return whether Claude Code currently has ``transcript``'s UUID registered.

    Claude Code records each running local session in
    ``~/.claude/sessions/<pid>.json``.  The registry is best-effort: a missing,
    unreadable, or malformed directory must preserve the historical launch
    behavior rather than block a session launch.  A record only counts as live
    when its pid is still running *and* that process's start time matches the
    record's -- an abandoned record otherwise blocked its session name forever.
    Records for dead pids are pruned as the directory is walked.

    A pid that is present but *stopped* is not live either: it is reclaimed (this
    session's own record only) so the resume can proceed, because a stopped
    process never ends by itself and would otherwise hold the name for good.
    """
    sessions_dir = Path.home() / ".claude" / "sessions"
    if not sessions_dir.is_dir():
        return False, None
    live: tuple[bool, int | str | None] = (False, None)
    try:
        entries = sorted(sessions_dir.glob("*.json"))
        for entry in entries:
            try:
                with entry.open(encoding="utf-8") as fh:
                    record = json.load(fh)
            except (OSError, json.JSONDecodeError, ValueError, TypeError, AttributeError):
                continue
            if not isinstance(record, dict):
                continue
            state = _cc_record_liveness(record, proc_dir)
            if state == "gone":
                _prune_dead_cc_session_record(entry)
                continue
            this_session = record.get("sessionId") == transcript.stem
            if state == "abandoned":
                # Scoped to the session being resumed: this walk visits every
                # record, so ending someone else's abandoned process here would
                # turn each launch into a fleet-wide reaper.
                if this_session:
                    _reclaim_abandoned_cc_session(entry, record)
                continue
            if state == "live" and this_session and not live[0]:
                live = (True, record.get("pid"))
    except OSError:
        pass
    return live


def _cc_live_session_warning(title: str, pid: int | str | None) -> str:
    """Explain why a named Claude Code transcript cannot be continued safely."""
    pid_detail = f" (pid {pid})" if pid is not None else ""
    return (
        f"Cannot continue session '{title}': its Claude Code session is still running{pid_detail}.\n"
        "Run `claude agents` to find and attach to it, or use `--fork-session` to branch a copy.\n"
        "Starting a fresh session instead to avoid resuming an unrelated transcript."
    )


def _bare_engine_command(
    engine: str,
    ai_name: str,
    target_root: Path,
    uuid: str | None,
    gemini_cmd: str,
    sandbox_flag: str,
    extra_args: list[str],
    resume: bool = False,
) -> list[str]:
    """Build the argv for a bare (no-tmux) engine launch.

    Bare mode is a real session, not a degraded one: it gets the same
    ``--name``/resume treatment the tmux session script applies, so ``ai c 1``
    behaves consistently on machines where tmux is unavailable or opted out via
    ``[session] use_tmux = false``.  The tmux path's auto-resume *loop* is the
    only thing bare mode genuinely cannot offer — nothing supervises the process
    to restart it.
    """
    if engine == "c":
        command = ["claude"]
        if not _is_root():
            command.append("--dangerously-skip-permissions")
        command += ["--name", ai_name]
        # Resume this session's own prior conversation when one exists. --continue
        # picks by mtime, so touch the matching transcript to make it the newest.
        matched = _find_cc_session_by_title(target_root, ai_name)
        if matched is not None:
            is_live, pid = _cc_session_is_live(matched)
            if is_live:
                print(_cc_live_session_warning(ai_name, pid), file=sys.stderr)
            else:
                try:
                    os.utime(matched, None)
                    command.append("--continue")
                except OSError:
                    pass
        return command + extra_args

    if engine == "p":
        # Pi scopes its saved sessions to the current worktree.  Continuing here
        # therefore resumes this session's prior conversation without needing a
        # provider-specific UUID registry.
        command = ["pi"]
        if resume:
            command.append("--continue")
        return [*command, "--name", ai_name, *extra_args]

    if engine == "cx":
        # Codex does not offer a launch-time session-name option. Its resume
        # command scopes --last to the current working directory by default,
        # which is this session's isolated worktree.
        command = ["codex"]
        if resume:
            command += ["resume", "--last"]
        return [*command, *extra_args]

    command = [*shlex.split(gemini_cmd), "-y", sandbox_flag]
    if uuid:
        command += ["-r", uuid]
    else:
        command += ["-i", f"/resume load {ai_name}"]
    return command + extra_args


def _is_root() -> bool:
    """Return True if the current process has elevated / root privileges.

    On Windows checks for Administrator via ctypes; on POSIX checks uid == 0.
    """
    if sys.platform == "win32":
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.getuid() == 0


def _announce_worktree_isolation(worktree_path: Path, created: bool) -> None:
    """Report the actual worktree outcome before entering the session."""
    if created:
        print(
            f"Creating isolated worktree for this session: {worktree_path}\n"
            f"  (disable with -W/--no-worktree, or [worktree] enabled = false in config.toml)",
            file=sys.stderr,
        )
    else:
        print(f"Using existing worktree: {worktree_path}", file=sys.stderr)


def _find_aicli_project_path(config: dict) -> "Path | None":
    """Locate the ai-cli-utils source tree regardless of cwd.

    Priority:
    1. [deploy] project_path from config
    2. Package __file__ location (dev-editable install)
    """
    cfg_path = config.get("deploy", {}).get("project_path", "")
    if cfg_path:
        return Path(cfg_path).expanduser()
    try:
        import importlib.util

        spec = importlib.util.find_spec("ai_cli")
        if spec and spec.origin:
            pkg_dir = Path(spec.origin).parent  # …/ai_cli/
            for candidate in (pkg_dir.parent, pkg_dir.parent.parent):
                pyproject = candidate / "pyproject.toml"
                if pyproject.exists() and "ai-cli-utils" in pyproject.read_text():
                    return candidate
    except Exception:
        pass
    # cwd fallback — valid when the user is already in the project directory.
    # Check the project name specifically to avoid matching projects that merely
    # depend on ai-cli-utils.
    cwd = Path.cwd()
    cwd_pyproject = cwd / "pyproject.toml"
    if cwd_pyproject.exists():
        content = cwd_pyproject.read_text()
        if re.search(r'^name\s*=\s*["\']ai-cli-utils["\']', content, re.MULTILINE):
            return cwd
    return None


def _deploy_cc_config_files(project_path: Path) -> None:
    """Copy bundled CC config files from the package data dir to ~/.claude/.

    Writes plain files, replacing any pre-existing symlinks. These files are
    owned by ai-cli-utils and must not be managed by ai sync or tracked in any
    project git repo.
    """
    data_dir = project_path / "src" / "ai_cli" / "data"
    cc_dir = Path.home() / ".claude"

    # Files to deploy: (source relative to data_dir, dest relative to ~/.claude/)
    # NOTE (AIH-164 audit F-02/AD-2): `data/statusline-command.sh` is the standalone-`ai`
    # fallback and MUST be kept in sync with the canonical ai-harness copy
    # (`ai-harness/.claude/statusline-command.sh`), which owns the statusline and wins via a
    # symlink whenever ai-harness is installed (the `dst.is_symlink()` skip below). Re-sync with:
    #   cp ~/projects/ai-harness/.claude/statusline-command.sh src/ai_cli/data/statusline-command.sh
    deployable = [
        ("statusline-command.sh", "statusline-command.sh"),
    ]

    for src_name, dst_rel in deployable:
        src = data_dir / src_name
        if not src.exists():
            continue
        dst = cc_dir / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        # Skip if already managed as a symlink (ai-harness install.sh owns it)
        if dst.is_symlink():
            continue
        import shutil as _shutil

        if dst.exists():
            dst.unlink()
        _shutil.copy2(src, dst)
        if src.suffix == ".sh":
            dst.chmod(dst.stat().st_mode | 0o755)


_PEER_UPDATE_WAIT_SECONDS = 90.0
_PEER_UPDATE_POLL_SECONDS = 0.25


def _await_peer_update(
    lock_path: Path,
    stamp_file: Path,
    current_hash: str,
    timeout: float | None = None,
    poll: float | None = None,
) -> bool:
    """Wait for a peer's in-flight update, and report whether we must re-exec.

    Returns True when the peer advanced the stamp to ``current_hash`` -- our
    imported modules are then stale and our installed files were rewritten
    underneath us, so the caller must re-exec.  Returns False when the wait times
    out, warning on stderr: continuing is not safe, but blocking a launch forever
    on a stuck peer is worse, and a re-exec here could land while `uv tool install`
    is still rewriting the `ai` entry point.  A bounded wait is the lesser evil and
    is why this is not simply an unconditional re-exec.

    ``timeout``/``poll`` default to the module constants at CALL time, not as
    default arguments, so a test can patch the bound down instead of busy-spinning
    the real 90 seconds.
    """
    if timeout is None:
        timeout = _PEER_UPDATE_WAIT_SECONDS
    if poll is None:
        poll = _PEER_UPDATE_POLL_SECONDS
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not lock_path.exists():
            break
        time.sleep(poll)
    try:
        advanced = stamp_file.exists() and stamp_file.read_text().strip() == current_hash
    except OSError:
        advanced = False
    if advanced:
        return True
    print(
        "Warning: another `ai` process is still updating ai-cli-utils; continuing with the\n"
        "  currently-loaded version. If this command fails with an unexpected ImportError,\n"
        "  re-run it once the other update finishes.",
        file=sys.stderr,
    )
    return False


UPDATE_VERBOSE_ENV = "AI_CLI_UPDATE_VERBOSE"


def _update_verbose_requested() -> bool:
    """Whether the operator asked to see the whole update transcript at launch.

    The session-launch auto-update is quiet by default; ``AI_CLI_UPDATE_VERBOSE=1``
    is the escape hatch for the case the quieting exists to serve — checking
    whether a source change actually reached the installed build.
    """
    return os.environ.get(UPDATE_VERBOSE_ENV, "").strip().lower() not in ("", "0", "false", "no", "off")


def _packaged_source_paths(project_path: Path) -> list[Path]:
    """Return every file that ends up inside the installed wheel, sorted.

    ``pyproject.toml`` (metadata, dependencies, entry points) plus the whole
    ``src/`` tree, which is what ``[tool.hatch.build.targets.wheel]`` packages —
    modules and the bundled ``data/`` artifacts alike. Nothing else in the
    repository can change the installed code, so nothing else belongs here.
    """
    pyproject = project_path / "pyproject.toml"
    if not pyproject.is_file():
        return []
    paths = [pyproject]
    src_dir = project_path / "src"
    if src_dir.is_dir():
        paths += [
            p
            for p in src_dir.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix not in (".pyc", ".pyo")
        ]
    # Sort on the POSIX-form relative path so the order is identical on every
    # platform; rglob's own order is filesystem-defined and would change the hash.
    return sorted(paths, key=lambda p: p.relative_to(project_path).as_posix())


def _installed_source_fingerprint(project_path: Path) -> str | None:
    """Hash the packaged source, or ``None`` if it cannot be read.

    This is the staleness signal for the session-launch auto-update, replacing a
    comparison against the repository's ``HEAD``. A commit pointer is the wrong
    question in both directions: it advances for commits that change nothing that
    ships (a docs edit, a task-tracker sync), which made every unrelated commit
    cost the next launch a full pull-and-reinstall, and it does not move at all
    for an uncommitted edit under ``src/``, which is the single most common way
    the installed build actually goes stale while someone is testing a change.
    """
    digest = hashlib.sha256()
    paths = _packaged_source_paths(project_path)
    if not paths:
        return None
    for path in paths:
        try:
            data = path.read_bytes()
        except OSError:
            # An unreadable input means the fingerprint cannot be trusted. Say so
            # rather than hashing a partial tree, which would read as "changed"
            # forever and reinstall on every launch.
            return None
        rel = path.relative_to(project_path).as_posix()
        # Length-delimit each record so a rename cannot collide with a content edit.
        digest.update(f"{rel}\0{len(data)}\0".encode())
        digest.update(data)
    return digest.hexdigest()


def _auto_update_if_stale(config: dict) -> bool:
    """Reinstall at session launch only when the packaged source has changed, and
    report whether the invoking launcher must restart.

    The install is not editable — ``uv tool install`` copies the package into its
    own venv — so a source change really does need a reinstall before it can take
    effect, and ``ai update`` defeats uv's cache with a unique ``.post<timestamp>``
    version to guarantee it. That guarantee is preserved; what changes here is
    *when* it is spent and how loud it is. The trigger is a content fingerprint of
    the packaged files (see ``_installed_source_fingerprint``), and the update runs
    quietly, reporting one line instead of the whole pull-and-install transcript.

    A successful reinstall rewrote this process's own installed tree, so the
    caller must re-exec: True says so. Every other outcome returns False.
    """
    project_path = _find_aicli_project_path(config)
    if project_path is None or not (project_path / "pyproject.toml").exists():
        return False
    fingerprint = _installed_source_fingerprint(project_path)
    if fingerprint is None:
        return False
    stamp_file = _config.get_xdg_state_home() / "last_install_fingerprint.txt"
    if stamp_file.exists() and stamp_file.read_text().strip() == fingerprint:
        return False
    # Serialize concurrent workers with an exclusive create-only lockfile.
    # O_CREAT|O_EXCL is atomic on both POSIX and Windows.
    lock_path = stamp_file.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except OSError:
        # Another process claimed the update -- which means it is running
        # `uv tool install`, tearing down and rewriting THIS process's own
        # installed tree while we run.  Returning False here said "no restart
        # needed" and let the caller carry on, so every not-yet-executed deferred
        # import was aimed at files being deleted.  AI-CLI-an5r/c5b33d04 fixed
        # this for the lock WINNER (it re-execs); the loser was left behind on the
        # adjacent line.  Wait for the peer instead, then re-exec if it advanced
        # the stamp.  Terminating by construction: the re-exec'd process sees the
        # fresh stamp and early-returns above, so it never reaches this branch.
        return _await_peer_update(lock_path, stamp_file, fingerprint)
    try:
        # Re-check after acquiring lock — another process may have just updated.
        if stamp_file.exists() and stamp_file.read_text().strip() == fingerprint:
            return False
        # The stamp is deliberately NOT written here, before the install. Doing so
        # made a concurrent launch's early return above see the new fingerprint
        # while `uv tool install` was still rewriting the files that launch imports
        # from -- the same unsafe skip AI-CLI-an5r closed for the lock loser. It is
        # written only once the install has actually succeeded, below, which also
        # means a failed install is retried on the next launch rather than being
        # remembered as done.
        verbose = _update_verbose_requested()
        ai_bin = shutil.which("ai") or "ai"
        cmd = [ai_bin, "update", "--force"] + ([] if verbose else ["--quiet"])
        result = subprocess.run(cmd, cwd=project_path, capture_output=not verbose, text=True, check=False)
        if result.returncode != 0:
            # A failed update is not automatically a harmless no-op. `uv tool
            # install --force` tears the environment down before rebuilding it, so
            # an interrupted run can leave it with no packages — and reporting that
            # as "continuing with current version" sent users on to the next
            # command with a tool that could no longer start at all. Check the
            # environment before characterising the failure.
            self_venv = _running_uv_tool_venv()
            if self_venv is not None and not _tool_env_can_import(self_venv):
                print(
                    "Error: auto-update failed AND left the installation broken — "
                    f"{self_venv} can no longer import ai_cli.\n"
                    f"  Repair it with:\n"
                    f"    uv tool install -e {project_path} --force --reinstall",
                    file=sys.stderr,
                )
            else:
                print(
                    "Warning: auto-update failed; the existing installation is intact and still in use.",
                    file=sys.stderr,
                )
            # Quieting the success path must never quiet a failure: with the
            # transcript captured rather than streamed, it is the only diagnostic
            # there is, so it is replayed in full here.
            captured = f"{result.stdout or ''}{result.stderr or ''}".rstrip()
            if captured:
                print(captured, file=sys.stderr)
            return False
        # Do not make a failed installation look current.  The caller must re-exec
        # after a successful installation because this process still has the old
        # template generator imported.
        stamp_file.write_text(fingerprint)
        summary = (result.stdout or "").strip()
        if summary:
            print(summary)
        warnings = (result.stderr or "").strip()
        if warnings:
            print(warnings, file=sys.stderr)
        return True
    finally:
        lock_path.unlink(missing_ok=True)


def trigger_background_update():
    state_file = _config.get_xdg_state_home() / "update_check.json"
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
    uv_bin = shutil.which("uv")
    if not uv_bin:
        # Warn but do not raise. This is an unrequested background check firing during
        # some other command, so a hard failure here would kill the foreground command
        # the user actually asked for (the AIH bug: bare "uv" + Popen without a shell
        # -> Windows CreateProcess raises FileNotFoundError -> `ai c 1` traceback).
        # Silent-skip would hide a permanently broken auto-updater, so warn on stderr.
        print(
            "Warning: 'uv' not found on PATH — skipping background update check.",
            file=sys.stderr,
        )
        return
    # Pass the resolved absolute path, not the bare name — see above.
    upgrade_cmd = [uv_bin, "tool", "upgrade", "ai-cli-utils"]
    if _should_use_uv_link_mode_copy(uv_bin):
        upgrade_cmd.append("--link-mode=copy")
    try:
        subprocess.Popen(
            upgrade_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        # Same reasoning: report, never propagate, so the foreground command survives.
        print(f"Warning: background update check failed to launch: {exc}", file=sys.stderr)


def _pkg_version_string() -> str:
    try:
        from importlib.metadata import version as _pkg_version

        return _pkg_version("ai-cli-utils")
    except Exception:
        return "unknown"


def _engine_script_from_meta(meta: dict) -> str:
    """Regenerate a session's launch script from its persisted metadata.

    ``session-meta-<tmux_session>.json`` (written by the wrapper at start) carries
    every parameter ``get_engine_script`` needs, so regeneration is faithful — no
    fragile reconstruction. Shared by ``refresh-template`` and ``write-stable-script``.
    """
    # Resolve through the session_script module (not the name imported into main) so
    # tests patching ``ai_cli.session_script.get_engine_script`` take effect.
    return _session_script.get_engine_script(
        engine=meta["engine"],
        ai_name=meta["ai_name"],
        session=meta["session"],
        prefix=meta["prefix"],
        project_prefix=meta["project_prefix"],
        session_id_uuid=meta.get("session_id_uuid") or None,
        sandbox=meta.get("sandbox", False),
        worktree_dir=meta.get("worktree_dir") or None,
        notify=meta.get("notify", False),
        is_remote=meta.get("is_remote", False),
        project_name=meta.get("project_name", ""),
        iterm2_slot=meta.get("iterm2_slot") or None,
        iterm2_cfg=meta.get("iterm2_cfg") or None,
        config_reload_idle_secs=meta.get("config_reload_idle_secs", 90),
        gemini_cmd=meta.get("gemini_cmd", "gemini"),
    )


def _write_launch_script_if_changed(script_path: Path, script: str) -> bool:
    """Write a session launch script, leaving it alone when it is already current.

    The script's mtime is the hot-reload signal every running wrapper polls, so an
    unconditional rewrite makes every live session ``exec`` a reload even when the
    regenerated bytes are identical (AI-CLI-129). Returns True if the file changed.
    """
    script_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if script_path.read_text(encoding="utf-8") == script and (
            os.name == "nt" or script_path.stat().st_mode & 0o777 == 0o700
        ):
            return False
    except OSError:
        pass
    script_path.write_text(script, encoding="utf-8")
    script_path.chmod(0o700)
    return True


def _decode_tmux_stderr(raw: str | bytes) -> str:
    """Decode captured tmux stderr without hiding the original diagnostic."""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw


# Bound on how often the live-session template refresh may run. Each run spawns a
# ``tmux list-sessions`` subprocess plus a write and a chmod per live session, so a
# caller that re-enters it in a loop turns it into a subprocess storm against the
# tmux server and the state dir (AI-CLI-129). A real `ai update` fires it once, so
# a budget of a few per minute is far above any legitimate use.
_REFRESH_BURST_LIMIT = 20
_REFRESH_BURST_WINDOW_SECS = 60.0
_REFRESH_CALL_TIMES: list[float] = []
_REFRESH_BURST_REPORTED_AT = 0.0


def _refresh_within_burst_budget() -> bool:
    """Record a refresh attempt; return False (loudly) once the burst budget is spent.

    Tracked both in-process and in the state dir, so a loop inside one process and a
    loop that re-execs ``ai update`` are both caught. A cooldown marker makes
    rejected calls no-ops rather than another state-dir write storm.
    """
    global _REFRESH_CALL_TIMES, _REFRESH_BURST_REPORTED_AT
    now = time.time()
    state_dir = _config.get_xdg_state_home()
    cooldown_path = state_dir / "refresh-template-cooldown"
    try:
        if 0 <= now - cooldown_path.stat().st_mtime < _REFRESH_BURST_WINDOW_SECS:
            return False
    except OSError:
        pass

    calls_path = state_dir / "refresh-template-calls.json"
    try:
        persisted = [float(t) for t in json.loads(calls_path.read_text())]
    except (OSError, ValueError, TypeError):
        persisted = []
    persisted = [t for t in persisted if 0 <= now - t < _REFRESH_BURST_WINDOW_SECS]
    in_process = [t for t in _REFRESH_CALL_TIMES if 0 <= now - t < _REFRESH_BURST_WINDOW_SECS]

    persisted.append(now)
    in_process.append(now)
    _REFRESH_CALL_TIMES = in_process[-_REFRESH_BURST_LIMIT * 2 :]
    attempts = max(len(persisted), len(in_process))
    if attempts <= _REFRESH_BURST_LIMIT:
        with contextlib.suppress(OSError):
            calls_path.parent.mkdir(parents=True, exist_ok=True)
            calls_path.write_text(json.dumps(persisted[-_REFRESH_BURST_LIMIT * 2 :]))
        return True

    with contextlib.suppress(OSError):
        cooldown_path.touch(exist_ok=True)
    # Report once per window: a caller spinning at kHz would otherwise turn the
    # complaint into the same kind of storm the bound exists to stop.
    if now - _REFRESH_BURST_REPORTED_AT >= _REFRESH_BURST_WINDOW_SECS:
        _REFRESH_BURST_REPORTED_AT = now
        print(
            f"Error: live session-template refresh attempted {attempts} times in "
            f"{int(_REFRESH_BURST_WINDOW_SECS)}s (limit {_REFRESH_BURST_LIMIT}) — refusing to run. "
            "Something is driving `ai update` in a loop; find and stop the caller (AI-CLI-129).",
            file=sys.stderr,
        )
    return False


def _write_stable_session_script(tmux_session: str) -> bool:
    """Bring ``sessions/<tmux_session>.sh`` in line with persisted metadata.

    A changed script bumps its mtime, which the running wrapper's hot-reload watches —
    so a live session picks up a new template on its next restart without a full
    ``ai c`` relaunch. An unchanged script is left untouched. Returns False only when
    the session has no usable metadata.
    """
    return _sync_stable_session_script(tmux_session) is not None


def _sync_stable_session_script(tmux_session: str) -> "bool | None":
    """Regenerate a session's stable script from metadata, skipping no-op writes.

    Returns True if the on-disk script changed, False if it was already current, and
    None if the session has no usable ``session-meta-*.json``.
    """
    state_dir = _config.get_xdg_state_home()
    meta_path = state_dir / f"session-meta-{tmux_session}.json"
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text())
        script = _engine_script_from_meta(meta)
    except Exception as exc:
        print(f"  (warning: could not regenerate {tmux_session}: {exc})", file=sys.stderr)
        return None
    return _write_launch_script_if_changed(state_dir / "sessions" / f"{tmux_session}.sh", script)


def _refresh_live_session_scripts(quiet: bool = False) -> int:
    """Regenerate stable scripts for every live ai-cli tmux session.

    Called after ``ai update`` installs a new template so the wrapper's mtime
    hot-reload fires on each session's next restart. A session is "ai-cli-managed"
    iff it has a ``session-meta-*.json``. Best-effort; returns the count of scripts
    that actually changed — sessions already on the current template are left alone
    so they do not hot-reload for nothing.
    """
    if not _refresh_within_burst_budget():
        return 0
    try:
        ls = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True, check=False
        )
    except Exception:
        return 0
    if ls.returncode != 0:
        return 0
    refreshed = 0
    for sname in ls.stdout.split():
        if (_config.get_xdg_state_home() / f"session-meta-{sname}.json").exists() and _sync_stable_session_script(
            sname
        ):
            if not quiet:
                print(f"  refreshed session template: {sname}")
            refreshed += 1
    return refreshed


def _request_remote_session_allocation(
    ssh_args: list[str], engine: str, project_prefix: str, name: str
) -> tuple[str, str]:
    """Return the canonical session identity allocated by the remote host."""
    remote_command = (
        'export PATH="$HOME/.local/bin:$PATH"; '
        f"ai internal allocate-session-name {shlex.quote(engine)} {shlex.quote(project_prefix)} {shlex.quote(name)}"
    )
    result = subprocess.run(
        [*ssh_args, f"zsh -l -c {shlex.quote(remote_command)}"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"remote session-name allocation failed{f': {detail}' if detail else ''}")
    try:
        allocation = json.loads(result.stdout)
        session_id = allocation["session_id"]
        ai_name = allocation["ai_name"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError("remote session-name allocation returned an invalid response") from exc
    expected_prefix = f"{engine}-r-"
    if not isinstance(session_id, str) or not isinstance(ai_name, str) or not session_id.startswith(expected_prefix):
        raise RuntimeError("remote session-name allocation returned an invalid identity")
    if session_id.removeprefix(expected_prefix) != ai_name:
        raise RuntimeError("remote session-name allocation returned inconsistent identities")
    return session_id, ai_name


# --- `ai internal` fast-path — machine-to-machine commands ---


def _handle_internal(argv: list[str]) -> None:
    """Handle `ai internal <action> [args...]` — invoked from bash hooks.

    Kept out of Click so sub-millisecond startup is preserved for hot paths.
    Always calls ``sys.exit`` — never returns.
    """
    if not argv:
        print("Usage: ai internal <action> [args...]", file=sys.stderr)
        sys.exit(1)
    action = argv[0]
    config = _config.load_config()

    if action == "get-latest-gemini-id":
        _ai_name_arg = argv[1] if len(argv) > 1 else None
        res = _session.get_latest_gemini_session_id(_ai_name_arg)
        if res:
            print(res)
        sys.exit(0)
    elif action == "resolve-continue-target":
        if len(argv) < 3:
            print("Usage: ai internal resolve-continue-target <cwd> <ai_name>", file=sys.stderr)
            sys.exit(1)
        matched = _find_cc_session_by_title(Path(argv[1]), argv[2])
        if matched is not None:
            is_live, pid = _cc_session_is_live(matched)
            if is_live:
                print(_cc_live_session_warning(argv[2], pid), file=sys.stderr)
                sys.exit(2)
            print(matched)
        sys.exit(0)
    elif action == "update-session-map":
        if len(argv) < 4:
            print("Usage: ai internal update-session-map <engine> <ai_name> <uuid>", file=sys.stderr)
            sys.exit(1)
        engine, ai_name, uuid = argv[1], argv[2], argv[3]
        d = _config.get_session_map(engine)
        d[ai_name] = uuid
        _config.save_session_map(d, engine)
        sys.exit(0)
    elif action == "cleanup-worktree":
        if len(argv) < 2:
            print("Usage: ai internal cleanup-worktree <ai_name>", file=sys.stderr)
            sys.exit(1)
        _session.cleanup_worktree(argv[1])
        sys.exit(0)
    elif action == "release-color-slot":
        if len(argv) < 2:
            print("Usage: ai internal release-color-slot <ai_name>", file=sys.stderr)
            sys.exit(1)
        _iterm2._release_iterm2_color_slot(argv[1])
        sys.exit(0)
    elif action == "cleanup-session-files":
        if len(argv) < 2:
            print("Usage: ai internal cleanup-session-files <ai_name>", file=sys.stderr)
            sys.exit(1)
        from . import icon_generator as _ig_cs

        _ig_cs.cleanup_session_files(argv[1])
        sys.exit(0)
    elif action == "allocate-session-name":
        if len(argv) < 4:
            print("Usage: ai internal allocate-session-name <engine> <project_prefix> <name>", file=sys.stderr)
            sys.exit(1)
        engine, project_prefix, name = argv[1], argv[2], argv[3]
        use_tmux = config.get("session", {}).get("use_tmux", True)
        session_id, ai_name = _session.build_session_name(
            engine, project_prefix, name, config, is_remote=True, use_tmux=use_tmux
        )
        print(json.dumps({"session_id": session_id, "ai_name": ai_name}))
        sys.exit(0)
    elif action == "get-version":
        print(_pkg_version_string())
        sys.exit(0)
    elif action == "refresh-template":
        if len(argv) < 2:
            print("Usage: ai internal refresh-template <tmux_session>", file=sys.stderr)
            sys.exit(1)
        tmux_session = argv[1]
        meta_path = _config.get_xdg_state_home() / f"session-meta-{tmux_session}.json"
        if not meta_path.exists():
            print(f"No session metadata for {tmux_session}", file=sys.stderr)
            sys.exit(1)
        try:
            meta = json.loads(meta_path.read_text())
            script = _engine_script_from_meta(meta)
        except Exception as exc:
            print(f"Failed to read session metadata: {exc}", file=sys.stderr)
            sys.exit(1)
        import tempfile

        fd, tmp_path = tempfile.mkstemp(prefix=f"ai-refresh-{tmux_session}-", suffix=".sh")
        with os.fdopen(fd, "w") as fh:
            fh.write("#!/usr/bin/env bash\n")
            fh.write(f'rm -f "{tmp_path}"\n')  # self-delete on exec
            fh.write(script)
        print(tmp_path)
        sys.exit(0)
    elif action == "write-stable-script":
        if len(argv) < 2:
            print("Usage: ai internal write-stable-script <tmux_session>", file=sys.stderr)
            sys.exit(1)
        sys.exit(0 if _write_stable_session_script(argv[1]) else 1)
    elif action == "notify":
        if len(argv) < 3:
            print("Usage: ai internal notify <session_id> <message>", file=sys.stderr)
            sys.exit(1)
        from .notifications import NotificationManager

        NotificationManager(argv[1]).notify(argv[2])
        sys.exit(0)
    elif action == "publish-event":
        if len(argv) < 3:
            print("Usage: ai internal publish-event <session_id> <event_type>", file=sys.stderr)
            sys.exit(1)
        import asyncio

        from .messaging import NATSClient

        nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
        client = NATSClient(servers=nats_servers)
        # NATS unavailable — non-fatal
        with contextlib.suppress(Exception):
            asyncio.run(client.publish_event(argv[1], argv[2]))
        sys.exit(0)
    elif action == "publish-heartbeat":
        if len(argv) < 3:
            print("Usage: ai internal publish-heartbeat <session_id> <data_json>", file=sys.stderr)
            sys.exit(1)
        import asyncio

        from .messaging import NATSClient

        try:
            data = json.loads(argv[2])
        except json.JSONDecodeError as e:
            print(f"Invalid JSON: {e}", file=sys.stderr)
            sys.exit(1)
        nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
        client = NATSClient(servers=nats_servers)
        # NATS unavailable — non-fatal
        with contextlib.suppress(Exception):
            asyncio.run(client.publish_heartbeat(argv[1], data))
        sys.exit(0)
    elif action == "publish-session-event":
        if len(argv) < 3:
            print("Usage: ai internal publish-session-event <session_id> <started|stopped>", file=sys.stderr)
            sys.exit(1)
        import asyncio

        from .messaging import NATSClient

        session_id = argv[1]
        event_verb = argv[2]
        subject = f"session.{session_id}.{event_verb}"
        nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
        client = NATSClient(servers=nats_servers)
        # NATS unavailable — non-fatal
        with contextlib.suppress(Exception):
            asyncio.run(client.publish(subject, {"session_id": session_id, "event": event_verb, "ts": time.time()}))
        sys.exit(0)
    elif action == "publish":
        if len(argv) < 3:
            print("Usage: ai internal publish <subject> <json_payload>", file=sys.stderr)
            sys.exit(1)
        import asyncio

        from .messaging import NATSClient

        subject = argv[1]
        try:
            payload = json.loads(argv[2])
        except (json.JSONDecodeError, IndexError):
            payload = {}
        nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
        client = NATSClient(servers=nats_servers)
        # NATS unavailable — non-fatal
        with contextlib.suppress(Exception):
            asyncio.run(client.publish(subject, payload))
        sys.exit(0)
    elif action == "signal-watch":
        if len(argv) < 3:
            print("Usage: ai internal signal-watch <project> <session_id>", file=sys.stderr)
            sys.exit(1)
        _internal_signal_watch(argv[1], argv[2], config)
        sys.exit(0)
    elif action == "quota-subscriber":
        _internal_quota_subscriber(config)
        sys.exit(0)
    elif action == "handoff-drain":
        if len(argv) < 3:
            sys.exit(0)
        _internal_handoff_drain(argv[1], argv[2], config)
        sys.exit(0)
    elif action == "set-iterm2-name":
        if len(argv) < 3:
            print("Usage: ai internal set-iterm2-name <tmux_session|tty> <name>", file=sys.stderr)
            sys.exit(1)
        # Resolve the physical pane by tty: accept a tty directly, or a tmux
        # session name whose live client tty we look up. tty matching renames
        # exactly the visible pane and can't collide across sessions.
        target = argv[1]
        tty = target if target.startswith("/dev/") else _iterm2._iterm_pane_tty_for_tmux_session(target)
        _iterm2._set_iterm2_name_by_tty(tty, argv[2])
        sys.exit(0)
    else:
        print(f"Usage: ai internal <action> [args...] (unknown action: {action})", file=sys.stderr)
        sys.exit(1)


async def _on_handoff_signal_watch(
    data: dict,
    *,
    handoff_dir: "Path | None",
    pending_file: "Path",
    session_id: str,
    machine_id: str,
) -> None:
    """Process one inbound handoff message for signal-watch.

    Extracted from the ``_on_handoff`` closure inside ``_internal_signal_watch``
    so it can be imported and unit-tested independently of the NATS subscription.
    """
    handoff_id = data.get("id")
    title = data.get("title", "")
    priority = data.get("priority", "")
    message = data.get("message", "")
    for_machine = data.get("for_machine", "")
    if not for_machine or for_machine != machine_id:
        return
    print(f"\n[HANDOFF] {priority} #{handoff_id}: {title}", flush=True)
    if handoff_dir is None or not handoff_id:
        return
    content = data.get("content")
    filename = data.get("filename")
    if content and filename:
        pending_dir = handoff_dir / "pending"
        claimed_dir = handoff_dir / "claimed"
        local_file = pending_dir / filename
        if (claimed_dir / filename).exists():
            return
        if not local_file.exists():
            pending_dir.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(OSError):
                local_file.write_text(content)
    claimed = _handoff._claim_handoff_for_signal(handoff_dir, int(handoff_id), session_id)
    if claimed is None:
        return
    _handoff._log_handoff_event(
        "handoff.claimed",
        handoff_id=handoff_id,
        session=session_id,
        layer="nats_realtime" if data.get("_source") != "startup_scan" else "startup_scan",
    )
    resume_msg = f"Auto-pickup: {priority} handoff #{handoff_id} — {title}. File: {claimed}\n\n{message}"
    pending_file.parent.mkdir(parents=True, exist_ok=True)
    pending_file.write_text(resume_msg)
    signal_file = _config.get_xdg_state_home() / f"cc-exit-{session_id}"
    with contextlib.suppress(OSError):
        signal_file.touch()


def _write_pending_if_claimed_drain(
    data: dict,
    *,
    handoff_dir: "Path | None",
    prompt_file: "Path",
    session: str,
    machine_id: str,
) -> bool:
    """Claim a handoff from drain data and write the resume prompt file.

    Extracted from the ``_write_pending_if_claimed`` closure inside
    ``_internal_handoff_drain`` so it can be unit-tested independently.
    Returns ``True`` if a handoff was claimed and the prompt file written.
    """
    handoff_id = data.get("id")
    title = data.get("title", "")
    priority = data.get("priority", "")
    message = data.get("message", "")
    for_machine = data.get("for_machine", "")
    if not for_machine or for_machine != machine_id:
        return False
    if handoff_dir is None or not handoff_id:
        return False
    content = data.get("content")
    filename = data.get("filename")
    if content and filename:
        pending_dir = handoff_dir / "pending"
        claimed_dir = handoff_dir / "claimed"
        local_file = pending_dir / filename
        if (claimed_dir / filename).exists():
            return False
        if not local_file.exists():
            pending_dir.mkdir(parents=True, exist_ok=True)
            try:
                local_file.write_text(content)
            except OSError:
                return False
    claimed = _handoff._claim_handoff_for_signal(handoff_dir, int(handoff_id), session)
    if claimed is None:
        return False
    _handoff._log_handoff_event(
        "handoff.claimed",
        handoff_id=handoff_id,
        session=session,
        layer="pre_launch_drain",
    )
    resume_msg = f"Auto-pickup: {priority} handoff #{handoff_id} — {title}. File: {claimed}\n\n{message}"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(resume_msg)
    return True


async def _on_quota_snapshot_handler(data: dict) -> None:
    """Process one inbound quota.snapshot message.

    Extracted from the ``_on_quota_snapshot_msg`` closure inside
    ``_internal_quota_subscriber`` so it can be unit-tested independently.
    """
    from .quota_db import record_quota_snapshot

    with contextlib.suppress(Exception):
        record_quota_snapshot(
            usage_percent=data["usage_percent"],
            session_pct=data.get("session_pct"),
            weekly_sonnet_pct=data.get("weekly_sonnet_pct"),
            weekly_model_name=data.get("weekly_model_name"),
            extra_pct=data.get("extra_pct"),
            reset_at=data.get("reset_at"),
        )


def _internal_signal_watch(sw_project: str, sw_session_id: str, config: dict) -> None:
    import asyncio

    from .messaging import NATSClient

    sw_handoff_dir = _config._get_handoff_queue_dir()
    sw_pending_file = _config.get_xdg_state_home() / f"handoff-pending-{sw_session_id}"
    nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
    sw_client = NATSClient(servers=nats_servers)

    async def _on_handoff(data):
        await _on_handoff_signal_watch(
            data,
            handoff_dir=sw_handoff_dir,
            pending_file=sw_pending_file,
            session_id=sw_session_id,
            machine_id=os.environ.get("AI_HOST", ""),
        )

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

    async def _run_subscriptions() -> None:
        await sw_client.subscribe_durable(f"handoff.{sw_project}", consumer_name, _on_handoff)

    # Not covered: _run_subscriptions blocks indefinitely on success; exception
    # path requires a live NATS server to fail mid-subscription.
    with contextlib.suppress(Exception):
        asyncio.run(_run_subscriptions())


def _internal_quota_subscriber(config: dict) -> None:
    # Persistent daemon: subscribes to quota.snapshot via JetStream durable consumer.
    # Runs as a Circus-managed process on Mac, independent of CC session lifecycle.
    # Missed messages during downtime are replayed on reconnect (JetStream durability).
    import asyncio

    from .messaging import NATSClient

    qs_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
    qs_client = NATSClient(servers=qs_servers)

    # Not covered: subscribe_durable blocks indefinitely on success; exception
    # path requires a live NATS server to fail mid-subscription.
    with contextlib.suppress(Exception):
        asyncio.run(
            qs_client.subscribe_durable(
                "quota.snapshot",
                "quota-subscriber-mac",
                _on_quota_snapshot_handler,
            )
        )


def _internal_handoff_drain(hd_project: str, hd_session: str, config: dict) -> None:
    # Synchronous: drain pending NATS messages + local file scan, then exit.
    # Called BEFORE CC launches so prompt_file is ready on first invocation.
    import asyncio

    from .messaging import NATSClient

    hd_handoff_dir = _config._get_handoff_queue_dir()
    hd_prompt_file = _config.get_xdg_state_home() / f"cc-resume-prompt-{hd_session}"
    nats_servers = config.get("messaging", {}).get("nats_servers", ["nats://localhost:4222"])
    hd_client = NATSClient(servers=nats_servers)
    _handoff._log_handoff_event("handoff.drain.started", session=hd_session, project=hd_project)

    def _write_pending_if_claimed(data):
        return _write_pending_if_claimed_drain(
            data,
            handoff_dir=hd_handoff_dir,
            prompt_file=hd_prompt_file,
            session=hd_session,
            machine_id=os.environ.get("AI_HOST", ""),
        )

    # 1. Local file scan first (fast, no network)
    if hd_handoff_dir is not None:
        pending_dir = hd_handoff_dir / "pending"
        if pending_dir.exists():
            best = _handoff._find_best_handoff(pending_dir, project_filter=hd_project)
            if best is not None:
                try:
                    fid = int(best.name.split("-")[0])
                    raw = best.read_text()
                    fm_title = re.search(r'^title:\s*"?([^"\n]+)"?', raw, re.MULTILINE)
                    fm_priority = re.search(r"^priority:\s*(\S+)", raw, re.MULTILINE)
                    fm_for_machine = re.search(r"^for_machine:\s*(\S+)", raw, re.MULTILINE)
                    body = raw.split("---", 2)[-1].strip() if raw.count("---") >= 2 else ""
                    local_for_machine = fm_for_machine.group(1) if fm_for_machine else ""
                    _handoff._log_handoff_event(
                        "handoff.drain.local_found",
                        session=hd_session,
                        handoff_id=fid,
                        for_machine=local_for_machine,
                    )
                    _write_pending_if_claimed(
                        {
                            "id": fid,
                            "title": fm_title.group(1).strip() if fm_title else best.stem,
                            "priority": fm_priority.group(1) if fm_priority else "",
                            "message": body,
                            "for_machine": local_for_machine,
                        }
                    )
                except Exception:
                    # Not covered: requires filesystem error reading a pending
                    # handoff file that exists and was just discovered by glob.
                    pass

    # 2. NATS drain: pull pending JetStream messages (non-blocking, 2s timeout)
    if not hd_prompt_file.exists():
        _handoff._log_handoff_event("handoff.drain.nats_attempt", session=hd_session, project=hd_project)

        # Not covered: _drain() is an async closure that requires a live NATS
        # JetStream server. Inner branches (js is None, message decode error,
        # _write_pending_if_claimed returning True, fetch timeout, subscribe
        # failure) all require specific live-server or network-failure conditions.
        # See docs/test/unit-tests.md §Intentionally Uncovered Lines.
        async def _drain():
            try:
                await hd_client.connect()
            except Exception as e:
                _handoff._log_handoff_event("handoff.drain.nats_connect_failed", session=hd_session, error=str(e))
                return
            if not hd_client.js:
                _handoff._log_handoff_event("handoff.drain.nats_no_js", session=hd_session)
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
            except Exception as e:
                _handoff._log_handoff_event("handoff.drain.nats_subscribe_failed", session=hd_session, error=str(e))
            finally:
                await hd_client.close()

        async def _drain_with_timeout():
            try:
                await asyncio.wait_for(_drain(), timeout=6.0)
            except TimeoutError:
                _handoff._log_handoff_event("handoff.drain.nats_timeout", session=hd_session)

        try:
            asyncio.run(_drain_with_timeout())
        except Exception as e:
            # Not covered: requires asyncio.run() itself to raise, which needs a
            # broken event loop or NATS server in a specific failure state.
            _handoff._log_handoff_event("handoff.drain.nats_run_failed", session=hd_session, error=str(e))


def _has_conflict_or_unknown(repo_root) -> bool:
    """True if the repo has conflict stages, or if that cannot be determined.

    Fails closed on purpose: callers use this to decide whether a destructive
    cleanup is safe, and "I could not check" must never license discarding
    someone's in-progress conflict resolution.
    """
    try:
        return bool(unmerged_paths(repo_root))
    except GitProbeError:
        return True


def _running_uv_tool_venv() -> "Path | None":
    """Return the uv tool environment this interpreter runs from, else None.

    uv writes ``uv-receipt.toml`` at the root of every tool environment, so its
    presence beside ``sys.prefix`` identifies one exactly — no ``uv tool dir``
    round trip, and it stays correct however uv relocates its directories.
    """
    if sys.prefix == sys.base_prefix:
        return None  # not a virtual environment at all
    prefix = Path(sys.prefix)
    return prefix if (prefix / "uv-receipt.toml").is_file() else None


def _venv_interpreter(venv: "Path") -> "Path":
    """Path to a venv's interpreter, on either platform layout."""
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _install_is_editable(venv: "Path") -> bool:
    """Whether this tool environment holds (or is recorded as) an editable install.

    Self-update must preserve editability, and NOT because editable is nicer.  A
    plain install silently converts the environment to a copied snapshot, and the
    fleet installer treats a missing editable marker as damage to repair with
    ``uv tool install --force --editable`` — which deletes and rebuilds the whole
    environment, taking ``~/.local/bin/ai`` with it.  Two owners then alternate
    forever, and every install-side repair is an outage window in which a fresh
    shell cannot resolve ``ai`` at all.

    Both evidence sources are consulted because they answer different questions.
    A marker in ``site-packages`` is ground truth about the CURRENT state.  uv's
    own ``uv-receipt.toml`` records the ORIGINAL intent and is not rewritten by
    ``uv pip install``, so it still reads ``editable`` after a plain install has
    already clobbered the marker — which is exactly the state that needs healing,
    so either being true means install editably.
    """
    for pattern in (
        "lib/python*/site-packages/__editable__*",
        "lib/python*/site-packages/*_editable_impl_*",
        "Lib/site-packages/__editable__*",
        "Lib/site-packages/*_editable_impl_*",
    ):
        if any(venv.glob(pattern)):
            return True
    try:
        receipt = (venv / "uv-receipt.toml").read_text(encoding="utf-8")
    except OSError:
        return False
    return "editable" in receipt


def _tool_env_can_import(venv: "Path", module: str = "ai_cli") -> bool:
    """Whether a *fresh* interpreter from ``venv`` can import ``module``.

    Must be a subprocess: this process already holds the module in memory, so an
    in-process import proves nothing about what survived on disk.
    """
    py = _venv_interpreter(venv)
    if not py.exists():
        return False
    try:
        probe = subprocess.run(
            [str(py), "-c", f"import {module}"],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except OSError:
        return False
    return probe.returncode == 0


def _should_use_uv_link_mode_copy(uv_bin: str, target_dir: "Path | None" = None) -> bool:
    """Detect if uv's cache and install target dirs are on different filesystems.

    When uv's cache directory and its install target directory reside on
    different filesystems (e.g. cache on NFS/EFS, target on local disk), hardlinking
    is physically impossible and uv falls back to copying with a warning. Detecting
    this condition lets us pass --link-mode=copy explicitly to suppress the warning.

    This decision is portable (os.stat().st_dev comparison works on Linux, macOS,
    and Windows) and automatic — no user-facing flag needed.

    Args:
        uv_bin: Path to the uv executable.
        target_dir: Optional target directory to check (e.g., a venv path for
            `uv pip install`). If None, checks the tool install directory.

    Returns True if the filesystems differ (use --link-mode=copy), False if they
    match (let uv hardlink), or False on any error (preserve default behavior).

    Implementation notes:
    - Resolve uv's cache directory via `uv cache dir` rather than reading UV_CACHE_DIR
      ourselves: this matches uv's own resolution (env var > platform default) and
      stays correct if uv's fallback logic changes. Cheap subprocess (exits immediately).
    - Resolve the tool install directory via `uv tool dir` if target_dir is None.
      If that returns nothing (on some uv versions), fall back to the platform-appropriate
      default.
    - If the target directory does not exist yet (first install), walk up to the
      nearest existing ancestor before stat'ing — st_dev is an inode property, so
      any ancestor on the same filesystem carries the same value.
    - On any failure (uv not found, command error, path stat error), return False
      to preserve the current "no explicit --link-mode" behavior — never let this
      detection break an update.
    """
    try:
        # Resolve uv's cache directory the way uv itself does.
        cache_result = subprocess.run([uv_bin, "cache", "dir"], capture_output=True, text=True, timeout=5, check=False)
        if cache_result.returncode != 0:
            return False
        cache_dir = Path(cache_result.stdout.strip())
        if not cache_dir or not cache_dir.exists():
            return False

        # Resolve the target directory.
        if target_dir is None:
            # Resolve the tool install directory. `uv tool dir` may print nothing on some
            # versions; fall back to the platform-appropriate default if so.
            tool_result = subprocess.run(
                [uv_bin, "tool", "dir"], capture_output=True, text=True, timeout=5, check=False
            )
            tool_dir_str = tool_result.stdout.strip() if tool_result.returncode == 0 else ""
            # Kept as if/else rather than a ternary (SIM108) to preserve the
            # else-branch comment documenting the platform defaults.
            if tool_dir_str:  # noqa: SIM108
                install_dir = Path(tool_dir_str)
            else:
                # Platform-specific default tool install location (uv 0.1.x - 0.5.x behavior).
                # Windows: %LOCALAPPDATA%\uv\tools or ~/.local/share/uv/tools
                # Unix: ~/.local/share/uv/tools
                install_dir = Path.home() / ".local" / "share" / "uv" / "tools"
        else:
            install_dir = target_dir

        # The target directory may not exist yet on a first install. Walk up to the
        # nearest existing ancestor — st_dev is the same for every path on that filesystem.
        target = install_dir
        while not target.exists():
            parent = target.parent
            if parent == target:
                # Reached the root without finding an existing ancestor.
                return False
            target = parent

        cache_dev = cache_dir.stat().st_dev
        target_dev = target.stat().st_dev
        return cache_dev != target_dev

    except Exception:
        # Preserve current behavior (no --link-mode flag) on any error.
        return False


# --- Command implementations (invoked by Click handlers below) ---


def _do_update_or_deploy(force_reinstall: bool, config: dict, quiet: bool = False) -> None:
    """Pull, bump the version so uv cannot serve a cached build, and install.

    ``quiet`` captures git's and uv's output instead of streaming it, and reports
    one summary line naming the version that was installed. It exists for the
    session-launch auto-update, where the full transcript scrolled past every
    launch immediately before the session painted. Failures are never quiet: the
    captured transcript is printed in full on stderr.
    """
    project_path = _find_aicli_project_path(config)
    if project_path is None:
        print(
            "Error: could not locate ai-cli-utils source. Set [deploy] project_path in config.",
            file=sys.stderr,
        )
        sys.exit(1)
    pyproject = project_path / "pyproject.toml"
    if not pyproject.exists():
        print(
            f"Error: pyproject.toml not found at {project_path}. Set [deploy] project_path in config.",
            file=sys.stderr,
        )
        sys.exit(1)
    # Restore pyproject.toml before pull — it may be dirty from an interrupted previous update
    subprocess.run(["git", "checkout", "--", "pyproject.toml"], cwd=project_path, capture_output=quiet, check=False)
    if not quiet:
        print("Pulling latest from origin...")
    # AIH-443 Shape B: `git pull --rebase --autostash` exits 0 even when its
    # automatic stash pop conflicted, so the exit code alone cannot be trusted
    # (measured on git 2.43.0 and 2.55.0). Left unchecked this strands the
    # checkout: the index keeps conflict stages, every later pull refuses with
    # "Pulling is not possible because you have unmerged files", and the repo
    # never advances again. That is how the remote build host sat 50 commits
    # behind for five days while its disk filled — docs/bugs/stranded-autostash.md.
    pull, stranded = pull_rebase_autostash(project_path)
    # A conflicted index means the pull could not advance the checkout — whether
    # this run caused it or a previous one did. Either way the source tree is
    # stale and cannot be built from, and every later pull will refuse too, so
    # this must be fatal rather than a warning that scrolls past.
    try:
        _conflicted = unmerged_paths(project_path)
    except GitProbeError as e:
        # Cannot verify; refuse rather than install from an unverifiable tree.
        _conflicted = {f"<unverifiable: {e}>"}
    if stranded or _conflicted:
        detail = stranded or f"pre-existing conflict in {', '.join(sorted(_conflicted))}"
        print(
            f"Error: {project_path} has a conflicted index ({detail}); "
            f"git pull exited {pull.returncode}.\n"
            f"  Refusing to install from a half-applied checkout — it would ship broken or stale code.\n"
            f"  Nothing has been discarded. Resolve, then re-run:\n"
            f"    git -C {project_path} status\n"
            f"    git -C {project_path} stash list",
            file=sys.stderr,
        )
        sys.exit(1)
    if pull.returncode != 0:
        # Tree is intact (nothing new conflicted, no autostash stranded), so the
        # pull merely failed — e.g. no network. Installing the current checkout
        # is still valid; say so rather than pretending the pull worked.
        print(
            f"Warning: git pull failed (exit {pull.returncode}); installing the current checkout.\n"
            f"  {pull.stderr.strip()}",
            file=sys.stderr,
        )
    # Abort if pull left unresolved conflict markers — installing with conflicts produces a broken package
    src_dir = project_path / "src"
    conflict_files = []
    if src_dir.is_dir():
        for py_file in src_dir.rglob("*.py"):
            try:
                text = py_file.read_text(errors="replace")
                if any(ln.startswith(("<<<<<<< ", ">>>>>>> ")) for ln in text.splitlines()):
                    conflict_files.append(py_file.relative_to(project_path))
            except OSError:
                pass
    if conflict_files:
        print(
            "Error: unresolved git conflict markers found — resolve before installing:",
            file=sys.stderr,
        )
        for f in conflict_files:
            print(f"  {f}", file=sys.stderr)
        sys.exit(1)
    # Read version after pull so the bump applies to the current remote state.
    #
    # Bytes, not text, for the whole round trip. Path.read_text() applies
    # universal-newline translation, so a CRLF pyproject.toml arrived as LF in
    # memory and the restore below wrote LF back to disk: the version bump was
    # reverted but the line endings were not, leaving a whole-file phantom diff
    # that re-appeared on every single update run. Only `git checkout --
    # pyproject.toml` above kept it from being noticed. write_bytes() translates
    # nothing, so the restore is byte-identical whatever the file's endings are.
    original = pyproject.read_bytes()
    m = re.search(rb'^(version\s*=\s*")([^"]+)(")', original, re.MULTILINE)
    if not m:
        print("Error: could not find version in pyproject.toml", file=sys.stderr)
        sys.exit(1)
    old_version = m.group(2).decode("utf-8", "replace")
    base = re.sub(r"\.post\d+$", "", old_version)
    new_version = f"{base}.post{int(time.strftime('%Y%m%d%H%M%S'))}"
    if not quiet:
        print(f"Updating {old_version} → {new_version}")
    uv_bin = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    exit_code = 0
    captured = ""
    try:
        pyproject.write_bytes(original[: m.start(2)] + new_version.encode("utf-8") + original[m.end(2) :])
        # `uv tool install --force` REPLACES the tool environment: it deletes the
        # existing one and rebuilds it. When the tool being updated is the one
        # currently running, that environment holds this interpreter's own mapped
        # image (<venv>/Scripts/python.exe), and Windows refuses to unlink a mapped
        # image — `Access is denied. (os error 5)`. uv is not atomic about it, so it
        # removes Lib/site-packages, then fails on Scripts, leaving an environment
        # with no packages at all. Install into the live environment instead: that
        # rewrites Lib/site-packages and never touches Scripts.
        self_venv = _running_uv_tool_venv()
        if self_venv is not None:
            uv_cmd = [uv_bin, "pip", "install", "--python", str(_venv_interpreter(self_venv))]
            # Keep an editable install editable.  Dropping ``-e`` here is what
            # hands the environment back and forth with the fleet installer, and
            # its repair path is destructive — see `_install_is_editable`.
            if _install_is_editable(self_venv):
                uv_cmd.append("-e")
            uv_cmd.append(str(project_path))
            if force_reinstall:
                uv_cmd.append("--force-reinstall")
            if _should_use_uv_link_mode_copy(uv_bin, self_venv):
                uv_cmd.append("--link-mode=copy")
        else:
            uv_cmd = [uv_bin, "tool", "install", str(project_path), "--force"]
            if force_reinstall:
                uv_cmd.append("--reinstall")
            if _should_use_uv_link_mode_copy(uv_bin):
                uv_cmd.append("--link-mode=copy")
        result = subprocess.run(uv_cmd, cwd=project_path, capture_output=quiet, text=True, check=False)
        exit_code = result.returncode
        if quiet:
            captured = f"{result.stdout or ''}{result.stderr or ''}"
    finally:
        pyproject.write_bytes(original)
    if exit_code != 0 and captured.strip():
        # Quiet mode hides uv's progress, never its diagnosis.
        print(captured.rstrip(), file=sys.stderr)
    if exit_code == 0:
        # Install into any configured extra venvs (e.g. tool venvs that depend on ai-cli-utils)
        extra_venvs = config.get("update", {}).get("extra_venvs", [])
        for venv_path_str in extra_venvs:
            venv_path = Path(venv_path_str).expanduser()
            if venv_path.exists():
                pip_cmd = [uv_bin, "pip", "install", str(project_path)]
                if force_reinstall:
                    pip_cmd.append("--force-reinstall")
                if _should_use_uv_link_mode_copy(uv_bin, venv_path):
                    pip_cmd.append("--link-mode=copy")
                pip_result = subprocess.run(
                    pip_cmd,
                    env={**os.environ, "VIRTUAL_ENV": str(venv_path)},
                    capture_output=quiet,
                    text=True,
                    check=False,
                )
                if quiet and pip_result.returncode != 0:
                    print(
                        f"Warning: installing into {venv_path} failed (exit {pip_result.returncode}):\n"
                        f"{(pip_result.stdout or '') + (pip_result.stderr or '')}".rstrip(),
                        file=sys.stderr,
                    )
        # Clear pycache (cross-platform)
        for _cache_dir in project_path.rglob("__pycache__"):
            if _cache_dir.is_dir():
                shutil.rmtree(_cache_dir, ignore_errors=True)
        # Record HEAD hash so session start (and each running wrapper's self-update)
        # can detect staleness. This is the monotonic update signal — it changes on
        # every update, unlike the package version which is restored to base above.
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=project_path, capture_output=True, text=True, check=False
        )
        if head.returncode == 0:
            stamp_file = _config.get_xdg_state_home() / "last_update_commit.txt"
            stamp_file.parent.mkdir(parents=True, exist_ok=True)
            stamp_file.write_text(head.stdout.strip())
        # Record the fingerprint of the source this install was built from, so the
        # next session launch can tell "already installed" from "genuinely stale"
        # without asking git what the current commit is. Written after the restore
        # above, so it describes the pyproject.toml that will be on disk next time —
        # not the transient bumped one. The HEAD stamp above stays: it is what the
        # generated session wrapper's own self-update reads.
        fingerprint = _installed_source_fingerprint(project_path)
        if fingerprint is not None:
            fingerprint_file = _config.get_xdg_state_home() / "last_install_fingerprint.txt"
            fingerprint_file.parent.mkdir(parents=True, exist_ok=True)
            fingerprint_file.write_text(fingerprint)
        # Regenerate the stable launch script for every live ai-cli session so the
        # wrapper's mtime hot-reload picks up this new template on its next restart —
        # no full `ai c` relaunch needed. Belt-and-suspenders alongside the commit-stamp
        # self-update baked into newly generated templates.
        _n = _refresh_live_session_scripts(quiet=quiet)
        if _n and not quiet:
            print(f"Refreshed {_n} live session template(s) — they reload on next restart.")
        # Deploy bundled CC config files to ~/.claude/ — write as plain files so any
        # pre-existing symlinks are replaced. These files are owned by ai-cli-utils and
        # should not be managed by ai sync or tracked in any project git repo.
        _deploy_cc_config_files(project_path)
        if quiet:
            # The whole quiet path's output: one line, naming what is now installed
            # and why it was rebuilt.
            reason = "cache-bypassing reinstall" if force_reinstall else "fresh build"
            print(f"ai-cli-utils {new_version} installed ({reason})")
    sys.exit(exit_code)


def _do_reconnect(requested: "list[int] | None", config: dict) -> None:
    # List remote tmux sessions and print reconnect commands.
    remote_cfg = _config.get_remote_machine(config)
    host = remote_cfg.get("host", "")
    user = remote_cfg.get("user", "ubuntu")
    if not host:
        print("Error: [remote] host not set in ~/.config/ai-cli-utils/config.toml", file=sys.stderr)
        sys.exit(1)

    probe = subprocess.run(
        ["ssh", f"{user}@{host}", "tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        print("Error: could not list remote tmux sessions", file=sys.stderr)
        sys.exit(1)

    remote_sessions = [s.strip() for s in probe.stdout.splitlines() if s.strip().startswith("c-r-")]
    if not remote_sessions:
        print("No remote CC sessions found on server.")
        sys.exit(0)

    if requested:
        remote_sessions = [s for s in remote_sessions if any(s.endswith(f"-{n}") for n in requested)]

    if not remote_sessions:
        print(f"No matching remote sessions for: {requested}")
        sys.exit(0)

    aliases = _config.get_project_aliases()

    # Load active transport state files for annotation
    _state_dir = _config.get_xdg_state_home()
    _transport_by_session: dict[str, str] = {}
    for _tf in _state_dir.glob("transport-*.json"):
        try:
            _td = json.loads(_tf.read_text())
            _transport_by_session[_td.get("session", "")] = _td.get("transport", "")
        except Exception:
            pass

    print(f"Found {len(remote_sessions)} remote session(s). Run each in a separate terminal:\n")
    for session_name in sorted(remote_sessions):
        parts = session_name.split("-")
        if len(parts) >= 4:
            num = parts[-1]
            proj_prefix = "-".join(parts[2:-1])
        else:
            continue
        project_name = aliases.get(proj_prefix, proj_prefix)
        _transport_tag = ""
        _transport = _transport_by_session.get(session_name, "")
        if _transport:
            _transport_tag = f"  [{_transport} connected]"
        if project_name == proj_prefix:
            print(f"  ai c {num} -R{_transport_tag}")
        else:
            print(f"  ai c {num} -R -p {proj_prefix}{_transport_tag}")
    print()
    sys.exit(0)


def _do_attach(session_name: str) -> None:
    check = subprocess.run(["tmux", "has-session", "-t", session_name], capture_output=True, check=False)
    if check.returncode != 0:
        print(f"No tmux session named '{session_name}'", file=sys.stderr)
        sys.exit(1)
    os.execvp("tmux", ["tmux", "attach-session", "-t", session_name])


def _do_ls(show_all: bool) -> None:
    res = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name} #{session_activity}"],
        capture_output=True,
        text=True,
        check=False,
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
            "No tmux sessions found." if show_all else "No ai-cli sessions found. Use --all to show all tmux sessions."
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
        """Extract project prefix from a provider session name."""
        parts = name.split("-")
        # Format: {c|g|p|cx}[-r]-{project}-{index}
        if len(parts) >= 3 and parts[0] in ("c", "g", "p", "cx"):
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
            check=False,
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


def _do_color(color_arg: str) -> None:
    _ai_name_env = os.environ.get("AI_TMUX_SESSION", "")
    if not _ai_name_env:
        print("ai color: not inside an ai session (AI_TMUX_SESSION not set)", file=sys.stderr)
        sys.exit(1)
    # Resolve color arg: named palette entry or raw hex
    _iterm2_cfg_c = _iterm2._load_iterm2_config()
    _palette_c = dict(_iterm2._iterm2_palette(_iterm2_cfg_c))
    if color_arg.startswith("#"):
        _new_hex = color_arg
    elif color_arg in _palette_c:
        _new_hex = f"#{_palette_c[color_arg]}"
    else:
        print(f"ai color: unknown color '{color_arg}'. Use a palette name or #RRGGBB.", file=sys.stderr)
        sys.exit(1)
    # Determine engine from session name convention
    _engine_c = "g" if _ai_name_env.startswith("g-") else "c"
    _session_type_c = _iterm2._iterm2_session_type(_engine_c)
    try:
        from . import icon_generator as _ig_c

        _icon_color_c = _iterm2._resolve_iterm2_config(_iterm2_cfg_c, _ai_name_env).get("icon_color")
        _ig_c.cleanup_session_files(_ai_name_env)
        _icon_path_c = _ig_c.generate_session_icon(_ai_name_env, _new_hex, _session_type_c, _icon_color_c)
        _ig_c.generate_dynamic_profile(_ai_name_env, _new_hex, _session_type_c, _icon_path_c)
    except Exception as e:
        print(f"ai color: icon generation failed: {e}", file=sys.stderr)
    _color_no_hash_c = _new_hex.lstrip("#")
    _profile_name_c = f"ai-cli:{_ai_name_env}"
    sys.stdout.write(f"\033]1337;SetProfile={_profile_name_c}\007")
    sys.stdout.write(f"\033]1337;SetColors=tab={_color_no_hash_c}\007")
    sys.stdout.flush()
    print(f"Color updated to {_new_hex}")
    sys.exit(0)


def _do_handoff_post(remote: bool, for_machine: str, post_args: "list[str]") -> None:
    if remote:
        remote_cfg = _config.get_remote_machine(_config.load_config())
        remote_host = remote_cfg.get("host", "")
        remote_user = remote_cfg.get("user", "ubuntu")
        if not remote_host:
            print("Error: [remote] host not set in config", file=sys.stderr)
            sys.exit(1)
        ssh_args = ["ssh", f"{remote_user}@{remote_host}", "ai", "handoff", "post"]
        # Preserve the --for-machine flag for the remote side.
        ssh_args += ["--for-machine", for_machine]
        ssh_args += list(post_args)
        os.execvp("ssh", ssh_args)
    if not for_machine:
        print("Error: --for-machine <machine> is required", file=sys.stderr)
        sys.exit(1)
    if len(post_args) < 4:
        print(
            "Usage: ai handoff post --for-machine <machine> <title> <priority> <project> <message>",
            file=sys.stderr,
        )
        sys.exit(1)
    _handoff.post_handoff(post_args[0], post_args[1], post_args[2], post_args[3], for_machine=for_machine)
    sys.exit(0)


# --- Session launch (default command — `ai c NAME`, `ai g NAME`) ---


def _do_session_launch(
    engine: str,
    name: str,
    resume: bool,
    once: bool,
    bare: bool,
    notify: bool,
    sandbox: bool,
    no_worktree: bool,
    remote: bool,
    project: str,
    is_remote: bool,
    project_prefix_override: str,
    extra_args: "list[str]",
    config: dict,
    remote_machine: str = "",
    no_direnv: bool = False,
) -> None:
    # tmux is a C binary, not a Python package -- `libtmux` in [dependencies] is
    # only the client library, so tmux can never be auto-installed by pip/uv and
    # must be preflighted here.
    #
    # `[session] use_tmux = false` opts a machine out of tmux entirely (equivalent
    # to always passing -b/--bare). tmux exists to keep sessions alive for detach
    # /reattach -- remote access from a phone, surviving a dropped SSH connection,
    # `ai ls`/`ai attach`. On a machine that only ever runs sessions in a local
    # terminal, it buys nothing and its absence should not be fatal.
    if not config.get("session", {}).get("use_tmux", True):
        bare = True

    if not bare and not shutil.which("tmux"):
        if sys.platform == "win32":
            # tmux is not standard on Windows; bare mode is the correct default.
            # Set [session] use_tmux = false in config.toml to suppress this notice.
            bare = True
        else:
            # Previously this check was gated on sys.platform == "win32", so every
            # non-Windows machine without tmux crashed with a raw FileNotFoundError
            # from deep inside cleanup_stale_sessions() instead of this message.
            if sys.platform == "darwin":
                _hint = "  brew install tmux"
            else:
                _hint = "  sudo apt install tmux     (or: dnf/yum/pacman/conda install tmux)"
            print(
                "Error: tmux not found, and it is required for the default session mode.\n"
                f"{_hint}\n"
                "\n"
                "Or run without tmux:\n"
                "  ai <engine> -b            one-off bare launch (no tmux)\n"
                "  [session] use_tmux = false     in ~/.config/ai-cli-utils/config.toml\n"
                "                            to make bare the default on this machine\n"
                "\n"
                "tmux provides detach/reattach (ai ls, ai attach), sessions that survive a\n"
                "dropped SSH connection, and remote access from another device. If you only\n"
                "run sessions in a local terminal, use_tmux = false is a fine permanent choice.",
                file=sys.stderr,
            )
            sys.exit(1)

    # direnv preflight, alongside tmux above for the same reason: it is a native
    # binary that pip/uv can never supply. Unlike tmux it is never fatal — the
    # engine runs fine without a project environment, and making a launch depend
    # on it is the exact regression AI-CLI-ai-c-direnv-jsqn fixed. So this
    # auto-installs when it can and otherwise prints remediation and continues.
    if not no_direnv:
        _direnv_setup.ensure_direnv(Path.cwd(), config)

    # Auto-promote to remote mode when running directly on a non-Mac host so
    # the c-r- / g-r- prefix is applied even without an explicit --is-remote flag.
    is_remote = _session._resolve_is_remote(is_remote)

    remote_cfg: dict | None = None
    if remote:
        try:
            remote_cfg = _config.get_remote_machine(config, remote_machine)
        except _config.RemoteMachineError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

    # Discovery is useful when creating an unqualified session, but a named
    # session or explicit project already identifies the launch target.  Do not
    # make that targeted launch wait for unrelated project-registration input.
    if (
        not project_prefix_override
        and not name
        and not project
        and not _config.validate_registry_completeness(interactive=sys.stdin.isatty())
    ):
        sys.exit(1)

    if project_prefix_override:
        project_prefix = project_prefix_override
    elif project:
        # An explicit project always derives its prefix from that project's
        # registered root, whether the session is local or remote.
        if "/" in project or "\\" in project:
            print("Error: --project name must not contain path separators", file=sys.stderr)
            sys.exit(1)
        _lp_aliases = _config.get_project_aliases()
        _lp_name = _lp_aliases.get(project, project)
        try:
            project_prefix = _config.resolve_project_prefix_by_name(_lp_name)
        except _config.ProjectPrefixError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        # No explicit project and no prefix override: the prefix would be derived
        # from cwd. If cwd isn't a resolvable project (not under ~/projects, not
        # registered, not inside an existing ai session), fail loudly instead of
        # fabricating a session from an unrelated directory (the old silent
        # "myproject"/cwd-derived fallback). Escape hatch: pass -p <project>.
        if not is_remote and not _session.is_current_project_resolved():
            print(
                "Error: no task prefix is registered for this repository.\n"
                f"  cwd: {Path.cwd()}\n"
                "  Fix: register the repository once, then retry:\n"
                f"    ai register -p {Path.cwd()} -x PREFIX",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            project_prefix = _session.get_project_prefix()
        except _config.ProjectPrefixError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
    engine_short = engine
    remote_seg = "-r" if is_remote else ""
    prefix = f"{engine_short}{remote_seg}-{project_prefix}-"

    use_sandbox = sandbox
    sandbox_flag = "-s" if use_sandbox else "--no-sandbox"
    gemini_cmd = config.get("gemini", {}).get("command", "gemini")

    if engine == "p" and not shutil.which("pi"):
        print("Error: pi executable not found on PATH. Install pi, then retry.", file=sys.stderr)
        sys.exit(1)
    if engine == "cx" and not shutil.which("codex"):
        print("Error: codex executable not found on PATH. Install Codex, then retry.", file=sys.stderr)
        sys.exit(1)

    if not name and extra_args:
        name = extra_args[0]
        extra_args = extra_args[1:]

    if remote:
        if remote_cfg is None:
            raise RuntimeError("remote machine was not resolved")
        host = remote_cfg.get("host", "")
        if not host:
            print("Error: [remote] host not set in ~/.config/ai-cli-utils/config.toml", file=sys.stderr)
            sys.exit(1)
        user = remote_cfg.get("user", "ubuntu")
        port = str(remote_cfg.get("port", 22))
        id_file = remote_cfg.get("identity_file", "")
        transport = remote_cfg.get("transport", "mosh")
        aliases = _config.get_project_aliases()
        raw_project = project or _config.get_current_project_name()
        if project and ("/" in project or "\\" in project):
            print("Error: --project name must not contain path separators", file=sys.stderr)
            sys.exit(1)
        remote_project = aliases.get(raw_project, raw_project)
        # When -p is provided, derive prefix from the target project's task_prefix
        if project:
            try:
                remote_prefix = _config.resolve_project_prefix_by_name(remote_project)
            except _config.ProjectPrefixError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                sys.exit(1)
        else:
            remote_prefix = project_prefix
        # vpn_host: direct-IP host used for SSH when VPN is active (bypasses Tailscale/WireGuard
        # which becomes unreachable when a split-tunneling VPN like Mullvad takes over routing).
        # Falls back to host when not set.
        vpn_host = remote_cfg.get("vpn_host", "") or host
        ssh_args = ["ssh", "-t", "-p", port]
        preflight_ssh_args = ["ssh", "-T", "-p", port]
        if id_file:
            identity_file = str(Path(id_file).expanduser())
            ssh_args += ["-i", identity_file]
            preflight_ssh_args += ["-i", identity_file]
        ssh_args.append(f"{user}@{vpn_host}")
        preflight_ssh_args.append(f"{user}@{vpn_host}")
        # Prepend ~/.local/bin to PATH so `ai` is found on the remote side even
        # when the shell is a non-interactive login shell (zsh -l -c) that does
        # not source ~/.zshrc where the uv env PATH setup typically lives.
        remote_cmd = f'export PATH="$HOME/.local/bin:$PATH"; ai {engine} --is-remote --project-prefix {shlex.quote(remote_prefix)} --project {shlex.quote(remote_project)}'
        if resume:
            remote_cmd += " --resume"
        remote_session_id = ""
        if name and not name.isdigit():
            try:
                remote_session_id, _ = _request_remote_session_allocation(
                    preflight_ssh_args, engine, remote_prefix, name
                )
            except RuntimeError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                sys.exit(1)
        if remote_session_id:
            remote_cmd += f" {shlex.quote(remote_session_id)}"
        elif name:
            remote_cmd += f" {shlex.quote(name)}"
        # Emit iTerm2 profile/color before mosh/ssh takes over the pane.
        # mosh blocks all \033]1337; sequences from the remote side, so this
        # is the only opportunity to set the profile and tab color.
        _r_engine_short = engine
        _r_ai_name = remote_session_id or _session._new_session_display_name(
            _r_engine_short, remote_prefix, name or "1", True
        )
        _iterm2_remote_slot = _iterm2._assign_iterm2_color_slot(_r_ai_name, engine)
        _iterm2._emit_iterm2_profile_setup(_r_ai_name, engine, _r_ai_name, slot=_iterm2_remote_slot)

        _cleanup_cmd = ["ai", "internal", "cleanup-session-files", _r_ai_name]
        ssh_args.append(f"zsh -l -c {shlex.quote(remote_cmd)}")

        # Build mosh_args unconditionally — needed for both initial connection
        # and for reconnecting after a VPN drop while on SSH.
        # mosh always uses the primary host (Tailscale/LAN) since it only runs without VPN.
        # ConnectTimeout=10 on the SSH phase ensures mosh fails fast (error + exit) instead
        # of hanging silently for ~2 minutes when the host is unreachable (e.g. Tailscale down).
        mosh_args = ["mosh"]
        _mosh_ssh = "ssh -o ConnectTimeout=10"
        if port != "22":
            _mosh_ssh += f" -p {port}"
        if id_file:
            _mosh_ssh += f" -i {shlex.quote(str(Path(id_file).expanduser()))}"
        mosh_args += ["--ssh", _mosh_ssh]
        mosh_args.append(f"{user}@{host}")
        mosh_args += ["--", "zsh", "-l", "-c", remote_cmd]

        if transport == "mosh":
            _transport._ensure_vpn_watcher(config)
            import asyncio as _asyncio

            try:
                _asyncio.run(
                    _transport._run_transport_loop(
                        ssh_args, mosh_args, _cleanup_cmd, _r_ai_name, config, tailscale_host=host
                    )
                )
            finally:
                _transport._maybe_stop_vpn_watcher()
            sys.exit(0)
        else:
            # Pure SSH transport — no VPN switching.
            if sys.platform == "win32":
                print("Error: remote SSH transport is not supported on Windows", file=sys.stderr)
                sys.exit(1)
            os.execvp("zsh", ["zsh", "-c", f"{shlex.join(ssh_args)}; {shlex.join(_cleanup_cmd)} 2>/dev/null"])

    # When running as the remote side of an --remote session, cd into the project directory
    # before creating the worktree so git commands work correctly.
    if is_remote:
        aliases = _config.get_project_aliases()
        raw_project = project or _config.get_remote_machine(config).get("project") or _config._get_main_project_name()
        if raw_project:
            project_name = aliases.get(raw_project, raw_project)
            project_dir = _config._find_project_dir(project_name)
            if project_dir.exists():
                os.chdir(project_dir)
    elif project:
        # Local session with explicit -p PROJECT: cd to the project directory so that
        # git worktrees and Gemini chats directories resolve relative to the correct root.
        # Mirrors the is_remote path above.
        aliases = _config.get_project_aliases()
        _local_project = aliases.get(project, project)
        _local_project_dir = _config._find_project_dir(_local_project)
        if _local_project_dir.exists():
            os.chdir(_local_project_dir)

    # Register workspace trust for the launch directory before starting Claude
    # Code. With ~/projects trusted as an ancestor, CC suppresses the trust
    # dialog for subfolders but still exact-matches gitRoot(cwd) when loading
    # permissions.allow — so an unregistered workspace silently drops its
    # .claude/settings.json permissions (GH #72896). This covers the bare and
    # --no-worktree paths; create_worktree() covers the worktree path.
    if engine == "c":
        from .trust import ensure_workspace_trusted

        ensure_workspace_trusted([Path.cwd()])

    # NOTE: bare mode does NOT short-circuit here. It shares the worktree
    # creation, session naming, and resume resolution below with the tmux path,
    # and execs the engine once that setup is done (see the `if bare:` block
    # after worktree setup). Returning early here was a long-standing bug: on any
    # machine with `[session] use_tmux = false` — or any `-b/--bare` launch —
    # `ai c N` silently degraded to a plain `claude` in the repo root, with no
    # worktree isolation, no --name, and no session resume. Nothing about
    # worktrees requires tmux; the two concerns were only ever coupled by the
    # order of these statements.

    # -r/--resume means "re-attach to the running tmux session". In bare mode
    # there is no tmux session to attach to, and the engine's own conversation
    # resume is already applied unconditionally below, so the flag is a no-op
    # rather than an error.
    if resume and not bare:
        session = _session.resolve_session(prefix, name)
        if not session:
            print(f"No matching session found for '{prefix}{name or '*'}'")
            sys.exit(1)
        os.execvp("tmux", ["tmux", "attach-session", "-t", session])

    # Stale-session sweeping and index discovery both drive tmux. In bare mode
    # there is no tmux server to query (it may not even be installed), so skip
    # the sweep and let build_session_name fall back to its non-tmux path.
    if not bare:
        _session.cleanup_stale_sessions(config)
    current_project_name = _config.get_current_project_name()
    try:
        session_id, ai_name = _session.build_session_name(
            engine, project_prefix, name, config, is_remote=is_remote, use_tmux=not bare
        )
    except _session.SessionSlotAmbiguityError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Worktree setup
    worktree_path = None
    if config.get("worktree", {}).get("enabled", True) and not no_worktree:
        # Repair backstop (AI-CLI-99), early — before any git work this launch does.
        # repo_root is the shared main working tree and must never be
        # core.bare=true / carry a stale core.worktree, regardless of source
        # (leaked GIT_* env, a prior interrupted worktree op, CC's own
        # isolation:worktree tool, ...).
        _repair_root = _session.detect_repo_root()
        if _repair_root:
            repair_bare_worktree_config(_repair_root)
        try:
            worktree_result = _session.create_worktree(ai_name, with_status=True)
        except RuntimeError as exc:
            print(
                f"Error: could not create or reuse the isolated session worktree; refusing to launch in the "
                f"repository root. {exc} Re-run after resolving the git worktree error, or explicitly use "
                f"--no-worktree.",
                file=sys.stderr,
            )
            sys.exit(1)
        if isinstance(worktree_result, tuple):
            worktree_path, worktree_created = worktree_result
        else:
            # Compatibility for callers that replace create_worktree in-process.
            worktree_path, worktree_created = worktree_result, False
        if not worktree_path:
            print(
                "Error: could not create or reuse the isolated session worktree; refusing to launch in the "
                "repository root. Re-run after resolving the git worktree error, or explicitly use --no-worktree.",
                file=sys.stderr,
            )
            sys.exit(1)
        _announce_worktree_isolation(worktree_path, worktree_created)
        if worktree_path:
            # Self-healing: detect index corruption (many staged deletions that don't reflect
            # disk state) BEFORE --autostash captures the corrupt state. If left unfixed,
            # --autostash saves the corrupt index, rebase runs cleanly, then pops the corrupt
            # stash back — perpetuating the corruption across sessions.
            # Threshold matches corrupt-index-guard.sh (50 staged deletions).
            _deleted = subprocess.run(
                ["git", "diff", "--cached", "--name-only", "--diff-filter=D"],
                capture_output=True,
                text=True,
                cwd=worktree_path,
                env=_git_env(),
                check=False,
            )
            if len(_deleted.stdout.strip().splitlines()) > 50:
                subprocess.run(
                    ["git", "read-tree", "HEAD"], capture_output=True, cwd=worktree_path, env=_git_env(), check=False
                )
                subprocess.run(
                    ["git", "update-index", "--refresh"],
                    capture_output=True,
                    cwd=worktree_path,
                    env=_git_env(),
                    check=False,
                )
                print(
                    f"Info: index corruption auto-healed in {worktree_path.name} "
                    f"({len(_deleted.stdout.strip().splitlines())} staged deletions reset to HEAD).",
                    file=sys.stderr,
                )
            # Sync worktree with any changes that landed on main from other sessions.
            # AIH-443 Shape B: this pull exits 0 even when its automatic stash pop
            # conflicted, so `returncode` alone cannot gate the launch. pull_rebase_autostash
            # measures repo state either side of the call instead.
            _conflicted_before = _has_conflict_or_unknown(worktree_path)
            pull, stranded = pull_rebase_autostash(worktree_path)
            if pull.returncode != 0 and not stranded and not _conflicted_before:
                # Reset only when the tree was clean beforehand, so this cleanup can
                # only ever undo work THIS launch started. Doing it unconditionally
                # would abort a rebase the user is part-way through and wipe their
                # conflict resolution — the opposite of the intent.
                subprocess.run(
                    ["git", "rebase", "--abort"], capture_output=True, cwd=worktree_path, env=_git_env(), check=False
                )
                subprocess.run(
                    ["git", "restore", "--staged", "."],
                    capture_output=True,
                    cwd=worktree_path,
                    env=_git_env(),
                    check=False,
                )
                # Quote git's own last line. An exit code alone does not say whether
                # this was no network, missing credentials for the remote, or something
                # that needs attention, and the user cannot re-run the pull to find out
                # once the index has been restored.
                _reason = (pull.stderr or pull.stdout or "").strip().splitlines()
                _detail = f" Last git error: {_reason[-1]}" if _reason else ""
                print(
                    f"Warning: git pull --rebase failed in worktree {worktree_path.name} "
                    f"(exit {pull.returncode}) — starting the session on the branch as-is "
                    f"(it may be behind main). Index restored to HEAD.{_detail}",
                    file=sys.stderr,
                )
            elif pull.returncode != 0 and not stranded:
                print(
                    f"Warning: git pull --rebase failed in worktree {worktree_path.name} "
                    f"(exit {pull.returncode}). Left as-is — this worktree already had "
                    f"a conflict in progress and nothing here will discard it.",
                    file=sys.stderr,
                )
            if stranded:
                # Refuse the launch. Dropping an agent into a worktree whose index
                # carries conflict stages is how AIH-443's phantom deletions spread
                # across six worktrees. Nothing is auto-repaired: the user's work is
                # in the stash and only they can say how to reconcile it.
                print(
                    f"Error: syncing worktree {worktree_path.name} stranded it ({stranded}).\n"
                    f"  `git pull --rebase --autostash` exited {pull.returncode} but did not finish cleanly.\n"
                    f"  Refusing to launch a session into a conflicted worktree.\n"
                    f"  Nothing was discarded. Inspect, then resolve:\n"
                    f"    git -C {worktree_path} status\n"
                    f"    git -C {worktree_path} stash list",
                    file=sys.stderr,
                )
                sys.exit(1)
            # Repair backstop again after this launch's git work.
            if _repair_root:
                repair_bare_worktree_config(_repair_root)
            # AIH-443 Shape A: a Claude Code `isolation: worktree` checkout can silently
            # drop tracked symlinks (confirmed: 21 symlinks missing from disk in one
            # sub-agent worktree while HEAD and origin/main both had them, no error
            # anywhere). Not something this launcher can fix at the source, but it can
            # stop it from being silent.
            _missing_symlinks = detect_missing_tracked_symlinks(worktree_path)
            if _missing_symlinks:
                print(
                    f"WARNING: {worktree_path.name} is missing {len(_missing_symlinks)} tracked "
                    f"symlink(s) present in HEAD (checkout dropped them silently) — "
                    f"e.g. {_missing_symlinks[0]}. Restore with "
                    f"`git -C {worktree_path} checkout -- <path>`.",
                    file=sys.stderr,
                )
            # AIH-443 Shape C: a tracked REGULAR file the index still holds but that
            # is gone from disk. Neither check above can see it — there is no stranded
            # stash (pre-commit uses its own patch file under ~/.cache/pre-commit, not
            # `git stash`) and the mode is not 120000. pre-commit's `staged_files_only`
            # then re-applies the deletion after every hook run, so the worktree never
            # self-heals and `git status` looks identical each time.
            _phantom = detect_phantom_deleted_files(worktree_path)
            if _phantom:
                print(
                    f"WARNING: {worktree_path.name} is missing {len(_phantom)} tracked "
                    f"file(s) that the index still holds — e.g. {_phantom[0]}. "
                    f"Committing now would delete content that is still live on the "
                    f"remote. Restore with "
                    f"`git -C {worktree_path} checkout -- <path>` before committing.",
                    file=sys.stderr,
                )

    # For Gemini, always check the chats directory for the latest session — the
    # session map may be stale if the user exited and restarted directly via gemini CLI.
    uuid = None
    if engine == "g":
        d = _config.get_session_map(engine)
        uuid = d.get(ai_name)
        latest = _session._find_latest_gemini_uuid(ai_name)
        if latest and latest != uuid:
            uuid = latest
            d[ai_name] = uuid
            _config.save_session_map(d, engine)

    if bare:
        # Bare launch: no tmux, but everything else the tmux path sets up has now
        # run — worktree created and synced, ai_name assigned, resume target
        # resolved. cd into the worktree first: `direnv exec DIR cmd` loads DIR's
        # environment but does NOT change the working directory, so without this
        # the engine would start in the repo root with the worktree's env.
        target_root = worktree_path or Path.cwd()
        try:
            os.chdir(target_root)
        except OSError as exc:
            print(f"Error: cannot enter session directory {target_root}: {exc}", file=sys.stderr)
            sys.exit(1)
        if engine == "c":
            # Pin the task-list namespace to ai_name, matching the tmux session
            # script, so the CC task panel survives process restarts.
            os.environ["CLAUDE_CODE_TASK_LIST_ID"] = ai_name
        command = _bare_engine_command(
            engine, ai_name, target_root, uuid, gemini_cmd, sandbox_flag, extra_args, resume=resume
        )
        _exec_with_direnv(target_root, command)

    # Propagate iTerm2 env vars into the tmux session — tmux doesn't inherit these,
    # so _iterm2_fleet_setup inside the bash script would silently no-op without them.
    # The pane is renamed by live client tty (not a stored GUID), so ITERM_SESSION_ID
    # no longer needs to be propagated or reconciled — only the terminal-type flags.
    _iterm_env_flags: list[str] = []
    for _var in ("LC_TERMINAL", "TERM_PROGRAM"):
        if _val := os.environ.get(_var):
            _iterm_env_flags += ["-e", f"{_var}={_val}"]

    if once:
        target_root = worktree_path or Path.cwd()
        cd_prefix = f"cd {shlex.quote(str(target_root))} && "
        # Resolved once for the whole --once branch: the pane interpreter must
        # exist (an absent one dies on exec and shows only `[exited]`), and direnv
        # must be optional (an absent one made `direnv exec` fail closed at 127).
        _session_shell = _session_shell_or_exit()
        _direnv = _direnv_prefix(target_root)
        if engine == "c":
            command = ["claude"]
            if not _is_root():
                command.append("--dangerously-skip-permissions")
            command += ["--name", ai_name]
            os.execvp(
                "tmux",
                [
                    "tmux",
                    "new-session",
                    "-s",
                    session_id,
                    *_iterm_env_flags,
                    "--",
                    _session_shell,
                    "-c",
                    cd_prefix + shlex.join([*_direnv, *command]),
                ],
            )
        elif engine == "g":
            command = [*shlex.split(gemini_cmd), "-y", sandbox_flag]
            if uuid:
                command += ["-r", uuid]
                os.execvp(
                    "tmux",
                    [
                        "tmux",
                        "new-session",
                        "-s",
                        session_id,
                        *_iterm_env_flags,
                        "--",
                        _session_shell,
                        "-c",
                        cd_prefix + shlex.join([*_direnv, *command]),
                    ],
                )
            else:
                command += ["-i", f"/resume load {ai_name}"]
                os.execvp(
                    "tmux",
                    [
                        "tmux",
                        "new-session",
                        "-s",
                        session_id,
                        *_iterm_env_flags,
                        "--",
                        _session_shell,
                        "-c",
                        cd_prefix + shlex.join([*_direnv, *command]),
                    ],
                )
        elif engine == "p":
            command = ["pi", "--name", ai_name]
        else:
            command = ["codex"]
        os.execvp(
            "tmux",
            [
                "tmux",
                "new-session",
                "-s",
                session_id,
                *_iterm_env_flags,
                "--",
                _session_shell,
                "-c",
                cd_prefix + shlex.join([*_direnv, *command]),
            ],
        )

    # Assign iTerm2 color slot before generating the script so both the pre-launch
    # emission and the embedded bash variables use the same slot.
    _iterm2_cfg = _iterm2._load_iterm2_config()
    _iterm2_slot = _iterm2._assign_iterm2_color_slot(ai_name, engine, project_name=current_project_name)

    _config_reload_idle_secs = int(config.get("session", {}).get("config_reload_idle_secs", 90))
    script = _session_script.get_engine_script(
        engine,
        ai_name,
        session_id,
        prefix,
        project_prefix,
        uuid,
        use_sandbox,
        str(worktree_path) if worktree_path else None,
        notify=notify,
        is_remote=is_remote,
        project_name=current_project_name,
        iterm2_slot=_iterm2_slot,
        iterm2_cfg=_iterm2_cfg,
        config_reload_idle_secs=_config_reload_idle_secs,
        gemini_cmd=gemini_cmd,
    )
    # Emit iTerm2 profile/color/title now, before tmux takes over the pane.
    # This fires in the current shell (no DCS wrapping needed) so it works
    # for new tabs, split panes, and re-attaches alike.
    _iterm2._emit_iterm2_profile_setup(
        ai_name, engine, session_id, slot=_iterm2_slot, project_name=current_project_name
    )

    # Check if session already exists (e.g., re-attaching after disconnect)
    existing = subprocess.run(["tmux", "has-session", "-t", session_id], capture_output=True, check=False)
    if existing.returncode == 0 and sandbox:
        # Explicit sandbox flag — kill old session so it recreates with new settings
        subprocess.run(["tmux", "kill-session", "-t", session_id], capture_output=True, check=False)
        existing = subprocess.run(["tmux", "has-session", "-t", session_id], capture_output=True, check=False)
    # Stable script path: refreshed on every launch/re-attach so the session script's
    # mtime check detects updates (e.g. after `ai update`) and hot-reloads. Written
    # only when the template actually changed — otherwise a plain re-attach would bump
    # the mtime and make the running wrapper exec a pointless reload (AI-CLI-129).
    _sessions_dir = _config.get_xdg_state_home() / "sessions"
    _script_path = str(_sessions_dir / f"{session_id}.sh")
    _write_launch_script_if_changed(Path(_script_path), script)

    if existing.returncode == 0:
        # Session exists — write fresh script (hot-reload detection), configure
        # for iTerm2, then attach (detach stale clients). The pane is renamed by
        # live client tty inside the session script, so there is no GUID to
        # reconcile here on re-attach.
        _iterm2._configure_tmux_for_iterm2(session_id)
        _iterm2._rename_tmux_window(session_id, ai_name)
        os.execvp("tmux", ["tmux", "attach-session", "-d", "-t", session_id])
    else:
        # New session: create detached so tmux options can be set before attaching.
        # tmux always allocates a PTY for the pane regardless of client attachment,
        # so Claude Code gets a proper PTY once we attach immediately after.
        _session_shell = _session_shell_or_exit()
        result = subprocess.run(
            ["tmux", "new-session", "-d", "-s", session_id, *_iterm_env_flags, "--", _session_shell, _script_path],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raw = result.stderr
            stderr = _decode_tmux_stderr(raw).strip()
            # Mac tmux may not support `--` separator — retry without it
            result2 = subprocess.run(
                ["tmux", "new-session", "-d", "-s", session_id, *_iterm_env_flags, _session_shell, _script_path],
                capture_output=True,
                check=False,
            )
            if result2.returncode != 0:
                raw2 = result2.stderr
                stderr2 = _decode_tmux_stderr(raw2).strip()
                Path(_script_path).unlink()
                print(f"Error: failed to create tmux session '{session_id}'", file=sys.stderr)
                print(f"  (with --): {stderr}", file=sys.stderr)
                print(f"  (without --): {stderr2}", file=sys.stderr)
                sys.exit(1)
        _iterm2._configure_tmux_for_iterm2(session_id)
        _iterm2._rename_tmux_window(session_id, ai_name)
        os.execvp("tmux", ["tmux", "attach-session", "-d", "-t", session_id])


# --- Click command tree ---


SESSION_CONTEXT = {
    "ignore_unknown_options": True,
    "allow_extra_args": True,
    "help_option_names": ["-h", "--help"],
}


def _session_command(engine: str):
    """Factory for the shared provider session-launch commands."""

    def _impl(
        ctx,
        name,
        resume,
        once,
        bare,
        notify,
        sandbox,
        no_worktree,
        no_direnv,
        remote,
        remote_machine,
        project,
        is_remote,
        project_prefix,
    ):
        if remote_machine and not remote:
            raise click.UsageError("--remote-machine requires -R/--remote")
        # Startup hooks happen only when launching a new session.
        config = _config.load_config()
        trigger_background_update()
        if _auto_update_if_stale(config) is True:
            ai_bin = shutil.which("ai") or "ai"
            os.execvp(ai_bin, [ai_bin, *sys.argv[1:]])
        _tunnel._ensure_nats_tunnel(config)
        _do_session_launch(
            engine=engine,
            name=name,
            resume=resume,
            once=once,
            bare=bare,
            notify=notify,
            sandbox=sandbox,
            no_worktree=no_worktree,
            remote=remote,
            project=project,
            is_remote=is_remote,
            project_prefix_override=project_prefix,
            extra_args=list(ctx.args),
            config=config,
            remote_machine=remote_machine,
            no_direnv=no_direnv,
        )

    _impl.__name__ = f"cmd_session_{engine}"
    return _impl


@click.group(
    name="ai",
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option("-V", "--version", is_flag=True, help="Show version and exit")
@click.pass_context
def _cli_group(ctx, version):
    """Unified AI CLI for Claude, Gemini, Pi, and Codex."""
    if version:
        click.echo(_pkg_version_string())
        ctx.exit(0)
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        ctx.exit(0)


def _session_options(func):
    """Decorator that adds the shared session-launch options."""
    func = click.pass_context(func)
    func = click.argument("name", required=False, default="")(func)
    func = click.option("-r", "--resume", is_flag=True, help="Resume an existing session")(func)
    func = click.option("-o", "--once", is_flag=True, help="Run once without tmux auto-resume loop")(func)
    func = click.option("-b", "--bare", is_flag=True, help="Run bare tool without tmux at all")(func)
    func = click.option("-n", "--notify", is_flag=True, help="Fire system notifications on task completion")(func)
    func = click.option("-s", "--sandbox", is_flag=True, help="Enable sandboxing (default: off)")(func)
    func = click.option("-W", "--no-worktree", is_flag=True, help="Disable git worktree isolation")(func)
    func = click.option("-D", "--no-direnv", is_flag=True, help="Skip the direnv preflight and auto-install")(func)
    func = click.option("-R", "--remote", is_flag=True, help="Run session on the default remote machine")(func)
    func = click.option("-m", "--remote-machine", default="", help="Remote-machine alias to use with -R/--remote")(func)
    func = click.option(
        "-p",
        "--project",
        default="",
        help="Project to open on remote server (directory name, e.g. 'myproject', 'webapp')",
    )(func)
    func = click.option("--is-remote", is_flag=True, hidden=True)(func)
    return click.option("--project-prefix", default="", hidden=True)(func)


@_cli_group.command("c", context_settings=SESSION_CONTEXT, help="Launch a Claude Code session")
@_session_options
def cmd_c(
    ctx,
    name,
    resume,
    once,
    bare,
    notify,
    sandbox,
    no_worktree,
    no_direnv,
    remote,
    remote_machine,
    project,
    is_remote,
    project_prefix,
):
    _session_command("c")(
        ctx,
        name,
        resume,
        once,
        bare,
        notify,
        sandbox,
        no_worktree,
        no_direnv,
        remote,
        remote_machine,
        project,
        is_remote,
        project_prefix,
    )


@_cli_group.command("g", context_settings=SESSION_CONTEXT, help="Launch a Gemini CLI session")
@_session_options
def cmd_g(
    ctx,
    name,
    resume,
    once,
    bare,
    notify,
    sandbox,
    no_worktree,
    no_direnv,
    remote,
    remote_machine,
    project,
    is_remote,
    project_prefix,
):
    _session_command("g")(
        ctx,
        name,
        resume,
        once,
        bare,
        notify,
        sandbox,
        no_worktree,
        no_direnv,
        remote,
        remote_machine,
        project,
        is_remote,
        project_prefix,
    )


@_cli_group.command("p", context_settings=SESSION_CONTEXT, help="Launch a Pi session")
@_session_options
def cmd_p(
    ctx,
    name,
    resume,
    once,
    bare,
    notify,
    sandbox,
    no_worktree,
    no_direnv,
    remote,
    remote_machine,
    project,
    is_remote,
    project_prefix,
):
    _session_command("p")(
        ctx,
        name,
        resume,
        once,
        bare,
        notify,
        sandbox,
        no_worktree,
        no_direnv,
        remote,
        remote_machine,
        project,
        is_remote,
        project_prefix,
    )


@_cli_group.command("cx", context_settings=SESSION_CONTEXT, help="Launch a Codex session")
@_session_options
def cmd_cx(
    ctx,
    name,
    resume,
    once,
    bare,
    notify,
    sandbox,
    no_worktree,
    no_direnv,
    remote,
    remote_machine,
    project,
    is_remote,
    project_prefix,
):
    _session_command("cx")(
        ctx,
        name,
        resume,
        once,
        bare,
        notify,
        sandbox,
        no_worktree,
        no_direnv,
        remote,
        remote_machine,
        project,
        is_remote,
        project_prefix,
    )


@_cli_group.command("upgrade", help="Upgrade ai-cli-utils via uv tool upgrade")
def cmd_upgrade():
    print("Upgrading ai-cli-utils...", file=sys.stderr)
    uv_bin = shutil.which("uv")
    if not uv_bin:
        print(
            "Cannot find 'uv' on PATH — unable to upgrade. Install uv, or add its\n"
            "directory to PATH, then re-run 'ai upgrade'.",
            file=sys.stderr,
        )
        sys.exit(1)
    # Pass the resolved absolute path, not the bare name: on Windows a bare name is
    # not resolvable by CreateProcess/execvp and raises FileNotFoundError.
    upgrade_args = [uv_bin, "tool", "upgrade", "ai-cli-utils"]
    if _should_use_uv_link_mode_copy(uv_bin):
        upgrade_args.append("--link-mode=copy")
    os.execvp(uv_bin, upgrade_args)


@_cli_group.command("register", help="Register a repository root and its task prefix")
@click.option("-p", "--project", required=True, help="Repository path or project directory name")
@click.option("-x", "--prefix", required=True, help="Task and session prefix")
@click.option("-t", "--type", "project_type", default="tool", show_default=True, help="Project type")
def cmd_register(project, prefix, project_type):
    """Persist one repository-root prefix mapping in config.toml."""
    try:
        root = _config.register_project(project, prefix, project_type)
    except _config.ProjectPrefixError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Registered {root} with prefix {prefix}")


@_cli_group.command("setup", help="Run interactive setup wizard")
def cmd_setup():
    from .setup import run_setup

    sys.exit(run_setup())


@_cli_group.command("doctor", help="Check the native binaries ai-cli-utils needs, installing what it can")
@click.option("-n", "--dry-run", is_flag=True, help="Report status only; install nothing")
def cmd_doctor(dry_run):
    """Report on the native dependencies pip/uv cannot supply, and repair direnv.

    Unlike the launch-time preflight this exits non-zero when something is still
    unusable: `ai doctor` is where an operator has asked to be told, so a silent
    pass would defeat the point.
    """
    config = _config.load_config()
    root = Path.cwd()

    # tmux is reported, never installed: `[session] use_tmux = false` and -b/--bare
    # are both legitimate permanent answers, so its absence is not a defect.
    for label, present, note in (
        ("bash", _direnv_setup.bash_available(), "required by direnv to evaluate .envrc"),
        ("tmux", shutil.which("tmux") is not None, "optional; -b/--bare and use_tmux=false opt out"),
    ):
        click.echo(f"  {'OK  ' if present else 'MISS'}  {label:<8} {note}")

    if _direnv_setup.is_bypassed(config):
        click.echo(f"  SKIP  direnv    bypassed via {_direnv_setup.BYPASS_ENV} or [direnv] enabled = false")
        return

    envrc = _direnv_setup.find_envrc(root)
    result = _direnv_setup.ensure_direnv(root, config, auto_install=not dry_run)
    if result.installed:
        click.echo(f"  OK    direnv    {result.detail or result.tool or 'usable'}")
        return
    click.echo(f"  MISS  direnv    {envrc} will not load", err=True)
    raise SystemExit(1)


# --- handoff group ---


@_cli_group.group("handoff", help="Post and claim handoffs from the shared queue")
def cmd_handoff_group():
    pass


@cmd_handoff_group.command(
    "post",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
    help="Post a new handoff (run ssh when --remote is passed)",
)
@click.option("-m", "--for-machine", default="", help="Machine the handoff targets (required)")
@click.option("-R", "--remote", is_flag=True, help="Post on the remote server instead of locally")
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def cmd_handoff_post(for_machine, remote, args):
    _do_handoff_post(remote=remote, for_machine=for_machine, post_args=list(args))


@cmd_handoff_group.command("check", help="Check for handoffs targeted at this machine")
def cmd_handoff_check():
    _handoff.check_handoff()
    sys.exit(0)


@cmd_handoff_group.command("check-project", help="Check pending handoffs for a specific project")
@click.argument("project_name")
def cmd_handoff_check_project(project_name):
    _handoff.check_handoff_project(project_name)
    sys.exit(0)


@cmd_handoff_group.command("claim", help="Mark a handoff as claimed")
@click.argument("file_path")
def cmd_handoff_claim(file_path):
    _handoff.claim_handoff(file_path)
    sys.exit(0)


@cmd_handoff_group.command("complete", help="Mark a handoff as complete")
@click.argument("file_path")
def cmd_handoff_complete(file_path):
    _handoff.complete_handoff(file_path)
    sys.exit(0)


# --- memory group ---


@_cli_group.group("memory", help="Memory-file watchers and helpers")
def cmd_memory_group():
    pass


@cmd_memory_group.command("watch", help="Run the memory-file watcher daemon")
def cmd_memory_watch():
    from .memory import memory_watch

    sys.exit(memory_watch())


# --- quota group ---


@_cli_group.group("quota", help="Claude weekly-quota tracking and statusline helpers")
def cmd_quota_group():
    pass


@cmd_quota_group.group("watch", help="Quota-watch Circus daemon management")
def cmd_quota_watch_group():
    pass


@cmd_quota_watch_group.command("start", help="Register quota-watch with Circus and start it")
@click.option(
    "--auto",
    is_flag=True,
    default=False,
    hidden=True,
    help="Per-session auto-start path — gated on [quota_watch] auto_start in config.toml.",
)
def cmd_quota_watch_start(auto):
    _process_manager._cmd_quota_watch_start(auto=auto)
    sys.exit(0)


@cmd_quota_watch_group.command("stop", help="Stop quota-watch")
def cmd_quota_watch_stop():
    _process_manager._cmd_quota_watch_stop()
    sys.exit(0)


@cmd_quota_watch_group.command("status", help="Show quota-watch Circus status")
def cmd_quota_watch_status():
    _process_manager._cmd_quota_watch_status()
    sys.exit(0)


@cmd_quota_watch_group.command("run", help="Run quota-watch daemon (Circus entry point)")
@click.option("-i", "--interval", "poll_interval", default=300, show_default=True, help="Poll interval in seconds")
def cmd_quota_watch_run(poll_interval):
    from .quota import quota_watch

    sys.exit(quota_watch(poll_interval))


@cmd_quota_group.command("status", help="Print current quota snapshot")
def cmd_quota_status():
    from .quota import quota_status

    sys.exit(quota_status())


@cmd_quota_group.command("history", help="Print quota history")
def cmd_quota_history():
    from .quota import quota_history

    sys.exit(quota_history())


@cmd_quota_group.command("scrape", help="Scrape /usage from a fresh CC session")
def cmd_quota_scrape():
    from .quota import quota_scrape

    sys.exit(quota_scrape())


@cmd_quota_group.command("statusline-part", help="Emit the quota section of the CC statusline")
def cmd_quota_statusline_part():
    from .quota import quota_statusline_part

    sys.exit(quota_statusline_part())


@cmd_quota_group.command("sync", help="Sync quota snapshot from the remote server")
def cmd_quota_sync():
    from .quota import quota_sync_from_remote

    sys.exit(quota_sync_from_remote())


@cmd_quota_group.command("record", help="Record a single AI-usage event into the quota DB")
@click.argument("session_id")
@click.argument("machine_id")
@click.argument("model")
@click.argument("total_tokens", type=int)
@click.argument("cost_usd", type=float, required=False, default=None)
def cmd_quota_record(session_id, machine_id, model, total_tokens, cost_usd):
    from .quota import quota_record

    sys.exit(quota_record(session_id, machine_id, model, total_tokens, cost_usd))


# --- notifications group ---


@_cli_group.group("notifications", help="Notification delivery and history")
def cmd_notifications_group():
    pass


@cmd_notifications_group.command("list", help="List configured notification channels")
def cmd_notifications_list():
    from .notifications import Notifier

    notifier = Notifier()
    channels = notifier.list_channels()
    print(f"{'Channel':<10} {'Enabled':<10} {'Credentials'}")
    print("-" * 55)
    for ch in channels:
        enabled_str = "yes" if ch["enabled"] else "no"
        creds_str = (
            ", ".join(f"{k}: {v}" for k, v in ch["credentials"].items())
            if ch["credentials"]
            else "(no credentials needed)"
        )
        print(f"{ch['name']:<10} {enabled_str:<10} {creds_str}")
    sys.exit(0)


@cmd_notifications_group.command("log", help="Show notification history")
@click.option("-n", "--last", default=10, show_default=True, help="Show last N notifications")
@click.option("-s", "--since", default=None, help="Since DATETIME or relative (2h, 30m, 1d, yesterday)")
@click.option("-f", "--from", "from_date", default=None, help="Start of date range (inclusive)")
@click.option("-t", "--to", "to_date", default=None, help="End of date range (inclusive)")
@click.option("--source", default=None, help="Filter by source (e.g. quota-watch)")
@click.option("--failed", is_flag=True, help="Show only notifications with at least one failed channel")
def cmd_notifications_log(last, since, from_date, to_date, source, failed):
    from .quota_db import query_notification_log

    rows = query_notification_log(
        last=last,
        since=since,
        from_date=from_date,
        to_date=to_date,
        source=source,
        failed_only=failed,
    )
    if not rows:
        print("No notifications found.")
        sys.exit(0)
    print(f"{'Time':<22} {'Source':<14} {'Title':<36} {'Channels'}")
    print("-" * 90)
    for row in rows:
        time_str = row["fired_at"][:19].replace("T", " ")
        succeeded = row.get("channels_succeeded", [])
        channels_str = " ".join(f"{ch}✓" if ch in succeeded else f"{ch}✗" for ch in row.get("channels_attempted", []))
        title_val = row["title"]
        title_trunc = title_val[:33] + "..." if len(title_val) > 36 else title_val
        print(f"{time_str:<22} {row.get('source', ''):<14} {title_trunc:<36} {channels_str}")
    sys.exit(0)


# --- telemetry group ---


@_cli_group.group("telemetry", help="Telemetry writer daemon")
def cmd_telemetry_group():
    pass


@cmd_telemetry_group.command("writer", help="Run the telemetry writer daemon")
def cmd_telemetry_writer():
    from .telemetry import telemetry_writer

    sys.exit(telemetry_writer())


# --- spend, cc-usage, copier-update, layout, color ---


@_cli_group.group("spend", help="Usage-cost reports")
def cmd_spend_group():
    pass


@cmd_spend_group.command("gemini", help="Report historical Gemini CLI spend from local logs")
def cmd_spend_gemini_cli():
    from .spend import cmd_spend_gemini

    sys.exit(cmd_spend_gemini(_config.load_config()))


@_cli_group.group("cc-usage", help="Claude Code CLI per-call token usage")
def cmd_cc_usage_group():
    pass


@cmd_cc_usage_group.command("push", help="Scan JSONL session files and push new events")
@click.option("-d", "--dry-run", is_flag=True, help="Parse but do not push")
def cmd_cc_usage_push(dry_run):
    from .cc_usage import scan_and_push

    config = _config.load_config()
    result = scan_and_push(config=config, dry_run=dry_run)
    if result.error:
        print(f"Error: {result.error}", file=sys.stderr)
        sys.exit(1)
    if dry_run:
        print(f"Dry run: {result.new_events} new events across {result.scanned_sessions} sessions (not pushed)")
    else:
        print(
            f"Pushed {result.inserted} new events, {result.skipped} skipped "
            f"({result.scanned_sessions} sessions scanned)"
        )
    sys.exit(0)


@cmd_cc_usage_group.command("status", help="Print cc-usage cursor summary")
def cmd_cc_usage_status():
    from .cc_usage import get_cursor_summary

    summary = get_cursor_summary()
    print(f"Sessions tracked: {summary['sessions_tracked']}")
    print(f"Last push:        {summary['last_push'] or 'never'}")
    sys.exit(0)


@_cli_group.command("copier-update", help="Propagate project-template changes to downstream projects")
@click.option("-d", "--dry-run", is_flag=True, help="Show diffs without applying")
@click.option("-p", "--project", default=None, help="Only update the given project")
@click.option(
    "--no-isolate",
    is_flag=True,
    help="Run copier directly in each repo's main tree (legacy; unsafe while sessions are active)",
)
@click.option(
    "--no-push",
    is_flag=True,
    help="Isolated mode: commit in the temp worktree but do not push HEAD:main",
)
def cmd_copier_update(dry_run, project, no_isolate, no_push):
    from .copier_update import run_copier_update

    sys.exit(
        run_copier_update(
            dry_run=dry_run,
            project_filter=project,
            isolate=not no_isolate,
            push=not no_push,
        )
    )


@_cli_group.command(
    "layout",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True, "help_option_names": []},
    help="Apply a tmux pane layout",
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def cmd_layout(args):
    from .layout import run_layout_command

    sys.exit(run_layout_command(list(args)))


@_cli_group.command("color", help="Reassign iTerm2 color for the current ai session")
@click.argument("color_arg")
def cmd_color(color_arg):
    _do_color(color_arg)


@_cli_group.command(
    "cc-migrate",
    help="Move a Claude Code session transcript from one project root to another (e.g. repo root -> worktree)",
)
@click.argument("dest", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("-t", "--title", default="", help="Select the source session by its customTitle (session name)")
@click.option("-u", "--uuid", "session_id", default="", help="Select the source session by UUID (transcript filename)")
@click.option(
    "-s",
    "--source",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Source project root the session ran in (default: current directory)",
)
@click.option("-k", "--keep-source", is_flag=True, help="Copy instead of move — leave the source transcript in place")
@click.option("-p", "--preserve-cwd", is_flag=True, help="Do not rewrite recorded cwd fields to the destination root")
@click.option("-d", "--dry-run", is_flag=True, help="Show what would happen without writing anything")
@click.option("-f", "--force", is_flag=True, help="Overwrite an existing destination transcript")
def cmd_cc_migrate(dest, title, session_id, source, keep_source, preserve_cwd, dry_run, force):
    """Migrate a CC session so ``ai c <n>`` in DEST resumes it.

    Typical use: a session was launched at the repo root (``claude --name
    myproject-2``) instead of through ``ai c 2``, so its transcript lives in
    the repo root's ~/.claude/projects directory where the worktree launch
    cannot see it. From the repo root, run:

        ai cc-migrate .worktrees/myproject-2 --title myproject-2

    then ``ai c 2`` resumes the migrated conversation.
    """
    from .cc_migrate import migrate_session

    source_root = (source or Path.cwd()).resolve()
    try:
        result = migrate_session(
            source_root,
            dest.resolve(),
            title=title or None,
            session_id=session_id or None,
            keep_source=keep_source,
            preserve_cwd=preserve_cwd,
            dry_run=dry_run,
            force=force,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    verb = "Would migrate" if result.dry_run else ("Copied" if not result.moved else "Migrated")
    print(f"{verb} {result.source_jsonl}")
    print(f"  -> {result.dest_jsonl}")
    print(f"  {result.lines} lines, {result.rewritten} cwd rewrites")
    if result.sidecar_moved is not None:
        print(f"  sidecar dir -> {result.sidecar_moved}")
    for warning in result.warnings:
        print(f"  warning: {warning}", file=sys.stderr)
    sys.exit(0)


def _print_adoption(result, out=None) -> None:
    """Report one adoption outcome (or its dry-run plan) to stdout."""
    stream = out or sys.stdout
    if result.already_adopted:
        print(f"{result.ai_name}: already adopted — {result.resolved} resolves in {result.dest_root}", file=stream)
        return
    verb = "Would adopt" if result.dry_run else "Adopted"
    print(f"{verb} {result.ai_name} -> {result.dest_root}", file=stream)
    if result.retitled_from:
        print(f"  retitled {result.retitled_from!r} -> {result.dest_root.name!r}", file=stream)
    if result.worktree_created:
        print(f"  worktree {'to create' if result.dry_run else 'created'}: {result.dest_root}", file=stream)
    if result.migration is not None:
        print(f"  transcript: {result.migration.source_jsonl}", file=stream)
        print(f"           -> {result.migration.dest_jsonl}", file=stream)
        print(f"  {result.migration.lines} lines, {result.migration.rewritten} cwd rewrites", file=stream)
    elif result.source_jsonl is not None:
        print(f"  transcript: {result.source_jsonl} ({result.source_lines} lines)", file=stream)
    if result.worktree_records_cleared:
        print(
            f"  worktree binding: {result.worktree_records_cleared} stale record(s) cleared "
            f"(Claude Code would otherwise move the transcript back out)",
            file=stream,
        )
    renumbered = [m for m in result.tasks_moved if m.renumbered_from]
    print(f"  tasks: {len(result.tasks_moved)} moved, {len(renumbered)} renumbered", file=stream)
    for move in renumbered:
        print(f"    task {move.renumbered_from} -> {move.dest.stem} ({move.dest.parent})", file=stream)
    print(f"  memory: {len(result.memory_copied)} copied, {len(result.memory_conflicts)} left alone", file=stream)
    for conflict in result.memory_conflicts:
        print(f"    kept existing {conflict}", file=stream)
    if result.resolved is not None:
        print(f"  resolve probe: `ai c` finds {result.resolved}", file=stream)


def _print_collision(exc, out=None) -> None:
    """Print both collision candidates and the retitle remedy, for a human."""
    stream = out or sys.stderr
    print(f"Error: {exc}", file=stream)
    print("", file=stream)
    print("Candidates:", file=stream)
    for candidate in exc.candidates:
        print(f"  {candidate.describe()}", file=stream)
    print("", file=stream)
    if exc.prefix and exc.free_index:
        proposed = f"{exc.prefix}-{exc.free_index}"
        print(
            f"Proposed remedy — retitle the session you are adopting to {proposed!r} "
            f"(lowest index claimed by neither a worktree nor a transcript title) and adopt it there:",
            file=stream,
        )
        print(f"  ai session-adopt {exc.title} -c retitle -T {proposed}", file=stream)
        print(f"  ai session-adopt {exc.title} -c retitle -I {exc.free_index}", file=stream)
    print("", file=stream)
    print(
        "This gate is unconditional: -y/--yes does not cover it, and there is no automatic mode. "
        "Confirm the new index yourself before anything is written.",
        file=stream,
    )


@_cli_group.command(
    "session-adopt",
    help="Adopt a Claude Code session started outside `ai c` so `ai c <n>` resumes it",
)
@click.argument("name", required=False, default="")
@click.option(
    "-s",
    "--source",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Project root the session ran in (default: the repo root)",
)
@click.option(
    "-r",
    "--repo",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Repo root that owns .worktrees/ (default: detected from the current directory)",
)
@click.option(
    "-c",
    "--on-collision",
    type=click.Choice(["gate", "retitle"]),
    default="gate",
    help="Duplicate-title handling: gate (stop for a human) or retitle (apply a human-supplied title)",
)
@click.option("-T", "--new-title", default="", help="With -c retitle: the confirmed new title")
@click.option("-I", "--new-index", type=int, default=None, help="With -c retitle: the confirmed new index")
@click.option("-N", "--task-namespace", default="", help="Source CC task namespace (default: derived from the UUID)")
@click.option("-a", "--all", "adopt_every", is_flag=True, help="Bulk mode: adopt every titled session in the source")
@click.option("-n", "--dry-run", is_flag=True, help="Show what would happen without writing anything")
@click.option("-y", "--yes", is_flag=True, help="Skip the confirmation prompt (never covers a title collision)")
def cmd_session_adopt(
    name, source, repo, on_collision, new_title, new_index, task_namespace, adopt_every, dry_run, yes
):
    """Adopt a session's transcript, tasks, memory and worktree in one pass.

    A session started as a plain ``claude`` in a repo root is invisible to
    ``ai c <n>``, which scans the *worktree's* project directory by title. This
    command moves everything that is keyed by the project slug into the pinned
    slot and then verifies that ``ai c`` really does resolve the result.
    """
    from .session_adopt import AdoptionError, TitleCollision, adopt_all, adopt_session, split_ai_name

    repo_root = (repo or _session.detect_repo_root() or Path.cwd()).resolve()

    if adopt_every:
        outcomes = adopt_all(repo_root, source_root=source, dry_run=dry_run)
        if not outcomes:
            print("No titled sessions found — nothing to adopt.")
            sys.exit(0)
        failures = 0
        for title, outcome in outcomes:
            if isinstance(outcome, TitleCollision):
                failures += 1
                _print_collision(outcome)
                print(f"Paused on {title} — continuing with the rest.", file=sys.stderr)
            elif isinstance(outcome, AdoptionError):
                failures += 1
                print(f"Skipped {title}: {outcome}", file=sys.stderr)
            else:
                _print_adoption(outcome)
        print(f"\n{len(outcomes) - failures} adopted, {failures} skipped.")
        sys.exit(1 if failures else 0)

    if not name:
        print("Error: NAME is required (or use -a/--all)", file=sys.stderr)
        sys.exit(1)

    if new_index is not None and not new_title:
        split = split_ai_name(name)
        if not split:
            print(f"Error: cannot derive a prefix from {name!r} — pass -T/--new-title instead", file=sys.stderr)
            sys.exit(1)
        new_title = f"{split[0]}-{new_index}"

    if on_collision == "retitle" and not new_title:
        print(
            "Error: -c/--on-collision retitle needs the human-confirmed title (-T/--new-title) "
            "or index (-I/--new-index). Run without -c first to see the candidates and the "
            "proposed free index.",
            file=sys.stderr,
        )
        sys.exit(1)

    def _run(preview: bool):
        return adopt_session(
            repo_root,
            name,
            source_root=source,
            task_namespace=task_namespace or None,
            new_title=new_title or None,
            on_collision=on_collision,
            dry_run=preview,
        )

    # Every refusal — collision, live session, insufficient space — is raised by
    # the dry run too, so preflighting here means the human is never prompted to
    # confirm an adoption that was going to be rejected anyway, and a collision
    # is reported before a `-y`-less prompt can obscure it.
    try:
        result = _run(True)
        if not dry_run:
            if not yes and not result.already_adopted:
                _print_adoption(result)
                if not click.confirm(f"Adopt {name} into {result.dest_root}?", default=False):
                    print("Aborted.", file=sys.stderr)
                    sys.exit(1)
            result = _run(False)
    except TitleCollision as exc:
        _print_collision(exc)
        sys.exit(1)
    except (AdoptionError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    _print_adoption(result)
    for warning in result.warnings:
        print(f"  warning: {warning}", file=sys.stderr)
    sys.exit(1 if any("FAILED" in w for w in result.warnings) else 0)


def _print_audit(report, ready, skipped, out=None) -> None:
    """Print the survey: collisions first, then every session, then the triage."""
    stream = out or sys.stdout
    print(
        f"Scanned {report.scanned_transcripts} transcripts in {report.scanned_project_dirs} project "
        f"directories — {len(report.sessions)} titled session(s) across {len(report.repos)} repo(s).",
        file=stream,
    )

    if report.collisions:
        print(f"\nTitle collisions ({len(report.collisions)}) — a human must choose:", file=stream)
        for title, group in report.collisions.items():
            print(f"  {title!r} claimed by {len(group)} transcripts:", file=stream)
            for record in group:
                print(
                    f"    {record.transcript} ({record.lines} lines, cwd={record.cwd or '<unrecorded>'})", file=stream
                )

    if report.sessions:
        print("\nSessions:", file=stream)
        for record in report.sessions:
            print(f"  {record.describe()}", file=stream)

    if ready:
        print(f"\nAdoptable ({len(ready)}):", file=stream)
        for record in ready:
            print(f"  {record.title} -> {record.slot}", file=stream)
    if skipped:
        print(f"\nSkipped ({len(skipped)}):", file=stream)
        for record, reason in skipped:
            print(f"  {record.title}: {reason}", file=stream)


@_cli_group.command(
    "session-audit",
    help="Survey titled Claude Code sessions fleet-wide and report which `ai c <n>` cannot resume",
)
@click.option(
    "-r",
    "--repo",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Report only sessions owned by this repo root (default: every repo found)",
)
@click.option("-t", "--title", default="", help="Report only the session with this exact title")
@click.option("-a", "--adopt", "run_adopt", is_flag=True, help="Adopt every session that is safe to adopt")
@click.option("-n", "--dry-run", is_flag=True, help="With -a: show what would be adopted without writing anything")
@click.option("-y", "--yes", is_flag=True, help="Skip the confirmation prompt (never covers a title collision)")
def cmd_session_audit(repo, title, run_adopt, dry_run, yes):
    """Survey every titled CC session, then optionally drive their adoption.

    The survey scans outward from ``~/.claude/projects/`` rather than inward from
    a list of repos, so a session that ran in an agent worktree under
    ``<repo>/.claude/worktrees/<id>`` is found and attributed without anyone
    supplying its path. Adoption itself is delegated to ``ai session-adopt``'s
    module, so the duplicate-title and live-session gates apply unchanged.
    """
    from .session_adopt import AdoptionError, TitleCollision
    from .session_audit import adopt_ready, survey, triage

    report = survey(repo=repo, title=title or None)
    ready, skipped = triage(report)

    if not report.sessions:
        scope = " matching the filters" if (repo or title) else ""
        print(f"No titled sessions found{scope} — nothing to audit.")
        sys.exit(0)

    if not run_adopt:
        _print_audit(report, ready, skipped)
        sys.exit(0)

    _print_audit(report, ready, skipped)
    if not ready:
        print("\nNothing safe to adopt.")
        sys.exit(1 if skipped else 0)

    if not dry_run and not yes and not click.confirm(f"\nAdopt {len(ready)} session(s)?", default=False):
        print("Aborted.", file=sys.stderr)
        sys.exit(1)

    outcomes, _ = adopt_ready(report, dry_run=dry_run)
    print("")
    failures = 0
    for record, outcome in outcomes:
        if isinstance(outcome, TitleCollision):
            failures += 1
            _print_collision(outcome)
            print(f"Paused on {record.title} — continuing with the rest.", file=sys.stderr)
        elif isinstance(outcome, AdoptionError):
            failures += 1
            print(f"Skipped {record.title}: {outcome}", file=sys.stderr)
        else:
            _print_adoption(outcome)
    print(f"\n{len(outcomes) - failures} adopted, {failures + len(skipped)} skipped.")
    sys.exit(1 if (failures or skipped) else 0)


# --- tunnel group ---


@_cli_group.group("tunnel", help="autossh SSH tunnels to the remote server")
def cmd_tunnel_group():
    pass


@cmd_tunnel_group.command("start", help="Start an SSH tunnel")
@click.argument("local_port", type=int)
@click.argument("remote_port", type=int, required=False, default=None)
@click.option("-L", "--forward", is_flag=True, help="Create a forward tunnel (default: reverse)")
def cmd_tunnel_start(local_port, remote_port, forward):
    config = _config.load_config()
    if remote_port is None:
        remote_port = local_port
    _tunnel._cmd_tunnel_start(local_port, remote_port, forward=forward, config=config)
    sys.exit(0)


@cmd_tunnel_group.command("stop", help="Stop an SSH tunnel")
@click.argument("port", type=int)
def cmd_tunnel_stop(port):
    _tunnel._cmd_tunnel_stop(port)
    sys.exit(0)


@cmd_tunnel_group.command("status", help="Show running SSH tunnels")
def cmd_tunnel_status():
    _tunnel._cmd_tunnel_status()
    sys.exit(0)


# --- signal-watch group ---


@_cli_group.group("signal-watch", help="Handoff signal-watch Circus daemon management")
def cmd_signal_watch_group():
    pass


@cmd_signal_watch_group.command("start", help="Start signal-watch for a session")
@click.argument("project")
@click.argument("session")
def cmd_signal_watch_start(project, session):
    _process_manager._cmd_signal_watch_start(project, session)
    sys.exit(0)


@cmd_signal_watch_group.command("stop", help="Stop signal-watch for a session")
@click.argument("session")
def cmd_signal_watch_stop(session):
    _process_manager._cmd_signal_watch_stop(session)
    sys.exit(0)


@cmd_signal_watch_group.command("status", help="Show signal-watch status")
def cmd_signal_watch_status():
    _process_manager._cmd_signal_watch_status()
    sys.exit(0)


# --- cdp group ---


@_cli_group.group("cdp", help="Chrome DevTools Protocol browser management")
def cmd_cdp_group():
    pass


@cmd_cdp_group.command("start", help="Launch Chrome with a CDP port")
@click.option("-p", "--port", type=int, default=None, help="CDP port (default: [cdp].port in config)")
@click.option("-I", "--no-incognito", is_flag=True, help="Use a normal (persistent) profile")
@click.option("-t", "--tunnel", is_flag=True, help="Start an SSH tunnel alongside Chrome")
@click.option("-L", "--forward", is_flag=True, help="Use forward tunnel (default: reverse)")
def cmd_cdp_start(port, no_incognito, tunnel, forward):
    config = _config.load_config()
    _default_port = config.get("cdp", {}).get("port", 9222)
    if port is None:
        port = _default_port
    _tunnel._cmd_cdp_start(port, not no_incognito, config, tunnel=tunnel, forward=forward)
    sys.exit(0)


@cmd_cdp_group.command("stop", help="Stop a CDP-launched Chrome instance")
@click.option("-p", "--port", type=int, default=None, help="CDP port")
@click.option("-t", "--tunnel", is_flag=True, help="Also stop the SSH tunnel for this port")
def cmd_cdp_stop(port, tunnel):
    config = _config.load_config()
    _default_port = config.get("cdp", {}).get("port", 9222)
    if port is None:
        port = _default_port
    _tunnel._cmd_cdp_stop(port, tunnel=tunnel)
    sys.exit(0)


@cmd_cdp_group.command("status", help="Show CDP Chrome status")
def cmd_cdp_status():
    _tunnel._cmd_cdp_status()
    sys.exit(0)


# --- vpn-watch / ps / sync / reconnect / update / deploy / attach / ls ---


@_cli_group.command("vpn-watch", help="Run the VPN-watch daemon")
def cmd_vpn_watch():
    from .vpn_watch import run_vpn_watch

    run_vpn_watch(_config.load_config())
    sys.exit(0)


@_cli_group.command(
    "ps",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True, "help_option_names": []},
    help="Process hygiene — show/kill stale ai-cli processes",
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def cmd_ps(args):
    from .process_hygiene import cmd_ps as _cmd_ps

    sys.exit(_cmd_ps(list(args), _config.load_config()))


@_cli_group.command(
    "sync",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True, "help_option_names": []},
    help="Sync CC session data (JSONL + memory files) across machines",
)
@click.argument("action", required=False, default="")
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def cmd_sync(action, args):
    if not action:
        print(
            "Usage: ai sync [push|pull|conflicts|resolve|watch|cleanup|repair-worktree] "
            "[-m|--memories-only] [-n|--dry-run] [-v|--verbose] [-f|--force]"
        )
        sys.exit(1)
    from .sync import (
        _cc_projects_dir,
        clean_worktree_cc_dirs,
        get_local_prefix,
        repair_worktree_cc_dir,
        sync_conflicts,
        sync_pull,
        sync_push,
        sync_resolve,
        sync_watch,
    )

    flags = list(args)
    if action == "push":
        sys.exit(sync_push(flags))
    elif action == "pull":
        sys.exit(sync_pull(flags))
    elif action == "conflicts":
        sys.exit(sync_conflicts(flags))
    elif action == "resolve":
        sys.exit(sync_resolve(flags))
    elif action == "watch":
        sys.exit(sync_watch(flags))
    elif action == "repair-worktree":
        positional = [a for a in flags if not a.startswith("-")]
        if len(positional) < 2:
            print(
                "Usage: ai sync repair-worktree <project> <worktree> [-n|--dry-run] [-v|--verbose]\n"
                "Example: ai sync repair-worktree myproject wt-1\n"
                "Copies all conversations from the main project CC dir into the worktree CC dir\n"
                "so they are accessible from the worktree session.",
                file=sys.stderr,
            )
            sys.exit(1)
        project_name = positional[0]
        wt_name = positional[1]
        dry_run = "-n" in flags or "--dry-run" in flags
        verbose = "-v" in flags or "--verbose" in flags
        copied = repair_worktree_cc_dir(
            project_name=project_name,
            wt_name=wt_name,
            cc_projects_dir=_cc_projects_dir(),
            local_prefix=get_local_prefix(),
            dry_run=dry_run,
            verbose=verbose,
        )
        sys.exit(0 if copied >= 0 else 1)
    elif action == "cleanup":
        dry_run = "-n" in flags or "--dry-run" in flags
        verbose = "-v" in flags or "--verbose" in flags
        removed_jsonl, removed_lock = clean_worktree_cc_dirs(
            _cc_projects_dir(),
            get_local_prefix(),
            dry_run=dry_run,
            verbose=verbose,
        )
        verb = "Would remove" if dry_run else "Removed"
        print(f"{verb}: {removed_jsonl} stale JSONL copies, {removed_lock} orphan lock dirs")
        sys.exit(0)
    else:
        print(
            f"Unknown sync action: {action}. Use push, pull, conflicts, watch, cleanup, or repair-worktree.",
            file=sys.stderr,
        )
        sys.exit(1)


@_cli_group.group("ws", help="Workspace-wide operations across all repos")
def cmd_ws_group():
    pass


@cmd_ws_group.command("pull", help="Pull/rebase all repos and worktrees in a VS Code workspace file")
@click.option("--workspace", "-w", "workspace_path", default=None, help="Path to .code-workspace file")
@click.option("--remote", "-r", "use_remote", is_flag=True, default=False, help="Use remote workspace file")
@click.option("--dry-run", "-d", is_flag=True, default=False, help="Print plan without touching git")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show full git output per repo/worktree")
def cmd_ws_pull(workspace_path, use_remote, dry_run, verbose):
    from pathlib import Path

    from .workspace import ws_pull

    cfg = _config.load_config().get("workspace", {})

    if workspace_path:
        ws_path = Path(workspace_path).expanduser().resolve()
    elif use_remote:
        remote = cfg.get("remote_path", "")
        if not remote:
            print(
                "Error: [workspace] remote_path not configured in config.toml. Set it or use --workspace PATH.",
                file=sys.stderr,
            )
            sys.exit(1)
        ws_path = Path(remote).expanduser().resolve()
    else:
        local = cfg.get("local_path", "")
        if not local:
            print(
                "Error: [workspace] local_path not configured in config.toml. Set it or use --workspace PATH.",
                file=sys.stderr,
            )
            sys.exit(1)
        ws_path = Path(local).expanduser().resolve()

    sys.exit(ws_pull(ws_path, dry_run=dry_run, verbose=verbose))


@_cli_group.command("reconnect", help="Print reconnect commands for remote tmux sessions")
@click.argument("sessions", nargs=-1)
def cmd_reconnect(sessions):
    config = _config.load_config()
    requested = [int(x) for x in sessions if x.isdigit()] if sessions else None
    _do_reconnect(requested, config)


@_cli_group.command("update", help="Update ai-cli-utils from the source tree (git pull + uv tool install)")
@click.option("-f", "--force", is_flag=True, help="Pass --reinstall to uv tool install (bypass uv's cache and deps)")
@click.option("-q", "--quiet", is_flag=True, help="Capture git/uv output; report one line naming the new version")
@click.option("-v", "--verbose", is_flag=True, help="Show the full git/uv transcript even when --quiet is passed")
def cmd_update(force, quiet, verbose):
    config = _config.load_config()
    _do_update_or_deploy(force_reinstall=force, config=config, quiet=quiet and not verbose)


@_cli_group.command("deploy", help="Alias for update (historical)")
@click.option("-f", "--force", is_flag=True, help="Pass --reinstall to uv tool install (bypass uv's cache and deps)")
@click.option("-q", "--quiet", is_flag=True, help="Capture git/uv output; report one line naming the new version")
@click.option("-v", "--verbose", is_flag=True, help="Show the full git/uv transcript even when --quiet is passed")
def cmd_deploy(force, quiet, verbose):
    config = _config.load_config()
    _do_update_or_deploy(force_reinstall=force, config=config, quiet=quiet and not verbose)


@_cli_group.command(
    "trust-backfill",
    help="Register Claude Code workspace trust for every repo under a root "
    "(fixes 'workspace has not been trusted' permission drops, GH #72896)",
)
@click.option("--root", "-r", default="~/projects", help="Root to scan for git repos (default: ~/projects)")
def cmd_trust_backfill(root):
    from .trust import backfill_projects_trust

    added = backfill_projects_trust(root)
    if added:
        print(f"Registered workspace trust for {len(added)} workspace(s) in ~/.claude.json:")
        for key in added:
            print(f"  + {key}")
    else:
        print("All workspaces under the root are already trusted — nothing to change.")


@_cli_group.command("attach", help="Attach to a tmux session by name")
@click.argument("session_name")
def cmd_attach(session_name):
    _do_attach(session_name)


@_cli_group.command("ls", help="List tmux sessions (fzf picker when available)")
@click.option("-a", "--all", "show_all", is_flag=True, help="Show all tmux sessions (not just ai-cli ones)")
def cmd_ls(show_all):
    _do_ls(show_all)


# --- Public entry points ---


def cli() -> None:
    """Top-level CLI entry point.

    Handles the ``ai internal`` machine-to-machine fast path directly (sub-
    millisecond startup for bash-hook callers) and delegates everything else
    to the Click command group. Click usage errors exit with code 1 so the
    exit contract matches the argparse/sys.argv dispatcher it replaces.
    """
    if len(sys.argv) > 1 and sys.argv[1] == "internal":
        _handle_internal(sys.argv[2:])
        return
    try:
        _cli_group(standalone_mode=False)
    except click.exceptions.UsageError as exc:
        exc.show(file=sys.stderr)
        sys.exit(1)
    except click.exceptions.ClickException as exc:
        exc.show()
        sys.exit(exc.exit_code)
    except click.exceptions.Abort:
        sys.exit(1)
    except KeyboardInterrupt:
        # standalone_mode=False means Click does not convert a raw Ctrl-C
        # (e.g. from an interactive input() prompt like registry sync) into
        # click.exceptions.Abort itself — without this handler it propagates
        # as an unhandled KeyboardInterrupt and prints a Python traceback.
        sys.exit(1)
    sys.exit(0)


def main() -> None:
    """uv-tool console-script entry. Alias for :func:`cli`."""
    cli()


if __name__ == "__main__":  # pragma: no cover
    main()
