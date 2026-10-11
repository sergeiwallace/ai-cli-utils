"""iTerm2 integration: color slots, profile emit, tmux config.

Depends on: config.py
"""

import contextlib
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import portalocker

from .config import get_xdg_config_home, get_xdg_state_home

# Default iTerm2 config written to ~/.config/ai-cli-utils/iterm2.toml on first use.
# Also shipped as docs/reference/iterm2-defaults.toml for documentation.
_DEFAULT_ITERM2_CONFIG = """\
## ai-cli-utils iTerm2 integration configuration
## Edit this file at ~/.config/ai-cli-utils/iterm2.toml to customize.

[iterm2]
## Master switch — set false to disable all iTerm2 integration
enabled = true

[iterm2.tab_title]
## Include type symbol (* for CC, ✦ for Gemini, π for Pi) in tab and pane titles
show_type_symbol = false
## Include status symbol (▶ ✓ ✗ ↻ ⏸) in tab and pane titles
show_status_symbol = false

[iterm2.color]
## Set tab/pane background color on session launch
enabled = true
## Use lease-file-based collision-free slot assignment (recommended).
## Set false to use simple session-number modulo (may collide across projects).
collision_avoidance = true

[iterm2.palette]
## Named tab background colors available for auto-rotation.
## Add your own entries — they are included in the rotation pool.
## Icon tint is auto-derived from each tab color via HSL color theory.
##
## Standard colors:
red         = "#e74c3c"
orange      = "#e67e22"
yellow      = "#f0b429"
green       = "#2ecc71"
teal        = "#1abc9c"
sky_blue    = "#039be5"
blue        = "#1e88e5"
purple      = "#5e35b1"
pink        = "#d81b60"
cyan        = "#00acc1"
##
## Extended palette:
deep_orange = "#ff5722"
lime        = "#7cb342"
indigo      = "#3949ab"
rose        = "#f43f5e"
amber       = "#ffb300"
emerald     = "#059669"
violet      = "#7c3aed"
slate       = "#475569"
warm_white  = "#f5f0e8"
charcoal    = "#2d2d2d"
##
## Add custom colors below. Use any name; hex value required.
## Example: my_color = "#a259ff"

[iterm2.defaults]
## Settings applied to all sessions unless overridden by a project or session block.
## tab_color: palette color name (e.g. "blue") or omit for auto-rotation
## icon_color: hex tint for the Claude/Gemini icon; omit to auto-derive from tab_color
# tab_color  = "blue"
# icon_color = "#ffffff"

## Per-project overrides — add one block per project directory name.
## Falls back to lowest free slot if the preferred color is already occupied.
##
## [iterm2.projects.myproject]
## tab_color  = "teal"
## icon_color = "#ffd700"   # omit to auto-derive

## Per-session overrides — add one block per tmux session name.
##
## [iterm2.sessions."c-myapp-1"]
## tab_color  = "purple"
## icon_color = "#ffd700"   # omit to auto-derive

[iterm2.base_profiles]
## Parent iTerm2 profile each generated session profile inherits from.
## Default (recommended): every session parents to the built-in "Default" and
## the generator supplies name/color/icon/keymap/Semantic-History itself — no
## hand-maintained profiles required. Override with the exact name of a custom
## profile only if you deliberately want a session type to inherit from it.
cc         = "Default"
gemini     = "Default"
pi         = "Default"
codex      = "Default"
shell      = "Default"
chrome     = "Default"
caffeinate = "Default"
ssh        = "Default"

# [iterm2.persistence]
## Master switch for session tracking and restore. false = write nothing, restore nothing.
## Reference: docs/iterm2-persistence.md in the ai-cli-utils repository.
# enabled = true

# [iterm2.persistence.tracking]
## Record each `ai c` launch in the session registry (~/.local/state/ai-cli-utils/iterm2-sessions.json)
# enabled = true
## Record local tmux sessions
# include_local = true
## Record `ai c -R` sessions
# include_remote = true
## Session-name globs; empty = every session
# include = []
## Session-name globs removed after include
# exclude = []
## Re-read every record's window/tab/pane after each `ai c` launch (one AppleScript pass)
# refresh_on_launch = true

# [iterm2.persistence.restore]
## Opt in per machine; the default never opens windows
# enabled = false
## Run when iTerm2 starts (needs the startup hook) ...
# on_startup = false
## ... and/or on demand
# on_demand = true
## "arrangement" | "sessions" | "arrangement+sessions"
# mode = "arrangement+sessions"
## Saved-arrangement name to restore first; empty = none
# default_arrangement = ""
## On demand only: let `it2 window arrange restore` open the arrangement; false = print the menu path
# use_it2 = true
## Place a session into its recorded pane of the open arrangement when that pane is an idle shell
# fill_arrangement = true
# include_local = true
# include_remote = true
# include = []
# exclude = []
## Remote aliases allowed to re-dial; empty = any recorded alias
# remote_hosts = []
## 0 = unlimited, else the most recent N
# max_sessions = 0
## Pause between launches so several ssh dials do not race one credential refresh
# stagger_seconds = 1.0
## true = print the plan and ask before touching iTerm2 (never at startup)
# confirm = false

# [iterm2.persistence.restore.relaunch]
## false = `restore --startup` relaunches nothing and prints why
# on_terminal_reopen = true

# [iterm2.persistence.restore.relaunch.scenarios]
## Relaunch a session whose previous run ended this way; false = list it as skipped with its cause.
## A cause the restore could not decide ("unknown") follows terminal_quit_or_crash.
# host_reboot = true
# terminal_quit_or_crash = true
## A session exited by hand is not resurrected by the terminal reopening
# manual_exit = false
"""

#: Every ``[iterm2.persistence]`` key and its default. A nested dict is a sub-table; the
#: type of each default is the type the key must hold. Kept equal to the commented-out
#: template tables in ``_DEFAULT_ITERM2_CONFIG`` (a test pins it); those ship commented so
#: an installer-managed block or the user's own edit is the only live definition.
_PERSISTENCE_DEFAULTS: dict = {
    "enabled": True,
    "tracking": {
        "enabled": True,
        "include_local": True,
        "include_remote": True,
        "include": [],
        "exclude": [],
        "refresh_on_launch": True,
    },
    "restore": {
        "enabled": False,
        "on_startup": False,
        "on_demand": True,
        "mode": "arrangement+sessions",
        "default_arrangement": "",
        "use_it2": True,
        "fill_arrangement": True,
        "include_local": True,
        "include_remote": True,
        "include": [],
        "exclude": [],
        "remote_hosts": [],
        "max_sessions": 0,
        "stagger_seconds": 1.0,
        "confirm": False,
        "relaunch": {
            "on_terminal_reopen": True,
            "scenarios": {"host_reboot": True, "terminal_quit_or_crash": True, "manual_exit": False},
        },
    },
}

_RESTORE_MODES = ("arrangement", "sessions", "arrangement+sessions")


class PersistenceConfigError(ValueError):
    """A ``[iterm2.persistence]`` key is unknown or holds a value of the wrong type."""


def _iterm2_state_dir() -> Path:
    """Return the XDG state dir for iTerm2 session-tracking files, creating it if needed."""
    d = get_xdg_state_home() / "iterm2"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _is_iterm2() -> bool:
    return os.environ.get("LC_TERMINAL") == "iTerm2" or os.environ.get("TERM_PROGRAM") == "iTerm.app"


def _load_iterm2_config() -> dict:
    """Load iTerm2 config from ~/.config/ai-cli-utils/iterm2.toml, writing defaults on first use."""
    config_path = get_xdg_config_home() / "iterm2.toml"
    if not config_path.exists():
        get_xdg_config_home().mkdir(parents=True, exist_ok=True)
        config_path.write_text(_DEFAULT_ITERM2_CONFIG, encoding="utf-8")
    try:
        with config_path.open("rb") as f:
            return tomllib.load(f)
    except Exception:
        try:
            return tomllib.loads(_DEFAULT_ITERM2_CONFIG)
        except Exception:
            return {}


def _persistence_value_error(section: str, key: str, default: object, value: object) -> str | None:
    """Return why ``value`` cannot stand in for ``default``, or None when it can."""
    if isinstance(default, bool):
        ok, expected = isinstance(value, bool), "a boolean"
    elif isinstance(default, list):
        ok = isinstance(value, list) and all(isinstance(item, str) for item in value)
        expected = "a list of strings"
    elif isinstance(default, str):
        ok, expected = isinstance(value, str), "a string"
    elif isinstance(default, float):
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
        expected = "a non-negative number"
    else:
        ok = isinstance(value, int) and not isinstance(value, bool) and value >= 0
        expected = "a non-negative integer"
    if ok:
        return None
    return f"[{section}] {key} must be {expected}, got {type(value).__name__} {value!r}"


def _resolve_persistence_table(raw: object, defaults: dict, section: str) -> dict:
    if not isinstance(raw, dict):
        raise PersistenceConfigError(f"[{section}] must be a table, got {type(raw).__name__} {raw!r}")
    unknown = sorted(set(raw) - set(defaults))
    if unknown:
        raise PersistenceConfigError(f"[{section}] has no key {unknown[0]!r}; valid keys: {', '.join(defaults)}")
    resolved: dict = {}
    for key, default in defaults.items():
        if isinstance(default, dict):
            resolved[key] = _resolve_persistence_table(raw.get(key, {}), default, f"{section}.{key}")
            continue
        value = raw.get(key, default)
        problem = _persistence_value_error(section, key, default, value)
        if problem:
            raise PersistenceConfigError(problem)
        resolved[key] = list(value) if isinstance(value, list) else value
    if "mode" in resolved and resolved["mode"] not in _RESTORE_MODES:
        raise PersistenceConfigError(
            f"[{section}] mode must be one of {', '.join(repr(m) for m in _RESTORE_MODES)}, got {resolved['mode']!r}"
        )
    return resolved


def load_persistence_config() -> dict:
    """Return the resolved ``[iterm2.persistence]`` settings, read from iterm2.toml on this call.

    Absent keys take their documented defaults; a key of the wrong type or an unknown key
    raises :class:`PersistenceConfigError` naming it, rather than falling back silently.
    """
    raw = _load_iterm2_config().get("iterm2", {})
    table = raw.get("persistence", {}) if isinstance(raw, dict) else {}
    return _resolve_persistence_table(table, _PERSISTENCE_DEFAULTS, "iterm2.persistence")


def _iterm2_palette(cfg: dict) -> list[tuple[str, str]]:
    """Return ordered list of (name, hex_no_hash) from config palette."""
    raw = cfg.get("iterm2", {}).get("palette", {})
    return [(name, val.lstrip("#")) for name, val in raw.items()]


def _resolve_iterm2_config(cfg: dict, ai_name: str, project_name: str = "") -> dict:
    """Resolve per-session iTerm2 config by merging defaults → project → session overrides.

    Returns a dict with the resolved keys (e.g. ``{"tab_color": "blue", "icon_color": "#4a7535"}``).
    Absent keys are simply missing — callers should use ``.get()``.

    Resolution order (later entries win):
    - ``[iterm2.defaults]`` — baseline for all sessions
    - ``[iterm2.projects.<project_name>]`` — project-level override
    - ``[iterm2.sessions.<ai_name>]`` — session-level override (highest priority)
    """
    iterm2 = cfg.get("iterm2", {})
    resolved: dict = {}
    resolved.update(iterm2.get("defaults", {}))
    if project_name:
        resolved.update(iterm2.get("projects", {}).get(project_name, {}))
    resolved.update(iterm2.get("sessions", {}).get(ai_name, {}))
    return resolved


def _assign_iterm2_color_slot(ai_name: str, engine: str, project_name: str = "") -> str | None:
    """Assign a collision-free tab color for this session.

    Returns the color hex string (e.g. "e74c3c") or None if not in iTerm2.
    Writes a PID-keyed lease entry to color-leases.json. Stale leases (dead PIDs)
    are pruned on each call so the pool stays clean across crashes.

    project_colors config can pin specific project/session names to a fixed
    palette color name; falls back to lowest free slot if that slot is occupied.
    """
    if not _is_iterm2():
        return None
    cfg = _load_iterm2_config()
    if not cfg.get("iterm2", {}).get("enabled", True):
        return None
    if not cfg.get("iterm2", {}).get("color", {}).get("enabled", True):
        return None

    palette = _iterm2_palette(cfg)
    if not palette:
        return None

    palette_names = [name for name, _ in palette]
    palette_dict = dict(palette)

    # Resolve preferred tab_color via defaults → project → session
    preferred_color_name = _resolve_iterm2_config(cfg, ai_name, project_name).get("tab_color")

    lease_file = _iterm2_state_dir() / "color-leases.json"
    lock_path = _iterm2_state_dir() / "color-leases.lock"

    with lock_path.open("w") as lock_fd:
        portalocker.lock(lock_fd, portalocker.LOCK_EX)
        try:
            leases: dict = {}
            if lease_file.exists():
                try:
                    leases = json.loads(lease_file.read_text()).get("leases", {})
                except Exception:
                    leases = {}

            # Prune stale leases (dead PIDs)
            import psutil

            active: dict = {}
            for name, info in leases.items():
                pid = info.get("pid", 0)
                if psutil.pid_exists(pid):
                    active[name] = info

            occupied = {info["slot"] for info in active.values()}

            # Fallback when all slots occupied: distribute by name hash so
            # different sessions get different colors instead of all piling on slot 0.
            import hashlib as _hashlib

            _fallback_idx = int(_hashlib.md5(ai_name.encode()).hexdigest(), 16) % len(palette)

            use_avoidance = cfg.get("iterm2", {}).get("color", {}).get("collision_avoidance", True)
            if use_avoidance:
                # Try preferred color first (project_colors pin)
                if preferred_color_name and preferred_color_name in palette_names:
                    preferred_idx = palette_names.index(preferred_color_name)
                    if preferred_idx not in occupied:
                        slot_idx = preferred_idx
                    else:
                        slot_idx = next((i for i in range(len(palette)) if i not in occupied), _fallback_idx)
                else:
                    slot_idx = next((i for i in range(len(palette)) if i not in occupied), _fallback_idx)
            else:
                m = re.search(r"\d+$", ai_name)
                num = int(m.group()) if m else 1
                slot_idx = (num - 1) % len(palette)

            slot_name = palette_names[slot_idx]
            color_hex = palette_dict[slot_name]
            active[ai_name] = {"slot": slot_idx, "pid": os.getpid(), "ts": str(time.time())}
            lease_file.write_text(json.dumps({"leases": active}, indent=2))
        finally:
            portalocker.unlock(lock_fd)

    return color_hex


def _release_iterm2_color_slot(ai_name: str) -> None:
    """Remove the color lease for ai_name (called on session EXIT)."""
    lease_file = _iterm2_state_dir() / "color-leases.json"
    if not lease_file.exists():
        return
    lock_path = _iterm2_state_dir() / "color-leases.lock"
    with lock_path.open("w") as lock_fd:
        portalocker.lock(lock_fd, portalocker.LOCK_EX)
        try:
            leases: dict = {}
            with contextlib.suppress(Exception):
                leases = json.loads(lease_file.read_text()).get("leases", {})
            leases.pop(ai_name, None)
            lease_file.write_text(json.dumps({"leases": leases}, indent=2))
        finally:
            portalocker.unlock(lock_fd)


def _iterm2_session_type(engine: str) -> str:
    """Map engine string to icon_generator session_type."""
    return (
        "cc"
        if engine == "c"
        else "gemini"
        if engine == "g"
        else "pi"
        if engine == "p"
        else "codex"
        if engine == "cx"
        else "shell"
    )


def _set_iterm2_name_by_tty(tty: str, name: str) -> bool:
    """Set the Name of the iTerm2 session on physical terminal ``tty`` (macOS only).

    The controlling ``tty`` (e.g. ``/dev/ttys000``) is assigned by the OS to
    exactly one live iTerm2 pane, so matching on it renames precisely the pane
    the user is looking at — with no possibility of collision.  This is the
    authoritative replacement for GUID-based matching, which drifted whenever a
    stored ``ITERM_SESSION_ID`` was inherited or shared across re-attaches
    (AI-CLI-59 root cause).

    Returns True when a matching pane was found and renamed, else False.
    """
    if sys.platform != "darwin" or not tty:
        return False
    safe_tty = tty.replace("\\", "\\\\").replace('"', '\\"')
    safe_name = name.replace("\\", "\\\\").replace('"', '\\"')
    script = f"""tell application "iTerm2"
    repeat with w in windows
        repeat with t in tabs of w
            repeat with s in sessions of t
                try
                    if tty of s is "{safe_tty}" then
                        set name of s to "{safe_name}"
                        return "ok"
                    end if
                end try
            end repeat
        end repeat
    end repeat
    return "miss"
end tell"""
    result = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5, text=True, check=False)
    return "ok" in (result.stdout or "")


class Iterm2PaneLookupError(RuntimeError):
    """iTerm2 could not be asked for its panes; the message says why."""


# One pass over every window, tab and session. The running check comes first so a
# lookup never launches iTerm2. `tab` is spelled `character id 9` because inside the
# tell block `tab` names iTerm2's tab class.
_LIST_PANES_SCRIPT = """if application "iTerm2" is not running then return "not-running"
set sep to character id 9
set out to ""
tell application "iTerm2"
    set wIdx to 0
    repeat with w in windows
        set tIdx to 0
        repeat with t in tabs of w
            set sIdx to 0
            repeat with s in sessions of t
                try
                    set out to out & wIdx & sep & tIdx & sep & sIdx & sep & (tty of s) & sep & (unique id of s) & linefeed
                end try
                set sIdx to sIdx + 1
            end repeat
            set tIdx to tIdx + 1
        end repeat
        set wIdx to wIdx + 1
    end repeat
end tell
return out"""


def _iterm2_panes_by_tty(timeout: float) -> dict[str, dict]:
    """Map each iTerm2 pane's tty to its 0-based window/tab/pane and session UUID (macOS only).

    Windows are numbered in iTerm2's front-to-back order. Raises
    :class:`Iterm2PaneLookupError` when iTerm2 is not running, does not answer within
    ``timeout`` seconds, or osascript fails; never launches iTerm2.
    """
    if sys.platform != "darwin":
        raise Iterm2PaneLookupError("iTerm2 is macOS-only")
    try:
        result = subprocess.run(
            ["osascript", "-e", _LIST_PANES_SCRIPT], capture_output=True, timeout=timeout, text=True, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise Iterm2PaneLookupError(f"iTerm2 did not answer in {timeout:g}s") from exc
    except OSError as exc:
        raise Iterm2PaneLookupError(f"osascript could not run: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        raise Iterm2PaneLookupError(f"osascript exited {result.returncode}{f': {detail[-1]}' if detail else ''}")
    output = (result.stdout or "").strip()
    if output == "not-running":
        raise Iterm2PaneLookupError("iTerm2 is not running")
    panes: dict[str, dict] = {}
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 5 or not all(field.isdigit() for field in fields[:3]) or not fields[3]:
            continue
        window, tab, pane, tty, session_uuid = fields
        panes[tty] = {"session_uuid": session_uuid, "window": int(window), "tab": int(tab), "pane": int(pane)}
    return panes


def _iterm_pane_tty_for_tmux_session(tmux_session: str) -> str:
    """Return the client tty a tmux session is currently displayed on, or "".

    ``tmux list-clients -t <session>`` reports the physical terminal(s) the
    session is attached to.  This is the pane the user is actually viewing the
    session in *right now* — it updates automatically on every re-attach, so it
    never goes stale (unlike a GUID inherited at launch time).  Returns "" when
    the session is detached (no client) or tmux is unavailable.
    """
    if not tmux_session:
        return ""
    result = subprocess.run(
        ["tmux", "list-clients", "-t", tmux_session, "-F", "#{client_tty}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    lines = [line for line in result.stdout.strip().splitlines() if line]
    return lines[0] if lines else ""


def _current_pane_tty() -> str:
    """Return the controlling tty of this process (the physical pane it runs in), or "".

    Used at pre-launch time (before tmux takes over) to rename the launching
    pane by its own tty.  Tries stdout, stderr, then stdin so a redirected
    stream doesn't defeat resolution.
    """
    for fd in (1, 2, 0):
        try:
            return os.ttyname(fd)
        except (AttributeError, OSError):
            continue
    return ""


def _configure_tmux_for_iterm2(session_id: str) -> None:
    """Enable DCS passthrough and disable auto-rename for a tmux session.

    Two options together ensure the bash script's DCS-wrapped SetProfile /
    OSC-1 sequences reliably reach the outer terminal (iTerm2):

    - ``allow-passthrough all``: let DCS-wrapped escape sequences pass through
      tmux to the outer terminal.  Required on tmux ≥ 3.3 (default off).
    - ``automatic-rename off``: stop tmux from emitting its own OSC 0/2 title
      sequences for the running process name.  Without this, tmux overrides
      the iTerm2 Session Name with the shell/job name, switching the Session
      Title dropdown from "Name" to "Shell".

    Both options are silently ignored on tmux versions that don't support them.
    """
    subprocess.run(
        ["tmux", "set-option", "-p", "-t", session_id, "allow-passthrough", "all"],
        capture_output=True,
        check=False,
    )
    subprocess.run(
        ["tmux", "set-window-option", "-t", session_id, "automatic-rename", "off"],
        capture_output=True,
        check=False,
    )


def _rename_tmux_window(session_id: str, name: str) -> None:
    """Set the tmux window name shown in tmux's native status line."""
    subprocess.run(
        ["tmux", "rename-window", "-t", session_id, name],
        capture_output=True,
        check=False,
    )


def _emit_iterm2_profile_setup(
    ai_name: str,
    engine: str,
    session: str = "",
    slot: str | None = None,
    project_name: str = "",
    vscode_authority: str | None = None,
) -> None:
    """Emit iTerm2 profile/color/title escape sequences directly to stdout.

    Called before os.execvp so sequences reach iTerm2 before tmux takes over.
    No DCS wrapping needed — we're not inside tmux yet at this point.

    Also generates the per-session Dynamic Profile JSON (with tinted icon) so
    iTerm2 can hot-reload it.  The generated profile name is ai-cli:{ai_name}.

    slot: color hex string from _assign_iterm2_color_slot, e.g. "#5e35b1".
    """
    if not _is_iterm2():
        return

    cfg = _load_iterm2_config()
    # Display the full session id WITH its engine prefix (e.g. "c-session-1" for Claude,
    # "g-session-1" for Gemini/agy) so panes are visually distinguishable by engine.
    # Matches the in-session rename, which uses "$tmux_session".
    session_name = session or ai_name
    session_type = _iterm2_session_type(engine)

    # Resolve color
    if slot:
        color_hex = slot if slot.startswith("#") else f"#{slot}"
    else:
        palette = _iterm2_palette(cfg)
        color_hex = f"#{palette[0][1]}" if palette else "#e74c3c"

    # Generate icon + Dynamic Profile
    try:
        from . import icon_generator as _ig

        # Resolve icon_color via defaults → project → session
        icon_color = _resolve_iterm2_config(cfg, ai_name, project_name).get("icon_color")

        icon_path = _ig.generate_session_icon(ai_name, color_hex, session_type, icon_color)
        _ig.generate_dynamic_profile(
            ai_name,
            color_hex,
            session_type,
            icon_path,
            vscode_authority=vscode_authority,
        )
        # Give iTerm2 time to FSEvents-reload the Dynamic Profile before SetProfile
        # is sent. Without this delay the profile may not exist yet when the escape
        # sequence arrives, which is unrecoverable for remote (mosh) sessions where
        # there is no second SetProfile from inside tmux.
        import time as _time

        _time.sleep(0.3)
    except Exception:
        pass  # Icon generation failure must never block session launch

    # Emit profile/color/title sequences
    profile_name = f"ai-cli:{ai_name}"
    color_no_hash = color_hex.lstrip("#")
    sys.stdout.write(f"\033]1337;SetProfile={profile_name}\007")
    sys.stdout.write(f"\033]1337;SetColors=tab={color_no_hash}\007")
    # OSC 1 sets the iTerm2 "Name" field — mosh does not intercept it
    sys.stdout.write(f"\033]1;{session_name}\007")
    sys.stdout.flush()
    # AppleScript fallback: set the session Name directly via the iTerm2 API.
    # This is the authoritative fix for local sessions where OSC 1 may be
    # overridden by tmux rendering, process tracking, or nested-tmux passthrough
    # failures. Runs synchronously here (before os.execvp) so the Name is set
    # before the process is replaced by tmux/mosh/ssh.
    #
    # Resolve the pane by this process's controlling tty — the physical pane the
    # launcher runs in, which is exactly the pane tmux is about to take over.
    # tty matching can't collide across sessions the way an inherited/shared
    # ITERM_SESSION_ID GUID could (AI-CLI-59 root cause).
    _set_iterm2_name_by_tty(_current_pane_tty(), session_name)
