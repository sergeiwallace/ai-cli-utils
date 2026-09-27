"""Standing guard that every declared dependency resolves from a public index.

A dependency pinned to a VCS or direct URL is not merely a style question in a
public package: it decides whether the project can be resolved at all by someone
without access to that URL. This repository shipped one such pin, and the failure
it produced was invisible until a dependency bump arrived (AI-CLI-f8la).

Measured, because the shape of the failure is the reason this guard exists rather
than a code review note. The pin was confined to an optional extra that the
default dev sync does not install, so every run whose ``uv.lock`` was already
current stayed green -- the install step simply skipped the package. But a change
to ``pyproject.toml`` with no matching relock forces ``uv`` to RE-RESOLVE, and
resolution fetches the pinned revision whether or not the package is then
installed. So an automated dependency bump touching only ``pyproject.toml``
failed all four required checks before running a single test, with a git clone
error that named neither the real cause nor the remedy, and the dependency
updater's own lock-file update failed the same way -- which is precisely why its
pull requests arrived without the relock that would have avoided the fetch.

"Optional" therefore does not contain this class, and neither does "not installed
in CI". Only absence from the resolution graph does.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

# Markers for a requirement that does not come from a package index. ``uv``
# spells a direct reference in a specifier (``name @ git+https://...``), while
# ``[tool.uv.sources]`` spells the same thing as a table key, so both forms are
# checked -- a guard that knew only one would pass a pyproject carrying the other.
_DIRECT_REFERENCE_MARKERS = ("git+", "hg+", "svn+", "bzr+", "://")
_NON_INDEX_SOURCE_KEYS = ("git", "url", "path")


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _dependency_specifiers(pyproject: dict) -> list[tuple[str, str]]:
    """Yield ``(where, specifier)`` for every requirement the project declares.

    Runtime dependencies, every extra and every dependency group are all
    included: a private pin is equally unresolvable wherever it is declared, and
    the pin this guard was written for lived in the two least-visited of those.

    The last three sites are the ones a guard written only against that pin would
    omit, and each takes PEP 508 strings that may carry a direct reference:
    ``[tool.uv]``'s constraints and overrides both participate in resolution (this
    project already declares a constraint), and ``build-system.requires`` is
    fetched before the project is even built.
    """
    specifiers: list[tuple[str, str]] = []
    project = pyproject.get("project", {})
    for specifier in project.get("dependencies", []):
        specifiers.append(("project.dependencies", specifier))
    for extra, requirements in project.get("optional-dependencies", {}).items():
        specifiers.extend((f"project.optional-dependencies.{extra}", requirement) for requirement in requirements)
    for group, requirements in pyproject.get("dependency-groups", {}).items():
        specifiers.extend(
            (f"dependency-groups.{group}", requirement) for requirement in requirements if isinstance(requirement, str)
        )
    uv = pyproject.get("tool", {}).get("uv", {})
    for key in ("constraint-dependencies", "override-dependencies"):
        specifiers.extend((f"tool.uv.{key}", requirement) for requirement in uv.get(key, []))
    specifiers.extend(
        ("build-system.requires", requirement) for requirement in pyproject.get("build-system", {}).get("requires", [])
    )
    return specifiers


def scan_for_non_index_dependencies(root: Path) -> list[str]:
    """Return one ``where: detail`` finding per dependency not resolvable from an index."""
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    findings = [
        f"{where}: {specifier}"
        for where, specifier in _dependency_specifiers(pyproject)
        if any(marker in specifier for marker in _DIRECT_REFERENCE_MARKERS)
    ]
    sources = pyproject.get("tool", {}).get("uv", {}).get("sources", {})
    findings.extend(
        f"tool.uv.sources.{name}: {key} = {source[key]}"
        for name, source in sources.items()
        if isinstance(source, dict)
        for key in _NON_INDEX_SOURCE_KEYS
        if key in source
    )
    return findings


def scan_lock_for_non_index_packages(root: Path) -> list[str]:
    """Return one finding per locked package whose source is not a package index.

    The lock is checked as well as ``pyproject.toml`` because the lock is what CI
    installs from, and the two can disagree -- a hand edit, or a merge that keeps
    one side of a conflict, leaves a resolvable pyproject with an unresolvable lock.
    """
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    return [
        f"uv.lock: {package.get('name')} = {package['source'][key]}"
        for package in lock.get("package", [])
        if isinstance(package.get("source"), dict)
        for key in _NON_INDEX_SOURCE_KEYS
        if key in package["source"]
    ]


def test_given_the_declared_dependencies_when_scanned_then_none_require_a_private_url():
    findings = scan_for_non_index_dependencies(_repo_root())
    assert not findings, "dependency not resolvable from a public index:\n" + "\n".join(findings)


def test_given_the_lock_file_when_scanned_then_every_package_comes_from_an_index():
    findings = scan_lock_for_non_index_packages(_repo_root())
    assert not findings, "locked package not resolvable from a public index:\n" + "\n".join(findings)


def test_given_a_git_pinned_extra_when_scanned_then_it_is_flagged(tmp_path):
    """Positive control, in the shape the real regression had.

    The pin is placed in an optional extra AND given a ``[tool.uv.sources]``
    entry, because that combination is what made the original defect look
    contained: a guard that only inspected runtime dependencies would report a
    clean repository while the resolution graph still reached a private URL.
    """
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'dependencies = ["click>=8.1"]\n'
        "[project.optional-dependencies]\n"
        'internal = ["example-testkit"]\n'
        "[tool.uv.sources]\n"
        'example-testkit = { git = "https://example.com/private.git", rev = "abc123" }\n'
    )

    findings = scan_for_non_index_dependencies(tmp_path)

    assert findings == ["tool.uv.sources.example-testkit: git = https://example.com/private.git"]


def test_given_a_direct_url_specifier_when_scanned_then_it_is_flagged(tmp_path):
    """Second positive control: the same pin written as a specifier, with no sources table."""
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'dependencies = ["example-pkg @ git+https://example.com/private.git"]\n'
        "[dependency-groups]\n"
        'dev = ["pytest>=8"]\n'
    )

    findings = scan_for_non_index_dependencies(tmp_path)

    assert findings == ["project.dependencies: example-pkg @ git+https://example.com/private.git"]


def test_given_a_constraint_and_a_build_requirement_when_scanned_then_both_are_flagged(tmp_path):
    """Control for the three sites a guard written only against the original pin would omit.

    Constraints and overrides participate in resolution without appearing in any
    dependency list, and ``build-system.requires`` is fetched before the project
    builds -- so a private pin in any of them breaks exactly the same way while
    reading as unrelated configuration.
    """
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'dependencies = ["click>=8.1"]\n'
        "[tool.uv]\n"
        'constraint-dependencies = ["example-lib @ git+https://example.com/private.git"]\n'
        'override-dependencies = ["example-other @ https://example.com/private.tar.gz"]\n'
        "[build-system]\n"
        'requires = ["example-backend @ git+https://example.com/backend.git"]\n'
    )

    findings = scan_for_non_index_dependencies(tmp_path)

    assert findings == [
        "tool.uv.constraint-dependencies: example-lib @ git+https://example.com/private.git",
        "tool.uv.override-dependencies: example-other @ https://example.com/private.tar.gz",
        "build-system.requires: example-backend @ git+https://example.com/backend.git",
    ]


def test_given_a_git_sourced_locked_package_when_scanned_then_it_is_flagged(tmp_path):
    """Positive control for the lock half, which no pyproject scan can reach."""
    (tmp_path / "uv.lock").write_text(
        "version = 1\n"
        "[[package]]\n"
        'name = "click"\n'
        'source = { registry = "https://pypi.org/simple" }\n'
        "[[package]]\n"
        'name = "example-testkit"\n'
        'source = { git = "https://example.com/private.git#abc123" }\n'
    )

    findings = scan_lock_for_non_index_packages(tmp_path)

    assert findings == ["uv.lock: example-testkit = https://example.com/private.git#abc123"]
