"""The Renovate rules that keep dependency updates from flooding hosted CI.

Every Renovate pull request runs the full Linux, macOS and Windows set. Three update
branches that could never pass were rebased onto ``main`` after every merge, so each
merge started three more full runs that failed the same way. These tests pin the
configuration that stops that: rebase only on a conflict, move ruff's two pins in one
pull request, and leave the interpreter version to a deliberate change.
docs/bugs/ai-cli-kreio-hosted-ci-queue-demand.md records the measurements.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "renovate.json5"


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _matches(rule: dict[str, Any], key: str, value: str) -> bool:
    """Whether one ``match*`` list of a package rule selects this value.

    Implements the parts of Renovate's matcher these rules use: exact names,
    ``!``-negated names, and the rule that a list of only negations matches
    everything it does not exclude.
    """
    patterns = rule.get(key)
    if patterns is None:
        return True
    positives = [p for p in patterns if not p.startswith("!")]
    negatives = [p[1:] for p in patterns if p.startswith("!")]
    if value in negatives:
        return False
    return not positives or value in positives


def effective(config: dict[str, Any], manager: str, package: str) -> dict[str, Any]:
    """The settings Renovate applies to one dependency: package rules merged in order."""
    result: dict[str, Any] = {"enabled": config.get(manager, {}).get("enabled", manager != "pre-commit")}
    for rule in config.get("packageRules", []):
        if _matches(rule, "matchManagers", manager) and _matches(rule, "matchPackageNames", package):
            result.update({k: v for k, v in rule.items() if not k.startswith("match") and k != "description"})
    return result


def test_given_the_renovate_config_when_read_then_it_is_plain_json() -> None:
    """A JSON5 comment would hide a rule from these tests, so the file stays plain JSON."""
    assert isinstance(load_config(), dict)


def test_given_an_update_branch_behind_main_when_main_moves_then_renovate_rebases_only_on_a_conflict() -> None:
    """The ruleset does not require branches to be up to date, so a rebase on every merge buys nothing."""
    assert load_config()["rebaseWhen"] == "conflicted"


def test_given_a_new_ruff_release_when_renovate_updates_then_both_ruff_pins_move_in_one_pull_request() -> None:
    config = load_config()
    library = effective(config, "pep621", "ruff")
    hook = effective(config, "pre-commit", "astral-sh/ruff-pre-commit")

    assert library["enabled"] and hook["enabled"]
    assert library.get("groupName") == hook.get("groupName") == "ruff"


@pytest.mark.parametrize(
    "package", ["pre-commit/pre-commit-hooks", "DavidAnson/markdownlint-cli2", "shellcheck-py/shellcheck-py"]
)
def test_given_another_pre_commit_hook_when_renovate_reads_the_config_then_it_opens_no_pull_request(
    package: str,
) -> None:
    """Enabling the pre-commit manager for ruff must not add a pull request per hook."""
    assert effective(load_config(), "pre-commit", package)["enabled"] is False


def test_given_a_new_python_release_when_renovate_reads_the_interpreter_pin_then_it_opens_no_pull_request() -> None:
    """One `.python-version` bump fails the version-sync check by design: the version lives in several files."""
    assert effective(load_config(), "pyenv", "python")["enabled"] is False
