# psmux compatibility with the session launcher

Status: research, 2026-11-17. Sources were read at psmux commit `290d347` (2026-11-17).

## What psmux is

psmux is a native Windows terminal multiplexer written in Rust. It runs on ConPTY and
needs no WSL, Cygwin or MSYS2. Installed by winget, scoop, cargo or chocolatey, it puts
`tmux.exe`, `pmux.exe` and `psmux.exe` on `PATH`, so on such a machine `tmux` is psmux.
Project: <https://github.com/psmux/psmux>. Docs index:
<https://github.com/psmux/psmux/blob/master/docs/README.md>.

## How it reports its version

- `tmux -V` prints two lines (`src/cli.rs`, `print_version`):

  ```text
  tmux 3.4.0
  psmux 3.4.0 (a1b2c3d 2026-10-13)
  ```

  The first line keeps tmux's contract for tools that parse it. The second line is build
  provenance for people.
- A running server answers `display-message -p '#{version}'` with the bare crate version,
  `3.4.0` (`src/format.rs`, the `"version"` format variable).

So a psmux client and its own server report the same version, `3.4.0`. The launcher read
the last token of the whole `-V` output, `2026-10-13)`, as the client version and refused
the launch as a client/server mismatch. The version pattern also did not accept a
three-part version. Both are fixed: only the first line is read, `3.4.0` is a valid
version, and a second line starting `psmux ` marks the kind as psmux.

## Commands the launcher uses

From the compatibility matrix
(<https://github.com/psmux/psmux/blob/master/docs/compatibility.md>):

| Launcher use | psmux |
| --- | --- |
| `-L <name>` socket namespaces | Supported, each namespace isolated |
| `-S <path>` | Parsed (`src/socket_path.rs`); not in the compatibility matrix |
| `new-session -e VAR=val` | Supported |
| `attach-session`, `has-session`, `list-sessions -F` | Supported; `-F` returned byte for byte |
| `display-message -p` | Supported, encoded as tmux does |
| `set-environment` / `show-environment` (`-g`, `-u`, `-r`) | Supported |
| `send-keys` (incl. `-H`) | Supported |
| `set-option -g` / `-w`, `show-options` | Supported; `show-options -v` prints an inherited value where tmux prints nothing |
| `kill-session`, `kill-server` | Supported; bare `kill-server` leaves `-L` namespaces running |
| `allow-passthrough on` | Partial: wrapped sequences are not forwarded yet |

Not confirmed by any psmux doc: `new-session -c/-x/-y/-P/-F`, `set-option -s/-q`,
`#{client_termname}`, `extended-keys`. The launch output names psmux, so a launch that
fails on one of these is attributable from the transcript.

## One server per multiplexer

psmux keeps its servers in its own directory and namespaces. An MSYS2 tmux uses its own
socket under the MSYS2 `/tmp`. A psmux client therefore never talks to an MSYS2 tmux
server, and the reverse. The client/server refusal still applies within one kind: it
compares versions, and the message names the kind and the client binary's path.

## Pinning one build: `[tmux] binary`

A machine with psmux can have more than one executable answering to `tmux`: psmux's
own `tmux.exe`, `pmux.exe` and `psmux.exe`, and an MSYS2 or Cygwin tmux that Git Bash
may put first on its PATH. Which one runs then depends on each shell's PATH, and a
server started by one is invisible to the other (see above), so `ai ls` in one shell
can miss a session `ai c` started in another.

`[tmux] binary` in config.toml names the executable every launcher tmux call runs, and
`[tmux.hosts.<host id>] binary` names it for one machine. With neither set, the
launcher runs `tmux` through PATH, as before. A configured path that does not exist is
refused by name, and the tmux on PATH is never run in its place. On a psmux machine,
set it to the full path of the psmux executable, for example:

```toml
[tmux]
binary = "C:/Users/user/AppData/Local/Microsoft/WinGet/Links/psmux.exe"
```

The supervisor script that runs inside each session calls `tmux` by name; when the
setting is present the launcher prepends a `tmux()` shell function to that script
naming the same executable, so the session's own calls reach the same server.

## Git Bash

psmux runs Git Bash panes and runs tmux plugin scripts with Git Bash
(<https://github.com/psmux/psmux/blob/master/docs/plugins.md>). Its diagnostics doc
measures cursor shapes in eight terminals, mintty among them
(<https://github.com/psmux/psmux/blob/master/docs/diagnostics.md>). No psmux doc confirms
attaching a session from Git Bash's mintty window; that is unconfirmed until measured.

## The session under Git Bash

Under Git Bash the session script runs in an MSYS2 shell while `ai` and `python3` are
native Windows programs. Three things follow, and the launcher handles each:

- A shell pid is not a Windows pid. `$$` names the shell in MSYS2's own pid table, which
  native Python cannot see, so a session record built from it failed with
  `process PID not found` and the session never started. Every shell pid the script
  hands Python goes through `_ai_native_pid`, which reads MSYS2's `/proc/<pid>/winpid`.
- A native program has no POSIX process groups. The POSIX session hands the terminal to
  its child body's process group; under Git Bash the child body runs as the
  supervisor's foreground command on the pane's console instead, and the stale-session
  reaper evidence, which needs process groups, stays off.
- Windows has no exec. CPython emulates it by starting the program and ending the
  caller, so the shell that ran `ai c` printed its prompt while the tmux client ran on
  the same console, and the two shared the keyboard. The launcher waits for the program
  it hands off to instead, and exits with its status.

- `direnv exec` cannot run a native program. Its PATH search splits on `:`, accepts
  only files with a POSIX execute bit, and ends in Go's `syscall.Exec`, which Windows
  lacks, so it reported `command 'claude' not found on PATH` with `claude.exe` on that
  PATH. Bare and one-shot launches run the agent through `python -m ai_cli.direnv_exec`
  there instead: it loads the environment with `direnv export json` and finds the agent
  with `shutil.which`, which honours PATHEXT. A managed session already evaluates
  `direnv export bash`; that export hands the MSYS2 shell PATH in its Windows spelling,
  so the session script converts it back with `cygpath`. `ai doctor` reports where each
  agent CLI resolves on the PATH a launch from the current directory uses.

A session that still cannot start, for any reason, can be launched without a
multiplexer with `ai c <name> -b`.
