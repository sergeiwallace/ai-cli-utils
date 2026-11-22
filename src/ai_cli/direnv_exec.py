"""Run an agent under a project's direnv environment on every OS.

Depends on: foreground.py.

``direnv exec <dir> <program>`` cannot run a native program on Windows. It finds the
program with its own PATH search, which splits PATH on ``:`` and accepts only a file
with a POSIX execute bit, and then calls Go's ``syscall.Exec``. On Windows PATH is
``;``-separated, no file carries an execute bit, and ``syscall.Exec`` is unsupported,
so a launch printed "command 'claude' not found on PATH" for a ``claude.exe`` sitting in
a directory on that PATH, whatever PATHEXT held.

Where that holds, the launcher runs this module in its place: it loads the same
environment with ``direnv export json``, finds the program on the loaded PATH with
``shutil.which`` (which honours PATHEXT, as it does for direnv itself), and runs it in
the foreground. Elsewhere the launcher keeps ``direnv exec``, which also resolves the
program on the loaded PATH.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

MODULE = "ai_cli.direnv_exec"


def direnv_exec_works() -> bool:
    """Whether ``direnv exec`` can run a program here: everywhere but native Windows."""
    return sys.platform != "win32"


def prefix(project_root: Path) -> list[str]:
    """The argv that runs a program, appended to it, under ``project_root``'s environment."""
    if direnv_exec_works():
        return ["direnv", "exec", str(project_root)]
    return [sys.executable, "-m", MODULE, str(project_root)]


def program_index(command: list[str]) -> int:
    """Where the program sits in ``command``, past either form of :func:`prefix`."""
    if command[:2] == ["direnv", "exec"] and len(command) > 3:
        return 3
    if command[1:3] == ["-m", MODULE] and len(command) > 4:
        return 4
    return 0


def loaded_environment(project_root: Path, base: dict[str, str]) -> tuple[dict[str, str], str]:
    """``base`` with the project's direnv environment applied, and why it could not be.

    The reason is empty when the environment loaded. A failure leaves ``base`` as it
    was: a project environment is an enhancement, never a precondition for a session.
    """
    direnv = shutil.which("direnv", path=base.get("PATH"))
    if direnv is None:
        return base, "direnv is not on PATH"
    try:
        loaded = subprocess.run(
            [direnv, "export", "json"],
            cwd=project_root,
            env=base,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return base, f"direnv could not run: {exc}"
    if loaded.returncode != 0:
        detail = loaded.stderr.strip().splitlines()
        return base, f"direnv exited with status {loaded.returncode}" + (f": {detail[-1]}" if detail else "")
    try:
        changes = json.loads(loaded.stdout) if loaded.stdout.strip() else {}
    except json.JSONDecodeError as exc:
        return base, f"direnv printed an environment that is not JSON: {exc}"
    environment = dict(base)
    for key, value in changes.items():
        if value is None:
            environment.pop(key, None)
        else:
            environment[key] = value
    return environment, ""


def main(argv: list[str] | None = None) -> int:
    from . import output

    args = sys.argv[1:] if argv is None else argv
    if len(args) < 2:
        output.error(output.Tag.LAUNCH, "direnv", f"usage: python -m {MODULE} DIR PROGRAM [ARGS...]")
        return 2
    root, command = Path(args[0]), args[1:]
    environment, failure = loaded_environment(root, dict(os.environ))
    if failure:
        output.warning(
            output.Tag.LAUNCH,
            "direnv",
            f"{failure}; starting {command[0]} without the project environment",
        )
    from .foreground import resolve_program, run_in_foreground

    program = resolve_program(command[0], environment.get("PATH"))
    if shutil.which(program, path=environment.get("PATH")) is None:
        output.error(output.Tag.LAUNCH, "handoff", f"{command[0]} not found on PATH.")
        return 127
    return run_in_foreground(command, executable=program, env=environment)


if __name__ == "__main__":
    sys.exit(main())
