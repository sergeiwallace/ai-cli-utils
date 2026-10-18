"""Install the durable publication worker independently of boot relaunch policy."""

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import session_run_state as state

LABEL = "org.ai-cli-utils.session-publish"
UNIT = "ai-session-publish"


def quote_systemd(value: str) -> str:
    """Quote one systemd value, including literal specifier characters."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("\n", "\\n") + '"'


def write_unit(path: Path, content: bytes) -> None:
    """Read the previous unit, then atomically replace it only when bytes differ."""
    if path.exists() and path.read_bytes() == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.read_bytes()
            temporary.unlink()


def run_manager(argv: list[str], *, runner=None, check: bool = True):
    """Keep service-manager execution injectable and diagnostics out of exceptions."""
    result = (runner or subprocess.run)(argv, capture_output=True, text=True, check=False, timeout=10)
    if check and result.returncode:
        raise RuntimeError("service manager refused unit operation")
    return result


def render(cfg: dict, *, platform: str, home: Path, path: str, executable: str) -> dict[Path, bytes]:
    interval = float(cfg["debounce_seconds"]) / 2
    if interval <= 0 or (platform == "darwin" and interval < 1):
        raise ValueError("debounce interval is too short for the publication timer")
    if platform == "darwin":
        # LaunchAgents are a macOS-specific login service.
        return {
            home / "Library/LaunchAgents" / f"{LABEL}.plist": plistlib.dumps(
                {
                    "Label": LABEL,
                    "ProgramArguments": [executable, "sessions", "publish", "--tick"],
                    "RunAtLoad": True,
                    "StartInterval": max(1, int(interval)),
                    "EnvironmentVariables": {"PATH": path, "HOME": str(home)},
                }
            )
        }
    if platform.startswith("linux"):
        root = state.config.resolve_base_dir("XDG_CONFIG_HOME", home / ".config") / "systemd/user"

        service = (
            f"[Unit]\nDescription=Publish session run state\n[Service]\nType=oneshot\n"
            f"Environment={quote_systemd('PATH=' + path)} {quote_systemd('HOME=' + str(home))}\n"
            f"ExecStart={quote_systemd(executable)} sessions publish --tick\n"
        )
        timer = (
            f"[Unit]\nDescription=Publish dirty session state periodically\n[Timer]\n"
            f"OnStartupSec=0\nOnUnitActiveSec={interval:g}s\nAccuracySec=1ms\nRandomizedDelaySec=0\n"
            f"Unit={UNIT}.service\n[Install]\nWantedBy=timers.target\n"
        )
        return {root / f"{UNIT}.service": service.encode(), root / f"{UNIT}.timer": timer.encode()}
    raise ValueError("publication timers support macOS and Linux")


def manage(action: str, *, runner=None) -> str:
    """Idempotently manage units. The runner is injectable so tests never call host managers."""
    runner = runner or subprocess.run
    try:
        cfg = state.settings()["publish"]
        home = Path.home()
        files = render(
            cfg,
            platform=sys.platform,
            home=home,
            path=os.environ.get("PATH", ""),
            executable=shutil.which("ai") or str(Path(sys.executable).with_name("ai")),
        )
        installed = all(path.is_file() and path.read_bytes() == content for path, content in files.items())
        if action == "install" and not cfg["repo"]:
            action = "uninstall"
        if action == "uninstall" and not any(path.exists() for path in files):
            return "absent"

        def run(argv):
            run_manager(argv, runner=runner)

        domain = f"gui/{os.getuid()}" if sys.platform == "darwin" else ""

        def active():
            commands = (
                [["launchctl", "print", f"{domain}/{LABEL}"]]
                if sys.platform == "darwin"
                else [
                    ["systemctl", "--user", "is-enabled", "--quiet", f"{UNIT}.timer"],
                    ["systemctl", "--user", "is-active", "--quiet", f"{UNIT}.timer"],
                ]
            )
            return all(
                runner(argv, capture_output=True, text=True, check=False, timeout=10).returncode == 0
                for argv in commands
            )

        if action == "status":
            return "installed" if installed and active() else "absent, disabled or outdated"
        if action == "install":
            loaded = active() if sys.platform == "darwin" else False
            if sys.platform == "darwin" and not installed and loaded:
                run(["launchctl", "bootout", f"{domain}/{LABEL}"])
            for path, content in files.items():
                if path.exists() and path.read_bytes() == content:
                    continue
                write_unit(path, content)
            if sys.platform == "darwin":
                if not installed or not loaded:
                    run(["launchctl", "bootstrap", domain, str(next(iter(files)))])
            else:
                run(["systemctl", "--user", "daemon-reload"])
                run(["systemctl", "--user", "enable", "--now", f"{UNIT}.timer"])
            return "installed"
        if action == "uninstall":
            if sys.platform == "darwin":
                run(["launchctl", "bootout", f"{domain}/{LABEL}"])
            else:
                run(["systemctl", "--user", "disable", "--now", f"{UNIT}.timer"])
            for path in files:
                if path.exists():
                    path.read_bytes()
                    path.unlink()
            if sys.platform != "darwin":
                run(["systemctl", "--user", "daemon-reload"])
            return "uninstalled"
        raise ValueError("unknown timer action")
    except Exception as exc:
        state.LOGGER.error("publication timer failed (%s)", type(exc).__name__)
        return f"publication timer failed ({type(exc).__name__})"
