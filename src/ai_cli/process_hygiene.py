"""
Process hygiene: detect and clean up orphaned ai-cli-managed processes.

Tracked processes:
- mosh-server:  High orphan risk — client disconnect leaves server running
- signal-watch: Medium risk — tied to a tmux session that may be gone
- ai memory watch / ai sync watch: Medium risk — launched per tmux session
- autossh, circusd, nats-server: Low risk (intentionally persistent, report only)

Usage:
    ai ps                    # list all managed processes
    ai ps --refresh          # force SSH re-check of remote host
    ai ps clean              # prompt before killing orphaned processes
    ai ps clean --force      # kill orphaned + suspect without prompting
    ai ps cron               # silent auto-clean (for cron or session-start hook)
"""

from __future__ import annotations

import json
import os
import re
import signal as _signal
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

ORPHAN_THRESHOLD = 80
SUSPECT_THRESHOLD = 40
CACHE_TTL_SECONDS = 1800  # 30 minutes


def _get_state_dir() -> Path:
    """Return XDG state directory for ai-cli-utils."""
    base = os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
    return Path(base) / "ai-cli-utils"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ProcessInfo:
    pid: int
    name: str
    age_seconds: float
    score: int
    verdict: str  # "active", "suspect", "orphaned"
    detail: str
    machine: str  # "local" or "remote"
    args: str = ""


def _clean_stale_transport_files() -> None:
    """Remove transport-*.json state files whose parent process is no longer alive."""
    state_dir = _get_state_dir()
    for f in state_dir.glob("transport-*.json"):
        try:
            data = json.loads(f.read_text())
            parent_pid = data.get("parent_pid")
            if parent_pid:
                os.kill(parent_pid, 0)  # 0 = check existence only
        except (ProcessLookupError, PermissionError):
            f.unlink(missing_ok=True)
        except Exception:
            pass


def _verdict_for(score: int) -> str:
    if score >= ORPHAN_THRESHOLD:
        return "orphaned"
    elif score >= SUSPECT_THRESHOLD:
        return "suspect"
    return "active"


# ---------------------------------------------------------------------------
# Subprocess helpers (injectable for testing)
# ---------------------------------------------------------------------------


def _run_ps(
    ps_fn: Callable[[], tuple[str, int]] | None = None,
) -> list[dict]:
    """
    Return parsed rows from ``ps`` for all processes.

    Each row is a dict with keys: pid (int), name (str), etime (str), args (str).
    *ps_fn* is injectable: called with no arguments, returns (stdout, returncode).
    """
    if ps_fn is not None:
        stdout, rc = ps_fn()
    else:
        result = subprocess.run(
            ["ps", "-ax", "-o", "pid=,comm=,etime=,args="],
            capture_output=True,
            text=True,
        )
        stdout, rc = result.stdout, result.returncode
    if rc != 0:
        return []
    rows: list[dict] = []
    for line in stdout.strip().splitlines():
        parts = line.split(None, 3)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        comm = parts[1]
        etime = parts[2]
        args = parts[3] if len(parts) > 3 else comm
        rows.append({"pid": pid, "name": comm, "etime": etime, "args": args})
    return rows


def _run_lsof_udp(
    lsof_fn: Callable[[], tuple[str, int]] | None = None,
) -> set[str]:
    """
    Return the set of UDP port numbers that have active mosh-client connections.
    *lsof_fn* is injectable for testing.
    """
    if lsof_fn is not None:
        stdout, rc = lsof_fn()
    else:
        result = subprocess.run(
            ["lsof", "-i", "UDP", "-n", "-P"],
            capture_output=True,
            text=True,
        )
        stdout, rc = result.stdout, result.returncode
    if rc != 0:
        return set()
    ports: set[str] = set()
    for line in stdout.splitlines():
        if "mosh-client" in line or "mosh-cli" in line:
            m = re.search(r":(\d+)\s*$", line)
            if m:
                ports.add(m.group(1))
    return ports


def _list_tmux_sessions(
    tmux_fn: Callable[[], tuple[str, int]] | None = None,
) -> dict[str, bool]:
    """
    Return ``{session_name: is_attached}`` for all running tmux sessions.
    *tmux_fn* is injectable for testing.
    """
    if tmux_fn is not None:
        stdout, rc = tmux_fn()
    else:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}:#{session_attached}"],
            capture_output=True,
            text=True,
        )
        stdout, rc = result.stdout, result.returncode
    if rc != 0:
        return {}
    sessions: dict[str, bool] = {}
    for line in stdout.strip().splitlines():
        if ":" in line:
            name, attached = line.rsplit(":", 1)
            sessions[name] = attached.strip() == "1"
    return sessions


# ---------------------------------------------------------------------------
# Elapsed time parsing
# ---------------------------------------------------------------------------


def _parse_etime(etime: str) -> float:
    """
    Parse ``ps`` *etime* format to seconds.

    Accepted formats: ``MM:SS``, ``HH:MM:SS``, ``DD-HH:MM:SS``.
    Returns 0.0 on parse failure.
    """
    etime = etime.strip()
    days = 0
    if "-" in etime:
        d_str, etime = etime.split("-", 1)
        try:
            days = int(d_str)
        except ValueError:
            return 0.0
    parts = etime.split(":")
    try:
        if len(parts) == 3:
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        elif len(parts) == 2:
            h, m, s = 0, int(parts[0]), int(parts[1])
        else:
            return 0.0
    except ValueError:
        return 0.0
    return days * 86400 + h * 3600 + m * 60 + float(s)


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------


def score_mosh_server(
    pid: int,
    age_seconds: float,
    args: str,
    udp_ports: set[str],
    tmux_sessions: dict[str, bool],
    lsof_available: bool = True,
) -> tuple[int, str]:
    """
    Score a mosh-server process.

    Returns *(score, detail)*.  Score thresholds: ≥80 orphaned, 40–79 suspect, <40 active.
    """
    score = 0
    reasons: list[str] = []

    # Extract UDP port from mosh-server args (last numeric token in typical invocation)
    mosh_port: str | None = None
    m = re.search(r"\b(6\d{4})\b", args)  # mosh uses 60000-61000 range typically
    if not m:
        m = re.search(r"\b(\d{5,6})\b", args)
    if m:
        mosh_port = m.group(1)

    if lsof_available:
        if mosh_port not in udp_ports:
            score += 50
            reasons.append("no active client")
    else:
        if age_seconds > 48 * 3600:
            score += 60
            reasons.append("lsof unavailable, age > 48h")

    if age_seconds > 24 * 3600:
        score += 20
        reasons.append("age > 24h")
    elif age_seconds > 6 * 3600:
        score += 10
        reasons.append("age > 6h")

    # Check for related tmux session
    matched_session: str | None = None
    for sess in tmux_sessions:
        if sess in args:
            matched_session = sess
            break

    if matched_session is not None:
        if tmux_sessions[matched_session]:
            score -= 10
            reasons.append("tmux attached")
        elif age_seconds > 2 * 3600:
            score += 5
            reasons.append("tmux unattached > 2h")
    else:
        score += 10
        reasons.append("no matching tmux session")

    detail = ", ".join(reasons) if reasons else "active"
    return score, detail


def score_signal_watch(
    pid: int,
    age_seconds: float,
    args: str,
    tmux_sessions: dict[str, bool],
) -> tuple[int, str]:
    """
    Score a signal-watch process.

    Returns *(score, detail)*.
    """
    score = 0
    reasons: list[str] = []

    # Extract watched session ID from args: "ai internal signal-watch <project> <session>"
    watched_session: str | None = None
    parts = args.split()
    for i, p in enumerate(parts):
        if p == "signal-watch" and i + 2 < len(parts):
            watched_session = parts[i + 2]  # session is the 2nd arg after signal-watch
            break

    if watched_session is not None and watched_session not in tmux_sessions:
        score += 30
        reasons.append(f"tmux session {watched_session!r} gone")

    if age_seconds > 12 * 3600:
        score += 20
        reasons.append("age > 12h")

    detail = ", ".join(reasons) if reasons else "active"
    return score, detail


def score_watcher(
    pid: int,
    age_seconds: float,
    args: str,
    tmux_sessions: dict[str, bool],
) -> tuple[int, str]:
    """
    Score an ``ai memory watch`` or ``ai sync watch`` process.

    Returns *(score, detail)*.
    """
    score = 0
    reasons: list[str] = []

    if not tmux_sessions:
        score += 60
        reasons.append("no tmux sessions running")

    if age_seconds > 24 * 3600:
        score += 20
        reasons.append("age > 24h")

    detail = ", ".join(reasons) if reasons else "active"
    return score, detail


# ---------------------------------------------------------------------------
# Process collection
# ---------------------------------------------------------------------------


def collect_local_processes(
    ps_fn: Callable[[], tuple[str, int]] | None = None,
    lsof_fn: Callable[[], tuple[str, int]] | None = None,
    tmux_fn: Callable[[], tuple[str, int]] | None = None,
) -> list[ProcessInfo]:
    """
    Collect and score all ai-managed processes on the local machine.

    All subprocess calls are injectable via *ps_fn*, *lsof_fn*, *tmux_fn* for
    unit-test isolation.
    """
    rows = _run_ps(ps_fn)
    tmux_sessions = _list_tmux_sessions(tmux_fn)

    try:
        udp_ports = _run_lsof_udp(lsof_fn)
        lsof_available = True
    except FileNotFoundError:
        udp_ports = set()
        lsof_available = False

    results: list[ProcessInfo] = []
    for row in rows:
        pid: int = row["pid"]
        name: str = row["name"]
        etime: str = row["etime"]
        args: str = row["args"]
        age = _parse_etime(etime)

        score: int
        detail: str

        if "mosh-server" in name or "mosh-server" in args:
            score, detail = score_mosh_server(pid, age, args, udp_ports, tmux_sessions, lsof_available)
        elif "signal-watch" in args and "ai" in args:
            score, detail = score_signal_watch(pid, age, args, tmux_sessions)
        elif ("memory" in args and "watch" in args and "ai" in args) or (
            "sync" in args and "watch" in args and "ai" in args
        ):
            score, detail = score_watcher(pid, age, args, tmux_sessions)
        elif name in ("autossh", "circusd", "nats-server"):
            score, detail = 0, "persistent"
        else:
            continue

        results.append(
            ProcessInfo(
                pid=pid,
                name=name,
                age_seconds=age,
                score=score,
                verdict=_verdict_for(score),
                detail=detail,
                machine="local",
                args=args,
            )
        )

    return results


# ---------------------------------------------------------------------------
# Remote process cache
# ---------------------------------------------------------------------------


def _cache_path() -> Path:
    return _get_state_dir() / "remote-ps-cache.json"


def _load_cache(path: Path) -> tuple[list[ProcessInfo], float] | None:
    """
    Load cached remote processes.

    Returns *(processes, cache_age_seconds)* or ``None`` if the cache is
    missing or unreadable.
    """
    try:
        data = json.loads(path.read_text())
        ts = float(data.get("ts", 0))
        procs = [ProcessInfo(**p) for p in data.get("processes", [])]
        age = time.time() - ts
        return procs, age
    except (OSError, json.JSONDecodeError, TypeError, KeyError):
        return None


def _save_cache(path: Path, processes: list[ProcessInfo]) -> None:
    """Persist remote processes to cache."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {
            "ts": time.time(),
            "processes": [asdict(p) for p in processes],
        }
        path.write_text(json.dumps(data))
    except OSError:
        pass


def collect_remote_processes(
    host: str,
    user: str,
    cache_ttl: int = CACHE_TTL_SECONDS,
    use_cache: bool = True,
    force_refresh: bool = False,
    ssh_fn: Callable[[str, str], tuple[str, int]] | None = None,
) -> tuple[list[ProcessInfo], float | None]:
    """
    Collect remote processes via SSH, using cache if fresh.

    Returns *(processes, cache_age_seconds)*. *cache_age* is ``None`` when
    results were freshly fetched from the remote host.

    *ssh_fn* is injectable for testing: called as ``ssh_fn(user, host)``,
    returns *(stdout, returncode)*.
    """
    path = _cache_path()

    if use_cache and not force_refresh:
        cached = _load_cache(path)
        if cached is not None:
            procs, age = cached
            if age < cache_ttl:
                return procs, age

    # Fetch from remote
    if ssh_fn is not None:
        stdout, rc = ssh_fn(user, host)
    else:
        try:
            result = subprocess.run(
                [
                    "ssh",
                    f"{user}@{host}",
                    "ps -ax -o pid=,comm=,etime=,args= 2>/dev/null",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            stdout, rc = result.stdout, result.returncode
        except (subprocess.TimeoutExpired, OSError):
            cached = _load_cache(path)
            return (cached[0], cached[1]) if cached else ([], None)

    if rc != 0:
        cached = _load_cache(path)
        return (cached[0], cached[1]) if cached else ([], None)

    procs = _parse_remote_ps(stdout)
    _save_cache(path, procs)
    return procs, None


def _parse_remote_ps(stdout: str) -> list[ProcessInfo]:
    """Parse remote ``ps`` output and return scored ``ProcessInfo`` list."""
    rows: list[dict] = []
    for line in stdout.strip().splitlines():
        parts = line.split(None, 3)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        comm = parts[1]
        etime = parts[2]
        args = parts[3] if len(parts) > 3 else comm
        rows.append({"pid": pid, "name": comm, "etime": etime, "args": args})

    procs: list[ProcessInfo] = []
    for row in rows:
        name = row["name"]
        args = row["args"]
        age = _parse_etime(row["etime"])

        if "mosh-server" in name or "mosh-server" in args:
            # Remote: no lsof — use age-based fallback
            score, detail = score_mosh_server(row["pid"], age, args, set(), {}, lsof_available=False)
        elif name in ("nats-server",):
            score, detail = 0, "persistent"
        elif "signal-watch" in args and "ai" in args:
            score, detail = score_signal_watch(row["pid"], age, args, {})
        else:
            continue

        procs.append(
            ProcessInfo(
                pid=row["pid"],
                name=name,
                age_seconds=age,
                score=score,
                verdict=_verdict_for(score),
                detail=detail,
                machine="remote",
                args=args,
            )
        )
    return procs


# ---------------------------------------------------------------------------
# Auto-clean
# ---------------------------------------------------------------------------


def auto_clean_orphans(
    processes: list[ProcessInfo],
    log_path: Path,
    dry_run: bool = False,
) -> list[ProcessInfo]:
    """
    Send SIGTERM to all local processes with verdict ``"orphaned"`` (score ≥ 80).

    Processes on remote machines are skipped — they must be cleaned separately
    via ``ai ps clean`` after SSH access is established.

    Returns the list of processes that were killed (or would be killed in dry
    run mode).  Already-dead processes are included: they were stale anyway.
    """
    killed: list[ProcessInfo] = []
    for proc in processes:
        if proc.verdict != "orphaned":
            continue
        if proc.machine != "local":
            continue
        if dry_run:
            killed.append(proc)
            continue
        try:
            os.kill(proc.pid, _signal.SIGTERM)
            killed.append(proc)
        except ProcessLookupError:
            killed.append(proc)  # already dead — count as cleaned
        except PermissionError:
            pass

    if killed and not dry_run:
        try:
            import datetime

            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a") as f:
                ts = datetime.datetime.now().isoformat(timespec="seconds")
                for proc in killed:
                    f.write(f"{ts} killed {proc.name} pid={proc.pid} score={proc.score} ({proc.detail})\n")
        except OSError:
            pass

    return killed


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _fmt_age(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    elif seconds < 86400:
        return f"{int(seconds // 3600)}h"
    else:
        return f"{int(seconds // 86400)}d"


def format_ps_table(
    local: list[ProcessInfo],
    remote: list[ProcessInfo],
    cache_age: float | None = None,
    remote_label: str = "REMOTE",
) -> str:
    """
    Format process lists as a human-readable table.

    *cache_age* is shown in the remote section header if not ``None``.
    """
    lines: list[str] = []

    if local:
        lines.append("LOCAL")
        for p in local:
            icon = "⚠" if p.verdict == "orphaned" else ("~" if p.verdict == "suspect" else "✓")
            lines.append(
                f"  {p.name:<14} pid={p.pid:<8} age={_fmt_age(p.age_seconds):<5} "
                f"score={p.score:<4} {icon} {p.verdict} ({p.detail})"
            )

    if remote:
        if cache_age is not None:
            header = f"\n{remote_label} (cached {_fmt_age(cache_age)} ago — run `ai ps --refresh` to update)"
        else:
            header = f"\n{remote_label}"
        lines.append(header)
        for p in remote:
            icon = "⚠" if p.verdict == "orphaned" else ("~" if p.verdict == "suspect" else "✓")
            lines.append(
                f"  {p.name:<14} pid={p.pid:<8} age={_fmt_age(p.age_seconds):<5} "
                f"score={p.score:<4} {icon} {p.verdict} ({p.detail})"
            )

    if not local and not remote:
        lines.append("No managed processes found.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def cmd_ps(
    args: list[str],
    config: dict,
    stdout_fn: Callable[[str], None] | None = None,
) -> int:
    """
    Implement ``ai ps`` subcommand.

    *args* is the remainder of ``sys.argv`` after ``"ps"``.
    *config* is the loaded config dict.
    *stdout_fn* is injectable for testing (defaults to print).
    Returns an exit code (0 or 1).
    """
    out = stdout_fn or print

    remote_cfg = config.get("remote", {})
    remote_host = remote_cfg.get("host", "")
    remote_user = remote_cfg.get("user", "")
    hygiene_cfg = config.get("process_hygiene", {})
    cache_ttl = int(hygiene_cfg.get("cache_ttl_minutes", 30)) * 60
    log_path = _get_state_dir() / "process-hygiene.log"

    # Determine sub-action
    action = ""
    force = False
    refresh = False
    cron_mode = False

    remaining = list(args)
    if remaining and remaining[0] == "clean":
        action = "clean"
        remaining = remaining[1:]
    elif remaining and remaining[0] == "cron":
        action = "cron"
        cron_mode = True
        remaining = remaining[1:]

    for flag in remaining:
        if flag in ("--force", "-f"):
            force = True
        elif flag in ("--refresh", "-r"):
            refresh = True

    local = collect_local_processes()

    # Cron mode only cleans local orphans — skip remote collection entirely to
    # avoid blocking session launch with a potentially slow SSH call.
    remote: list[ProcessInfo] = []
    cache_age: float | None = None
    if remote_host and remote_user and not cron_mode:
        remote, cache_age = collect_remote_processes(
            host=remote_host,
            user=remote_user,
            cache_ttl=cache_ttl,
            force_refresh=refresh,
        )

    all_procs = local + remote

    if action == "clean":
        orphaned = [p for p in all_procs if p.verdict == "orphaned"]
        suspect = [p for p in all_procs if p.verdict == "suspect"]

        if not orphaned and not suspect:
            out("No orphaned or suspect processes found.")
            return 0

        if orphaned:
            out("Orphaned (score ≥ 80):")
            for p in orphaned:
                out(
                    f"  {p.machine.upper():<8} {p.name:<14} pid={p.pid:<8} "
                    f"age={_fmt_age(p.age_seconds):<5} score={p.score}  ({p.detail})"
                )
        if suspect:
            out("Suspect (score 40–79):")
            for p in suspect:
                out(
                    f"  {p.machine.upper():<8} {p.name:<14} pid={p.pid:<8} "
                    f"age={_fmt_age(p.age_seconds):<5} score={p.score}  ({p.detail})"
                )

        to_kill: list[ProcessInfo]
        if force:
            to_kill = orphaned + suspect
        else:
            to_kill = orphaned
            if suspect:
                out(f"\n{len(suspect)} suspect process(es) require --force to include.")

        if not to_kill:
            return 0

        if not force:
            try:
                answer = input(f"\nKill {len(to_kill)} orphan(s)? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
            if answer != "y":
                out("Aborted.")
                return 0

        killed = auto_clean_orphans(to_kill, log_path=log_path)
        out(f"Killed {len(killed)} process(es).")
        return 0

    elif action == "cron" or cron_mode:
        # Silent auto-clean: orphaned local only, log, print summary if any
        killed = auto_clean_orphans(local, log_path=log_path)
        if killed:
            out(f"[ai ps] Cleaned {len(killed)} orphaned process(es). Run 'ai ps' for details.")
        # Clean up stale transport state files (parent PID dead)
        _clean_stale_transport_files()
        return 0

    else:
        # Default: list all processes
        out(format_ps_table(local, remote, cache_age=cache_age, remote_label=remote_host.upper() or "REMOTE"))
        return 0
