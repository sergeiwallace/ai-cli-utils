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
    parse_minor,
    parse_requires_python_floor,
    read_declared_version,
    supported_minors,
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
    requires_python: str = ">=3.11",
    pyright: str = "3.14",
    minors: tuple[str, ...] = ("3.11", "3.12", "3.13", "3.14"),
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


def test_given_a_bare_floor_when_parsed_then_the_floor_is_returned():
    assert parse_requires_python_floor(">=3.11") == (3, 11)
    assert parse_requires_python_floor(">= 3.11") == (3, 11)


def test_given_a_bounded_range_when_parsed_then_it_is_rejected():
    """An upper bound is not a floor, and must not be read as one."""
    assert parse_requires_python_floor(">=3.14,<3.15") is None
    assert parse_requires_python_floor("==3.14.*") is None


def test_given_a_floor_and_a_dev_version_when_listed_then_every_minor_between_is_named():
    assert supported_minors((3, 11), (3, 14)) == ["3.11", "3.12", "3.13", "3.14"]
    assert supported_minors((3, 14), (3, 14)) == ["3.14"]


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


def test_given_an_upper_bound_on_requires_python_when_checked_then_error():
    """The case this check exists to refuse, and the one it used to REQUIRE.

    An upper bound makes an already-published release uninstallable the day the
    next Python ships -- for everyone, until a human cuts a new release -- and a
    resolver cannot route around metadata baked into the published artifact
    (AI-CLI-dgbd). The classifier check also fires, because a bounded range has no
    floor to compute the advertised set from.
    """
    errors = check_pyproject(_pyproject(requires_python=">=3.14,<3.15"), (3, 14))

    assert any("requires-python" in e and "upper bound" in e for e in errors)


def test_given_a_floor_above_the_dev_version_when_checked_then_error():
    """Developing on an interpreter the package tells installers it does not support."""
    errors = check_pyproject(_pyproject(requires_python=">=3.15", minors=("3.15",)), (3, 14))

    assert any("floor is 3.15" in e for e in errors)


def test_given_a_floor_equal_to_the_dev_version_when_checked_then_no_errors():
    """A single supported version is still allowed -- it is just no longer required."""
    assert check_pyproject(_pyproject(requires_python=">=3.14", minors=("3.14",)), (3, 14)) == []


def test_given_a_requires_python_with_spaces_when_checked_then_no_errors():
    assert check_pyproject(_pyproject(requires_python=">= 3.11"), (3, 14)) == []


def test_given_classifiers_that_omit_a_supported_minor_when_checked_then_error():
    """PyPI shows the classifiers to installers, so they must agree with the resolver.

    Advertising fewer versions than `requires-python` accepts is a self-contradicting
    claim: a resolver would install on 3.12 while the page says it is unsupported.
    """
    errors = check_pyproject(_pyproject(requires_python=">=3.11", minors=("3.11", "3.14")), (3, 14))

    assert any("classifiers" in e for e in errors)


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
    pyproject = '[project]\nname = "x"\nrequires-python = ">=3.14"\nclassifiers = []\n'

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
    pyproject = PYPROJECT_TEMPLATE.format(requires_python=">=3.14", classifiers="", pyright="3.14")

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
            requires_python=f">=3.{minor}",
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


# --- the requires-python floor exception (AI-CLI-dgbd) -------------------------


_FLOOR_WORKFLOW = """\
name: CI
on: [push]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: astral-sh/setup-uv@v10
        with:
          python-version: "3.14"
      - run: uv run --python {version} --no-project python -m compileall -q src/
"""


def test_given_a_uv_python_flag_on_the_requires_python_floor_when_checked_then_no_errors():
    """The one step that makes the wider consumer claim falsifiable must be allowed.

    Without this exception the check forbids the only thing in CI that parses the
    shipped source as the oldest interpreter the package claims to support.
    """
    errors = check_workflow("ci.yml", _FLOOR_WORKFLOW.format(version="3.11"), (3, 14), floor=(3, 11))

    assert errors == []


def test_given_a_uv_python_flag_below_the_floor_when_checked_then_error():
    """The exception is the floor exactly, not "anything old" -- otherwise it is a hole."""
    errors = check_workflow("ci.yml", _FLOOR_WORKFLOW.format(version="3.10"), (3, 14), floor=(3, 11))

    assert len(errors) == 1
    assert "--python 3.10" in errors[0]


def test_given_a_uv_python_flag_between_the_floor_and_the_declared_version_when_checked_then_error():
    """A supported-but-not-floor version is still refused: it would be a second matrix."""
    errors = check_workflow("ci.yml", _FLOOR_WORKFLOW.format(version="3.12"), (3, 14), floor=(3, 11))

    assert len(errors) == 1
    assert "--python 3.12" in errors[0]


def test_given_no_floor_when_a_uv_python_flag_names_another_version_then_it_is_still_refused():
    """With no floor known, the original one-version rule applies unchanged."""
    errors = check_workflow("ci.yml", _FLOOR_WORKFLOW.format(version="3.11"), (3, 14), floor=None)

    assert len(errors) == 1
    assert "--python 3.11" in errors[0]


def test_given_a_python_version_key_naming_the_floor_when_checked_then_error():
    """The exception covers `--python` only.

    A `python-version:` key selects the interpreter a whole job runs on, so allowing
    the floor there would let a job quietly run its entire suite on the floor and
    reintroduce the matrix this check exists to prevent.
    """
    workflow = _FLOOR_WORKFLOW.format(version="3.11").replace('python-version: "3.14"', 'python-version: "3.11"')

    errors = check_workflow("ci.yml", workflow, (3, 14), floor=(3, 11))

    assert any("python-version" in e and "3.11" in e for e in errors)
