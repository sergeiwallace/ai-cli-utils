"""Real Git transport and durable publication retries."""

import plistlib
import subprocess
import tomllib

import pytest

from ai_cli import session_publish_timer as timer
from ai_cli import session_registry as registry
from ai_cli import session_run_state as state
from ai_cli import session_state_publish as publish


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def publishing(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    remote.mkdir()
    repo.mkdir()
    git(remote, "init", "--bare")
    git(repo, "init")
    git(repo, "config", "user.name", "Example User")
    git(repo, "config", "user.email", "user@example.com")
    git(repo, "remote", "add", "origin", str(remote))
    cfg = {"machine": {"host_id": "host-a"}, "session_relaunch": {"publish": {"repo": str(repo)}}}
    monkeypatch.setattr(state.config, "load_config", lambda: cfg)
    clock = [1000.0]
    monkeypatch.setattr(publish.time, "time", lambda: clock[0])
    state.register("c-session-1", recipe={"argv": ["ai", "c", "1"], "cwd": "/work/myproject"}, host="host-a")
    state.begin("c-session-1")
    assert state.mark_running("c-session-1")
    return repo, remote, cfg, clock


def snapshot(remote):
    return tomllib.loads(git(remote, "show", "refs/heads/fleet-sessions/host-a:sessions/host-a.toml"))


def test_started_session_when_ticks_run_then_state_published_within_two_intervals(publishing):
    repo, remote, _, clock = publishing
    assert publish.publish(tick=True).published
    doc = snapshot(remote)
    assert doc["sessions"]["c-session-1"]["run_state"] == "running"
    assert doc["sessions"]["c-session-1"]["changed_at"]
    previous = git(remote, "rev-parse", "refs/heads/fleet-sessions/host-a")
    clock[0] += 1
    assert state.mark_exited("c-session-1", "manual_exit", {})
    assert not publish.publish(tick=True).published
    clock[0] += 60
    assert publish.publish(tick=True).published
    assert snapshot(remote)["sessions"]["c-session-1"]["cause"] == "manual_exit"
    assert git(remote, "rev-parse", "refs/heads/fleet-sessions/host-a^") == previous
    assert not (repo / ".git/index").exists()
    assert git(repo, "status", "--porcelain") == ""


@pytest.mark.parametrize("failure", ["offline", "refused"])
def test_final_push_failed_when_next_tick_recovers_then_dirty_state_retries(publishing, failure):
    repo, remote, cfg, clock = publishing
    if failure == "offline":
        cfg["session_relaunch"]["publish"]["remote"] = "missing"
    else:
        git(remote, "config", "receive.denyCurrentBranch", "refuse")
        git(remote, "config", "core.bare", "false")
        git(remote, "symbolic-ref", "HEAD", "refs/heads/fleet-sessions/host-a")
    assert not publish.publish(tick=True).published
    doc = registry.load_registry()
    assert doc["dirty_revision"] > doc.get("published_revision", 0)
    assert doc["sessions"][0]["run_state"] == "running"
    cfg["session_relaunch"]["publish"]["remote"] = "origin"
    git(remote, "config", "core.bare", "true")
    clock[0] += 60
    assert publish.publish(tick=True).published
    assert snapshot(remote)["sessions"]["c-session-1"]["run_state"] == "running"


def test_mutation_during_push_when_snapshot_acknowledged_then_later_revision_stays_dirty(publishing, monkeypatch):
    _, remote, _, clock = publishing
    push = publish.Publisher.push

    def mutate_during_push(self, text):
        state.mark_exited("c-session-1", "manual_exit", {})
        push(self, text)

    monkeypatch.setattr(publish.Publisher, "push", mutate_during_push)
    assert publish.publish(tick=True).published
    doc = registry.load_registry()
    assert doc["dirty_revision"] > doc["published_revision"]
    monkeypatch.setattr(publish.Publisher, "push", push)
    clock[0] += 90
    assert publish.publish(tick=True).published
    assert snapshot(remote)["sessions"]["c-session-1"]["run_state"] == "exited"


def test_empty_repo_when_publishing_then_no_git_operations(monkeypatch):
    monkeypatch.setattr(state.config, "load_config", dict)
    monkeypatch.setattr(publish.Publisher, "push", lambda *args: pytest.fail("unexpected git operation"))
    assert not publish.publish(tick=True).published


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_boot_relaunch_off_when_timer_installed_then_trailing_worker_is_enabled(publishing, monkeypatch, platform):
    _, _, _, _ = publishing
    monkeypatch.setattr(timer.sys, "platform", platform)
    monkeypatch.setattr(timer.os, "getuid", lambda: 1000, raising=False)
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    assert timer.manage("install", runner=runner) == "installed"
    files = timer.render(
        state.settings()["publish"],
        platform=platform,
        home=timer.Path.home(),
        path=timer.os.environ["PATH"],
        executable="ai",
    )
    contents = {p: p.read_bytes() for p in files}
    mtimes = {p: p.stat().st_mtime_ns for p in files}
    if platform == "darwin":
        doc = plistlib.loads(next(iter(contents.values())))
        assert doc["RunAtLoad"] and doc["StartInterval"] <= 30
        assert doc["ProgramArguments"][-1] == "--tick"
        assert doc["EnvironmentVariables"]["HOME"] == str(timer.Path.home())
    else:
        assert any(b"OnStartupSec=0" in text and b"OnUnitActiveSec=30s" in text for text in contents.values())
        assert any(b"sessions publish --tick" in text and b"HOME=" in text for text in contents.values())
    assert timer.manage("install", runner=runner) == "installed"
    assert mtimes == {p: p.stat().st_mtime_ns for p in files}
    assert timer.manage("status", runner=runner) == "installed"
    assert calls
    assert timer.manage("uninstall", runner=runner) == "uninstalled"
    count = len(calls)
    assert timer.manage("uninstall", runner=runner) == "absent"
    assert len(calls) == count


def test_publisher_dies_when_restarted_then_unacknowledged_revision_retries(publishing, monkeypatch):
    _, remote, _, clock = publishing
    original = publish.Publisher.push

    def die(*args):
        raise SystemExit(1)

    monkeypatch.setattr(publish.Publisher, "push", die)
    with pytest.raises(SystemExit):
        publish.publish(tick=True)
    assert registry.load_registry().get("published_revision", 0) == 0
    monkeypatch.setattr(publish.Publisher, "push", original)
    clock[0] += 90
    assert publish.publish(tick=True).published
    assert snapshot(remote)["sessions"]["c-session-1"]["run_state"] == "running"


def test_remote_ref_moves_when_pushing_then_retry_is_parented_on_new_tip(publishing, monkeypatch):
    _, remote, _, _ = publishing
    original = publish.Publisher.git
    moved = []

    def race(self, *args, **kwargs):
        if args[0] == "push" and not moved:
            moved.append(True)
            competitor = publish.Publisher(state.settings()["publish"])
            # Distinct snapshot ensures the first push is rejected as non-fast-forward.
            monkeypatch.setattr(publish.Publisher, "git", original)
            competitor.push(publish.render(registry.load_registry(), "host-a") + "\n# concurrent update\n")
            moved.append(git(remote, "rev-parse", "refs/heads/fleet-sessions/host-a"))
            monkeypatch.setattr(publish.Publisher, "git", race)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(publish.Publisher, "git", race)
    assert publish.publish(tick=True).published
    assert git(remote, "rev-parse", "refs/heads/fleet-sessions/host-a^") == moved[1]


def test_publisher_lock_held_when_tick_runs_then_returns_without_waiting(publishing):
    import portalocker

    path = registry.registry_path().with_name("session-publish.lock")
    with portalocker.Lock(path, mode="a", timeout=0):
        assert not publish.publish(tick=True).published
    assert registry.load_registry().get("published_revision", 0) == 0


def test_publication_deadline_missed_when_push_returns_then_snapshot_is_not_acknowledged(publishing, monkeypatch):
    _, remote, _, clock = publishing
    moment = [1000.0]
    monkeypatch.setattr(publish.time, "monotonic", lambda: moment[0])
    original = publish.Publisher.git

    def slow_push(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if args[0] == "push":
            moment[0] += 16
        return result

    monkeypatch.setattr(publish.Publisher, "git", slow_push)
    assert not publish.publish(tick=True).published
    doc = registry.load_registry()
    assert doc["dirty_revision"] > doc.get("published_revision", 0)
    clock[0] += 90
    monkeypatch.setattr(publish.Publisher, "git", original)
    assert publish.publish(tick=True).published
    assert snapshot(remote)["sessions"]["c-session-1"]["run_state"] == "running"
