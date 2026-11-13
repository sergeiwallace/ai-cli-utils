"""A stopped worktree is evidence of a title, not of who initiated its next launch."""

from types import SimpleNamespace

import pytest
from conftest import run_cli

from ai_cli import main, session, session_title


@pytest.mark.parametrize("engine", ["c", "g", "p", "cx"])
@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_given_stopped_worktree_when_named_then_launch_origin_sets_the_engine_prefix(
    tmp_path, monkeypatch, engine, remote, legacy
):
    title = "myrepo-1" if legacy else "myrepo-box-1"
    (tmp_path / ".worktrees" / title).mkdir(parents=True)
    monkeypatch.setattr(session, "detect_repo_root", lambda: tmp_path)
    result = session.build_session_name(
        engine, "myrepo-box", "1", is_remote=remote, use_tmux=False, legacy_prefix="myrepo"
    )
    assert result == (f"{engine}{'-r' if remote else ''}-{title}", title)


@pytest.mark.parametrize("remote", [False, True])
def test_given_stopped_named_worktree_when_reattached_then_origin_is_preserved(tmp_path, monkeypatch, remote):
    title = "myrepo-box-alpha"
    (tmp_path / ".worktrees" / title).mkdir(parents=True)
    monkeypatch.setattr(session, "detect_repo_root", lambda: tmp_path)
    result = main._reattach_title(
        "c",
        "myrepo-box",
        session_title.classify_positional("alpha", frozenset({"alpha"})),
        None,
        "host-a",
        is_remote=remote,
        use_tmux=False,
        reporter=None,
    )
    assert result == (f"c{'-r' if remote else ''}-{title}", title)


def test_given_remote_origin_when_cli_reports_start_then_it_is_remote(monkeypatch):
    monkeypatch.setattr(main, "_do_session_launch", lambda **kwargs: None)
    code, _, output = run_cli(["ai", "c", "--is-remote", "-d", "-D"])
    assert code == 0
    assert "Starting Claude Code session (remote, tmux)" in output


def test_given_remote_origin_when_launch_reports_host_then_it_does_not_claim_local(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(session, "detect_repo_root", lambda: tmp_path)
    monkeypatch.setattr(main._config, "_find_project_dir", lambda name: tmp_path)
    main._do_session_launch(
        engine="c",
        name="1",
        resume=False,
        once=False,
        bare=True,
        notify=False,
        sandbox=False,
        no_worktree=True,
        remote=False,
        project="myrepo",
        is_remote=True,
        project_prefix_override="myrepo",
        extra_args=[],
        config={},
        no_direnv=True,
        dry_run=True,
    )
    output = capsys.readouterr().err
    assert "(remote origin)" in output
    assert "(local)" not in output


def test_given_stopped_label_worktree_when_reattached_remotely_then_origin_is_preserved(tmp_path, monkeypatch):
    title = "myrepo-box-review"
    (tmp_path / ".worktrees" / title).mkdir(parents=True)
    monkeypatch.setattr(session, "detect_repo_root", lambda: tmp_path)
    snapshot = SimpleNamespace(rows={"review": {"kind": "label", "title": title, "machine": "host-a"}})
    result = main._reserve_label_title(
        "c",
        SimpleNamespace(stem="myrepo-box", machine="host-a"),
        "review",
        SimpleNamespace(snapshot=lambda: snapshot),
        is_remote=True,
        use_tmux=False,
        dry_run=False,
        reporter=None,
    )
    assert result == (f"c-r-{title}", title)


def test_given_live_local_name_when_reached_remotely_then_existing_identity_is_kept(monkeypatch):
    monkeypatch.setattr(session, "_tmux_session_names", lambda: ["c-myrepo-box-1"])
    assert session.build_session_name("c", "myrepo-box", "1", is_remote=True) == ("c-myrepo-box-1", "myrepo-box-1")
