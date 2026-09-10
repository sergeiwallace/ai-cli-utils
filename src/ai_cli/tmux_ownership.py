"""Generation-fenced ownership checks for destructive tmux operations."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

_IDENTITY_FORMAT = "#{session_id}\t#{session_name}\t#{@ai_cli_session_generation}"
_FENCE_FORMAT = "#{session_id}|#{@ai_cli_session_generation}"
_FENCE_MISMATCH = "__ai_cli_ownership_mismatch__"
_SESSION_ID_RE = re.compile(r"^\$\d+$")
_GENERATION_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@dataclass(frozen=True)
class TmuxSessionIdentity:
    """Immutable identity captured from one managed tmux session generation."""

    session_id: str
    session_name: str
    generation: str


def capture_tmux_session_identity(
    target: str,
    *,
    expected_generation: str | None = None,
    tmux_command: Sequence[str] = ("tmux",),
) -> TmuxSessionIdentity | None:
    """Capture a validated opaque ID and managed-generation marker."""
    try:
        result = subprocess.run(
            [*tmux_command, "display-message", "-p", "-t", target, _IDENTITY_FORMAT],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    output = result.stdout if isinstance(result.stdout, str) else ""
    parts = output.rstrip("\n").split("\t")
    if len(parts) != 3:
        return None
    session_id, session_name, generation = parts
    if (
        not _SESSION_ID_RE.fullmatch(session_id)
        or not session_name
        or not _GENERATION_RE.fullmatch(generation)
        or (expected_generation is not None and generation != expected_generation)
    ):
        return None
    return TmuxSessionIdentity(session_id, session_name, generation)


def kill_owned_tmux_session(
    identity: TmuxSessionIdentity,
    *,
    tmux_command: Sequence[str] = ("tmux",),
) -> bool:
    """Atomically revalidate ``identity`` and kill only that exact generation."""
    if not _SESSION_ID_RE.fullmatch(identity.session_id) or not _GENERATION_RE.fullmatch(identity.generation):
        return False
    expected = f"{identity.session_id}|{identity.generation}"
    predicate = f"#{{==:{_FENCE_FORMAT},{expected}}}"
    try:
        result = subprocess.run(
            [
                *tmux_command,
                "if-shell",
                "-F",
                "-t",
                identity.session_id,
                predicate,
                f"kill-session -t '{identity.session_id}'",
                f"display-message -p {_FENCE_MISMATCH}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    output = result.stdout if isinstance(result.stdout, str) else ""
    return result.returncode == 0 and _FENCE_MISMATCH not in output
