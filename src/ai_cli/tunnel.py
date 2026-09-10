"""SSH tunnel (autossh) and CDP (Chrome DevTools Protocol) browser management.

Depends on: config.py, transport.py.
"""

import json
import math
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

import psutil

from .config import _pid_alive, get_remote_machine, get_xdg_data_home, get_xdg_state_home
from .transport import _is_vpn_active

_PROCESS_STATE_VERSION = 1


@dataclass(frozen=True)
class _ManagedProcessIdentity:
    """Durable identity for one process created by this tool."""

    version: int
    pid: int
    create_time: float
    executable: str
    command: tuple[str, ...]
    port: int


def _capture_process_identity(pid: int, port: int) -> _ManagedProcessIdentity | None:
    try:
        process = psutil.Process(pid)
        create_time = process.create_time()
        executable = process.exe()
        command = tuple(process.cmdline())
    except (OSError, psutil.Error):
        return None
    if (
        pid <= 0
        or port <= 0
        or not isinstance(create_time, (int, float))
        or isinstance(create_time, bool)
        or not math.isfinite(create_time)
        or not executable
        or not command
        or not all(isinstance(part, str) for part in command)
    ):
        return None
    return _ManagedProcessIdentity(_PROCESS_STATE_VERSION, pid, create_time, executable, command, port)


def _write_process_identity(path: Path, pid: int, port: int) -> bool:
    identity = _capture_process_identity(pid, port)
    if identity is None:
        return False
    payload = asdict(identity)
    payload["command"] = list(identity.command)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        temporary.write_text(json.dumps(payload, sort_keys=True))
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        return False
    return True


def _read_process_identity(path: Path, port: int) -> _ManagedProcessIdentity | None:
    try:
        payload = json.loads(path.read_text())
        command = payload["command"]
        if not isinstance(command, list):
            return None
        identity = _ManagedProcessIdentity(
            version=payload["version"],
            pid=payload["pid"],
            create_time=payload["create_time"],
            executable=payload["executable"],
            command=tuple(command),
            port=payload["port"],
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(identity.version, int)
        or isinstance(identity.version, bool)
        or identity.version != _PROCESS_STATE_VERSION
        or not isinstance(identity.pid, int)
        or isinstance(identity.pid, bool)
        or identity.pid <= 0
        or not isinstance(identity.create_time, (int, float))
        or isinstance(identity.create_time, bool)
        or not math.isfinite(identity.create_time)
        or not isinstance(identity.executable, str)
        or not identity.executable
        or not identity.command
        or not all(isinstance(part, str) for part in identity.command)
        or not isinstance(identity.port, int)
        or isinstance(identity.port, bool)
        or identity.port != port
    ):
        return None
    return identity


def _matching_process(identity: _ManagedProcessIdentity) -> psutil.Process | None:
    """Revalidate every durable identity field immediately before use."""
    try:
        process = psutil.Process(identity.pid)
        if (
            process.create_time() != identity.create_time
            or process.exe() != identity.executable
            or tuple(process.cmdline()) != identity.command
        ):
            return None
    except (OSError, psutil.Error):
        return None
    return process


def _registered_process(path: Path, port: int) -> tuple[_ManagedProcessIdentity, psutil.Process] | None:
    identity = _read_process_identity(path, port)
    if identity is None:
        return None
    process = _matching_process(identity)
    return (identity, process) if process is not None else None


def _cmd_tunnel_start(
    local_port: int, remote_port: int, *, forward: bool = True, config: dict, quiet: bool = False
) -> None:
    state_dir = get_xdg_state_home()
    pid_file = state_dir / f"tunnel-{local_port}.pid"
    if pid_file.exists():
        registered = _registered_process(pid_file, local_port)
        if registered is not None:
            if not quiet:
                print(f"Tunnel already running: localhost:{local_port} (PID {registered[0].pid})")
            return
        pid_file.unlink(missing_ok=True)

    autossh_bin = shutil.which("autossh")
    if not autossh_bin:
        print(
            "autossh not found. Install it first:\n  macOS:  brew install autossh\n  Linux:  apt install autossh",
            file=sys.stderr,
        )
        sys.exit(1)

    remote_cfg = get_remote_machine(config)
    host = remote_cfg.get("host", "")
    user = remote_cfg.get("user", "ubuntu")
    if not host:
        print("Error: [remote] host not set in ~/.config/ai-cli-utils/config.toml", file=sys.stderr)
        sys.exit(1)
    # Use vpn_host when VPN is active — Tailscale becomes unreachable under VPN.
    vpn_host = remote_cfg.get("vpn_host", "") or host
    if vpn_host != host and _is_vpn_active():
        host = vpn_host

    direction = "-L" if forward else "-R"
    cmd = [
        autossh_bin,
        "-M",
        "0",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "ExitOnForwardFailure=yes",
        "-N",
        direction,
        f"{remote_port}:localhost:{local_port}",
        f"{user}@{host}",
    ]
    proc = subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    state_dir.mkdir(parents=True, exist_ok=True)
    if not _write_process_identity(pid_file, proc.pid, local_port):
        proc.terminate()
        print("Error: could not capture tunnel process identity; tunnel was stopped", file=sys.stderr)
        sys.exit(1)
    if not quiet:
        print(f"Tunnel started: localhost:{local_port} -> {host}:{remote_port} (PID {proc.pid})")


def _ensure_nats_tunnel(config: dict) -> None:
    """Auto-start NATS tunnel if [messaging] tunnel_port is configured and tunnel isn't running."""
    tunnel_port = config.get("messaging", {}).get("tunnel_port")
    if not tunnel_port:
        return
    port = int(tunnel_port)
    # Check if already running before starting — no sleep needed if already up
    state_dir = get_xdg_state_home()
    pid_file = state_dir / f"tunnel-{port}.pid"
    already_running = False
    if pid_file.exists():
        already_running = _registered_process(pid_file, port) is not None
    try:
        _cmd_tunnel_start(port, port, forward=True, config=config, quiet=True)
    except SystemExit:
        return  # missing autossh or remote config — skip silently
    if not already_running:
        # Give SSH time to establish before dependent services try to connect.
        time.sleep(3)


def _cmd_tunnel_stop(local_port: int) -> None:
    state_dir = get_xdg_state_home()
    pid_file = state_dir / f"tunnel-{local_port}.pid"
    if not pid_file.exists():
        return
    identity = _read_process_identity(pid_file, local_port)
    pid_file.unlink(missing_ok=True)
    process = _matching_process(identity) if identity is not None else None
    if process is None:
        print(f"Removed stale tunnel record: port {local_port}; no process was stopped")
        return
    process.terminate()
    print(f"Tunnel stopped: port {local_port}")


def _cmd_tunnel_status() -> None:
    state_dir = get_xdg_state_home()
    pid_files = sorted(state_dir.glob("tunnel-*.pid"))
    if not pid_files:
        print("No tunnels registered.")
        return
    for pid_file in pid_files:
        port = pid_file.stem[len("tunnel-") :]
        registered = _registered_process(pid_file, int(port))
        if registered is None:
            pid_file.unlink(missing_ok=True)
            print(f"port {port}: dead (stale record removed)")
            continue
        print(f"port {port}: PID {registered[0].pid} (alive)")


# --- CDP (Chrome DevTools Protocol) browser management ---


def _find_chrome_binary(config: dict) -> str | None:
    """Return path to Chrome/Chromium binary, or None if not found."""
    configured = config.get("cdp", {}).get("binary_path", "")
    if configured:
        return str(configured) if Path(configured).exists() else None

    if sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif sys.platform == "win32":
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    else:
        candidates = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]

    for c in candidates:
        found = shutil.which(c)
        if found:
            return found
        if Path(c).exists():
            return c
    return None


def _find_chrome_pid_by_port(port: int) -> int | None:
    """Find a Chrome/Chromium process PID by its --remote-debugging-port argument."""
    flag = f"--remote-debugging-port={port}"
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            if proc.info["cmdline"] and flag in proc.info["cmdline"]:
                return proc.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return None


def _clear_stale_singleton_lock(user_data_dir: Path) -> bool:
    """Remove Chrome Singleton* lock artifacts if their owning PID is dead.

    Chrome's SingletonLock is a symlink named ``<host>-<pid>``. A lock left
    behind by a crashed or killed instance makes every new Chrome exit on
    startup, so the CDP port never opens. Only clears the lock when the PID is
    dead — a live instance keeps its lock untouched. Returns True if cleared.
    """
    lock = user_data_dir / "SingletonLock"
    if not lock.is_symlink():
        return False
    try:
        pid = int(str(lock.readlink()).rsplit("-", 1)[-1])
    except (OSError, ValueError):
        return False
    if _pid_alive(pid):
        return False
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        (user_data_dir / name).unlink(missing_ok=True)
    return True


def _port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """True if something is already listening on ``host:port``."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def _next_free_port(start: int, limit: int = 20) -> int | None:
    """First free port in ``[start, start+limit)``, or ``None`` if all are taken."""
    for candidate in range(start, start + limit):
        if not _port_in_use(candidate):
            return candidate
    return None


def _cmd_cdp_start(port: int, incognito: bool, config: dict, tunnel: bool = False, forward: bool = False) -> None:
    state_dir = get_xdg_state_home()
    pid_file = state_dir / f"cdp-{port}.pid"

    if pid_file.exists():
        registered = _registered_process(pid_file, port)
        if registered is not None:
            print(f"CDP already running on port {port} (PID {registered[0].pid})")
            return
        pid_file.unlink(missing_ok=True)

    # If the requested port is held by a process we don't track (a foreign Chrome or
    # any other app, or an untracked instance), don't fight it — auto-increment to
    # the next free port so a clean automation Chrome can still come up. Our own
    # tracked instance was already handled (and reused) by the pid_file check above.
    if _port_in_use(port):
        free_port = _next_free_port(port + 1)
        if free_port is None:
            print(
                f"Port {port} is in use and no free port was found in "
                f"{port + 1}-{port + 20}. Stop the holder or pass a different --port.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"Port {port} is in use by another process — starting CDP on {free_port} instead.")
        port = free_port
        pid_file = state_dir / f"cdp-{port}.pid"

    chrome = _find_chrome_binary(config)
    if not chrome:
        print(
            "Chrome/Chromium not found. Install it or set [cdp] binary_path in config.",
            file=sys.stderr,
        )
        sys.exit(1)

    cdp_cfg = config.get("cdp", {})
    if "profile_dir" in cdp_cfg:
        user_data_dir = Path(cdp_cfg["profile_dir"]).expanduser()
    else:
        user_data_dir = get_xdg_data_home() / "chrome-profiles" / "automation"
    user_data_dir.mkdir(parents=True, exist_ok=True)
    _clear_stale_singleton_lock(user_data_dir)

    chrome_args = [
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-default-apps",
    ]
    if incognito:
        chrome_args.append("--incognito")

    state_dir.mkdir(parents=True, exist_ok=True)
    pid: int | None = None

    if sys.platform == "darwin":
        # On macOS, launching the binary directly trampolines into the existing
        # Chrome process (Chrome's process model reuses its running instance),
        # so the CDP port never opens. Use `open -na` to force a new app instance.
        _app_dir = next((p for p in Path(chrome).parts if p.endswith(".app")), None)
        _app_name = _app_dir[:-4] if _app_dir else "Google Chrome"
        subprocess.run(["open", "-na", _app_name, "--args", *chrome_args], check=False)
    else:
        proc = subprocess.Popen(
            [chrome, *chrome_args],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        pid = proc.pid
        if not _write_process_identity(pid_file, pid, port):
            proc.terminate()
            print("Error: could not capture CDP process identity; browser was stopped", file=sys.stderr)
            sys.exit(1)

    url = f"http://localhost:{port}/json/version"
    deadline = time.monotonic() + 5.0
    ready = False
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=0.5)
            ready = True
            break
        except Exception:
            time.sleep(0.25)

    if sys.platform == "darwin":
        pid = _find_chrome_pid_by_port(port)
        if pid is not None:
            _write_process_identity(pid_file, pid, port)

    if ready:
        print(f"CDP ready at localhost:{port}")
    else:
        suffix = f" (PID {pid})" if pid is not None else ""
        print(f"CDP started{suffix} — endpoint not yet responding on port {port}")

    if tunnel:
        _cmd_tunnel_start(port, port, forward=forward, config=config)


def _cmd_cdp_stop(port: int, tunnel: bool = False) -> None:
    state_dir = get_xdg_state_home()
    pid_file = state_dir / f"cdp-{port}.pid"
    if not pid_file.exists():
        print(f"No CDP process registered on port {port}.")
        return
    identity = _read_process_identity(pid_file, port)
    pid_file.unlink(missing_ok=True)
    process = _matching_process(identity) if identity is not None else None
    if process is None:
        print(f"Removed stale CDP record: port {port}; no process was stopped")
    else:
        process.terminate()
        print(f"CDP stopped: port {port}")
    if tunnel:
        _cmd_tunnel_stop(port)


def _cmd_cdp_status() -> None:
    state_dir = get_xdg_state_home()
    pid_files = sorted(state_dir.glob("cdp-*.pid"))
    if not pid_files:
        print("No CDP processes registered.")
        return
    for pid_file in pid_files:
        port = pid_file.stem[len("cdp-") :]
        registered = _registered_process(pid_file, int(port))
        if registered is None:
            pid_file.unlink(missing_ok=True)
            print(f"port {port}: stale record")
            continue
        print(f"port {port}: PID {registered[0].pid} (alive)")
