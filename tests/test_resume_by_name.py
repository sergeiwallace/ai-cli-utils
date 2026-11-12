"""``-r/--resume NAME`` reaches an existing session by name, or refuses before creating anything.

Measured on a real launch: ``ai c cos -R -r`` named no existing session, yet the remote
allocator reserved a fresh first name (``...-cos-rae``) and the remote side then took
that seconds-old reservation for a stopped session and began creating its worktree.
Every test here runs a command line through ``cli()`` against a real name registry (a
bare git remote) and a fake tmux at the process boundary, and stops the launch at the
first write it would make: ``create_worktree``.
"""

from __future__ import annotations

import json
import subprocess
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from conftest import make_subprocess_result, run_cli
from name_fleet import REPO, build_fleet, git
from test_cli import tmux_available  # noqa: F401

from ai_cli import name_registry as reg
from ai_cli import session_naming

HOST = "test-host"
STEM = f"{REPO}-tst"
LOCAL = ["ai", "c", "--project-prefix", REPO, "-D"]
REMOTE_SIDE = ["ai", "c", "--is-remote", "--project-prefix", REPO, "--project", REPO, "-D"]
WORKTREE_STOP = 97


class Env:
    def __init__(self, fleet, repo, live):
        self.fleet = fleet
        self.repo = repo
        self.live = live
        self.created: list[str] = []
        self.attached: list[list[str]] = []
        self.allocator_commands: list[str] = []

    def worktree(self, title: str):
        path = self.repo / ".worktrees" / title
        path.mkdir(parents=True)
        return path

    def reserve(self, name: str, title: str) -> None:
        """A row this host holds, as the allocator writes it, with nothing else behind it."""
        row = {
            "token": name,
            "kind": "session",
            "name": name,
            "label": None,
            "title": title,
            "machine": HOST,
            "repo": REPO,
            "repo_alias": REPO,
            "host_alias": "tst",
            "client": "claude-code",
            "source": "picked",
            "worktree": f".worktrees/{title}",
            "reserved_at": "2026-11-10T19:22:38Z",
        }
        self.fleet.registry.transact(
            lambda snapshot: reg.Change({f"names/{name}.json": (json.dumps(row) + "\n").encode()}, f"reserve {name}")
        )


@pytest.fixture
def env(tmp_path, monkeypatch, tmux_available, interactive_terminal):  # noqa: F811
    own_host_id = session_naming.host_id
    fleet = build_fleet(tmp_path, monkeypatch)
    # Keep the suite's host id, the one its alias file names (alias "tst").
    monkeypatch.setattr(session_naming, "host_id", own_host_id)
    monkeypatch.setenv("AI_CLI_SESSION_NAMES", "1")
    repo = fleet.projects / REPO
    repo.mkdir()
    git(repo.parent, "init", "-q", "-b", "main", str(repo))
    monkeypatch.chdir(repo)
    live: list[str] = []
    state = Env(fleet, repo, live)
    guarded_run = subprocess.run

    def fake_run(*args, **kwargs):
        command = list(args[0] if args else kwargs["args"])
        if command[:1] == ["tmux"]:
            if command[1] == "list-sessions":
                return make_subprocess_result(0, "\n".join(live) + "\n")
            if command[1] == "has-session":
                return make_subprocess_result(0 if command[-1].lstrip("=") in live else 1)
            return make_subprocess_result(1)
        return guarded_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    def stop_at_worktree(ai_name, **_kwargs):
        state.created.append(ai_name)
        raise SystemExit(WORKTREE_STOP)

    def handoff(program, argv):
        state.attached.append(argv)
        raise SystemExit(0)

    with ExitStack() as stack:
        for target, kwargs in (
            ("ai_cli.session.create_worktree", {"side_effect": stop_at_worktree}),
            ("ai_cli.main._exec_handoff", {"side_effect": handoff}),
            ("ai_cli.main._resume_suspended_panes", {}),
            ("ai_cli.main._session_launch_state.reattach", {}),
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


def _new_rows(env, before: dict) -> dict:
    return {token: row for token, row in env.fleet.rows().items() if token not in before and row["kind"] != "spare"}


# --- AC-1: an existing session is reached by its exact title -----------------------------


def test_given_a_running_first_name_session_when_resumed_by_title_then_it_attaches_and_reserves_nothing(env):
    env.reserve("ada", f"{STEM}-ada")
    env.worktree(f"{STEM}-ada")
    env.live.append(f"c-{STEM}-ada")
    before = env.fleet.rows()

    code, _out, err = run_cli([*LOCAL, f"{STEM}-ada", "-r"])

    assert code == 0, err
    assert env.attached == [["tmux", "attach-session", "-t", f"c-{STEM}-ada"]]
    assert env.created == []
    assert _new_rows(env, before) == {}


@pytest.mark.parametrize("title", [f"{STEM}-2", f"{STEM}-review-1"], ids=["index", "labelled-index"])
def test_given_a_stopped_legacy_index_session_when_resumed_by_title_then_it_relaunches_that_title(env, title):
    """A session named before the first-name registry holds no row: its worktree is the evidence."""
    env.worktree(title)
    before = env.fleet.rows()

    code, _out, err = run_cli([*LOCAL, title, "-r"])

    assert code == WORKTREE_STOP, err
    assert env.created == [title]
    assert _new_rows(env, before) == {}


def test_given_a_stopped_first_name_session_when_resumed_by_its_name_then_it_relaunches_its_title(env):
    env.reserve("ada", f"{STEM}-review-ada")
    env.worktree(f"{STEM}-review-ada")
    before = env.fleet.rows()

    code, _out, err = run_cli([*LOCAL, "ada", "-r"])

    assert code == WORKTREE_STOP, err
    assert env.created == [f"{STEM}-review-ada"]
    assert _new_rows(env, before) == {}


def test_given_a_stopped_legacy_session_when_the_remote_side_resumes_its_full_name_then_it_relaunches_it(env):
    env.worktree(f"{STEM}-review-1")

    code, _out, err = run_cli([*REMOTE_SIDE, "--resume", f"c-r-{STEM}-review-1"])

    assert code == WORKTREE_STOP, err
    assert env.created == [f"{STEM}-review-1"]


# --- AC-2: a name no session holds is refused before anything is reserved or created -----


def test_given_no_session_named_when_resumed_then_it_refuses_naming_close_titles_and_the_new_session_command(env):
    env.worktree(f"{STEM}-cost-1")
    before = env.fleet.rows()
    tip = env.fleet.tip()

    code, _out, err = run_cli([*LOCAL, "cos", "-r"])

    assert code == 1
    assert env.created == []
    assert env.fleet.tip() == tip and _new_rows(env, before) == {}
    assert f"no session named {STEM}-cos" in err
    assert "nothing was reserved or created" in err.lower()
    assert f"{STEM}-cost-1" in err
    assert "`ai c cos`" in err


def test_given_no_session_named_when_a_resume_is_dry_run_then_it_refuses_rather_than_planning_a_new_one(env):
    code, out, err = run_cli([*LOCAL, "cos", "-r", "--dry-run"])

    assert code == 1
    assert "planned" not in out + err
    assert f"no session named {STEM}-cos" in err


def test_given_a_running_session_when_a_resume_is_dry_run_then_the_plan_is_an_attach(env):
    env.live.append(f"c-{STEM}-3")

    code, out, err = run_cli([*LOCAL, f"{STEM}-3", "-r", "--dry-run"])

    assert code == 0, err
    assert f"attach to tmux session c-{STEM}-3" in out
    assert env.attached == []


def test_given_no_session_named_when_the_remote_allocator_is_asked_to_resume_then_it_refuses_reserving_nothing(env):
    before = env.fleet.rows()
    tip = env.fleet.tip()

    code, out, err = run_cli(
        ["ai", "internal", "allocate-session-name", "c", REPO, "cos", "--resume", "--project", REPO]
    )

    assert code != 0
    assert "session_id" not in out
    assert env.fleet.tip() == tip and _new_rows(env, before) == {}
    assert f"no session named {STEM}-cos" in err


def test_given_a_stopped_legacy_session_when_the_remote_allocator_is_asked_to_resume_then_it_answers_that_title(env):
    env.worktree(f"{STEM}-review-1")
    before = env.fleet.rows()

    code, out, err = run_cli(
        ["ai", "internal", "allocate-session-name", "c", REPO, f"{STEM}-review-1", "--resume", "--project", REPO]
    )

    assert code == 0, err
    reply = json.loads(out.strip().splitlines()[-1])
    assert reply == {"session_id": f"c-r-{STEM}-review-1", "ai_name": f"{STEM}-review-1"}
    assert _new_rows(env, before) == {}


REMOTE_CONFIG = {"remote": {"host": "remote.example", "user": "u", "transport": "ssh"}}


def _remote_launch(env, argv, allocator_reply):
    def fake_ssh(*args, **kwargs):
        command = list(args[0] if args else kwargs["args"])
        env.allocator_commands.append(command[-1])
        return allocator_reply

    with (
        patch("ai_cli.main._resolve_remote_shell", return_value="bash"),
        patch("ai_cli.main._update_remote_ai_cli", return_value=(True, "current")),
        patch("ai_cli.main.subprocess.run", side_effect=fake_ssh),
        patch("ai_cli.main._iterm2._emit_iterm2_profile_setup"),
        patch("ai_cli.main._transport.run_ssh_with_reconnect", side_effect=AssertionError("handed off")) as handoff,
    ):
        code, out, err = run_cli(argv, config=REMOTE_CONFIG)
    return code, out, err, handoff


def test_given_a_remote_resume_when_the_launcher_asks_the_remote_host_then_it_asks_to_resume_not_to_allocate(env):
    refusal = make_subprocess_result(1, "", f"no session named {REPO}-two-cos exists on box-two")

    code, _out, err, handoff = _remote_launch(env, ["ai", "c", "cos", "-R", "-r", "-p", REPO], refusal)

    assert code == 1
    assert not handoff.called
    [allocator] = [command for command in env.allocator_commands if "allocate-session-name" in command]
    assert "--resume" in allocator.split()
    assert "no session named" in err


# --- AC-3: a positional word that is not an existing title says it starts a new session ---


def test_given_a_label_that_is_no_existing_title_when_launched_without_resume_then_it_says_a_new_session_starts(env):
    code, _out, err = run_cli([*LOCAL, "cos"])

    assert code == WORKTREE_STOP, err
    [title] = env.created
    assert title.startswith(f"{STEM}-cos-")
    assert f"new session {title}" in err
    assert "-r" in err.split(f"new session {title}", 1)[1].splitlines()[0]


# --- AC-4: the remote side never treats a fresh reservation as a session to resume -------


def test_given_a_title_reserved_seconds_ago_when_the_remote_side_resumes_it_then_it_refuses(env):
    env.reserve("ada", f"{STEM}-cos-ada")
    before = env.fleet.rows()

    code, _out, err = run_cli([*REMOTE_SIDE, "--resume", f"c-r-{STEM}-cos-ada"])

    assert code == 1
    assert env.created == []
    assert "relaunching it under the title it holds" not in err
    assert f"no session named {STEM}-cos-ada" in err
    assert _new_rows(env, before) == {}


# --- AC-5: the name tools see a session named before the registry ------------------------


def test_given_a_legacy_index_session_with_a_worktree_when_located_then_it_is_reported_with_its_resume_command(env):
    env.worktree(f"{STEM}-review-1")

    code, out, err = run_cli(["ai", "locate-session", f"{STEM}-review-1"])

    assert code == 0, err
    assert "no session holds" not in out + err
    assert str(env.repo / ".worktrees" / f"{STEM}-review-1") in out
    assert f"ai c {STEM}-review-1 -r -p {REPO}" in out


def test_given_a_running_legacy_index_session_when_names_are_listed_then_it_is_listed_as_unregistered(env):
    env.live.append(f"c-r-{STEM}-2")

    code, out, err = run_cli(["ai", "list-names"])

    assert code == 0, err
    line = next(line for line in out.splitlines() if f"{STEM}-2" in line)
    assert "running" in line and "no name" in line


def test_given_nothing_by_that_title_when_located_then_it_still_says_no_session_holds_it(env):
    code, _out, err = run_cli(["ai", "locate-session", f"{STEM}-9"])

    assert code == 1
    assert f"no session holds {STEM}-9" in err


# --- AC-6: an unreadable registry or remote host refuses, never allocates -----------------


def test_given_an_unreadable_registry_when_resumed_then_it_refuses_with_the_read_error(env):
    policy = env.fleet.repo / "config" / "session-names.toml"
    policy.write_text(policy.read_text().replace('registry_ref = "', 'registry_ref_moved = "'))
    env.worktree(f"{STEM}-review-ada")

    code, _out, err = run_cli([*LOCAL, "ada", "-r"])

    assert code == 1
    assert env.created == []
    assert "names no registry_ref" in err


def test_given_an_unreachable_remote_host_when_a_remote_resume_asks_it_then_it_refuses_and_allocates_nothing(env):
    unreachable = make_subprocess_result(255, "", "ssh: connect to host remote.example port 22: Connection refused")

    code, _out, err, handoff = _remote_launch(env, ["ai", "c", "cos", "-R", "-r", "-p", REPO], unreachable)

    assert code == 1
    assert not handoff.called
    assert "Connection refused" in err
