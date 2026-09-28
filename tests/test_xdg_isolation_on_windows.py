"""The XDG isolation fixture must isolate the WINDOWS base dir too, not only the XDG ones.

``_isolate_xdg_state_home`` set ``XDG_STATE_HOME`` and nothing else, which made it a **no-op on
Windows**: ``config.get_xdg_state_home``, ``get_xdg_cache_home`` and ``get_xdg_data_home`` all
branch on ``sys.platform == "win32"`` to ``resolve_base_dir("LOCALAPPDATA", ...)`` and never
consult the XDG variable there (``src/ai_cli/config.py:191-213``). A real Windows host has
``LOCALAPPDATA`` set to the operator's profile, so those resolvers returned the real state,
cache and data directories while the fixture reported success -- the same escape AI-CLI-u2ox
found for the canonical worktree registry, in different files.

**What this file asserts, and why it is the fixture's contract rather than a resolver's output.**
An earlier version of this test drove the resolvers with ``sys.platform`` patched to ``win32``
and asserted the result was not under the real home. That test passed with the fix REMOVED, so
it was worthless: a separate autouse fixture already redirects ``HOME``, so on POSIX the Windows
branch's *fallback* (``Path.home() / "AppData" / "Local"``) lands in a temp directory whether or
not ``LOCALAPPDATA`` is redirected. The defect is invisible that way, because the real hazard is
the environment variable being SET -- which is the one thing a POSIX host does not reproduce.

So the assertion is on the environment the fixture establishes: ``LOCALAPPDATA`` must be set,
and must point outside the operator's real home. That is checkable from any platform, fails the
moment the redirect is dropped, and is exactly the precondition a Windows run depends on.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest
from conftest import _REAL_HOME

from ai_cli import config

# The Windows base-directory variable every branched resolver consults.
_WINDOWS_BASE_DIR_VARS = ("LOCALAPPDATA", "APPDATA")

# The POSIX variables the same fixture redirects, kept here so dropping one is a test failure
# rather than a silent loss of isolation.
_POSIX_BASE_DIR_VARS = ("XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME")


def _is_under(path: Path, ancestor: Path) -> bool:
    """True when *path* is *ancestor* or sits beneath it, compared case-insensitively.

    ``normcase`` as well as ``normpath``, because Windows compares paths case-insensitively and
    the same root arrives spelled both ways -- from the environment as the OS set it, and from
    ``Path.home()`` as pathlib built it. A comparison blind to that would report isolation while
    the value sat in the real profile under a different spelling.
    """
    target = os.path.normcase(os.path.normpath(str(path)))
    root = os.path.normcase(os.path.normpath(str(ancestor)))
    return target == root or target.startswith(root + os.sep)


@pytest.mark.parametrize("env_var", _WINDOWS_BASE_DIR_VARS)
def test_given_the_isolation_fixture_when_active_then_the_windows_vars_are_redirected(env_var):
    """The load-bearing assertion, and the one that fails if the fix is reverted."""
    value = os.environ.get(env_var)

    assert value, (
        f"{env_var} is not set, so on Windows every `get_xdg_*` resolver would "
        "fall back to the operator's real profile. The autouse XDG isolation fixture must set "
        "it (AI-CLI-6al2)."
    )
    assert not _is_under(Path(value), _REAL_HOME), (
        f"{env_var} points at {value}, inside the operator's real home "
        f"{_REAL_HOME}. On Windows that is the real state, cache and data directory."
    )


@pytest.mark.parametrize("env_var", _POSIX_BASE_DIR_VARS)
def test_given_the_isolation_fixture_when_active_then_the_posix_vars_are_redirected(env_var):
    """The half that already worked, pinned so a Windows fix cannot regress POSIX."""
    value = os.environ.get(env_var)

    assert value, f"{env_var} is not set, so POSIX resolution falls back to the real home"
    assert not _is_under(Path(value), _REAL_HOME), f"{env_var} points at {value}, inside the real home"


def test_given_the_resolvers_when_read_then_every_localappdata_branch_is_covered_by_one_variable():
    """Guards the guard: a resolver added later must not reach a variable nothing redirects.

    Read out of ``config.py`` rather than from a remembered list, so the check cannot drift from
    the code. If a future resolver branches to ``APPDATA`` (the Roaming counterpart) instead,
    this fails -- correctly, because nothing would be isolating that one.
    """
    source = Path(config.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    windows_vars: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("get_xdg_"):
            continue
        segment = ast.get_source_segment(source, node) or ""
        windows_vars.update(candidate for candidate in ("LOCALAPPDATA", "APPDATA") if candidate in segment)

    assert windows_vars == set(_WINDOWS_BASE_DIR_VARS), (
        f"config.py's get_xdg_* resolvers read Windows base-dir variables {sorted(windows_vars)}, "
        f"but the isolation fixture redirects {sorted(_WINDOWS_BASE_DIR_VARS)}. An unredirected one "
        "reaches the operator's real profile on Windows."
    )


def test_given_a_value_under_the_real_home_when_checked_then_it_is_detected() -> None:
    """Positive control for ``_is_under``, so the assertions above cannot pass by never matching."""
    assert _is_under(_REAL_HOME / "AppData" / "Local", _REAL_HOME)
    assert _is_under(_REAL_HOME, _REAL_HOME)
    assert not _is_under(Path(os.path.sep) / "tmp" / "somewhere-else", _REAL_HOME)
