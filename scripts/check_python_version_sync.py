#!/usr/bin/env python3
"""Fail if anything in the repo disagrees about which Python version it supports.

This project supports exactly ONE Python version (AI-CLI-6rwp). That version was
previously declared in six independent places -- `.python-version`, three CI
matrices, `requires-python`, the pyright target and the trove classifiers -- and
they did not agree: the local venv ran 3.14 while CI ran 3.11/3.12/3.13. The
consequence was not a tidiness problem. A defect can be *unobservable* locally
under that arrangement rather than merely unobserved: `pathlib` refuses to
instantiate a `WindowsPath` on POSIX up to 3.13 and allows it on 3.14, so a test
exercising that passed every local run and went red on four CI jobs, and no
amount of local care could have caught it.

`.python-version` is the single canonical declaration. Everything else must
agree with it, including the interpreter actually running this script -- a
consistent set of config files still proves nothing if the process reading them
is some other Python.

Wired in twice on purpose: as a pre-commit hook (so drift cannot be committed)
and as the FIRST step of CI's lint job (so it cannot be bypassed locally, and so
a green tick below it is known to be about the right interpreter).
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml

# `--python 3.14` / `--python=3.14` as passed to uv inside a workflow `run:` step.
UV_PYTHON_FLAG_RE = re.compile(r"--python[=\s]+(\d+\.\d+(?:\.\d+)?)")
VERSION_RE = re.compile(r"^(\d+)\.(\d+)")
# A `${{ ... }}` workflow expression: indirection that hides the declaration
# from this check.
WORKFLOW_EXPRESSION_RE = re.compile(r"\$\{\{.*\}\}")

CLASSIFIER_PREFIX = "Programming Language :: Python :: "

Version = tuple[int, int]


def parse_minor(text: str) -> Version | None:
    """Return (major, minor) from a version string, ignoring any patch part."""
    match = VERSION_RE.match(text.strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def format_version(version: Version) -> str:
    return f"{version[0]}.{version[1]}"


REQUIRES_PYTHON_RE = re.compile(r"^>=(\d+)\.(\d+)$")


def parse_requires_python_floor(raw: str) -> Version | None:
    """Return the `>=X.Y` floor from `requires-python`, or None if it is not that shape."""
    match = REQUIRES_PYTHON_RE.match(raw.replace(" ", ""))
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def check_requires_python(raw: str | None, declared: Version) -> list[str]:
    """Check `requires-python` -- a claim about CONSUMERS, not about this checkout.

    This deliberately does NOT require `requires-python` to name the one version
    the project develops on (AI-CLI-dgbd). It used to, producing
    `>=3.14,<3.15`, and that conflates two different questions:

    * which interpreter this repo builds, lints, type-checks and tests on -- one
      version, declared by `.python-version`, which is what the consolidation
      asked for and is unaffected here;
    * which interpreters an installed copy of a PUBLISHED package runs on.

    Measured 2026-09-27 before widening this: all 43 shipped modules compile
    under 3.11, 3.12 and 3.13; the package imports and `ai --help` runs on 3.13;
    the full `test_config`/`test_quota` slice passes 210/210 on 3.12 and 51/51 on
    3.11. So the narrow pin was not describing a real incompatibility.

    The upper bound is refused outright rather than merely not required. An upper
    bound on `requires-python` makes an already-published release uninstallable
    the day the next Python ships, for everyone, until a human cuts a new
    release -- and a resolver cannot route around it because the metadata is
    baked into the published artifact. "Forces a deliberate decision" was the
    argument for it, but the decision it forces is taken under an outage rather
    than before one.
    """
    if raw is None:
        return [
            "python-version-sync: pyproject.toml declares no requires-python -- an installer "
            "would then accept any interpreter, including ones this package cannot run on."
        ]
    floor = parse_requires_python_floor(raw)
    if floor is None:
        return [
            f"python-version-sync: requires-python is {raw!r}, which is not a bare '>=X.Y' floor. "
            "An upper bound makes an already-published release uninstallable the day the next "
            "Python ships, until a human cuts a new one; a consumer cannot route around metadata "
            "baked into the artifact. Declare the floor only."
        ]
    if floor > declared:
        return [
            f"python-version-sync: requires-python floor is {format_version(floor)} but "
            f".python-version declares {format_version(declared)} -- the project would be "
            "developed on an interpreter it tells installers it does not support."
        ]
    return []


def supported_minors(floor: Version, declared: Version) -> list[str]:
    """Every minor version from the `requires-python` floor up to the dev version."""
    return [f"{floor[0]}.{minor}" for minor in range(floor[1], declared[1] + 1)]


def read_declared_version(root: Path) -> tuple[Version | None, list[str]]:
    """Read the canonical version from `.python-version`."""
    path = root / ".python-version"
    if not path.exists():
        return None, [
            "python-version-sync: .python-version is missing -- it is the canonical "
            "declaration of the one Python version this project supports, and "
            "without it uv provisions whatever interpreter it happens to find."
        ]
    raw = path.read_text().strip()
    version = parse_minor(raw)
    if version is None:
        return None, [f"python-version-sync: .python-version does not contain a version number (found {raw!r})"]
    return version, []


def check_pyproject(text: str, declared: Version) -> list[str]:
    """Check `requires-python`, the pyright target and the trove classifiers."""
    expected = format_version(declared)
    errors: list[str] = []
    data = tomllib.loads(text)

    requires_python = data.get("project", {}).get("requires-python")
    errors.extend(check_requires_python(requires_python, declared))

    pyright_version = data.get("tool", {}).get("pyright", {}).get("pythonVersion")
    if pyright_version is None:
        errors.append(
            f"python-version-sync: [tool.pyright] declares no pythonVersion (expected {expected!r}) -- "
            "pyright would then infer a target, and an inferred target can differ "
            "from the interpreter CI runs."
        )
    elif pyright_version != expected:
        errors.append(
            f"python-version-sync: [tool.pyright] pythonVersion is {pyright_version!r} but "
            f".python-version declares {expected} -- pyright would accept or reject "
            "syntax and typing features for the wrong interpreter."
        )

    # `[tool.ruff] target-version` is deliberately NOT required to equal the
    # runtime version: it is the source-syntax floor, a separate question from
    # which interpreter the project is tested on. What it must never be is
    # *absent*, because ruff then infers it from `requires-python` -- and that
    # inference is what silently turned a runtime bump into a 29-file PEP 758
    # rewrite plus 19 UP037 findings. Requiring the pin is what keeps the two
    # decisions independent and each one visible.
    if "target-version" not in data.get("tool", {}).get("ruff", {}):
        errors.append(
            "python-version-sync: [tool.ruff] does not pin target-version -- ruff "
            "then infers it from requires-python, so changing the supported runtime "
            "silently changes which syntax the formatter writes and which upgrade "
            "rules fire. Pin it explicitly; it is the source-syntax floor, not the "
            "runtime version, and the two are allowed to differ."
        )

    classifiers = data.get("project", {}).get("classifiers", [])
    versioned = [
        c[len(CLASSIFIER_PREFIX) :] for c in classifiers if c.startswith(CLASSIFIER_PREFIX) and c[-1].isdigit()
    ]
    # `Programming Language :: Python :: 3` is a bare-major classifier, not a
    # claim about a minor version, so it is left alone.
    minors = [v for v in versioned if "." in v]
    # The classifiers advertise the SUPPORTED RANGE, so they are checked against
    # `requires-python`'s floor and the dev version together rather than against one
    # version. They are what PyPI shows an installer, so an installer reading them must
    # reach the same conclusion as a resolver reading `requires-python`: disagreement
    # between the two is a claim that contradicts itself.
    floor = parse_requires_python_floor(requires_python) if isinstance(requires_python, str) else None
    if floor is not None:
        wanted_minors = supported_minors(floor, declared)
        if minors != wanted_minors:
            errors.append(
                f"python-version-sync: pyproject.toml classifiers advertise Python "
                f"{minors or ['(none)']} but requires-python declares a floor of "
                f"{format_version(floor)} and .python-version declares {expected} -- "
                f"expected {wanted_minors}. The classifiers are what PyPI shows installers, "
                "so they must agree with the range a resolver would compute."
            )

    return errors


def _walk_python_version_keys(node: Any, path: str = "") -> list[tuple[str, Any]]:
    """Collect every `python-version` key in a parsed workflow, at any depth.

    Walking the parsed YAML rather than grepping the text catches the scalar
    form, the inline-list form and the block-list form identically, so
    reintroducing a matrix cannot slip past on formatting alone.
    """
    found: list[tuple[str, Any]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if key == "python-version":
                found.append((child, value))
            else:
                found.extend(_walk_python_version_keys(value, child))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_walk_python_version_keys(value, f"{path}[{index}]"))
    return found


def _walk_run_steps(node: Any) -> list[str]:
    """Collect every `run:` script body in a parsed workflow."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "run" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_walk_run_steps(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_walk_run_steps(value))
    return found


def check_workflow(name: str, text: str, declared: Version, floor: Version | None = None) -> list[str]:
    """Check one workflow file's `python-version` keys and uv `--python` flags.

    ``floor`` is the ``requires-python`` floor, and a ``--python`` flag naming it
    exactly is permitted alongside the declared version. That is one narrow
    exception, for the step that byte-compiles the shipped source on the oldest
    interpreter the package claims to support: the wider consumer claim has to be
    falsifiable somewhere, and this check would otherwise forbid the only step that
    tests it (AI-CLI-dgbd).

    It stays narrow deliberately. Only the floor is allowed, not any older version,
    and only via ``--python`` -- a ``python-version:`` key still has to name the
    declared version, so a job cannot quietly start running its whole suite on the
    floor and reintroduce the matrix this check exists to prevent.
    """
    expected = format_version(declared)
    errors: list[str] = []
    data = yaml.safe_load(text)

    for key_path, value in _walk_python_version_keys(data):
        values = value if isinstance(value, list) else [value]
        if len(values) > 1:
            errors.append(
                f"python-version-sync: {name} declares a python-version matrix at "
                f"{key_path} ({values}) -- this project supports exactly one version, "
                "so every job must pin it directly."
            )
        for item in values:
            item_text = str(item)
            if WORKFLOW_EXPRESSION_RE.search(item_text):
                errors.append(
                    f"python-version-sync: {name} sets {key_path} to the expression "
                    f"{item_text!r} -- the version must be written literally where this "
                    "check can read it, not indirected through a matrix or variable."
                )
                continue
            found = parse_minor(item_text)
            if found is None:
                errors.append(f"python-version-sync: {name} sets {key_path} to {item_text!r}, which is not a version")
            elif found != declared:
                errors.append(
                    f"python-version-sync: {name} sets {key_path} to {item_text!r} but "
                    f".python-version declares {expected}"
                )

    for script in _walk_run_steps(data):
        for match in UV_PYTHON_FLAG_RE.finditer(script):
            found = parse_minor(match.group(1))
            if found == declared or (floor is not None and found == floor):
                continue
            allowed = (
                expected if floor is None or floor == declared else f"{expected} or the floor {format_version(floor)}"
            )
            errors.append(
                f"python-version-sync: {name} passes `--python {match.group(1)}` but "
                f".python-version declares {expected} -- allowed: {allowed}"
            )

    return errors


def check_workflows(root: Path, declared: Version, floor: Version | None = None) -> list[str]:
    workflow_dir = root / ".github" / "workflows"
    if not workflow_dir.is_dir():
        return []
    errors: list[str] = []
    for path in sorted(workflow_dir.iterdir()):
        if path.suffix in {".yml", ".yaml"}:
            errors.extend(check_workflow(f".github/workflows/{path.name}", path.read_text(), declared, floor))
    return errors


def check_running_interpreter(declared: Version, running: Version | None = None) -> list[str]:
    """Check the interpreter executing this script against the declared version.

    The point of the whole exercise is that a local run predicts CI, and that
    only holds if the interpreter is the same one. A repo whose config files all
    agree while the developer's environment runs something else reproduces
    exactly the gap this check exists to close, which is why agreement between
    files is not on its own sufficient.
    """
    if running is None:
        running = sys.version_info[:2]
    if running == declared:
        return []
    return [
        f"python-version-sync: this check is running on Python {format_version(running)} "
        f"but .python-version declares {format_version(declared)} -- run it under the "
        "project environment (`uv run python scripts/check_python_version_sync.py`), "
        "and if the project venv itself is on the wrong version, delete `.venv` and "
        "re-run `uv sync --dev` so uv rebuilds it from `.python-version`."
    ]


def check(root: Path) -> list[str]:
    """Return a list of error messages; empty means everything agrees."""
    declared, errors = read_declared_version(root)
    if declared is None:
        return errors

    floor: Version | None = None
    pyproject_path = root / "pyproject.toml"
    if pyproject_path.exists():
        pyproject_text = pyproject_path.read_text()
        errors.extend(check_pyproject(pyproject_text, declared))
        # Read once and threaded through, so the workflow check and the classifier
        # check cannot disagree about what the floor is.
        raw = tomllib.loads(pyproject_text).get("project", {}).get("requires-python")
        floor = parse_requires_python_floor(raw) if isinstance(raw, str) else None
    errors.extend(check_workflows(root, declared, floor))
    errors.extend(check_running_interpreter(declared))
    return errors


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    errors = check(root)
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        print(
            f"\n{len(errors)} disagreement(s). This project supports ONE Python version; "
            "`.python-version` is canonical.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
