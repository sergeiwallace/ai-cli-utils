"""Versioned, client-confirmed identity and billing guard for Antigravity (``agy``) slots.

``ai g`` launches Antigravity (``agy``) on the user's Google subscription sign-in, never on
a metered API key. Before exec, this module:

1. Refuses a missing ``~/.gemini/antigravity-cli/antigravity-oauth-token`` file (existence
   only; token bytes are never read) and refuses an API-billed ``modelProvider`` in
   ``~/.gemini/antigravity-cli/settings.json``.
2. Strips API-key environment variables so an ambient key in the caller's shell can never
   be billed.
3. Enforces an explicit ``--mode`` whenever headless print flags are passed.
4. Reserves a versioned slot record under ``<state>/ai-cli-utils/sessions/<slot>.agy.json``,
   ensures the lightweight global ``SessionStart`` confirmation plugin is present, and
   resumes confirmed slots with ``--conversation <uuid>`` after corroborating the native
   store. Wrong-client (legacy Gemini) or unconfirmed slots are refused rather than guessed.
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import shlex
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import portalocker

from . import output
from .output import Untagged
from .process_probe import StartTimeMatch, normalize_proc_start, probe_for
from .session_clients import PRESENT, Antigravity, Gemini

SCHEMA_VERSION = 1
SIGN_IN_FILE = Path(".gemini") / "antigravity-cli" / "antigravity-oauth-token"
SETTINGS_FILE = Path(".gemini") / "antigravity-cli" / "settings.json"
PLUGIN_REL_DIR = Path(".gemini") / "config" / "plugins" / "ai-cli-slot-confirm"
#: ``modelProvider`` values that bill an API key or cloud project instead of Google sign-in.
API_PROVIDERS = frozenset({"gemini", "vertex"})
#: Removed from the agent's environment on every launch.
API_KEY_VARIABLES = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "AGY_ADC_AUTH")
SIGN_IN_STEP = "run `agy` once in a terminal and finish the Google sign-in, then retry"
HOOK_COMMAND = "ai internal agy-session-confirm"
SLOT_RECORD_ENV = "AI_CLI_AGY_SLOT_RECORD"
LAUNCH_TOKEN_ENV = "AI_CLI_AGY_LAUNCH_TOKEN"
_UUID_RE = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.IGNORECASE)
_PRINT_FLAGS = frozenset({"-p", "--print", "--prompt"})


class IdentityError(ValueError):
    """The slot cannot safely identify or launch a resumable Antigravity conversation."""


def _is_uuid(value: object) -> bool:
    return isinstance(value, str) and _UUID_RE.fullmatch(value) is not None


def _is_abs_path(value: object) -> bool:
    return isinstance(value, str) and bool(value) and Path(value).is_absolute()


def subscription_refusal(home: Path | None = None) -> str | None:
    """Return why ``agy`` must not launch on this account, or None when it may."""
    root = home if home is not None else Path.home()
    if not (root / SIGN_IN_FILE).is_file():
        return f"no Google sign-in found for agy ({SIGN_IN_FILE.as_posix()} is missing): {SIGN_IN_STEP}"
    try:
        settings = json.loads((root / SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        settings = {}
    provider = settings.get("modelProvider") if isinstance(settings, dict) else None
    if isinstance(provider, str) and provider.strip().lower() in API_PROVIDERS:
        return (
            f'agy is set to bill an API ("modelProvider": "{provider}" in {SETTINGS_FILE.as_posix()}); '
            "remove that key to use the Google sign-in, then retry"
        )
    return None


def strip_api_keys(environ: dict[str, str] | os._Environ[str]) -> None:
    """Remove metered API-key environment variables in place."""
    for name in API_KEY_VARIABLES:
        environ.pop(name, None)


def validate_launch_args(args: Sequence[str]) -> None:
    """Refuse unguarded headless print invocations without an explicit ``--mode``."""
    has_print = any(arg in _PRINT_FLAGS or arg.startswith(("--print=", "--prompt=")) for arg in args)
    if not has_print:
        return
    has_mode = False
    for index, arg in enumerate(args):
        if arg == "--mode" and index + 1 < len(args) and args[index + 1].strip():
            has_mode = True
            break
        if arg.startswith("--mode=") and arg.split("=", 1)[1].strip():
            has_mode = True
            break
    if not has_mode:
        raise IdentityError("headless agy print mode requires an explicit --mode flag (for example --mode plan)")


def _has_explicit_conversation_flag(args: Sequence[str]) -> bool:
    return any(
        arg in ("-c", "--continue", "--conversation") or arg.startswith(("--continue=", "--conversation="))
        for arg in args
    )


def _plugin_hook_command() -> str:
    if sys.platform == "win32":
        # Platform-specific Windows cmd.exe guard avoids spawning Python on manual agy runs.
        return f"if defined {SLOT_RECORD_ENV} ({HOOK_COMMAND}) else (echo {{}})"
    return (
        f'[ -n "${{{SLOT_RECORD_ENV}:-}}" ] && [ -n "${{{LAUNCH_TOKEN_ENV}:-}}" ] '
        f"&& exec {HOOK_COMMAND} || printf '{{}}\\n'"
    )


def ensure_confirm_plugin(home: Path | None = None) -> Path:
    """Install or update the lightweight global ``SessionStart`` confirmation plugin."""
    root = home if home is not None else Path.home()
    plugin_dir = root / PLUGIN_REL_DIR
    manifest_text = json.dumps({"name": "ai-cli-slot-confirm"}, indent=2) + "\n"
    hooks_doc = {
        "ai-cli-slot-confirm": {
            "SessionStart": [
                {
                    "type": "command",
                    "command": _plugin_hook_command(),
                    "timeout": 10,
                }
            ]
        }
    }
    hooks_text = json.dumps(hooks_doc, indent=2) + "\n"
    manifest_path = plugin_dir / "plugin.json"
    hooks_path = plugin_dir / "hooks.json"
    try:
        if (
            manifest_path.is_file()
            and hooks_path.is_file()
            and manifest_path.read_text(encoding="utf-8") == manifest_text
            and hooks_path.read_text(encoding="utf-8") == hooks_text
        ):
            return plugin_dir
        plugin_dir.mkdir(parents=True, exist_ok=True)
        for target, text in ((manifest_path, manifest_text), (hooks_path, hooks_text)):
            staged = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            staged.write_text(text, encoding="utf-8")
            staged.replace(target)
    except OSError as exc:
        raise IdentityError(f"could not write Antigravity confirmation hook under {plugin_dir}: {exc}") from None
    return plugin_dir


def _writer() -> dict:
    probe = probe_for()
    pid = os.getpid()
    identity = probe.capture_identity(pid)
    if identity is None:
        raise IdentityError("Antigravity writer identity unavailable on this platform")
    return {"pid": pid, "backend": identity.backend, "start_time": identity.marker}


def _recorded_writer(launch: dict) -> tuple[dict, int, int | float]:
    """Validate stored writer identity without interpreting unreadable values as death."""
    writer = launch.get("writer")
    if not isinstance(writer, dict):
        raise IdentityError("Antigravity writer identity missing; launch refused")
    pid, start = writer.get("pid"), normalize_proc_start(writer.get("start_time"))
    if (
        not isinstance(pid, int)
        or isinstance(pid, bool)
        or not 0 < pid <= 0xFFFFFFFF
        or not isinstance(start, (int, float))
        or isinstance(start, bool)
        or not 0 < start < 2**64
        or not math.isfinite(start)
        or writer.get("backend") not in ("procfs", "psutil")
    ):
        raise IdentityError("Antigravity writer identity malformed; launch refused")
    return writer, pid, start


def _check_writer(launch: dict) -> None:
    """Return only on proven death/reuse; unreadable evidence never proves death."""
    writer, pid, start = _recorded_writer(launch)
    probe = probe_for()
    if not probe.is_present(pid):
        return
    current = probe.capture_identity(pid)
    if current is None or current.backend != writer["backend"]:
        raise IdentityError("Antigravity writer identity unreadable or backend changed; launch refused")
    match = probe.start_time_match(pid, start)
    if match is StartTimeMatch.MATCH:
        raise IdentityError("Antigravity writer is live; slot collision refused")
    if match is not StartTimeMatch.UNPROVEN or current.marker == start:
        raise IdentityError("Antigravity writer identity cannot be compared; launch refused")


def _read(path: Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
        raise IdentityError("Antigravity slot schema is incompatible; identity is unknown")
    required = {"phase", "conversation_id", "transcript_path", "result", "resumable", "in_flight"}
    if not required <= doc.keys():
        raise IdentityError("Antigravity slot record is incomplete; identity is unknown")
    launch = doc.get("in_flight")
    if (
        not isinstance(doc.get("slot"), str)
        or not isinstance(doc.get("cwd"), str)
        or not Path(doc["cwd"]).is_absolute()
        or doc.get("phase") not in ("confirmed", "unconfirmed")
        or not isinstance(launch, dict)
        or not isinstance(launch.get("token"), str)
        or not launch["token"]
        or not isinstance(launch.get("launched_at"), str)
        or not isinstance(launch.get("client_version"), str)
        or not isinstance(launch.get("route"), str)
        or not isinstance(doc.get("client_version"), str)
        or doc.get("execution_mode") != "interactive"
        or not isinstance(doc.get("route"), str)
    ):
        raise IdentityError("Antigravity slot record is malformed; identity is unknown")
    try:
        launched_at = datetime.fromisoformat(launch["launched_at"])
    except ValueError:
        raise IdentityError("Antigravity launch time is malformed") from None
    if launched_at.tzinfo is None:
        raise IdentityError("Antigravity launch time has no timezone")
    if doc["phase"] == "confirmed":
        if not _is_uuid(doc.get("conversation_id")) or doc.get("result") not in ("confirmed", "live-only"):
            raise IdentityError("Antigravity confirmed identity is malformed")
        tpath = doc.get("transcript_path")
        if tpath is not None and not _is_abs_path(tpath):
            raise IdentityError("Antigravity confirmed transcript path is malformed")
    elif (
        doc.get("conversation_id") is not None
        or doc.get("transcript_path") is not None
        or doc.get("result") != "unconfirmed"
    ):
        raise IdentityError("Antigravity unconfirmed identity is malformed")
    saved = doc.get("resumable")
    if saved is not None and (
        not isinstance(saved, dict)
        or not _is_uuid(saved.get("conversation_id"))
        or (saved.get("transcript_path") is not None and not _is_abs_path(saved.get("transcript_path")))
    ):
        raise IdentityError("Antigravity saved resumable identity is malformed")
    if doc["phase"] == "unconfirmed" and saved is not None:
        raise IdentityError("Antigravity unconfirmed state cannot hold a resumable fallback")
    _recorded_writer(launch)
    return doc


def _write(path: Path, doc: dict) -> None:
    staged = path.with_suffix(".pending")
    staged.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    staged.replace(path)


def _legacy_gemini_records(cwd: Path, home: Path | None = None) -> list:
    client = Gemini(home)
    if client._root_problem() is not None:
        return []
    try:
        return client.records_for(cwd)
    except OSError:
        return []


def _existing_agy_records(cwd: Path, home: Path | None = None) -> list:
    client = Antigravity(home)
    if client._root_problem() is not None:
        return []
    try:
        return client.records_for(cwd)
    except OSError:
        return []


def prepare(
    path: Path,
    cwd: Path,
    slot: str,
    *,
    fresh: bool,
    route: str,
    client_version: str,
    start_fresh: str | None = None,
    adopt_conversation: tuple[str, str] | None = None,
    home: Path | None = None,
) -> tuple[str, str, str]:
    """Reserve this process's launch while preserving confirmed native identity.

    Returns ``(target, record_path, token)`` where ``target`` is ``"new"`` or an exact
    corroborated Antigravity conversation UUID.
    """
    if start_fresh is not None and start_fresh != slot:
        raise IdentityError("start-fresh session does not match this launch")
    if adopt_conversation is not None:
        adopt_id, adopt_slot = adopt_conversation
        if adopt_slot != slot:
            raise IdentityError("adopt-conversation session does not match this launch")
        if not _is_uuid(adopt_id):
            raise IdentityError("adopt-conversation id must be a valid UUID")
    if start_fresh is not None and adopt_conversation is not None:
        raise IdentityError("cannot combine --start-fresh and --adopt-conversation")

    path = path.resolve()
    resolved_cwd = cwd.resolve()
    if "\n" in str(path) or "\r" in str(path):
        raise IdentityError("Antigravity slot path cannot be represented in the launcher reply")
    path.parent.mkdir(parents=True, exist_ok=True)
    agy_client = Antigravity(home)

    with portalocker.Lock(str(path) + ".lock", timeout=5):
        doc = _read(path) if path.exists() else None
        target = "new"
        decision = None
        if doc is not None:
            if doc.get("slot") != slot or Path(doc["cwd"]).resolve() != resolved_cwd:
                raise IdentityError("Antigravity slot identity does not match this launch")
            _check_writer(doc["in_flight"])
            if start_fresh is not None or adopt_conversation is not None:
                raise IdentityError("recovery approval requires a missing slot record; existing identity is preserved")
            if doc["phase"] == "confirmed":
                probe = agy_client.probe_conversation(resolved_cwd, doc["conversation_id"])
                if probe.state == PRESENT:
                    if doc["result"] == "live-only":
                        doc["result"] = "confirmed"
                        doc["resumable"] = {
                            "conversation_id": doc["conversation_id"],
                            "transcript_path": doc["transcript_path"],
                        }
                    target = doc["conversation_id"]
                elif doc["result"] == "confirmed":
                    raise IdentityError(probe.reason)
                elif doc["resumable"] is not None:
                    saved = doc["resumable"]
                    proof = agy_client.probe_conversation(resolved_cwd, saved["conversation_id"])
                    if proof.state != PRESENT:
                        raise IdentityError(proof.reason)
                    target = saved["conversation_id"]
            decision = "resume-confirmed" if target != "new" else "abandoned-unconfirmed; start-fresh"
        else:
            if adopt_conversation is not None:
                adopt_id = adopt_conversation[0]
                proof = agy_client.probe_conversation(resolved_cwd, adopt_id)
                if proof.state != PRESENT:
                    raise IdentityError(proof.reason)
                doc = {
                    "schema_version": SCHEMA_VERSION,
                    "slot": slot,
                    "cwd": str(resolved_cwd),
                    "phase": "confirmed",
                    "conversation_id": adopt_id,
                    "transcript_path": None,
                    "result": "confirmed",
                    "resumable": {"conversation_id": adopt_id, "transcript_path": None},
                    "client_version": client_version,
                    "execution_mode": "interactive",
                    "route": route,
                }
                target = adopt_id
            else:
                legacy_gemini = _legacy_gemini_records(resolved_cwd, home)
                existing_agy = _existing_agy_records(resolved_cwd, home)
                if start_fresh is None and legacy_gemini:
                    raise IdentityError(
                        "Legacy Gemini CLI records exist in this worktree; refusing wrong-client reuse. "
                        "With owner approval, repeat prepare with --start-fresh SESSION naming this exact slot."
                    )
                if start_fresh is None and (not fresh or existing_agy):
                    raise IdentityError(
                        "Antigravity slot has no client-confirmed conversation; refusing directory-based adoption. "
                        "With owner approval, repeat prepare with --start-fresh SESSION or "
                        "--adopt-conversation UUID SESSION naming this exact slot."
                    )
                doc = {
                    "schema_version": SCHEMA_VERSION,
                    "slot": slot,
                    "cwd": str(resolved_cwd),
                    "phase": "unconfirmed",
                    "conversation_id": None,
                    "transcript_path": None,
                    "result": "unconfirmed",
                    "resumable": None,
                    "client_version": client_version,
                    "execution_mode": "interactive",
                    "route": route,
                }
        doc["in_flight"] = {
            "token": secrets.token_hex(16),
            "writer": _writer(),
            "launched_at": datetime.now(UTC).isoformat(),
            "client_version": client_version,
            "route": route,
        }
        _write(path, doc)
        if decision:
            output.warning(output.Tag.SESSION, "session", f"Antigravity slot {slot}: abandoned launch; {decision}.")
        return target, str(path), doc["in_flight"]["token"]


def confirm(path: Path, token: str, event: object) -> None:
    """Bind a native Antigravity ``SessionStart`` event to the current launch."""
    if not token or not path.is_file():
        raise IdentityError("missing Antigravity launch confirmation identity")
    with portalocker.Lock(str(path) + ".lock", timeout=5):
        doc = _read(path)
        if doc["in_flight"]["token"] != token:
            raise IdentityError("stale Antigravity launch confirmation refused")
        if not isinstance(event, dict) or event.get("hookEventName") != "SessionStart":
            raise IdentityError("unsupported Antigravity confirmation event")
        conversation_id = event.get("conversationId")
        cwd = event.get("cwd")
        transcript_path = event.get("transcriptPath")
        if (
            not _is_uuid(conversation_id)
            or not isinstance(cwd, str)
            or not cwd
            or Path(cwd).resolve() != Path(doc["cwd"]).resolve()
            or not (transcript_path is None or transcript_path == "" or _is_abs_path(transcript_path))
        ):
            raise IdentityError("Antigravity confirmation identity is incompatible with the launch")
        normalized_transcript = transcript_path if isinstance(transcript_path, str) and transcript_path else None
        doc["resumable"] = {"conversation_id": conversation_id, "transcript_path": normalized_transcript}
        doc.update(
            phase="confirmed",
            conversation_id=conversation_id,
            transcript_path=normalized_transcript,
            result="confirmed",
            client_version=doc["in_flight"]["client_version"],
            route=doc["in_flight"]["route"],
        )
        _write(path, doc)


def confirm_from_environment(args: list[str]) -> int:
    """Handle ``ai internal agy-session-confirm`` invoked by the ``SessionStart`` hook."""
    record = os.environ.get(SLOT_RECORD_ENV)
    token = os.environ.get(LAUNCH_TOKEN_ENV)
    if not record and not token:
        # Manual `agy` session outside `ai g` — acknowledge cleanly with empty JSON.
        output.raw(Untagged.INTERNAL_REPLY, "{}")
        return 0
    try:
        if args or not record or not token:
            raise IdentityError("missing Antigravity launch environment")
        confirm(Path(record), token, json.load(sys.stdin))
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, portalocker.LockException):
        output.error(output.Tag.SESSION, "session", "Antigravity identity unknown: launch confirmation refused.")
        return 1
    output.raw(Untagged.INTERNAL_REPLY, "{}")
    return 0


def _detect_version(executable: str) -> str:
    result = subprocess.run([executable, "--version"], capture_output=True, text=True, check=False, timeout=10)
    if result.returncode != 0:
        return "unknown"
    text = result.stdout.strip()
    match = re.search(r"([0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?)", text)
    return match[1] if match else (text.splitlines()[0] if text else "unknown")


def launch_command(
    command: str,
    record: Path | str,
    cwd: Path | str,
    slot: str,
    ai_name: str,
    *,
    fresh: bool,
    route: str,
    sandbox: bool = False,
    extra_args: Sequence[str] | None = None,
) -> list[str]:
    """Build the argv that delegates Antigravity launch through this module."""
    argv = [
        sys.executable,
        "-m",
        "ai_cli.agy_session",
        "launch",
        str(record),
        str(cwd),
        slot,
        ai_name,
        "true" if fresh else "false",
        route,
        "--command",
        command,
    ]
    if sandbox:
        argv.append("--sandbox")
    if extra_args:
        argv.append("--")
        argv.extend(extra_args)
    return argv


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    action = raw[0] if raw else ""
    try:
        if action not in {"prepare", "launch"} or len(raw) < 7:
            raise IdentityError("invalid Antigravity identity operation")
        record_path = Path(raw[1])
        cwd_path = Path(raw[2])
        slot = raw[3]
        ai_name = raw[4]
        fresh = raw[5] == "true"
        route = raw[6]
        rest = raw[7:]

        command_str = "agy"
        sandbox = False
        start_fresh: str | None = None
        adopt_conversation: tuple[str, str] | None = None
        extra_args: list[str] = []

        idx = 0
        while idx < len(rest):
            tok = rest[idx]
            if tok == "--":
                extra_args.extend(rest[idx + 1 :])
                break
            if tok in ("-C", "--command") and idx + 1 < len(rest):
                command_str = rest[idx + 1]
                idx += 2
            elif tok in ("-s", "--sandbox"):
                sandbox = True
                idx += 1
            elif tok in ("-F", "--start-fresh") and idx + 1 < len(rest):
                start_fresh = rest[idx + 1]
                idx += 2
            elif tok in ("-A", "--adopt-conversation") and idx + 2 < len(rest):
                adopt_conversation = (rest[idx + 1], rest[idx + 2])
                idx += 3
            else:
                raise IdentityError(f"unknown Antigravity launch option: {tok}")

        base_argv = shlex.split(command_str)
        if not base_argv or not base_argv[0]:
            raise IdentityError("empty Antigravity command")

        if action == "launch":
            refusal = subscription_refusal()
            if refusal:
                raise IdentityError(refusal)
            validate_launch_args([*base_argv[1:], *extra_args])

        client_version = _detect_version(base_argv[0])
        if action == "launch":
            ensure_confirm_plugin()

        target, record, token = prepare(
            record_path,
            cwd_path,
            slot,
            fresh=fresh,
            route=route,
            client_version=client_version,
            start_fresh=start_fresh,
            adopt_conversation=adopt_conversation,
        )
        if action == "launch":
            strip_api_keys(os.environ)
            os.environ["AI_SESSION_NAME"] = ai_name
            os.environ["AI_CLI_SESSION_NAME"] = ai_name
            os.environ["CLAUDE_CODE_TASK_LIST_ID"] = ai_name
            os.environ[SLOT_RECORD_ENV] = record
            os.environ[LAUNCH_TOKEN_ENV] = token

            exec_argv = list(base_argv)
            if sandbox and "--sandbox" not in exec_argv and "--sandbox" not in extra_args:
                exec_argv.append("--sandbox")
            if target != "new" and not _has_explicit_conversation_flag([*exec_argv[1:], *extra_args]):
                exec_argv.extend(["--conversation", target])
            exec_argv.extend(extra_args)

            from .main import _exec_handoff

            _exec_handoff(exec_argv[0], exec_argv)

        output.raw(Untagged.INTERNAL_REPLY, target)
        output.raw(Untagged.INTERNAL_REPLY, record)
        output.raw(Untagged.INTERNAL_REPLY, token)
    except IdentityError as exc:
        output.error(output.Tag.SESSION, "session", f"Antigravity identity unknown: {exc}")
        return 76 if action == "launch" else 1
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RuntimeError,
        portalocker.LockException,
        subprocess.TimeoutExpired,
    ):
        output.error(
            output.Tag.SESSION,
            "session",
            "Antigravity identity unknown: launch/resume refused; keep a live pane attached or inspect the slot record.",
        )
        return 76 if action == "launch" else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
