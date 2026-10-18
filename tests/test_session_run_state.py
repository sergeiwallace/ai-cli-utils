"""Supervisor lifecycle persistence and compatibility."""

import json

import pytest

from ai_cli import session_registry as registry
from ai_cli import session_run_state as state


@pytest.fixture
def registered(tmp_path):
    state.register(
        "c-session-1",
        recipe={
            "engine": "c",
            "session_name": "c-session-1",
            "owning_host": "host-a",
            "argv": ["ai", "c", "1"],
            "extra_args": [],
            "options": {},
            "cwd": str(tmp_path / "myproject"),
            "project": "myproject",
        },
        host="host-a",
    )
    return state.begin("c-session-1")


def record():
    return registry.load_registry()["sessions"][0]


def test_hook_exit_when_restarted_then_end_markers_clear(registered):
    registry.record_exit("c-session-1", cause="manual_exit", evidence={})
    assert state.mark_running("c-session-1", generation=registered, sequence=1)
    assert record()["exit"] is None and record()["ended_at"] is None
    registry.record_exit("c-session-1", cause="manual_exit", evidence={})
    assert state.run_state(record()) == "running"
    assert state.cause(record(), "next-boot") == "host_reboot"


def test_final_exit_when_delayed_start_arrives_then_exit_retained(registered):
    assert state.mark_exited("c-session-1", "manual_exit", {}, generation=registered, sequence=2)
    assert not state.mark_running("c-session-1", generation=registered, sequence=1)
    assert not state.mark_running("c-session-1", generation="old", sequence=3)
    assert state.cause(record(), "next-boot") == "manual_exit"


def test_legacy_records_when_read_then_state_uses_end_marker():
    assert state.run_state({}) == "running"
    assert state.run_state({"ended_at": "time"}) == "exited"


def test_missing_record_when_marked_then_refused_without_creation():
    assert not state.mark_running("c-session-1")
    assert not registry.registry_path().exists()


def test_registry_failure_when_exited_then_safety_file_is_durable(registered, monkeypatch):
    write = registry._write

    def fail_registry(path, doc):
        if path == registry.registry_path():
            raise OSError("disk unavailable")
        write(path, doc)

    monkeypatch.setattr(registry, "_write", fail_registry)
    assert state.mark_exited("c-session-1", "manual_exit", {}, generation=registered, sequence=2)
    path = state.tombstone_path("c-session-1", "host-a")
    assert json.loads(path.read_text())["deny_boot"]
    monkeypatch.setattr(registry, "_write", write)
    assert not state.mark_running("c-session-1", generation=registered, sequence=3)
    generation = state.begin("c-session-1")
    assert state.mark_running("c-session-1", generation=generation, sequence=3)
    assert not path.exists()


def test_both_stores_failed_when_exiting_then_exit_is_refused(registered, monkeypatch):
    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(registry, "_write", fail)
    assert not state.mark_exited("c-session-1", "manual_exit", {}, generation=registered, sequence=2)


def test_unsafe_causes_when_loading_then_configuration_refused(monkeypatch):
    monkeypatch.setattr(state.config, "load_config", lambda: {"session_relaunch": {"causes": ["manual_exit"]}})
    with pytest.raises(ValueError, match="permits only"):
        state.settings()


def test_replaced_record_when_reattached_then_policy_and_generation_survive(registered):
    from ai_cli.iterm2 import _PERSISTENCE_DEFAULTS

    registry._mutate(lambda doc: doc["sessions"][0].update(relaunch="never", relaunched_boot_id="boot-1") or True)
    previous = record()
    registry.record_launch(
        kind="local",
        name="c-session-1",
        relaunch_argv=["ai", "c", "-r", "1"],
        cwd="/work",
        remote=None,
        tty="",
        iterm_session_id=None,
        launcher_pid=1,
        config=_PERSISTENCE_DEFAULTS,
    )
    assert record()["id"] != previous["id"]
    assert record()["generation"] == registered
    assert record()["relaunch"] == "never"
    assert record()["relaunched_boot_id"] == "boot-1"
    assert record()["boot_recipe"] == previous["boot_recipe"]


def test_final_exit_when_registry_recovers_then_safety_state_becomes_publishable(registered, monkeypatch):
    original = registry._write

    def fail_registry(path, doc):
        if path == registry.registry_path():
            raise OSError("disk unavailable")
        original(path, doc)

    monkeypatch.setattr(registry, "_write", fail_registry)
    assert state.mark_exited("c-session-1", "manual_exit", {}, generation=registered, sequence=2)
    revision = registry.load_registry()["dirty_revision"]
    monkeypatch.setattr(registry, "_write", original)
    state.reconcile_safety()
    assert state.cause(record(), "next-boot") == "manual_exit"
    assert registry.load_registry()["dirty_revision"] > revision


def test_transition_when_written_then_file_and_directory_are_synced(registered, monkeypatch):
    import os
    import stat

    sync = registry.os.fsync
    synced = []

    def capture(fd):
        synced.append(stat.S_ISDIR(os.fstat(fd).st_mode))
        sync(fd)

    monkeypatch.setattr(registry.os, "fsync", capture)
    assert state.mark_exited("c-session-1", "manual_exit", {}, generation=registered, sequence=2)
    assert synced == ([False] if os.name == "nt" else [False, True])


def test_reattach_then_new_start_when_recipe_changes_then_only_new_start_replaces_recipe(registered):
    original = record()["boot_recipe"]
    replacement = {"argv": ["ai", "c", "1", "--once"], "cwd": "/work/other"}
    state.register("c-session-1", recipe=replacement, host="host-a")
    assert record()["boot_recipe"] == original
    assert record()["generation"] == registered
    state.register("c-session-1", recipe=replacement, host="host-a", new_start=True)
    assert record()["boot_recipe"] == replacement
    assert record()["generation"] == registered


@pytest.mark.parametrize("generation", [False, True])
def test_given_valid_recipe_when_can_begin_then_registry_bytes_and_generation_unchanged(registered, generation):
    if not generation:
        registry._mutate(lambda doc: doc["sessions"][0].pop("generation") is not None)
    path = registry.registry_path()
    before = {entry: entry.read_bytes() for entry in path.parent.rglob("*") if entry.is_file()}
    state.can_begin("c-session-1")
    assert before == {entry: entry.read_bytes() for entry in path.parent.rglob("*") if entry.is_file()}
    assert ("generation" in record()) == generation


@pytest.mark.parametrize("missing", ["record", "recipe", "invalid", "lock", "permissions", "locked", "corrupt"])
def test_given_unusable_registry_when_can_begin_then_refused_without_writes(registered, monkeypatch, missing):
    import portalocker

    path = registry.registry_path()
    lock = path.with_name(f"{path.name}.lock")
    if missing == "record":
        registry._mutate(lambda doc: doc["sessions"].clear() or True)
    elif missing in ("recipe", "invalid"):
        registry._mutate(
            lambda doc: (
                doc["sessions"][0].update(boot_recipe={} if missing == "recipe" else {"engine": "unknown"}) or True
            )
        )
    elif missing == "lock":
        assert lock.read_bytes() == b""
        lock.unlink()
    elif missing == "permissions":
        if state.os.name == "nt":
            pytest.skip("os.access is not a write test on Windows; the lock open and registry read decide there")
        monkeypatch.setattr(state.os, "access", lambda *args: False)
    elif missing == "locked":

        def unavailable(*args, **kwargs):
            raise portalocker.exceptions.LockException("registry lock unavailable")

        monkeypatch.setattr(state.portalocker, "Lock", unavailable)
    elif missing == "corrupt":
        assert path.read_bytes()
        path.write_text("{")
    before = {entry: entry.read_bytes() for entry in path.parent.rglob("*") if entry.is_file()}
    with pytest.raises((ValueError, OSError, registry.RegistryError, portalocker.exceptions.LockException)):
        state.can_begin("c-session-1")
    assert before == {entry: entry.read_bytes() for entry in path.parent.rglob("*") if entry.is_file()}


def test_given_absent_registry_when_can_begin_then_no_file_or_lock_created():
    path = registry.registry_path()
    assert not path.exists()
    assert not path.with_name(f"{path.name}.lock").exists()
    with pytest.raises((ValueError, OSError)):
        state.can_begin("c-session-1")
    assert not path.exists()
    assert not path.with_name(f"{path.name}.lock").exists()


def test_given_legacy_recipe_when_begin_then_launch_is_preserved_but_restart_probe_refuses(tmp_path):
    state.register("c-session-1", recipe={"argv": ["ai", "c", "1"], "cwd": str(tmp_path)}, host="host-a")
    generation = state.begin("c-session-1")
    assert record()["generation"] == generation
    before = registry.registry_path().read_bytes()
    with pytest.raises(ValueError, match="invalid boot recipe"):
        state.can_begin("c-session-1")
    assert registry.registry_path().read_bytes() == before
