"""SSH tunnel (autossh) and CDP (Chrome DevTools Protocol) browser management.

Depends on: config.py, transport.py.
"""

import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import psutil

from .config import _pid_alive, get_xdg_data_home, get_xdg_state_home
from .transport import _is_vpn_active


def _cmd_tunnel_start(
    local_port: int, remote_port: int, *, forward: bool = True, config: dict, quiet: bool = False
) -> None:
    state_dir = get_xdg_state_home()
    pid_file = state_dir / f"tunnel-{local_port}.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if _pid_alive(pid):
                if not quiet:
                    print(f"Tunnel already running: localhost:{local_port} (PID {pid})")
                return
        except ValueError:
            pass
        pid_file.unlink(missing_ok=True)

    autossh_bin = shutil.which("autossh")
    if not autossh_bin:
        print(
            "autossh not found. Install it first:\n  macOS:  brew install autossh\n  Linux:  apt install autossh",
            file=sys.stderr,
        )
        sys.exit(1)

    remote_cfg = config.get("remote", {})
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
    pid_file.write_text(str(proc.pid))
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
        try:
            pid = int(pid_file.read_text().strip())
            already_running = _pid_alive(pid)
        except ValueError:
            pass
    try:
        _cmd_tunnel_start(port, port, forward=True, config=config, quiet=True)
    except SystemExit:
        return  # missing autossh or remote config — skip silently
    if not already_running:
        # Give SSH time to establish before handoff-drain tries to connect
        time.sleep(3)


def _cmd_tunnel_stop(local_port: int) -> None:
    state_dir = get_xdg_state_home()
    pid_file = state_dir / f"tunnel-{local_port}.pid"
    if not pid_file.exists():
        return
    pid = int(pid_file.read_text().strip())
    try:
        psutil.Process(pid).terminate()
    except psutil.NoSuchProcess:
        pass
    pid_file.unlink(missing_ok=True)
    print(f"Tunnel stopped: port {local_port}")


def _cmd_tunnel_status() -> None:
    state_dir = get_xdg_state_home()
    pid_files = sorted(state_dir.glob("tunnel-*.pid"))
    if not pid_files:
        print("No tunnels registered.")
        return
    for pid_file in pid_files:
        port = pid_file.stem[len("tunnel-") :]
        pid = int(pid_file.read_text().strip())
        if _pid_alive(pid):
            status = "alive"
        else:
            status = "dead"
            pid_file.unlink(missing_ok=True)
        print(f"port {port}: PID {pid} ({status})")


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


def _cmd_cdp_start(port: int, incognito: bool, config: dict, tunnel: bool = False, forward: bool = False) -> None:
    state_dir = get_xdg_state_home()
    pid_file = state_dir / f"cdp-{port}.pid"

    if pid_file.exists():
        try:
            existing_pid = int(pid_file.read_text().strip())
            if _pid_alive(existing_pid):
                print(f"CDP already running on port {port} (PID {existing_pid})")
                return
        except ValueError:
            pass
        pid_file.unlink(missing_ok=True)

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
        subprocess.run(["open", "-na", _app_name, "--args"] + chrome_args, check=False)
    else:
        proc = subprocess.Popen(
            [chrome] + chrome_args,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        pid = proc.pid
        pid_file.write_text(str(pid))

    url = f"http://localhost:{port}/json/version"
    deadline = time.monotonic() + 5.0
    ready = False
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=0.5)  # noqa: S310
            ready = True
            break
        except Exception:
            time.sleep(0.25)

    if sys.platform == "darwin":
        pid = _find_chrome_pid_by_port(port)
        if pid is not None:
            pid_file.write_text(str(pid))

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
    pid = int(pid_file.read_text().strip())
    try:
        psutil.Process(pid).terminate()
    except psutil.NoSuchProcess:
        pass
    pid_file.unlink(missing_ok=True)
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
        pid = int(pid_file.read_text().strip())
        if _pid_alive(pid):
            status = "alive"
        else:
            status = "dead"
            pid_file.unlink(missing_ok=True)
        print(f"port {port}: PID {pid} ({status})")
