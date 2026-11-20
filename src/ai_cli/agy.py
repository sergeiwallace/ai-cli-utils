"""Launch Antigravity in the named session's workspace."""

import json
import os
import shlex
import sys
from pathlib import Path
from uuid import UUID

from . import output as _out


def conversation_cache(home: Path) -> Path:
    # Antigravity 1.5.13 stores its own workspace-to-conversation mapping here.
    return home / ".gemini" / "antigravity-cli" / "cache" / "last_conversations.json"


def read_conversations(cache: Path) -> dict[str, str]:
    data = json.loads(cache.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("expected a workspace-to-conversation mapping")
    conversations = {}
    for workspace, value in data.items():
        if isinstance(value, str):
            try:
                conversations[workspace] = str(UUID(value))
            except ValueError:
                continue
    return conversations


def launch_command(command: str, name: str, *, sandbox: bool, isolated: bool, extra_args: list[str]) -> list[str]:
    # Use the same exec adapter after direnv in bare, one-shot and managed launches.
    return [
        sys.executable,
        "-m",
        "ai_cli.agy",
        command,
        name,
        "worktree" if isolated else "shared",
        *(["--sandbox"] if sandbox else []),
        *extra_args,
    ]


def launch(command: str, name: str, scope: str, *extra_args: str) -> int:
    from .main import _exec_handoff

    try:
        argv = shlex.split(command)
        if not argv or not argv[0]:
            raise ValueError("empty command")
        if scope == "worktree" and not any(
            arg in ("-c", "--continue", "--conversation") or arg.startswith(("--continue=", "--conversation="))
            for arg in (*argv[1:], *extra_args)
        ):
            try:
                conversation = read_conversations(conversation_cache(Path.home())).get(str(Path.cwd().resolve()))
            except (OSError, ValueError):
                conversation = None
            if conversation:
                argv += ["--conversation", conversation]
        os.environ["AI_CLI_SESSION_NAME"] = name
        _exec_handoff(argv[0], [*argv, *extra_args])
    except (OSError, ValueError) as exc:
        _out.error(
            _out.Tag.LAUNCH,
            "start",
            f"Could not start Antigravity using {command!r}: {exc}. "
            "Install agy and put it on PATH, or set [agy] command in your ai config.",
        )
        return 126


if __name__ == "__main__":
    sys.exit(launch(*sys.argv[1:]))
