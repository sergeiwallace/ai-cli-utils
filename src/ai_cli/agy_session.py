"""Launch Antigravity (``agy``) on its Google sign-in, never on an API key.

``agy`` shares the Google sign-in of the Gemini CLI, kept in ``~/.gemini/oauth_creds.json``.
Its own settings live in ``~/.gemini/antigravity-cli/settings.json``, where
``"modelProvider": "gemini"`` (with ``GEMINI_API_KEY``) or ``"vertex"`` switches it to
metered API billing. A launch through ``ai agy`` refuses both a missing sign-in and an
API provider, and removes the API-key variables from the agent's environment, so a key
exported in the caller's shell can never be billed. The sign-in file is checked for
existence only; its contents are never read.
"""

import json
import os
import sys
from pathlib import Path

SIGN_IN_FILE = Path(".gemini") / "oauth_creds.json"
SETTINGS_FILE = Path(".gemini") / "antigravity-cli" / "settings.json"
#: ``modelProvider`` values that bill an API key or a cloud project instead of the sign-in.
API_PROVIDERS = frozenset({"gemini", "vertex"})
#: Removed from the agent's environment on every launch.
API_KEY_VARIABLES = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI")
SIGN_IN_STEP = "run `agy` once in a terminal and finish the Google sign-in, then retry"


def subscription_refusal(home: Path | None = None) -> str | None:
    """Why ``agy`` must not launch on this account, or None when it may."""
    home = home or Path.home()
    if not (home / SIGN_IN_FILE).is_file():
        return f"no Google sign-in found for agy ({SIGN_IN_FILE.as_posix()} is missing): {SIGN_IN_STEP}"
    try:
        settings = json.loads((home / SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        settings = {}
    provider = settings.get("modelProvider") if isinstance(settings, dict) else None
    if isinstance(provider, str) and provider.strip().lower() in API_PROVIDERS:
        return (
            f'agy is set to bill an API ("modelProvider": "{provider}" in {SETTINGS_FILE.as_posix()}); '
            "remove that key to use the Google sign-in, then retry"
        )
    return None


def strip_api_keys(environ: dict[str, str] | os._Environ) -> None:
    """Remove the API-key variables from ``environ`` in place (the bare launch execs into it)."""
    for name in API_KEY_VARIABLES:
        environ.pop(name, None)


def launch_command(extra_args: list[str]) -> list[str]:
    """``agy`` with its extra arguments, the API-key variables removed where ``env -u`` exists."""
    if sys.platform == "win32":
        # No ``env -u`` here: the bare launch strips the variables from its own environment.
        return ["agy", *extra_args]
    unset = [flag for name in API_KEY_VARIABLES for flag in ("-u", name)]
    return ["env", *unset, "agy", *extra_args]
