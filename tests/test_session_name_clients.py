"""Every client shares one registry, and a remote launch is named by the remote host (T-3.7)."""

from __future__ import annotations

import json

import pytest
from name_fleet import HOST, OTHER_HOST, REPO, STEM, build_fleet, quiet_reporter

from ai_cli import main

CLIENTS = {"c": "claude-code", "g": "gemini", "p": "pi", "cx": "codex"}


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_CLI_SESSION_NAMES", "1")
    monkeypatch.setattr("ai_cli.session._tmux_session_names", list)
    return build_fleet(tmp_path, monkeypatch)


def _launch_name(engine: str) -> str:
    reporter, _stream = quiet_reporter()
    _session_id, title = main._resolve_session_identity(
        engine,
        STEM,
        "",
        {},
        is_remote=False,
        use_tmux=True,
        legacy_prefix=REPO,
        naming=main._NamingRequest(),
        repo=REPO,
        repo_alias=REPO,
        host_alias="box",
        worktree=True,
        dry_run=False,
        reporter=reporter,
    )
    return title


def test_given_each_client_when_it_launches_then_it_reserves_through_one_registry_recording_its_client(fleet):
    titles = {engine: _launch_name(engine) for engine in CLIENTS}

    rows = fleet.rows()
    for engine, title in titles.items():
        row = rows[title.rsplit("-", 1)[-1]]
        assert row["client"] == CLIENTS[engine]
        assert row["title"] == title
    assert len(set(titles.values())) == len(CLIENTS)


def test_given_codex_and_claude_in_one_repo_when_both_launch_then_their_titles_differ(fleet):
    assert _launch_name("cx") != _launch_name("c")


def test_given_a_remote_launch_when_the_remote_host_allocates_then_it_reserves_and_owns_the_row(
    fleet, monkeypatch, capsys
):
    # This process plays the remote host.
    monkeypatch.setattr("ai_cli.session_naming.host_id", lambda: OTHER_HOST)
    monkeypatch.setattr("ai_cli.session_naming.resolve_or_prompt_host_alias", lambda **_: "two")
    monkeypatch.setattr("ai_cli.tmux_setup.config_opts_out", lambda config: False)

    with pytest.raises(SystemExit) as caught:
        main._handle_internal(["allocate-session-name", "c", REPO, "", "--project", REPO])

    assert caught.value.code == 0
    reply = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    token = reply["ai_name"].rsplit("-", 1)[-1]
    assert reply["ai_name"] == f"{REPO}-two-{token}"
    assert reply["session_id"] == f"c-r-{reply['ai_name']}"
    row = fleet.rows()[token]
    assert row["machine"] == OTHER_HOST != HOST
    assert row["repo"] == REPO


def test_given_naming_flags_when_forwarded_to_the_remote_allocator_then_they_read_back_unchanged():
    naming = main._NamingRequest(custom="cleo", with_name=True, session_names=True)

    read_back, project = main._naming_from_internal_args([*main._naming_cli_args(naming), "--project", REPO])

    assert read_back == naming
    assert project == REPO
