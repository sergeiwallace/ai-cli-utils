"""Resolve a fresh launch host from a login shell, then the tmux server."""

from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
import sys

from . import tmux_binary
from .config import RELAUNCH_DEFAULTS, read_relaunch_config

# Runs the login shell ("$@") and kills its whole process group as soon as the caller is gone.
# Its stdin is a pipe the caller holds open and never writes, so the background read returns
# only when the caller exits for any reason, SIGKILL included, which no cleanup code survives.
_LOGIN_PROBE_WATCHDOG = (
    "exec 3<&0 </dev/null; "
    "(read -r _ <&3; kill -KILL 0) >/dev/null 2>&1 & monitor=$!; "
    '"$@" 3<&-; code=$?; '
    'kill -KILL "$monitor" 2>/dev/null; exit "$code"'
)


def _run_login_probe(command: list[str], env: dict, timeout: float) -> tuple[int, str]:
    """Run the login-shell probe so that neither it nor anything it starts outlives the call.

    An interactive shell spawned from a terminal takes part in that terminal's job control;
    orphaned when its caller died mid-wait, one spun at full CPU for over a day. Here the
    probe runs in a new session with no controlling terminal, under a watchdog that kills
    the session's process group as soon as the caller exits; on timeout the caller kills it.
    """
    if sys.platform == "win32":
        # No process groups or POSIX signals; a login shell here is a plain bounded child.
        completed = subprocess.run(
            command,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return completed.returncode, completed.stdout
    caller_gone, caller_alive = os.pipe()
    try:
        try:
            process = subprocess.Popen(
                ["/bin/sh", "-c", _LOGIN_PROBE_WATCHDOG, "sh", *command],
                env=env,
                stdin=caller_gone,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                errors="replace",
                start_new_session=True,
            )
        finally:
            os.close(caller_gone)
        with process:
            try:
                stdout, _ = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                # The watchdog leads the session's group and stays unreaped until the with-block
                # exits, so its pid still names the group.
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                raise
        return process.returncode, stdout
    finally:
        os.close(caller_alive)


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
        # -i keeps the startup files an interactive terminal reads, where a profile may set AI_HOST.
        probes.append(
            ("login-shell", [shell, "-lic", 'printf "AI_HOST_FRESH=%s\\n" "${AI_HOST:-}"'], "AI_HOST_FRESH=", login_env)
        )
    # A configured multiplexer that is missing has no server to ask, and PATH's tmux is
    # never asked in its place.
    with contextlib.suppress(tmux_binary.TmuxBinaryMissing):
        probes.append(("tmux", tmux_binary.argv("show-environment", "-g", "AI_HOST"), "AI_HOST=", None))
    for source, command, prefix, probe_env in probes:
        try:
            if source == "login-shell":
                returncode, stdout = _run_login_probe(command, probe_env, timeout)
            else:
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
                returncode, stdout = completed.returncode, completed.stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        if returncode != 0:
            continue
        lines = [line[len(prefix) :] for line in stdout.splitlines() if line.startswith(prefix)]
        if lines and re.fullmatch(r"[A-Za-z0-9._-]+", lines[-1]):
            return source, lines[-1]
    inherited = os.environ.get("AI_HOST", "")
    # Preserve unusual inherited values in the caller without breaking the one-line reply.
    return "inherited", inherited if re.fullmatch(r"[A-Za-z0-9._-]+", inherited) else ""


def refresh_host_env(config: dict) -> None:
    """Install a fresh identity before naming; failed probes preserve inherited AI_HOST."""
    source, host = resolve_host_env(config)
    if source in ("login-shell", "tmux"):
        os.environ["AI_HOST"] = host
