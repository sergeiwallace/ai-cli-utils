"""No test module may import a POSIX-only stdlib module without a platform guard.

This exists because an unguarded one is not a failing test, it is a **collection error**, and a
collection error aborts the whole run on that platform. Measured 2026-09-25: a single
``import fcntl`` at the top of one test module took all three Windows CI jobs red while all three
Linux jobs stayed green, so the symptom read as a platform problem rather than as one missing
guard, and it landed on ``main`` because nothing in the suite could see it from a POSIX machine.

That last part is the point. A developer on macOS or Linux cannot reproduce this by running the
suite; the guard has to be a static scan or it is not a guard at all.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent

#: Stdlib modules that do not exist on Windows. Importing any of these at module level makes the
#: module unimportable there, whatever the tests inside it do.
#:
#: ``crypt`` deliberately excluded: it was removed from the stdlib entirely in Python 3.13, so it is
#: absent on every platform rather than Windows-specifically, and listing it made the premise test
#: below fail on this repo's own interpreter. That test catching it is the guard working.
POSIX_ONLY_MODULES = frozenset({"fcntl", "grp", "pty", "pwd", "resource", "termios", "tty", "posix", "syslog"})


def _module_level_imports(tree: ast.Module) -> list[tuple[str, int]]:
    """Return ``(module name, lineno)`` for every import at the module's own top level.

    Imports nested inside a function, class, ``if`` or ``try`` are deliberately ignored: those are
    already guarded by construction, since they do not run at import time or run conditionally.
    """
    found: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            found.extend((alias.name.split(".")[0], node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.module.split(".")[0], node.lineno))
    return found


def _guards_windows_before(tree: ast.Module, lineno: int) -> bool:
    """True when a module-level ``sys.platform`` check appears before ``lineno``.

    Deliberately shallow: it asks whether the author put *a* platform guard ahead of the import,
    not whether the guard is correct. A precise check would have to model
    ``pytest.skip(allow_module_level=True)`` versus ``pytest.importorskip`` versus a bare
    ``sys.exit``, and being wrong about that would make this scanner reject valid code — the
    failure mode that gets a hygiene test deleted rather than fixed.
    """
    for node in tree.body:
        if getattr(node, "lineno", 0) >= lineno:
            break
        if isinstance(node, ast.If) and "sys.platform" in ast.unparse(node.test):
            return True
    return False


def _violations() -> list[str]:
    out: list[str] = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name, lineno in _module_level_imports(tree):
            if name in POSIX_ONLY_MODULES and not _guards_windows_before(tree, lineno):
                out.append(f"{path.name}:{lineno} imports POSIX-only {name!r} with no platform guard")
    return out


def test_given_the_test_suite_when_scanned_then_no_posix_only_import_is_unguarded():
    violations = _violations()
    assert violations == [], (
        "These modules cannot be imported on Windows, so each one is a collection error that "
        "aborts the entire Windows run:\n  " + "\n  ".join(violations)
    )


def test_given_the_scanner_when_given_an_unguarded_import_then_it_reports_it(tmp_path, monkeypatch):
    # The guard must be able to fail in its own failure case, or it enforces nothing.
    planted = tmp_path / "test_planted_unguarded.py"
    planted.write_text("import fcntl\n\n\ndef test_x():\n    assert fcntl\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "TESTS_DIR", tmp_path)
    assert _violations() == ["test_planted_unguarded.py:1 imports POSIX-only 'fcntl' with no platform guard"]


def test_given_the_scanner_when_the_import_is_platform_guarded_then_it_is_accepted(tmp_path, monkeypatch):
    planted = tmp_path / "test_planted_guarded.py"
    planted.write_text(
        "import sys\n\nimport pytest\n\n"
        'if sys.platform == "win32":\n'
        '    pytest.skip("posix only", allow_module_level=True)\n\n'
        "import fcntl\n\n\ndef test_x():\n    assert fcntl\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "TESTS_DIR", tmp_path)
    assert _violations() == []


def test_given_the_scanner_when_the_import_is_inside_a_function_then_it_is_ignored(tmp_path, monkeypatch):
    planted = tmp_path / "test_planted_nested.py"
    planted.write_text("def test_x():\n    import fcntl\n\n    assert fcntl\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "TESTS_DIR", tmp_path)
    assert _violations() == []


@pytest.mark.parametrize("name", sorted(POSIX_ONLY_MODULES))
def test_given_each_listed_module_when_checked_then_it_is_absent_on_windows_only(name):
    # Pins the list's premise: every entry must be importable here (a POSIX host) and must be one
    # of the modules Python does not ship on Windows. A typo'd entry would otherwise sit in the
    # list forever, silently scanning for a module name that can never appear.
    if sys.platform == "win32":
        pytest.skip("the list describes what is missing on Windows; assert it from a POSIX host")
    __import__(name)
