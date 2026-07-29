"""Tests for scripts/check_ruff_version_sync.py (AIH-473 ruff pin/rev gate)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_ruff_version_sync import check

PRECOMMIT_TEMPLATE = """\
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v{rev}
    hooks:
      - id: ruff-check
      - id: ruff-format
"""


def _write(root: Path, precommit_rev: str, pyproject_ruff_line: str) -> None:
    (root / ".pre-commit-config.yaml").write_text(PRECOMMIT_TEMPLATE.format(rev=precommit_rev))
    (root / "pyproject.toml").write_text(
        f"[project]\nname = 'x'\n[project.optional-dependencies]\ndev = [{pyproject_ruff_line}]\n"
    )


def test_given_matching_pins_when_checked_then_no_errors(tmp_path):
    _write(tmp_path, "0.16.0", '"ruff==0.16.0"')

    assert check(tmp_path) == []


def test_given_mismatched_pins_when_checked_then_one_error(tmp_path):
    _write(tmp_path, "0.16.0", '"ruff==0.15.21"')

    errors = check(tmp_path)

    assert len(errors) == 1
    assert "0.16.0" in errors[0]
    assert "0.15.21" in errors[0]


def test_given_floor_instead_of_pin_when_checked_then_error(tmp_path):
    _write(tmp_path, "0.16.0", '"ruff>=0.8"')

    errors = check(tmp_path)

    assert len(errors) == 1
    assert "floor" in errors[0]


def test_given_no_ruff_dependency_when_checked_then_error(tmp_path):
    _write(tmp_path, "0.16.0", '"pytest>=8"')

    errors = check(tmp_path)

    assert len(errors) == 1
    assert "no ruff dependency pin" in errors[0]


def test_given_no_precommit_config_when_checked_then_no_errors(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\ndependencies = []\n")

    assert check(tmp_path) == []


def test_given_no_ruff_hook_in_precommit_when_checked_then_no_errors(tmp_path):
    (tmp_path / ".pre-commit-config.yaml").write_text(
        "repos:\n  - repo: https://github.com/pre-commit/pre-commit-hooks\n"
        "    rev: v6.0.0\n    hooks:\n      - id: check-yaml\n"
    )
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")

    assert check(tmp_path) == []
