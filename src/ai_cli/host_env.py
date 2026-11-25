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

# Runs the login shell ("$@") and, as soon as the caller is gone, kills the shell, the process
# group the shell may have moved into, and the watchdog's own group. Its stdin is a pipe the
# caller holds open and never writes, so the monitor's read returns only when the caller exits
# for any reason, SIGKILL included, or closes the pipe on a timeout. An interactive shell can
# put itself in a group of its own, which a kill of the watchdog's group alone never reaches.
_LOGIN_PROBE_WATCHDOG = (
    "exec 3<&0 </dev/null; "
    '"$@" 3<&- & shell=$!; '
    "(read -r _ <&3; "
    'kill -KILL -"$shell" 2>/dev/null; kill -KILL "$shell" 2>/dev/null; kill -KILL 0) '
    ">/dev/null 2>&1 & monitor=$!; "
    'wait "$shell"; code=$?; '
    'kill -KILL "$monitor" 2>/dev/null; exit "$code"'
)

# How long a timed-out probe waits for the monitor to kill the shell before killing the group.
_MONITOR_GRACE_SECONDS = 2.0


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
    alive_open = True
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
        try:
            with process:
                try:
                    stdout, _ = process.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    # Closing the pipe wakes the monitor, which kills the shell wherever its
                    # group is, before the group kill below.
                    os.close(caller_alive)
                    alive_open = False
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=_MONITOR_GRACE_SECONDS)
                    raise
        finally:
            # Whatever ended the call, kill what is left of the probe's group: a child the shell
            # backgrounded, or the shell itself when the monitor never ran. Linux and macOS do not
            # hand out a pid still in use as a process group id, so the id cannot name a stranger.
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)
        return process.returncode, stdout
    finally:
        if alive_open:
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
