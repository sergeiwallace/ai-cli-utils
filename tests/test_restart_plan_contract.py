"""Guard safety and delivery requirements while the restart feature is a draft."""

import re
from pathlib import Path

import pytest


@pytest.fixture
def restart_plan():
    plans = Path(__file__).resolve().parents[1] / "docs" / "plans"
    matches = list(plans.glob("*restart-idle-sessions-plan.md"))
    assert len(matches) == 1
    return matches[0].read_text(encoding="utf-8")


def acceptance_criterion(plan, number):
    match = re.search(rf"^- \[[ xX]\] AC-{re.escape(number)} (.*?)(?=^- \[|^### |^## |\Z)", plan, re.M | re.S)
    assert match, f"missing AC-{number}"
    return " ".join(match[1].split())


def test_given_restart_plan_when_resume_contract_checked_then_pin_and_existing_session_upgrade_are_required(
    restart_plan,
):
    launch = acceptance_criterion(restart_plan, "2.15")
    assert "exact UUID" in launch
    assert "no title lookup or fresh-conversation fallback" in launch
    assert "generation" in launch and "old-child" in launch
    regression = acceptance_criterion(restart_plan, "2.18")
    assert "newer same-title transcript after the final pre-check" in regression
    assert "both prompt and no-prompt branches" in regression
    upgrade = acceptance_criterion(restart_plan, "5.7")
    assert "pre-feature supervisor" in upgrade
    assert "without replacing the pane or supervisor" in upgrade


def test_given_restart_plan_when_delivery_contract_checked_then_specific_receipts_gate_closure(restart_plan):
    for number, required in (
        ("6.1", "Remote Control"),
        ("6.2", "SendFeedback"),
        ("6.3", "issue URL"),
        ("6.4", "related-issue"),
        ("6.5", "epic closure"),
    ):
        assert required in acceptance_criterion(restart_plan, number)
    assert "Owned and delivered by the launching session" not in restart_plan


@pytest.mark.parametrize("number", ["6.1", "6.2", "6.3", "6.4"])
def test_given_restart_plan_when_reporting_route_checked_then_remote_control_subject_is_required(restart_plan, number):
    contract = acceptance_criterion(restart_plan, number)
    assert "shall" in contract
    assert "Remote Control discovery defect" in contract
    assert "operator confirms" not in contract
    assert "or both" not in contract


def test_given_restart_documents_when_reporting_scope_checked_then_cutoff_cannot_replace_requested_delivery(
    restart_plan,
):
    research = Path(__file__).resolve().parents[1] / "docs/research/claude-code-session-identity-and-reachability.md"
    for document in (restart_plan, research.read_text(encoding="utf-8")):
        assert "Remote Control reporting does not require reconfirmation" in document
        assert "prompt-cutoff work is separate and cannot substitute" in document


def test_given_restart_plan_when_visibility_contract_checked_then_incomplete_evidence_cannot_pass(restart_plan):
    for number in ("2.8", "5.2"):
        contract = acceptance_criterion(restart_plan, number)
        assert "non-zero" in contract and "verification-incomplete" in contract
    rollout = acceptance_criterion(restart_plan, "5.5")
    assert "SendMessage" in rollout and "mobile" in rollout
    assert "D-11 blocks completion" in restart_plan
    assert "nothing waits on this choice" not in restart_plan
