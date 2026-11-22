# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 ai-harness contributors
"""Read-only Claude registration digest vendored from ai-harness.

Adapted from src/ai_harness/mcp/client_install.py at 26b85ec80f65e6901b1e83940b0af84fbdb478c5.
Keep the projection and encoding in sync with the pinned producer fixture.
"""

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

CLAUDE_DIGEST_SCHEME = "semantic-v1"
CORE_SERVER_NAME = "ai-harness-core"
CORE_ID = "ai-harness-core@ai-harness-core-local"
MARKETPLACE = "ai-harness-core-local"
BEADS_ID = "ai-harness-beads@ai-harness-core-local"
CLAUDE_FILES = (
    "settings.json",
    "plugins/known_marketplaces.json",
    "plugins/installed_plugins.json",
)


class ArtifactError(ValueError):
    """Invalid registration with a diagnostic that contains no input bytes."""


@dataclass(frozen=True)
class PluginRegistration:
    plugin_id: str
    receipt_key: str


CORE = PluginRegistration(CORE_ID, "claude")
REGISTRATIONS = (CORE, PluginRegistration(BEADS_ID, "claude-beads"))


def _digest(content: bytes) -> str:
    return sha256(content).hexdigest()


def _json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _safe_path(path: Path) -> bool:
    return not any(p.is_symlink() for p in (path, *path.parents))


def _read(path: Path) -> bytes | None:
    if not _safe_path(path):
        raise ArtifactError("Artifact conflict")
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _registration_paths(*paths: Path) -> None:
    for path in paths:
        if not path.is_absolute() or not _safe_path(path) or ".worktrees" in path.parts:
            raise ArtifactError("Invalid registration path")


def _registration_json(path: Path) -> dict:
    raw = _read(path)
    value = json.loads(raw.decode()) if raw is not None else {}
    if not isinstance(value, dict):
        raise ArtifactError("Registration conflict")
    return value


def _claude_documents(directory: Path) -> dict[str, dict]:
    documents = {name: _registration_json(directory / name) for name in CLAUDE_FILES}
    settings, _, installed = documents.values()
    for value in (
        settings.get("enabledPlugins", {}),
        settings.get("extraKnownMarketplaces", {}),
        settings.get("mcpServers", {}),
        installed.get("plugins", {}),
    ):
        if not isinstance(value, dict):
            raise ArtifactError("Registration conflict")
    if CORE_SERVER_NAME in settings.get("mcpServers", {}):
        raise ArtifactError("Registration conflict")
    return documents


def _claude_entries(documents: dict[str, dict], registration=CORE) -> dict:
    settings, known, installed = documents.values()
    return {
        "enabled": settings.get("enabledPlugins", {}).get(registration.plugin_id),
        "marketplace_setting": settings.get("extraKnownMarketplaces", {}).get(MARKETPLACE),
        "marketplace": known.get(MARKETPLACE),
        "installed": installed.get("plugins", {}).get(registration.plugin_id),
    }


def _claude_semantic_entries(documents: dict[str, dict], registration=CORE) -> dict:
    """Select installer-owned fields, excluding client-maintained timestamps."""
    entries = _claude_entries(documents, registration)
    setting, market, rows = (
        entries["marketplace_setting"],
        entries["marketplace"],
        entries["installed"],
    )
    if (
        not isinstance(setting, dict)
        or not isinstance(market, dict)
        or not isinstance(rows, list)
        or len(rows) != 1
        or not isinstance(rows[0], dict)
    ):
        raise ArtifactError("Modified client registration")
    sources = [setting.get("source"), market.get("source")]
    if not all(isinstance(source, dict) for source in sources):
        raise ArtifactError("Modified client registration")
    return {
        "plugin_id": registration.plugin_id,
        "enabled": entries["enabled"],
        "marketplace_setting": {key: sources[0].get(key) for key in ("source", "path")},
        "marketplace": {key: sources[1].get(key) for key in ("source", "path")},
        "installLocation": market.get("installLocation"),
        "installed": {key: rows[0].get(key) for key in ("installPath", "version", "scope")},
    }


def claude_registration_digest(directory: Path, receipt_path: Path) -> str:
    """Hash the live semantic entries for registrations owned by the Core receipt."""
    _registration_paths(directory, receipt_path)
    receipt = _registration_json(receipt_path)
    if not receipt.get(CORE.receipt_key):
        raise ArtifactError("Core registration unavailable")
    documents = _claude_documents(directory)
    entries = [
        _claude_semantic_entries(documents, registration)
        for registration in REGISTRATIONS
        if receipt.get(registration.receipt_key)
    ]
    return _digest(_json(entries))
