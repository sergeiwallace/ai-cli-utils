"""Regression coverage for development-only test dependencies."""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_given_parallel_pytest_configuration_when_reading_dev_extra_then_required_plugins_are_declared():
    with (Path(__file__).resolve().parent.parent / "pyproject.toml").open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)

    dev_dependencies = pyproject["project"]["optional-dependencies"]["dev"]

    assert any(dependency.startswith("pytest-xdist") for dependency in dev_dependencies)
    assert any(dependency.startswith("pytest-timeout") for dependency in dev_dependencies)
