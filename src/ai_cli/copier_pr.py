"""Publish an isolated template update as a pull request."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .copier_recovery import _template_tip
from .git_repair import _git_env


@dataclass(frozen=True)
class PendingUpdatePR:
    number: int
    url: str


def _open_update_pr(wt_dir: Path, project_name: str, source: str) -> PendingUpdatePR:
    """Reuse an open PR or publish a versioned branch and open a PR for it."""
    _, short = _template_tip(source)
    branch = f"copier-update/{short}"
    ref = f"refs/heads/{branch}"
    env = {**_git_env(), "GH_PROMPT_DISABLED": "1", "GIT_TERMINAL_PROMPT": "0"}

    def run(args: list[str], operation: str, *, acceptable: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(args, cwd=wt_dir, capture_output=True, text=True, env=env, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"{operation} could not run ({type(exc).__name__})") from exc
        if result.returncode not in acceptable:
            raise RuntimeError(f"{operation} failed (exit {result.returncode})")
        return result

    git = ["git", "-C", str(wt_dir)]
    origin = run([*git, "remote", "get-url", "origin"], "Origin lookup").stdout.strip()
    if "://" not in origin:
        # Git also accepts SSH remotes in the form git@host:owner/repo.git.
        origin = re.sub(r"^([^/@]+@)?([^/:]+):", r"ssh://\1\2/", origin)
    try:
        parsed = urlsplit(origin)
        repo = parsed.path.strip("/").removesuffix(".git")
        if parsed.hostname is None or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
            raise ValueError("expected host and repository path")
        env["GH_REPO"] = f"{parsed.hostname}/{repo}"
    except ValueError as exc:
        raise RuntimeError("Origin does not identify a GitHub repository") from exc

    listed = run(["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number,url"], "PR lookup")
    try:
        rows = json.loads(listed.stdout)
        if not isinstance(rows, list):
            raise ValueError("expected a PR list")
        if rows:
            number, url = rows[0]["number"], rows[0]["url"]
            if type(number) is not int or number < 1 or not isinstance(url, str) or not url:
                raise ValueError("expected a PR number and URL")
            return PendingUpdatePR(number, url)
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeError("PR lookup returned invalid data") from exc

    remote = run(
        [*git, "ls-remote", "--exit-code", "--refs", "origin", ref],
        "PR branch lookup",
        acceptable=(0, 2),
    )
    if remote.returncode == 2:
        # An empty lease permits creation only, even if the branch appears after the lookup.
        run([*git, "push", f"--force-with-lease={ref}:", "origin", f"HEAD:{ref}"], "PR branch push")

    created = run(
        [
            "gh",
            "pr",
            "create",
            "--base",
            "main",
            "--head",
            branch,
            "--title",
            "chore: copier update from project-template",
            "--body",
            f"Update {project_name} from project-template at `{short}`.",
        ],
        "PR creation",
    )
    url = created.stdout.strip()
    match = re.fullmatch(r"https?://[^\s]+/pull/([1-9][0-9]*)/?", url)
    if match is None:
        raise RuntimeError("PR creation did not return a pull request URL")
    return PendingUpdatePR(int(match[1]), url)
