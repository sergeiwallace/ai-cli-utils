"""Chief-of-staff session launch support (``ai cos``).

A chief-of-staff is one coordinating Claude Code session per machine that talks to the
user, dispatches to the other ("VP") sessions and surfaces their blockers. It runs as
an ordinary interactive session whose chief home (``FM_HOME``) is a per-machine
state directory outside every repository, seeded by the environment's installer:

    <[chief_of_staff] homes_dir>/<machine-key>

The names that tie a chief to one environment are configuration, read from the
``[chief_of_staff]`` section of ``config.toml`` at call time and never guessed:
``project`` (the repository every chief launches into), ``session_name`` (the session-name
segment that marks a chief), ``instance_key_prefix`` (the registry's chief instance key is
``<prefix>/<machine-key>``) and ``homes_dir`` (relative to ``$XDG_STATE_HOME`` unless
absolute). A launch that needs one that is unset refuses and names it. Two optional lists
add synonyms for the built-in names: ``command_aliases`` (extra subcommands that are
``ai cos``) and ``flag_aliases`` (extra long flags that are ``--chief``).

This module owns the launch-side bookkeeping around that home:

* resolving and validating the home (never guessing: a missing home or a home without
  its transport policy refuses the launch),
* the projected registry (``registry.json``, schema 2: chief identity, generation,
  routes; ``vp_sessions`` preserved untouched), and
* the registration record (``state/chief-session.json``) that other sessions read at
  startup to learn which agent to message and whether it is running.

Nothing here starts a process; ``main.py`` composes these with the ordinary Claude
launch path after exporting ``FM_HOME`` and ``AI_SESSION_ROLE=chief-of-staff``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from . import config as _config

try:
    import fcntl as _fcntl
except ImportError:  # Platform-specific: Windows has no fcntl, so the registry is not locked there.
    _fcntl = None

MACHINE_KEY_ENV = "AI_MACHINE_ID"
ROLE_ENV = "AI_SESSION_ROLE"
ROLE = "chief-of-staff"
FM_HOME_ENV = "FM_HOME"
SETTINGS_SECTION = "chief_of_staff"
REGISTRY_SCHEMA_VERSION = 2
REGISTRATION_SCHEMA_VERSION = 1
# The installer seeds these; a home without them has no transport policy and must not
# launch a chief that would dispatch against an unknown order.
REQUIRED_HOME_FILES = ("config/message-transports.json",)
_MACHINE_KEY = re.compile(r"^[A-Za-z0-9._-]+$")
_ALIAS_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class ChiefOfStaffError(Exception):
    """A launch-time refusal; the message names the remedy."""


def setting(key: str) -> str:
    """``[chief_of_staff] <key>`` from config.toml, read at call time; "" when unset."""
    section = _config.load_config().get(SETTINGS_SECTION)
    value = section.get(key) if isinstance(section, dict) else None
    return value.strip() if isinstance(value, str) else ""


def alias_setting(key: str) -> tuple[str, ...]:
    """``[chief_of_staff] <key>``: a list of extra names, read at call time; () when unset.

    Command routing reads it on every ``ai`` invocation, dry runs and session scripts
    included, so it reads config.toml without creating it or registering the machine.
    """
    section = _config.load_config(read_only=True).get(SETTINGS_SECTION)
    value = section.get(key) if isinstance(section, dict) else None
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(name, str) and _ALIAS_NAME.match(name) for name in value):
        raise ChiefOfStaffError(
            f'{SETTINGS_SECTION}.{key} must be a list of bare names such as ["mychief"] (letters, digits, '
            f"'-' and '_', no leading dash) in {_config.config_file_text()}"
        )
    return tuple(dict.fromkeys(value))


def required_setting(key: str) -> str:
    """``[chief_of_staff] <key>``, or a refusal naming the setting when it is unset."""
    value = setting(key)
    if not value:
        raise ChiefOfStaffError(
            f'{SETTINGS_SECTION}.{key} is not set: add {key} = "..." under [{SETTINGS_SECTION}] in '
            f"{_config.config_file_text()}; a chief-of-staff launch does not guess it"
        )
    return value


def _session_name() -> str:
    """The configured chief session-name segment, casefolded; "" when none is configured."""
    return setting("session_name").casefold()


def _chief_segment(session_name: str) -> re.Pattern[str]:
    return re.compile(rf"(?:^|-){re.escape(session_name)}(?:-|$)")


def is_chief_positional(name: str) -> bool:
    """True when a positional session name alone asks for the chief launch.

    That is the configured session name, ``<name>-N``, or a full chief session name ending
    in one (``myproject-box-chief-1``, ``c-r-myproject-box-chief-1`` for session name
    ``chief``). With no session name configured, no positional name is a chief launch.
    """
    session_name = _session_name()
    if not name or not session_name:
        return False
    return bool(re.search(rf"(?:^|-){re.escape(session_name)}(?:-\d+)?$", name.casefold()))


def names_a_chief(name: str) -> bool:
    """True when ``name`` carries the configured chief session-name segment anywhere."""
    session_name = _session_name()
    return bool(name) and bool(session_name) and bool(_chief_segment(session_name).search(name.casefold()))


def chief_session_name(name: str) -> str:
    """The session name a chief launch asks for: ``<session name>[-<suffix>]``.

    A name that already carries the session-name segment is a chief session name
    (``myproject-box-chief-1``, ``c-r-myproject-box-chief-1``, ``chief-review``) and is
    returned unchanged, so the slot a remote dispatcher allocated and the session name an
    iTerm2 relaunch replays both reach the same session instead of nesting a second
    segment in front of it.
    """
    session_name = required_setting("session_name").casefold()
    if not name:
        return session_name
    if names_a_chief(name):
        return name
    return f"{session_name}-{name}"


def chief_slot(name: str) -> str:
    """The ``<session name>[-...]`` slot a chief session, agent or requested name carries.

    Prefix-agnostic (``c-r-myproject-box-chief-1``, ``myproject-box-chief-1`` and
    ``chief-1`` are all ``chief-1``) and sanitized the way a session slot is, so a
    request and a registration compare equal exactly when they name the same chief.
    """
    folded = name.casefold()
    session_name = _session_name()
    match = _chief_segment(session_name).search(folded) if session_name else None
    tail = folded[match.start() :] if match else folded
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9_-]+", "-", tail)).strip("-")


def is_numbered_slot(slot: str) -> bool:
    """``chief-1`` names one chief; ``chief`` / ``chief-review`` ask for *the* chief."""
    return slot.rsplit("-", 1)[-1].isdigit()


def same_chief(requested_slot: str, running_slot: str) -> bool:
    """Whether a request reaches the running chief rather than asking for a second one.

    A numbered request must name the running chief's slot exactly; an unnumbered one
    (``ai cos``, ``ai c --chief``, ``ai c <session name>``) means whichever chief of that
    name is running, whatever index it was given.
    """
    if is_numbered_slot(requested_slot):
        return requested_slot == running_slot
    return re.sub(r"-\d+$", "", running_slot) == requested_slot


def tmux_panes_all_dead(target: str) -> bool:
    """Whether ``target`` is a managed tmux session whose every pane has exited.

    The same test the ordinary launch applies before reattaching: such a session shows
    frozen output forever, so the chief in it is resumed rather than attached to.
    """
    from . import stale_session_reaper

    adapter = stale_session_reaper.SubprocessTmuxAdapter()
    try:
        candidate = next((item for item in adapter.sessions() if item.session_name == target), None)
        return candidate is not None and adapter.capture_fingerprint(candidate) is not None
    except (OSError, RuntimeError, ValueError):
        return False


def chief_homes_root() -> Path | None:
    """The directory holding one chief home per machine key; None when none is configured.

    ``[chief_of_staff] homes_dir``, resolved under ``$XDG_STATE_HOME`` unless it is absolute
    or starts with ``~``.
    """
    configured = setting("homes_dir")
    if not configured:
        return None
    path = Path(configured).expanduser()
    if path.is_absolute():
        return path
    return _config.resolve_base_dir("XDG_STATE_HOME", Path.home() / ".local" / "state") / path


def resolve_chief_home(fm_home: str | None, machine_key: str | None, env: dict | None = None) -> Path:
    """Return the chief home: an explicit ``fm_home``, else the per-machine state path.

    The machine key comes from ``machine_key`` or ``$AI_MACHINE_ID`` and must be a plain
    token, because it becomes a path component. With neither, the one home the installer
    seeded on this machine is used: a non-interactive shell (the remote side of ``-R``
    runs in one) skips an rc file that exports the key, but the home it named is still on
    disk. Several seeded homes are a refusal, never a pick.
    """
    environ = os.environ if env is None else env
    if fm_home:
        return Path(fm_home).expanduser()
    root = chief_homes_root()
    if root is None:
        raise ChiefOfStaffError(
            f'{SETTINGS_SECTION}.homes_dir is not set: add homes_dir = "..." under [{SETTINGS_SECTION}] in '
            f"{_config.config_file_text()}, or name the home with -H/--fm-home"
        )
    key = machine_key or environ.get(MACHINE_KEY_ENV, "")
    if not key:
        seeded = sorted(path.name for path in _seeded_homes(root))
        if len(seeded) == 1:
            return root / seeded[0]
        found = f"; {root} holds several homes ({', '.join(seeded)}), so name one" if seeded else ""
        raise ChiefOfStaffError(
            f"no machine key{found}: pass -k/--machine-key or export {MACHINE_KEY_ENV} "
            "(your environment's installer provisions it), or name the home with -H/--fm-home"
        )
    if not _MACHINE_KEY.match(key):
        raise ChiefOfStaffError(
            f"machine key {key!r} is not a plain token ([A-Za-z0-9._-]); refusing to use it as a path"
        )
    return root / key


def _seeded_homes(root: Path) -> list[Path]:
    """The chief homes under ``root`` that carry the installer's required files."""
    try:
        candidates = [path for path in root.iterdir() if _MACHINE_KEY.match(path.name) and path.is_dir()]
    except OSError:
        return []
    return [path for path in candidates if all((path / name).is_file() for name in REQUIRED_HOME_FILES)]


def validate_chief_home(home: Path) -> None:
    """Refuse a home that does not exist or lacks its transport policy."""
    if not home.is_dir():
        raise ChiefOfStaffError(
            f"chief home {home} does not exist; provision it first (your environment's installer seeds it), "
            "then re-run `ai cos`"
        )
    missing = [name for name in REQUIRED_HOME_FILES if not (home / name).is_file()]
    if missing:
        raise ChiefOfStaffError(
            f"chief home {home} is missing {', '.join(missing)}; refusing to launch a chief without its transport policy"
        )


def registry_path(home: Path) -> Path:
    return home / "registry.json"


@contextmanager
def _registry_lock(home: Path) -> Iterator[None]:
    """Hold the exclusive advisory lock every writer of ``registry.json`` takes.

    The contract: ``flock(LOCK_EX)`` on ``<home>/registry.json.lock`` (opened
    ``O_CREAT | O_RDWR`` at 0600), released with ``LOCK_UN`` and then closed. Without
    fcntl, or before the home exists, no lock is taken. Readers never lock.
    """
    if _fcntl is None or not home.is_dir():
        yield
        return
    fd = os.open(home / "registry.json.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        _fcntl.flock(fd, _fcntl.LOCK_EX)
        try:
            yield
        finally:
            _fcntl.flock(fd, _fcntl.LOCK_UN)
    finally:
        os.close(fd)


def registration_path(home: Path) -> Path:
    return home / "state" / "chief-session.json"


def _read_json_object(path: Path, what: str) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ChiefOfStaffError(f"{what} {path} is unreadable or not JSON ({exc}); inspect or remove it") from exc
    if not isinstance(data, dict):
        raise ChiefOfStaffError(f"{what} {path} is not a JSON object; inspect or remove it")
    return data


def read_registration(home: Path) -> dict | None:
    """The current chief registration, ``None`` when nothing is registered."""
    return _read_json_object(registration_path(home), "chief registration")


def tmux_has_session(target: str) -> bool:
    """Whether a tmux session named exactly ``target`` exists (False without tmux)."""
    if not target or shutil.which("tmux") is None:
        return False
    try:
        result = subprocess.run(["tmux", "has-session", "-t", f"={target}"], capture_output=True, check=False)
    except OSError:
        return False
    return result.returncode == 0


def registration_is_live(registration: dict | None, has_session: Callable[[str], bool] | None = None) -> bool:
    """A registration is live when its recorded tmux target still exists.

    A bare-mode chief records no tmux target and is therefore never reported live: the
    launcher cannot tell, so it does not claim to. The probe is resolved at call time
    (not bound as a default) so a test can replace ``tmux_has_session`` on the module.
    """
    if not registration:
        return False
    target = registration.get("tmux_target") or ""
    probe = has_session or tmux_has_session
    return bool(target) and probe(target)


def _write_private_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)


def write_registration(
    home: Path,
    *,
    native_agent_name: str,
    tmux_target: str,
    machine_key: str,
    machine_name: str,
    launch_cwd: str,
    instance_key_prefix: str,
    now: datetime | None = None,
) -> dict:
    """Record this launch in the registry and the registration file; return the registration.

    ``registry.json`` keeps its ``vp_sessions`` and any unknown fields; the chief fields are
    replaced and ``chief_generation`` / ``chief_routes.route_revision`` are incremented so a
    stale reader can tell it is stale.
    """
    stamp = (now or datetime.now(UTC)).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    # <session>@<host>: the only session identifier the registry's readers accept.
    session = f"{native_agent_name}@{machine_name}"
    # The save must follow the load it changed, so both happen under the writers' lock.
    with _registry_lock(home):
        registry = _read_json_object(registry_path(home), "chief registry") or {}
        generation = int(registry.get("chief_generation") or 0) + 1
        # Narrow once into a local: two separate ``.get`` calls are two separate
        # expressions, so neither the reader nor the type checker can tell that the
        # ``isinstance`` guard applies to the value actually used.
        stored_routes = registry.get("chief_routes")
        routes: dict = stored_routes if isinstance(stored_routes, dict) else {}
        registry.update(
            {
                "schema_version": REGISTRY_SCHEMA_VERSION,
                "chief_of_staff_session": session,
                "machine_key": machine_key,
                "machine_name": machine_name,
                "chief_instance_key": f"{instance_key_prefix}/{machine_key}",
                "chief_home": str(home),
                "launch_cwd": launch_cwd,
                "chief_generation": generation,
                "chief_routes": {
                    "route_revision": int(routes.get("route_revision") or 0) + 1,
                    "native_agent_name": native_agent_name,
                    # Mail and typed-input routes are configured by their own provisioning;
                    # unknown here, and never invented.
                    "agent_mail": routes.get("agent_mail"),
                    "fm_send": routes.get("fm_send"),
                },
            }
        )
        registry.setdefault("vp_sessions", [])
        _write_private_json(registry_path(home), registry)

    registration = {
        "schema_version": REGISTRATION_SCHEMA_VERSION,
        "session": session,
        "native_agent_name": native_agent_name,
        "tmux_target": tmux_target,
        "machine_key": machine_key,
        "machine_name": machine_name,
        "chief_generation": generation,
        "launch_cwd": launch_cwd,
        "launcher_pid": os.getpid(),
        "registered_at": stamp,
    }
    _write_private_json(registration_path(home), registration)
    return registration
