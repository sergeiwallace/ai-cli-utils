"""Memory watch daemon — inotify-based watcher for CC memory file writes.

Publishes memory.dream.started on first write and memory.dream.completed
after 2 seconds of silence (debounce). Used by ai sync push guard to
avoid syncing during active auto-dream writes.

Linux-only (inotify via watchdog). Mac follow-up: SW-655.
"""

import asyncio
import sys
import time
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileModifiedEvent


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

    def on_modified(self, event):
        if not isinstance(event, FileModifiedEvent):
            return
        if not event.src_path.endswith("MEMORY.md"):
            return
        now = time.time()
        self._last_write = now
        if not self._dreaming:
            self._dreaming = True
            self._on_write_start(event.src_path)

    def check_settle(self):
        """Call periodically. If dreaming and no write for debounce_s, emit settle."""
        if self._dreaming and self._last_write > 0:
            if time.time() - self._last_write >= self._debounce_s:
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
    from .sync import _acquire_pid_file, _release_pid_file
    from .messaging import NATSClient

    if not _acquire_pid_file("memory-watch"):
        print("ai memory watch is already running.", file=sys.stderr)
        return 2

    client = NATSClient()

    # Verify NATS is available at startup
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(client.connect())
    finally:
        pass  # keep loop open for later use

    if not client.nc:
        print("NATS unavailable — cannot start memory watcher.", file=sys.stderr)
        _release_pid_file("memory-watch")
        return 1

    def on_write_start(path):
        print(f"[memory-watch] dream started: {path}")
        try:
            loop.run_until_complete(client.publish("memory.dream.started", {"path": str(path), "ts": time.time()}))
        except Exception as e:
            print(f"[memory-watch] failed to publish dream.started: {e}", file=sys.stderr)

    def on_write_settle():
        print("[memory-watch] dream completed (2s debounce)")
        try:
            loop.run_until_complete(client.publish("memory.dream.completed", {"ts": time.time()}))
        except Exception as e:
            print(f"[memory-watch] failed to publish dream.completed: {e}", file=sys.stderr)

    handler = MemoryFileHandler(on_write_start, on_write_settle)
    observer = Observer()

    dirs = _find_memory_dirs()
    if not dirs:
        print("[memory-watch] no memory directories found to watch", file=sys.stderr)
        _release_pid_file("memory-watch")
        loop.run_until_complete(client.close())
        loop.close()
        return 1

    for d in dirs:
        observer.schedule(handler, str(d), recursive=True)
        print(f"[memory-watch] watching {d}")

    observer.start()
    print("ai memory watch — watching for MEMORY.md writes (Ctrl+C to stop)")

    try:
        while True:
            handler.check_settle()
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
        loop.run_until_complete(client.close())
        loop.close()
        _release_pid_file("memory-watch")

    return 0
