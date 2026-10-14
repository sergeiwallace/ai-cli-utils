"""Every script in ``.githooks/`` either runs or says why it does not.

A hook script that no ``.pre-commit-config.yaml`` entry names enforces nothing, and
nothing about the file shows it: ``.githooks/commit-msg`` sat in the tree for weeks
looking like a CHANGELOG guard while no commit ever ran it. These tests make an
unregistered hook a visible decision rather than a silent default.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / ".githooks"
CONFIG = REPO_ROOT / ".pre-commit-config.yaml"

#: The installer is run by hand, not by git, so it is never a hook entry.
_NOT_A_HOOK = {"install.sh"}

#: Scripts that are deliberately not registered, each with the reason. Adding a
#: name here is the explicit way to keep a script inert; removing it is how the
#: script is switched on (register it in .pre-commit-config.yaml in the same change).
_INTENTIONALLY_INERT: dict[str, str] = {}

_ENTRY = re.compile(r"^\s*entry:\s*(\S+)", re.MULTILINE)


def _unaccounted_hooks(hooks_dir: Path, config_text: str) -> list[str]:
    """Executable files in ``hooks_dir`` that no entry names and no list excuses."""
    registered = {Path(entry).name for entry in _ENTRY.findall(config_text) if entry.startswith(".githooks/")}
    excused = registered | _NOT_A_HOOK | set(_INTENTIONALLY_INERT)
    return sorted(
        path.name
        for path in hooks_dir.iterdir()
        if path.is_file() and os.access(path, os.X_OK) and path.name not in excused
    )


@pytest.mark.skipif(os.name == "nt", reason="the executable bit is not tracked on Windows checkouts")
def test_given_the_real_githooks_dir_when_audited_then_every_script_is_registered_or_explicitly_inert():
    assert _unaccounted_hooks(HOOKS_DIR, CONFIG.read_text(encoding="utf-8")) == []


def test_given_an_inert_listing_when_audited_then_it_names_a_script_that_exists_and_is_not_registered():
    """A stale excuse would hide the next unregistered script of the same name."""
    config_text = CONFIG.read_text(encoding="utf-8")
    registered = {Path(entry).name for entry in _ENTRY.findall(config_text)}
    for name, reason in _INTENTIONALLY_INERT.items():
        assert (HOOKS_DIR / name).is_file(), f"{name} is listed as inert but no longer exists"
        assert name not in registered, f"{name} is registered now; drop it from the inert list"
        assert reason.strip()


@pytest.mark.skipif(os.name == "nt", reason="the executable bit is not tracked on Windows checkouts")
def test_given_a_new_unregistered_hook_script_when_audited_then_it_is_reported(tmp_path):
    hooks = tmp_path / ".githooks"
    hooks.mkdir()
    for name in ("registered.sh", "install.sh", "new-guard.sh"):
        script = hooks / name
        script.write_text("#!/bin/sh\n")
        script.chmod(0o755)
    config_text = (
        "repos:\n  - repo: local\n    hooks:\n      - id: registered\n        entry: .githooks/registered.sh\n"
    )

    assert _unaccounted_hooks(hooks, config_text) == ["new-guard.sh"]
