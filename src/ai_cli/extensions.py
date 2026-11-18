"""Launch extensions: commands from config.toml that run once before a session starts.

Each ``[extensions.run.<name>]`` table names a ``command`` (an argument list, run without a
shell) and the ``path`` of the directory it runs in. A relative ``path`` resolves against
``[project] projects_dir``, so a config shared between machines carries no machine paths. A
relative command file (``bin/start``) resolves inside that directory; a bare name is looked up
on ``PATH``.

An extension runs to completion within its timeout. One that starts something long-lived
detaches it and returns. A command that cannot be found, a directory that does not exist, a
non-zero exit and a timeout are all errors, and ``on_error`` decides what an error does:
``"warn"`` reports it and the launch continues, ``"fail"`` reports it and refuses the launch.
The ``[extensions]`` table sets the default; an extension's own ``on_error`` overrides it.

Every attempt is reported through the launch reporter, so it reaches the terminal and the
launch log, with the extension's own output written to the log only.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePath

from . import config as _config
from .launch_reporter import LaunchReporter

ON_ERROR_POLICIES = ("warn", "fail")
EXTENSIONS_DEFAULTS = {"on_error": "fail", "timeout_seconds": 30}
_OP = "extension"


class ExtensionRefusal(SystemExit):
    """A launch refused before it started, because an extension under ``on_error = "fail"`` failed."""

    def __init__(self, reason: str) -> None:
        super().__init__(1)
        self.reason = reason


@dataclass(frozen=True)
class Extension:
    name: str
    command: list[str]
    directory: Path
    on_error: str
    timeout_seconds: float


class _ExtensionError(Exception):
    pass


def _policy(value: object, key: str) -> str:
    if value not in ON_ERROR_POLICIES:
        raise ValueError(f'invalid {key}: use "warn" or "fail"')
    return str(value)


def _timeout(value: object, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"invalid {key}: use a positive number of seconds")
    return float(value)


def read_extensions(config: dict) -> list[Extension]:
    """The configured extensions in config order; ``ValueError`` names the first invalid key."""
    section = config.get("extensions", {})
    if not isinstance(section, dict):
        raise ValueError("extensions must be a table")
    settings = EXTENSIONS_DEFAULTS | section
    default_policy = _policy(settings["on_error"], "extensions.on_error")
    default_timeout = _timeout(settings["timeout_seconds"], "extensions.timeout_seconds")
    entries = settings.get("run", {})
    if not isinstance(entries, dict):
        raise ValueError("extensions.run must be a table of [extensions.run.<name>] tables")
    if not entries:
        return []
    try:
        projects_root = _config.projects_dir_from(config)
    except TypeError as exc:
        raise ValueError("invalid project.projects_dir: use a path") from exc
    extensions = []
    for name, entry in entries.items():
        key = f"extensions.run.{name}"
        if not isinstance(entry, dict):
            raise ValueError(f"{key} must be a table")
        command = entry.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(part, str) and part for part in command):
            raise ValueError(f"invalid {key}.command: use a non-empty list of strings")
        path = entry.get("path", "")
        if not isinstance(path, str):
            raise ValueError(f"invalid {key}.path: use a path string")
        directory = Path(path).expanduser()
        extensions.append(
            Extension(
                name=name,
                command=list(command),
                directory=directory if directory.is_absolute() else projects_root / directory,
                on_error=_policy(entry.get("on_error", default_policy), f"{key}.on_error"),
                timeout_seconds=_timeout(entry.get("timeout_seconds", default_timeout), f"{key}.timeout_seconds"),
            )
        )
    return extensions


def _executable(extension: Extension) -> str:
    program = extension.command[0]
    if PurePath(program).is_absolute() or len(PurePath(program).parts) > 1:
        candidate = Path(program).expanduser()
        if not candidate.is_absolute():
            candidate = extension.directory / candidate
        found = shutil.which(str(candidate))
    else:
        found = shutil.which(program)
    if found is None:
        raise _ExtensionError(f"command not found or not executable: {program}")
    return found


def _run(extension: Extension, reporter: LaunchReporter) -> None:
    if not extension.directory.is_dir():
        raise _ExtensionError(f"path not found: {extension.directory}")
    argv = [_executable(extension), *extension.command[1:]]
    reporter.detail(
        _OP,
        f"{extension.name}: running {' '.join(argv)} in {extension.directory} "
        f"(timeout {extension.timeout_seconds:g}s, on_error {extension.on_error})",
    )
    started = time.monotonic()
    # A file, not a pipe: a child the extension detaches keeps its output handle open, and a
    # pipe would hold this launch until that child exits.
    with tempfile.TemporaryFile() as captured:
        try:
            completed = subprocess.run(
                argv,
                cwd=extension.directory,
                stdin=subprocess.DEVNULL,
                stdout=captured,
                stderr=subprocess.STDOUT,
                timeout=extension.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            outcome = f"timed out after {extension.timeout_seconds:g}s"
            returncode = None
        except OSError as exc:
            raise _ExtensionError(f"could not start {argv[0]}: {exc.strerror or exc}") from exc
        else:
            returncode = completed.returncode
            outcome = f"exited with status {returncode}"
        elapsed = time.monotonic() - started
        captured.seek(0)
        output = captured.read().decode("utf-8", errors="replace")
    for line in output.splitlines():
        reporter.detail(_OP, f"{extension.name} output: {line}")
    if returncode != 0:
        raise _ExtensionError(f"{outcome} in {elapsed:.1f}s")
    reporter.phase(_OP).outcome(f"{extension.name}: {outcome} in {elapsed:.1f}s")


def run_launch_extensions(config: dict, reporter: LaunchReporter, log_path: Path | None = None) -> None:
    """Run every configured extension once, in config order; nothing configured does nothing.

    Raises :class:`ExtensionRefusal` when an extension under ``on_error = "fail"`` fails or the
    ``[extensions]`` configuration is invalid; the session must then not start.
    """
    if not config.get("extensions"):
        return
    try:
        extensions = read_extensions(config)
    except ValueError as exc:
        reason = f"{exc}; launch stopped"
        reporter.error(_OP, reason)
        raise ExtensionRefusal(reason) from exc
    where = f"; output in {log_path}" if log_path is not None else ""
    for extension in extensions:
        try:
            _run(extension, reporter)
        except _ExtensionError as exc:
            if extension.on_error == "warn":
                reporter.warning(_OP, f'{extension.name}: {exc}{where}; continuing launch (on_error = "warn")')
                continue
            reason = f'{extension.name}: {exc}{where}; launch stopped (on_error = "fail")'
            reporter.error(_OP, reason)
            raise ExtensionRefusal(reason) from exc
