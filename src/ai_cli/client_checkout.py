"""Bring an editable client checkout up to its upstream target before a remote launch.

A remote launch updates the REMOTE host's ai-cli-utils, but the client running the
launch is this machine's own checkout. The install check only compares that
checkout's packaged files with what was last installed from it, so a checkout
nobody has pulled reads as current indefinitely, and a fix to the client side of
the protocol can never reach the machine that needs it through ``ai c -R``.

The sync is deliberately narrow: fast-forward only, on ``main`` only, with no
tracked change in the tree. Anything else is reported with the command that fixes
it and left exactly as it was found.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .git_repair import _git_env

UPSTREAM_REMOTE = "origin"
UPSTREAM_BRANCH = "main"
UPSTREAM_REF = f"refs/remotes/{UPSTREAM_REMOTE}/{UPSTREAM_BRANCH}"
FETCH_TIMEOUT_SECONDS = 20.0
GIT_TIMEOUT_SECONDS = 30.0


class ClientState(StrEnum):
    CURRENT = "current"
    SYNCED = "synced"
    BEHIND = "behind"
    UNCHECKED = "unchecked"


@dataclass(frozen=True)
class ClientCheck:
    state: ClientState
    behind: int = 0
    reason: str = ""
    fix: str = ""


def _git(checkout: Path, *args: str, timeout: float = GIT_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(checkout), *args],
        capture_output=True,
        text=True,
        env=_git_env(),
        timeout=timeout,
        check=False,
    )


def _first_line(result: subprocess.CompletedProcess) -> str:
    text = (result.stderr or result.stdout or "").strip()
    return text.splitlines()[0] if text else f"exit {result.returncode}"


def sync_client_checkout(checkout: Path) -> ClientCheck:
    """Fast-forward ``checkout`` to ``origin/main`` when that is safe; report what was done."""
    target = f"{UPSTREAM_REMOTE}/{UPSTREAM_BRANCH}"
    try:
        fetch = _git(checkout, "fetch", "--quiet", UPSTREAM_REMOTE, UPSTREAM_BRANCH, timeout=FETCH_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return ClientCheck(ClientState.UNCHECKED, reason=f"git fetch timed out after {FETCH_TIMEOUT_SECONDS:.0f}s")
    except OSError as exc:
        return ClientCheck(ClientState.UNCHECKED, reason=f"git could not run: {exc}")
    if fetch.returncode != 0:
        return ClientCheck(ClientState.UNCHECKED, reason=f"git fetch failed: {_first_line(fetch)}")

    try:
        counts = _git(checkout, "rev-list", "--left-right", "--count", f"HEAD...{UPSTREAM_REF}")
        if counts.returncode != 0:
            return ClientCheck(ClientState.UNCHECKED, reason=f"cannot compare with {target}: {_first_line(counts)}")
        ahead, behind = (int(n) for n in counts.stdout.split())
        if behind == 0:
            return ClientCheck(ClientState.CURRENT)

        ff_command = f"git -C {checkout} merge --ff-only {target}"
        if ahead:
            return ClientCheck(
                ClientState.BEHIND,
                behind=behind,
                reason=f"it has {ahead} local commit(s) not on {target}, so it cannot fast-forward",
                fix=f"git -C {checkout} rebase {target}",
            )
        branch = _git(checkout, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
        if branch != UPSTREAM_BRANCH:
            return ClientCheck(
                ClientState.BEHIND,
                behind=behind,
                reason=f"it is on {branch or 'a detached HEAD'}, not {UPSTREAM_BRANCH}",
                fix=f"git -C {checkout} switch {UPSTREAM_BRANCH} && {ff_command}",
            )
        status = _git(checkout, "status", "--porcelain", "--untracked-files=no")
        if status.returncode != 0 or status.stdout.strip():
            return ClientCheck(
                ClientState.BEHIND,
                behind=behind,
                reason="it has uncommitted changes; commit or stash them first",
                fix=ff_command,
            )
        merge = _git(checkout, "merge", "--ff-only", "--quiet", UPSTREAM_REF)
        if merge.returncode != 0:
            return ClientCheck(
                ClientState.BEHIND,
                behind=behind,
                reason=f"git merge --ff-only failed: {_first_line(merge)}",
                fix=ff_command,
            )
    except subprocess.TimeoutExpired as exc:
        return ClientCheck(ClientState.UNCHECKED, reason=f"git timed out: {' '.join(map(str, exc.cmd[3:]))}")
    except (OSError, ValueError) as exc:
        return ClientCheck(ClientState.UNCHECKED, reason=f"cannot compare with {target}: {exc}")
    return ClientCheck(ClientState.SYNCED, behind=behind)
