"""Install the durable publication worker independently of boot relaunch policy."""

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from . import session_run_state as state

LABEL = "org.ai-cli-utils.session-publish"
UNIT = "ai-session-publish"


def render(cfg: dict, *, platform: str, home: Path, path: str, executable: str) -> dict[Path, bytes]:
    interval = max(0.1, float(cfg["debounce_seconds"]) / 2)
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
        root = Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config"))) / "systemd/user"

        def quote(value):
            return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("\n", "\\n") + '"'

        service = (
            f"[Unit]\nDescription=Publish session run state\n[Service]\nType=oneshot\n"
            f"Environment={quote('PATH=' + path)} {quote('HOME=' + str(home))}\n"
            f"ExecStart={quote(executable)} sessions publish --tick\n"
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
        if action == "status":
            return "installed" if installed else "absent or outdated"
        if action == "install" and not cfg["repo"]:
            action = "uninstall"
        if action == "uninstall" and not any(path.exists() for path in files):
            return "absent"

        def run(argv):
            result = runner(argv, capture_output=True, text=True, check=False, timeout=10)
            if result.returncode:
                raise RuntimeError("service manager refused publication timer operation")

        domain = f"gui/{os.getuid()}" if sys.platform == "darwin" else ""
        if action == "install":
            if installed:
                return "installed"
            if sys.platform == "darwin" and any(path.exists() for path in files):
                run(["launchctl", "bootout", f"{domain}/{LABEL}"])
            for path, content in files.items():
                if path.exists() and path.read_bytes() == content:
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            if sys.platform == "darwin":
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
