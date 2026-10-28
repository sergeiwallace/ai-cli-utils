"""Pick the live background Claude Code session an ``ai c`` relaunch should attach to.

Choosing "Move to background and exit" in Claude Code's exit dialog leaves the
conversation running as a background session with a new short id, while the old
session id stays resumable. Relaunching by title then opens a second interactive
copy beside the live one, and two writers share one transcript.

``claude agents --json`` decides what is live. A configured resolver command may
name a session id, but that id is only a hint and is attached only when the list
shows it live.
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import psutil

from .config import get_xdg_state_home, read_relaunch_config

_BACKGROUND_ID = re.compile(r"[0-9a-f]{8}")
_DIALOG_OPTION = re.compile(r"❯\s*\d+\.")
# Claude Code's refusal when the `disableAgentView` setting or its environment variable is on.
_AGENT_VIEW_OFF = re.compile(r"is disabled by (?:CLAUDE_CODE_DISABLE_AGENT_VIEW|the 'disableAgentView' setting)")
_AGENTS_LIST_TIMEOUT_SECONDS = 15
_PANE_CAPTURE_TIMEOUT_SECONDS = 10
_TMUX_TIMEOUT_SECONDS = 10
# Row words ``claude agents --json`` uses for a conversation that is over. A `done` row is
# not one of them: it is a live copy whose turn ended and which waits for input (its pid is
# alive and its status is idle). A `stopped` row is over, and the default list drops it.
_OVER = frozenset({"finished", "failed", "stopped", "exited", "killed"})
# The states in which a copy is between turns and can take a typed prompt. Claude Code 2.1.311
# reports a copy that finished its turn and waits on the user as `blocked`, with a `needs` text
# on its job record, and a copy holding only a shell sits there too. `working` is mid-turn, and
# typing into it answers the wrong thing. A blocked copy can still be showing a choice, which is
# why delivery also waits for the input box. Any word not listed is treated as not idle.
_IDLE_STATES = frozenset({"done", "blocked"})
_IDLE_STATUSES = frozenset({"idle", ""})
# Seconds between the paste and the Enter that submits it, so the TUI has taken the paste in.
PASTE_SETTLE_SECONDS = 0.4
_COMPOSER_LINES = 25
# The relaunch holds (exit status 75, EX_TEMPFAIL) when resuming might start a second writer.
HOLD_EXIT_STATUS = 75
# Exit status once a hold has lasted the configured time: the script parks instead of waiting on.
HOLD_TIMEOUT_EXIT_STATUS = 76
# Exit status after stopping a copy that ran in another session's environment: the script
# resolves the transcript again, because the stopped copy's conversation is now the one to resume.
STOPPED_FOREIGN_EXIT_STATUS = 73
_STOP_TIMEOUT_SECONDS = 30
_TASK_LIST_VARIABLE = "CLAUDE_CODE_TASK_LIST_ID"
DEFAULT_HOLD_TIMEOUT_SECONDS = 300.0
DEFAULT_HOLD_MAX_ATTEMPTS = 12
DEFAULT_EXIT_COPY_WINDOW_SECONDS = 30.0
DELIVERY_WAIT_SECONDS = 90.0
DELIVERY_POLL_SECONDS = 1.0


@dataclass(frozen=True)
class Resolution:
    """The background session to attach to, or why there is none.

    ``warning`` is set only when a person should hear why the relaunch falls back
    to a title-matched resume, or why it holds. Having no background session at all
    is the ordinary case and stays silent. ``hold`` means the relaunch must neither
    attach nor resume: a live copy may exist and cannot be told from none.
    ``timed_out`` means the hold has lasted the configured time: the script stops waiting.
    ``stopped`` means a copy running in another session's environment was stopped, so the
    transcript to resume must be resolved again.
    """

    background_id: str | None = None
    warning: str | None = None
    hold: bool = False
    timed_out: bool = False
    stopped: bool = False
    matching_ids: tuple[str, ...] = ()


def _word(row: dict, key: str) -> str:
    return str(row.get(key) or "").lower()


def is_idle(row: dict) -> bool:
    """Whether the copy is between turns and can take a typed prompt (an allowlist)."""
    return _word(row, "state") in _IDLE_STATES and _word(row, "status") in _IDLE_STATUSES


def is_copy_title(title: object, name: str) -> bool:
    """Whether ``title`` is session ``name`` or a copy Claude Code numbered ``name (N)``.

    Claude Code numbers a new background copy " (N)" when an older copy already holds
    the bare name, and the copy's transcript keeps that numbered title after it stops.
    """
    return isinstance(title, str) and re.fullmatch(rf"{re.escape(name)}( \(\d+\))?", title) is not None


def is_moved_copy(row: dict, name: str, cwd: Path) -> bool:
    """Whether a background row is the copy of session ``name`` in ``cwd``."""
    if not _same_directory(row.get("cwd"), cwd):
        return False
    return is_copy_title(row.get("name"), name)


def _claude_config_dir() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def _pid_alive(pid: object) -> bool:
    return isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 and psutil.pid_exists(pid)


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _daemon_hosted(config_dir: Path) -> frozenset[str] | None:
    """Short ids of the copies a live daemon worker hosts, or None when the roster cannot be read.

    ``claude agents --json`` reads job records, and the daemon's idle retire of a blocked job
    never writes a terminal state, so a record can outlive both its worker and its daemon.
    The daemon's roster names its own pid and each worker's pid.
    """
    roster = _read_json(config_dir / "daemon" / "roster.json")
    if not isinstance(roster, dict) or not isinstance(roster.get("workers"), dict):
        return None
    if not _pid_alive(roster.get("supervisorPid")):
        return frozenset()
    return frozenset(
        short
        for short, worker in roster["workers"].items()
        if isinstance(worker, dict) and _pid_alive(worker.get("pid"))
    )


def _is_stale_daemon_record(row: dict, config_dir: Path, hosted: frozenset[str] | None) -> bool:
    """Whether ``row`` is a daemon job that no live daemon worker hosts.

    Only a record the daemon owns can be proved stale; an unreadable roster or job record
    leaves the row live, so the relaunch keeps its safe hold.
    """
    if hosted is None or row["id"] in hosted:
        return False
    job = _read_json(config_dir / "jobs" / row["id"] / "state.json")
    return isinstance(job, dict) and job.get("backend") == "daemon"


def list_live_background_sessions() -> list[dict] | None:
    """Live background rows from ``claude agents --json``, or None when the list is unreadable.

    A daemon job record that no live daemon worker hosts is dropped (AI-CLI-h330).
    """
    try:
        completed = subprocess.run(
            ["claude", "agents", "--json"],
            capture_output=True,
            text=True,
            timeout=_AGENTS_LIST_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        # With agent view turned off Claude Code runs no background sessions at all, so its
        # refusal is an empty list, not an unreadable one that holds the relaunch.
        return [] if _AGENT_VIEW_OFF.search(completed.stderr or "") else None
    try:
        rows = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(rows, list):
        return None
    listed = [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("kind") not in (None, "interactive")
        and _word(row, "status") not in _OVER
        and _word(row, "state") not in _OVER
        and _BACKGROUND_ID.fullmatch(str(row.get("id", "")))
    ]
    config_dir = _claude_config_dir()
    hosted = _daemon_hosted(config_dir)
    return [row for row in listed if not _is_stale_daemon_record(row, config_dir, hosted)]


def _capture_pane(tmux_session: str, destination: Path) -> bool:
    """Save the pane text, scrollback included. tmux is POSIX-only, as is the supervisor that calls this."""
    tmux = shutil.which("tmux")
    if not tmux or not tmux_session:
        return False
    try:
        completed = subprocess.run(
            [tmux, "capture-pane", "-p", "-S", "-", "-t", tmux_session],
            capture_output=True,
            text=True,
            timeout=_PANE_CAPTURE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if completed.returncode != 0:
        return False
    destination.write_text(completed.stdout, encoding="utf-8")
    return True


def _run_resolver(command: str, session_key: str, tmux_session: str, timeout: float) -> tuple[str | None, str | None]:
    """Run the configured command. Returns (id, None) for an answer, else (None, reason)."""
    try:
        words = shlex.split(command)
    except ValueError as error:
        return None, f"has an unparsable command ({error})"
    if not words:
        return None, "has an empty command"
    words[0] = str(Path(words[0]).expanduser())
    with tempfile.TemporaryDirectory(prefix="ai-background-resolver-") as scratch:
        capture = Path(scratch) / "pane.txt"
        if not _capture_pane(tmux_session, capture):
            return None, "was skipped because the session pane could not be captured"
        try:
            completed = subprocess.run(
                [*words, "--session", session_key, "--pane-capture", str(capture)],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return None, f"timed out after {timeout:g}s"
        except OSError as error:
            return None, f"could not run ({error})"
    if completed.returncode != 0:
        return None, f"exited {completed.returncode}"
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(lines) != 1 or not _BACKGROUND_ID.fullmatch(lines[0]):
        return None, "did not print exactly one 8-character id"
    return lines[0], None


def _same_directory(left: object, right: Path) -> bool:
    return isinstance(left, str) and Path(left).resolve() == right.resolve()


def hold_timeout_seconds(config: dict) -> float:
    """The configured hold limit, read from ``config`` as given; the default when it is invalid."""
    try:
        return float(read_relaunch_config(config)["background_hold_timeout_seconds"])
    except ValueError:
        return DEFAULT_HOLD_TIMEOUT_SECONDS


def hold_max_attempts(config: dict) -> int:
    """The configured hold-attempt limit, or the safe default when config is invalid."""
    try:
        return int(read_relaunch_config(config)["background_hold_max_attempts"])
    except ValueError:
        return DEFAULT_HOLD_MAX_ATTEMPTS


def _recent_exit_copy(row: dict, relaunch_started_at: float, window_seconds: float) -> bool:
    """Whether ``row`` has the provenance of the copy created by the exiting client."""
    pid = row.get("pid")
    started_at = row.get("startedAt")
    if (
        relaunch_started_at <= 0
        or _word(row, "status") != "idle"
        or isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 0
        or isinstance(started_at, bool)
        or not isinstance(started_at, (int, float))
        or not math.isfinite(started_at)
    ):
        return False
    started_seconds = started_at / 1000.0
    return relaunch_started_at - window_seconds <= started_seconds <= relaunch_started_at


def _manual_remedies(background_ids: tuple[str, ...]) -> str:
    return "; ".join(
        f"use `claude attach {background_id}` to continue it or `claude stop {background_id}` to stop it"
        for background_id in background_ids
    )


def resolve_background_target(
    config: dict,
    cwd: Path,
    ai_name: str,
    tmux_session: str,
    *,
    resume_follows: bool = False,
    prompted: bool = False,
    held_seconds: float = 0.0,
    hold_attempt: int = 0,
    relaunch_started_at: float = 0.0,
) -> Resolution:
    """Choose the background session to attach to; a hold that has lasted too long times out.

    ``held_seconds`` and ``hold_attempt`` describe the caller's existing hold. A hold never
    passes either configured bound: the result then carries ``timed_out`` and the caller
    parks instead of waiting on a copy that may never settle.
    """
    resolution = _resolve(
        config,
        cwd,
        ai_name,
        tmux_session,
        resume_follows=resume_follows,
        prompted=prompted,
        relaunch_started_at=relaunch_started_at,
    )
    limit = hold_timeout_seconds(config)
    attempt_limit = hold_max_attempts(config)
    time_exhausted = math.isfinite(held_seconds) and held_seconds >= limit
    attempts_exhausted = hold_attempt >= attempt_limit
    if not resolution.hold or not (time_exhausted or attempts_exhausted):
        return resolution
    if attempts_exhausted:
        exhausted = f"gave up after {hold_attempt} attempts (relaunch.background_hold_max_attempts = {attempt_limit})"
    else:
        exhausted = f"gave up after {held_seconds:.0f}s (relaunch.background_hold_timeout_seconds = {limit:g})"
    remedies = _manual_remedies(resolution.matching_ids)
    return replace(
        resolution,
        timed_out=True,
        warning=f"{resolution.warning}; {exhausted} and is not resuming beside it"
        + (f"; {remedies}" if remedies else ""),
    )


def hold_log_path(config: dict) -> Path:
    """The log a parked relaunch writes: ``relaunch.background_hold_log``, else the launcher's own."""
    try:
        configured = str(read_relaunch_config(config)["background_hold_log"]).strip()
    except ValueError:
        configured = ""
    if configured:
        return Path(configured).expanduser()
    return get_xdg_state_home() / "relaunch-hold.log"


def record_hold_timeout(config: dict, ai_name: str, reason: str, held_seconds: float, log: Path | None = None) -> None:
    """Append one ``UR-RELAUNCH-PARKED`` line, in the shape of the update-restart log's lines."""
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    quoted = json.dumps(reason)
    line = f"{stamp} UR-RELAUNCH-PARKED session={ai_name} held={held_seconds:.0f}s reason={quoted}\n"
    path = log or hold_log_path(config)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass  # The pane still says why; a log that cannot be written must not stop the park.


def _resolve(
    config: dict,
    cwd: Path,
    ai_name: str,
    tmux_session: str,
    *,
    resume_follows: bool = False,
    prompted: bool = False,
    relaunch_started_at: float = 0.0,
) -> Resolution:
    """Choose the background session to attach to, in order: resolver hint, matching copy.

    A recorded or matched id is never attached unless ``claude agents --json`` lists
    it as a live background session. ``resume_follows`` says the caller would otherwise
    resume the transcript, and ``prompted`` that a resume prompt is waiting to be
    delivered. A resume never starts beside a live copy: when the copy cannot be told
    from none (an unreadable list, several matches) and a resume follows, or when a
    prompt is waiting and the one copy is mid-turn, the result holds instead.
    """
    notes: list[str] = []
    try:
        settings = read_relaunch_config(config)
    except ValueError as error:
        settings = None
        notes.append(f"ignoring {error}")

    rows = list_live_background_sessions()
    if rows is None:
        warning = "could not read `claude agents --json`"
        if resume_follows:
            return Resolution(
                warning=f"{warning}; holding the relaunch rather than resume beside a possible live copy", hold=True
            )
        return Resolution(warning=f"{warning}; resuming by title instead")

    command = settings["background_session_resolver"].strip() if settings else ""
    if settings and command:
        hint, reason = _run_resolver(
            command, ai_name, tmux_session, settings["background_session_resolver_timeout_seconds"]
        )
        if hint is None:
            notes.append(f"background resolver {reason}")
        elif any(row["id"] == hint for row in rows):
            return _attach_or_hold(next(row for row in rows if row["id"] == hint), prompted, ai_name, cwd)
        else:
            notes.append(f"background resolver named {hint}, which is not a live background session")

    matches = [row for row in rows if is_moved_copy(row, ai_name, cwd)]
    if len(matches) == 1:
        return _attach_or_hold(matches[0], prompted, ai_name, cwd)
    if len(matches) > 1:
        window_seconds = (
            settings["background_exit_copy_window_seconds"] if settings else DEFAULT_EXIT_COPY_WINDOW_SECONDS
        )
        recent = [row for row in matches if _recent_exit_copy(row, relaunch_started_at, window_seconds)]
        if len(recent) == 1 and all(is_idle(row) for row in matches if row is not recent[0]):
            # A blocked state can be an exit dialog even though the row is already idle.
            # Attaching is safe; prompt delivery independently waits for the composer.
            return _attach_or_hold(recent[0], False, ai_name, cwd)
        ids = ", ".join(sorted(row["id"] for row in matches))
        notes.append(f"{len(matches)} live background sessions match this name and directory ({ids})")
        if resume_follows:
            return Resolution(
                warning="; ".join(notes) + "; holding the relaunch rather than resume beside them",
                hold=True,
                matching_ids=tuple(sorted(row["id"] for row in matches)),
            )
    if notes:
        return Resolution(warning="; ".join(notes) + "; resuming by title instead")
    return Resolution()


def hosting_environment(background_id: str) -> dict[str, str] | None:
    """The environment of the process hosting background copy ``background_id``, or None.

    ``claude agents --json`` gives a background row no pid. The process that hosts the copy
    names the copy's socket, ``.../pty/<id>.sock``, as one argument, so it is found by that;
    a command that only mentions the path inside a longer argument (a shell line) is not it.
    None when no such process is found or its environment cannot be read.
    """
    socket_name = f"{background_id}.sock"
    for process in psutil.process_iter(["cmdline"]):
        try:
            arguments = [Path(part) for part in process.info.get("cmdline") or ()]
            if any(path.name == socket_name and path.parent.name == "pty" for path in arguments):
                return process.environ()
        except (psutil.Error, OSError):
            continue
    return None


def foreign_owner(background_id: str, ai_name: str, cwd: Path) -> str | None:
    """Whose environment hosts the copy, when it is not session ``ai_name``'s own; else None.

    A Claude Code daemon pre-forks spare hosts from the environment of the session that
    started it, and a later session moved to the background can be hosted by one of them.
    That copy then runs with the other session's task list (AI-CLI-qe0c). The host's
    ``CLAUDE_CODE_TASK_LIST_ID`` names whose environment it is; a host without one is
    judged by the directory it was started from. An unreadable host is not judged foreign.
    """
    environment = hosting_environment(background_id)
    if environment is None:
        return None
    owner = environment.get(_TASK_LIST_VARIABLE)
    if owner:
        return None if owner == ai_name else owner
    started_in = environment.get("PWD")
    if started_in and not _same_directory(started_in, cwd):
        return started_in
    return None


def stop_background_session(background_id: str) -> bool:
    """Stop a background copy with ``claude stop``; its conversation is kept."""
    try:
        completed = subprocess.run(
            ["claude", "stop", background_id],
            capture_output=True,
            text=True,
            timeout=_STOP_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _attach_or_hold(row: dict, prompted: bool, ai_name: str, cwd: Path) -> Resolution:
    """Attach to ``row``; with a prompt waiting, only once the copy is between turns.

    A copy hosted in another session's environment is never attached: it is stopped once it
    is between turns, and the relaunch then resumes its conversation as an ordinary process.
    """
    owner = foreign_owner(row["id"], ai_name, cwd)
    if owner is not None:
        described = f"background session {row['id']} runs in the environment of {owner}, not {ai_name}"
        if not is_idle(row):
            return Resolution(
                warning=f"{described}; holding the relaunch until it is idle so it can be stopped",
                hold=True,
                matching_ids=(row["id"],),
            )
        if not stop_background_session(row["id"]):
            return Resolution(
                warning=f"{described}; `claude stop {row['id']}` failed, holding the relaunch",
                hold=True,
                matching_ids=(row["id"],),
            )
        return Resolution(warning=f"{described}; stopped it and resuming its conversation instead", stopped=True)
    if prompted and not is_idle(row):
        return Resolution(
            warning=(
                f"background session {row['id']} is not between turns "
                f"(state {_word(row, 'state') or 'unknown'}); holding the relaunch until it is idle"
            ),
            hold=True,
            matching_ids=(row["id"],),
        )
    return Resolution(background_id=row["id"])


def _tmux(*args: str) -> subprocess.CompletedProcess | None:
    tmux = shutil.which("tmux")
    if not tmux:
        return None
    try:
        return subprocess.run(
            [tmux, *args],
            capture_output=True,
            text=True,
            timeout=_TMUX_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _composer_drawn(tmux_session: str) -> bool:
    """Whether the pane shows Claude's input box: its prompt line between two rules.

    A choice dialog marks its selected option with the same ``❯``, followed by a number,
    and a pane showing one is not taking a prompt.
    """
    captured = _tmux("capture-pane", "-p", "-t", tmux_session)
    if captured is None or captured.returncode != 0:
        return False
    lines = [line.strip() for line in captured.stdout.splitlines() if line.strip()][-_COMPOSER_LINES:]
    if any(_DIALOG_OPTION.match(line) for line in lines):
        return False
    rules = sum(1 for line in lines if len(line) >= 10 and set(line) <= {"─"})
    return rules >= 2 and any(line.startswith("❯") for line in lines)


def _row_for(background_id: str) -> dict | None:
    rows = list_live_background_sessions()
    if rows is None:
        return None
    return next((row for row in rows if row["id"] == background_id), None)


def deliver_resume_prompt(
    tmux_session: str,
    background_id: str,
    prompt_file: Path,
    *,
    wait_seconds: float = DELIVERY_WAIT_SECONDS,
    poll_seconds: float = DELIVERY_POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> str | None:
    """Type the waiting resume prompt into the attached session once it is between turns.

    ``claude attach`` carries no prompt, so the wrapper delivers it afterwards. The prompt
    is pasted as one bracketed paste and submitted only when the copy's row says it is
    live and idle, checked again immediately before the paste, never while it is mid-turn.
    The prompt file is always removed so a later relaunch cannot replay it beside a live
    copy; a prompt that could not be delivered is kept beside it as ``<name>.undelivered``.
    Returns None on delivery, else the reason.
    """
    try:
        text = prompt_file.read_text(encoding="utf-8")
    except OSError as error:
        return f"could not read the resume prompt ({error})"
    reason = "the attached session never became idle with its input box drawn"
    deadline = time.monotonic() + wait_seconds
    delivered = False
    while True:
        row = _row_for(background_id)
        if row is None:
            reason = f"background session {background_id} is no longer listed as live"
        elif is_idle(row) and _composer_drawn(tmux_session):
            delivered, reason = _paste(tmux_session, text, sleep), "tmux could not take the paste"
            if delivered:
                reason = ""
            break
        else:
            reason = "the attached session never became idle with its input box drawn"
        if time.monotonic() >= deadline:
            break
        sleep(poll_seconds)
    if delivered:
        prompt_file.unlink(missing_ok=True)
        return None
    kept = prompt_file.with_name(prompt_file.name + ".undelivered")
    try:
        prompt_file.replace(kept)
    except OSError:
        prompt_file.unlink(missing_ok=True)
        return f"{reason}; the resume prompt could not be kept"
    return f"{reason}; the resume prompt is kept at {kept}"


def _paste(tmux_session: str, text: str, sleep: Callable[[float], None] = time.sleep) -> bool:
    """Paste ``text`` into the session's pane as one bracketed paste, then submit it.

    One trailing newline is dropped so the Enter that submits does not add a second, and the
    Enter waits a moment so a TUI still taking in the paste does not swallow it.
    """
    buffer = f"ai-resume-{tmux_session}"
    with tempfile.TemporaryDirectory(prefix="ai-resume-prompt-") as scratch:
        path = Path(scratch) / "prompt.txt"
        path.write_text(text.removesuffix("\n"), encoding="utf-8")
        for step in (
            ("load-buffer", "-b", buffer, str(path)),
            ("paste-buffer", "-p", "-d", "-b", buffer, "-t", tmux_session),
        ):
            done = _tmux(*step)
            if done is None or done.returncode != 0:
                return False
    sleep(PASTE_SETTLE_SECONDS)
    submitted = _tmux("send-keys", "-t", tmux_session, "Enter")
    return submitted is not None and submitted.returncode == 0
