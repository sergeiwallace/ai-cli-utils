"""The subprocess budgets the shared registry fixtures hand every registry.

A test of registry logic runs the real ``git`` and ``dolt`` programs, so the time each
call takes is the host's, not the test's. On a managed macOS host where endpoint security
makes every process spawn cost 0.5 to 6 s, one ``dolt push`` to a bare remote under
``tmp_path`` was measured at 44 s, 60 s and 87 s outside pytest (2026-11-18), and every
fixture that published a name list errored at setup against the shipped 60 s push budget.
These tests pin the fixture budgets above that measurement, and each budget and the host
lock's wait below the per-test bound, so a slow host runs the logic and a hung call or a
held lock still fails inside its test.
"""

from __future__ import annotations

from conftest import DOLT_TEST_TIMEOUT_S
from name_fleet import build_fleet, dolt_policy, registry_for

from ai_cli import name_registry as reg

SLOWEST_MEASURED_DOLT_CALL_S = 87


def _budgets(registry: reg.FleetRegistry) -> dict[str, float]:
    budgets = {
        "timeout_s": registry.timeout_s,
        "fetch": registry.fetch_timeout_s(),
        "push": registry.push_timeout_s(),
        "host lock": reg.lock_wait(registry),
    }
    if hasattr(registry, "clone_timeout_s"):
        budgets["clone"] = registry.clone_timeout_s()
    return budgets


def test_given_a_host_where_one_dolt_push_takes_87_s_when_the_fixtures_build_a_registry_then_every_budget_outlasts_it(
    tmp_path, monkeypatch
):
    fleet = build_fleet(tmp_path, monkeypatch, backend="git", publish=False)
    (fleet.repo / reg.POLICY_MARKER).write_text(dolt_policy((fleet.repo / reg.POLICY_MARKER).read_text()))

    built = {
        "git fixture": _budgets(fleet.registry),
        "dolt fixture": _budgets(registry_for(fleet.repo, backend="dolt")),
        "dolt discovered from the policy": _budgets(reg.discover().registry),
    }

    for source, budgets in built.items():
        for name, seconds in budgets.items():
            assert seconds > SLOWEST_MEASURED_DOLT_CALL_S, (source, name, seconds)
            assert seconds < DOLT_TEST_TIMEOUT_S, (source, name, seconds)
