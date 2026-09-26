"""Chief-of-staff session launch support (``ai cos``).

A chief-of-staff is one coordinating Claude Code session per machine that talks to the
user, dispatches to the other ("VP") sessions and surfaces their blockers. It runs as
an ordinary interactive session whose Firstmate home (``FM_HOME``) is a per-machine
state directory outside every repository, seeded by the environment's installer:

    $XDG_STATE_HOME/firstmate/chief-of-staff/<machine-key>

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
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from . import config as _config

MACHINE_KEY_ENV = "AI_MACHINE_ID"
ROLE_ENV = "AI_SESSION_ROLE"
ROLE = "chief-of-staff"
FM_HOME_ENV = "FM_HOME"
SESSION_NAME = "cos"
REGISTRY_SCHEMA_VERSION = 2
REGISTRATION_SCHEMA_VERSION = 1
# The installer seeds these; a home without them has no transport policy and must not
# launch a chief that would dispatch against an unknown order.
REQUIRED_HOME_FILES = ("config/message-transports.json",)
_MACHINE_KEY = re.compile(r"^[A-Za-z0-9._-]+$")


class ChiefOfStaffError(Exception):
    """A launch-time refusal; the message names the remedy."""


def resolve_chief_home(fm_home: str | None, machine_key: str | None, env: dict | None = None) -> Path:
    """Return the chief home: an explicit ``fm_home``, else the per-machine state path.

    The machine key comes from ``machine_key`` or ``$AI_MACHINE_ID`` and must be a plain
    token, because it becomes a path component.
    """
    environ = os.environ if env is None else env
    if fm_home:
        return Path(fm_home).expanduser()
    key = machine_key or environ.get(MACHINE_KEY_ENV, "")
    if not key:
        raise ChiefOfStaffError(
            f"no machine key: pass -k/--machine-key or export {MACHINE_KEY_ENV} "
            "(your environment's installer provisions it), or name the home with -H/--fm-home"
        )
    if not _MACHINE_KEY.match(key):
        raise ChiefOfStaffError(
            f"machine key {key!r} is not a plain token ([A-Za-z0-9._-]); refusing to use it as a path"
        )
    state = _config.resolve_base_dir("XDG_STATE_HOME", Path.home() / ".local" / "state")
    return state / "firstmate" / "chief-of-staff" / key


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
    tmp.replace(path)


def write_registration(
    home: Path,
    *,
    native_agent_name: str,
    tmux_target: str,
    machine_key: str,
    machine_name: str,
    launch_cwd: str,
    now: datetime | None = None,
) -> dict:
    """Record this launch in the registry and the registration file; return the registration.

    ``registry.json`` keeps its ``vp_sessions`` and any unknown fields; the chief fields are
    replaced and ``chief_generation`` / ``chief_routes.route_revision`` are incremented so a
    stale reader can tell it is stale.
    """
    stamp = (now or datetime.now(UTC)).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    registry = _read_json_object(registry_path(home), "chief registry") or {}
    generation = int(registry.get("chief_generation") or 0) + 1
    routes = registry.get("chief_routes") if isinstance(registry.get("chief_routes"), dict) else {}
    session = f"{machine_name}/{native_agent_name}"
    registry.update(
        {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "chief_of_staff_session": session,
            "machine_key": machine_key,
            "machine_name": machine_name,
            "chief_instance_key": f"firstmate-chief/{machine_key}",
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
