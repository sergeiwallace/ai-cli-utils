"""Circus daemon management and signal-watch process lifecycle.

Depends on: config.py.
"""

import shutil
import subprocess
from pathlib import Path


from .config import _pid_alive, get_xdg_state_home


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


def _cmd_signal_watch_start(project: str, session: str) -> None:
    endpoint = _ensure_circusd()
    from circus.client import CircusClient

    client = CircusClient(endpoint=endpoint, timeout=5.0)
    watcher_name = f"sw-{session}"
    ai_bin = shutil.which("ai") or "ai"
    cmd = f"{ai_bin} internal signal-watch {project} {session}"

    # Remove existing watcher idempotently
    try:
        client.send_message("rm", name=watcher_name)
    except Exception:
        pass

    client.send_message(
        "add",
        name=watcher_name,
        cmd=cmd,
        options={
            "copy_env": True,
            "respawn": False,
            "singleton": True,
        },
        start=True,
    )


def _cmd_signal_watch_stop(session: str) -> None:
    state_dir = get_xdg_state_home()
    endpoint = f"ipc://{state_dir}/circus.endpoint"
    try:
        from circus.client import CircusClient

        CircusClient(endpoint=endpoint, timeout=2.0).send_message("rm", name=f"sw-{session}")
    except Exception:
        pass


def _cmd_signal_watch_status() -> None:
    state_dir = get_xdg_state_home()
    endpoint = f"ipc://{state_dir}/circus.endpoint"
    try:
        from circus.client import CircusClient

        result = CircusClient(endpoint=endpoint, timeout=2.0).send_message("status")
        statuses = result.get("statuses", {}) if isinstance(result, dict) else {}
        sw_watchers = {k: v for k, v in statuses.items() if k.startswith("sw-")}
        if not sw_watchers:
            print("No signal-watch processes running.")
            return
        for name, status in sorted(sw_watchers.items()):
            session = name[len("sw-") :]
            print(f"{session}: {status}")
    except Exception:
        print("circusd not running.")


def _cmd_quota_watch_start() -> None:
    """Register quota-watch as a Circus watcher and start it."""
    endpoint = _ensure_circusd()
    from circus.client import CircusClient

    state_dir = get_xdg_state_home()
    ai_bin = shutil.which("ai") or "ai"
    cmd = f"{ai_bin} quota watch run"
    log_path = str(state_dir / "quota-watch.log")

    client = CircusClient(endpoint=endpoint, timeout=5.0)
    try:
        client.send_message("rm", name="quota-watch")
    except Exception:
        pass

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
