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

## Design Decisions

### D-1: the statusline script reads the binary the session exported

The statusline script (`statusline-command.sh`) runs outside Python, as a child of
the agent the session script starts. The session script's prelude exports the
resolved binary as `AI_CLI_TMUX_BINARY`, and the statusline runs
`"${AI_CLI_TMUX_BINARY:-tmux}"`. With nothing configured, the prelude unsets the
variable, so a value inherited from an outer session cannot point this one at
another build, and the tmux on PATH answers. A statusline in a session the launcher
did not start sees no variable and also uses PATH.

The value is fixed when the session starts, not read from config.toml on each
render. That is deliberate: the session's server was started by the launch-time
binary, so that binary is the client that can reach it. Re-reading the setting
would, after a mid-session edit, query a different build's server and find nothing.
Asking `ai` on each render was rejected for the same reason and for its cost (a
Python start per statusline refresh).

### D-2: ssh commands run the remote host's bare `tmux`

`ai reconnect` and the remote liveness probe in `ai iterm2 sessions --prune` send a tmux command
over ssh. That tmux runs on the other host, so this host's `[tmux] binary` is the
wrong answer, and both name `tmux` (`tmux_binary.REMOTE`): the remote login's PATH
decides, as it did before the setting existed.

Running `ai` on the remote host to resolve its own setting was considered and
rejected for now. The remote login's shell may be POSIX, cmd or PowerShell, and only
a plain program name runs in all of them; a fallback for a remote without `ai` on
its non-interactive PATH needs shell syntax, and without one a probe that works
today would fail. Bare `tmux` is also the more reversible choice: it adds no remote
interface. A remote host that sets `[tmux] binary` should put that build first on
the ssh login's PATH.

## Git Bash

psmux runs Git Bash panes and runs tmux plugin scripts with Git Bash
(<https://github.com/psmux/psmux/blob/master/docs/plugins.md>). Its diagnostics doc
measures cursor shapes in eight terminals, mintty among them
(<https://github.com/psmux/psmux/blob/master/docs/diagnostics.md>). No psmux doc confirms
attaching a session from Git Bash's mintty window; that is unconfirmed until measured.
