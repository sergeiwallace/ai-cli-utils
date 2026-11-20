"""Standing guard that every check CI's lint job runs is decided about locally.

CI's ``lint`` job is where this repo's static analysis actually runs. The local gate
every session runs before merging was ``ruff check`` + ``ruff format`` + ``pytest``,
which is a strict subset of it, so a **type error passed every local gate and landed
on main** (AI-CLI-ckzk). Measured 2026-09-27 while reviewing a branch whose gate had
been reported clean: ``uv run pyright src/`` reported
``main.py:3156 - error: Argument of type "Path | None" cannot be assigned to
parameter "path" of type "Path"``, which nothing local would ever have shown.

``pyright`` is now a pre-commit hook, so that specific
gap is closed. This guard is about the gap *class*: the two definitions live in two
files that no single edit has to touch together, so the next check added to CI would
re-open it silently and be discovered the same way -- by accident, months later.

What it enforces is deliberately not "the two lists are identical". Some CI checks
should not run locally, and pretending otherwise would make the guard a nuisance that
gets weakened. It enforces that **every** lint-job command is classified as either
locally enforced (naming the hook that does it) or intentionally CI-only (naming the
reason). Adding a check to CI without deciding which it is fails this test. Both
classifications are asserted to be real: a named hook must exist in
``.pre-commit-config.yaml``, so a renamed or deleted hook fails here too rather than
leaving a mapping that merely claims coverage.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

# Each entry maps one lint-job command to the pre-commit hook id that runs the same
# check locally. The pattern identifies the command; the id is verified to exist.
_LOCALLY_ENFORCED: tuple[tuple[str, str], ...] = (
    (r"\bscripts/check_python_version_sync\.py\b", "python-version-sync"),
    (r"\bruff\s+check\b", "ruff-check"),
    (r"\bruff\s+format\b", "ruff-format"),
    (r"\bpyright\b", "pyright"),
)

# Lint-job commands that intentionally have no local counterpart. A reason is required
# because "we did not get round to it" and "this cannot sensibly run locally" are
# different states, and only the second one is allowed to persist silently.
_CI_ONLY: tuple[tuple[str, str], ...] = (
    (
        r"\buv\s+sync\b",
        "environment provisioning, not a check -- a local run already has its venv, "
        "and `tests/test_ci_lock_assertion.py` is what guards this step's --locked flag",
    ),
    (
        r"\bcompileall\b",
        "needs a SECOND interpreter (the 3.11 floor) that a local gate would have to "
        "download on every machine to verify a claim about the published artifact; CI "
        "provisions interpreters for free, so requiring it locally would buy nothing "
        "and cost every contributor a toolchain",
    ),
)


# Lint-job steps that prepare a self-hosted runner rather than check anything, keyed by
# step name. They are decided about as whole steps because their shell lines (a mkdir,
# an installer download, a GITHUB_ENV write) are not checks and would each need a
# pattern of their own. A name listed here must still exist in the lint job.
_RUNNER_PROVISIONING_STEPS: dict[str, str] = {
    "Use a job-scoped home on a self-hosted runner": (
        "environment provisioning for a self-hosted attempt, not a check -- a local run already has a writable home"
    ),
    "Install uv on a self-hosted runner": (
        "environment provisioning for a self-hosted attempt, not a check -- a local run "
        "already has uv, and the GitHub-hosted path installs it with setup-uv"
    ),
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _lint_job_steps(root: Path) -> list[dict]:
    workflow = yaml.safe_load((root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    return workflow["jobs"]["lint"]["steps"]


def lint_job_commands(root: Path) -> list[str]:
    """Every ``run:`` command in the ``lint`` job, one per logical command.

    A ``run: |`` block holds several commands, so the block is split on newlines --
    without that, a multi-command block would be classified by whichever check
    happened to match first and the rest would go unexamined.
    """
    commands: list[str] = []
    for step in _lint_job_steps(root):
        script = step.get("run")
        if not script:
            continue  # a `uses:` step runs no command of its own
        if step.get("name") in _RUNNER_PROVISIONING_STEPS:
            continue
        commands.extend(line.strip() for line in script.splitlines() if line.strip())
    return commands


def configured_hook_ids(root: Path) -> set[str]:
    config = yaml.safe_load((root / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    return {hook["id"] for repo in config["repos"] for hook in repo["hooks"]}


def classify(command: str) -> tuple[str, str] | None:
    """``(kind, detail)`` for one command, or ``None`` when nothing classifies it.

    Returns ``("ambiguous", …)`` when more than one classifier matches, which is a
    failure rather than a pass: a command matching both a local hook and a CI-only
    exemption means one of the two patterns is wrong, and silently preferring either
    would hide that.
    """
    matched = [("local", hook_id) for pattern, hook_id in _LOCALLY_ENFORCED if re.search(pattern, command)]
    matched += [("ci-only", reason) for pattern, reason in _CI_ONLY if re.search(pattern, command)]
    if not matched:
        return None
    if len(matched) > 1:
        return ("ambiguous", f"{len(matched)} classifiers matched: {[m[1] for m in matched]}")
    return matched[0]


def test_given_the_ci_lint_job_when_scanned_then_every_check_is_classified() -> None:
    """The load-bearing assertion: no lint-job check is silently absent locally."""
    unclassified = [command for command in lint_job_commands(_repo_root()) if classify(command) is None]

    assert not unclassified, (
        "CI's lint job runs a check this repo has not decided about locally. Either add a "
        "pre-commit hook and map it in _LOCALLY_ENFORCED, or record why it is CI-only in "
        "_CI_ONLY. Leaving it unmapped is how a type error reached main (AI-CLI-ckzk):\n"
        + "\n".join(f"  {command}" for command in unclassified)
    )


def test_given_the_ci_lint_job_when_scanned_then_no_check_is_ambiguously_classified() -> None:
    ambiguous = [
        (command, classify(command))
        for command in lint_job_commands(_repo_root())
        if (classify(command) or ("",))[0] == "ambiguous"
    ]

    assert not ambiguous, f"a lint-job command matched more than one classifier, so one pattern is wrong: {ambiguous}"


def test_given_the_local_mapping_when_checked_then_every_named_hook_exists() -> None:
    """A mapping that names a hook which does not exist claims coverage it lacks.

    This is what turns a hook rename or deletion into a failure here, rather than into
    a quietly false claim that the check still runs locally.
    """
    configured = configured_hook_ids(_repo_root())
    missing = sorted({hook_id for _, hook_id in _LOCALLY_ENFORCED} - configured)

    assert not missing, f"_LOCALLY_ENFORCED names pre-commit hooks that are not configured: {missing}"


def test_given_the_provisioning_exemptions_when_checked_then_each_names_a_self_hosted_only_lint_step() -> None:
    """An exemption for a step that is gone, or that runs everywhere, hides a real check.

    A renamed provisioning step would leave a stale name here that exempts nothing, and
    a step that also ran on GitHub-hosted runners could carry a check past the
    classifier under a provisioning label.
    """
    steps = {step.get("name"): step for step in _lint_job_steps(_repo_root())}
    missing = sorted(set(_RUNNER_PROVISIONING_STEPS) - set(steps))
    unguarded = sorted(
        name
        for name in _RUNNER_PROVISIONING_STEPS
        if name in steps and steps[name].get("if") != "runner.environment == 'self-hosted'"
    )

    assert not missing, f"_RUNNER_PROVISIONING_STEPS names lint steps that do not exist: {missing}"
    assert not unguarded, f"provisioning exemptions must only run on a self-hosted runner: {unguarded}"


def test_given_the_pyright_hook_when_read_then_it_runs_before_code_leaves_the_machine() -> None:
    """pyright must actually gate a push, not merely exist.

    A hook configured at no useful stage, or pointed at the wrong tree, would satisfy
    the mapping above while enforcing nothing -- so the specific defect AI-CLI-ckzk
    describes is pinned directly rather than only through the id.
    """
    config = yaml.safe_load((_repo_root() / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hook = next(h for repo in config["repos"] for h in repo["hooks"] if h["id"] == "pyright")

    assert hook["stages"] == ["pre-push"], f"pyright must gate the push, got stages={hook.get('stages')}"
    assert "src/" in hook["entry"], f"pyright must check the shipped tree, got entry={hook['entry']!r}"
    assert hook["always_run"] is True, "pyright must run even when no Python file is staged"


def test_given_the_ci_lint_job_when_scanned_then_it_still_has_checks_to_compare() -> None:
    """Guards the guard: a scanner that finds no commands cannot fail usefully.

    Renaming the job, or any change that makes the lookup return an empty list, would
    otherwise turn every assertion above into a permanent pass.
    """
    commands = lint_job_commands(_repo_root())

    assert len(commands) >= 6, f"expected the lint job's checks, found {len(commands)}: {commands}"
    assert any(re.search(r"\bpyright\b", command) for command in commands), (
        "the lint job no longer runs pyright, so this guard is comparing against nothing"
    )


@pytest.mark.parametrize(
    ("command", "expected_kind"),
    [
        ("uv run pyright src/", "local"),
        ("uv run ruff check src/ tests/", "local"),
        ("uv sync --locked --dev", "ci-only"),
        ("uv run --python 3.11 --no-project python -m compileall -q src/", "ci-only"),
    ],
)
def test_given_a_known_command_when_classified_then_it_lands_in_the_right_bucket(
    command: str, expected_kind: str
) -> None:
    """Negative control on the classifier itself.

    Without this, a classifier that matched everything would make the load-bearing
    test pass unconditionally -- the failure mode that makes a green suite mean less
    than it appears to.
    """
    result = classify(command)

    assert result is not None, f"{command!r} should be classified"
    assert result[0] == expected_kind


def test_given_an_unmapped_check_when_classified_then_it_is_not_silently_accepted() -> None:
    """Positive control, in the shape a newly added CI check would arrive in."""
    assert classify("uv run bandit -r src/") is None
