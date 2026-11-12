"""``ai sessions list`` ages out registry rows whose session is proved gone.

Each test writes a temp registry, fakes only the external inputs (tmux, the process
table, the boot time, the remote probe) and drives ``cli()``. A row goes only when its
session is proved gone and it has not changed for ``[session_registry]
stale_row_retention_hours``; a live or unprovable row stays.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from conftest import run_cli

from ai_cli import session_reconcile
from ai_cli import session_registry as registry
from ai_cli.stale_session_reaper import run_stale_session_reaper

HOST = "host-a"
CONFIG = {"machine": {"host_id": HOST}}
BOOT = "boot-now"


def _ago(hours: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row(name: str, *, kind: str = "local", hours: float = 240, **fields) -> dict:
    stamp = _ago(hours)
    row = {
        "id": f"id-{name}",
        "kind": kind,
        "name": name,
        "relaunch_argv": ["ai", "c", name],
        "cwd": "/home/user/projects/myproject",
        "remote": None,
        "iterm2": None,
        "launched_at": stamp,
        "refreshed_at": stamp,
        "launcher_pid": None,
        "ended_at": None,
        "exit": None,
        "boot_id": BOOT,
    }
    if kind == "local":
        row.update(
            owning_host=HOST,
            relaunch="auto",
            boot_recipe={"engine": "c", "session_name": name, "owning_host": HOST, "argv": ["ai", "c", name]},
        )
    row.update(fields)
    return row


def _exited(name: str, *, hours: float = 240, source: str = "liveness_reconciliation", **fields) -> dict:
    stamp = _ago(hours)
    exit_ = {"cause": "manual_exit", "at": stamp, "evidence": {"source": source}}
    return _row(
        name, hours=hours, run_state="exited", changed_at=stamp, ended_at=stamp, exit=exit_, supervisor_exit=exit_
    )


def _write(*rows: dict) -> None:
    path = registry.registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 2, "machine": "box", "sessions": list(rows)}), encoding="utf-8")


def _names() -> list[str]:
    return [r["name"] for r in registry.load_registry()["sessions"]]


def _list(config: dict | None = None) -> list[str]:
    code, out, err = run_cli(["ai", "sessions", "list", "-j"], config=config or CONFIG)
    assert code == 0, out + err
    return [r["name"] for r in json.loads(out)]


@pytest.fixture
def gone(monkeypatch):
    """This boot started a day ago; tmux has no session and no agent process runs."""
    alive: dict[str, bool | None] = {}
    monkeypatch.setattr(registry, "current_boot_id", lambda: BOOT)
    monkeypatch.setattr(session_reconcile, "boot_time", lambda: time.time() - 24 * 3600, raising=False)
    monkeypatch.setattr(registry, "_local_session_alive", lambda name: alive.get(name, False))
    monkeypatch.setattr("psutil.process_iter", lambda *a, **kw: iter(()))
    return alive


@pytest.mark.parametrize("source", ["liveness_reconciliation", "single-run", "on_clean_exit"])
def test_given_exited_row_past_retention_when_listed_then_it_is_removed(gone, source):
    _write(_exited("c-r-session-1", source=source), _exited("c-session-2", hours=1))
    assert _list() == ["c-session-2"]
    assert _names() == ["c-session-2"]


def test_given_row_from_an_earlier_boot_when_this_boot_is_past_retention_then_it_is_removed(gone, monkeypatch):
    monkeypatch.setattr(session_reconcile, "boot_time", lambda: time.time() - 240 * 3600, raising=False)
    legacy = _row("c-session-1", boot_id="boot-before")
    for key in ("owning_host", "relaunch", "boot_recipe"):
        legacy.pop(key)
    _write(legacy)
    assert _list() == []


def test_given_row_from_an_earlier_boot_when_this_boot_is_recent_then_it_is_kept(gone):
    _write(_row("c-session-1", boot_id="boot-before", run_state="running", changed_at=_ago(500)))
    assert _list() == ["c-session-1"]


@pytest.mark.parametrize("tmux", [True, None])
def test_given_live_or_unprovable_tmux_when_listed_then_an_old_row_is_kept(gone, tmux):
    gone["c-session-1"] = tmux
    _write(_exited("c-session-1"))
    assert _list() == ["c-session-1"]


def test_given_live_agent_process_when_listed_then_an_old_row_is_kept(gone, monkeypatch):
    import psutil

    process = psutil.Process()
    process.info = {"pid": -1, "username": process.username(), "status": process.status()}
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **kw: iter([process]))
    monkeypatch.setattr(process, "cmdline", lambda: ["claude", "--name", "session-1"])
    monkeypatch.setattr(process, "environ", dict)
    _write(_exited("c-session-1"))
    assert _list() == ["c-session-1"]


def test_given_deny_boot_safety_file_when_listed_then_the_row_is_kept(gone):
    from ai_cli import session_run_state as state

    _write(_exited("c-session-1"))
    path = state.tombstone_path("c-session-1", HOST)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    assert _list() == ["c-session-1"]


@pytest.mark.parametrize(("hours", "kept"), [(1000, ["c-session-1"]), (0, ["c-session-1"]), (1, [])])
def test_given_retention_in_config_when_listed_then_it_is_read_at_call_time(gone, hours, kept):
    _write(_exited("c-session-1"))
    config = {**CONFIG, "session_registry": {"stale_row_retention_hours": hours}}
    assert _list(config) == kept


def test_given_minute_worker_when_it_ticks_then_old_gone_rows_are_removed(gone, monkeypatch):
    monkeypatch.setattr("ai_cli.config.load_config", lambda **_kw: CONFIG)
    _write(_exited("c-session-1"))

    def stop(_seconds):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_stale_session_reaper({}, reaper_factory=lambda *a, **kw: Mock(), sleep=stop)
    assert _names() == []


def _remote(name: str, **fields) -> dict:
    return _row(name, kind="remote", remote={"alias": "devbox", "session": name}, **fields)


@pytest.fixture
def probes(gone, monkeypatch):
    """The remote host's answer for each session; every call is counted."""
    answers: dict[str, bool | None] = {}
    calls: list[str] = []

    def probe(record, _config):
        calls.append(record["name"])
        return answers.get(record["name"])

    monkeypatch.setattr(registry, "_remote_session_alive", probe)
    return answers, calls


def test_given_client_row_for_a_session_renamed_on_its_host_when_listed_then_it_is_removed(probes):
    answers, calls = probes
    answers["c-r-session-1"] = False
    answers["c-r-session-2"] = True
    _write(_remote("c-r-session-1"), _remote("c-r-session-2"))
    assert _list() == ["c-r-session-2"]
    assert calls == ["c-r-session-1", "c-r-session-2"]


def test_given_remote_probe_inconclusive_when_listed_twice_then_row_is_kept_and_probed_once(probes):
    _answers, calls = probes
    _write(_remote("c-r-session-1"), _remote("c-r-session-2"))
    assert _list() == ["c-r-session-1", "c-r-session-2"]
    assert _list() == ["c-r-session-1", "c-r-session-2"]
    # One unreachable host costs one dial per pass, and the next pass waits out the retention.
    assert calls == ["c-r-session-1"]


def test_given_remote_row_with_a_live_launcher_or_recent_change_when_listed_then_it_is_not_probed(probes):
    _answers, calls = probes
    _write(_remote("c-r-session-1", launcher_pid=os.getpid(), launched_at=_ago(0)), _remote("c-r-session-2", hours=1))
    assert _list() == ["c-r-session-1", "c-r-session-2"]
    assert calls == []


def test_given_remote_row_with_a_clean_exit_past_retention_when_listed_then_it_is_removed_without_a_probe(probes):
    _answers, calls = probes
    _write(_remote("c-r-session-1", ended_at=_ago(240)))
    assert _list() == []
    assert calls == []
