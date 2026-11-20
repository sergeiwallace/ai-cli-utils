"""The hosted-first runner fallback contract, checked on every job of every workflow.

Every job asks for its GitHub-hosted label on attempt 1. A later attempt asks for a
self-hosted label set only when the run is trusted (a push, a manual dispatch or a pull
request whose head is this repository) and the repository variable
``CI_FALLBACK_RUNS_ON`` maps that exact ``<run_id>-<run_attempt>-<os>`` key to a label
array. The variable is written by an external watchdog for one run attempt at a time;
this repository never names a self-hosted label.

The routing tests evaluate each job's real ``runs-on`` expression against a table of
contexts, so they prove where a job would run rather than only what the text looks
like. The evaluator below implements the subset of GitHub's expression language these
workflows use, with GitHub's documented truthiness, short-circuit and loose-equality
rules: values of different types compare as numbers, and strings compare
case-insensitively.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
VARIABLE = "CI_FALLBACK_RUNS_ON"
REPOSITORY = "owner/myproject"
RUN_ID = "4242"

WORKFLOW_OS_KEYS = {
    "ci.yml": "linux",
    "ci-windows.yml": "windows",
    "ci-macos.yml": "macos",
    "publish.yml": "release",
    "wiki.yml": "release",
}

# Every job and the hosted label it ran on before the contract existed. A job that
# appears, disappears or moves between workflows fails the inventory test, because the
# four required check names (`lint`, `test`, `test-windows`, `test-macos`) must not
# change.
EXPECTED_JOBS = {
    "ci.yml": {"lint": "ubuntu-latest", "test": "ubuntu-latest"},
    "ci-windows.yml": {"test-windows-shard": "windows-latest", "test-windows": "ubuntu-latest"},
    "ci-macos.yml": {"test-macos-shard": "macos-latest", "test-macos": "ubuntu-latest"},
    "publish.yml": {"publish": "ubuntu-latest", "release": "ubuntu-latest"},
    "wiki.yml": {"build": "ubuntu-latest", "publish": "ubuntu-latest"},
}

# The operating-system keys whose jobs have self-hosted step variants. Windows has no
# fallback pool, so its steps run unchanged wherever the job lands.
SELF_HOSTED_OS_KEYS = {"linux", "macos", "release"}

CI_WORKFLOWS = ("ci.yml", "ci-windows.yml", "ci-macos.yml")
CI_TRIGGERS = {"push": {"branches": ["main"]}, "pull_request": None, "workflow_dispatch": None}


def contract_expression(os_key: str, hosted: str) -> str:
    """The one ``runs-on`` expression a job with this OS key and hosted label must use."""
    return (
        "${{ github.run_attempt > 1 && (github.event_name != 'pull_request' || "
        "github.event.pull_request.head.repo.full_name == github.repository) && "
        f"fromJSON(vars.{VARIABLE} || '{{}}')[format('{{0}}-{{1}}-{os_key}', github.run_id, github.run_attempt)]"
        f" || '{hosted}' }}}}"
    )


# --- expression evaluator -----------------------------------------------------------

_TOKEN_RE = re.compile(
    r"\s*(?:(?P<num>\d+(?:\.\d+)?)|(?P<str>'(?:[^']|'')*')|(?P<op>&&|\|\||==|!=|<=|>=|[<>!()\[\].,])"
    r"|(?P<ident>[A-Za-z_][A-Za-z0-9_-]*))"
)


def _tokenize(text: str) -> list[tuple[str, Any]]:
    tokens: list[tuple[str, Any]] = []
    position = 0
    while position < len(text):
        if text[position:].strip() == "":
            break
        match = _TOKEN_RE.match(text, position)
        if match is None:
            raise ValueError(f"cannot tokenize {text[position:]!r}")
        position = match.end()
        if match.group("num") is not None:
            tokens.append(("lit", float(match.group("num"))))
        elif match.group("str") is not None:
            tokens.append(("lit", match.group("str")[1:-1].replace("''", "'")))
        elif match.group("op") is not None:
            tokens.append(("op", match.group("op")))
        else:
            word = match.group("ident")
            literals = {"true": True, "false": False, "null": None}
            tokens.append(("lit", literals[word]) if word in literals else ("ident", word))
    return tokens


def _truthy(value: Any) -> bool:
    if value is None or value is False or value == "":
        return False
    return not (isinstance(value, float) and (value == 0 or math.isnan(value)))


def _to_number(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        if value.strip() == "":
            return 0.0
        try:
            return float(value)
        except ValueError:
            return math.nan
    return math.nan


def _compare(op: str, left: Any, right: Any) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        a, b = left.casefold(), right.casefold()
    elif type(left) is type(right) and isinstance(left, (dict, list)):
        if op not in ("==", "!="):
            return False
        return (left is right) == (op == "==")
    elif isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
        return op == "!="
    else:
        a, b = _to_number(left), _to_number(right)
        if math.isnan(a) or math.isnan(b):
            return op == "!="
    return {"==": a == b, "!=": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


def _deref(value: Any, key: Any) -> Any:
    if isinstance(value, dict) and isinstance(key, str):
        for name, item in value.items():
            if name.casefold() == key.casefold():
                return item
        return None
    if isinstance(value, list) and isinstance(key, float) and key.is_integer() and 0 <= key < len(value):
        return value[int(key)]
    return None


def _format(template: str, *args: Any) -> str:
    def text(value: Any) -> str:
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return "" if value is None else str(value)

    out, index = [], 0
    while index < len(template):
        if template.startswith("{{", index):
            out.append("{")
            index += 2
        elif template.startswith("}}", index):
            out.append("}")
            index += 2
        elif template[index] == "{":
            end = template.index("}", index)
            out.append(text(args[int(template[index + 1 : end])]))
            index = end + 1
        else:
            out.append(template[index])
            index += 1
    return "".join(out)


def _from_json(text: Any) -> Any:
    def floats(value: Any) -> Any:
        if isinstance(value, bool) or value is None or isinstance(value, str):
            return value
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, list):
            return [floats(item) for item in value]
        return {key: floats(item) for key, item in value.items()}

    return floats(json.loads(text))


_FUNCTIONS = {"format": _format, "fromjson": _from_json, "always": lambda: True}


class _Parser:
    def __init__(self, tokens: list[tuple[str, Any]], context: dict[str, Any]) -> None:
        self.tokens = tokens
        self.position = 0
        self.context = context

    def peek(self) -> tuple[str, Any] | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self, op: str | None = None) -> tuple[str, Any]:
        token = self.peek()
        if token is None or (op is not None and token != ("op", op)):
            raise ValueError(f"expected {op!r}, found {token!r}")
        self.position += 1
        return token

    def at(self, op: str) -> bool:
        return self.peek() == ("op", op)

    def parse(self) -> Any:
        value = self.or_expr()
        if self.peek() is not None:
            raise ValueError(f"unexpected trailing token {self.peek()!r}")
        return value

    def or_expr(self) -> Any:
        value = self.and_expr()
        while self.at("||"):
            self.take("||")
            right = self.and_expr()
            value = value if _truthy(value) else right
        return value

    def and_expr(self) -> Any:
        value = self.equality()
        while self.at("&&"):
            self.take("&&")
            right = self.equality()
            value = right if _truthy(value) else value
        return value

    def equality(self) -> Any:
        value = self.comparison()
        while self.at("==") or self.at("!="):
            op = self.take()[1]
            value = _compare(op, value, self.comparison())
        return value

    def comparison(self) -> Any:
        value = self.unary()
        while any(self.at(op) for op in ("<", "<=", ">", ">=")):
            op = self.take()[1]
            value = _compare(op, value, self.unary())
        return value

    def unary(self) -> Any:
        if self.at("!"):
            self.take("!")
            return not _truthy(self.unary())
        return self.postfix()

    def postfix(self) -> Any:
        value = self.primary()
        while self.at(".") or self.at("["):
            if self.at("."):
                self.take(".")
                kind, name = self.take()
                if kind != "ident":
                    raise ValueError(f"expected a property name, found {name!r}")
                value = _deref(value, name)
            else:
                self.take("[")
                key = self.or_expr()
                self.take("]")
                value = _deref(value, key)
        return value

    def primary(self) -> Any:
        kind, value = self.take()
        if kind == "lit":
            return value
        if kind == "op" and value == "(":
            inner = self.or_expr()
            self.take(")")
            return inner
        if kind == "ident":
            if self.at("("):
                self.take("(")
                args = []
                while not self.at(")"):
                    args.append(self.or_expr())
                    if not self.at(")"):
                        self.take(",")
                self.take(")")
                return _FUNCTIONS[value.casefold()](*args)
            return _deref(self.context, value)
        raise ValueError(f"unexpected token {value!r}")


def evaluate(expression: Any, context: dict[str, Any]) -> Any:
    """Evaluate a workflow value: a literal, a ``${{ }}`` expression, or a bare ``if:``."""
    if not isinstance(expression, str):
        return expression
    stripped = expression.strip()
    if stripped.count("${{") == 1 and stripped.startswith("${{") and stripped.endswith("}}"):
        return _Parser(_tokenize(stripped[3:-2]), context).parse()

    def text(match: re.Match[str]) -> str:
        value = _Parser(_tokenize(match.group(1)), context).parse()
        return _format("{0}", value)

    return re.sub(r"\$\{\{(.*?)\}\}", text, expression)


def evaluate_if(condition: Any, context: dict[str, Any]) -> bool:
    if condition is None:
        return True
    text = condition if "${{" in str(condition) else f"${{{{ {condition} }}}}"
    return _truthy(evaluate(text, context))


# --- contexts -----------------------------------------------------------------------


def make_context(attempt: str, event: str, variable: str | None, *, runner: str = "github-hosted") -> dict[str, Any]:
    payload: dict[str, Any] = {}
    event_name = event
    if event in ("same-repo-pr", "fork-pr"):
        event_name = "pull_request"
        head = REPOSITORY if event == "same-repo-pr" else "someone-else/myproject"
        payload = {"pull_request": {"number": 7.0, "head": {"repo": {"full_name": head}}}}
    variables = {} if variable is None else {VARIABLE: variable}
    return {
        "github": {
            "run_id": RUN_ID,
            "run_attempt": attempt,
            "event_name": event_name,
            "repository": REPOSITORY,
            "event": payload,
        },
        "vars": variables,
        "runner": {"environment": runner},
    }


EVENTS = ("push", "workflow_dispatch", "same-repo-pr", "fork-pr")
TRUSTED_EVENTS = {"push", "workflow_dispatch", "same-repo-pr"}
POOL = ["self-hosted", "example-pool"]


def variable_cases(os_key: str, attempt: str) -> dict[str, tuple[str | None, bool]]:
    """Variable values, each with whether it authorizes this exact run attempt."""
    exact = f"{RUN_ID}-{attempt}-{os_key}"
    other_os = "macos" if os_key != "macos" else "linux"
    return {
        "unset": (None, False),
        "empty-string": ("", False),
        "empty-object": ("{}", False),
        "exact-entry": (json.dumps({exact: POOL}), True),
        "other-run-id": (json.dumps({f"1{RUN_ID}-{attempt}-{os_key}": POOL}), False),
        "other-attempt": (json.dumps({f"{RUN_ID}-{int(attempt) + 1}-{os_key}": POOL}), False),
        "previous-attempt": (json.dumps({f"{RUN_ID}-{int(attempt) - 1}-{os_key}": POOL}), False),
        "other-os-key": (json.dumps({f"{RUN_ID}-{attempt}-{other_os}": POOL}), False),
        "bare-os-key": (json.dumps({os_key: POOL}), False),
        "exact-among-others": (
            json.dumps({exact: POOL, f"{RUN_ID}-{attempt}-{other_os}": ["self-hosted", "other"]}),
            True,
        ),
    }


def routing_table(os_key: str) -> list[tuple[str, dict[str, Any], bool]]:
    rows = []
    for attempt in ("1", "2", "3"):
        for event in EVENTS:
            for case, (value, authorizes) in variable_cases(os_key, attempt).items():
                routed = attempt != "1" and event in TRUSTED_EVENTS and authorizes
                rows.append(
                    (f"attempt={attempt} event={event} variable={case}", make_context(attempt, event, value), routed)
                )
    return rows


# --- the contract check -------------------------------------------------------------


def load_workflows(root: Path = REPO) -> dict[str, dict[str, Any]]:
    directory = root / ".github" / "workflows"
    return {
        path.name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in sorted(directory.iterdir())
        if path.suffix in {".yml", ".yaml"}
    }


def routing_violations(workflow: str, data: dict[str, Any], hosted_labels: dict[str, str]) -> list[str]:
    """Every way a workflow's jobs break the routing contract, each naming workflow and job."""
    os_key = WORKFLOW_OS_KEYS.get(workflow)
    if os_key is None:
        return [f"{workflow}: no OS key is assigned to this workflow"]
    found: list[str] = []
    for job, spec in data.get("jobs", {}).items():
        hosted = hosted_labels.get(job)
        if hosted is None:
            found.append(f"{workflow}:{job}: job has no recorded hosted label")
            continue
        runs_on = spec.get("runs-on")
        expected = contract_expression(os_key, hosted)
        if runs_on != expected:
            found.append(f"{workflow}:{job}: runs-on is not the contract expression for OS key {os_key!r}: {runs_on!r}")
        for label, context, routed in routing_table(os_key):
            try:
                result = evaluate(runs_on, context)
            except (ValueError, KeyError, json.JSONDecodeError) as error:
                found.append(f"{workflow}:{job}: runs-on does not evaluate ({error}) for {label}")
                break
            want = POOL if routed else hosted
            if result != want:
                found.append(f"{workflow}:{job}: {label} routed to {result!r}, expected {want!r}")
                break
    return found


def test_given_the_workflow_directory_when_read_then_every_workflow_has_its_os_key_and_expected_jobs() -> None:
    workflows = load_workflows()

    assert set(workflows) == set(WORKFLOW_OS_KEYS), f"workflows without an OS key or missing: {sorted(workflows)}"
    assert {name: set(data["jobs"]) for name, data in workflows.items()} == {
        name: set(jobs) for name, jobs in EXPECTED_JOBS.items()
    }


def test_given_every_job_of_every_workflow_when_routed_then_it_follows_the_contract() -> None:
    violations = [
        violation
        for name, data in load_workflows().items()
        for violation in routing_violations(name, data, EXPECTED_JOBS[name])
    ]

    assert not violations, "\n".join(violations)


def test_given_the_routing_table_when_built_then_it_holds_routed_and_hosted_rows_for_every_os_key() -> None:
    """Guards the guard: a table with no routed row could not catch a job that never falls back."""
    for os_key in set(WORKFLOW_OS_KEYS.values()):
        outcomes = [routed for _, _, routed in routing_table(os_key)]
        assert any(outcomes) and not all(outcomes), os_key


@pytest.mark.parametrize("workflow", sorted(WORKFLOW_OS_KEYS))
@pytest.mark.parametrize("event", EVENTS)
@pytest.mark.parametrize("variable", ["unset", "empty-object", "exact-entry", "exact-among-others"])
def test_given_attempt_one_when_any_job_is_routed_then_it_gets_its_hosted_label(
    workflow: str, event: str, variable: str
) -> None:
    os_key = WORKFLOW_OS_KEYS[workflow]
    value, _ = variable_cases(os_key, "1")[variable]
    context = make_context("1", event, value)

    for job, spec in load_workflows()[workflow]["jobs"].items():
        assert evaluate(spec["runs-on"], context) == EXPECTED_JOBS[workflow][job], f"{workflow}:{job}"


@pytest.mark.parametrize("workflow", sorted(WORKFLOW_OS_KEYS))
@pytest.mark.parametrize("attempt", ["1", "2", "3"])
def test_given_a_fork_pull_request_when_the_variable_authorizes_its_attempt_then_every_job_stays_hosted(
    workflow: str, attempt: str
) -> None:
    os_key = WORKFLOW_OS_KEYS[workflow]
    value, _ = variable_cases(os_key, attempt)["exact-entry"]
    context = make_context(attempt, "fork-pr", value)

    for job, spec in load_workflows()[workflow]["jobs"].items():
        assert evaluate(spec["runs-on"], context) == EXPECTED_JOBS[workflow][job], f"{workflow}:{job}"


@pytest.mark.parametrize("workflow", sorted(WORKFLOW_OS_KEYS))
@pytest.mark.parametrize("event", sorted(TRUSTED_EVENTS))
def test_given_a_trusted_second_attempt_with_an_exact_entry_when_routed_then_every_job_gets_the_pool_labels(
    workflow: str, event: str
) -> None:
    os_key = WORKFLOW_OS_KEYS[workflow]
    value, _ = variable_cases(os_key, "2")["exact-entry"]
    context = make_context("2", event, value)

    for job, spec in load_workflows()[workflow]["jobs"].items():
        assert evaluate(spec["runs-on"], context) == POOL, f"{workflow}:{job}"


@pytest.mark.parametrize("workflow", sorted(WORKFLOW_OS_KEYS))
@pytest.mark.parametrize(
    "variable",
    [
        "unset",
        "empty-string",
        "empty-object",
        "other-run-id",
        "other-attempt",
        "previous-attempt",
        "other-os-key",
        "bare-os-key",
    ],
)
def test_given_a_trusted_second_attempt_without_an_exact_entry_when_routed_then_every_job_stays_hosted(
    workflow: str, variable: str
) -> None:
    os_key = WORKFLOW_OS_KEYS[workflow]
    value, _ = variable_cases(os_key, "2")[variable]
    context = make_context("2", "push", value)

    for job, spec in load_workflows()[workflow]["jobs"].items():
        assert evaluate(spec["runs-on"], context) == EXPECTED_JOBS[workflow][job], f"{workflow}:{job}"


def _broken_ci(runs_on: str, job: str = "test") -> dict[str, Any]:
    data = copy.deepcopy(load_workflows()["ci.yml"])
    data["jobs"][job]["runs-on"] = runs_on
    return data


_LINUX = contract_expression("linux", "ubuntu-latest")


@pytest.mark.parametrize(
    ("defect", "runs_on"),
    [
        ("literal label", "ubuntu-latest"),
        ("no attempt test", _LINUX.replace("github.run_attempt > 1 && ", "")),
        (
            "no same-repository test",
            _LINUX.replace(
                "(github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository) && ",
                "",
            ),
        ),
        (
            "lookup without the attempt",
            _LINUX.replace("'{0}-{1}-linux', github.run_id, github.run_attempt", "'{0}-linux', github.run_id"),
        ),
        (
            "lookup by OS key alone",
            _LINUX.replace("[format('{0}-{1}-linux', github.run_id, github.run_attempt)]", "['linux']"),
        ),
        ("another workflow's OS key", contract_expression("macos", "ubuntu-latest")),
        ("another variable", _LINUX.replace(VARIABLE, "OTHER_RUNS_ON")),
    ],
)
def test_given_a_job_that_breaks_the_contract_when_checked_then_the_failure_names_the_workflow_and_job(
    defect: str, runs_on: str
) -> None:
    violations = routing_violations("ci.yml", _broken_ci(runs_on), EXPECTED_JOBS["ci.yml"])

    assert violations, f"{defect} was not caught"
    assert all(violation.startswith("ci.yml:test: ") for violation in violations), violations


@pytest.mark.parametrize(
    ("defect", "runs_on"),
    [
        ("no attempt test", _LINUX.replace("github.run_attempt > 1 && ", "")),
        (
            "no same-repository test",
            _LINUX.replace(
                "(github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository) && ",
                "",
            ),
        ),
        (
            "lookup by OS key alone",
            _LINUX.replace("[format('{0}-{1}-linux', github.run_id, github.run_attempt)]", "['linux']"),
        ),
        ("literal label", "ubuntu-latest"),
    ],
)
def test_given_a_broken_expression_when_evaluated_then_the_routing_table_alone_catches_it(
    defect: str, runs_on: str
) -> None:
    """The behavioural check stands on its own, not only behind the exact-text check."""
    misrouted = [
        label
        for label, context, routed in routing_table("linux")
        if evaluate(runs_on, context) != (POOL if routed else "ubuntu-latest")
    ]

    assert misrouted, f"{defect} routed every context correctly"


# --- per-OS workflows ---------------------------------------------------------------


@pytest.mark.parametrize("workflow", CI_WORKFLOWS)
def test_given_a_ci_workflow_when_read_then_it_triggers_on_the_same_events(workflow: str) -> None:
    data = load_workflows()[workflow]
    triggers = data.get("on", data.get(True))

    assert triggers == CI_TRIGGERS
    assert data["permissions"] == {"contents": "read"}


@pytest.mark.parametrize(
    ("workflow", "summary", "shards", "count"),
    [
        ("ci-windows.yml", "test-windows", "test-windows-shard", 12),
        ("ci-macos.yml", "test-macos", "test-macos-shard", 2),
    ],
)
def test_given_a_summary_job_when_read_then_it_runs_always_and_passes_only_when_every_shard_passed(
    workflow: str, summary: str, shards: str, count: int
) -> None:
    jobs = load_workflows()[workflow]["jobs"]
    matrix = jobs[shards]["strategy"]["matrix"]["shard"]
    final_run = jobs[summary]["steps"][-1]["run"]

    assert matrix == list(range(1, count + 1))
    assert jobs[summary].get("if") == "always()"
    assert jobs[summary]["needs"] == shards
    assert final_run == f'test "${{{{ needs.{shards}.result }}}}" = "success"'


@pytest.mark.parametrize(("workflow", "summary"), [("ci-windows.yml", "test-windows"), ("ci-macos.yml", "test-macos")])
def test_given_a_rerouted_attempt_when_the_summary_job_is_routed_then_it_lands_on_its_shards_pool(
    workflow: str, summary: str
) -> None:
    os_key = WORKFLOW_OS_KEYS[workflow]
    value, _ = variable_cases(os_key, "2")["exact-entry"]
    jobs = load_workflows()[workflow]["jobs"]
    context = make_context("2", "same-repo-pr", value)

    assert evaluate(jobs[summary]["runs-on"], context) == evaluate(jobs[f"{summary}-shard"]["runs-on"], context) == POOL


@pytest.mark.parametrize("workflow", CI_WORKFLOWS)
def test_given_two_pull_request_runs_of_one_pr_when_grouped_then_they_share_a_group_that_cancels(workflow: str) -> None:
    concurrency = load_workflows()[workflow]["concurrency"]
    first, second = make_context("1", "same-repo-pr", None), make_context("1", "same-repo-pr", None)
    second["github"]["run_id"] = "9999"
    other_pr = make_context("1", "same-repo-pr", None)
    other_pr["github"]["event"]["pull_request"]["number"] = 8.0

    group = evaluate(concurrency["group"], first)
    assert group == evaluate(concurrency["group"], second)
    assert group != evaluate(concurrency["group"], other_pr)
    assert evaluate(concurrency["cancel-in-progress"], first) is True


@pytest.mark.parametrize("workflow", CI_WORKFLOWS)
@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_given_two_runs_that_are_not_pull_requests_when_grouped_then_neither_cancels_the_other(
    workflow: str, event: str
) -> None:
    concurrency = load_workflows()[workflow]["concurrency"]
    first, second = make_context("1", event, None), make_context("1", event, None)
    second["github"]["run_id"] = "9999"

    assert evaluate(concurrency["group"], first) != evaluate(concurrency["group"], second)
    assert evaluate(concurrency["cancel-in-progress"], first) is False


def test_given_the_three_ci_workflows_when_grouped_then_each_has_its_own_group_per_pull_request() -> None:
    names = {"ci.yml": "CI", "ci-windows.yml": "CI (Windows)", "ci-macos.yml": "CI (macOS)"}
    groups = set()
    for workflow in CI_WORKFLOWS:
        data = load_workflows()[workflow]
        context = make_context("1", "same-repo-pr", None)
        context["github"]["workflow"] = data["name"]
        assert data["name"] == names[workflow]
        groups.add(evaluate(data["concurrency"]["group"], context))

    assert len(groups) == 3


# --- step variants ------------------------------------------------------------------


def steps_that_run(job: dict[str, Any], runner: str) -> list[dict[str, Any]]:
    """The steps that can run on this kind of runner.

    Only a condition on ``runner.environment`` is decided here. Any other condition,
    such as an MSYS2 retry that runs after a failed attempt, depends on how earlier
    steps went, so the step is kept as one that can run.
    """
    context = make_context("2", "push", None, runner=runner)
    return [
        step
        for step in job.get("steps", [])
        if "runner.environment" not in str(step.get("if", "")) or evaluate_if(step.get("if"), context)
    ]


def jobs_with_os_key(keys: set[str]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (workflow, job, spec)
        for workflow, data in load_workflows().items()
        if WORKFLOW_OS_KEYS[workflow] in keys
        for job, spec in data["jobs"].items()
    ]


def _run_text(steps: list[dict[str, Any]]) -> str:
    return "\n".join(step.get("run", "") for step in steps)


def _uses(steps: list[dict[str, Any]]) -> list[str]:
    return [step["uses"].split("@")[0] for step in steps if "uses" in step]


def test_given_a_self_hosted_runner_when_any_job_runs_then_no_step_needs_privilege_or_a_package_manager() -> None:
    offenders = [
        f"{workflow}:{job}"
        for workflow, job, spec in jobs_with_os_key(SELF_HOSTED_OS_KEYS)
        if re.search(r"\b(sudo|apt-get|apt)\b", _run_text(steps_that_run(spec, "self-hosted")))
    ]

    assert not offenders, offenders


def test_given_the_test_job_when_routed_then_the_nats_image_is_empty_exactly_when_runs_on_is_self_hosted() -> None:
    job = load_workflows()["ci.yml"]["jobs"]["test"]
    rows = routing_table("linux")

    for label, context, routed in rows:
        assert evaluate(job["services"]["nats"]["image"], context) == ("" if routed else "nats:latest"), label
    assert any(routed for _, _, routed in rows)


def test_given_a_self_hosted_linux_job_when_it_runs_then_uv_comes_from_the_installer_script_and_every_sync_is_locked() -> (
    None
):
    for workflow, job, spec in jobs_with_os_key({"linux", "release"}):
        steps = steps_that_run(spec, "self-hosted")
        if not re.search(r"\buv\b", _run_text(steps)):
            continue
        installer = [index for index, step in enumerate(steps) if "astral.sh/uv/install.sh" in step.get("run", "")]
        first_uv = next(index for index, step in enumerate(steps) if re.search(r"(^|\s)uv\s", step.get("run", "")))
        syncs = re.findall(r"\buv\s+sync\b[^\n;&|]*", _run_text(steps))

        assert "astral-sh/setup-uv" not in _uses(steps), f"{workflow}:{job}"
        assert installer and installer[0] < first_uv, f"{workflow}:{job}: uv is used before the installer runs"
        assert all("--locked" in sync.split() for sync in syncs), f"{workflow}:{job}: {syncs}"


def test_given_a_hosted_linux_job_when_it_runs_then_it_keeps_setup_uv_and_never_runs_the_installer_script() -> None:
    for workflow, job, spec in jobs_with_os_key({"linux", "release"}):
        steps = steps_that_run(spec, "github-hosted")
        if not re.search(r"\buv\b", _run_text(steps)):
            continue
        assert "astral-sh/setup-uv" in _uses(steps), f"{workflow}:{job}"
        assert "astral.sh/uv/install.sh" not in _run_text(steps), f"{workflow}:{job}"


def _env_writes(steps: list[dict[str, Any]]) -> dict[str, str]:
    writes = {}
    for step in steps:
        for name, value in re.findall(r'echo "([A-Z_]+)=([^"]*)" >> "\$GITHUB_ENV"', step.get("run", "")):
            writes[name] = value
    return writes


def test_given_a_self_hosted_runner_when_any_job_runs_then_home_and_dolt_root_are_under_the_runner_temp() -> None:
    for workflow, job, spec in jobs_with_os_key(SELF_HOSTED_OS_KEYS):
        steps = steps_that_run(spec, "self-hosted")
        writes = _env_writes(steps)
        first_work = next(
            (index for index, step in enumerate(steps) if "run" in step and "GITHUB_ENV" not in step["run"]), len(steps)
        )
        env_step = next((index for index, step in enumerate(steps) if "GITHUB_ENV" in step.get("run", "")), len(steps))

        assert writes.get("HOME", "").startswith("$RUNNER_TEMP/"), f"{workflow}:{job}: {writes}"
        assert writes.get("DOLT_ROOT_PATH", "").startswith("$RUNNER_TEMP/"), f"{workflow}:{job}: {writes}"
        assert env_step < first_work, f"{workflow}:{job}: a step runs before HOME is moved"


def test_given_a_hosted_runner_when_any_job_runs_then_home_is_left_alone() -> None:
    for workflow, job, spec in jobs_with_os_key(SELF_HOSTED_OS_KEYS):
        assert "HOME" not in _env_writes(steps_that_run(spec, "github-hosted")), f"{workflow}:{job}"


def _pytest_steps() -> list[tuple[str, str]]:
    found = []
    for workflow, job, spec in jobs_with_os_key({"linux", "macos"}):
        for step in spec.get("steps", []):
            if re.search(r"\buv run pytest\b", step.get("run", "")):
                found.append((f"{workflow}:{job}", step["run"]))
    return found


def test_given_the_linux_and_macos_test_jobs_when_read_then_each_has_a_pytest_step() -> None:
    assert {name for name, _ in _pytest_steps()} == {"ci.yml:test", "ci-macos.yml:test-macos-shard"}


def _write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("sh") is None, reason="runs the step under a POSIX shell")
@pytest.mark.parametrize("wrapper", [None, "wrap"])
def test_given_the_runner_wrapper_variable_when_the_test_step_runs_then_pytest_runs_through_it_only_when_set(
    tmp_path: Path, wrapper: str | None
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    _write_stub(bin_dir, "uv", f'echo "uv $*" >> "{log}"')
    _write_stub(bin_dir, "wrap", f'echo "wrap $1 $2 $3" >> "{log}"\nexec "$@"')

    for name, script in _pytest_steps():
        log.unlink(missing_ok=True)
        script = script.replace("${{ matrix.shard }}", "1")
        env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
        if wrapper:
            env["CI_TEST_WRAPPER"] = wrapper
        subprocess.run(["sh", "-c", script], env=env, check=True)
        calls = log.read_text(encoding="utf-8").splitlines()

        expected_first = "wrap uv run pytest" if wrapper else None
        assert calls[-1].startswith("uv run pytest"), (name, calls)
        assert (calls[0] if len(calls) > 1 else None) == expected_first, (name, calls)


def test_given_a_self_hosted_macos_runner_with_tmux_when_the_shard_runs_then_brew_install_tmux_is_skipped() -> None:
    """Checked on the step text, not by running it.

    The suite refuses any shell command string that names tmux, stub or not, so the
    step is not executed here. Every self-hosted line that installs tmux must sit
    behind a `command -v tmux ||` test on that same line.
    """
    job = load_workflows()["ci-macos.yml"]["jobs"]["test-macos-shard"]
    self_hosted = steps_that_run(job, "self-hosted")
    install_lines = [
        line
        for step in self_hosted
        for line in step.get("run", "").splitlines()
        if re.search(r"\bbrew\s+install\s+tmux\b", line)
    ]

    assert install_lines, "no self-hosted step provides tmux"
    assert all(
        re.fullmatch(r"\s*command -v tmux >/dev/null \|\| brew install tmux\s*", line) for line in install_lines
    ), install_lines
    assert any(step.get("run", "").strip() == "brew install tmux" for step in steps_that_run(job, "github-hosted"))


def test_given_the_publish_job_when_routed_then_hosted_uses_the_pypi_action_and_self_hosted_uses_uv_publish() -> None:
    job = load_workflows()["publish.yml"]["jobs"]["publish"]
    hosted, self_hosted = steps_that_run(job, "github-hosted"), steps_that_run(job, "self-hosted")

    assert job["permissions"]["id-token"] == "write"
    assert "pypa/gh-action-pypi-publish" in _uses(hosted)
    assert "uv publish" not in _run_text(hosted)
    assert "pypa/gh-action-pypi-publish" not in _uses(self_hosted)
    assert re.search(r"^uv publish --trusted-publishing always$", _run_text(self_hosted), re.MULTILINE)


# Parity: what a GitHub-hosted runner executes is what it executed before the contract.
HOSTED_PARITY = {
    ("ci.yml", "lint"): {
        "uses": ["actions/checkout", "astral-sh/setup-uv"],
        "run": ["compileall"],
    },
    ("ci.yml", "test"): {
        "uses": ["actions/checkout", "astral-sh/setup-uv", "codecov/codecov-action"],
        "run": ["sudo apt-get install -y tmux", "dolt-linux-amd64.tar.gz", "uv run pytest --cov=ai_cli"],
    },
    ("ci-windows.yml", "test-windows-shard"): {
        "uses": [
            "actions/checkout",
            "astral-sh/setup-uv",
            "msys2/setup-msys2",
            "msys2/setup-msys2",
            "msys2/setup-msys2",
        ],
        "run": [
            "Start-Sleep -Seconds 120",
            "Start-Sleep -Seconds 240",
            "dolt-windows-amd64.zip",
            "--ci-shard=${{ matrix.shard }}/12",
        ],
    },
    ("ci-macos.yml", "test-macos-shard"): {
        "uses": ["actions/checkout", "astral-sh/setup-uv"],
        "run": ["brew install tmux", "dolt-darwin-arm64.tar.gz", "--ci-shard=${{ matrix.shard }}/2"],
    },
    ("publish.yml", "publish"): {
        "uses": ["actions/checkout", "astral-sh/setup-uv", "pypa/gh-action-pypi-publish"],
        "run": ["uv build"],
    },
    ("publish.yml", "release"): {"uses": ["actions/checkout", "softprops/action-gh-release"], "run": ["CHANGELOG.md"]},
    ("wiki.yml", "build"): {
        "uses": ["actions/checkout", "astral-sh/setup-uv", "actions/upload-artifact"],
        "run": ["generate_docs.py --check"],
    },
    ("wiki.yml", "publish"): {"uses": ["actions/download-artifact"], "run": ["git push --quiet origin HEAD"]},
}


@pytest.mark.parametrize(("workflow", "job"), sorted(HOSTED_PARITY))
def test_given_a_hosted_runner_when_the_job_runs_then_it_keeps_its_existing_steps(workflow: str, job: str) -> None:
    spec = load_workflows()[workflow]["jobs"][job]
    steps = steps_that_run(spec, "github-hosted")
    expected = HOSTED_PARITY[(workflow, job)]

    assert _uses(steps) == expected["uses"]
    assert all(fragment in _run_text(steps) for fragment in expected["run"]), expected["run"]
    assert not any(step.get("name", "").endswith("on a self-hosted runner") for step in steps)


def test_given_a_hosted_attempt_when_the_test_job_starts_then_its_nats_service_still_runs() -> None:
    job = load_workflows()["ci.yml"]["jobs"]["test"]

    assert evaluate(job["services"]["nats"]["image"], make_context("1", "same-repo-pr", None)) == "nats:latest"
    assert job["services"]["nats"]["ports"] == ["4222:4222"]
