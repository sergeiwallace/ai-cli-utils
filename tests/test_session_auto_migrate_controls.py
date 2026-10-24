"""Safety controls around automatic migration of old-form session slots."""

from __future__ import annotations

import json
import os

import pytest
import test_session_migrate
import test_session_migrate_hooks
from test_session_auto_migrate import _launch
from test_session_boot_relaunch import boot_record, detached_launch  # noqa: F401
from test_session_migrate import NEW, OLD

from ai_cli import session_commands
from ai_cli.config import read_naming_config
from ai_cli.session_migrate import MIGRATION_LEDGER, queue_migration_context

slot = test_session_migrate.slot
hooks = test_session_migrate_hooks.hooks


def test_given_a_legacy_slot_when_launched_noninteractively_then_it_is_not_migrated(slot):
    code, out, err, launched = _launch(slot)

    assert code == 0, out + err
    assert [item["ai_name"] for item in launched] == [OLD]
    assert slot["wt"].is_dir()
    assert not (slot["repo"] / ".worktrees" / NEW).exists()


@pytest.mark.usefixtures("interactive_terminal")
def test_given_auto_migrate_disabled_when_legacy_slot_is_launched_then_it_prints_the_manual_command(slot):
    code, out, err, launched = _launch(slot, config={"naming": {"auto_migrate": False}})

    assert code == 0, out + err
    assert [item["ai_name"] for item in launched] == [OLD]
    assert "naming.auto_migrate = false" in err
    assert f"ai migrate-session {OLD} {NEW} -r {slot['repo']}" in err


@pytest.mark.usefixtures("interactive_terminal")
def test_given_alias_names_disabled_when_legacy_slot_is_launched_then_it_is_left_in_place(slot):
    code, out, err, launched = _launch(slot, config={"naming": {"alias_convention": False}})

    assert code == 0, out + err
    assert [item["ai_name"] for item in launched] == [OLD]
    assert "naming.alias_convention = false" in err
    assert slot["wt"].is_dir()


@pytest.mark.usefixtures("interactive_terminal")
def test_given_legacy_names_disabled_when_old_slot_exists_then_the_alias_launch_does_not_migrate_it(slot):
    code, out, err, launched = _launch(slot, config={"naming": {"legacy_convention": "off"}})

    assert code == 0, out + err
    assert [item["ai_name"] for item in launched] == [NEW]
    assert 'naming.legacy_convention = "off"' in err
    assert slot["wt"].is_dir()


@pytest.mark.usefixtures("interactive_terminal")
def test_given_a_running_legacy_session_when_launched_then_migration_is_refused_with_the_manual_command(slot):
    sessions = slot["claude"] / "sessions"
    sessions.mkdir(exist_ok=True)
    (sessions / f"{os.getpid()}.json").write_text(
        json.dumps({"pid": os.getpid(), "name": OLD, "cwd": str(slot["wt"])}), encoding="utf-8"
    )

    code, out, err, launched = _launch(slot)

    assert code == 0, out + err
    assert [item["ai_name"] for item in launched] == [OLD]
    assert "still running" in err
    assert f"ai migrate-session {OLD} {NEW} -r {slot['repo']}" in err
    assert slot["wt"].is_dir()


@pytest.mark.usefixtures("interactive_terminal")
def test_given_the_same_alias_index_is_occupied_when_legacy_slot_is_launched_then_the_next_index_is_used(slot):
    occupied = slot["repo"] / ".worktrees" / NEW
    test_session_migrate._git("worktree", "add", "-q", str(occupied), "-b", f"wt-{NEW}", cwd=slot["repo"])

    code, out, err, launched = _launch(slot)

    target = "myrepo-box-4"
    assert code == 0, out + err
    assert [item["ai_name"] for item in launched] == [target]
    assert occupied.is_dir()
    assert (slot["repo"] / ".worktrees" / target).is_dir()
    assert f"auto-migrated {OLD} -> {target} (the same index was occupied)" in err


@pytest.mark.usefixtures("interactive_terminal")
def test_given_the_pair_is_already_recorded_when_legacy_slot_is_launched_then_it_is_not_migrated_again(slot):
    ledger = slot["state"] / MIGRATION_LEDGER
    ledger.write_text(
        json.dumps({"old": OLD, "new": NEW, "repo": str(slot["repo"]), "worktree": str(slot["repo"] / NEW)}) + "\n",
        encoding="utf-8",
    )

    code, out, err, launched = _launch(slot)

    assert code == 0, out + err
    assert [item["ai_name"] for item in launched] == [OLD]
    assert "already recorded" in err
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.usefixtures("interactive_terminal")
def test_given_the_recorded_target_exists_when_legacy_leftover_is_launched_then_the_recorded_target_is_used(slot):
    target = slot["repo"] / ".worktrees" / NEW
    test_session_migrate._git("worktree", "add", "-q", str(target), "-b", f"wt-{NEW}", cwd=slot["repo"])
    ledger = slot["state"] / MIGRATION_LEDGER
    ledger.write_text(
        json.dumps({"old": OLD, "new": NEW, "repo": str(slot["repo"]), "worktree": str(target)}) + "\n",
        encoding="utf-8",
    )

    code, out, err, launched = _launch(slot)

    assert code == 0, out + err
    assert [item["ai_name"] for item in launched] == [NEW]
    assert slot["wt"].is_dir()
    assert target.is_dir()
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 2  # context delivery records its own event


@pytest.mark.usefixtures("interactive_terminal", "hooks")
def test_given_live_migration_step_fails_after_the_move_when_launched_then_the_new_slot_still_launches(slot, request):
    add_hook = request.getfixturevalue("hooks")
    add_hook("10-example", "example", fail="live")

    code, out, err, launched = _launch(slot)

    assert code == 0, out + err
    assert [item["ai_name"] for item in launched] == [NEW]
    assert not slot["wt"].exists()
    assert (slot["repo"] / ".worktrees" / NEW).is_dir()
    assert f"automatic migration {OLD} -> {NEW} was incomplete" in err


def test_given_the_resume_prompt_cannot_be_replaced_when_context_is_queued_then_it_returns_one_warning(slot):
    code, out, err = test_session_migrate._migrate(slot["repo"], "-y")
    assert code == 0, out + err
    prompt = slot["state"] / f"cc-resume-prompt-c-{NEW}"
    prompt.rename(prompt.with_suffix(".kept"))
    prompt.mkdir()

    queued, warnings = queue_migration_context(NEW, f"c-{NEW}", slot["state"])

    assert queued == 0
    assert len(warnings) == 1
    assert f"migration context for {NEW} was not queued" in warnings[0]


@pytest.mark.usefixtures("detached_launch")
def test_given_a_legacy_slot_when_boot_recipe_replays_then_it_is_not_migrated(tmp_path, request):
    record = request.getfixturevalue("boot_record")
    repo = tmp_path / "myrepo"
    legacy_slot = repo / ".worktrees" / OLD
    legacy_slot.mkdir(parents=True)
    record["name"] = f"c-{OLD}"
    recipe = record["boot_recipe"]
    recipe.update(session_name=f"c-{OLD}", cwd=str(legacy_slot), project="myrepo")
    recipe["argv"] = ["ai", "c", "3", "--project-prefix", "myrepo"]

    session_commands._launch_recipe(record)

    assert legacy_slot.is_dir()
    assert not (repo / ".worktrees" / NEW).exists()


@pytest.mark.parametrize("value", ["yes", 1, None])
def test_given_invalid_auto_migrate_when_naming_config_is_read_then_it_is_refused(value):
    with pytest.raises(ValueError, match=r"naming\.auto_migrate"):
        read_naming_config({"naming": {"auto_migrate": value}})
