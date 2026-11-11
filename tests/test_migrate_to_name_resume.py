"""Named migrations use the same preflight and completion checks as direct renames."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import test_migrate_to_name
import test_session_migrate
import test_session_migrate_hooks
from test_migrate_to_name import STEM, migrate
from test_session_migrate import OLD, _everything

from ai_cli import name_registry as reg

fleet = test_migrate_to_name.fleet
slot = test_session_migrate.slot
hooks = test_session_migrate_hooks.hooks


def test_given_a_named_plan_with_a_conflicting_artifact_when_previewed_then_completion_is_not_reported(fleet):
    (fleet.slot["state"] / f"cc-resume-prompt-c-{STEM}-cleo").write_text("unrelated prompt")
    before, tip = _everything(fleet.slot), fleet.tip()
    code, out, err = migrate(fleet, OLD, "-t", "cleo", "-n")
    assert code == 1, out + err
    assert "name registry | would do" in out
    assert _everything(fleet.slot) == before
    assert fleet.tip() == tip


def test_given_a_completed_named_migration_when_original_command_repeats_then_reports_existing_parts(fleet):
    assert migrate(fleet, OLD, "-t", "cleo")[0] == 0
    before, tip = _everything(fleet.slot), fleet.tip()
    for flag in ("-n", "-y", "-y"):
        code, out, err = migrate(fleet, OLD, "-t", "cleo", flag)
        assert code == 0, out + err
        assert "worktree | done earlier" in out
        assert _everything(fleet.slot) == before
        assert fleet.tip() == tip


def test_given_a_recorded_named_pair_without_local_ledger_when_retried_then_registry_proves_recovery(fleet):
    assert migrate(fleet, OLD, "-t", "cleo")[0] == 0
    ledger = fleet.slot["state"] / "session-migrations.jsonl"
    assert OLD in ledger.read_text()
    ledger.unlink()
    code, out, err = migrate(fleet, OLD, "-t", "cleo")
    assert code == 0, out + err
    assert "worktree | done earlier" in out


@pytest.mark.parametrize(
    ("name", "reason"), [("unknown", "not in the name database"), ("ken", "common English word"), ("cleo", "held by")]
)
def test_given_an_invalid_requested_name_when_previewed_then_same_refusal_as_real_run(fleet, name, reason):
    if name == "cleo":
        fleet.registry.transact(
            lambda snapshot: reg.Change(
                {reg.row_path("cleo"): reg.checked_row(reg.spare_row(name="cleo", host_alias="box", machine="other"))},
                "fixture spare",
            )
        )
    before, tip = _everything(fleet.slot), fleet.tip()
    with patch("ai_cli.name_registry.common_words", return_value=frozenset({"ken"})):
        for flag in ("-n", "-y"):
            code, out, err = migrate(fleet, OLD, "-t", name, flag)
            assert code == 2, out + err
            assert reason in out + err
            assert _everything(fleet.slot) == before
            assert fleet.tip() == tip


def test_given_a_named_hook_failure_when_migrating_then_incomplete_until_original_command_finishes(fleet, hooks):
    script = hooks("10-notes", "notes", fail="live")
    code, out, err = migrate(fleet, OLD, "-t", "cleo")
    assert code == 1, out + err
    assert fleet.rows()["cleo"]["state"] == "migration-incomplete"
    content = script.read_text()
    script.write_text(content.replace("'live' ==", "'never' =="))
    code, out, err = migrate(fleet, OLD, "-t", "cleo")
    assert code == 0, out + err
    assert fleet.rows()["cleo"]["state"] == "active"


def test_given_a_named_hook_refusal_when_previewed_then_no_name_is_reserved(fleet, hooks):
    hooks("10-notes", "agent-mail", fail="plan")
    before = fleet.tip()
    code, out, err = migrate(fleet, OLD, "-t", "cleo", "-n")
    assert code == 2, out + err
    assert "agent-mail" in out + err
    assert fleet.tip() == before


def test_given_a_dry_run_when_offline_activations_exist_then_registry_publishing_is_not_called(fleet):
    with patch("ai_cli.name_commands.publish_pending", side_effect=AssertionError("dry run tried to publish")):
        code, out, err = migrate(fleet, OLD, "-t", "cleo", "-n")
    assert code == 0, out + err


def test_given_an_explicit_named_migration_without_yes_when_declined_then_nothing_is_reserved(fleet):
    from click.testing import CliRunner

    from ai_cli import main

    before = fleet.tip()
    with patch("ai_cli.config._get_projects_dir", return_value=fleet.projects):
        result = CliRunner().invoke(
            main._cli_group, ["migrate-session", OLD, "-t", "cleo", "-r", str(fleet.slot["repo"])], input="n\n"
        )
    assert result.exit_code != 0
    assert fleet.tip() == before
    assert fleet.slot["wt"].exists()


def test_given_a_completed_name_with_a_new_failing_hook_when_retried_then_row_stays_incomplete_until_hook_passes(
    fleet, hooks
):
    assert migrate(fleet, OLD, "-t", "cleo")[0] == 0
    script = hooks("10-notes", "notes", fail="live")
    assert migrate(fleet, OLD, "-t", "cleo")[0] == 1
    assert fleet.rows()["cleo"]["state"] == "migration-incomplete"
    script.write_text(script.read_text().replace("'live' ==", "'never' =="))
    code, out, err = migrate(fleet, OLD, "-t", "cleo")
    assert code == 0, out + err
    assert fleet.rows()["cleo"]["state"] == "active"
