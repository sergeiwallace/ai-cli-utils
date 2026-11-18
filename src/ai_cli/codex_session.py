"""Name a Codex session after its launcher session, and find it again by that name.

Depends on: background_attach.py (the tmux paste used for a waiting resume prompt).

Codex has no launch-time option or config key that names a thread, and a
``SessionStart`` hook can only add model context. A name is set by ``/rename``, which
appends ``{"id", "thread_name", "updated_at"}`` to ``$CODEX_HOME/session_index.jsonl``;
the newest non-empty entry for an id is its name. Codex's own picker and its
``codex resume <name>`` lookup skip a thread that has had no turn, while
``codex resume <id>`` opens it. So the launcher resolves the name to an id itself
(:func:`find_thread`), and types ``/rename <name>`` only when no thread carries the name
yet (:func:`name_session`), which is the session's first launch.

Design: docs/designs/codex-session-naming.md.
"""

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

INDEX_FILE = "session_index.jsonl"
#: The idle footer Codex draws once its composer takes input. The ``›`` prompt line is
#: drawn earlier, while Codex is still loading, and keys typed then are lost or queued.
READY_MARKER = "? for shortcuts"
READY_WAIT_SECONDS = 30.0
#: A ``/rename`` submitted while Codex is still starting is queued until startup ends, and a
#: first start in a new Codex home installs its app-server first (measured: the name landed
#: 28 s after launch). The namer is detached, so a long wait costs nothing.
CONFIRM_WAIT_SECONDS = 120.0
POLL_SECONDS = 0.5
#: A pause between the typed command and its Enter, so a TUI that treats a fast burst
#: of keys as a paste does not take the Enter as a newline inside it.
SUBMIT_SETTLE_SECONDS = 0.5
#: The bare pty path has no screen to read, so it waits this long before typing.
FIFO_START_SECONDS = 5.0
#: Where :func:`bind_pane` records each thread's pane, under ``$CODEX_HOME``.
BINDINGS_DIR = "pane-bindings"
#: A new session is named only after Codex starts and records the ``/rename``.
BIND_WAIT_SECONDS = READY_WAIT_SECONDS + CONFIRM_WAIT_SECONDS
_TMUX_TIMEOUT_SECONDS = 5.0
_HELP_TIMEOUT_SECONDS = 10.0


def exclusive_process_flags(codex: str = "codex", cache_dir: Path | None = None) -> list[str]:
    """``["--no-daemon"]`` when the installed Codex offers it, else nothing.

    By default Codex 0.169 starts one shared app-server daemon that every TUI on the host
    connects to, so tool processes would descend from that shared server rather than from
    the session's own Codex. ``--no-daemon`` keeps each session in its own process. An
    older Codex without the option would refuse it, so it is passed only when ``codex
    --help`` lists it. The answer is cached per binary (resolved path, size, mtime), so the
    help runs once per Codex install, and the probe is bounded so a Codex that does not
    answer ``--help`` cannot stall a launch.
    """
    path = shutil.which(codex)
    if path is None:
        return []
    resolved = Path(path).resolve()
    try:
        stat = resolved.stat()
    except OSError:
        return []
    key = f"{resolved}\t{stat.st_size}\t{stat.st_mtime_ns}"
    cache = (cache_dir or _flags_cache_dir()) / "codex-exclusive-flags"
    try:
        cached_key, _, cached_flags = cache.read_text(encoding="utf-8").partition("\n")
        if cached_key == key:
            return cached_flags.split()
    except OSError:
        pass
    try:
        shown = subprocess.run(
            [path, "--help"], capture_output=True, text=True, timeout=_HELP_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    flags = ["--no-daemon"] if "--no-daemon" in shown.stdout else []
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(f"{key}\n{' '.join(flags)}", encoding="utf-8")
    except OSError:
        pass
    return flags


def _flags_cache_dir() -> Path:
    from .config import get_xdg_state_home

    return get_xdg_state_home()


def codex_home() -> Path:
    """Codex's state directory: ``$CODEX_HOME``, else ``~/.codex`` on every OS."""
    configured = os.environ.get("CODEX_HOME", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def _index_entries(home: Path) -> list[dict]:
    try:
        text = (home / INDEX_FILE).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    entries = []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("id"), str) and isinstance(entry.get("thread_name"), str):
            entries.append(entry)
    return entries


def _has_rollout(home: Path, thread_id: str) -> bool:
    sessions = home / "sessions"
    if not sessions.is_dir():
        return False
    return any(sessions.rglob(f"rollout-*{thread_id}.jsonl")) or any(sessions.rglob(f"rollout-*{thread_id}.jsonl.zst"))


def find_thread(name: str, home: Path | None = None, *, resumable: bool = True) -> str | None:
    """The id of the newest resumable Codex thread whose current name is ``name``.

    A thread counts only while its newest non-empty index entry still says ``name`` (a
    later ``/rename`` moves it away) and, when ``resumable``, its rollout is still under
    ``sessions/`` (an archived or deleted thread cannot be resumed there). A running
    thread is named before Codex saves its rollout, so :func:`bind_pane` passes
    ``resumable=False``.
    """
    name = name.strip()
    if not name:
        return None
    home = home or codex_home()
    seen: set[str] = set()
    for entry in reversed(_index_entries(home)):
        thread_id, thread_name = entry["id"], entry["thread_name"].strip()
        if not thread_name or thread_id in seen:
            continue
        seen.add(thread_id)
        if thread_name == name and (not resumable or _has_rollout(home, thread_id)):
            return thread_id
    return None


def bind_pane(
    name: str,
    pane: str,
    *,
    home: Path | None = None,
    wait_seconds: float | None = None,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
    now: Callable[[], float] | None = None,
) -> str | None:
    """Record which tmux pane runs the Codex thread named ``name``.

    A tool process inside Codex sees its thread id (``CODEX_THREAD_ID``) but inherits
    ``TMUX_PANE`` from whatever first started Codex's long-lived app-server, which can name
    a pane that has since closed. The launcher owns the pane, so it writes
    ``$CODEX_HOME/pane-bindings/<thread id>.json`` holding ``thread_id``, ``pane_id``,
    ``tmux_session`` and ``written_at``, for such a tool to find its own pane. It runs on
    every launch and relaunch in the pane, so the record follows the live pane. A new
    session has no thread id until its name is recorded, so this waits for the name.
    Returns None once written, else the reason it was not.
    """
    if not pane.startswith("%"):
        return "no tmux pane to bind"
    home = home or codex_home()
    sleep = sleep or time.sleep
    monotonic = monotonic or time.monotonic
    wait_seconds = BIND_WAIT_SECONDS if wait_seconds is None else wait_seconds
    deadline = monotonic() + wait_seconds
    while (thread_id := find_thread(name, home, resumable=False)) is None:
        if monotonic() >= deadline:
            return f"no Codex thread carried the name within {wait_seconds:.0f}s"
        sleep(POLL_SECONDS)
    shown = _tmux("display-message", "-p", "-t", pane, "#{session_name}")
    session = shown.stdout.strip() if shown is not None and shown.returncode == 0 else ""
    if not session:
        return f"tmux does not report pane {pane}"
    record = {"thread_id": thread_id, "pane_id": pane, "tmux_session": session, "written_at": (now or time.time)()}
    directory = home / BINDINGS_DIR
    target = directory / f"{thread_id}.json"
    staging = directory / f".{thread_id}.{os.getpid()}.tmp"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        staging.write_text(json.dumps(record), encoding="utf-8")
        staging.replace(target)
    except OSError as exc:
        staging.unlink(missing_ok=True)
        return f"could not write {target}: {exc}"
    return None


def _index_size(home: Path) -> int:
    try:
        return (home / INDEX_FILE).stat().st_size
    except OSError:
        return 0


def _named_since(home: Path, name: str, offset: int) -> bool:
    """Whether the index gained an entry naming ``name`` after byte ``offset``."""
    try:
        with (home / INDEX_FILE).open("rb") as handle:
            handle.seek(offset)
            added = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return False
    for line in added.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and str(entry.get("thread_name", "")).strip() == name:
            return True
    return False


def _tmux(*args: str) -> subprocess.CompletedProcess | None:
    tmux = shutil.which("tmux")
    if not tmux:
        return None
    try:
        return subprocess.run([tmux, *args], capture_output=True, text=True, timeout=_TMUX_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _ready(tmux_target: str) -> bool:
    captured = _tmux("capture-pane", "-p", "-t", tmux_target)
    return captured is not None and captured.returncode == 0 and READY_MARKER in captured.stdout


def _type_tmux(tmux_target: str, text: str, sleep: Callable[[float], None]) -> bool:
    typed = _tmux("send-keys", "-t", tmux_target, "-l", text)
    if typed is None or typed.returncode != 0:
        return False
    sleep(SUBMIT_SETTLE_SECONDS)
    submitted = _tmux("send-keys", "-t", tmux_target, "Enter")
    return submitted is not None and submitted.returncode == 0


def _type_fifo(fifo: Path, text: str, sleep: Callable[[float], None]) -> bool:
    try:
        # O_NONBLOCK: a FIFO no launcher holds open fails here instead of hanging.
        fd = os.open(fifo, os.O_WRONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return False
    try:
        os.write(fd, text.encode("utf-8"))
        sleep(SUBMIT_SETTLE_SECONDS)
        os.write(fd, b"\r")
    except OSError:
        return False
    finally:
        os.close(fd)
    return True


def name_session(
    name: str,
    *,
    tmux_target: str | None = None,
    fifo: Path | None = None,
    prompt_file: Path | None = None,
    home: Path | None = None,
    ready_wait_seconds: float | None = None,
    confirm_wait_seconds: float | None = None,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> str | None:
    """Type ``/rename <name>`` into a newly started Codex, then confirm it from the index.

    Exactly one of ``tmux_target`` and ``fifo`` names the input path. On tmux it waits for
    Codex's idle footer first; the bare pty path has no screen, so it waits a fixed time.
    A ``prompt_file`` (a relaunch's resume prompt) is pasted only once the name is
    confirmed, so the ``/rename`` is not queued behind a turn; the file is removed either
    way. Returns None once Codex recorded the name, else the reason it did not.
    """
    home = home or codex_home()
    sleep = sleep or time.sleep
    monotonic = monotonic or time.monotonic
    ready_wait_seconds = READY_WAIT_SECONDS if ready_wait_seconds is None else ready_wait_seconds
    confirm_wait_seconds = CONFIRM_WAIT_SECONDS if confirm_wait_seconds is None else confirm_wait_seconds
    try:
        prompt = prompt_file.read_text(encoding="utf-8") if prompt_file is not None else None
    except OSError:
        prompt = None
    if prompt_file is not None:
        prompt_file.unlink(missing_ok=True)

    if tmux_target is not None:
        deadline = monotonic() + ready_wait_seconds
        while not _ready(tmux_target):
            if monotonic() >= deadline:
                return f"Codex never showed its input prompt within {ready_wait_seconds:.0f}s, so it was not named"
            sleep(POLL_SECONDS)
    else:
        sleep(FIFO_START_SECONDS)

    offset = _index_size(home)
    command = f"/rename {name}"
    if tmux_target is not None:
        typed = _type_tmux(tmux_target, command, sleep)
    elif fifo is not None:
        typed = _type_fifo(fifo, command, sleep)
    else:
        return "no input path to type into"
    if not typed:
        return "could not type the rename command into the session"

    deadline = monotonic() + confirm_wait_seconds
    while not _named_since(home, name, offset):
        if monotonic() >= deadline:
            return f"Codex did not record the name within {confirm_wait_seconds:.0f}s"
        sleep(POLL_SECONDS)

    if prompt and prompt.strip():
        if tmux_target is not None:
            from .background_attach import _paste

            delivered = _paste(tmux_target, prompt, sleep)
        else:
            delivered = fifo is not None and _type_fifo(fifo, prompt.removesuffix("\n"), sleep)
        if not delivered:
            return "the session was named, but the resume prompt could not be typed"
    return None
