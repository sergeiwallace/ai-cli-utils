"""Boot decisions and detached replay use isolated state and a fake tmux."""

import pytest

from ai_cli import session_run_state as state


@pytest.fixture
def boot_record(tmp_path):
    return {
        "id": "record-1",
        "name": "c-myproject-host-1",
        "kind": "local",
        "owning_host": "host-a",
        "boot_id": "previous-boot",
        "run_state": "running",
        "relaunch": "auto",
        "boot_recipe": {
            "session_name": "c-myproject-host-1",
            "owning_host": "host-a",
            "cwd": tmp_path.as_posix(),
            "project": "myproject",
            "engine": "c",
            "options": {"no_direnv": True},
            "extra_args": ["--model", "example-model"],
            "shell": "sh",
            "argv": ["ai", "c", "c-myproject-host-1", "--project-prefix", "myproject"],
        },
    }


def decide(record, **overrides):
    args = {"cfg": state.DEFAULTS | {"on_boot": True}, "host": "host-a", "boot_id": "current-boot", "alive": False}
    args.update(overrides)
    return state.boot_veto(record, **args)


def test_given_disabled_profile_when_boot_checked_then_ac_2_6_switch_is_winning_veto(boot_record):
    boot_record.update(kind="remote", relaunch="never")
    assert decide(boot_record, cfg=state.DEFAULTS) == "session_relaunch.on_boot is off"


def test_given_same_boot_reservation_when_checked_then_ac_2_7_second_attempt_is_refused(boot_record):
    boot_record["relaunched_boot_id"] = "current-boot"
    assert decide(boot_record) == "already attempted in this boot"


def test_given_running_record_when_rebooted_then_ac_2_1_hook_exit_cannot_override_reboot(boot_record):
    boot_record.update(exit={"cause": "manual_exit"}, ended_at="shutdown")
    assert state.cause(boot_record, "current-boot") == "host_reboot"
    assert decide(boot_record) is None


@pytest.mark.parametrize("cause", ["manual_exit", "unknown"])
def test_given_final_exit_when_rebooted_then_ac_2_2_cause_is_logged_veto(boot_record, cause):
    boot_record.update(run_state="exited", supervisor_exit={"cause": cause})
    assert decide(boot_record) == f"cause {cause} forbids boot relaunch"


@pytest.mark.parametrize("policy", ["auto", "never", "always"])
@pytest.mark.parametrize("cause", ["manual_exit", "unknown"])
def test_given_forbidden_cause_and_policy_when_checked_then_ac_2_8_cause_wins(boot_record, policy, cause):
    boot_record.update(run_state="exited", supervisor_exit={"cause": cause}, relaunch=policy)
    # The safety veto remains unconditional even if an unvalidated caller enables it.
    assert decide(boot_record, cfg={"on_boot": True, "causes": [cause]}) == f"cause {cause} forbids boot relaunch"


@pytest.mark.parametrize("policy", ["auto", "never", "always"])
def test_given_policy_when_checked_then_ac_2_3_profile_causes_and_vetoes_apply(boot_record, policy):
    boot_record["relaunch"] = policy
    assert decide(boot_record) == ("policy never forbids boot relaunch" if policy == "never" else None)
    assert decide(boot_record, cfg={"on_boot": True, "causes": []}) is not None
    assert decide(boot_record, alive=True) == "tmux session already exists"


def test_given_live_tmux_when_checked_then_ac_2_4_duplicate_is_refused(boot_record):
    boot_record["relaunched_boot_id"] = "current-boot"
    assert decide(boot_record, alive=True) == "tmux session already exists"
    assert decide(boot_record, alive=None) == "tmux liveness could not be established"


def test_given_remote_and_foreign_records_when_checked_then_ac_2_9_only_owner_replays(boot_record):
    boot_record["boot_recipe"]["options"]["is_remote"] = True
    assert decide(boot_record) is None
    assert decide(boot_record, host="host-b") == "foreign or remote session"
    boot_record["kind"] = "remote"
    assert decide(boot_record) == "foreign or remote session"


def test_given_safety_tombstone_when_checked_then_boot_refuses_before_liveness(boot_record):
    path = state.tombstone_path(boot_record["name"], "host-a")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    assert decide(boot_record, alive=True) == "deny-boot safety file exists"


@pytest.mark.parametrize("missing", ["cwd", "session_name", "options", "engine", "owning_host", "argv"])
def test_given_incomplete_recipe_when_checked_then_ac_2_10_replay_is_refused(boot_record, missing):
    del boot_record["boot_recipe"][missing]
    assert decide(boot_record) == "unsupported or incomplete boot recipe"


def test_given_missing_boot_identity_when_checked_then_unknown_is_refused(boot_record):
    assert decide(boot_record, boot_id=None) == "cause unknown forbids boot relaunch"
    del boot_record["boot_id"]
    assert decide(boot_record) == "cause unknown forbids boot relaunch"
