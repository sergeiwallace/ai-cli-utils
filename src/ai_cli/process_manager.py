"""Circus daemon management for persistent utility processes.

Depends on: config.py.
"""

import shutil
import subprocess
import sys
import threading
from pathlib import Path

import portalocker

from . import output as _out
from .config import _pid_alive, get_xdg_state_home
from .output import Tag

_STALE_SESSION_REAPER_WATCHER = "stale-session-reaper"
_STALE_SESSION_REAPER_START_LOCK = threading.Lock()


def _native_windows() -> bool:
    """The stale-session reaper does not run on native Windows.

    Its watcher and its tmux termination path were built for POSIX hosts; a Windows
    host with psmux has a tmux, but the reaper is not supported there.
    """
    return sys.platform == "win32"


def _circusd_binary() -> str:
    """circusd from this interpreter's own environment first.

    ``uv tool install`` links only the package's entry point (``ai``) onto PATH; circus is
    a dependency, so its ``circusd`` script exists only beside the tool's interpreter. A
    remote login shell (``ai c -R``) has ``~/.local/bin`` on PATH and nothing else, so the
    PATH lookup and the ``~/.local/bin`` guess both missed it there.
    """
    sibling = Path(sys.executable).parent / ("circusd.exe" if sys.platform == "win32" else "circusd")
    if sibling.is_file():
        return str(sibling)
    return shutil.which("circusd") or str(Path.home() / ".local" / "bin" / "circusd")


def _ensure_circusd() -> str:
    """Start circusd if not already running. Returns the endpoint URI."""
    import time as _time

    state_dir = get_xdg_state_home()
    state_dir.mkdir(parents=True, exist_ok=True)
    endpoint = f"ipc://{state_dir}/circus.endpoint"

    # Check PID file first — if the PID is dead, clean up stale socket files so
    # CircusClient doesn't hang connecting to a dead IPC socket (ZMQ connects to
    # the file but nobody's listening, and send_message blocks indefinitely).
    pid_file = state_dir / "circusd.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if not _pid_alive(pid):
                raise ProcessLookupError(pid)
        except (ValueError, ProcessLookupError):
            for _stale in ("circus.endpoint", "circus.pubsub", "circusd.pid"):
                (state_dir / _stale).unlink(missing_ok=True)

    # Try existing daemon first
    endpoint_sock = state_dir / "circus.endpoint"
    if endpoint_sock.exists():
        try:
            from circus.client import CircusClient

            CircusClient(endpoint=endpoint, timeout=1.0).send_message("status")
            return endpoint
        except Exception:
            pass

    # Write circus.ini
    ini_path = state_dir / "circus.ini"
    ini_path.write_text(
        f"[circus]\n"
        f"endpoint        = {endpoint}\n"
        f"pubsub_endpoint = ipc://{state_dir}/circus.pubsub\n"
        f"logoutput       = {state_dir}/circus.log\n"
        f"umask           = 0o022\n"
    )

    circusd_bin = _circusd_binary()
    pidfile = str(state_dir / "circusd.pid")
    # circusd daemonizes and logs to `logoutput`; an inherited terminal would let
    # its untagged lines through, and a pipe would be held open by the daemon.
    subprocess.Popen(
        [circusd_bin, "--daemon", "--pidfile", pidfile, str(ini_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Poll until ready
    from circus.client import CircusClient

    for _ in range(10):
        _time.sleep(0.3)
        try:
            CircusClient(endpoint=endpoint, timeout=1.0).send_message("status")
            return endpoint
        except Exception:
            pass

    raise RuntimeError("circusd did not start in time")


def _cmd_stale_session_reaper_start() -> bool:
    """Register the independently managed stale-session reaper watcher.

    The reaper is never run as a fallback from this lifecycle command.  Failure
    to reach Circus therefore leaves every tmux session untouched.
    """
    if _native_windows():
        _out.emit(Tag.DAEMON, "start", "stale-session-reaper: no-op on native Windows (not supported there)")
        return True

    state_dir = get_xdg_state_home()
    state_dir.mkdir(parents=True, exist_ok=True)
    try:
        # The thread lock covers concurrent callers in one process; the file lock
        # extends the same singleton decision across near-simultaneous CLI launches.
        with _STALE_SESSION_REAPER_START_LOCK:
            with portalocker.Lock(str(state_dir / "stale-session-reaper-start.lock"), mode="a", timeout=10):
                endpoint = _ensure_circusd()
                from circus.client import CircusClient

                ai_bin = shutil.which("ai") or "ai"
                client = CircusClient(endpoint=endpoint, timeout=5.0)
                status = client.send_message("status")
                statuses = status.get("statuses", {}) if isinstance(status, dict) else {}
                watcher = statuses.get(_STALE_SESSION_REAPER_WATCHER)
                active = (
                    bool(watcher.get("active"))
                    if isinstance(watcher, dict)
                    else watcher
                    in {
                        "running",
                        "active",
                    }
                )
                if not active:
                    if watcher is not None:
                        result = client.send_message("start", name=_STALE_SESSION_REAPER_WATCHER)
                    else:
                        result = client.send_message(
                            "add",
                            name=_STALE_SESSION_REAPER_WATCHER,
                            cmd=f"{ai_bin} session-reaper run",
                            options={
                                "copy_env": True,
                                "respawn": True,
                                "singleton": True,
                                "stdout_stream": {
                                    "class": "FileStream",
                                    "filename": str(state_dir / "stale-session-reaper.log"),
                                },
                                "stderr_stream": {
                                    "class": "FileStream",
                                    "filename": str(state_dir / "stale-session-reaper.log"),
                                },
                            },
                            start=True,
                        )
                    if isinstance(result, dict) and result.get("status") not in {None, "ok"}:
                        raise RuntimeError("Circus rejected watcher start")
    except Exception as exc:
        _out.emit(
            Tag.DAEMON,
            "start",
            f"stale-session-reaper: failed to start ({exc}); retry with `ai session-reaper start`",
            err=True,
        )
        return False
    _out.emit(Tag.DAEMON, "start", "stale-session-reaper: running")
    return True


def _start_stale_session_reaper_for_launch() -> None:
    """Schedule reaper startup without delaying or failing a Claude launch."""
    if _native_windows():
        return
    ai_bin = shutil.which("ai") or "ai"
    try:
        # Keep stderr attached so the child can emit the single actionable
        # warning from the lifecycle command if Circus startup fails.
        subprocess.Popen(
            [ai_bin, "session-reaper", "start"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        _out.emit(
            Tag.DAEMON,
            "start",
            f"stale-session-reaper: failed to schedule start ({exc}); retry with `ai session-reaper start`",
            err=True,
        )


def _cmd_stale_session_reaper_stop() -> bool:
    """Remove the stale-session reaper watcher without starting Circus."""
    state_dir = get_xdg_state_home()
    endpoint = f"ipc://{state_dir}/circus.endpoint"
    try:
        from circus.client import CircusClient

        result = CircusClient(endpoint=endpoint, timeout=2.0).send_message("rm", name=_STALE_SESSION_REAPER_WATCHER)
        if isinstance(result, dict) and result.get("status") not in {None, "ok"}:
            raise RuntimeError("Circus rejected watcher removal")
    except Exception as exc:
        _out.emit(Tag.DAEMON, "stop", f"stale-session-reaper: failed to stop ({exc})", err=True)
        return False
    _out.emit(Tag.DAEMON, "stop", "stale-session-reaper: stopped")
    return True


def _cmd_stale_session_reaper_status() -> bool:
    """Report whether the stale-session reaper watcher is running."""
    state_dir = get_xdg_state_home()
    endpoint = f"ipc://{state_dir}/circus.endpoint"
    try:
        from circus.client import CircusClient

        result = CircusClient(endpoint=endpoint, timeout=2.0).send_message("status")
        if not isinstance(result, dict) or not isinstance(result.get("statuses"), dict):
            raise RuntimeError("invalid Circus status response")
        statuses = result["statuses"]
        watcher = statuses.get(_STALE_SESSION_REAPER_WATCHER)
    except Exception as exc:
        _out.emit(Tag.DAEMON, "status", f"stale-session-reaper: failed to query status ({exc})", err=True)
        return False
    active = bool(watcher.get("active")) if isinstance(watcher, dict) else watcher in {"running", "active"}
    _out.emit(Tag.DAEMON, "status", f"stale-session-reaper: {'running' if active else 'not running'}")
    return True
