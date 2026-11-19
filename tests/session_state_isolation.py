"""Fail closed before session tests resolve persistent state paths."""

import os
import sys
from pathlib import Path

import pytest


def require_isolated_session_state(request, tmp_path_factory):
    message = "Session tests require isolated HOME and XDG directories. Run pytest with tests/conftest.py enabled."
    try:
        request.getfixturevalue("_redirect_home_away_from_the_operator")
        request.getfixturevalue("_verify_user_state_isolation")
        request.getfixturevalue("_redirect_canonical_worktree_registry")
    except pytest.FixtureLookupError:
        pytest.fail(message, pytrace=False)

    base = tmp_path_factory.getbasetemp().resolve()
    names = ["HOME", "USERPROFILE", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"]
    if sys.platform == "win32":
        names.extend(("APPDATA", "LOCALAPPDATA"))
    names.append("AI_CLI_CANONICAL_WORKTREE_REGISTRY")
    for name in names:
        # The application resolvers can inspect and rename legacy directories.
        # Validate their inputs before calling them.
        path = Path(os.environ.get(name, ""))
        if not path.is_relative_to(base) or not path.resolve().is_relative_to(base):
            pytest.fail(f"{message} {name} must point inside this pytest run's temporary directory.", pytrace=False)
