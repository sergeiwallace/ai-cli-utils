"""``-t/--probe`` launches a throwaway session that nothing treats as a canonical session home.

A probe or test session launched as an ordinary ``ai c`` slot used to be indistinguishable
from a real one: it reserved a first name in the shared registry, its worktree under
``<repo>/.worktrees/`` was recorded in the canonical-worktree registry (which deletion
guards read as "never reap this"), and its run-state row carried a boot recipe that
``ai sessions relaunch --boot`` replays after every restart.

Every test here runs a command line through ``cli()`` against a real name registry (a
bare git remote), a real git repository with an ``origin``, the real canonical-worktree
registry and the real session registry (both redirected per test by conftest), with tmux
and the engine faked at the process boundary. The launch is bare (``-b``), so it ends in
``_exec_with_direnv``, after every write a launch makes.
"""

from __future__ import annotations

import json
import subprocess
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import make_subprocess_result, run_cli
from name_fleet import REPO, build_fleet, git

from ai_cli import iterm2_restore, session_naming
from ai_cli import session_registry as registry
from ai_cli import session_run_state as run_state
from ai_cli.canonical_worktrees import get_canonical_worktree_registry_path

STEM = f"{REPO}-tst"
LAUNCH = ["ai", "c", "--project-prefix", REPO, "-D", "-b"]


class Env:
    def __init__(self, fleet, repo):
        self.fleet = fleet
        self.repo = repo
        self.started: list[tuple[Path, list[str]]] = []

    def canonical_paths(self) -> list[str]:
        path = get_canonical_worktree_registry_path()
        if not path.exists():
            return []
        return [entry["path"] for entry in json.loads(path.read_text(encoding="utf-8"))["worktrees"]]

    def session_rows(self) -> list[dict]:
        return registry.load_registry()["sessions"] if registry.registry_path().exists() else []

    def name_rows(self) -> dict:
        return {token: row for token, row in self.fleet.rows().items() if row["kind"] != "spare"}


@pytest.fixture
def env(tmp_path, monkeypatch, interactive_terminal):
    own_host_id = session_naming.host_id
    fleet = build_fleet(tmp_path, monkeypatch)
    monkeypatch.setattr(session_naming, "host_id", own_host_id)
    monkeypatch.setenv("AI_CLI_SESSION_NAMES", "1")
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    repo = fleet.projects / REPO
    git(repo.parent, "init", "-q", "-b", "main", str(repo))
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("myrepo\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-q", "-m", "initial")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "main")
    git(repo, "fetch", "-q", "origin")
    monkeypatch.chdir(repo)
    state = Env(fleet, repo)
    guarded_run = subprocess.run

    def fake_run(*args, **kwargs):
        command = list(args[0] if args else kwargs["args"])
        if command[:1] == ["tmux"]:
            # No tmux session runs: the launch is bare, and an exited session has none.
            return make_subprocess_result(1, "", "can't find session")
        return guarded_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    def start_engine(target_root, command, **_kwargs):
        # The supervisor's first step, as the real handoff takes it: the engine command is
        # written into the boot recipe. The engine then "runs" until the host goes down, so
        # each row is left exactly as a reboot would find it.
        run_state.begin(f"c-{command[command.index('--name') + 1]}", command=list(command))
        state.started.append((Path(target_root), list(command)))
        raise SystemExit(0)

    with ExitStack() as stack:
        for target, kwargs in (
            ("ai_cli.main._exec_with_direnv", {"side_effect": start_engine}),
            ("ai_cli.main.pull_rebase_autostash", {"return_value": (make_subprocess_result(0), None)}),
            ("ai_cli.main._refresh_claude_plugins", {}),
            ("ai_cli.session.cleanup_stale_sessions", {}),
            ("ai_cli.trust.ensure_workspace_trusted", {}),
            ("ai_cli.main._ensure_dolt_server", {}),
            ("ai_cli.main._ensure_wake_sidecar", {}),
            ("ai_cli.main._launch_install_origin", {}),
            ("ai_cli.main._sync_launch_client", {"return_value": None}),
            ("ai_cli.main._tunnel._ensure_nats_tunnel", {}),
            ("ai_cli.main._process_manager._start_stale_session_reaper_for_launch", {}),
        ):
            stack.enter_context(patch(target, **kwargs))
        yield state


def _boot_relaunchable(rows: list[dict]) -> list[str]:
    """The rows ``ai sessions relaunch --boot`` would replay after the host restarts."""
    cfg = {**run_state.settings(), "on_boot": True}
    return [
        row["name"]
        for row in rows
        if run_state.boot_veto(row, cfg=cfg, host=row.get("owning_host", ""), boot_id="next-boot", alive=False) is None
    ]


# --- AC-1: a probe launch reserves no name, registers no canonical worktree, enrols no boot relaunch ---


def test_given_an_ordinary_launch_when_it_starts_then_it_holds_a_name_a_canonical_worktree_and_a_boot_row(env):
    """The baseline the probe mode departs from, pinned so the contrast below means something."""
    code, _out, err = run_cli([*LAUNCH])

    assert code == 0, err
    assert len(env.started) == 1
    cwd = env.started[0][0]
    assert cwd.parent == env.repo / ".worktrees"
    assert env.name_rows(), "an ordinary launch reserves a first name"
    assert [Path(p) for p in env.canonical_paths()] == [cwd.resolve()]
    assert _boot_relaunchable(env.session_rows()) == [f"c-{cwd.name}"]


def test_given_a_probe_launch_when_it_starts_then_it_reserves_no_name_registers_nothing_and_enrols_no_relaunch(env):
    tip = env.fleet.tip()

    code, _out, err = run_cli([*LAUNCH, "-t"])

    assert code == 0, err
    assert len(env.started) == 1
    cwd, command = env.started[0]
    assert cwd.name == f"{STEM}-probe-1"
    assert command[command.index("--name") + 1] == f"{STEM}-probe-1"
    assert env.fleet.tip() == tip and env.name_rows() == {}
    assert env.canonical_paths() == []
    rows = env.session_rows()
    assert [row["name"] for row in rows] == [f"c-{STEM}-probe-1"]
    assert _boot_relaunchable(rows) == []


def test_given_a_probe_launch_with_a_label_when_it_starts_then_the_label_follows_probe(env):
    code, _out, err = run_cli([*LAUNCH, "--probe", "smoke"])

    assert code == 0, err
    assert env.started[0][0].name == f"{STEM}-probe-smoke-1"
    assert env.name_rows() == {}
    assert f"probe session {STEM}-probe-smoke-1" in err
    assert "to resume an existing session" not in err


# --- AC-2: a probe's leftovers live outside .worktrees/ and are reapable once it exits ---------


def test_given_a_probe_launch_when_it_starts_then_its_worktree_lives_outside_the_repository(env):
    from ai_cli.session import probe_worktree_base

    code, _out, err = run_cli([*LAUNCH, "-t"])

    assert code == 0, err
    cwd = env.started[0][0]
    assert cwd.parent == probe_worktree_base(env.repo)
    assert env.repo not in cwd.parents
    assert not (env.repo / ".worktrees").exists()
    listed = git(env.repo, "worktree", "list", "--porcelain")
    assert f"worktree {cwd.resolve().as_posix()}" in listed.replace("\\", "/")


def test_given_a_probe_that_exited_when_dead_records_are_pruned_then_its_row_is_removed(env):
    code, _out, err = run_cli([*LAUNCH, "-t"])
    assert code == 0, err

    removed = registry.prune()

    assert [record["name"] for record, _reason in removed] == [f"c-{STEM}-probe-1"]
    assert env.session_rows() == []


def test_given_a_bare_probe_still_running_when_dead_records_are_pruned_then_its_row_is_kept(env):
    """A bare session has no tmux session, so "tmux has none" alone would prune a live probe."""
    code, _out, err = run_cli([*LAUNCH, "-t"])
    assert code == 0, err
    assert run_state.mark_running(f"c-{STEM}-probe-1")

    assert registry.prune() == []
    assert [row["name"] for row in env.session_rows()] == [f"c-{STEM}-probe-1"]


def test_given_an_ordinary_session_that_exited_when_dead_records_are_pruned_then_its_boot_row_is_kept(env):
    code, _out, err = run_cli([*LAUNCH])
    assert code == 0, err

    assert registry.prune() == []
    assert len(env.session_rows()) == 1


def test_given_a_probe_row_when_an_iterm2_restore_selects_sessions_then_the_probe_is_skipped(env):
    code, _out, err = run_cli([*LAUNCH, "-t"])
    assert code == 0, err
    settings = {"include_local": True, "include_remote": True, "include": [], "exclude": [], "remote_hosts": []}

    rule = iterm2_restore._skip_rule(env.session_rows()[0], settings, None)

    assert rule == "probe session"


# --- Refusals: a probe never becomes, reaches or names a canonical session ---------------------


@pytest.mark.parametrize(
    "extra",
    [["-r", "ada"], ["-N", "ada"], ["-w"], ["-u"], ["-S"], ["-F"], ["-R"]],
    ids=["resume", "name", "with-name", "no-name", "session-names", "chief", "remote"],
)
def test_given_a_probe_launch_with_a_canonical_naming_flag_when_run_then_it_refuses_creating_nothing(env, extra):
    tip = env.fleet.tip()

    code, _out, err = run_cli([*LAUNCH, "-t", *extra])

    assert code == 1, err
    assert "-t/--probe" in err
    assert env.started == []
    assert env.fleet.tip() == tip
    assert env.canonical_paths() == [] and env.session_rows() == []


def test_given_a_probe_launch_when_dry_run_then_the_plan_names_the_probe_and_writes_nothing(env):
    tip = env.fleet.tip()

    code, out, err = run_cli([*LAUNCH, "-t", "-d"])

    assert code == 0, err
    assert f"{STEM}-probe-1" in out
    assert env.started == [] and env.fleet.tip() == tip
    assert env.canonical_paths() == [] and env.session_rows() == []
