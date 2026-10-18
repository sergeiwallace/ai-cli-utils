"""Install a terminal-independent, one-shot boot relaunch service."""

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from . import session_run_state as state
from .session_publish_timer import quote_systemd, run_manager, write_unit

LABEL = "org.ai-cli-utils.session-relaunch"
UNIT = "ai-session-relaunch"


def _service_path(path: str, executable: str) -> str:
    """Keep durable tool directories and the installed entry point's directory."""
    executable_dir = Path(executable).parent
    seen = set()
    entries = []
    for entry in [*path.split(os.pathsep), str(executable_dir)]:
        directory = Path(entry)
        if not entry or directory in seen:
            continue
        if directory != executable_dir and (
            not directory.is_dir() or {".worktrees", ".direnv"}.intersection(directory.parts)
        ):
            continue
        seen.add(directory)
        entries.append(entry)
    return os.pathsep.join(entries)


def render(*, platform: str, home: Path, path: str, executable: str) -> dict[Path, bytes]:
    """Render the installed entry point and the configuration/state environment."""
    if platform != "darwin" and not platform.startswith("linux"):
        raise ValueError("boot units support macOS and Linux only")
    if not Path(executable).is_absolute():
        raise ValueError("boot units require an absolute ai entry point")
    environment = {"PATH": _service_path(path, executable), "HOME": str(home)}
    for key in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        value = os.environ.get(key, "")
        if value and Path(value).is_absolute():
            environment[key] = value
    if platform == "darwin":
        # LaunchAgents run at macOS login, independently of terminal applications.
        return {
            home / "Library/LaunchAgents" / f"{LABEL}.plist": plistlib.dumps(
                {
                    "Label": LABEL,
                    "ProgramArguments": [executable, "sessions", "relaunch", "--boot"],
                    "RunAtLoad": True,
                    # The one-shot parent must leave detached tmux children alive.
                    "AbandonProcessGroup": True,
                    "EnvironmentVariables": environment,
                }
            )
        }
    root = state.config.resolve_base_dir("XDG_CONFIG_HOME", home / ".config") / "systemd/user"
    env = " ".join(quote_systemd(f"{key}={value}") for key, value in environment.items())
    service = (
        "[Unit]\nDescription=Relaunch recorded sessions after boot\n"
        "[Service]\nType=oneshot\nTimeoutStartSec=infinity\n"
        # Keep the detached tmux server alive when the one-shot exits.
        "KillMode=process\n"
        f"Environment={env}\n"
        f"ExecStart={quote_systemd(executable).replace('$', '$$')} sessions relaunch --boot\n"
        "[Install]\nWantedBy=default.target\n"
    )
    return {root / f"{UNIT}.service": service.encode("utf-8")}


def manage(action: str, *, on_boot: bool | None = None, dry_run: bool = False, runner=None) -> str:
    """Converge desired unit state without starting a Linux session at install time."""
    runner = runner or subprocess.run
    if action not in ("install", "uninstall", "status"):
        raise ValueError("unknown boot-unit action")
    if sys.platform != "darwin" and not sys.platform.startswith("linux"):
        raise ValueError("boot units support macOS and Linux only")
    desired = (state.settings()["on_boot"] if on_boot is None else on_boot) if action == "install" else False
    executable = shutil.which("ai") or str(Path(sys.executable).with_name("ai"))
    executable = str(Path(executable).absolute())
    files = render(platform=sys.platform, home=Path.home(), path=os.environ.get("PATH", ""), executable=executable)
    target, content = next(iter(files.items()))
    previous = target.read_bytes() if target.exists() else None
    changed = previous != content
    mac = sys.platform == "darwin"
    domain = f"gui/{os.getuid()}" if mac else ""
    identity = f"{domain}/{LABEL}" if mac else f"{UNIT}.service"
    probe = ["launchctl", "print", identity] if mac else ["systemctl", "--user", "is-enabled", "--quiet", identity]
    disable = ["launchctl", "bootout", identity] if mac else ["systemctl", "--user", "disable", identity]
    enable = ["launchctl", "enable", identity] if mac else ["systemctl", "--user", "enable", identity]
    bootstrap = ["launchctl", "bootstrap", domain, str(target)]
    reload = ["systemctl", "--user", "daemon-reload"]
    if dry_run:
        commands = [enable, bootstrap] if desired and mac else [enable] if desired else [disable]
        operation = ("write" if changed else "unchanged") if desired else "remove if present"
        plan = [f"path: {target}", content.decode("utf-8"), f"{operation}: {target}"]
        if mac and desired and changed and previous is not None:
            plan.append(shlex.join(disable) + " (if loaded)")
        if not mac and ((desired and changed) or (not desired and previous is not None)):
            plan.append(shlex.join(reload))
        plan.append("enable if needed" if desired else "disable if enabled")
        plan.extend(shlex.join(argv) + " (if needed)" for argv in commands)
        return "\n".join([*plan, "dry-run: 0 mutations"])

    result = run_manager(probe, runner=runner, check=False)
    absent_codes = (1, 3, 113) if mac else (1, 4)
    if result.returncode and (
        result.returncode not in absent_codes or (not mac and result.returncode == 1 and result.stderr)
    ):
        raise RuntimeError("cannot determine boot-unit state from service manager")
    enabled = result.returncode == 0
    if action == "status":
        return f"{target}: " + ("installed and enabled" if not changed and enabled else "absent, disabled or outdated")
    mutations = 0

    def run(argv):
        nonlocal mutations
        run_manager(argv, runner=runner)
        mutations += 1

    if desired:
        if not Path(executable).is_file():
            raise ValueError("installed ai entry point was not found; install ai before enabling boot relaunch")
        if mac and enabled and changed:
            run(disable)
            enabled = False
        if changed:
            write_unit(target, content)
            mutations += 1
            if not mac:
                run(reload)
        if not enabled:
            run(enable)
            if mac:
                run(bootstrap)
        return f"{target}: installed and enabled; {mutations} mutations"
    if enabled or (not mac and previous is not None):
        run(disable)
    if previous is not None:
        target.unlink()
        mutations += 1
        if not mac:
            run(reload)
    return f"{target}: disabled and absent; {mutations} mutations"
