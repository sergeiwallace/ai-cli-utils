"""User-configured ``[session_names] reserved`` list honored across all launch pickers."""

from __future__ import annotations

import random

import pytest
from click.testing import CliRunner
from name_fleet import HOST, HOST_ALIAS, REPO, STEM, build_fleet, quiet_reporter

from ai_cli import config as _config
from ai_cli import main, session_naming, session_title
from ai_cli import name_registry as reg
from ai_cli.main import _cli_group

ENGINES = ("c", "cx", "p", "g")


def _write_reserved(entries: list[str] | str) -> None:
    path = _config.get_xdg_config_home() / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(entries, str):
        body = f"[session_names]\nreserved = {entries}\n"
    else:
        rendered = ", ".join(f'"{item}"' for item in entries)
        body = f"[session_names]\nreserved = [{rendered}]\n"
    path.write_text(body, encoding="utf-8")


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_CLI_SESSION_NAMES", "1")
    monkeypatch.setattr("ai_cli.session._tmux_session_names", list)
    result = build_fleet(tmp_path, monkeypatch)
    owner = result.projects / REPO
    owner.mkdir()
    (owner / "pyproject.toml").write_text(f'[tool.ai-cli]\ntask_prefix = "{REPO}"\n', encoding="utf-8")
    return result


def _resolve_launch(engine: str, *, naming: main._NamingRequest | None = None, dry_run: bool = False) -> str:
    reporter, _stream = quiet_reporter()
    _session_id, title = main._resolve_session_identity(
        engine,
        STEM,
        "",
        {},
        is_remote=False,
        use_tmux=True,
        legacy_prefix=REPO,
        naming=naming or main._NamingRequest(),
        repo=REPO,
        repo_alias=REPO,
        host_alias=HOST_ALIAS,
        worktree=True,
        dry_run=dry_run,
        reporter=reporter,
    )
    return title


def test_given_user_reserved_names_in_pick_request_when_candidates_filter_then_reserved_names_are_excluded_with_reason():
    request = session_naming.PickRequest(
        generated=frozenset({"ada", "bob"}),
        held=frozenset(),
        held_titles=frozenset(),
        stem=STEM,
        title_prefix=f"{STEM}-",
        repo_alias=REPO,
        min_length=3,
        max_length=4,
        letters=session_naming.alias_letters(REPO),
        pick_floor=10,
        reserved_names=frozenset({"ada"}),
    )

    candidates, removed = session_naming.name_candidates(request)
    assert candidates == ["bob"]
    assert removed["reserved in user config"] == 1

    exhausted = session_naming.PickRequest(
        generated=frozenset({"ada"}),
        held=frozenset(),
        held_titles=frozenset(),
        stem=STEM,
        title_prefix=f"{STEM}-",
        repo_alias=REPO,
        min_length=3,
        max_length=4,
        letters=session_naming.alias_letters(REPO),
        pick_floor=10,
        reserved_names=frozenset({"ada"}),
    )
    with pytest.raises(session_naming.NamePoolExhaustedError, match="reserved in user config: 1"):
        session_naming.pick_name(exhausted, random.Random(0))


def test_given_mixed_case_and_whitespace_reserved_config_when_resolved_then_normalized_at_call_time(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert session_title.resolve_reserved_names() == frozenset()

    _write_reserved([" ADA ", "Max", "", "  "])
    assert session_title.resolve_reserved_names() == frozenset({"ada", "max"})

    # Re-read fresh on the next call without restarting the process.
    _write_reserved(["CLEO"])
    assert session_title.resolve_reserved_names() == frozenset({"cleo"})


@pytest.mark.parametrize("raw_toml", ['"ada"', "42", '["ada", 7]'])
def test_given_invalid_reserved_config_when_launching_then_it_exits_two_before_reserving(fleet, raw_toml):
    _write_reserved(raw_toml)
    before = fleet.tip()
    reporter, stream = quiet_reporter()

    with pytest.raises(SystemExit) as caught:
        main._resolve_session_identity(
            "c",
            STEM,
            "",
            {},
            is_remote=False,
            use_tmux=True,
            legacy_prefix=REPO,
            naming=main._NamingRequest(),
            repo=REPO,
            repo_alias=REPO,
            host_alias=HOST_ALIAS,
            worktree=True,
            dry_run=False,
            reporter=reporter,
        )

    assert caught.value.code == 2
    assert "reserved" in stream.getvalue()
    assert fleet.tip() == before


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("dry_run", [False, True], ids=["reserve", "dry-run"])
def test_given_reserved_names_in_user_config_when_each_client_launches_then_auto_picker_skips_them(
    fleet, engine, dry_run
):
    # All three 3-letter names in name_fleet.NAMES are "ada", "bob", "cyd".
    # Reserve two of them with mixed case so the shortest-length tier has only "cyd".
    _write_reserved(["ADA", "Bob"])

    title = _resolve_launch(engine, dry_run=dry_run)

    assert title == f"{STEM}-cyd"


def test_given_reserved_names_in_user_config_when_spares_are_replenished_then_reserved_names_are_not_taken_as_spares(
    fleet,
):
    # Reserve every 3-letter name except "cyd" (which the launch itself will claim),
    # plus several 4-letter names; verify spares never pick any reserved name.
    reserved = ["ada", "bob", "bert", "cleo"]
    _write_reserved(reserved)

    title = _resolve_launch("g")
    assert title == f"{STEM}-cyd"

    rows = fleet.rows()
    for name in reserved:
        assert name not in rows


def test_given_a_preexisting_spare_later_added_to_reserved_config_when_taking_offline_spare_then_it_is_skipped(
    fleet,
):
    slot = reg.Slot(
        repo=REPO,
        repo_alias=REPO,
        host_alias=HOST_ALIAS,
        label=None,
        machine=HOST,
        client="agy",
        worktree=True,
    )
    # Seed a first launch with no reserved list so this machine gets a spare row.
    first = reg.reserve_picked(fleet.registry, slot, rng=random.Random(0))
    assert first.spares_added
    spare_name = first.spares_added[0]

    # Now add that existing spare to the user's reserved list before an offline fallback.
    _write_reserved([spare_name.upper()])
    with pytest.raises(reg.SparesExhaustedError):
        reg.take_spare(fleet.registry, slot, cause="offline")


def test_given_a_user_reserved_name_when_explicitly_requested_with_name_flag_then_launch_allows_it(fleet):
    _write_reserved(["ada"])

    title = _resolve_launch("g", naming=main._NamingRequest(custom="ada"))

    assert title == f"{STEM}-ada"
    assert fleet.rows()["ada"]["title"] == f"{STEM}-ada"


def test_given_reserved_names_in_user_config_when_show_name_config_runs_then_it_lists_them_and_their_layer(fleet):
    _write_reserved(["Max", "ada"])

    result = CliRunner().invoke(_cli_group, ["show-name-config"])

    assert result.exit_code == 0, result.output
    assert "reserved" in result.output
    assert "ada, max" in result.output
    assert "[session_names]" in result.output


def test_given_malformed_reserved_config_when_releasing_names_then_release_succeeds_and_skips_spare_top_up(
    fleet, monkeypatch
):
    import json
    from pathlib import Path

    from name_fleet import git

    repo = fleet.projects / REPO
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "T")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    (Path.home() / ".claude" / "projects").mkdir(parents=True, exist_ok=True)
    title = _resolve_launch("c")
    token = title.rsplit("-", 1)[-1]
    # Remove the seeded spare so reconcile attempts a spare top-up under the malformed config.
    spares = [r["token"] for r in fleet.rows().values() if r["kind"] == "spare"]
    for spare in spares:
        reg._release_spare(fleet.registry, spare, HOST)

    _write_reserved('"ada"')

    monkeypatch.setattr("ai_cli.name_commands._projects_root", lambda: fleet.projects)
    monkeypatch.setattr("ai_cli.name_commands._host_alias", lambda: HOST_ALIAS)
    result = CliRunner().invoke(_cli_group, ["reconcile-names", "--json"])

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output.strip().splitlines()[-1])
    assert token in summary["released"]
    assert summary["top_up_skipped"] is not None
    assert "reserved" in summary["top_up_skipped"]


def test_given_malformed_reserved_config_when_offline_prompt_auto_runs_then_it_raises_clean_prompt_exit(
    fleet,
):
    from ai_cli import name_prompt

    slot = reg.Slot(
        repo=REPO,
        repo_alias=REPO,
        host_alias=HOST_ALIAS,
        label=None,
        machine=HOST,
        client="claude-code",
        worktree=True,
    )
    _write_reserved('"ada"')
    io = name_prompt.PromptIO(terminal=False, ask=lambda _: None, say=lambda _: None)

    with pytest.raises(name_prompt.PromptExit) as caught:
        name_prompt._write_picked(fleet.registry, slot, io, frozenset(), None)

    assert caught.value.code == name_prompt.EXIT_REFUSED
    assert "reserved" in caught.value.message


def test_given_malformed_reserved_config_when_list_name_candidates_runs_then_it_exits_cleanly_without_traceback(
    fleet, monkeypatch
):
    _write_reserved('"ada"')
    monkeypatch.setattr("ai_cli.name_commands._host_alias", lambda: HOST_ALIAS)

    result = CliRunner().invoke(_cli_group, ["list-name-candidates", "-r", REPO])

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "reserved" in result.output


def test_given_existing_spares_later_reserved_in_config_when_reconcile_names_runs_then_replenishes_usable_spares(
    fleet, monkeypatch
):
    import json

    slot = reg.Slot(
        repo=REPO,
        repo_alias=REPO,
        host_alias=HOST_ALIAS,
        label=None,
        machine=HOST,
        client="agy",
        worktree=True,
    )
    first = reg.reserve_picked(fleet.registry, slot, rng=random.Random(0))
    assert first.spares_added
    reserved_spare = first.spares_added[0]

    # Edit config to reserve the existing spare; reconcile-names must top up a usable replacement.
    _write_reserved([reserved_spare.upper()])
    monkeypatch.setattr("ai_cli.name_commands._adapters", dict)
    monkeypatch.setattr("ai_cli.name_commands._projects_root", lambda: fleet.projects)
    monkeypatch.setattr("ai_cli.name_commands._host_alias", lambda: HOST_ALIAS)
    result = CliRunner().invoke(_cli_group, ["reconcile-names", "--json"])

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output.strip().splitlines()[-1])
    assert summary["spares_added"]
    assert reserved_spare not in summary["spares_added"]

    # Offline fallback now succeeds using the newly replenished usable spare.
    offline = reg.take_spare(fleet.registry, slot, cause="offline")
    assert offline.name in summary["spares_added"]
    assert offline.name != reserved_spare


def _cli_launch_patches(repo_root):
    from unittest.mock import MagicMock

    return [
        ("ai_cli.main._auto_update_if_stale", {"return_value": False}),
        ("ai_cli.main.shutil.which", {"return_value": "/usr/bin/fake-cli"}),
        (
            "ai_cli.compact_transport.resolve",
            {"return_value": MagicMock(must_refuse=False, platform_limited=False, detail="pty")},
        ),
        ("ai_cli.session.cleanup_stale_sessions", {}),
        ("ai_cli.trust.ensure_workspace_trusted", {}),
        ("ai_cli.session.detect_repo_root", {"return_value": repo_root}),
        ("ai_cli.session._resolve_is_remote", {"return_value": False}),
        ("ai_cli.config.validate_registry_completeness", {"return_value": True}),
        ("ai_cli.session.get_project_prefix", {"return_value": REPO}),
        ("ai_cli.session.is_current_project_resolved", {"return_value": True}),
        ("ai_cli.config.get_current_project_name", {"return_value": REPO}),
        ("ai_cli.session_naming.resolve_or_prompt_host_alias", {"return_value": HOST_ALIAS}),
        ("ai_cli.session._tmux_session_names", {"return_value": []}),
    ]


@pytest.mark.parametrize("engine", ENGINES)
def test_given_reserved_names_when_cli_launch_command_runs_dry_run_then_it_skips_reserved_names(fleet, engine):
    from contextlib import ExitStack
    from unittest.mock import patch

    _write_reserved(["ADA", "Bob"])
    with ExitStack() as stack:
        for target, kwargs in _cli_launch_patches(fleet.projects / REPO):
            stack.enter_context(patch(target, **kwargs))
        result = CliRunner().invoke(_cli_group, [engine, "-b", "-d"])

    assert result.exit_code == 0, result.output
    assert f"planned {STEM}-cyd" in result.output


@pytest.mark.parametrize(
    ("engine", "flag"),
    [("g", "-N"), ("c", "--name")],
    ids=["short-flag", "long-flag"],
)
def test_given_reserved_name_when_cli_launch_passes_explicit_name_flag_then_it_allows_it(fleet, engine, flag):
    from contextlib import ExitStack
    from unittest.mock import patch

    _write_reserved(["ada"])
    with ExitStack() as stack:
        for target, kwargs in _cli_launch_patches(fleet.projects / REPO):
            stack.enter_context(patch(target, **kwargs))
        result = CliRunner().invoke(_cli_group, [engine, "-b", "-d", flag, "ada"])

    assert result.exit_code == 0, result.output
    assert f"planned {STEM}-ada" in result.output


def test_given_explicit_empty_reserved_list_when_show_name_config_runs_then_layer_is_session_names_not_built_in(fleet):
    unset = CliRunner().invoke(_cli_group, ["show-name-config"])
    assert unset.exit_code == 0, unset.output
    assert any("reserved" in line and "(built-in)" in line for line in unset.output.splitlines())

    _write_reserved([])
    explicit_empty = CliRunner().invoke(_cli_group, ["show-name-config"])
    assert explicit_empty.exit_code == 0, explicit_empty.output
    assert any(
        "reserved" in line and "(none)" in line and "[session_names]" in line
        for line in explicit_empty.output.splitlines()
    )
