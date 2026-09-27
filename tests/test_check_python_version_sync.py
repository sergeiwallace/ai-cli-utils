"""Tests for scripts/check_python_version_sync.py (AI-CLI-6rwp single-version gate).

Each mismatch branch is exercised against a synthetic tree in tmp_path, so the
gate is proven to FAIL on a deliberately wrong value rather than merely proven to
pass on the real repo -- a check that has only ever been seen returning zero is
not known to be a check at all. The final tests assert against the real repo, so
a future edit to `.python-version`, a workflow or `pyproject.toml` that reopens
the drift this gate was written to prevent fails here too.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_python_version_sync import (
    check,
    check_pyproject,
    check_running_interpreter,
    check_workflow,
    expected_requires_python,
    parse_minor,
    read_declared_version,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

PYPROJECT_TEMPLATE = """\
[project]
name = "x"
requires-python = "{requires_python}"
classifiers = [
    "Programming Language :: Python :: 3",
{classifiers}
]

[tool.pyright]
pythonVersion = "{pyright}"

[tool.ruff]
target-version = "py311"
"""

WORKFLOW_TEMPLATE = """\
name: CI
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: astral-sh/setup-uv@v10
        with:
          python-version: {python_version}
      - run: uv run pytest
"""


def _pyproject(
    requires_python: str = ">=3.14,<3.15",
    pyright: str = "3.14",
    minors: tuple[str, ...] = ("3.14",),
) -> str:
    classifiers = "\n".join(f'    "Programming Language :: Python :: {m}",' for m in minors)
    return PYPROJECT_TEMPLATE.format(requires_python=requires_python, classifiers=classifiers, pyright=pyright)


def _tree(root: Path, declared: str = "3.14", pyproject: str | None = None, workflow: str | None = None) -> None:
    (root / ".python-version").write_text(f"{declared}\n")
    (root / "pyproject.toml").write_text(pyproject if pyproject is not None else _pyproject())
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(
        workflow if workflow is not None else WORKFLOW_TEMPLATE.format(python_version='"3.14"')
    )


# --- version parsing -------------------------------------------------------


def test_given_a_patch_level_version_when_parsed_then_the_patch_is_ignored():
    assert parse_minor("3.14.7") == (3, 14)


def test_given_a_non_version_string_when_parsed_then_none():
    assert parse_minor("pypy3.10") is None


def test_given_a_declared_version_when_building_the_bound_then_it_spans_one_minor():
    assert expected_requires_python((3, 14)) == ">=3.14,<3.15"


# --- .python-version -------------------------------------------------------


def test_given_no_python_version_file_when_read_then_it_is_reported_missing(tmp_path):
    declared, errors = read_declared_version(tmp_path)

    assert declared is None
    assert len(errors) == 1
    assert ".python-version is missing" in errors[0]


def test_given_an_unparseable_python_version_file_when_read_then_error(tmp_path):
    (tmp_path / ".python-version").write_text("system\n")

    declared, errors = read_declared_version(tmp_path)

    assert declared is None
    assert "does not contain a version number" in errors[0]


# --- pyproject.toml --------------------------------------------------------


def test_given_agreeing_pyproject_fields_when_checked_then_no_errors():
    assert check_pyproject(_pyproject(), (3, 14)) == []


def test_given_an_open_ended_requires_python_when_checked_then_error():
    errors = check_pyproject(_pyproject(requires_python=">=3.14"), (3, 14))

    assert len(errors) == 1
    assert "requires-python" in errors[0]
    assert ">=3.14,<3.15" in errors[0]


def test_given_a_requires_python_for_another_version_when_checked_then_error():
    errors = check_pyproject(_pyproject(requires_python=">=3.11,<3.12"), (3, 14))

    assert len(errors) == 1
    assert "requires-python" in errors[0]


def test_given_a_requires_python_with_spaces_when_checked_then_no_errors():
    assert check_pyproject(_pyproject(requires_python=">=3.14, <3.15"), (3, 14)) == []


def test_given_a_missing_requires_python_when_checked_then_error():
    pyproject = '[project]\nname = "x"\nclassifiers = []\n[tool.pyright]\npythonVersion = "3.14"\n'

    errors = check_pyproject(pyproject, (3, 14))

    assert any("declares no requires-python" in e for e in errors)


def test_given_a_stale_pyright_target_when_checked_then_error():
    errors = check_pyproject(_pyproject(pyright="3.11"), (3, 14))

    assert len(errors) == 1
    assert "pythonVersion" in errors[0]
    assert "3.11" in errors[0]


def test_given_no_pyright_target_when_checked_then_error():
    pyproject = '[project]\nname = "x"\nrequires-python = ">=3.14,<3.15"\nclassifiers = []\n'

    errors = check_pyproject(pyproject, (3, 14))

    assert any("declares no pythonVersion" in e for e in errors)
    assert any("classifiers" in e for e in errors)


def test_given_an_explicit_ruff_target_when_checked_then_no_errors():
    assert check_pyproject(_pyproject(), (3, 14)) == []


def test_given_a_ruff_target_below_the_runtime_version_when_checked_then_no_errors():
    """The source-syntax floor may lag the runtime; only an absent pin is a fault.

    Ruff's `target-version` answers "what syntax must this source stay
    compatible with", which is a different question from "which interpreter is
    this tested on". Requiring them to be equal would force a PEP 758 source
    rewrite every time the runtime moves.
    """
    pyproject = _pyproject().replace('target-version = "py311"', 'target-version = "py312"')

    assert check_pyproject(pyproject, (3, 14)) == []


def test_given_no_ruff_target_pin_when_checked_then_error():
    pyproject = _pyproject().replace('[tool.ruff]\ntarget-version = "py311"\n', "")

    errors = check_pyproject(pyproject, (3, 14))

    assert len(errors) == 1
    assert "does not pin target-version" in errors[0]


def test_given_a_ruff_table_without_a_target_when_checked_then_error():
    pyproject = _pyproject().replace('target-version = "py311"', "line-length = 120")

    errors = check_pyproject(pyproject, (3, 14))

    assert len(errors) == 1
    assert "does not pin target-version" in errors[0]


def test_given_classifiers_for_several_minors_when_checked_then_error():
    errors = check_pyproject(_pyproject(minors=("3.11", "3.12", "3.13")), (3, 14))

    assert len(errors) == 1
    assert "classifiers" in errors[0]


def test_given_only_the_bare_major_classifier_when_checked_then_the_minor_is_still_required():
    pyproject = PYPROJECT_TEMPLATE.format(requires_python=">=3.14,<3.15", classifiers="", pyright="3.14")

    errors = check_pyproject(pyproject, (3, 14))

    assert len(errors) == 1
    assert "classifiers" in errors[0]


# --- workflows -------------------------------------------------------------


def test_given_a_workflow_pinning_the_declared_version_when_checked_then_no_errors():
    workflow = WORKFLOW_TEMPLATE.format(python_version='"3.14"')

    assert check_workflow("ci.yml", workflow, (3, 14)) == []


def test_given_a_workflow_pinning_another_version_when_checked_then_error():
    workflow = WORKFLOW_TEMPLATE.format(python_version='"3.12"')

    errors = check_workflow("ci.yml", workflow, (3, 14))

    assert len(errors) == 1
    assert "3.12" in errors[0]


def test_given_a_reintroduced_inline_matrix_when_checked_then_error():
    workflow = WORKFLOW_TEMPLATE.format(python_version='["3.11", "3.12", "3.13"]')

    errors = check_workflow("ci.yml", workflow, (3, 14))

    assert any("matrix" in e for e in errors)


def test_given_a_reintroduced_block_matrix_when_checked_then_error():
    workflow = """\
name: CI
on: [push]
jobs:
  test:
    strategy:
      matrix:
        python-version:
          - "3.13"
          - "3.14"
    steps:
      - run: uv run pytest
"""

    errors = check_workflow("ci.yml", workflow, (3, 14))

    assert any("matrix" in e for e in errors)


def test_given_a_single_item_matrix_on_another_version_when_checked_then_error():
    workflow = WORKFLOW_TEMPLATE.format(python_version='["3.12"]')

    errors = check_workflow("ci.yml", workflow, (3, 14))

    assert len(errors) == 1
    assert "3.12" in errors[0]


def test_given_a_matrix_expression_reference_when_checked_then_the_indirection_is_rejected():
    workflow = WORKFLOW_TEMPLATE.format(python_version='"${{ matrix.python-version }}"')

    errors = check_workflow("ci.yml", workflow, (3, 14))

    assert len(errors) == 1
    assert "indirected" in errors[0]


def test_given_a_non_version_python_version_value_when_checked_then_error():
    workflow = WORKFLOW_TEMPLATE.format(python_version='"pypy-3.10"')

    errors = check_workflow("ci.yml", workflow, (3, 14))

    assert any("not a version" in e for e in errors)


def test_given_a_uv_python_flag_on_another_version_when_checked_then_error():
    workflow = """\
name: CI
on: [push]
jobs:
  test:
    steps:
      - run: uv sync --dev --python 3.12
"""

    errors = check_workflow("ci.yml", workflow, (3, 14))

    assert len(errors) == 1
    assert "--python 3.12" in errors[0]


def test_given_a_uv_python_flag_on_the_declared_version_when_checked_then_no_errors():
    workflow = """\
name: CI
on: [push]
jobs:
  test:
    steps:
      - run: uv run --python 3.14 pytest
"""

    assert check_workflow("ci.yml", workflow, (3, 14)) == []


# --- running interpreter ---------------------------------------------------


def test_given_the_running_interpreter_matches_when_checked_then_no_errors():
    assert check_running_interpreter((3, 14), running=(3, 14)) == []


def test_given_the_running_interpreter_differs_when_checked_then_error():
    errors = check_running_interpreter((3, 14), running=(3, 12))

    assert len(errors) == 1
    assert "running on Python 3.12" in errors[0]
    assert "declares 3.14" in errors[0]


def test_given_no_running_version_supplied_when_checked_then_the_live_interpreter_is_used():
    assert check_running_interpreter(sys.version_info[:2]) == []


# --- whole-tree ------------------------------------------------------------


def test_given_a_fully_consistent_tree_when_checked_then_no_errors(tmp_path):
    _tree(tmp_path, declared=".".join(str(p) for p in sys.version_info[:2]))
    minor = sys.version_info[1]
    (tmp_path / "pyproject.toml").write_text(
        _pyproject(
            requires_python=f">=3.{minor},<3.{minor + 1}",
            pyright=f"3.{minor}",
            minors=(f"3.{minor}",),
        )
    )
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(WORKFLOW_TEMPLATE.format(python_version=f'"3.{minor}"'))

    assert check(tmp_path) == []


def test_given_a_tree_disagreeing_everywhere_when_checked_then_every_source_is_reported(tmp_path):
    _tree(
        tmp_path,
        declared="3.14",
        pyproject=_pyproject(requires_python=">=3.11", pyright="3.11", minors=("3.11", "3.12")),
        workflow=WORKFLOW_TEMPLATE.format(python_version='["3.11", "3.12", "3.13"]'),
    )

    errors = check(tmp_path)

    assert any("requires-python" in e for e in errors)
    assert any("pythonVersion" in e for e in errors)
    assert any("classifiers" in e for e in errors)
    assert any("matrix" in e for e in errors)


def test_given_a_missing_python_version_file_when_checked_then_nothing_else_is_reported(tmp_path):
    (tmp_path / "pyproject.toml").write_text(_pyproject(requires_python=">=3.11"))

    errors = check(tmp_path)

    assert len(errors) == 1
    assert ".python-version is missing" in errors[0]


# --- the real repo ---------------------------------------------------------


def test_given_this_repository_when_checked_then_every_source_agrees():
    assert check(REPO_ROOT) == []


def test_given_this_repository_when_the_suite_runs_then_it_is_on_the_declared_interpreter():
    declared, errors = read_declared_version(REPO_ROOT)

    assert errors == []
    assert sys.version_info[:2] == declared
