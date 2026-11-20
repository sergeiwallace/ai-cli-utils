"""The one multiplexer executable every tmux call in the launcher runs.

``[tmux] binary`` in config.toml names it, and ``[tmux.hosts.<host id>] binary``
names it for one machine, overriding the global value there. The host id is the
machine's ``[machine] host_id``. Both are read from config.toml on every call, so
an edit applies to the next tmux call with no relaunch.

Why a setting: on a Windows machine whose tmux is psmux, more than one build can
answer to ``tmux`` (psmux installs ``psmux``, ``pmux`` and ``tmux`` executables,
and an MSYS2 or Cygwin tmux may sit on PATH too), and which one a shell finds
depends on that shell's PATH. A server started by one build and queried by
another is the client/server mismatch the launcher refuses. Naming the executable
once makes every shell on the machine run the same build.

Unset (or empty), argv[0] is the bare name ``tmux`` and the operating system's
PATH lookup decides, exactly as before the setting existed. A configured binary
that does not resolve to an executable is refused by name, citing the setting
that named it; it is never replaced by whatever ``tmux`` PATH finds.

This module is the only place a tmux argv[0] is chosen;
``tests/test_tmux_binary.py`` fails if any other module in the package builds an
argv starting with the literal ``"tmux"``.

Two kinds of call do not take their argv[0] from here. The statusline script runs
outside Python, as a child of the agent inside a session, so the session script
exports the resolved binary as :data:`ENV_VAR` and the statusline reads that. A
command sent over ssh (``ai reconnect``, the remote liveness probe) runs on the
other host, where this host's setting means nothing, so it names :data:`REMOTE`
and the remote login's PATH decides; ``docs/research/psmux-compatibility.md``
records why.
"""

from __future__ import annotations

import shlex
import shutil
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from . import config as _config

#: argv[0] when nothing is configured: the PATH lookup the launcher always did.
DEFAULT = "tmux"

#: The program an ssh command runs on the remote host. Bare on purpose: the remote
#: login's shell may be POSIX, cmd or PowerShell, and only a plain program name runs
#: in all of them without the remote ``ai`` being on that login's PATH.
REMOTE = "tmux"

#: The resolved binary, exported into each session for scripts outside Python (the
#: statusline). Unset in a session when nothing is configured.
ENV_VAR = "AI_CLI_TMUX_BINARY"


class TmuxBinaryMissing(FileNotFoundError):
    """A configured ``[tmux] binary`` that names no executable.

    A ``FileNotFoundError`` on purpose: every caller that already treats a missing
    tmux as "tmux is absent" (by catching ``OSError``) keeps doing so, rather than
    falling back to another tmux.
    """


@dataclass(frozen=True)
class Resolution:
    """Which multiplexer executable this machine's tmux calls run, and why.

    The executable is looked up only when something needs its location: with nothing
    configured, building an argv makes no PATH lookup at all, exactly as the literal
    ``"tmux"`` it replaced made none.
    """

    #: The raw configured value; None when nothing is configured.
    configured: str | None
    #: Where the value came from, e.g. ``.../config.toml [tmux] binary``; empty when unset.
    setting: str

    @cached_property
    def path(self) -> str | None:
        """The executable's location: the configured binary resolved, or PATH's ``tmux``; None when not found."""
        if self.configured is None:
            return shutil.which(DEFAULT)
        # A bare name ("psmux") is looked up on PATH as named; a path is checked as given.
        return shutil.which(str(Path(self.configured).expanduser()))

    @property
    def missing(self) -> bool:
        """True only for a configured binary that does not resolve. An absent PATH tmux is not this."""
        return self.configured is not None and self.path is None

    @property
    def refusal(self) -> str | None:
        """The message that refuses a missing configured binary by name; None otherwise."""
        if not self.missing:
            return None
        # Quoted as written, not repr'd: a Windows path must read as the operator typed it.
        return (
            f'{self.setting} = "{self.configured}" is not an executable file.\n'
            "  The launcher runs only the configured multiplexer and never falls back to the\n"
            "  tmux on PATH: fix the path, or remove the setting to use the tmux on PATH."
        )

    @property
    def executable(self) -> str:
        """argv[0] for a tmux call. Raises :class:`TmuxBinaryMissing` for a missing configured binary."""
        if self.configured is None:
            return DEFAULT
        if self.path is None:
            raise TmuxBinaryMissing(self.refusal)
        return self.path


def _read_config() -> dict:
    """config.toml, read fresh. Missing, unreadable or malformed reads as empty.

    Read directly rather than through ``load_config``: that also registers a machine
    profile and validates unrelated sections, which a tmux call must neither do nor
    be refused by. A malformed file is reported by the launch's own config load.
    ``tomllib`` raises ``UnicodeDecodeError``, not ``TOMLDecodeError``, for a file
    that is not UTF-8, so both count as malformed.
    """
    try:
        with Path(_config.config_file_text()).open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}


def _host_id(config: dict) -> str:
    """``[machine] host_id`` when set, else the detected one, as ``load_config`` resolves it."""
    machine = config.get("machine")
    value = machine.get("host_id") if isinstance(machine, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return _config.detect_machine_profile(config)["host_id"]


def _configured(config: dict) -> tuple[str | None, str]:
    """The configured value and the setting that supplied it; ``(None, "")`` when unset."""
    section = config.get("tmux")
    if not isinstance(section, dict):
        return None, ""
    where = _config.config_file_text()
    hosts = section.get("hosts")
    if isinstance(hosts, dict):
        host = _host_id(config)
        table = hosts.get(host)
        if isinstance(table, dict) and "binary" in table and table["binary"] != "":
            return str(table["binary"]), f"{where} [tmux.hosts.{host}] binary"
    if "binary" in section and section["binary"] != "":
        return str(section["binary"]), f"{where} [tmux] binary"
    return None, ""


def resolve(config: dict | None = None) -> Resolution:
    """Resolve this machine's multiplexer executable. ``config`` defaults to config.toml, read now."""
    configured, setting = _configured(_read_config() if config is None else config)
    return Resolution(configured=configured, setting=setting)


def executable(config: dict | None = None) -> str:
    """argv[0] for a tmux call; raises :class:`TmuxBinaryMissing` for a missing configured binary."""
    return resolve(config).executable


def argv(*args: str, config: dict | None = None) -> list[str]:
    """A tmux command line: the resolved executable followed by ``args``."""
    return [executable(config), *args]


def command(tmux_command: Sequence[str] | None = None) -> tuple[str, ...]:
    """``tmux_command`` when a caller injected one, else the resolved executable alone."""
    return tuple(tmux_command) if tmux_command is not None else (executable(),)


def which(config: dict | None = None) -> str | None:
    """Where the executable is, or None: the presence check every caller makes."""
    return resolve(config).path


def shell_prelude(config: dict | None = None) -> str:
    """A shell function routing a script's bare ``tmux`` calls to the configured binary.

    It also exports the binary as :data:`ENV_VAR`, which is how the statusline script,
    run by the agent this script starts, finds the same executable. The value is the
    launch's resolution on purpose: this session's server was started by that binary,
    so it is the client that can reach it even if the setting changes mid-session.

    With nothing configured, or a missing configured binary, the script's ``tmux``
    calls keep using PATH exactly as before (a missing binary has already been refused
    by the launch that writes the script), and :data:`ENV_VAR` is unset so a value
    inherited from an outer session cannot point this one at another build.
    """
    resolution = resolve(config)
    if resolution.configured is None or resolution.path is None:
        return f"unset {ENV_VAR}\n"
    quoted = shlex.quote(Path(resolution.path).as_posix())
    return f'tmux() {{ {quoted} "$@"; }}\nexport {ENV_VAR}={quoted}\n'


def server_process_names(config: dict | None = None) -> frozenset[str]:
    """Process names a multiplexer server can run under: tmux's, plus the configured binary's."""
    names = {"tmux", "tmux.exe"}
    resolution = resolve(config)
    path = resolution.path if resolution.configured is not None else None
    if path:
        name = Path(path).name
        names |= {name, Path(name).stem}
    return frozenset(names)
