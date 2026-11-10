"""A launch with human names enabled, up to the point where it would create anything.

``_do_session_launch`` runs for real against a bare-remote registry. It is stopped by a
sentinel at the first call after naming, ``_finish_recorded_migrations``, and every
call that would create a worktree or start a session is a tripwire, so a refusal is
proved to happen before anything exists.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from name_fleet import HOST, NAMES, OTHER_HOST, REPO, STEM, build_fleet, git, policy_commit, policy_of, publish_list

from ai_cli import main, output
from ai_cli import name_registry as reg


class _Named(Exception):
    def __init__(self, ai_name: str) -> None:
        super().__init__(ai_name)
        self.ai_name = ai_name


class _Created(AssertionError):
    pass


def _tripwire(*_args, **_kwargs):
    raise _Created("the launch reached a call that creates session state")


def _stop_after_naming(ai_name, reporter, **_kwargs):
    raise _Named(ai_name)


@pytest.fixture
def launch(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_CLI_SESSION_NAMES", "1")

    def run(
        name: str = "",
        *,
        dry_run: bool = False,
        naming: main._NamingRequest | None = None,
        resume: bool = False,
        bare: bool = True,
    ):
        tmux_mode = [
            ("ai_cli.tmux_setup.tmux_runs", {"return_value": True}),
            ("ai_cli.tmux_setup.formats_expand", {"return_value": True}),
            ("ai_cli.tmux_setup.probe", {"return_value": MagicMock(versions_disagree=False)}),
            ("ai_cli.tmux_setup.report_lines", {"return_value": []}),
            (
                "ai_cli.compact_transport.resolve",
                {"return_value": MagicMock(must_refuse=False, platform_limited=False, detail="tmux")},
            ),
            # No tmux session runs on this host.
            ("ai_cli.session.resolve_session", {"return_value": ""}),
        ]
        with ExitStack() as stack:
            for target, kwargs in [
                *([] if bare else tmux_mode),
                ("ai_cli.session.create_worktree", {"side_effect": _tripwire}),
                ("ai_cli.main.os.execvp", {"side_effect": _tripwire}),
                ("ai_cli.main._finish_recorded_migrations", {"side_effect": _stop_after_naming}),
                ("ai_cli.session.cleanup_stale_sessions", {}),
                ("ai_cli.trust.ensure_workspace_trusted", {}),
                ("ai_cli.session.detect_repo_root", {"return_value": tmp_path / "repo"}),
                ("ai_cli.session._resolve_is_remote", {"return_value": False}),
                ("ai_cli.config.validate_registry_completeness", {"return_value": True}),
                ("ai_cli.session.get_project_prefix", {"return_value": REPO}),
                ("ai_cli.session.is_current_project_resolved", {"return_value": True}),
                ("ai_cli.config.get_current_project_name", {"return_value": REPO}),
                ("ai_cli.session_naming.resolve_or_prompt_host_alias", {"return_value": "box"}),
                ("ai_cli.session._tmux_session_names", {"return_value": []}),
            ]:
                stack.enter_context(patch(target, **kwargs))
            main._do_session_launch(
                engine="c",
                name=name,
                resume=resume,
                once=False,
                bare=bare,
                notify=False,
                sandbox=False,
                no_worktree=False,
                remote=False,
                project="",
                is_remote=False,
                project_prefix_override=REPO,
                extra_args=[],
                config={},
                dry_run=dry_run,
                naming=naming,
            )

    return run


def named(run, *args, **kwargs) -> str:
    with pytest.raises(_Named) as caught:
        run(*args, **kwargs)
    return caught.value.ai_name


def test_given_names_enabled_when_a_session_launches_then_it_is_named_and_reserved_before_anything_exists(
    tmp_path, monkeypatch, launch
):
    fleet = build_fleet(tmp_path, monkeypatch)

    ai_name = named(launch)

    token = ai_name.rsplit("-", 1)[-1]
    assert ai_name == f"{STEM}-{token}"
    row = fleet.rows()[token]
    assert row["machine"] == HOST and row["worktree"] == f".worktrees/{ai_name}"


def test_given_a_dry_run_when_launching_then_the_plan_names_the_pick_and_nothing_is_reserved(
    tmp_path, monkeypatch, launch, capsys
):
    fleet = build_fleet(tmp_path, monkeypatch)
    before = fleet.tip()

    launch(dry_run=True)

    output = capsys.readouterr()
    text = output.out + output.err
    assert "3-letter name" in text
    assert "tier len3/" in text
    assert "candidates in the run" in text
    assert "reserves nothing" in text
    assert fleet.tip() == before


def test_given_few_free_names_when_launching_then_one_warning_names_the_remedies_and_the_launch_goes_on(
    tmp_path, monkeypatch, launch, capsys
):
    build_fleet(tmp_path, monkeypatch, names=["ada"])

    named(launch)

    err = capsys.readouterr().err
    assert err.count("ai list-name-candidates") == 1
    assert "max_name_length" in err


def test_given_no_free_name_when_launching_then_it_exits_non_zero_before_anything_exists(
    tmp_path, monkeypatch, launch, capsys
):
    fleet = build_fleet(tmp_path, monkeypatch, names=["ada"])
    reg.reserve_hand_chosen(
        fleet.other_machine(),
        "ada",
        reg.Slot(repo=REPO, repo_alias=REPO, host_alias="two", label=None, machine=OTHER_HOST, client="codex"),
        add=False,
        via="add-name",
        added_by="o@two",
        words=frozenset(),
    )

    with pytest.raises(SystemExit) as caught:
        launch()

    assert caught.value.code == 1
    assert "held: 1" in capsys.readouterr().err


def test_given_an_empty_name_list_when_launching_then_it_exits_non_zero_naming_the_file(
    tmp_path, monkeypatch, launch, capsys
):
    build_fleet(tmp_path, monkeypatch, names=[])

    with pytest.raises(SystemExit) as caught:
        launch()

    assert caught.value.code == 1
    assert "available/names.txt" in capsys.readouterr().err


def test_given_the_registry_unreachable_when_launching_then_a_spare_is_used_and_said(
    tmp_path, monkeypatch, launch, capsys
):
    fleet = build_fleet(tmp_path, monkeypatch)
    named(launch)
    [spare] = [token for token, row in fleet.rows().items() if row["kind"] == "spare"]
    fleet.offline()

    ai_name = named(launch)

    assert ai_name == f"{STEM}-{spare}"
    assert f"offline: using reserved spare {spare}" in capsys.readouterr().err
    assert set(reg.read_spool()) == {spare}


def test_given_the_registry_unreachable_and_no_spare_when_launching_then_it_exits_before_anything_exists(
    tmp_path, monkeypatch, launch, capsys
):
    fleet = build_fleet(tmp_path, monkeypatch)
    named(launch)
    fleet.offline()
    named(launch)  # takes the one spare

    with pytest.raises(SystemExit) as caught:
        launch()

    assert caught.value.code == 75
    assert "ai reconcile-names" in capsys.readouterr().err


@pytest.mark.parametrize(
    "failure",
    [
        reg.RegistryUnreachableError("could not fetch the name registry: timed out after 20 s"),
        reg.RegistryContendedError("lost the race to another writer after 5 attempts"),
    ],
    ids=["unreachable", "contended"],
)
def test_given_the_reserve_fails_with_a_spare_left_when_launching_then_the_warning_and_log_name_the_cause(
    tmp_path, monkeypatch, launch, capsys, failure
):
    fleet = build_fleet(tmp_path, monkeypatch)
    named(launch)
    [spare] = [token for token, row in fleet.rows().items() if row["kind"] == "spare"]
    logged: list[str] = []
    previous_sink = output.attach_file_sink(logged.append)
    try:
        with patch("ai_cli.name_registry.reserve_picked", side_effect=failure):
            ai_name = named(launch)
    finally:
        output.attach_file_sink(previous_sink)

    assert ai_name == f"{STEM}-{spare}"
    err = capsys.readouterr().err
    assert f"offline: using reserved spare {spare}" in err
    assert str(failure) in err
    assert any(f"offline: using reserved spare {spare}" in line and str(failure) in line for line in logged)


def test_given_a_failing_push_and_no_spare_when_launching_then_the_refusal_names_the_push(
    tmp_path, monkeypatch, launch, capsys
):
    fleet = build_fleet(tmp_path, monkeypatch)
    named(launch)
    git(fleet.repo, "config", "remote.origin.pushurl", str(tmp_path / "missing.git"))
    named(launch)  # the fetch still works; the push fails, so this takes the one spare
    capsys.readouterr()

    with pytest.raises(SystemExit) as caught:
        launch()

    err = capsys.readouterr().err
    assert caught.value.code == 75
    assert "could not push" in err
    assert "no unactivated spare" in err


def test_given_a_registry_repo_that_predates_human_names_when_launching_then_index_names_are_kept_and_said(
    tmp_path, monkeypatch, launch, capsys
):
    projects = tmp_path / "projects"
    (projects / "envrepo" / "config").mkdir(parents=True)
    (projects / "envrepo" / "config" / "fleet-projects.toml").write_text("max_length = 6\n")
    monkeypatch.setenv("PROJECTS_DIR", str(projects))
    monkeypatch.setattr("ai_cli.config._get_projects_dir", lambda: projects)

    ai_name = named(launch)

    assert ai_name == f"{STEM}-1"
    assert "predates human session names" in capsys.readouterr().err


def test_given_a_registry_repo_that_predates_human_names_when_a_name_is_asked_for_then_it_exits_before_anything(
    tmp_path, monkeypatch, launch, capsys
):
    projects = tmp_path / "projects"
    (projects / "envrepo" / "config").mkdir(parents=True)
    (projects / "envrepo" / "config" / "fleet-projects.toml").write_text("max_length = 6\n")
    monkeypatch.setenv("PROJECTS_DIR", str(projects))
    monkeypatch.setattr("ai_cli.config._get_projects_dir", lambda: projects)

    with pytest.raises(SystemExit) as caught:
        launch(naming=main._NamingRequest(custom="cleo"))

    assert caught.value.code == 1
    assert "--name cannot be honoured" in capsys.readouterr().err


def test_given_a_label_and_with_name_when_launched_then_the_label_is_followed_by_a_picked_name(
    tmp_path, monkeypatch, launch
):
    fleet = build_fleet(tmp_path, monkeypatch)

    ai_name = named(launch, "probe", naming=main._NamingRequest(with_name=True))

    assert ai_name.startswith(f"{STEM}-probe-")
    assert fleet.rows()[ai_name.rsplit("-", 1)[-1]]["label"] == "probe"


def _fresh_clone_fleet(tmp_path, monkeypatch):
    """A fleet whose list was published from another clone, so the launching clone has never fetched the ref."""
    fleet = build_fleet(tmp_path, monkeypatch, publish=False)
    publish_list(fleet.other_machine("publisher"), NAMES, policy_of(fleet.repo), policy_commit(fleet.repo))
    assert git(fleet.repo, "rev-parse", "--verify", "--quiet", reg.PRIVATE_REF, check=False) == ""
    return fleet


def test_given_a_clone_that_never_fetched_the_registry_when_launching_then_it_fetches_and_reserves_a_pick(
    tmp_path, monkeypatch, launch
):
    fleet = _fresh_clone_fleet(tmp_path, monkeypatch)

    ai_name = named(launch)

    token = ai_name.rsplit("-", 1)[-1]
    assert ai_name == f"{STEM}-{token}"
    assert fleet.rows()[token]["machine"] == HOST


def test_given_a_clone_that_never_fetched_the_registry_when_a_name_is_asked_for_then_it_is_reserved(
    tmp_path, monkeypatch, launch
):
    fleet = _fresh_clone_fleet(tmp_path, monkeypatch)

    ai_name = named(launch, naming=main._NamingRequest(custom="cleo"))

    assert ai_name == f"{STEM}-cleo"
    assert fleet.rows()["cleo"]["machine"] == HOST


def test_given_a_clone_that_never_fetched_the_registry_when_dry_running_then_it_plans_and_reserves_nothing(
    tmp_path, monkeypatch, launch, capsys
):
    fleet = _fresh_clone_fleet(tmp_path, monkeypatch)
    before = fleet.tip()

    launch(dry_run=True)

    output = capsys.readouterr()
    assert "reserves nothing" in output.out + output.err
    assert fleet.tip() == before


@pytest.mark.parametrize(
    ("naming", "dry_run"),
    [(None, False), (main._NamingRequest(custom="cleo"), False), (None, True)],
    ids=["picked", "custom", "dry-run"],
)
def test_given_a_clone_that_never_fetched_the_registry_and_it_unreachable_when_launching_then_it_exits_75_taking_nothing(
    tmp_path, monkeypatch, launch, capsys, naming, dry_run
):
    fleet = _fresh_clone_fleet(tmp_path, monkeypatch)
    before = fleet.tip()
    fleet.offline()

    with pytest.raises(SystemExit) as caught:
        launch(naming=naming, dry_run=dry_run)

    assert caught.value.code == 75
    err = capsys.readouterr().err
    assert "could not fetch" in err
    assert "ai sync-names" not in err
    assert fleet.tip() == before
    assert reg.read_spool() == {}


def test_given_a_terminal_inside_tmux_when_a_tmux_launch_starts_then_it_refuses_before_reserving_anything(
    tmp_path, monkeypatch, launch, capsys
):
    fleet = build_fleet(tmp_path, monkeypatch)
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")

    with pytest.raises(SystemExit) as exited:
        launch("probe", bare=False)

    assert exited.value.code == 1
    assert fleet.rows() == {}
    assert "already inside a tmux session" in capsys.readouterr().err


def test_given_a_terminal_inside_tmux_when_a_bare_launch_starts_then_it_is_named_as_usual(
    tmp_path, monkeypatch, launch
):
    build_fleet(tmp_path, monkeypatch)
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")

    assert named(launch).startswith(f"{STEM}-")


def _held_labelled_title(launch) -> str:
    """Reserve a labelled title on this machine, as a --once launch that then died leaves it."""
    return named(launch, "probe", naming=main._NamingRequest(with_name=True))


def test_given_a_labelled_title_held_here_with_no_session_when_resumed_by_name_then_that_title_relaunches(
    tmp_path, monkeypatch, launch
):
    build_fleet(tmp_path, monkeypatch)
    title = _held_labelled_title(launch)
    token = title.rsplit("-", 1)[-1]
    # The worktree a session that ran and died leaves behind: a resume needs it, since a
    # row alone is also what a reservation made seconds earlier looks like.
    (tmp_path / "repo" / ".worktrees" / title).mkdir(parents=True)

    assert named(launch, token, resume=True, bare=False) == title


def test_given_a_labelled_title_held_here_with_nothing_behind_it_when_resumed_then_it_is_refused(
    tmp_path, monkeypatch, launch, capsys
):
    build_fleet(tmp_path, monkeypatch)
    title = _held_labelled_title(launch)
    capsys.readouterr()

    with pytest.raises(SystemExit) as exited:
        launch(title.rsplit("-", 1)[-1], resume=True, bare=False)

    assert exited.value.code == 1
    assert "Nothing was reserved or created" in capsys.readouterr().err


def test_given_a_labelled_title_held_here_with_no_session_when_named_positionally_then_that_title_relaunches(
    tmp_path, monkeypatch, launch
):
    build_fleet(tmp_path, monkeypatch)
    title = _held_labelled_title(launch)

    assert named(launch, title.rsplit("-", 1)[-1]) == title


def test_given_a_title_held_by_another_machine_when_resumed_by_name_then_it_is_still_refused(
    tmp_path, monkeypatch, launch, capsys
):
    from ai_cli import session_naming

    build_fleet(tmp_path, monkeypatch)
    monkeypatch.setattr(session_naming, "host_id", lambda: OTHER_HOST)
    title = _held_labelled_title(launch)
    monkeypatch.setattr(session_naming, "host_id", lambda: HOST)
    capsys.readouterr()

    with pytest.raises(SystemExit) as exited:
        launch(title.rsplit("-", 1)[-1], resume=True, bare=False)

    assert exited.value.code == 1
    assert "no session named" in capsys.readouterr().err
