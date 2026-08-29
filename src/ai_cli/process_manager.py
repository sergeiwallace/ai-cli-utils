"""Circus daemon management for persistent utility processes.

Depends on: config.py.
"""

import contextlib
import shutil
import subprocess
import sys
from pathlib import Path

from .config import _pid_alive, get_xdg_state_home

_STALE_SESSION_REAPER_WATCHER = "stale-session-reaper"


def _ensure_circusd() -> str:
    """Start circusd if not already running. Returns the endpoint URI."""
    import shutil as _shutil
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

    circusd_bin = _shutil.which("circusd") or str(Path.home() / ".local" / "bin" / "circusd")
    pidfile = str(state_dir / "circusd.pid")
    subprocess.Popen(
        [circusd_bin, "--daemon", "--pidfile", pidfile, str(ini_path)],
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


def _cmd_quota_watch_start(auto: bool = False) -> None:
    """Register quota-watch as a Circus watcher and start it.

    ``auto=True`` is the per-session-launch auto-start path (session_script.py);
    it is gated on ``[quota_watch] auto_start`` in config.toml (default off — the
    CC statusline already surfaces weekly usage, so unattended ntfy/discord alerts
    are opt-in). A bare, explicitly-typed ``ai quota watch start`` (``auto=False``)
    always registers regardless of the config flag — explicit intent is honored.
    """
    if auto:
        from .config import load_config

        if not load_config().get("quota_watch", {}).get("auto_start", False):
            return

    endpoint = _ensure_circusd()
    from circus.client import CircusClient

    state_dir = get_xdg_state_home()
    ai_bin = shutil.which("ai") or "ai"
    cmd = f"{ai_bin} quota watch run"
    log_path = str(state_dir / "quota-watch.log")

    client = CircusClient(endpoint=endpoint, timeout=5.0)
    with contextlib.suppress(Exception):
        client.send_message("rm", name="quota-watch")

    client.send_message(
        "add",
        name="quota-watch",
        cmd=cmd,
        options={
            "copy_env": True,
            "respawn": True,
            "singleton": True,
            "stdout_stream": {"class": "FileStream", "filename": log_path},
            "stderr_stream": {"class": "FileStream", "filename": log_path},
        },
        start=True,
    )


def _cmd_quota_watch_stop() -> None:
    """Remove quota-watch watcher from Circus."""
    state_dir = get_xdg_state_home()
    endpoint = f"ipc://{state_dir}/circus.endpoint"
    try:
        from circus.client import CircusClient

        CircusClient(endpoint=endpoint, timeout=2.0).send_message("rm", name="quota-watch")
    except Exception:
        pass


def _cmd_quota_watch_status() -> None:
    """Print quota-watch process status from Circus."""
    state_dir = get_xdg_state_home()
    endpoint = f"ipc://{state_dir}/circus.endpoint"
    try:
        from circus.client import CircusClient

        result = CircusClient(endpoint=endpoint, timeout=2.0).send_message("status")
        statuses = result.get("statuses", {}) if isinstance(result, dict) else {}
        qw_status = statuses.get("quota-watch", "not registered")
        print(f"quota-watch: {qw_status}")
    except Exception:
        print("circusd not running.")


def _cmd_stale_session_reaper_start() -> bool:
    """Register the independently managed stale-session reaper watcher.

    The reaper is never run as a fallback from this lifecycle command.  Failure
    to reach Circus therefore leaves every tmux session untouched.
    """
    try:
        endpoint = _ensure_circusd()
        from circus.client import CircusClient

        state_dir = get_xdg_state_home()
        ai_bin = shutil.which("ai") or "ai"
        client = CircusClient(endpoint=endpoint, timeout=5.0)
        with contextlib.suppress(Exception):
            client.send_message("rm", name=_STALE_SESSION_REAPER_WATCHER)
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
            raise RuntimeError("Circus rejected watcher registration")
    except Exception as exc:
        print(f"stale-session-reaper: failed to start ({exc})", file=sys.stderr)
        return False
    print("stale-session-reaper: running")
    return True


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
        print(f"stale-session-reaper: failed to stop ({exc})", file=sys.stderr)
        return False
    print("stale-session-reaper: stopped")
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
        print(f"stale-session-reaper: failed to query status ({exc})", file=sys.stderr)
        return False
    active = bool(watcher.get("active")) if isinstance(watcher, dict) else watcher in {"running", "active"}
    print(f"stale-session-reaper: {'running' if active else 'not running'}")
    return True
