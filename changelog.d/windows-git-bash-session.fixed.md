Start sessions under Git Bash on Windows. The session script hands native Windows Python
the Windows pid of its shell (MSYS2's `/proc/<pid>/winpid`) instead of the shell's own pid,
which Python could not find, and runs its child body without the POSIX terminal handoff.
The launcher now waits for the multiplexer client or agent it starts on Windows instead
of emulating an exec, so the calling shell no longer shares the console's keyboard with
it, and a session ending itself no longer opens console windows.
