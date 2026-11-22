"""Run a program in the foreground of this process's terminal and wait for it.

Depends on: nothing in this package.
"""

import os
import shutil
import signal
import subprocess
from pathlib import Path


def resolve_program(program: str, path: str | None = None) -> str:
    """``program`` as the absolute path of what a shell would run, else ``program`` unchanged.

    ``shutil.which`` searches PATH with PATHEXT on Windows, so ``claude`` resolves to
    ``claude.EXE`` and ``codex`` to ``codex.cmd`` there; CreateProcess alone tries only
    ``.exe`` and cannot start a ``.cmd`` by bare name. A name with a directory part is
    already a path and is left alone, and an unresolved name is returned as given so the
    caller's own error reports it.
    """
    if Path(program).name != program:
        return program
    return shutil.which(program, path=path) or program


def run_in_foreground(args: list[str], executable: str | None = None, env: dict[str, str] | None = None) -> int:
    """Run ``args`` on the terminal this process was given and return its exit status.

    The program, ``executable`` or else ``args[0]``, is resolved with :func:`resolve_program`
    on the PATH it will run under. It inherits stdin, stdout and stderr untouched, so an
    interactive agent sees a terminal. Ctrl+C reaches every process on that terminal, so
    this one ignores it while the program runs and leaves the interrupt to the program. It
    is ignored only once the program exists, so the program never starts with it ignored,
    whatever a platform passes on to a child.
    """
    program = resolve_program(executable or args[0], (os.environ if env is None else env).get("PATH"))
    child = subprocess.Popen(args, executable=program, env=env)
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        return child.wait()
    finally:
        signal.signal(signal.SIGINT, previous)
