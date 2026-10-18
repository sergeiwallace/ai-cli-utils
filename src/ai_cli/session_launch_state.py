"""Owning-host launch registration and lifecycle handoff adapters."""

import os
import shlex
import subprocess
import sys
from pathlib import Path

from . import config
from . import session_run_state as state


def register_launch(name: str, *, engine: str, cwd: Path, project: str, options: dict, extra_args: list[str]) -> None:
    cfg = config.load_config()
    host = cfg.get("machine", {}).get("host_id") or state.registry.load_registry()["machine"]
    argv = ["ai", engine, name]
    if project:
        argv += ["-p", project]
    for key, flag in (
        ("once", "--once"),
        ("bare", "--bare"),
        ("sandbox", "--sandbox"),
        ("no_worktree", "--no-worktree"),
        ("no_direnv", "--no-direnv"),
    ):
        if options.get(key):
            argv.append(flag)
    argv += extra_args
    state.register(
        name,
        recipe={
            "session_name": name,
            "owning_host": host,
            "cwd": str(cwd),
            "project": project,
            "engine": engine,
            "options": options,
            "extra_args": extra_args,
            "argv": argv,
        },
        host=host,
    )
    # Unit installation is independent of on_boot and never delays a lifecycle write.
    if state.settings()["publish"]["repo"] and os.name != "nt":
        subprocess.Popen(
            [sys.executable, "-m", "ai_cli.main", "sessions", "publish-timer", "install"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def single_run_command(name: str, command: list[str]) -> list[str]:
    return [sys.executable, "-m", "ai_cli.main", "sessions", "_run", name, "--", *command]


def single_run_shell(name: str, command: list[str]) -> str:
    return shlex.join(single_run_command(name, command))


def handoff(name: str, delegate):
    def wrapped(_file, args):
        command = single_run_command(name, args)
        return delegate(command[0], command)

    return wrapped
