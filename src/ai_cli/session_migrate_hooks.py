"""External rekey hooks for ``ai migrate-session``.

Tools outside this package can keep state keyed by a session name. Each one joins a
migration by dropping a TOML file into ``<config dir>/migrate-session.d/``::

    name = "notes"
    argv = ["python3", "~/bin/notes_rekey.py"]

``migrate-session`` runs every hook, sorted by filename, as
``argv + ["--old", OLD, "--new", NEW]``: with ``--dry-run`` while it plans (a
non-zero exit refuses the whole migration before anything is written, except under
``--finish``, where only that hook is skipped and reported as pending), then
without it after every core move (a non-zero exit is reported as ``FAILED``). A live
run that keeps NEW's own copy and archives OLD's prints ``migrate-archived: <path>`` for
each file it archived, and the migration ledger records it.
``adopt-session`` runs the same hooks under the same contract whenever an adoption
changes the session's name. The contract is documented in
``docs/reference/migrate-session-hooks.md``.

A host can also name the command that publishes a Beads store the migration re-keyed,
in ``<config dir>/migrate-session-publish.d/beads.toml``::

    argv = ["python3", "~/bin/publish_store.py", "--repo", "{store}"]
    timeout_s = 600   # optional; seconds one publish may run
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import config as _config
from .session_migrate_progress import Progress, timed

HOOKS_DIRNAME = "migrate-session.d"
PUBLISH_DIRNAME = "migrate-session-publish.d"
BEADS_PUBLISH_FILE = "beads.toml"
STORE_PLACEHOLDER = "{store}"
#: Seconds one publish may run when its file names no ``timeout_s``.
DEFAULT_PUBLISH_TIMEOUT_S = 600.0


class HookConfigError(ValueError):
    """A hook file that does not follow the contract."""


class HookRefusal(Exception):
    """A hook file is malformed, or a hook refused its ``--dry-run`` plan."""


@dataclass(frozen=True)
class Hook:
    name: str
    argv: tuple[str, ...]
    path: Path
    status: str = "pending"


@dataclass(frozen=True)
class BeadsPublish:
    """The configured command that publishes a re-keyed Beads store, and how long it may run."""

    argv: tuple[str, ...]
    timeout_s: float = DEFAULT_PUBLISH_TIMEOUT_S


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


def _program(argv: tuple[str, ...]) -> list[str]:
    program, *args = argv
    if program.startswith("~"):
        program = str(Path(program).expanduser())
    # Windows cannot execute a .py file directly, so a Python program runs under the
    # interpreter running `ai`, on every OS alike.
    prefix = [sys.executable] if program.lower().endswith(".py") else []
    return [*prefix, program, *args]


def _command(hook: Hook, old: str, new: str, dry_run: bool) -> list[str]:
    return [*_program(hook.argv), "--old", old, "--new", new, *(["--dry-run"] if dry_run else [])]


def load_beads_publish(directory: Path | None = None) -> BeadsPublish | None:
    """The configured Beads publish command, or None when this host configures none.

    Read at call time from ``<config dir>/migrate-session-publish.d/beads.toml``. Raises
    :class:`HookConfigError` for a file that is not TOML, has no ``argv`` list of strings,
    never names the ``{store}`` placeholder, or has a ``timeout_s`` that is not a positive
    number of seconds.
    """
    path = (
        directory if directory is not None else _config.get_xdg_config_home() / PUBLISH_DIRNAME
    ) / BEADS_PUBLISH_FILE
    if not path.is_file():
        return None
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise HookConfigError(f"publish file {path} is not readable TOML: {exc}") from exc
    argv = data.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv) or not argv[0]:
        raise HookConfigError(f"publish file {path}: field 'argv' must be a non-empty list of strings")
    if not any(STORE_PLACEHOLDER in arg for arg in argv):
        raise HookConfigError(f"publish file {path}: field 'argv' must name the {STORE_PLACEHOLDER} placeholder")
    timeout = data.get("timeout_s", DEFAULT_PUBLISH_TIMEOUT_S)
    if isinstance(timeout, bool) or not isinstance(timeout, int | float) or timeout <= 0:
        raise HookConfigError(f"publish file {path}: field 'timeout_s' must be a positive number of seconds")
    return BeadsPublish(tuple(argv), float(timeout))


def publish_command(argv: tuple[str, ...], store: Path) -> list[str]:
    """``argv`` with every ``{store}`` replaced by the store's directory, ready to run."""
    return _program(tuple(arg.replace(STORE_PLACEHOLDER, str(store)) for arg in argv))


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


def plan_hooks(
    old: str,
    new: str,
    *,
    cwd: Path | None = None,
    refused: list[HookRun] | None = None,
    progress: Progress | None = None,
) -> tuple[list[Hook], list[HookRun]]:
    """Load every hook and run each with ``--dry-run``, before anything is written.

    Raises :class:`HookRefusal` for a malformed hook file or the first hook that
    exits non-zero, quoting its stderr; the caller must then write nothing. When
    ``refused`` is given, a hook that exits non-zero is appended to it and left out of
    the plan instead, and the other hooks are still planned. Each hook's dry run is
    reported through ``progress`` as it starts and ends.
    """
    try:
        hooks = load_hooks()
    except HookConfigError as exc:
        raise HookRefusal(f"{exc} -- refusing before any write") from exc
    runs: list[HookRun] = []
    for hook in hooks:
        with timed(progress, f"hook {hook.name}", "planning (--dry-run)") as step:
            run = run_hook(hook, old, new, dry_run=True, cwd=cwd)
            step.outcome = "planned" if run.ok else f"refused (exit {run.returncode})"
        if not run.ok and refused is not None:
            refused.append(run)
            continue
        if not run.ok:
            raise HookRefusal(
                f"hook {hook.name} ({hook.path}) refused the migration (exit {run.returncode}): "
                f"{run.stderr or '(no stderr)'}"
            )
        runs.append(run)
    planned = []
    for run in runs:
        statuses = [line.removeprefix("migrate-status: ") for line in run.lines if line.startswith("migrate-status: ")]
        if statuses and (len(statuses) != 1 or statuses[0] not in {"done", "pending", "partial"}):
            raise HookRefusal(f"hook {run.hook.name} returned an invalid migrate-status")
        planned.append(replace(run.hook, status=statuses[0] if statuses else "pending"))
    return planned, runs


def run_hooks(
    hooks: list[Hook], old: str, new: str, *, cwd: Path | None = None, progress: Progress | None = None
) -> list[HookRun]:
    """Run every planned hook live, in order; one failure never stops the rest.

    Each run is reported through ``progress`` as it starts and ends.
    """
    runs = []
    for hook in [hook for hook in hooks if hook.status != "done"]:
        with timed(progress, f"hook {hook.name}", "running") as step:
            run = run_hook(hook, old, new, dry_run=False, cwd=cwd)
            step.outcome = f"{'ran' if run.ok else 'failed'} (exit {run.returncode})"
        runs.append(run)
    return runs


#: The line a live run prints once per file it archived, its path after the prefix.
ARCHIVED_PREFIX = "migrate-archived: "


def archived_paths(run: HookRun) -> dict[str, str]:
    """``{archived path: its line}`` for each ``migrate-archived:`` line of a live run."""
    return {line.removeprefix(ARCHIVED_PREFIX).strip(): line for line in run.lines if line.startswith(ARCHIVED_PREFIX)}


def refusal_warning(run: HookRun) -> str:
    """The ``NOT run`` line for a hook a ``--finish`` skipped because its ``--dry-run`` refused."""
    return (
        f"hook {run.hook.name} ({run.hook.path}) NOT run: it refused its preflight (exit {run.returncode}): "
        f"{run.stderr or '(no stderr)'} -- pending; re-run the same command to finish"
    )


def failure_warning(run: HookRun) -> str:
    """The ``FAILED`` line for a hook that exited non-zero on its live run."""
    return (
        f"hook {run.hook.name} ({run.hook.path}) FAILED (exit {run.returncode}): {run.stderr or '(no stderr)'} "
        f"-- re-run the same migrate-session command to finish"
    )
