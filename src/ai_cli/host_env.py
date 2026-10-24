"""Resolve a fresh launch host from a login shell, then the tmux server."""

from __future__ import annotations

import os
import re
import subprocess

from .config import RELAUNCH_DEFAULTS, read_relaunch_config


def resolve_host_env(config: dict) -> tuple[str, str]:
    """Return the source and host, keeping the inherited value if both probes fail.

    Only AI_HOST is requested. Startup banners may surround the login shell's
    sentinel; the last sentinel line is authoritative and must contain a valid host.
    Both probes are bounded using the configuration supplied for this call.
    """
    try:
        timeout = read_relaunch_config(config)["host_refresh_timeout_seconds"]
    except ValueError:
        timeout = RELAUNCH_DEFAULTS["host_refresh_timeout_seconds"]
    shell = os.environ.get("SHELL", "")
    # The login shell must not hand back the stale value it inherited: without AI_HOST in its
    # environment, only a profile that sets it can answer, and otherwise the tmux probe runs.
    login_env = {name: value for name, value in os.environ.items() if name != "AI_HOST"}
    probes = []
    if shell:
        probes.append(
            ("login-shell", [shell, "-lic", 'printf "AI_HOST_FRESH=%s\\n" "${AI_HOST:-}"'], "AI_HOST_FRESH=", login_env)
        )
    probes.append(("tmux", ["tmux", "show-environment", "-g", "AI_HOST"], "AI_HOST=", None))
    for source, command, prefix, probe_env in probes:
        try:
            completed = subprocess.run(
                command,
                env=probe_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if completed.returncode != 0:
            continue
        lines = [line[len(prefix) :] for line in completed.stdout.splitlines() if line.startswith(prefix)]
        if lines and re.fullmatch(r"[A-Za-z0-9._-]+", lines[-1]):
            return source, lines[-1]
    inherited = os.environ.get("AI_HOST", "")
    # Preserve unusual inherited values in the caller without breaking the one-line reply.
    return "inherited", inherited if re.fullmatch(r"[A-Za-z0-9._-]+", inherited) else ""
