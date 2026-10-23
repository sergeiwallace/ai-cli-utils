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
from dataclasses import dataclass
from pathlib import Path

from .config import read_relaunch_config

_BACKGROUND_ID = re.compile(r"[0-9a-f]{8}")
_AGENTS_LIST_TIMEOUT_SECONDS = 15
_PANE_CAPTURE_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class Resolution:
    """The background session to attach to, or why there is none.

    ``warning`` is set only when a person should hear why the relaunch falls back
    to a title-matched resume. Having no background session at all is the ordinary
    case and stays silent.
    """

    background_id: str | None = None
    warning: str | None = None


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
        and row.get("status") != "finished"
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


def resolve_background_target(config: dict, cwd: Path, ai_name: str, tmux_session: str) -> Resolution:
    """Choose the background session to attach to, in order: resolver hint, exact name-and-directory match.

    Anything else returns no id, so the caller keeps its title-matched resume. A
    recorded or matched id is never attached unless ``claude agents --json`` lists
    it as a live background session.
    """
    notes: list[str] = []
    try:
        settings = read_relaunch_config(config)
    except ValueError as error:
        settings = None
        notes.append(f"ignoring {error}")

    rows = list_live_background_sessions()
    if rows is None:
        return Resolution(warning="could not read `claude agents --json`; resuming by title instead")

    command = settings["background_session_resolver"].strip() if settings else ""
    if command:
        hint, reason = _run_resolver(
            command, ai_name, tmux_session, settings["background_session_resolver_timeout_seconds"]
        )
        if hint is None:
            notes.append(f"background resolver {reason}")
        elif any(row["id"] == hint for row in rows):
            return Resolution(background_id=hint)
        else:
            notes.append(f"background resolver named {hint}, which is not a live background session")

    matches = [row for row in rows if row.get("name") == ai_name and _same_directory(row.get("cwd"), cwd)]
    if len(matches) == 1:
        return Resolution(background_id=matches[0]["id"])
    if len(matches) > 1:
        ids = ", ".join(sorted(row["id"] for row in matches))
        notes.append(f"{len(matches)} live background sessions match this name and directory ({ids})")
    if notes:
        return Resolution(warning="; ".join(notes) + "; resuming by title instead")
    return Resolution()
