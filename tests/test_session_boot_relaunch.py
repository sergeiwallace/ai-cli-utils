"""Boot decisions and detached replay use isolated state and a fake tmux."""

import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from ai_cli import main, session_commands
from ai_cli import session_registry as registry
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


@pytest.fixture
def boot_cli(boot_record, monkeypatch):
    cfg = {"machine": {"host_id": "host-a"}, "session_relaunch": {"on_boot": True, "network_probe": ""}}
    monkeypatch.setattr(state.config, "load_config", lambda: cfg)
    monkeypatch.setattr(session_commands, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(registry, "current_boot_id", lambda: "current-boot")
    registry._mutate(lambda doc: doc["sessions"].append(deepcopy(boot_record)) or True)
    monkeypatch.setattr(registry, "_local_session_alive", lambda name: False)
    launches = []
    real_launch = session_commands._launch_recipe
    monkeypatch.setattr(
        session_commands, "_launch_recipe", lambda record: launches.append(deepcopy(record)), raising=False
    )
    publish = Mock()
    monkeypatch.setattr(session_commands.session_state_publish, "publish", publish)
    return SimpleNamespace(
        cfg=cfg,
        launches=launches,
        real_launch=real_launch,
        publish=publish,
        invoke=lambda *args: CliRunner().invoke(main._cli_group, ["sessions", "relaunch", "--boot", *args]),
    )


def test_given_switch_off_when_cli_runs_then_ac_2_6_nothing_launches_and_off_is_logged(boot_cli):
    boot_cli.cfg["session_relaunch"]["on_boot"] = False
    result = boot_cli.invoke()
    assert result.exit_code == 0, result.output
    assert "on_boot is off" in result.output
    assert not boot_cli.launches


def test_given_reboot_when_cli_runs_twice_then_ac_2_7_reservation_precedes_only_spawn(boot_cli):
    observed = []

    def launch(record):
        observed.append(registry.load_registry()["sessions"][0]["relaunched_boot_id"])

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(session_commands, "_launch_recipe", launch, raising=False)
        assert boot_cli.invoke().exit_code == 0
        result = boot_cli.invoke()
    assert result.exit_code == 0, result.output
    assert observed == ["current-boot"]
    assert "already attempted in this boot" in result.output


@pytest.mark.parametrize("failed", [False, True])
def test_given_attempt_then_reattach_when_retried_then_ac_2_11_reservation_and_policy_survive(
    boot_cli, monkeypatch, failed
):
    from ai_cli import session_launch_state

    calls = []

    def launch(record):
        calls.append(record)
        if failed:
            raise RuntimeError("example launch failure")

    monkeypatch.setattr(session_commands, "_launch_recipe", launch, raising=False)
    first = boot_cli.invoke()
    assert first.exit_code == (1 if failed else 0), first.output
    session_launch_state.reattach("c-myproject-host-1")
    assert boot_cli.invoke().exit_code == 0
    assert len(calls) == 1
    registry._mutate(lambda doc: doc["sessions"][0].update(relaunch="never", relaunched_boot_id="older-boot") or True)
    session_launch_state.reattach("c-myproject-host-1")
    result = boot_cli.invoke()
    assert "policy never" in result.output
    assert len(calls) == 1


def test_given_dry_run_when_cli_runs_then_each_winning_veto_prints_without_writes(boot_cli):
    before = registry.registry_path().read_bytes()
    result = boot_cli.invoke("--dry-run")
    assert result.exit_code == 0, result.output
    assert "c-myproject-host-1" in result.output and "host_reboot" in result.output
    assert registry.registry_path().read_bytes() == before
    assert not boot_cli.launches
    boot_cli.publish.assert_not_called()


@pytest.fixture
def detached_launch(boot_record, monkeypatch):
    monkeypatch.chdir(boot_record["boot_recipe"]["cwd"])
    monkeypatch.setattr(state.config, "load_config", lambda: {"machine": {"host_id": "host-a"}})
    calls = []
    marker = [""]
    existing = [False]

    class LaunchSys:
        platform = "linux"

        def __getattr__(self, name):
            return getattr(sys, name)

    monkeypatch.setattr(main, "sys", LaunchSys())
    monkeypatch.setattr(main, "_is_root", lambda: False)

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        text, code = "", 0
        if argv[:2] == ["tmux", "has-session"]:
            code = 0 if existing[0] else 1
        elif argv[:2] == ["tmux", "new-session"]:
            text = "$1\n"
            existing[0] = True
        elif argv[:2] == ["tmux", "set-option"] and "@ai_cli_session_generation" in argv:
            marker[0] = argv[-1]
        elif argv[:2] == ["tmux", "display-message"]:
            text = f"$1\t{boot_record['name']}\t{marker[0]}"
        return subprocess.CompletedProcess(argv, code, text, "")

    monkeypatch.setattr(main.subprocess, "run", run)
    monkeypatch.setattr(main, "_exec_handoff", Mock(side_effect=AssertionError("must not attach or exec")))
    monkeypatch.setattr(main, "_session_shell_or_exit", lambda: "sh")
    monkeypatch.setattr(main._tmux_setup, "tmux_runs", lambda: True)
    monkeypatch.setattr(main._tmux_setup, "formats_expand", lambda: True)
    monkeypatch.setattr(main._tmux_setup, "probe", lambda **kw: SimpleNamespace(versions_disagree=False))
    monkeypatch.setattr(main._tmux_setup, "report_lines", lambda **kw: [])
    monkeypatch.setattr(
        main._compact_transport,
        "resolve",
        lambda **kw: SimpleNamespace(must_refuse=False, platform_limited=False, detail="tmux"),
    )
    for module, names in (
        (main._session, ["cleanup_stale_sessions", "build_session_name"]),
        (main, ["_finish_recorded_migrations", "_start_session_wake_watcher"]),
        (main._iterm2, ["_emit_iterm2_profile_setup", "_configure_tmux_for_iterm2", "_rename_tmux_window"]),
    ):
        for name in names:
            monkeypatch.setattr(module, name, Mock(side_effect=AssertionError(f"unexpected boot action: {name}")))
    monkeypatch.setattr(main._iterm2, "_load_iterm2_config", dict)
    monkeypatch.setattr(main._iterm2, "_assign_iterm2_color_slot", lambda *a, **kw: None)
    monkeypatch.setattr(main._config, "get_session_map", lambda *a: {})
    monkeypatch.setattr(
        main._session_naming, "resolve_or_prompt_host_alias", Mock(side_effect=AssertionError("prompt"))
    )
    monkeypatch.setattr("ai_cli.trust.ensure_workspace_trusted", lambda *a: None)
    script = Mock(return_value="true\n")
    monkeypatch.setattr(main._session_script, "get_engine_script", script)
    for variable in ("ITERM_SESSION_ID", "TERM_PROGRAM", "TMUX"):
        monkeypatch.delenv(variable, raising=False)
    return calls, script


@pytest.mark.parametrize("mode", ["ordinary", "indexed-reattach", "named-reattach", "remote", "once", "bare"])
def test_given_recipe_without_terminal_when_replayed_then_ac_2_10_exact_detached_name(
    detached_launch, boot_record, mode
):
    calls, script = detached_launch
    recipe = boot_record["boot_recipe"]
    if mode.endswith("reattach"):
        boot_record["relaunch_argv"] = ["ai", "c", "-r", "1" if mode.startswith("indexed") else "review"]
    if mode == "remote":
        recipe["options"]["is_remote"] = True
    if mode in ("once", "bare"):
        recipe["options"][mode] = True
    if mode == "bare":
        recipe["command"] = ["example-agent", "--option", "value with spaces"]
    session_commands._launch_recipe(boot_record)
    created = [argv for argv, kw in calls if argv[:2] == ["tmux", "new-session"]]
    assert len(created) == 1
    assert "-d" in created[0]
    assert created[0][created[0].index("-s") + 1] == boot_record["name"]
    assert not any("attach-session" in argv for argv, kw in calls)
    if mode == "bare":
        assert "example-agent --option 'value with spaces'" in created[0][-1]
        assert str(Path(recipe["cwd"])) in created[0][-1]
    if mode not in ("once", "bare"):
        assert script.call_args.args[1] == "myproject-host-1"
        assert Path(script.call_args.args[7]) == Path(recipe["cwd"])


def test_given_detached_boot_launch_when_attach_used_later_then_ac_2_5_existing_attach_still_works(
    detached_launch, boot_record, monkeypatch
):
    session_commands._launch_recipe(boot_record)
    handoff = Mock(side_effect=SystemExit(0))
    monkeypatch.setattr(main, "_exec_handoff", handoff)
    with pytest.raises(SystemExit):
        main._do_attach(boot_record["name"])
    handoff.assert_called_once_with("tmux", ["tmux", "attach-session", "-t", boot_record["name"]])


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_given_running_shutdown_record_when_boot_cli_replays_then_ac_2_1_detached_tmux_is_created(
    boot_cli, detached_launch, monkeypatch, platform
):
    monkeypatch.setattr(session_commands, "_launch_recipe", boot_cli.real_launch)
    monkeypatch.setattr(state.config, "load_config", lambda: boot_cli.cfg)
    monkeypatch.setattr(session_commands.sys, "platform", platform)
    monkeypatch.setattr(main.sys, "platform", platform)
    result = boot_cli.invoke()
    assert result.exit_code == 0, result.output
    record = registry.load_registry()["sessions"][0]
    assert record["run_state"] == "relaunched"
    assert record["boot_decision"]["cause"] == "host_reboot"
    assert record["boot_decision"]["reason"] == "relaunched detached"
    calls, _ = detached_launch
    created = [argv for argv, kw in calls if argv[:2] == ["tmux", "new-session"]]
    assert len(created) == 1 and "-d" in created[0]
    assert not any("attach-session" in argv for argv, kw in calls)


def test_given_destination_and_initiator_records_when_boot_runs_then_ac_2_9_only_local_owner_spawns(boot_cli):
    def add_remote(doc):
        local = doc["sessions"][0]
        local["boot_recipe"]["options"]["is_remote"] = True
        remote = deepcopy(local)
        remote.update(id="remote-1", kind="remote")
        doc["sessions"].append(remote)
        return True

    registry._mutate(add_remote)
    result = boot_cli.invoke()
    assert result.exit_code == 0, result.output
    assert len(boot_cli.launches) == 1
    assert boot_cli.launches[0]["boot_recipe"]["options"]["is_remote"]
    assert "foreign or remote session" in result.output


@pytest.mark.parametrize("cause", ["manual_exit", "unknown"])
@pytest.mark.parametrize("policy", ["auto", "never", "always"])
def test_given_forbidden_final_exit_when_cli_runs_then_ac_2_8_winning_cause_is_logged(boot_cli, cause, policy):
    registry._mutate(
        lambda doc: (
            doc["sessions"][0].update(run_state="exited", supervisor_exit={"cause": cause}, relaunch=policy) or True
        )
    )
    result = boot_cli.invoke()
    assert result.exit_code == 0, result.output
    assert f"cause {cause} forbids boot relaunch" in result.output
    assert not boot_cli.launches


def test_given_overlapping_boot_invocation_when_reserved_then_ac_2_7_only_one_spawns(boot_cli, monkeypatch):
    nested = []

    def launch(record):
        nested.append(boot_cli.invoke())

    monkeypatch.setattr(session_commands, "_launch_recipe", launch)
    assert boot_cli.invoke().exit_code == 0
    assert len(nested) == 1
    assert nested[0].exit_code == 0
    assert "already attempted in this boot" in nested[0].output


def test_given_reservation_persistence_failure_when_boot_runs_then_no_agent_starts(boot_cli, monkeypatch):
    def fail_write(*args):
        raise OSError("storage unavailable")

    monkeypatch.setattr(registry, "_write", fail_write)
    assert boot_cli.invoke().exit_code != 0
    assert not boot_cli.launches


@pytest.mark.parametrize("unavailable", ["network", "directory"])
def test_given_readiness_timeout_when_boot_runs_then_skip_is_logged_without_reservation(
    boot_cli, monkeypatch, unavailable
):
    boot_cli.cfg["session_relaunch"]["network_wait_seconds"] = 0
    boot_cli.cfg["session_relaunch"]["network_probe"] = "example.com:443"

    def offline(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(session_commands.socket, "create_connection", offline)
    if unavailable == "directory":
        registry._mutate(
            lambda doc: (
                doc["sessions"][0]["boot_recipe"].update(
                    cwd=str(session_commands.Path(doc["sessions"][0]["boot_recipe"]["cwd"]) / "missing")
                )
                or True
            )
        )
    result = boot_cli.invoke()
    assert result.exit_code == 0, result.output
    assert f"{'network probe' if unavailable == 'network' else 'session directory'} is unavailable" in result.output
    assert not boot_cli.launches
    assert "relaunched_boot_id" not in registry.load_registry()["sessions"][0]


def test_given_manual_exit_during_readiness_when_boot_reserves_then_final_exit_wins(boot_cli, monkeypatch):
    def final_exit(recipe, cfg):
        registry._mutate(
            lambda doc: doc["sessions"][0].update(run_state="exited", supervisor_exit={"cause": "manual_exit"}) or True
        )

    monkeypatch.setattr(session_commands, "_wait_for_boot_recipe", final_exit)
    result = boot_cli.invoke()
    assert result.exit_code == 0, result.output
    assert "cause manual_exit" in result.output
    assert not boot_cli.launches


def test_given_publish_target_when_boot_finishes_then_existing_publisher_is_ticked(boot_cli):
    boot_cli.cfg["session_relaunch"]["publish"] = {"repo": "example-repo"}
    assert boot_cli.invoke().exit_code == 0
    boot_cli.publish.assert_called_once_with(tick=True)
