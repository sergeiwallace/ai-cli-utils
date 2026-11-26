"""Run the configured ``[session_register]`` command for a confirmed session.

A deployment may keep a registry of live agent sessions so other tools can address one
by name. The session-start hook runs inside the client's process tree. An agy tmux
resume runs registration after the control bridge has pre-bound the conversation, with
the slot name in ``AI_TMUX_SESSION``. A failure prints one warning line and the session
still starts.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import config as _config
from . import output


def _warn(message: str) -> None:
    output.warning(output.Tag.SESSION, "session", f"Session register skipped: {message}.")


def register(client: str) -> None:
    """Run the configured register command for ``client`` (``codex`` or ``agy``); never raises."""
    try:
        settings = _config.read_session_register_config(_config.load_config(read_only=True))
    except (OSError, ValueError) as exc:
        _warn(f"{exc} (config.toml [session_register])")
        return
    if settings is None:
        return
    argv = [word.replace(_config.SESSION_REGISTER_PLACEHOLDER, client) for word in settings["command"]]
    # A bare name is looked up on PATH here, so Windows also finds .cmd and .bat programs.
    resolved = shutil.which(argv[0]) if Path(argv[0]).name == argv[0] else None
    argv = [resolved or argv[0], *argv[1:]]
    timeout = settings["timeout_seconds"]
    try:
        # Output is captured: the hook's stdout is its reply to the client.
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _warn(f"the command timed out after {timeout:g}s")
        return
    except OSError as exc:
        _warn(f"the command could not start ({exc})")
        return
    if result.returncode != 0:
        lines = result.stdout.decode(errors="replace").strip().splitlines()
        _warn(f"the command failed (exit {result.returncode})" + (f": {lines[0]}" if lines else ""))
