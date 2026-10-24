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
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import read_relaunch_config

_BACKGROUND_ID = re.compile(r"[0-9a-f]{8}")
_AGENTS_LIST_TIMEOUT_SECONDS = 15
_PANE_CAPTURE_TIMEOUT_SECONDS = 10
_TMUX_TIMEOUT_SECONDS = 10
# Row words ``claude agents --json`` uses for a conversation that is over. A finished
# copy keeps its row, with the bare name, so it must never count as the live one.
_OVER = frozenset({"finished", "done", "failed", "stopped", "exited", "killed"})
# Row words for a copy in the middle of a turn: nothing may be typed into it.
_BUSY_STATUSES = frozenset({"busy"})
_BUSY_STATES = frozenset({"working", "running", "busy"})
# The relaunch holds (exit status 75, EX_TEMPFAIL) when resuming might start a second writer.
HOLD_EXIT_STATUS = 75
DELIVERY_WAIT_SECONDS = 90.0
DELIVERY_POLL_SECONDS = 1.0


@dataclass(frozen=True)
class Resolution:
    """The background session to attach to, or why there is none.

    ``warning`` is set only when a person should hear why the relaunch falls back
    to a title-matched resume, or why it holds. Having no background session at all
    is the ordinary case and stays silent. ``hold`` means the relaunch must neither
    attach nor resume: a live copy may exist and cannot be told from none.
    """

    background_id: str | None = None
    warning: str | None = None
    hold: bool = False


def _word(row: dict, key: str) -> str:
    return str(row.get(key) or "").lower()


def is_busy(row: dict) -> bool:
    """Whether the copy is in the middle of a turn."""
    return _word(row, "status") in _BUSY_STATUSES or _word(row, "state") in _BUSY_STATES


def is_moved_copy(row: dict, name: str, cwd: Path) -> bool:
    """Whether a background row is the copy of session ``name`` in ``cwd``.

    Claude Code numbers a new background copy " (N)" when an older copy already holds
    the bare name, so the moved copy is named ``name`` or ``name (N)``.
    """
    if not _same_directory(row.get("cwd"), cwd):
        return False
    return re.fullmatch(rf"{re.escape(name)}( \(\d+\))?", str(row.get("name") or "")) is not None


def list_live_background_sessions() -> list[dict] | None:
    """Live background rows from ``claude agents --json``, or None when the list is unreadable."""
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
        return None
    try:
        rows = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(rows, list):
        return None
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("kind") not in (None, "interactive")
        and _word(row, "status") not in _OVER
        and _word(row, "state") not in _OVER
        and _BACKGROUND_ID.fullmatch(str(row.get("id", "")))
    ]


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


def resolve_background_target(
    config: dict,
    cwd: Path,
    ai_name: str,
    tmux_session: str,
    *,
    resume_follows: bool = False,
    prompted: bool = False,
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
            return _attach_or_hold(next(row for row in rows if row["id"] == hint), prompted)
        else:
            notes.append(f"background resolver named {hint}, which is not a live background session")

    matches = [row for row in rows if is_moved_copy(row, ai_name, cwd)]
    if len(matches) == 1:
        return _attach_or_hold(matches[0], prompted)
    if len(matches) > 1:
        ids = ", ".join(sorted(row["id"] for row in matches))
        notes.append(f"{len(matches)} live background sessions match this name and directory ({ids})")
        if resume_follows:
            return Resolution(
                warning="; ".join(notes) + "; holding the relaunch rather than resume beside them", hold=True
            )
    if notes:
        return Resolution(warning="; ".join(notes) + "; resuming by title instead")
    return Resolution()


def _attach_or_hold(row: dict, prompted: bool) -> Resolution:
    """Attach to ``row``; with a prompt waiting, only once the copy is between turns."""
    if prompted and is_busy(row):
        return Resolution(
            warning=f"background session {row['id']} is mid-turn; holding the relaunch until it is idle",
            hold=True,
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
    reason = "the attached session never became idle"
    deadline = time.monotonic() + wait_seconds
    delivered = False
    while True:
        row = _row_for(background_id)
        if row is None:
            reason = f"background session {background_id} is no longer listed as live"
        elif not is_busy(row):
            delivered, reason = _paste(tmux_session, text), "tmux could not take the paste"
            if delivered:
                reason = ""
            break
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


def _paste(tmux_session: str, text: str) -> bool:
    """Paste ``text`` into the session's pane as one bracketed paste, then submit it."""
    buffer = f"ai-resume-{tmux_session}"
    with tempfile.TemporaryDirectory(prefix="ai-resume-prompt-") as scratch:
        path = Path(scratch) / "prompt.txt"
        path.write_text(text, encoding="utf-8")
        steps = (
            ("load-buffer", "-b", buffer, str(path)),
            ("paste-buffer", "-p", "-d", "-b", buffer, "-t", tmux_session),
            ("send-keys", "-t", tmux_session, "Enter"),
        )
        for step in steps:
            done = _tmux(*step)
            if done is None or done.returncode != 0:
                return False
    return True
