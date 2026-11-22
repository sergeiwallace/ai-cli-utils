"""Run a program in the foreground of this process's terminal and wait for it.

Depends on: nothing in this package.
"""

import signal
import subprocess


def run_in_foreground(args: list[str], executable: str | None = None, env: dict[str, str] | None = None) -> int:
    """Run ``args`` on the terminal this process was given and return its exit status.

    The program inherits stdin, stdout and stderr untouched, so an interactive agent
    sees a terminal. Ctrl+C reaches every process on that terminal, so this one ignores
    it while the program runs and leaves the interrupt to the program. It is ignored
    only once the program exists, so the program never starts with it ignored,
    whatever a platform passes on to a child.
    """
    child = subprocess.Popen(args, executable=executable, env=env)
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        return child.wait()
    finally:
        signal.signal(signal.SIGINT, previous)
