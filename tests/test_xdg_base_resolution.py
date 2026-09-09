"""
Base-directory resolution must never depend on the current working directory.

A base-directory environment variable that is present but empty, or present but
relative, must be treated as unset. Both cases otherwise yield a relative base
path, which resolves against the process cwd -- so a cache or state file lands
inside whatever repository happens to be current instead of the user's home.

The XDG Base Directory specification requires exactly this: "If an
implementation encounters a relative path in any of these variables it should
consider the path invalid and ignore it." An empty value is the degenerate
relative case.

These tests are behavioural (what path comes out) plus one mechanical sweep that
fails if any new call site reintroduces the raw-environment-read shape.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

from ai_cli import config

SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "ai_cli"

# Every resolver whose output must be an absolute path, regardless of what the
# corresponding environment variable holds.
BASE_DIR_RESOLVERS = (
    ("XDG_CONFIG_HOME", config.get_xdg_config_home),
    ("XDG_STATE_HOME", config.get_xdg_state_home),
    ("XDG_CACHE_HOME", config.get_xdg_cache_home),
    ("XDG_DATA_HOME", config.get_xdg_data_home),
)

# Values that must all be rejected in favour of the home-relative fallback.
IGNORED_VALUES = (
    pytest.param("", id="empty"),
    pytest.param("relative-dir", id="bare-relative"),
    pytest.param("./relative-dir", id="dot-relative"),
    pytest.param("../relative-dir", id="parent-relative"),
)


@pytest.fixture
def cwd_outside_home(tmp_path, monkeypatch):
    """Run from a directory that is not under the user's home.

    Any resolver that leaks a relative path resolves it here, so a leak is
    visible as a path under *tmp_path* rather than silently landing somewhere
    plausible-looking inside the home directory.
    """
    workdir = tmp_path / "some-repo"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    return workdir


@pytest.mark.parametrize("value", IGNORED_VALUES)
@pytest.mark.parametrize(("env_var", "resolver"), BASE_DIR_RESOLVERS, ids=lambda v: getattr(v, "__name__", v))
def test_given_ignorable_base_dir_value_when_resolving_then_path_is_absolute(
    env_var, resolver, value, monkeypatch, cwd_outside_home
):
    monkeypatch.setenv(env_var, value)

    resolved = resolver()

    assert resolved.is_absolute(), f"{env_var}={value!r} produced a cwd-relative path: {resolved}"
    assert cwd_outside_home not in resolved.parents, f"{env_var}={value!r} leaked the cwd into {resolved}"


@pytest.mark.parametrize(("env_var", "resolver"), BASE_DIR_RESOLVERS, ids=lambda v: getattr(v, "__name__", v))
def test_given_absolute_base_dir_value_when_resolving_then_it_is_honoured(
    env_var, resolver, tmp_path, monkeypatch, cwd_outside_home
):
    """An absolute value is still respected -- the fix must not ignore real overrides."""
    override = tmp_path / "explicit-base"
    monkeypatch.setenv(env_var, str(override))

    resolved = resolver()

    assert resolved.is_absolute()
    assert override in resolved.parents or resolved == override, (
        f"{env_var} override {override} was not honoured; got {resolved}"
    )


@pytest.mark.parametrize("value", IGNORED_VALUES)
def test_given_ignorable_state_home_when_process_hygiene_resolves_then_cache_stays_out_of_cwd(
    value, monkeypatch, cwd_outside_home
):
    """The site that actually leaked a captured process listing into a repo."""
    from ai_cli import process_hygiene

    monkeypatch.setenv("XDG_STATE_HOME", value)

    cache_path = process_hygiene._cache_path()

    assert cache_path.is_absolute(), f"XDG_STATE_HOME={value!r} produced {cache_path}"
    assert cwd_outside_home not in cache_path.parents


@pytest.mark.parametrize("value", IGNORED_VALUES)
def test_given_ignorable_state_home_when_cc_usage_is_imported_then_state_dir_is_absolute(
    value, monkeypatch, cwd_outside_home
):
    """cc_usage binds its state dir at import time, so the env must be right on reload."""
    monkeypatch.setenv("XDG_STATE_HOME", value)

    cc_usage = importlib.reload(importlib.import_module("ai_cli.cc_usage"))
    try:
        assert cc_usage._STATE_DIR.is_absolute(), f"XDG_STATE_HOME={value!r} produced {cc_usage._STATE_DIR}"
        assert cwd_outside_home not in cc_usage._STATE_DIR.parents
        assert cc_usage._CURSOR_FILE.is_absolute()
    finally:
        # Restore the module to a state consistent with the real environment so
        # later tests patching its constants see the ordinary values.
        monkeypatch.undo()
        importlib.reload(cc_usage)


def test_given_unset_base_dir_var_when_resolving_then_home_default_is_used(monkeypatch, cwd_outside_home):
    """Positive control: the absent-key path was always correct and must stay so."""
    for env_var, _ in BASE_DIR_RESOLVERS:
        monkeypatch.delenv(env_var, raising=False)

    for env_var, resolver in BASE_DIR_RESOLVERS:
        resolved = resolver()
        assert resolved.is_absolute(), f"{env_var} unset produced {resolved}"
        assert Path.home() in resolved.parents, f"{env_var} unset did not fall back to home: {resolved}"


def _raw_base_dir_reads(directory: Path) -> list[str]:
    """Return every direct environment read of a base-directory variable.

    Finds ``os.environ.get("XDG_...")``, ``os.getenv("XDG_...")`` and the
    Windows equivalents, so a new call site cannot reintroduce the defect
    without this guard failing. Reads inside the shared resolver itself are
    excluded -- that is the one place the variable is legitimately consulted.
    """
    tracked_prefixes = ("XDG_", "APPDATA", "LOCALAPPDATA")
    offenders: list[str] = []

    for path in sorted(directory.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        # Function bodies allowed to read the raw variable.
        allowed_nodes: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == config.BASE_DIR_RESOLVER_NAME:
                allowed_nodes.update(id(child) for child in ast.walk(node))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or id(node) in allowed_nodes:
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in {"get", "getenv"}:
                if not node.args or not isinstance(node.args[0], ast.Constant):
                    continue
                name = node.args[0].value
                if isinstance(name, str) and name.startswith(tracked_prefixes):
                    offenders.append(f"{path.relative_to(directory)}:{node.lineno} reads {name}")

    return offenders


def test_given_the_source_tree_when_scanned_then_no_module_reads_a_base_dir_var_directly():
    """Mechanical guard: all base-dir reads must go through the shared resolver.

    Without this, the two sites fixed here can be re-added by any later change
    and the behavioural tests above would still pass -- they only cover the
    resolvers that exist today.
    """
    offenders = _raw_base_dir_reads(SRC_DIR)

    assert offenders == [], "base-directory variables must be read only via the shared resolver:\n" + "\n".join(
        offenders
    )


def test_given_the_scanner_when_shown_a_known_offender_then_it_reports_it(tmp_path):
    """Negative control: prove the scanner can actually fail.

    A guard that never fires is indistinguishable from a guard that finds
    nothing, so assert it detects the exact shape it exists to reject.
    """
    offender = tmp_path / "offender.py"
    offender.write_text(
        "import os\nfrom pathlib import Path\nbase = os.environ.get('XDG_STATE_HOME', '/fallback')\n",
        encoding="utf-8",
    )

    offenders = _raw_base_dir_reads(tmp_path)

    assert len(offenders) == 1
    assert "XDG_STATE_HOME" in offenders[0]


def test_given_the_scanner_when_shown_clean_source_then_it_reports_nothing(tmp_path):
    """Positive control for the scanner: unrelated env reads are not flagged."""
    clean = tmp_path / "clean.py"
    clean.write_text(
        "import os\nvalue = os.environ.get('SOME_OTHER_VAR', 'default')\n",
        encoding="utf-8",
    )

    assert _raw_base_dir_reads(tmp_path) == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX resolvers are not consulted on Windows")
def test_given_windows_base_dir_vars_when_ignorable_then_resolution_stays_absolute(monkeypatch, cwd_outside_home):
    """The Windows branches share the defect class, so cover them by simulation."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", "relative-appdata")
    monkeypatch.setenv("LOCALAPPDATA", "")

    for _, resolver in BASE_DIR_RESOLVERS:
        resolved = resolver()
        assert resolved.is_absolute(), f"{resolver.__name__} produced {resolved}"
        assert cwd_outside_home not in resolved.parents


def test_given_a_relative_fallback_when_resolving_then_it_is_rejected_loudly(monkeypatch):
    """The resolver must not silently accept a relative *fallback* either.

    A caller passing a relative default would reintroduce the bug through the
    shared helper, which is the one place a mistake would be invisible.
    """
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)

    with pytest.raises(ValueError, match="absolute"):
        config.resolve_base_dir("XDG_STATE_HOME", Path("relative-fallback"))


def test_given_a_nonstring_environ_value_when_resolving_then_it_does_not_crash(monkeypatch, cwd_outside_home):
    """os.environ values are always str, but guard the whitespace-only case.

    A value of "   " is not empty and not obviously relative, yet Path("   ")
    is relative -- so it must be rejected by the absoluteness check rather
    than by an emptiness check alone.
    """
    monkeypatch.setenv("XDG_STATE_HOME", "   ")

    resolved = config.get_xdg_state_home()

    assert resolved.is_absolute()
    assert cwd_outside_home not in resolved.parents
