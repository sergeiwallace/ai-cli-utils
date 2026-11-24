"""Versioned, client-confirmed identity and billing guard for Antigravity (``agy``) slots.

``ai g`` launches Antigravity (``agy``) on the user's Google subscription sign-in, never on
a metered API key. Before exec, this module:

1. Refuses a missing ``~/.gemini/antigravity-cli/antigravity-oauth-token`` file (existence
   only; token bytes are never read) and refuses an API-billed ``modelProvider`` in
   ``~/.gemini/antigravity-cli/settings.json``.
2. Strips API-key environment variables from both version probes and launches so an ambient
   key in the caller's shell can never be billed.
3. Refuses native resume overrides (``-c``/``--continue``, ``--conversation``, ``-r``/``--resume``)
   and enforces effective read-only ``--mode plan`` (never with ``--disable-slash-commands``),
   validated prompt framing, and output classification whenever headless print flags are passed.
4. Reserves a versioned slot record under ``<state>/ai-cli-utils/sessions/<slot>.agy.json``
   with cross-slot conversation exclusivity, ensures the owned global ``SessionStart``
   confirmation plugin is present only after slot preparation succeeds, and resumes confirmed
   slots with exact ``--conversation <uuid>`` after corroborating the native store.
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
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import portalocker

from . import output, tmux_binary
from .output import Untagged
from .process_probe import StartTimeMatch, normalize_proc_start, probe_for
from .session_clients import PRESENT, Antigravity, Gemini, Probe

SCHEMA_VERSION = 1
SIGN_IN_FILE = Path(".gemini") / "antigravity-cli" / "antigravity-oauth-token"
SETTINGS_FILE = Path(".gemini") / "antigravity-cli" / "settings.json"
PLUGIN_REL_DIR = Path(".gemini") / "config" / "plugins" / "ai-cli-slot-confirm"
PLUGIN_OWNER = "ai-cli-utils"
#: ``modelProvider`` values that bill an API key or cloud project instead of Google sign-in.
API_PROVIDERS = frozenset({"gemini", "vertex"})
#: Removed from the agent's environment on every version probe and launch.
API_KEY_VARIABLES = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "AGY_ADC_AUTH")
SIGN_IN_STEP = "run `agy` once in a terminal and finish the Google sign-in, then retry"
HOOK_COMMAND = "ai internal agy-session-confirm"
SLOT_RECORD_ENV = "AI_CLI_AGY_SLOT_RECORD"
LAUNCH_TOKEN_ENV = "AI_CLI_AGY_LAUNCH_TOKEN"
SIGN_IN_VERIFIED_ENV = "AI_CLI_AGY_SIGN_IN_VERIFIED"
EXIT_PRINT_EMPTY = 77
EXIT_PRINT_AUTO_DENIED = 78
_UUID_RE = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.IGNORECASE)
_PRINT_FLAGS = frozenset({"-p", "--print", "--prompt"})
_PRINT_PREFIXES = ("--print=", "--prompt=")
_RESUME_OVERRIDE_FLAGS = frozenset({"-c", "--continue", "--conversation", "-r", "--resume"})
_RESUME_OVERRIDE_PREFIXES = ("--continue=", "--conversation=", "--resume=")
_ALLOWED_PRINT_MODES = frozenset({"plan"})
_VALUE_FLAGS = frozenset(
    {
        "--mode",
        "--model",
        "--input-format",
        "--output-format",
        "--print-timeout",
        "-i",
        "--prompt-interactive",
        "--agent",
        "--profile",
        "--workspace",
    }
)
_PRINT_DENIAL_MARKERS = (
    "tool call denied",
    "tool denied",
    "auto-denied",
    "auto denied",
    "permission denied",
    "requires confirmation",
    "tool confirmation",
)
# Line-framed diagnostic matcher for plain-text stdout auto-denials so ordinary
# prose or quoted examples (e.g. "The example shows a tool call denied by policy")
# are never misclassified as exit 78.
_STDOUT_AUTO_DENIAL_LINE_RE = re.compile(
    r"^\s*(?:(?:\[[^\]]+\]|error|warning|policy|denied|tool)\s*:\s*)*"
    r"(?:tool\s+call\s+(?:was\s+)?(?:auto[- ]?denied|denied)|"
    r"tool\s+(?:was\s+)?(?:auto[- ]?denied|denied)|"
    r"auto[- ]?denied\s+by\b|"
    r"tool\s+confirmation\s+required\b|"
    r"requires\s+tool\s+confirmation\b)",
    re.IGNORECASE,
)


class IdentityError(ValueError):
    """The slot cannot safely identify or launch a resumable Antigravity conversation."""


def _is_uuid(value: object) -> bool:
    return isinstance(value, str) and _UUID_RE.fullmatch(value) is not None


def _is_abs_path(value: object) -> bool:
    return isinstance(value, str) and bool(value) and Path(value).is_absolute()


_AUTH_ERROR_MARKERS = (
    "please sign in",
    "not signed in",
    "sign in",
    "unauthenticated",
    "unauthorized",
    "login required",
    "authentication required",
)


def _probe_sign_in(executable: str, home: Path) -> str:
    """Ask ``agy models`` whether this account is signed in without reading any token bytes.

    Returns ``"signed-in"``, ``"signed-out"``, or ``"unknown"`` (for example offline or
    missing binary). Always strips API-key environment variables so an ambient key cannot
    satisfy the probe.
    """
    env = _sanitized_env()
    env["HOME"] = str(home)
    if sys.platform == "win32":
        # Platform-specific Windows home directory override for isolated sign-in probes.
        env["USERPROFILE"] = str(home)
    try:
        result = subprocess.run(
            [executable, "models"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if result.returncode == 0:
        return "signed-in"
    combined = f"{result.stdout}\n{result.stderr}".lower()
    if any(marker in combined for marker in _AUTH_ERROR_MARKERS):
        return "signed-out"
    return "unknown"


def sign_in_cache_token(executable: str, home: Path | None = None) -> str:
    """Return the process-scoped cache key binding a preflight sign-in probe to ``(home, executable)``."""
    root = home if home is not None else Path.home()
    return f"{root}:{executable}"


def subscription_refusal(
    home: Path | None = None,
    *,
    executable: str | None = None,
    platform: str | None = None,
    allow_cached: bool = False,
) -> str | None:
    """Return why ``agy`` must not launch on this account, or None when it may.

    Enforces the subscription-only billing guard on every OS (refusing API-billed
    ``modelProvider`` overrides). For sign-in presence, accepts the local OAuth token file
    when present (fast path on Linux) and otherwise probes ``agy models`` non-interactively
    so OS credential stores (macOS Keychain, Windows Credential Manager) work without
    requiring a plaintext token file. When ``allow_cached`` is True and preflight already
    verified sign-in for the same ``(home, executable)``, skips repeating the subprocess probe.
    """
    root = home if home is not None else Path.home()
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
    if (root / SIGN_IN_FILE).is_file():
        return None
    if allow_cached and executable and os.environ.get(SIGN_IN_VERIFIED_ENV) == sign_in_cache_token(executable, root):
        return None
    target_platform = sys.platform if platform is None else platform
    if executable:
        probe = _probe_sign_in(executable, root)
        if probe == "signed-in":
            return None
        if probe == "signed-out":
            return f"no Google sign-in found for agy: {SIGN_IN_STEP}"
        if not target_platform.startswith("linux"):
            # Platform-specific macOS/Windows credential stores live outside ~/.gemini;
            # when offline or unprovable, warn and let agy handle sign-in while keeping
            # the modelProvider refusal and stripped API-key variables active.
            output.warning(
                output.Tag.SESSION,
                "session",
                "Could not verify Antigravity sign-in state (offline or probe unavailable); continuing.",
            )
            return None
    return f"no Google sign-in found for agy ({SIGN_IN_FILE.as_posix()} is missing): {SIGN_IN_STEP}"


def strip_api_keys(environ: dict[str, str] | os._Environ[str]) -> None:
    """Remove metered API-key environment variables in place."""
    for name in API_KEY_VARIABLES:
        environ.pop(name, None)


def _sanitized_env() -> dict[str, str]:
    env = dict(os.environ)
    strip_api_keys(env)
    return env


def _option_args(args: Sequence[str]) -> list[str]:
    """Return arguments before the ``--`` option terminator (real CLI flags only)."""
    result: list[str] = []
    for arg in args:
        if arg == "--":
            break
        result.append(arg)
    return result


def is_print_mode(args: Sequence[str]) -> bool:
    """Return True when ``args`` request non-interactive headless print execution."""
    return any(arg in _PRINT_FLAGS or arg.startswith(_PRINT_PREFIXES) for arg in _option_args(args))


def _option_value(args: Sequence[str], flag: str, default: str = "") -> str:
    opts = _option_args(args)
    prefix = f"{flag}="
    value = default
    index = 0
    while index < len(opts):
        arg = opts[index]
        if arg == flag and index + 1 < len(opts):
            value = opts[index + 1]
            index += 2
            continue
        if arg.startswith(prefix):
            value = arg.split("=", 1)[1]
        index += 1
    return value


def validate_launch_args(args: Sequence[str]) -> None:
    """Refuse native resume overrides and unguarded headless print invocations."""
    opts = _option_args(args)
    for arg in opts:
        if arg in _RESUME_OVERRIDE_FLAGS or arg.startswith(_RESUME_OVERRIDE_PREFIXES):
            raise IdentityError(
                f"native resume flag {arg!r} is refused on managed Antigravity slots; "
                "let the slot record own conversation binding or use --adopt-conversation"
            )

    if not is_print_mode(opts):
        return

    modes: list[str] = []
    index = 0
    while index < len(opts):
        arg = opts[index]
        if arg == "--mode":
            if index + 1 >= len(opts) or not opts[index + 1].strip():
                raise IdentityError("headless agy print mode requires --mode plan")
            modes.append(opts[index + 1].strip().lower())
            index += 2
            continue
        if arg.startswith("--mode="):
            raw_mode = arg.split("=", 1)[1].strip().lower()
            if not raw_mode:
                raise IdentityError("headless agy print mode requires --mode plan")
            modes.append(raw_mode)
        if arg == "--disable-slash-commands" or (
            arg.startswith("--disable-slash-commands=")
            and arg.split("=", 1)[1].strip().lower() not in {"", "false", "0", "no"}
        ):
            raise IdentityError(
                "headless agy print mode refuses --disable-slash-commands because it disables --mode plan enforcement"
            )
        index += 1

    if not modes:
        raise IdentityError("headless agy print mode requires an explicit read-only --mode plan flag")
    invalid = [m for m in modes if m not in _ALLOWED_PRINT_MODES]
    if invalid:
        raise IdentityError(
            f"headless agy print mode only allows read-only --mode plan (refused --mode {invalid[0]!r})"
        )

    input_fmt = _option_value(opts, "--input-format", "text").strip().lower()
    if input_fmt not in {"text", "stream-json"}:
        raise IdentityError(f"unsupported headless agy --input-format {input_fmt!r}; use text or stream-json")


def _extract_argv_prompt(args: Sequence[str]) -> str:
    """Return the explicit prompt passed in ``args``, or empty string if absent."""
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            rest = [tok for tok in args[index + 1 :] if tok.strip()]
            return rest[0] if rest else ""
        if arg.startswith(_PRINT_PREFIXES):
            return arg.split("=", 1)[1]
        if arg in _PRINT_FLAGS:
            if index + 1 < len(args) and args[index + 1] != "--" and not args[index + 1].startswith("-"):
                return args[index + 1]
            index += 1
            continue
        if arg in _VALUE_FLAGS:
            index += 2
            continue
        if arg.startswith("-"):
            index += 1
            continue
        return arg
    return ""


def validate_print_prompt_and_stdin(
    args: Sequence[str],
    *,
    stdin_text: str | None = None,
    stdin_is_tty: bool | None = None,
) -> str | None:
    """Validate prompt framing for headless print mode.

    Returns the validated single-line NDJSON stdin payload when ``--input-format stream-json``
    is used, or ``None`` (meaning the child receives ``DEVNULL``) when an argv prompt is
    supplied. Plain-text stdin and multi-line NDJSON streams are refused.
    """
    input_fmt = _option_value(args, "--input-format", "text").strip().lower()
    is_tty = sys.stdin.isatty() if stdin_is_tty is None else stdin_is_tty

    if input_fmt == "stream-json":
        if stdin_text is None:
            if is_tty:
                raise IdentityError("headless agy --input-format stream-json requires NDJSON user input on stdin")
            raw_stdin = sys.stdin.read()
        else:
            raw_stdin = stdin_text
        lines = [line.strip() for line in raw_stdin.splitlines() if line.strip()]
        if len(lines) != 1:
            raise IdentityError(
                f"headless agy --input-format stream-json requires exactly one NDJSON user line (got {len(lines)})"
            )
        try:
            obj = json.loads(lines[0])
        except ValueError as exc:
            raise IdentityError("headless agy stream-json stdin must contain valid NDJSON objects") from exc
        if not isinstance(obj, dict) or (obj.get("type") != "user" and obj.get("role") != "user"):
            raise IdentityError('headless agy stream-json stdin lines must be user JSON objects ({"type":"user", ...})')
        return lines[0] + "\n"

    prompt = _extract_argv_prompt(args)
    if not prompt.strip():
        raise IdentityError(
            "headless agy print mode requires an explicit argv prompt or --input-format stream-json; "
            "plain-text stdin is refused"
        )
    if stdin_text is not None and stdin_text.strip():
        raise IdentityError("headless agy print mode refuses plain-text stdin; pass the prompt in argv or stream-json")
    return None


_NON_ASSISTANT_TYPES = frozenset({"user", "tool_result", "tool_use", "system", "init", "meta", "status"})
_NON_ASSISTANT_ROLES = frozenset({"user", "tool", "system"})
_DENIAL_EVENT_TYPES = frozenset({"error", "permission_denied", "tool_denied", "auto_denied"})
_DENIAL_STATUSES = frozenset({"denied", "auto_denied", "permission_denied", "error"})


def _extract_json_text_and_denial(payload: object) -> tuple[str, bool]:
    """Return ``(assistant_response_text, auto_denied)`` from a single parsed JSON print payload.

    Classifies strictly by structured event type/role and error/denial fields rather than
    free-text prose matching, so assistant answers discussing permissions never false-positive
    and tool-result-only or echoed-user streams never count as assistant output.
    """
    if not isinstance(payload, dict):
        return "", False
    denied = bool(payload.get("is_error"))
    denials = payload.get("permission_denials")
    if isinstance(denials, list) and len(denials) > 0:
        denied = True
    subtype = payload.get("subtype")
    if isinstance(subtype, str) and subtype.lower() in {"error_during_execution", "permission_denied", "auto_denied"}:
        denied = True
    event_type = payload.get("type")
    event_type_lower = event_type.lower() if isinstance(event_type, str) else ""
    if event_type_lower in _DENIAL_EVENT_TYPES:
        denied = True
    status_val = payload.get("status")
    if isinstance(status_val, str) and status_val.lower() in _DENIAL_STATUSES:
        denied = True

    if event_type_lower in _NON_ASSISTANT_TYPES:
        return "", denied
    role = payload.get("role")
    if isinstance(role, str) and role.lower() in _NON_ASSISTANT_ROLES:
        return "", denied

    texts: list[str] = []
    for key in ("result", "response", "text", "content", "delta"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            texts.append(val.strip())
    message = payload.get("message")
    if isinstance(message, dict):
        msg_role = message.get("role")
        if not (isinstance(msg_role, str) and msg_role.lower() in _NON_ASSISTANT_ROLES):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                texts.append(content.strip())
            elif isinstance(content, list):
                for item in content:
                    if (
                        isinstance(item, dict)
                        and item.get("type") not in {"tool_use", "tool_result"}
                        and isinstance(item.get("text"), str)
                        and item["text"].strip()
                    ):
                        texts.append(item["text"].strip())
    return "\n".join(texts), denied


_FENCE_OPEN_RE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})(.*)$")


def _has_plain_text_auto_denial(text: str) -> bool:
    """Return True when ``text`` has an unquoted, non-fenced auto-denial diagnostic line.

    Follows CommonMark fenced code block rules (0-3 leading spaces, matching delimiter
    character and minimum length) so a 4-space-indented backtick line cannot open a
    fence to hide a genuine denial and a shorter inner fence cannot close an outer fence.
    """
    fence_char = ""
    fence_len = 0
    for line in text.splitlines():
        if fence_char:
            if re.match(rf"^[ ]{{0,3}}{re.escape(fence_char)}{{{fence_len},}}[ \t]*$", line):
                fence_char = ""
                fence_len = 0
            continue
        match = _FENCE_OPEN_RE.match(line)
        if match:
            marker, info = match.group(1), match.group(2)
            if marker[0] == "~" or "`" not in info:
                fence_char = marker[0]
                fence_len = len(marker)
                continue
        if re.match(r"^[ ]{0,3}>", line):
            continue
        if _STDOUT_AUTO_DENIAL_LINE_RE.match(line):
            return True
    return False


def classify_print_output(returncode: int, stdout: str, stderr: str, *, output_format: str = "text") -> int:
    """Classify headless print exit status so empty or auto-denied runs never look successful."""
    if returncode != 0:
        return returncode

    fmt = output_format.strip().lower() or "text"
    stderr_lower = stderr.lower()
    has_stderr_denial = any(marker in stderr_lower for marker in _PRINT_DENIAL_MARKERS)

    if fmt == "json":
        try:
            parsed = json.loads(stdout)
        except ValueError:
            return EXIT_PRINT_AUTO_DENIED if has_stderr_denial else EXIT_PRINT_EMPTY
        text, denied = _extract_json_text_and_denial(parsed)
        if denied or has_stderr_denial:
            return EXIT_PRINT_AUTO_DENIED
        if not text:
            return EXIT_PRINT_EMPTY
        return 0

    if fmt == "stream-json":
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not lines:
            return EXIT_PRINT_AUTO_DENIED if has_stderr_denial else EXIT_PRINT_EMPTY
        texts: list[str] = []
        denied = False
        for line in lines:
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            part_text, part_denied = _extract_json_text_and_denial(parsed)
            if part_denied:
                denied = True
            if part_text:
                texts.append(part_text)
        if denied or has_stderr_denial:
            return EXIT_PRINT_AUTO_DENIED
        if not texts:
            return EXIT_PRINT_EMPTY
        return 0

    if has_stderr_denial:
        return EXIT_PRINT_AUTO_DENIED
    stripped = stdout.strip()
    if not stripped or stripped == "SUCCESS":
        return EXIT_PRINT_EMPTY
    # A bare JSON status object (e.g. {"status": "SUCCESS"}) with no response text is empty.
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            parsed = json.loads(stripped)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            text, denied = _extract_json_text_and_denial(parsed)
            if denied:
                return EXIT_PRINT_AUTO_DENIED
            if not text:
                return EXIT_PRINT_EMPTY
            return 0
    if _has_plain_text_auto_denial(stdout):
        return EXIT_PRINT_AUTO_DENIED
    return 0


def _plugin_hook_command() -> str:
    if sys.platform == "win32":
        # Platform-specific Windows cmd.exe guard avoids spawning Python on manual agy runs.
        return f"if defined {SLOT_RECORD_ENV} ({HOOK_COMMAND}) else (echo {{}})"
    return (
        f'[ -n "${{{SLOT_RECORD_ENV}:-}}" ] && [ -n "${{{LAUNCH_TOKEN_ENV}:-}}" ] '
        f"&& exec {HOOK_COMMAND} || printf '{{}}\\n'"
    )


def _plugin_is_owned(manifest_path: Path) -> bool:
    try:
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(doc, dict) or doc.get("name") != "ai-cli-slot-confirm":
        return False
    owner = doc.get("managed_by")
    return owner is None or owner == PLUGIN_OWNER


def _create_exclusive_backup(target: Path, content: bytes) -> Path:
    """Write ``content`` to an exclusively created ``.bak`` (or ``.bak.N``) file."""
    seq = 0
    while True:
        name = f"{target.name}.bak" if seq == 0 else f"{target.name}.bak.{seq}"
        candidate = target.with_name(name)
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            seq += 1
            continue
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        return candidate


def ensure_confirm_plugin(home: Path | None = None) -> Path:
    """Install or update the owned lightweight global ``SessionStart`` confirmation plugin."""
    root = home if home is not None else Path.home()
    plugin_dir = root / PLUGIN_REL_DIR
    manifest_text = json.dumps({"name": "ai-cli-slot-confirm", "managed_by": PLUGIN_OWNER}, indent=2) + "\n"
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
        with portalocker.Lock(str(plugin_dir / ".plugin.lock"), timeout=5):
            if (
                manifest_path.is_file()
                and hooks_path.is_file()
                and manifest_path.read_text(encoding="utf-8") == manifest_text
                and hooks_path.read_text(encoding="utf-8") == hooks_text
            ):
                return plugin_dir
            if manifest_path.exists() and not _plugin_is_owned(manifest_path):
                raise IdentityError(
                    f"refusing to overwrite unmanaged Antigravity plugin at {plugin_dir}; "
                    "inspect or remove it before retrying"
                )
            if not manifest_path.exists() and hooks_path.exists():
                raise IdentityError(
                    f"refusing to overwrite unmanaged Antigravity hooks at {hooks_path}; "
                    "inspect or remove it before retrying"
                )
            for target, text in ((manifest_path, manifest_text), (hooks_path, hooks_text)):
                if target.is_file():
                    existing_bytes = target.read_bytes()
                    if existing_bytes != text.encode("utf-8"):
                        _create_exclusive_backup(target, existing_bytes)
                staged = target.with_name(f".{target.name}.{os.getpid()}.tmp")
                staged.write_text(text, encoding="utf-8")
                staged.replace(target)
    except IdentityError:
        raise
    except (OSError, portalocker.LockException) as exc:
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
    if "adopted_from" in doc and (doc["phase"] != "confirmed" or not _is_abs_path(doc["adopted_from"])):
        raise IdentityError("Antigravity adoption workspace is malformed")
    _recorded_writer(launch)
    return doc


def _write(path: Path, doc: dict) -> None:
    staged = path.with_suffix(".pending")
    staged.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    staged.replace(path)


def _registry_lock_path(record_path: Path) -> Path:
    return record_path.parent / ".agy-slots.lock"


def _owned_conversations(record_path: Path) -> dict[str, str]:
    """Return ``{lowercase_uuid: owner_slot}`` for all sibling ``*.agy.json`` records."""
    resolved = record_path.resolve()
    owned: dict[str, str] = {}
    for sibling in sorted(resolved.parent.glob("*.agy.json")):
        if sibling.resolve() == resolved:
            continue
        try:
            other = json.loads(sibling.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(other, dict):
            continue
        owner = str(other.get("slot") or sibling.stem)
        other_cid = other.get("conversation_id")
        if isinstance(other_cid, str):
            owned[other_cid.lower()] = owner
        saved = other.get("resumable")
        if isinstance(saved, dict) and isinstance(saved.get("conversation_id"), str):
            owned[saved["conversation_id"].lower()] = owner
    return owned


def _ensure_conversation_unowned(record_path: Path, conversation_id: str) -> None:
    """Refuse binding or adopting a conversation UUID already owned by another slot."""
    owner = _owned_conversations(record_path).get(conversation_id.lower())
    if owner is not None:
        raise IdentityError(
            f"Antigravity conversation {conversation_id} is already owned by slot {owner}; "
            "concurrent multi-slot reuse refused"
        )


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


def _probe_slot_conversation(client: Antigravity, cwd: Path, conversation_id: str, adopted_from: Path | None) -> Probe:
    proof = client.probe_conversation(cwd, conversation_id)
    if proof.state != PRESENT and adopted_from is not None and adopted_from != cwd:
        return client.probe_conversation(adopted_from, conversation_id)
    return proof


def bound_slot_conversation(path: Path, cwd: Path, slot: str, client: Antigravity | None = None) -> str | None:
    if not path.is_file():
        return None
    doc = _read(path)
    if doc["slot"] != slot or Path(doc["cwd"]).resolve() != cwd.resolve() or doc["phase"] != "confirmed":
        return None
    source = Path(doc["adopted_from"]) if "adopted_from" in doc else None
    proof = _probe_slot_conversation(client or Antigravity(), cwd, doc["conversation_id"], source)
    return doc["conversation_id"] if proof.state == PRESENT else None


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
    adopted_from: Path | None = None,
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
    if adopted_from is not None and adopt_conversation is None:
        raise IdentityError("an adoption workspace requires an explicit conversation ID")

    path = path.resolve()
    resolved_cwd = cwd.resolve()
    source = adopted_from.resolve() if adopted_from is not None else None
    if "\n" in str(path) or "\r" in str(path):
        raise IdentityError("Antigravity slot path cannot be represented in the launcher reply")
    path.parent.mkdir(parents=True, exist_ok=True)
    agy_client = Antigravity(home)

    with (
        portalocker.Lock(str(_registry_lock_path(path)), timeout=5),
        portalocker.Lock(str(path) + ".lock", timeout=5),
    ):
        doc = _read(path) if path.exists() else None
        target = "new"
        decision = None
        if doc is not None:
            if doc.get("slot") != slot or Path(doc["cwd"]).resolve() != resolved_cwd:
                raise IdentityError("Antigravity slot identity does not match this launch")
            _check_writer(doc["in_flight"])
            if doc["phase"] == "confirmed":
                if start_fresh is not None or adopt_conversation is not None:
                    raise IdentityError(
                        "recovery approval requires a missing or unconfirmed slot record; existing identity is preserved"
                    )
                recorded_source = Path(doc["adopted_from"]) if "adopted_from" in doc else None
                probe = _probe_slot_conversation(agy_client, resolved_cwd, doc["conversation_id"], recorded_source)
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
                else:
                    raise IdentityError(probe.reason)
                _ensure_conversation_unowned(path, target)
                decision = "resume-confirmed"
            else:
                if adopt_conversation is not None:
                    adopt_id = adopt_conversation[0]
                    _ensure_conversation_unowned(path, adopt_id)
                    proof = agy_client.probe_conversation(source or resolved_cwd, adopt_id)
                    if proof.state != PRESENT:
                        raise IdentityError(proof.reason)
                    doc.update(
                        phase="confirmed",
                        conversation_id=adopt_id,
                        transcript_path=None,
                        result="confirmed",
                        resumable={"conversation_id": adopt_id, "transcript_path": None},
                        client_version=client_version,
                        route=route,
                    )
                    target = adopt_id
                    decision = "adopt-confirmed"
                elif not fresh and start_fresh is None:
                    raise IdentityError(
                        "Antigravity slot has an unconfirmed abandoned launch; refusing silent fresh start on resume. "
                        "With owner approval, repeat prepare with --start-fresh SESSION or "
                        "--adopt-conversation UUID SESSION naming this exact slot."
                    )
                else:
                    decision = "abandoned-unconfirmed; start-fresh"
        else:
            if adopt_conversation is not None:
                adopt_id = adopt_conversation[0]
                _ensure_conversation_unowned(path, adopt_id)
                proof = agy_client.probe_conversation(source or resolved_cwd, adopt_id)
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
                owned_cids = _owned_conversations(path)
                existing_agy = [
                    rec for rec in _existing_agy_records(resolved_cwd, home) if rec.session_id.lower() not in owned_cids
                ]
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
        if source is not None and source != resolved_cwd:
            doc["adopted_from"] = str(source)
        doc["in_flight"] = {
            "token": secrets.token_hex(16),
            "writer": _writer(),
            "launched_at": datetime.now(UTC).isoformat(),
            "client_version": client_version,
            "route": route,
            "expected_conversation_id": None if target == "new" else target,
        }
        _write(path, doc)
        if decision:
            output.warning(output.Tag.SESSION, "session", f"Antigravity slot {slot}: abandoned launch; {decision}.")
        return target, str(path), doc["in_flight"]["token"]


def confirm(path: Path, token: str, event: object) -> None:
    """Bind a native Antigravity ``SessionStart`` event to the current launch."""
    if not token or not path.is_file():
        raise IdentityError("missing Antigravity launch confirmation identity")
    resolved = path.resolve()
    with (
        portalocker.Lock(str(_registry_lock_path(resolved)), timeout=5),
        portalocker.Lock(str(resolved) + ".lock", timeout=5),
    ):
        doc = _read(resolved)
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
        assert isinstance(conversation_id, str)
        expected = doc["in_flight"].get("expected_conversation_id")
        if expected is None and doc["phase"] == "confirmed":
            expected = doc.get("conversation_id")
        if expected is not None and conversation_id.lower() != expected.lower():
            raise IdentityError(
                f"Antigravity confirmation conversation {conversation_id} does not match pinned slot {expected}"
            )
        _ensure_conversation_unowned(resolved, conversation_id)
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
        from .agy_control import notify

        # The bridge must read the confirmed record during its handshake. Stage
        # the original bytes first so a refusal can roll back by atomic rename,
        # without needing another serialization or file write after rejection.
        with tempfile.TemporaryDirectory(prefix=".agy-confirm-", dir=resolved.parent) as root:
            rollback = Path(root) / resolved.name
            rollback.write_bytes(resolved.read_bytes())
            try:
                _write(resolved, doc)
                notify(resolved, token)
            except BaseException:
                rollback.replace(resolved)
                raise


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
    result = subprocess.run(
        [executable, "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        env=_sanitized_env(),
    )
    if result.returncode != 0:
        return "unknown"
    text = result.stdout.strip()
    match = re.search(r"([0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?)", text)
    return match[1] if match else (text.splitlines()[0] if text else "unknown")


_PYTHON_EXE_RE = re.compile(r"^python(?:3(?:\.\d+)*t?|w)?(?:\.exe)?$")


def is_agy_replay_command(command: Sequence[str]) -> bool:
    """Return True only when a bare replay recipe's launcher prefix runs ``python -m ai_cli.agy_session launch``."""
    tokens = list(command)
    if (
        len(tokens) >= 3
        and not tokens[0].endswith(("/", "\\"))
        and Path(tokens[0]).name.lower() in {"direnv", "direnv.exe"}
        and tokens[1] == "exec"
        and bool(tokens[2])
    ):
        tokens = tokens[3:]
    if len(tokens) < 4 or tokens[0].endswith(("/", "\\")):
        return False
    exe = Path(tokens[0]).name.lower()
    if not _PYTHON_EXE_RE.fullmatch(exe):
        return False
    return tokens[1] == "-m" and tokens[2] == "ai_cli.agy_session" and tokens[3] == "launch"


def launch_command(
    command: str,
    record: Path | str,
    cwd: Path | str,
    slot: str,
    ai_name: str,
    *,
    fresh: bool | str,
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
        fresh if isinstance(fresh, str) else "true" if fresh else "false",
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


def tmux_launch_line(slot: str, cwd: Path, pane_argv: Sequence[str]) -> str:
    target = f"={slot}"
    return " && ".join(
        (
            shlex.join(
                tmux_binary.argv("new-session", "-d", "-s", slot, "-c", str(cwd), f"exec {shlex.join(pane_argv)}")
            ),
            shlex.join(tmux_binary.argv("set-option", "-t", target, "@ai_cli_client", "agy")),
            shlex.join(tmux_binary.argv("attach-session", "-t", target)),
        )
    )


def _run_print_child(exec_argv: list[str], stdin_payload: str | None) -> int:
    """Execute headless print ``agy`` as a supervised child and classify its outcome."""
    output_fmt = _option_value(exec_argv[1:], "--output-format", "text")
    if stdin_payload is None:
        result = subprocess.run(
            exec_argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            env=dict(os.environ),
        )
    else:
        result = subprocess.run(
            exec_argv,
            input=stdin_payload,
            capture_output=True,
            text=True,
            check=False,
            env=dict(os.environ),
        )
    if result.stdout:
        output.raw(Untagged.INTERNAL_REPLY, result.stdout.rstrip("\r\n"))
    status = classify_print_output(result.returncode, result.stdout, result.stderr, output_format=output_fmt)
    if status == EXIT_PRINT_AUTO_DENIED:
        output.error(
            output.Tag.SESSION,
            "session",
            "Antigravity headless print run was auto-denied or failed tool confirmation.",
        )
    elif status == EXIT_PRINT_EMPTY:
        output.error(
            output.Tag.SESSION,
            "session",
            "Antigravity headless print run exited 0 with an empty response.",
        )
    elif status != 0 and result.stderr.strip():
        output.error(output.Tag.SESSION, "session", result.stderr.strip())
    return status


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

        combined_args = [*base_argv[1:], *extra_args]
        stdin_payload: str | None = None
        print_launch = False
        if action == "launch":
            refusal = subscription_refusal(executable=base_argv[0], allow_cached=True)
            os.environ.pop(SIGN_IN_VERIFIED_ENV, None)
            if refusal:
                raise IdentityError(refusal)
            validate_launch_args(combined_args)
            print_launch = is_print_mode(combined_args)
            if print_launch:
                stdin_payload = validate_print_prompt_and_stdin(combined_args)

        client_version = _detect_version(base_argv[0])

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
            ensure_confirm_plugin()
            strip_api_keys(os.environ)
            os.environ["AI_SESSION_NAME"] = ai_name
            os.environ["AI_CLI_SESSION_NAME"] = ai_name
            os.environ["CLAUDE_CODE_TASK_LIST_ID"] = ai_name
            os.environ[SLOT_RECORD_ENV] = record
            os.environ[LAUNCH_TOKEN_ENV] = token
            if not print_launch:
                from .agy_control import start

                start(Path(record))

            exec_argv = list(base_argv)
            if sandbox and "--sandbox" not in exec_argv and "--sandbox" not in extra_args:
                exec_argv.append("--sandbox")
            if target != "new":
                exec_argv.extend(["--conversation", target])
            exec_argv.extend(extra_args)

            if print_launch:
                return _run_print_child(exec_argv, stdin_payload)

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
