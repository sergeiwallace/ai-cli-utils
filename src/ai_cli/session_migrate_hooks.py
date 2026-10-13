"""External rekey hooks for ``ai session-migrate``.

Tools outside this package can keep state keyed by a session name. Each one joins a
migration by dropping a TOML file into ``<config dir>/session-migrate.d/``::

    name = "notes"
    argv = ["python3", "~/bin/notes_rekey.py"]

``session-migrate`` runs every hook, sorted by filename, as
``argv + ["--old", OLD, "--new", NEW]``: with ``--dry-run`` while it plans (a
non-zero exit refuses the whole migration before anything is written), then
without it after every core move (a non-zero exit is reported as ``FAILED``). The
contract is documented in ``docs/reference/session-migrate-hooks.md``.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import config as _config

HOOKS_DIRNAME = "session-migrate.d"


class HookConfigError(ValueError):
    """A hook file that does not follow the contract."""


@dataclass(frozen=True)
class Hook:
    name: str
    argv: tuple[str, ...]
    path: Path


@dataclass
class HookRun:
    """One invocation of a hook: its exit status, stdout lines and stderr."""

    hook: Hook
    returncode: int
    lines: list[str] = field(default_factory=list)
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def hooks_dir() -> Path:
    return _config.get_xdg_config_home() / HOOKS_DIRNAME


def load_hooks(directory: Path | None = None) -> list[Hook]:
    """Every hook declared in ``directory`` (default :func:`hooks_dir`), sorted by filename.

    Read at call time. An absent directory declares no hooks. Raises
    :class:`HookConfigError` naming the file and the field for a file that does not
    follow the contract.
    """
    root = directory if directory is not None else hooks_dir()
    if not root.is_dir():
        return []
    hooks: list[Hook] = []
    for path in sorted(root.glob("*.toml")):
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise HookConfigError(f"hook file {path} is not readable TOML: {exc}") from exc
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            raise HookConfigError(f"hook file {path}: field 'name' must be a non-empty string")
        argv = data.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv) or not argv[0]:
            raise HookConfigError(f"hook file {path}: field 'argv' must be a non-empty list of strings")
        hooks.append(Hook(name=name.strip(), argv=tuple(argv), path=path))
    return hooks


def _command(hook: Hook, old: str, new: str, dry_run: bool) -> list[str]:
    program, *args = hook.argv
    if program.startswith("~"):
        program = str(Path(program).expanduser())
    # Windows cannot execute a .py file directly, so a Python program runs under the
    # interpreter running `ai`, on every OS alike.
    prefix = [sys.executable] if program.lower().endswith(".py") else []
    return [*prefix, program, *args, "--old", old, "--new", new, *(["--dry-run"] if dry_run else [])]


def run_hook(hook: Hook, old: str, new: str, *, dry_run: bool, cwd: Path | None = None) -> HookRun:
    """Run ``hook`` once; a program that cannot be started is a failed run, never an exception."""
    try:
        proc = subprocess.run(
            _command(hook, old, new, dry_run),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return HookRun(hook=hook, returncode=127, stderr=f"could not start {hook.argv[0]}: {exc}")
    lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    return HookRun(hook=hook, returncode=proc.returncode, lines=lines, stderr=(proc.stderr or "").strip())
