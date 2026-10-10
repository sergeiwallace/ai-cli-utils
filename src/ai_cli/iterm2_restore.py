"""Re-attach the sessions in the iTerm2 session registry after an iTerm2 restart (macOS).

The registry supplies the sessions, a saved iTerm2 arrangement supplies the window
shape, and tmux (locally) or the remote host's tmux supplies survival. Each selected
session is brought back by typing its recorded ``relaunch_argv`` into a pane: its own
recorded pane when that pane is an idle shell, otherwise a new tab in the frontmost
window. Everything iTerm2 is asked to do goes through AppleScript, except restoring a
saved arrangement by name, which only iTerm2's ``it2`` utility can do.

Depends on: iterm2.py, session_registry.py
"""

import fnmatch
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import iterm2 as _iterm2
from . import session_registry as _session_registry

IT2_PATH = Path("/Applications/iTerm.app/Contents/Resources/utilities/it2")
#: Bound on each AppleScript call; iTerm2 that does not answer in this long stops the run.
ITERM2_TIMEOUT_SECONDS = 10
#: Bound on each ``it2`` call. ``it2`` can raise iTerm2's API permission prompt, which no
#: script can answer, so a call that outlives this is treated as "it2 unavailable".
IT2_TIMEOUT_SECONDS = 15
#: On ``--startup``, how long to wait for iTerm2 to open its first window before
#: giving up on in-place fill and opening new tabs.
STARTUP_WAIT_SECONDS = 15
#: Bound on the one ``ps`` pass that finds idle shells.
PS_TIMEOUT_SECONDS = 5
MENU_PATH = "Window > Restore Window Arrangement"

_SECTION = "[iterm2.persistence.restore]"
_SHELLS = frozenset({"sh", "bash", "zsh", "fish", "dash", "ksh", "tcsh", "csh", "nu", "xonsh"})


class Iterm2NotAnswering(RuntimeError):
    """iTerm2 timed out, is not running, or refused an AppleScript call; the message says which."""


@dataclass
class _Entry:
    record: dict
    verdict: str  # "restore" | "skipped" | "dead"
    detail: str = ""
    tty: str | None = None  # the pane to type into; None opens a new tab

    @property
    def name(self) -> str:
        return str(self.record.get("name", "?"))


def _disabled_by(config: dict, startup: bool) -> str | None:
    """The key that turns this run off, "" for a silent exit, or None to proceed."""
    if not config["enabled"]:
        return "[iterm2.persistence] enabled=false"
    settings = config["restore"]
    if not settings["enabled"]:
        return f"{_SECTION} enabled=false"
    if startup and not settings["on_startup"]:
        return ""
    if not startup and not settings["on_demand"]:
        return f"{_SECTION} on_demand=false"
    return None


def _skip_rule(record: dict, settings: dict, only: str | None) -> str | None:
    kind = record.get("kind")
    name = str(record.get("name", ""))
    if only and kind != only:
        return f"--only {only}"
    if kind == "local" and not settings["include_local"]:
        return "include_local=false"
    if kind == "remote" and not settings["include_remote"]:
        return "include_remote=false"
    if settings["include"] and not any(fnmatch.fnmatchcase(name, glob) for glob in settings["include"]):
        return f"include {settings['include']!r} has no matching glob"
    for glob in settings["exclude"]:
        if fnmatch.fnmatchcase(name, glob):
            return f"exclude glob {glob!r} matches"
    if kind == "remote" and settings["remote_hosts"]:
        alias = _session_registry._remote_alias(record) or ""
        if alias not in settings["remote_hosts"]:
            return f"remote_hosts does not list {alias!r}"
    return None


def _position_key(record: dict) -> tuple:
    position = record.get("iterm2")
    if isinstance(position, dict):
        return (0, position.get("window", 0), position.get("tab", 0), position.get("pane", 0))
    return (1, 0, 0, 0)


def _where(pane: dict) -> str:
    return f"window {pane['window']} tab {pane['tab']} pane {pane['pane']}"


def _select(records: list[dict], settings: dict, only: str | None) -> tuple[list[_Entry], list[_Entry]]:
    """Split live records into (to restore, in recorded-position order) and skipped."""
    skipped: list[_Entry] = []
    chosen: list[dict] = []
    for record in records:
        rule = _skip_rule(record, settings, only)
        if rule is None:
            chosen.append(record)
        else:
            skipped.append(_Entry(record, "skipped", rule))
    chosen.sort(key=lambda r: str(r.get("refreshed_at") or ""), reverse=True)
    limit = settings["max_sessions"]
    if limit and len(chosen) > limit:
        skipped += [_Entry(r, "skipped", f"max_sessions={limit}") for r in chosen[limit:]]
        chosen = chosen[:limit]
    chosen.sort(key=_position_key)
    return [_Entry(r, "restore") for r in chosen], skipped


def _idle_shell_ttys() -> set[str]:
    """Ttys whose foreground process group is only a shell, from one ``ps`` pass.

    A pane at its prompt has the shell as its only foreground (``+``) process; a pane
    running tmux, ssh or Claude Code has that program in the foreground instead. Any
    failure answers "no idle panes", so the caller opens new tabs rather than typing
    into a pane it could not inspect.
    """
    try:
        result = subprocess.run(
            ["ps", "-A", "-o", "tty=,stat=,comm="],
            capture_output=True,
            text=True,
            check=False,
            timeout=PS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if result.returncode != 0:
        return set()
    foreground: dict[str, list[str]] = {}
    for line in (result.stdout or "").splitlines():
        fields = line.split(None, 2)
        if len(fields) != 3 or "+" not in fields[1] or fields[0] in ("?", "??"):
            continue
        tty, _, comm = fields
        foreground.setdefault(f"/dev/{tty}", []).append(Path(comm.strip()).name.lstrip("-"))
    return {tty for tty, comms in foreground.items() if comms and all(c in _SHELLS for c in comms)}


def _resolve_pane(position: dict, panes: dict[str, dict]) -> str | None:
    """The tty of the pane a record names: by session UUID, else by window/tab/pane.

    An arrangement restore assigns new UUIDs, so the position is the fallback; it is the
    last refreshed position when a refresh ran, else the launch-time one.
    """
    session_uuid = position.get("session_uuid")
    for tty, pane in panes.items():
        if session_uuid and pane.get("session_uuid") == session_uuid:
            return tty
    wanted = (position.get("window"), position.get("tab"), position.get("pane"))
    for tty, pane in panes.items():
        if (pane["window"], pane["tab"], pane["pane"]) == wanted:
            return tty
    return None


def _placed(
    selected: list[_Entry], settings: dict, startup: bool, echo: Callable[[str], None]
) -> tuple[list[_Entry], list[_Entry]]:
    """Choose a pane or a new tab for each selected session; return (to restore, already open).

    A session that already has a client is skipped: re-running its ``relaunch_argv``
    re-attaches with ``attach-session -d``, which would pull it out of the pane showing it.
    """
    fill = settings["fill_arrangement"]
    panes = _list_panes(startup, echo) if selected else {}
    idle = _idle_shell_ttys() if fill and panes else set()
    claimed: set[str] = set()
    entries: list[_Entry] = []
    already_open: list[_Entry] = []
    for chosen in selected:
        record = chosen.record
        current = _session_registry._current_tty(record)
        if current:
            shown = f" ({_where(panes[current])})" if current in panes else ""
            already_open.append(_Entry(record, "skipped", f"already open on {current}{shown}"))
            continue
        position = record.get("iterm2")
        tty = _resolve_pane(position, panes) if fill and isinstance(position, dict) else None
        if tty and tty in idle and tty not in claimed:
            claimed.add(tty)
            entries.append(_Entry(record, "restore", _where(panes[tty]), tty))
        else:
            entries.append(_Entry(record, "restore", "new tab"))
    return entries, already_open


def _shell_command(record: dict) -> str:
    """The text typed into the pane: ``cd <cwd> && <relaunch_argv>``.

    The directory matters because a launch without ``-p`` takes its project from it.
    """
    command = shlex.join(str(part) for part in record.get("relaunch_argv") or [])
    cwd = record.get("cwd")
    return f"cd {shlex.quote(str(cwd))} && {command}" if cwd else command


def _applescript_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _osascript(script: str) -> str:
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=ITERM2_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise Iterm2NotAnswering(f"iTerm2 did not answer in {ITERM2_TIMEOUT_SECONDS}s") from exc
    except OSError as exc:
        raise Iterm2NotAnswering(f"osascript could not run: {exc}") from exc
    output = (result.stdout or "").strip()
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        raise Iterm2NotAnswering(f"osascript exited {result.returncode}{f': {detail[-1]}' if detail else ''}")
    if output == "not-running":
        raise Iterm2NotAnswering("iTerm2 is not running")
    return output


def _write_into_pane(tty: str, text: str) -> bool:
    """Type ``text`` into the pane on ``tty``; False when that pane no longer exists."""
    script = f"""if application "iTerm2" is not running then return "not-running"
tell application "iTerm2"
    repeat with w in windows
        repeat with t in tabs of w
            repeat with s in sessions of t
                if tty of s is "{_applescript_text(tty)}" then
                    tell s to write text "{_applescript_text(text)}"
                    return "ok"
                end if
            end repeat
        end repeat
    end repeat
end tell
return "miss"
"""
    return _osascript(script) == "ok"


def _write_into_new_tab(text: str) -> None:
    """Open a tab in the frontmost window (a window when there is none) and type ``text`` there."""
    script = f"""if application "iTerm2" is not running then return "not-running"
tell application "iTerm2"
    if (count windows) is 0 then
        set w to (create window with default profile)
    else
        set w to current window
        if w is missing value then set w to window 1
        tell w to create tab with default profile
    end if
    tell current session of current tab of w to write text "{_applescript_text(text)}"
end tell
return "ok"
"""
    _osascript(script)


def _it2(*args: str) -> subprocess.CompletedProcess | None:
    """Run ``it2``; None when it is absent, cannot run, or did not finish (an unanswered prompt)."""
    if not IT2_PATH.exists():
        return None
    try:
        return subprocess.run(
            [str(IT2_PATH), *args], capture_output=True, text=True, check=False, timeout=IT2_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _saved_arrangements(listing: str) -> list[str]:
    return [line.strip()[2:].strip() for line in listing.splitlines() if line.strip().startswith("- ")]


def _restore_arrangement(name: str, use_it2: bool) -> str:
    """Open the saved arrangement ``name`` on demand and return the line describing what happened."""
    manual = f"arrangement: open it via {MENU_PATH} > {name}"
    if not use_it2:
        return manual
    listing = _it2("window", "arrange", "list")
    if listing is None or listing.returncode != 0:
        return f"{manual} (it2 is unavailable or was refused)"
    saved = _saved_arrangements(listing.stdout or "")
    if name not in saved:
        known = ", ".join(repr(a) for a in saved) or "none"
        return f"arrangement: {name!r} is not a saved arrangement (saved: {known}); restoring sessions only"
    restored = _it2("window", "arrange", "restore", name)
    if restored is None or restored.returncode != 0:
        return f"{manual} (it2 could not restore it)"
    return f"arrangement: restored {name!r} with it2"


def _arrangement_plan(name: str, settings: dict, startup: bool) -> str:
    if startup:
        return f"arrangement: {name!r} is opened by iTerm2 itself at startup; filling its panes"
    if settings["use_it2"]:
        return f"arrangement: would restore {name!r} with it2"
    return f"arrangement: open it via {MENU_PATH} > {name}"


def _list_panes(startup: bool, echo: Callable[[str], None]) -> dict[str, dict]:
    """One AppleScript pass over iTerm2's panes; empty (so new tabs) when it cannot be read.

    At startup iTerm2 may still be opening its arrangement, so an empty answer is retried
    once a second for up to :data:`STARTUP_WAIT_SECONDS`.
    """
    for attempt in range(STARTUP_WAIT_SECONDS + 1):
        try:
            panes = _iterm2._iterm2_panes_by_tty(ITERM2_TIMEOUT_SECONDS)
        except _iterm2.Iterm2PaneLookupError as exc:
            echo(f"panes: not read ({exc}); sessions open in new tabs")
            return {}
        if panes or not startup or attempt == STARTUP_WAIT_SECONDS:
            return panes
        time.sleep(1)
    return {}


def _line(entry: _Entry) -> str:
    verdict = "restored" if entry.verdict == "restore" else entry.verdict
    return f"{entry.name}: {verdict} ({entry.detail})"


def restore(
    *,
    startup: bool,
    dry_run: bool,
    only: str | None,
    arrangement: str | None,
    echo: Callable[[str], None],
    ask: Callable[[str], bool],
) -> int:
    """Run ``ai iterm2 restore`` and return its exit status.

    Raises :class:`ai_cli.iterm2.PersistenceConfigError` for a bad config key and
    :class:`ai_cli.session_registry.RegistryError` for an unreadable registry.
    """
    if sys.platform != "darwin":
        echo("restore is macOS/iTerm2 only")
        return 0
    config = _iterm2.load_persistence_config()
    disabled = _disabled_by(config, startup)
    if disabled is not None:
        if disabled:
            echo(f"restore disabled by {disabled}")
        return 0
    settings = config["restore"]
    previewing = dry_run or (settings["confirm"] and not startup)

    dead = [_Entry(r, "dead", reason) for r, reason in _session_registry.prune(dry_run=previewing)]
    dead_ids = {e.record.get("id") for e in dead}
    live = [r for r in _session_registry.load_registry()["sessions"] if r.get("id") not in dead_ids]
    if not live and not dead:
        echo(f"sessions: none recorded in {_session_registry.registry_path()}")
    selected, skipped = _select(live, settings, only)
    if settings["mode"] == "arrangement":
        skipped += [_Entry(e.record, "skipped", 'mode="arrangement"') for e in selected]
        selected = []
    name = arrangement if arrangement is not None else settings["default_arrangement"]
    wants_arrangement = bool(name) and "arrangement" in settings["mode"]

    if previewing:
        entries, already_open = _placed(selected, settings, startup, echo)
        if wants_arrangement:
            echo(_arrangement_plan(name, settings, startup))
        for entry in entries:
            echo(f"{entry.name}: would restore ({entry.detail}): {_shell_command(entry.record)}")
        for entry in already_open + skipped + dead:
            echo(_line(entry))
        if dry_run:
            return 0
        if not ask(f"Restore {len(entries)} session(s)?"):
            echo("restore cancelled; nothing changed")
            return 0
        _session_registry.prune()

    if wants_arrangement and not startup:
        echo(_restore_arrangement(name, settings["use_it2"]))
    entries, already_open = _placed(selected, settings, startup, echo)
    for index, entry in enumerate(entries):
        if index:
            time.sleep(settings["stagger_seconds"])
        text = _shell_command(entry.record)
        try:
            if entry.tty is None or not _write_into_pane(entry.tty, text):
                if entry.tty is not None:
                    entry.detail = "new tab"
                _write_into_new_tab(text)
        except Iterm2NotAnswering as exc:
            echo(f"stopped: {exc}; the sessions below stay in the registry")
            for remaining in entries[index:]:
                echo(f"{remaining.name}: not restored ({exc})")
            return 1
        echo(_line(entry))
    for entry in already_open + skipped + dead:
        echo(_line(entry))
    return 0
