"""Memory watch daemon — inotify-based watcher for CC memory file writes.

Publishes memory.dream.started on first write and memory.dream.completed
after 2 seconds of silence (debounce). Used by ai sync push guard to
avoid syncing during active auto-dream writes.

Linux-only (inotify via watchdog). macOS support is tracked separately.
"""

import asyncio
import os
import time
from pathlib import Path

from watchdog.events import FileModifiedEvent, FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from . import output as _out
from .output import Tag


class MemoryFileHandler(FileSystemEventHandler):
    """Watches MEMORY.md files for modifications and tracks dream state."""

    def __init__(self, on_write_start, on_write_settle):
        super().__init__()
        self._on_write_start = on_write_start
        self._on_write_settle = on_write_settle
        self._dreaming = False
        self._last_write = 0.0
        self._debounce_s = 2.0

    @property
    def dreaming(self):
        return self._dreaming

    def on_modified(self, event: FileSystemEvent) -> None:
        if not isinstance(event, FileModifiedEvent):
            return
        if not str(event.src_path).endswith("MEMORY.md"):
            return
        now = time.time()
        self._last_write = now
        if not self._dreaming:
            self._dreaming = True
            self._on_write_start(event.src_path)

    def check_settle(self):
        """Call periodically. If dreaming and no write for debounce_s, emit settle."""
        if self._dreaming and self._last_write > 0 and time.time() - self._last_write >= self._debounce_s:
            self._dreaming = False
            self._on_write_settle()


def _find_memory_dirs() -> list[Path]:
    """Find all CC project memory directories to watch."""
    cc_projects = Path.home() / ".claude" / "projects"
    dirs = []
    if cc_projects.exists():
        for project_dir in cc_projects.iterdir():
            if not project_dir.is_dir():
                continue
            memory_dir = project_dir / "memory"
            if memory_dir.exists():
                dirs.append(memory_dir)
            elif (project_dir / "MEMORY.md").exists():
                dirs.append(project_dir)
    return dirs


def memory_watch() -> int:
    """Run the memory watch daemon.

    Watches ~/.claude/projects/*/memory/MEMORY.md for writes.
    Publishes memory.dream.started and memory.dream.completed to NATS JetStream.
    PID file guard prevents duplicate instances.
    Exit codes: 0 = clean stop, 1 = error
    """
    from .messaging import NATSClient
    from .sync import _acquire_pid_file, _dream_state_path, _release_pid_file

    if not _acquire_pid_file("memory-watch"):
        _out.emit(Tag.MEMORY, "ai memory watch is already running.", err=True)
        return 2

    dream_state_path = _dream_state_path()
    dream_state_path.unlink(missing_ok=True)

    client = NATSClient()

    # Verify NATS is available at startup
    loop = asyncio.new_event_loop()
    loop.run_until_complete(client.connect())

    if not client.nc:
        _out.emit(Tag.MEMORY, "NATS unavailable — cannot start memory watcher.", err=True)
        loop.close()
        _release_pid_file("memory-watch")
        return 1

    def on_write_start(path):
        dream_state_path.parent.mkdir(parents=True, exist_ok=True)
        dream_state_path.write_text(str(os.getpid()), encoding="utf-8")
        _out.emit(Tag.MEMORY_WATCH, f"dream started: {path}")
        try:
            loop.run_until_complete(client.publish("memory.dream.started", {"path": str(path), "ts": time.time()}))
        except Exception as e:
            _out.emit(Tag.MEMORY_WATCH, f"failed to publish dream.started: {e}", err=True)

    def on_write_settle():
        dream_state_path.unlink(missing_ok=True)
        _out.emit(Tag.MEMORY_WATCH, "dream completed (2s debounce)")
        try:
            loop.run_until_complete(client.publish("memory.dream.completed", {"ts": time.time()}))
        except Exception as e:
            _out.emit(Tag.MEMORY_WATCH, f"failed to publish dream.completed: {e}", err=True)

    handler = MemoryFileHandler(on_write_start, on_write_settle)
    observer = Observer()

    dirs = _find_memory_dirs()
    if not dirs:
        _out.emit(Tag.MEMORY_WATCH, "no memory directories found to watch", err=True)
        _release_pid_file("memory-watch")
        loop.run_until_complete(client.close())
        loop.close()
        return 1

    for d in dirs:
        observer.schedule(handler, str(d), recursive=True)
        _out.emit(Tag.MEMORY_WATCH, f"watching {d}")

    observer.start()
    _out.emit(Tag.MEMORY, "ai memory watch — watching for MEMORY.md writes (Ctrl+C to stop)")

    try:
        while True:
            handler.check_settle()
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
        dream_state_path.unlink(missing_ok=True)
        loop.run_until_complete(client.close())
        loop.close()
        _release_pid_file("memory-watch")

    return 0
