"""Best-effort Claude Code plugin refresh before each interactive agent start."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable

from . import config as _config
from . import output as _out
from .output import Untagged

_COMMAND_TIMEOUT_SECONDS = 10


def _run(claude: str, args: list[str], plugin: str, emit: Callable[[str, str], None]) -> str | None:
    try:
        return (
            subprocess.run(
                [claude, "plugin", *args],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_COMMAND_TIMEOUT_SECONDS,
                check=True,
            ).stdout
            or ""
        )
    except subprocess.TimeoutExpired:
        reason = "timed out"
    except (OSError, subprocess.CalledProcessError):
        reason = "failed"
    # Never forward command output or exception text: either can contain credentials.
    emit("warning", f"Plugin refresh {plugin}: {' '.join(args)} {reason}; continuing launch")
    return None


def _version(claude: str, plugin: str, emit: Callable[[str, str], None]) -> str:
    reply = _run(claude, ["list", "--json"], plugin, emit)
    if reply is None:
        return "unknown"
    try:
        rows = json.loads(reply)
        if not isinstance(rows, list):
            raise ValueError
        versions = {
            row["version"]
            for row in rows
            if isinstance(row, dict)
            and row.get("id") == plugin
            and isinstance(row.get("version"), str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,127}", row["version"])
        }
        return ",".join(sorted(versions)) or "unknown"
    except (ValueError, TypeError):
        emit("warning", f"Plugin refresh {plugin}: could not read plugin versions; continuing launch")
        return "unknown"


def _print_record(level: str, message: str) -> None:
    _out.raw(Untagged.INTERNAL_REPLY, f"{level}\t{message}")


def refresh_plugins(emit: Callable[[str, str], None] = _print_record) -> None:
    """Print level/tab/message records for the session script's existing logger.

    Resolve the executable through PATH (including Windows executable suffixes),
    and pass argument lists to subprocess rather than quoting paths for a shell.
    """
    claude = shutil.which("claude")
    if claude is None:
        return
    try:
        plugins = _config.read_launch_config(_config.load_config(read_only=True))["refresh_plugins"]
    except (ValueError, OSError):
        emit("warning", "Plugin refresh: invalid launch config; continuing launch")
        return
    for plugin in plugins:
        before = _version(claude, plugin, emit)
        _run(claude, ["marketplace", "update", plugin.split("@", 1)[1]], plugin, emit)
        _run(claude, ["update", plugin], plugin, emit)
        after = _version(claude, plugin, emit)
        emit("info", f"Plugin refresh {plugin}: {before} -> {after}")
