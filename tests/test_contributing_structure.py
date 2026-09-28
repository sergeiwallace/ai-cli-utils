"""CONTRIBUTING.md's Project Structure must describe modules that actually exist.

That section is hand-maintained prose, so it drifts silently: nothing fails when a module
is deleted and the doc keeps advertising it. Measured 2026-09-28, it still listed
``handoff.py`` as a module of this package four weeks after commit 86c41d0 deleted it
(AI-CLI-2qu) -- so the first thing a new contributor read about the layout named a file
they could not open.

This is the cheap half of "staleness is a bug". It does not check that every real module is
DOCUMENTED, on purpose: the listing is an orientation subset, not an inventory, and
requiring completeness would either force 38 entries into a doc meant to be skimmed or be
quietly relaxed later. The asymmetry is deliberate -- a documented module that does not
exist misleads a reader, while an undocumented one merely leaves them to look.
"""

from __future__ import annotations

import re
from pathlib import Path

_PROJECT_STRUCTURE_HEADING = "## Project Structure"

# A listing line inside the fenced block: two spaces, a module name, then a comment.
# Anchored on the `.py` so the `src/ai_cli/` and `tests/` header lines are not matched.
_MODULE_LINE = re.compile(r"^\s{2}(?P<name>[A-Za-z_][A-Za-z0-9_]*\.py)\s")


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def documented_src_modules(root: Path) -> list[str]:
    """Module filenames the Project Structure block attributes to ``src/ai_cli/``.

    Reads only as far as the ``tests/`` sub-heading inside the block, because the same
    shape is reused there for test files and those are checked separately below.
    """
    text = (root / "CONTRIBUTING.md").read_text(encoding="utf-8")
    _, _, after_heading = text.partition(_PROJECT_STRUCTURE_HEADING)
    _, _, after_fence = after_heading.partition("src/ai_cli/")
    block, _, _ = after_fence.partition("tests/")
    return [match["name"] for line in block.splitlines() if (match := _MODULE_LINE.match(line))]


def documented_test_modules(root: Path) -> list[str]:
    text = (root / "CONTRIBUTING.md").read_text(encoding="utf-8")
    _, _, after_heading = text.partition(_PROJECT_STRUCTURE_HEADING)
    _, _, after_tests = after_heading.partition("tests/")
    block, _, _ = after_tests.partition("```")
    return [match["name"] for line in block.splitlines() if (match := _MODULE_LINE.match(line))]


def test_given_the_project_structure_block_when_read_then_every_named_module_exists() -> None:
    root = _repo_root()
    missing = [name for name in documented_src_modules(root) if not (root / "src" / "ai_cli" / name).is_file()]

    assert not missing, (
        "CONTRIBUTING.md's Project Structure names src/ai_cli modules that do not exist: "
        f"{missing}. Delete the stale entries — a contributor reading that section is being "
        "pointed at files they cannot open (AI-CLI-2qu left `handoff.py` there for four weeks "
        "after it was deleted)."
    )


def test_given_the_project_structure_block_when_read_then_every_named_test_file_exists() -> None:
    root = _repo_root()
    missing = [name for name in documented_test_modules(root) if not (root / "tests" / name).is_file()]

    assert not missing, f"CONTRIBUTING.md's Project Structure names tests/ files that do not exist: {missing}"


def test_given_the_parser_when_run_then_it_found_modules_to_check() -> None:
    """Guards the guard: a parser that matches nothing cannot fail usefully.

    Reformatting the block, renaming the heading, or changing the indentation would
    otherwise turn both assertions above into permanent passes — the failure mode that
    makes a green suite mean less than it appears to.
    """
    root = _repo_root()
    src_modules = documented_src_modules(root)
    test_modules = documented_test_modules(root)

    assert len(src_modules) >= 5, f"expected the documented module list, parsed {src_modules}"
    assert "main.py" in src_modules, f"main.py should always be documented, parsed {src_modules}"
    assert len(test_modules) >= 2, f"expected the documented test list, parsed {test_modules}"


def test_given_a_block_naming_a_deleted_module_when_checked_then_it_is_flagged(tmp_path: Path) -> None:
    """Positive control, in the exact shape the defect had before this guard existed."""
    (tmp_path / "src" / "ai_cli").mkdir(parents=True)
    (tmp_path / "src" / "ai_cli" / "main.py").touch()
    (tmp_path / "CONTRIBUTING.md").write_text(
        "## Project Structure\n\n```text\nsrc/ai_cli/\n  main.py          # entry point\n"
        "  handoff.py       # deleted four weeks ago\n\ntests/\n  test_main.py     # tests\n```\n",
        encoding="utf-8",
    )

    documented = documented_src_modules(tmp_path)

    assert documented == ["main.py", "handoff.py"]
    assert [name for name in documented if not (tmp_path / "src" / "ai_cli" / name).is_file()] == ["handoff.py"]
