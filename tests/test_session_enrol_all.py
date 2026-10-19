"""Bulk recovery validates launch evidence before registering and refreshing scripts."""

import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

from ai_cli import config, main, session_enrol
from ai_cli import session_registry as registry
from ai_cli import session_run_state as state

_LOAD_CONFIG = config.load_config


@pytest.fixture
def live_sessions(tmp_path, monkeypatch):
    root = tmp_path / "state"
    root.mkdir()
    cwd = tmp_path / "myproject"
    cwd.mkdir()
    monkeypatch.setattr(config, "get_xdg_state_home", lambda: root)
    monkeypatch.setattr(registry, "registry_path", lambda: root / "registry.json")
    cfg = {"machine": {"host_id": "host-a"}}
    monkeypatch.setattr(config, "load_config", lambda **kwargs: cfg)
    monkeypatch.setattr(main._session_script, "resolve_session_shell", lambda: "bash")
    monkeypatch.delenv("AI_HOST", raising=False)
    rows, leaders, metadata = {}, {}, {}
    commands = []

    def add(name="c-session-1", *, legacy=False, saved=True, engine="c", remote=False):
        meta = {
            "session": name,
            "engine": engine,
            "ai_name": name.removeprefix(f"{engine}-r-" if remote else f"{engine}-"),
            "prefix": f"{engine}-",
            "project_prefix": "myproject",
            "project_name": "myproject",
            "worktree_dir": str(cwd),
            "is_remote": remote,
            "sandbox": False,
            "notify": False,
        }
        script = root / "sessions" / f"{name}.sh"
        script.parent.mkdir(exist_ok=True)
        source = main._engine_script_from_meta(meta)
        if legacy:
            # Old supervisors do not acquire a generation. Their launch metadata
            # and exact stable-script invocation still prove the session's origin.
            source = source.replace(
                "AI_CLI_RUN_GENERATION=$(ai sessions _begin", "legacy_generation=$(ai sessions _begin"
            )
        script.write_text(source, encoding="utf-8")
        if saved:
            (root / f"session-meta-{name}.json").write_text(json.dumps(meta), encoding="utf-8")
        rows[name] = f"{name}\t{100 + len(rows)}\t{cwd}\n"
        leaders[100 + len(leaders)] = ["bash", str(script)]
        metadata[name] = meta
        return script

    def run(argv, **kwargs):
        commands.append(argv)
        if argv[:2] == ["tmux", "list-sessions"]:
            return subprocess.CompletedProcess(argv, 0, "\n".join(rows), "")
        if argv[:2] == ["tmux", "list-panes"]:
            return subprocess.CompletedProcess(argv, 0, rows[argv[3].removeprefix("=")], "")
        pytest.fail(f"unexpected subprocess: {argv[:2]}")

    monkeypatch.setattr(session_enrol.subprocess, "run", run)
    monkeypatch.setattr(session_enrol.psutil, "Process", lambda pid: SimpleNamespace(cmdline=lambda: leaders[pid]))
    monkeypatch.setattr(session_enrol.os, "kill", lambda *a: pytest.fail("enrolment signalled a process"))
    return SimpleNamespace(
        root=root, cwd=cwd, cfg=cfg, rows=rows, leaders=leaders, meta=metadata, add=add, commands=commands
    )


def invoke(*args):
    return CliRunner().invoke(main._cli_group, ["sessions", "enrol", *args])


@pytest.mark.parametrize("engine", ["c", "g", "p", "cx"])
@pytest.mark.parametrize("remote", [False, True])
def test_given_legacy_supervisor_when_bulk_enrolled_then_valid_local_recipe_without_restart(
    live_sessions, engine, remote
):
    w = live_sessions
    name = f"{engine}-{'r-' if remote else ''}session-1"
    script = w.add(name, legacy=True, saved=False, engine=engine, remote=remote)
    source = script.read_bytes()
    result = invoke("--all", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == [{"name": name, "status": "registered", "reason": ""}]
    record = registry.load_registry()["sessions"][0]
    assert state.valid_boot_recipe(record)
    assert record["kind"] == "local" and record["owning_host"] == "host-a"
    assert record["relaunch"] == "never" and "generation" not in record
    assert record["boot_recipe"]["options"]["is_remote"] is remote
    assert script.read_bytes() != source
    assert script.read_text() == main._engine_script_from_meta(w.meta[name])
    assert {cmd[1] for cmd in w.commands} == {"list-sessions", "list-panes"}


def test_given_existing_ownership_and_policy_when_bulk_repeated_then_bytes_and_records_survive(live_sessions):
    w = live_sessions
    w.add()
    assert invoke("-a", "-j").exit_code == 0
    registry._mutate(lambda doc: doc["sessions"][0].update(relaunch="always", generation="generation-1") or True)
    before = registry.registry_path().read_bytes()
    stat = registry.registry_path().stat()
    result = invoke("-a", "-j")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["status"] == "already-registered"
    assert registry.registry_path().read_bytes() == before
    assert registry.registry_path().stat().st_mtime_ns == stat.st_mtime_ns


def test_given_registered_legacy_script_when_bulk_runs_then_script_refreshes_without_generation_change(live_sessions):
    w = live_sessions
    script = w.add()
    assert invoke("-a", "-j").exit_code == 0
    registry._mutate(lambda doc: doc["sessions"][0].update(relaunch="always", generation="generation-1") or True)
    w.add(legacy=True)
    before = registry.registry_path().read_bytes()
    result = invoke("-a", "-j")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["status"] == "already-registered"
    assert script.read_text() == main._engine_script_from_meta(w.meta["c-session-1"])
    assert registry.registry_path().read_bytes() == before


def test_given_unregistered_sessions_when_bulk_dry_run_then_no_registry_lock_or_script_changes(live_sessions):
    w = live_sessions
    script = w.add()
    before = {p: p.read_bytes() for p in w.root.rglob("*") if p.is_file()}
    result = invoke("-a", "-d", "-j")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0] == {
        "name": "c-session-1",
        "status": "registered",
        "reason": "dry-run: would register",
    }
    assert {p: p.read_bytes() for p in w.root.rglob("*") if p.is_file()} == before
    assert script.exists()


@pytest.mark.parametrize("failure", ["name", "saved", "script", "leader", "missing", "options", "cwd", "unrelated"])
def test_given_unvalidated_metadata_when_bulk_runs_then_skips_with_reason_without_writing(live_sessions, failure):
    w = live_sessions
    script = w.add()
    if failure == "name":
        w.rows["c-session-2"] = w.rows.pop("c-session-1").replace("c-session-1", "c-session-2")
    elif failure == "saved":
        (w.root / "session-meta-c-session-1.json").write_text('{"session":"c-other-1"}')
    elif failure == "script":
        script.write_text("unrecognised script")
    elif failure == "leader":
        w.leaders[100] = ["bash", "-c", str(script)]
    elif failure == "missing":
        script.rename(script.with_suffix(".old"))
    elif failure == "options":
        source = script.read_text().replace('"sandbox": false', '"sandbox": "false"')
        script.write_text(source)
    elif failure == "cwd":
        w.rows["c-session-1"] = w.rows["c-session-1"].replace(str(w.cwd), str(w.root))
    else:
        w.rows["unrelated"] = "unrelated\t101\t/tmp\n"
        w.rows.pop("c-session-1")
    result = invoke("-a", "-j")
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report[0]["status"] == "skipped" and report[0]["reason"]
    assert not registry.registry_path().exists()


def test_given_remote_connection_and_legacy_local_record_when_bulk_runs_then_only_missing_recipe_is_added(
    live_sessions,
):
    w = live_sessions
    w.add()
    local = registry._new_record(
        kind="local",
        name="c-session-1",
        relaunch_argv=["ai", "c", "c-session-1"],
        cwd=str(w.cwd),
        remote=None,
        iterm2={"window": 1},
        launcher_pid=123,
    )
    local.update(owning_host="host-a", relaunch="always", generation="generation-1", custom="keep")
    remote = local | {"kind": "remote", "id": "connection-1", "owning_host": "host-b"}
    registry._mutate(lambda doc: doc["sessions"].extend([remote, local]) or True)
    result = invoke("-a", "-j")
    assert result.exit_code == 0, result.output
    after_remote, after_local = registry.load_registry()["sessions"]
    assert after_remote == remote
    assert after_local == local | {"boot_recipe": after_local["boot_recipe"]}
    assert state.valid_boot_recipe(after_local)


def test_given_foreign_local_owner_when_bulk_runs_then_ownership_and_policy_are_untouched(live_sessions):
    w = live_sessions
    w.add()
    registry._mutate(
        lambda doc: (
            doc["sessions"].append(
                {"name": "c-session-1", "kind": "local", "owning_host": "host-b", "relaunch": "always"}
            )
            or True
        )
    )
    before = registry.registry_path().read_bytes()
    result = invoke("-a", "-j")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["status"] == "skipped"
    assert registry.registry_path().read_bytes() == before


def test_given_concurrent_bulk_runs_when_same_session_is_discovered_then_one_registration_is_written(
    live_sessions, monkeypatch
):
    w = live_sessions
    w.add()
    barrier = threading.Barrier(2)
    original = session_enrol._pane_snapshot
    seen = threading.local()

    def inspect(name):
        if not getattr(seen, "waited", False):
            seen.waited = True
            barrier.wait(timeout=10)
        return original(name)

    monkeypatch.setattr(session_enrol, "_pane_snapshot", inspect)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: session_enrol.enrol_live_sessions(), range(2)))
    assert sorted(result[0]["status"] for result in results) == ["already-registered", "registered"]
    assert len(registry.load_registry()["sessions"]) == 1
    assert registry.load_registry()["dirty_revision"] == 1


@pytest.mark.parametrize("entry", ["begin", "refresh", "boot", "heartbeat"])
def test_given_upgrade_entry_when_invoked_then_other_live_session_is_enrolled_without_starting_it(
    live_sessions, entry, monkeypatch
):
    w = live_sessions
    w.add("c-session-1", legacy=True)
    w.add("c-session-2")
    if entry == "begin":
        result = CliRunner().invoke(
            main._cli_group,
            ["sessions", "_begin", "c-session-2", "--launch-metadata", json.dumps(w.meta["c-session-2"])],
        )
    elif entry == "refresh":
        command = click.Command("refresh", callback=lambda: main._handle_internal(["refresh-template", "c-session-2"]))
        result = CliRunner().invoke(command)
    elif entry == "heartbeat":

        async def publish(*args):
            pass

        monkeypatch.setattr("ai_cli.messaging.NATSClient.publish_heartbeat", publish)
        monkeypatch.setattr("ai_cli.session_restart.schedule_auto", lambda *args: None)
        command = click.Command(
            "heartbeat", callback=lambda: main._handle_internal(["publish-heartbeat", "c-session-1", "{}"])
        )
        result = CliRunner().invoke(command)
    else:
        result = CliRunner().invoke(main._cli_group, ["sessions", "relaunch", "--boot"])
    assert result.exit_code == 0, result.output
    record = next(r for r in registry.load_registry()["sessions"] if r["name"] == "c-session-1")
    assert state.valid_boot_recipe(record) and "generation" not in record
    assert record["relaunch"] == "never"
    script = w.root / "sessions/c-session-1.sh"
    assert script.read_text() == main._engine_script_from_meta(w.meta["c-session-1"])
    assert all(cmd[1] in ("list-sessions", "list-panes") for cmd in w.commands)


def test_given_changed_host_configuration_when_bulk_runs_again_then_new_owner_uses_current_config(live_sessions):
    w = live_sessions
    w.add()
    assert invoke("-a", "-j").exit_code == 0
    w.cfg["machine"]["host_id"] = "host-b"
    w.add("c-session-2")
    assert invoke("-a", "-j").exit_code == 0
    owners = {r["name"]: r["owning_host"] for r in registry.load_registry()["sessions"]}
    assert owners == {"c-session-1": "host-a", "c-session-2": "host-b"}


@pytest.mark.parametrize("message", ["permission denied", "error connecting to socket (Permission denied)"])
def test_given_tmux_discovery_failure_when_bulk_runs_then_failure_is_reported(live_sessions, monkeypatch, message):
    monkeypatch.setattr(
        session_enrol.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", message)
    )
    result = invoke("-a", "-j")
    assert result.exit_code != 0 and "discovery failed" in result.output
    assert not registry.registry_path().exists()


def test_given_replaced_pane_when_enrolment_rechecks_then_session_is_skipped(live_sessions, monkeypatch):
    w = live_sessions
    w.add()
    original = session_enrol._pane_snapshot
    calls = []

    def snapshot(name):
        cwd, argv, pid = original(name)
        calls.append(name)
        return cwd, argv, pid + (len(calls) - 1)

    monkeypatch.setattr(session_enrol, "_pane_snapshot", snapshot)
    result = invoke("-a", "-j")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["status"] == "skipped"
    assert not registry.registry_path().exists()


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_given_supported_platform_when_bulk_runs_then_portable_enrolment_registers(
    live_sessions, monkeypatch, platform
):
    live_sessions.add()
    monkeypatch.setattr(main.sys, "platform", platform)
    result = invoke("-a", "-j")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["status"] == "registered"


def test_given_boot_dry_run_when_live_sessions_exist_then_recovery_changes_nothing(live_sessions):
    live_sessions.add()
    before = {p: p.read_bytes() for p in live_sessions.root.rglob("*") if p.is_file()}
    result = CliRunner().invoke(main._cli_group, ["sessions", "relaunch", "--boot", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert {p: p.read_bytes() for p in live_sessions.root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("existing_config", [False, True])
def test_given_uninitialised_config_when_bulk_dry_run_then_config_is_not_created_or_rewritten(
    live_sessions, monkeypatch, existing_config
):
    w = live_sessions
    w.add()
    directory = w.root / "config"
    directory.mkdir()
    path = directory / "config.toml"
    if existing_config:
        path.write_text('[machine]\nhost_id = "host-a"\n')
    monkeypatch.setattr(config, "load_config", _LOAD_CONFIG)
    monkeypatch.setattr(config, "get_xdg_config_home", lambda: directory)
    monkeypatch.setattr(config, "config_file_text", lambda: str(path))
    before = {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    result = invoke("-a", "-d", "-j")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["status"] == "registered"
    assert {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()} == before


def test_given_uninitialised_host_profile_when_enrolled_then_owner_matches_future_launcher(live_sessions, monkeypatch):
    w = live_sessions
    w.add()
    path = w.root / "config.toml"
    monkeypatch.setattr(config, "load_config", _LOAD_CONFIG)
    monkeypatch.setattr(config, "config_file_text", lambda: str(path))
    monkeypatch.setattr(registry.socket, "gethostname", lambda: "host-b")
    monkeypatch.setenv("AI_HOST", "host-a")
    result = invoke("-a", "-j")
    assert result.exit_code == 0, result.output
    record = registry.load_registry()["sessions"][0]
    assert record["owning_host"] == record["boot_recipe"]["owning_host"] == "host-a"
    assert not path.exists()
