"""Validated VS Code Remote-SSH file opening."""

from __future__ import annotations

import re
import shlex
import subprocess
from collections.abc import Callable
from pathlib import PurePosixPath

_AUTHORITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_POSITIVE_INTEGER_RE = re.compile(r"[1-9][0-9]*\Z")


def _contains_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _validate_authority(authority: str) -> None:
    if not _AUTHORITY_RE.fullmatch(authority):
        raise ValueError("authority must be a non-empty SSH alias containing only letters, digits, '.', '_', or '-'")


def build_vscode_remote_command(authority: str, path: str, line: str = "") -> list[str]:
    """Return an argv-safe VS Code command for one absolute remote path."""
    _validate_authority(authority)
    if not path or _contains_control_character(path) or not PurePosixPath(path).is_absolute():
        raise ValueError("path must be an absolute POSIX path without control characters")
    if line and not _POSITIVE_INTEGER_RE.fullmatch(line):
        raise ValueError("line must be a positive integer")

    target = f"{path}:{line}" if line else path
    return ["code", "--remote", f"ssh-remote+{authority}", "--goto", target]


def build_iterm2_semantic_history(authority: str) -> dict[str, str]:
    """Return remote-only Semantic History settings for a generated profile."""
    _validate_authority(authority)
    command = shlex.join(["ai", "internal", "open-vscode-remote", authority, r"\1", r"\2"])
    return {"action": "command", "text": command}


def open_vscode_remote(
    authority: str,
    path: str,
    line: str = "",
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> int:
    """Open one remote file and return the VS Code process exit status."""
    command = build_vscode_remote_command(authority, path, line)
    result = runner(command, check=False, shell=False)
    return result.returncode
