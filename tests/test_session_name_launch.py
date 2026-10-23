"""A launch with human names enabled, up to the point where it would create anything.

``_do_session_launch`` runs for real against a bare-remote registry. It is stopped by a
sentinel at the first call after naming, ``_finish_recorded_migrations``, and every
call that would create a worktree or start a session is a tripwire, so a refusal is
proved to happen before anything exists.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import patch

import pytest
from name_fleet import HOST, OTHER_HOST, REPO, STEM, build_fleet

from ai_cli import main
from ai_cli import name_registry as reg


class _Named(Exception):
    def __init__(self, ai_name: str) -> None:
        super().__init__(ai_name)
        self.ai_name = ai_name


class _Created(AssertionError):
    pass


def _tripwire(*_args, **_kwargs):
    raise _Created("the launch reached a call that creates session state")


def _stop_after_naming(ai_name, reporter):
    raise _Named(ai_name)


@pytest.fixture
def launch(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_CLI_SESSION_NAMES", "1")

    def run(name: str = "", *, dry_run: bool = False, naming: main._NamingRequest | None = None):
        with ExitStack() as stack:
            for target, kwargs in [
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
                resume=False,
                once=False,
                bare=True,
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
