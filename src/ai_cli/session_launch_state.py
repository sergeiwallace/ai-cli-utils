"""Owning-host launch registration and lifecycle handoff adapters."""

import os
import shlex
import subprocess
import sys
from pathlib import Path

from . import config
from . import session_run_state as state


def register_launch(
    name: str,
    *,
    engine: str,
    cwd: Path,
    project: str,
    options: dict,
    extra_args: list[str],
    project_prefix: str = "",
    new_start: bool = False,
) -> None:
    cfg = config.load_config()
    host = cfg.get("machine", {}).get("host_id") or state.registry.load_registry()["machine"]
    argv = ["ai", engine, name]
    if project:
        argv += ["-p", project]
    if project_prefix:
        argv += ["--project-prefix", project_prefix]
    for key, flag in (
        ("once", "--once"),
        ("bare", "--bare"),
        ("sandbox", "--sandbox"),
        ("no_worktree", "--no-worktree"),
        ("no_direnv", "--no-direnv"),
        ("is_remote", "--is-remote"),
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
            "shell": os.environ.get("SHELL", ""),
            "argv": argv,
        },
        host=host,
        new_start=new_start,
    )
    # Unit installation is independent of on_boot and never delays a lifecycle write.
    if state.settings()["publish"]["repo"] and os.name != "nt":
        try:
            subprocess.Popen(
                [sys.executable, "-m", "ai_cli.main", "sessions", "publish-timer", "install"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            state.LOGGER.error(
                "publication timer installation could not be started; run ai sessions publish-timer install"
            )


def single_run_command(name: str, command: list[str]) -> list[str]:
    return [sys.executable, "-m", "ai_cli.main", "sessions", "_run", name, "--", *command]


def single_run_shell(name: str, command: list[str]) -> str:
    return shlex.join(single_run_command(name, command))


def handoff(name: str, delegate):
    def wrapped(file, args):
        import psutil

        generation = state.begin(name, command=list(args))
        if not state.mark_running(name, generation=generation):
            raise RuntimeError("startup persistence failed; agent was not started")
        try:
            owner = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "ai_cli.session_bare_owner",
                    str(os.getpid()),
                    str(psutil.Process().create_time()),
                    name,
                    generation,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            from .session_bare_owner import settle

            settle(name, generation, "unknown")
            raise
        cause = "manual_exit"
        try:
            return delegate(file, args)
        except OSError:
            cause = "unknown"
            raise
        finally:
            try:
                if owner.stdin is not None:
                    owner.stdin.write(b"cancel")
                    owner.stdin.close()
            except BrokenPipeError:
                pass
            owner.wait()
            from .session_bare_owner import settle

            settle(name, generation, cause)

    return wrapped


def reattach(name: str) -> None:
    for record in state.registry.load_registry()["sessions"]:
        if record.get("kind") == "local" and record.get("name") == name and record.get("boot_recipe"):
            state.register(name, recipe=record["boot_recipe"], host=record.get("owning_host", ""))
            return
    state.LOGGER.warning("legacy session has no complete boot recipe; boot replay requires an explicit new launch")
